"""Endpoint cho điện thoại đồng hành (mục 7.2–7.3 feature_spec). Gắn vào cả server LAN (HTTPS 8766) và server
localhost (để mở trang đồng hành ngay trên laptop).

- ``WS /ws/companion/{token}``: token ghép nối (dùng một lần) hoặc token thiết bị. Điện thoại → laptop: PCM
  16 kHz mono (như chế độ tab) + JSON điều khiển; laptop → điện thoại: sự kiện ``segment.*``,
  ``source.health``, ``warning``... của phiên đang học để hiện phụ đề.
- ``/api/companion/*``: ghim, chụp bảng, "Giải thích", "Hỏi trong lớp" — xác thực bằng
  ``Authorization: Bearer <token thiết bị>``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Header, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import col, select

from app.companion.hub import DeviceConn
from app.db.models import CompanionDevice, Course, LectureSession, Pin, Segment
from app.db.session import db_session
from app.llm.client import LLMError
from app.routers.deps import api_error, get_ctx
from app.schemas import segment_out, session_out
from app.services.pins import create_pin

if TYPE_CHECKING:
    from app.context import AppContext

log = logging.getLogger(__name__)
router = APIRouter()

SNAPSHOT_SEGMENTS = 30
MIC_EVENTS = {"status", "source.health", "warning", "network", "network.state", "companion.mic", "marker.created"}
DROP_EVENTS = {"level", "cost.update", "slides.updated", "post.status", "summary.ready", "flashcards.ready"}
CLOSE_TOKEN_INVALID = 4401
CLOSE_TOKEN_EXPIRED = 4410
CLOSE_REVOKED = 4403


# ---------------------------------------------------------------------------
# Xác thực
# ---------------------------------------------------------------------------


def require_device(
    authorization: str | None = Header(default=None), ctx: AppContext = Depends(get_ctx)
) -> CompanionDevice:
    token = (authorization or "").removeprefix("Bearer ").strip()
    device = ctx.pairing.authenticate(token) if token else None
    if device is None:
        raise api_error(401, "DEVICE_UNAUTHORIZED", "Thiết bị chưa ghép nối hoặc đã bị thu hồi. Hãy quét mã QR mới.")
    return device


def target_session(ctx: AppContext) -> LectureSession:
    sid = ctx.companion.target_session_id
    if sid is None:
        raise api_error(409, "NO_ACTIVE_SESSION", "Laptop chưa bắt đầu buổi học.")
    with db_session(ctx.engine) as s:
        sess = s.get(LectureSession, sid)
    if sess is None or sess.status == "ended":
        raise api_error(409, "NO_ACTIVE_SESSION", "Buổi học đã kết thúc.")
    return sess


def _same_origin(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return True
    host = ws.headers.get("host", "")
    return urlsplit(origin).netloc.lower() == host.lower()


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


def snapshot(ctx: AppContext, session_id: int | None) -> dict[str, Any]:
    settings = ctx.settings.get()
    base: dict[str, Any] = {
        "type": "snapshot",
        "settings": {"browser_audio_processing": settings.browser_audio_processing},
    }
    if session_id is None:
        return {**base, "session": None, "segments": [], "pins": [], "runner": None}
    with db_session(ctx.engine) as s:
        sess = s.get(LectureSession, session_id)
        if sess is None:
            return {**base, "session": None, "segments": [], "pins": [], "runner": None}
        course = s.get(Course, sess.course_id) if sess.course_id else None
        segs = list(
            s.exec(
                select(Segment)
                .where(Segment.session_id == session_id)
                .order_by(col(Segment.seq).desc())
                .limit(SNAPSHOT_SEGMENTS)
            )
        )
        pins = list(s.exec(select(Pin).where(Pin.session_id == session_id)))
    runner = ctx.runners.peek(session_id)
    return {
        **base,
        "session": session_out(sess, course),
        "course": {"id": course.id, "name": course.name, "name_it": course.name_it} if course else None,
        "segments": [segment_out(sg) for sg in reversed(segs)],
        "pins": [{"id": p.id, "segment_id": p.segment_id, "kind": p.kind} for p in pins],
        "runner": runner.status_event() if runner else None,
    }


def _allowed(event: dict[str, Any], mode: str) -> bool:
    kind = event.get("type", "")
    if kind in DROP_EVENTS:
        return False
    if mode == "mic":
        return kind in MIC_EVENTS or kind.startswith("companion.")
    return True


async def _sender(ws: WebSocket, ctx: AppContext, conn: DeviceConn) -> None:
    """Chuyển sự kiện của phiên mục tiêu tới điện thoại; đổi phiên khi laptop bắt đầu buổi khác."""
    session_id: int | None = None
    queue: asyncio.Queue | None = None
    try:
        while True:
            conn.retarget.clear()
            target = ctx.companion.target_session_id
            if target != session_id or queue is None:
                if queue is not None and session_id is not None:
                    ctx.hub.unsubscribe(session_id, queue)
                session_id = target
                queue = ctx.hub.subscribe(session_id) if session_id is not None else asyncio.Queue()
                await ws.send_json(snapshot(ctx, session_id))
            get = asyncio.create_task(queue.get())
            wake = asyncio.create_task(conn.retarget.wait())
            done, pending = await asyncio.wait({get, wake}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if get in done:
                event = get.result()
                if _allowed(event, conn.mode):
                    await ws.send_json(event)
    finally:
        if queue is not None and session_id is not None:
            ctx.hub.unsubscribe(session_id, queue)


@router.websocket("/ws/companion/{token}")
async def companion_ws(websocket: WebSocket, token: str) -> None:
    ctx: AppContext = websocket.app.state.ctx
    if not _same_origin(websocket) and websocket.headers.get("origin") not in ctx.env.allowed_origins:
        await websocket.close(code=4403)
        return
    await websocket.accept()

    device: CompanionDevice | None
    if ctx.pairing.is_pairing_token(token):
        result, item = ctx.pairing.consume(token)
        if result != "ok":
            code, message = (
                ("TOKEN_EXPIRED", "Mã QR đã hết hạn, hãy tạo mã mới.")
                if result in ("expired", "used")
                else ("TOKEN_INVALID", "Mã QR không hợp lệ, hãy tạo mã mới trên laptop.")
            )
            await websocket.send_json({"type": "error", "code": code, "message": message})
            await websocket.close(code=CLOSE_TOKEN_EXPIRED if result != "invalid" else CLOSE_TOKEN_INVALID)
            return
        device, device_token = ctx.pairing.register_device()
        await websocket.send_json({"type": "paired", "device_id": device.id, "device_token": device_token})
        if item is not None and item.session_id is not None:
            ctx.companion.set_target(item.session_id)
    else:
        device = ctx.pairing.authenticate(token)
        if device is None:
            await websocket.send_json(
                {"type": "error", "code": "TOKEN_INVALID", "message": "Thiết bị đã bị thu hồi hoặc chưa ghép nối. Hãy quét mã QR mới."}
            )
            await websocket.close(code=CLOSE_TOKEN_INVALID)
            return
        await websocket.send_json({"type": "welcome", "device_id": device.id})

    conn = DeviceConn(device_id=device.id or 0, name=device.name, ws=websocket)
    ctx.companion.add(conn)
    sender = asyncio.create_task(_sender(websocket, ctx, conn))
    last_touch = 0.0
    loop = asyncio.get_running_loop()
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                ctx.companion.on_audio(conn, data)
                continue
            raw = message.get("text")
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            kind = payload.get("type")
            if kind == "hello":
                name = str(payload.get("name") or "").strip()[:80]
                if name and name != conn.name:
                    conn.name = name
                    ctx.pairing.rename(conn.device_id, name)
                ctx.companion.set_mode(conn, str(payload.get("mode") or "subtitle"))
            elif kind == "mode":
                ctx.companion.set_mode(conn, str(payload.get("mode") or "subtitle"))
            elif kind == "ping":
                await websocket.send_json({"type": "pong", "t": payload.get("t")})
            elif kind == "stats":
                rtt = payload.get("rtt_ms")
                conn.rtt_ms = float(rtt) if isinstance(rtt, int | float) else conn.rtt_ms
                battery = payload.get("battery")
                conn.battery = battery if isinstance(battery, dict) else conn.battery
            if loop.time() - last_touch > 60:
                last_touch = loop.time()
                ctx.pairing.touch(conn.device_id)
    except WebSocketDisconnect:
        pass
    except RuntimeError as e:
        log.debug("WebSocket điện thoại đóng: %s", e)
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sender
        ctx.companion.remove(conn)


# ---------------------------------------------------------------------------
# REST (token thiết bị)
# ---------------------------------------------------------------------------


class CompanionPinIn(BaseModel):
    segment_id: int | None = None
    kind: str = Field(default="confused", pattern="^(confused|important|todo)$")


class ExplainIn(BaseModel):
    segment_id: int | None = None
    question: str | None = Field(default=None, max_length=2000)


class SpeakIn(BaseModel):
    text_vi: str = Field(min_length=1, max_length=1000)


@router.get("/api/companion/me")
def companion_me(device: CompanionDevice = Depends(require_device), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return {
        "device": {"id": device.id, "name": device.name},
        "target_session_id": ctx.companion.target_session_id,
    }


@router.post("/api/companion/pins", status_code=201)
async def companion_pin(
    body: CompanionPinIn,
    device: CompanionDevice = Depends(require_device),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    sess = target_session(ctx)
    return create_pin(ctx, sess.id, body.segment_id, body.kind, None)  # type: ignore[arg-type]


@router.post("/api/companion/board-captures", status_code=201)
async def companion_board(
    file: UploadFile = File(...),
    device: CompanionDevice = Depends(require_device),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    from app.llm.board_ocr import MAX_UPLOAD_BYTES, BoardError, capture_out

    sess = target_session(ctx)
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise api_error(413, "FILE_TOO_LARGE", "Ảnh quá lớn (tối đa 20 MB).")
    try:
        row = await ctx.boards.capture(sess.id, data, source="phone")  # type: ignore[arg-type]
    except BoardError as e:
        raise api_error(400, "INVALID_IMAGE", str(e)) from e
    return capture_out(row)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/api/companion/explain")
async def companion_explain(
    body: ExplainIn,
    device: CompanionDevice = Depends(require_device),
    ctx: AppContext = Depends(get_ctx),
) -> StreamingResponse:
    """Nút "💡 Giải thích" (IC-FR-61): hỏi nhanh Claude về đoạn vừa rồi, trả lời stream (SSE)."""
    sess = target_session(ctx)
    if not ctx.assistant_llm.has_key:
        raise api_error(400, "NO_API_KEY", "Chưa có API key Claude hoặc chưa bật gói Pro cho trợ lý (Cài đặt → Trợ lý học tập).")
    with db_session(ctx.engine) as s:
        stmt = select(Segment).where(Segment.session_id == sess.id, Segment.status != "redacted")
        seg = (
            s.get(Segment, body.segment_id)
            if body.segment_id
            else s.exec(stmt.order_by(col(Segment.seq).desc()).limit(1)).first()
        )
    from app.services.timefmt import fmt_ts

    if body.question:
        question = body.question.strip()
    elif seg is not None and seg.session_id == sess.id:
        question = (
            f"Giải thích ngắn gọn (tối đa 5 câu, dễ hiểu) đoạn giảng viên vừa nói lúc [{fmt_ts(seg.t_start_ms)}]: "
            f"«{seg.source_text}». Nêu thuật ngữ quan trọng kèm tiếng Ý."
        )
    else:
        question = "Giải thích ngắn gọn đoạn giảng viên vừa nói (khoảng 2 phút gần nhất)."
    queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()

    async def run() -> None:
        try:
            msg, meta = await ctx.assistant.answer(sess.id, question, lambda t: queue.put_nowait(("delta", {"text": t})))  # type: ignore[arg-type]
            await queue.put(("done", {"content": msg.content, "model": meta.get("model")}))
        except LLMError as e:
            await queue.put(("error", {"code": e.code, "message": e.message}))
        except Exception:  # noqa: BLE001
            log.exception("Lỗi giải thích từ điện thoại")
            await queue.put(("error", {"code": "internal", "message": "Lỗi nội bộ khi gọi trợ lý"}))
        finally:
            await queue.put(None)

    async def stream() -> AsyncIterator[str]:
        task = asyncio.create_task(run())
        try:
            yield _sse("start", {"question": question})
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield _sse(item[0], item[1])
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/api/companion/speak")
async def companion_speak(
    body: SpeakIn,
    device: CompanionDevice = Depends(require_device),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    """IC-US-10: câu hỏi tiếng Việt → câu tiếng Ý ngắn để nói trực tiếp trong lớp, kèm gợi ý nhấn âm."""
    sess = target_session(ctx)
    if not ctx.assistant_llm.has_key:
        raise api_error(400, "NO_API_KEY", "Chưa có API key Claude hoặc chưa bật gói Pro cho trợ lý (Cài đặt → Trợ lý học tập).")
    _msg, payload = await ctx.assistant.compose_speak(sess.id, body.text_vi.strip())  # type: ignore[arg-type]
    return payload
