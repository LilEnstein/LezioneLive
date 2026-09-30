"""Flow D: kết thúc buổi học → đợi hàng đợi dịch (≤ 60 s) → tóm tắt → trích flashcard."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.db.models import LectureSession
from app.db.session import db_session
from app.llm.client import LLMError

if TYPE_CHECKING:
    from app.context import AppContext

log = logging.getLogger(__name__)

DRAIN_TIMEOUT_S = 60.0


class PostProcessor:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._tasks: dict[int, asyncio.Task] = {}

    def running(self, session_id: int) -> bool:
        task = self._tasks.get(session_id)
        return task is not None and not task.done()

    def start(self, session_id: int, *, summary: bool = True, flashcards: bool = True) -> asyncio.Task:
        task = self._tasks.get(session_id)
        if task is not None and not task.done():
            return task
        task = asyncio.create_task(self._run(session_id, summary=summary, flashcards=flashcards))
        self._tasks[session_id] = task
        return task

    def _set(self, session_id: int, status: str, error: str | None = None) -> None:
        with db_session(self.ctx.engine) as s:
            sess = s.get(LectureSession, session_id)
            if sess is None:
                return
            sess.post_status = status
            sess.post_error = error
            s.add(sess)
            s.commit()
        event = {"type": "post.status", "status": status}
        if error:
            event["error"] = error
        self.ctx.hub.publish(session_id, event)

    async def _run(self, session_id: int, *, summary: bool, flashcards: bool) -> None:
        self._set(session_id, "pending")
        drained = await self.ctx.translation.drain(session_id, DRAIN_TIMEOUT_S)
        if not drained:
            log.warning("Phiên %s: hàng đợi dịch chưa trống sau %ss, vẫn tạo tóm tắt", session_id, DRAIN_TIMEOUT_S)
        if not self.ctx.assistant_llm.has_key:
            self._set(session_id, "failed", "Chưa có API key Claude hoặc chưa bật gói Pro cho trợ lý (Cài đặt → Trợ lý học tập).")
            return
        try:
            if summary:
                self._set(session_id, "summary")
                await self.ctx.summarizer.generate(session_id)
                self.ctx.hub.publish(session_id, {"type": "summary.ready"})
            if flashcards:
                self._set(session_id, "flashcards")
                cards = await self.ctx.flashcards.generate(session_id)
                self.ctx.hub.publish(session_id, {"type": "flashcards.ready", "count": len(cards)})
            self._set(session_id, "done")
        except LLMError as e:
            log.warning("Xử lý sau buổi học lỗi (%s): %s", e.code, e.message)
            self._set(session_id, "failed", e.message)
        except Exception as e:  # noqa: BLE001
            log.exception("Xử lý sau buổi học lỗi")
            self._set(session_id, "failed", f"Lỗi nội bộ: {type(e).__name__}")

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
