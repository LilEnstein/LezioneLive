"""WebSocket ``/ws/session/{session_id}`` (mục 9.2).

Client → Server: binary PCM Int16 LE 16 kHz mono (100 ms/frame) hoặc JSON điều khiển
``{"type": "control", "action": "pause" | "resume" | "source_ended"}``.
Server → Client: sự kiện JSON (status, vad.*, segment.*, cost.update, warning...).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import INCLASS_SOURCES
from app.context import AppContext
from app.db.models import LectureSession
from app.db.session import db_session
from app.pipeline.session_runner import SessionRunner

log = logging.getLogger(__name__)
router = APIRouter()


def _static_status(ctx: AppContext, sess: LectureSession) -> dict[str, Any]:
    return {
        "type": "status",
        "state": sess.status,
        "stt_ready": ctx.stt.ready,
        "queue_len": ctx.translation.queue_len(sess.id),  # type: ignore[arg-type]
        "translation_enabled": sess.translation_enabled,
        "translation_slow": ctx.translation.slow,
        "network_online": ctx.connectivity.online,
        "api_key_ok": ctx.translator.ready and not ctx.translation.auth_failed,
        "speaking": False,
        "t_ms": sess.active_ms,
        "audio_mode": sess.audio_mode,
        "capture_source": sess.capture_source,
        "inclass": sess.capture_source in INCLASS_SOURCES,
        "post_status": sess.post_status,
    }


async def _sender(ws: WebSocket, queue: asyncio.Queue) -> None:
    while True:
        event = await queue.get()
        await ws.send_json(event)


@router.websocket("/ws/session/{session_id}")
async def session_ws(websocket: WebSocket, session_id: int) -> None:
    ctx: AppContext = websocket.app.state.ctx
    origin = websocket.headers.get("origin")
    if origin and origin not in ctx.env.allowed_origins:
        await websocket.close(code=4403)
        return
    with db_session(ctx.engine) as s:
        sess = s.get(LectureSession, session_id)
    if sess is None:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    conn_id = f"ws-{id(websocket)}"
    queue = ctx.hub.subscribe(session_id)
    runner: SessionRunner | None = None
    if sess.status != "ended":
        runner = await ctx.runners.get(session_id)
    await websocket.send_json(runner.status_event() if runner else _static_status(ctx, sess))
    sender = asyncio.create_task(_sender(websocket, queue))
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                if runner is not None:
                    runner.push_audio(data, conn_id)
                continue
            raw = message.get("text")
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            kind = payload.get("type")
            if kind == "ping":
                await websocket.send_json({"type": "pong"})
            elif kind == "control":
                action = payload.get("action")
                if runner is None:
                    runner = await ctx.runners.get(session_id)
                if runner is None:
                    continue
                if action == "pause":
                    await runner.pause()
                elif action == "source_ended":
                    await runner.pause(reason="source_ended")
                elif action == "resume":
                    await runner.resume()
    except WebSocketDisconnect:
        pass
    except RuntimeError as e:  # gửi/nhận sau khi socket đóng
        log.debug("WebSocket phiên %s đóng: %s", session_id, e)
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sender
        ctx.hub.unsubscribe(session_id, queue)
        if runner is not None:
            runner.release_audio(conn_id)
