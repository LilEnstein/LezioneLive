"""/sessions/{id}/chat: hỏi đáp (mode "qa", theo từng cuộc trò chuyện) và soạn câu hỏi tiếng Ý qua SSE."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func
from sqlmodel import col, delete, select

from app.context import AppContext
from app.db.models import ChatAttachment, ChatMessage, ChatThread
from app.db.session import db_session
from app.llm.client import LLMError
from app.routers.deps import api_error, get_ctx, require_session
from app.schemas import ChatIn, ChatThreadIn, ChatThreadPatch, chat_out, thread_out
from app.services.chat_images import (
    ChatImageError,
    SavedImage,
    attachment_out,
    decode_image,
    delete_files,
    save_images,
)

log = logging.getLogger(__name__)
router = APIRouter()


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _thread(ctx: AppContext, thread_id: int, session_id: int | None = None) -> ChatThread:
    with db_session(ctx.engine) as s:
        thread = s.get(ChatThread, thread_id)
    if thread is None or (session_id is not None and thread.session_id != session_id):
        raise api_error(404, "THREAD_NOT_FOUND", "Không tìm thấy cuộc trò chuyện")
    return thread


def _create_thread(ctx: AppContext, session_id: int, title: str = "") -> ChatThread:
    thread = ChatThread(session_id=session_id, title=title.strip())
    with db_session(ctx.engine) as s:
        s.add(thread)
        s.commit()
        s.refresh(thread)
    return thread


@router.get("/sessions/{session_id}/chat/threads")
def list_threads(session_id: int, ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    """Các cuộc trò chuyện hỏi đáp của buổi học, mới cập nhật trước."""
    require_session(ctx, session_id)
    with db_session(ctx.engine) as s:
        threads = list(
            s.exec(
                select(ChatThread)
                .where(ChatThread.session_id == session_id)
                .order_by(col(ChatThread.updated_at).desc(), col(ChatThread.id).desc())
            )
        )
        counts = dict(
            s.exec(
                select(ChatMessage.thread_id, func.count())
                .where(ChatMessage.session_id == session_id, col(ChatMessage.thread_id).is_not(None))
                .group_by(ChatMessage.thread_id)
            ).all()
        )
    return [thread_out(t, counts.get(t.id, 0)) for t in threads]


@router.post("/sessions/{session_id}/chat/threads")
def create_thread(session_id: int, body: ChatThreadIn, ctx: AppContext = Depends(get_ctx)) -> dict:
    require_session(ctx, session_id)
    return thread_out(_create_thread(ctx, session_id, body.title or ""))


@router.patch("/chat/threads/{thread_id}")
def rename_thread(thread_id: int, body: ChatThreadPatch, ctx: AppContext = Depends(get_ctx)) -> dict:
    _thread(ctx, thread_id)
    with db_session(ctx.engine) as s:
        thread = s.get(ChatThread, thread_id)
        assert thread is not None
        thread.title = body.title.strip()
        s.add(thread)
        s.commit()
        s.refresh(thread)
        count = s.exec(select(func.count()).where(ChatMessage.thread_id == thread_id)).one()
    return thread_out(thread, count)


@router.delete("/chat/threads/{thread_id}")
def delete_thread(thread_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    _thread(ctx, thread_id)
    with db_session(ctx.engine) as s:
        files = s.exec(
            select(ChatAttachment.image_path, ChatAttachment.thumb_path)
            .join(ChatMessage, col(ChatAttachment.message_id) == col(ChatMessage.id))
            .where(ChatMessage.thread_id == thread_id)
        ).all()
        s.exec(delete(ChatMessage).where(col(ChatMessage.thread_id) == thread_id))
        s.exec(delete(ChatThread).where(col(ChatThread.id) == thread_id))
        s.commit()
    delete_files([p for pair in files for p in pair])
    return {"deleted": True}


@router.get("/chat/attachments/{attachment_id}")
def chat_attachment(attachment_id: int, thumb: bool = False, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    with db_session(ctx.engine) as s:
        row = s.get(ChatAttachment, attachment_id)
    path = None if row is None else (row.thumb_path if thumb else row.image_path)
    if path is None or not Path(path).exists():
        raise api_error(404, "ATTACHMENT_NOT_FOUND", "Không tìm thấy ảnh")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/sessions/{session_id}/chat")
def chat_history(
    session_id: int, mode: str | None = None, thread_id: int | None = None, ctx: AppContext = Depends(get_ctx)
) -> list[dict]:
    require_session(ctx, session_id)
    if thread_id is not None:
        _thread(ctx, thread_id, session_id)
        messages = ctx.assistant.history(session_id, "qa", thread_id=thread_id)
    elif mode:
        messages = ctx.assistant.history(session_id, mode)
    else:
        messages = (
            ctx.assistant.history(session_id, "qa")
            + ctx.assistant.history(session_id, "compose_it")
            + ctx.assistant.history(session_id, "speak_it")
        )
        messages.sort(key=lambda m: m.id or 0)
    attached = ctx.assistant.attachments([m.id for m in messages if m.id is not None])
    return [chat_out(m, [attachment_out(a) for a in attached.get(m.id or 0, [])]) for m in messages]


@router.post("/sessions/{session_id}/chat")
async def chat(session_id: int, body: ChatIn, ctx: AppContext = Depends(get_ctx)) -> StreamingResponse:
    require_session(ctx, session_id)
    if not ctx.assistant_llm.has_key:
        raise api_error(400, "NO_API_KEY", "Chưa có API key Claude hoặc chưa bật gói Pro cho trợ lý (Cài đặt → Trợ lý học tập).")
    if body.images and body.mode != "qa":
        raise api_error(400, "IMAGES_NOT_SUPPORTED", "Chỉ gửi ảnh được khi hỏi bài.")
    thread: ChatThread | None = _thread(ctx, body.thread_id, session_id) if body.thread_id else None
    images: list[SavedImage] = []
    if body.images:
        try:
            raws = [decode_image(d) for d in body.images]
            images = await asyncio.to_thread(save_images, ctx.env.data_dir, session_id, raws)
        except ChatImageError as e:
            raise api_error(400, "BAD_IMAGE", str(e)) from e
    if body.mode == "qa" and thread is None:
        thread = _create_thread(ctx, session_id)
    queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()

    def on_text(text: str) -> None:
        queue.put_nowait(("delta", {"text": text}))

    async def run() -> None:
        try:
            if body.mode == "qa":
                assert thread is not None
                msg, meta = await ctx.assistant.answer(
                    session_id, body.message.strip(), on_text, thread.id, images=images
                )
                await queue.put(("done", {"message": chat_out(msg), **meta}))
            else:
                if body.mode == "speak_it":
                    msg, payload = await ctx.assistant.compose_speak(session_id, body.message.strip())
                else:
                    msg, payload = await ctx.assistant.compose_it(session_id, body.message.strip())
                await queue.put(("result", payload))
                await queue.put(("done", {"message": chat_out(msg)}))
        except LLMError as e:
            await queue.put(("error", {"code": e.code, "message": e.message}))
        except Exception:  # noqa: BLE001
            log.exception("Lỗi chat")
            await queue.put(("error", {"code": "internal", "message": "Lỗi nội bộ khi gọi trợ lý"}))
        finally:
            await queue.put(None)

    async def stream() -> AsyncIterator[str]:
        task = asyncio.create_task(run())
        try:
            yield sse("start", {"mode": body.mode, "thread_id": thread.id if thread else None})
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield sse(item[0], item[1])
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
