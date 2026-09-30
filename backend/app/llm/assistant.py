"""Trợ lý hỏi đáp trên transcript (FR-AS-01..07, Flow C) và soạn câu hỏi tiếng Ý (Flow E).

Chiến lược prompt caching (mục 10.3):
- Transcript được chia thành các khối cố định ``TRANSCRIPT_CHUNK`` segment. Khối "đóng" (đủ số segment và
  mọi segment đã dịch xong) không bao giờ đổi byte, nên có thể tái dùng cache giữa các câu hỏi.
- Breakpoint 1: cuối phần thuật ngữ + slide (TTL 1 giờ). Breakpoint 2: khối transcript đóng cuối cùng
  (TTL 1 giờ, vì người học thường hỏi cách nhau > 5 phút). Breakpoint 3: cuối lịch sử chat (TTL 5 phút).
- Các dòng mới nhất (khối đang mở) đi kèm câu hỏi ở tin nhắn cuối, sau mọi breakpoint.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlmodel import col, select

from app.db.models import ChatAttachment, ChatMessage, ChatThread, Segment, SlideDeck, TranscriptBlockSummary
from app.db.session import db_session
from app.llm.client import LLMClient, LLMError
from app.llm.prompt_library import PromptLibrary
from app.llm.translator import format_glossary
from app.pipeline.context import ContextProvider
from app.services.chat_images import HISTORY_IMAGES, SavedImage, image_block
from app.services.chat_threads import thread_title
from app.services.settings_store import SettingsStore
from app.services.timefmt import fmt_ts

log = logging.getLogger(__name__)

TRANSCRIPT_CHUNK = 40
HISTORY_MESSAGES = 10
TOKEN_LIMIT = 150_000
BLOCK_MS = 30 * 60 * 1000
CHARS_PER_TOKEN = 2.8  # ước lượng thận trọng cho văn bản Ý/Việt có dấu
DOC_TOKENS_PER_PAGE = 1600
IMAGE_TOKENS = 1600  # ảnh ≤ 1568 px ≈ 1.6k token
INLINE_PDF_MAX_BYTES = 10 * 1024 * 1024
FINAL_STATES = {"translated", "failed"}

CACHE_1H = {"type": "ephemeral", "ttl": "1h"}
CACHE_5M = {"type": "ephemeral"}

COMPOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question_it": {"type": "string"},
        "back_translation_vi": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["question_it", "back_translation_vi", "notes"],
    "additionalProperties": False,
}


SPEAK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question_it": {"type": "string"},
        "pronunciation": {"type": "string"},
        "back_translation_vi": {"type": "string"},
        "tip": {"type": "string"},
    },
    "required": ["question_it", "pronunciation", "back_translation_vi", "tip"],
    "additionalProperties": False,
}

CONSENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject_it": {"type": "string"},
        "email_it": {"type": "string"},
        "back_translation_vi": {"type": "string"},
    },
    "required": ["subject_it", "email_it", "back_translation_vi"],
    "additionalProperties": False,
}


def transcript_line(seg: Segment, *, with_vi: bool = True) -> str:
    flag = " (?)" if seg.confidence < 0.5 else ""
    who = " (sinh viên hỏi)" if seg.speaker == "other" else ""
    line = f"[{fmt_ts(seg.t_start_ms)}] IT{who}: {seg.source_text}{flag}"
    if with_vi:
        vi = seg.text_vi if seg.text_vi and seg.translation_kind not in ("draft", "local") else None
        line += f"\n        VI: {vi or '…'}"
    return line


def split_chunks(segments: list[Segment], size: int = TRANSCRIPT_CHUNK) -> tuple[list[list[Segment]], list[Segment]]:
    """Trả về (các khối đã đóng, phần đuôi đang mở). Khối chỉ đóng khi mọi segment ở trạng thái cuối."""
    closed: list[list[Segment]] = []
    i = 0
    while i + size <= len(segments):
        chunk = segments[i : i + size]
        if any(s.status not in FINAL_STATES for s in chunk):
            break
        closed.append(chunk)
        i += size
    return closed, segments[i:]


def estimate_tokens(blocks: list[dict[str, Any]], pages: int = 0) -> int:
    chars = 0
    for b in blocks:
        if b.get("type") == "text":
            chars += len(b["text"])
    return int(chars / CHARS_PER_TOKEN) + pages * DOC_TOKENS_PER_PAGE


@dataclass
class ChatRequest:
    system: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    summary_mode: bool
    est_tokens: int


class AssistantService:
    def __init__(
        self,
        engine: Engine,
        llm: LLMClient,
        prompts: PromptLibrary,
        settings: SettingsStore,
        context: ContextProvider,
    ) -> None:
        self._engine = engine
        self._llm = llm
        self._prompts = prompts
        self._settings = settings
        self._context = context
        self._block_locks: dict[tuple[int, int], asyncio.Lock] = {}

    # ------------------------------------------------------------------ data
    def _segments(self, session_id: int) -> list[Segment]:
        """Transcript cho trợ lý: bỏ lời người khác đã xoá theo tuỳ chọn quyền riêng tư."""
        with db_session(self._engine) as s:
            return list(
                s.exec(
                    select(Segment)
                    .where(Segment.session_id == session_id, Segment.status != "redacted")
                    .order_by(Segment.seq)
                )
            )

    def _decks(self, session_id: int) -> list[SlideDeck]:
        with db_session(self._engine) as s:
            return list(
                s.exec(
                    select(SlideDeck)
                    .where(SlideDeck.session_id == session_id, SlideDeck.status != "failed")
                    .order_by(SlideDeck.id)
                )
            )

    def history(
        self, session_id: int, mode: str = "qa", limit: int | None = None, thread_id: int | None = None
    ) -> list[ChatMessage]:
        """Lịch sử theo mode; ``thread_id`` giới hạn trong một cuộc trò chuyện (None = mọi thread)."""
        with db_session(self._engine) as s:
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id, ChatMessage.mode == mode)
                .order_by(col(ChatMessage.id).desc())
            )
            if thread_id is not None:
                stmt = stmt.where(ChatMessage.thread_id == thread_id)
            if limit:
                stmt = stmt.limit(limit)
            rows = list(s.exec(stmt))
        return list(reversed(rows))

    def _save_message(
        self, session_id: int, role: str, content: str, mode: str, model: str | None, thread_id: int | None = None
    ) -> ChatMessage:
        msg = ChatMessage(
            session_id=session_id, role=role, content=content, mode=mode, model=model, thread_id=thread_id
        )
        with db_session(self._engine) as s:
            s.add(msg)
            if thread_id is not None:
                thread = s.get(ChatThread, thread_id)
                if thread is not None:
                    thread.updated_at = msg.created_at
                    if not thread.title and role == "user":
                        thread.title = thread_title(content)
                    s.add(thread)
            s.commit()
            s.refresh(msg)
        return msg

    def _attach(self, session_id: int, message_id: int, images: list[SavedImage]) -> None:
        with db_session(self._engine) as s:
            for img in images:
                s.add(
                    ChatAttachment(
                        session_id=session_id,
                        message_id=message_id,
                        image_path=img.image_path,
                        thumb_path=img.thumb_path,
                        width=img.width,
                        height=img.height,
                    )
                )
            s.commit()

    def attachments(self, message_ids: list[int]) -> dict[int, list[ChatAttachment]]:
        if not message_ids:
            return {}
        with db_session(self._engine) as s:
            rows = list(
                s.exec(
                    select(ChatAttachment)
                    .where(col(ChatAttachment.message_id).in_(message_ids))
                    .order_by(col(ChatAttachment.id))
                )
            )
        out: dict[int, list[ChatAttachment]] = {}
        for r in rows:
            out.setdefault(r.message_id or 0, []).append(r)
        return out

    # --------------------------------------------------------------- context
    def _slide_blocks(self, decks: list[SlideDeck]) -> tuple[list[dict[str, Any]], int]:
        """Block slide: document (Files API / base64) hoặc text có đánh số trang. Trả về (blocks, số trang doc)."""
        mode = self._settings.get().slides_mode if getattr(self._llm, "supports_documents", True) else "text"
        blocks: list[dict[str, Any]] = []
        doc_pages = 0
        for deck in decks:
            if mode == "document" and deck.file_id:
                blocks.append(
                    {"type": "document", "source": {"type": "file", "file_id": deck.file_id}, "title": deck.filename}
                )
                doc_pages += deck.page_count
            elif mode == "document" and deck.size_bytes <= INLINE_PDF_MAX_BYTES and Path(deck.path).exists():
                data = base64.standard_b64encode(Path(deck.path).read_bytes()).decode("ascii")
                blocks.append(
                    {
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": data},
                        "title": deck.filename,
                    }
                )
                doc_pages += deck.page_count
            elif deck.extracted_text.strip():
                blocks.append(
                    {"type": "text", "text": f'<slides file="{deck.filename}">\n{deck.extracted_text}\n</slides>'}
                )
        return blocks, doc_pages

    async def _block_summaries(self, session_id: int, segments: list[Segment], upto_block: int) -> list[str]:
        """Rolling summary cho các khối 30 phút có chỉ số < ``upto_block`` (tạo mới nếu thiếu)."""
        blocks: dict[int, list[Segment]] = {}
        for seg in segments:
            b = seg.t_start_ms // BLOCK_MS
            if b < upto_block:
                blocks.setdefault(b, []).append(seg)
        with db_session(self._engine) as s:
            existing = {
                r.block_index: r
                for r in s.exec(
                    select(TranscriptBlockSummary).where(TranscriptBlockSummary.session_id == session_id)
                )
            }
        tasks = {}
        for b, segs in blocks.items():
            row = existing.get(b)
            if row is None or row.last_seq != segs[-1].seq:
                tasks[b] = asyncio.create_task(self._summarize_block(session_id, b, segs))
        if tasks:
            await asyncio.gather(*tasks.values())
            with db_session(self._engine) as s:
                existing = {
                    r.block_index: r
                    for r in s.exec(
                        select(TranscriptBlockSummary).where(TranscriptBlockSummary.session_id == session_id)
                    )
                }
        out = []
        for b in sorted(blocks):
            row = existing.get(b)
            if row is not None:
                out.append(f"### Khối {b + 1} [{fmt_ts(row.t_start_ms)}–{fmt_ts(row.t_end_ms)}]\n{row.content}")
        return out

    async def _summarize_block(self, session_id: int, block: int, segs: list[Segment]) -> None:
        lock = self._block_locks.setdefault((session_id, block), asyncio.Lock())
        async with lock:
            with db_session(self._engine) as s:
                row = s.exec(
                    select(TranscriptBlockSummary).where(
                        TranscriptBlockSummary.session_id == session_id,
                        TranscriptBlockSummary.block_index == block,
                    )
                ).first()
            if row is not None and row.last_seq == segs[-1].seq:
                return
            st = self._context.static(session_id)
            settings = self._settings.get()
            system = self._prompts.render(
                "rolling_summary_v1",
                course_name=st.course_name,
                start=fmt_ts(segs[0].t_start_ms),
                end=fmt_ts(segs[-1].t_end_ms),
            )
            text = "\n".join(transcript_line(sg, with_vi=False) for sg in segs)
            result = await self._llm.stream(
                purpose="rolling_summary",
                session_id=session_id,
                model=settings.translate_model,
                max_tokens=2000,
                system=system,
                messages=[{"role": "user", "content": f"<transcript>\n{text}\n</transcript>"}],
                route="fast",
            )
            with db_session(self._engine) as s:
                row = s.exec(
                    select(TranscriptBlockSummary).where(
                        TranscriptBlockSummary.session_id == session_id,
                        TranscriptBlockSummary.block_index == block,
                    )
                ).first()
                if row is None:
                    row = TranscriptBlockSummary(
                        session_id=session_id,
                        block_index=block,
                        t_start_ms=segs[0].t_start_ms,
                        t_end_ms=segs[-1].t_end_ms,
                        last_seq=segs[-1].seq,
                        content=result.text.strip(),
                        model=result.model,
                    )
                else:
                    row.t_end_ms = segs[-1].t_end_ms
                    row.last_seq = segs[-1].seq
                    row.content = result.text.strip()
                    row.model = result.model
                s.add(row)
                s.commit()

    async def build_request(
        self,
        session_id: int,
        question: str,
        *,
        force_summary: bool = False,
        thread_id: int | None = None,
        images: list[str] | None = None,
    ) -> ChatRequest:
        """``images``: đường dẫn ảnh gửi kèm câu hỏi hiện tại (JPEG đã chuẩn hoá)."""
        settings = self._settings.get()
        st = self._context.static(session_id)
        segments = self._segments(session_id)
        system = [{"type": "text", "text": self._prompts.render("assistant_v1")}]

        head = (
            f"Buổi học: {st.title}\nMôn: {st.course_name}"
            + (f" ({st.course_name_it})" if st.course_name_it else "")
            + (f"\nGiảng viên: {st.lecturer}" if st.lecturer else "")
            + f"\n\n<glossary>\n{format_glossary(st.glossary)}\n</glossary>"
        )
        context_blocks: list[dict[str, Any]] = [{"type": "text", "text": head}]
        slide_blocks, doc_pages = self._slide_blocks(self._decks(session_id))
        context_blocks.extend(slide_blocks)
        context_blocks[-1] = {**context_blocks[-1], "cache_control": CACHE_1H}

        summary_mode = force_summary
        transcript_blocks, recent = self._transcript_blocks(segments, summary_lines=None)
        est = estimate_tokens(context_blocks + transcript_blocks, doc_pages) + int(len(recent) / CHARS_PER_TOKEN)
        if not summary_mode and est > TOKEN_LIMIT * 0.8 and self._llm.has_key:
            try:
                est = await self._llm.count_tokens(
                    model=settings.assistant_model,
                    system=system,
                    messages=[{"role": "user", "content": context_blocks + transcript_blocks + [_text(recent)]}],
                )
            except LLMError as e:
                log.warning("count_tokens lỗi (%s), dùng ước lượng", e.code)
            summary_mode = est > TOKEN_LIMIT
        if summary_mode and segments:
            # Khối 30 phút nằm trọn trước mốc (hiện tại − 30 phút) được thay bằng tóm tắt (lưu DB, tạo một
            # lần); phần sau giữ nguyên văn. Cửa sổ nguyên văn vì thế dài 30–60 phút, căn theo khối để các
            # bản tóm tắt ổn định (không phải tạo lại mỗi câu hỏi, prefix cache không đổi).
            last_block = segments[-1].t_start_ms // BLOCK_MS
            verbatim_from_block = max(0, (segments[-1].t_start_ms - BLOCK_MS) // BLOCK_MS)
            verbatim_from_block = min(verbatim_from_block, last_block)
            summaries = await self._block_summaries(session_id, segments, verbatim_from_block)
            verbatim = [sg for sg in segments if sg.t_start_ms // BLOCK_MS >= verbatim_from_block]
            transcript_blocks, recent = self._transcript_blocks(verbatim, summary_lines=summaries)
            est = estimate_tokens(context_blocks + transcript_blocks, doc_pages)

        messages: list[dict[str, Any]] = [{"role": "user", "content": context_blocks + transcript_blocks}]

        history = self.history(session_id, "qa", limit=HISTORY_MESSAGES + 1, thread_id=thread_id)
        # Bỏ tin nhắn người dùng vừa lưu (câu hỏi hiện tại) và đảm bảo lịch sử bắt đầu bằng "user".
        if history and history[-1].role == "user" and history[-1].content == question:
            history = history[:-1]
        history = history[-HISTORY_MESSAGES:]
        while history and history[0].role != "user":
            history = history[1:]
        # Ảnh của các câu hỏi trước trong cuộc trò chuyện (ưu tiên gần nhất, tối đa HISTORY_IMAGES).
        attached = self.attachments([m.id for m in history if m.role == "user" and m.id is not None])
        budget = HISTORY_IMAGES
        history_images: dict[int, list[str]] = {}
        for msg in reversed(history):
            paths = [a.image_path for a in attached.get(msg.id or 0, [])]
            if paths and budget > 0:
                history_images[msg.id or 0] = paths[:budget]
                budget -= len(history_images[msg.id or 0])
        n_images = 0
        for i, msg in enumerate(history):
            content: list[dict[str, Any]] = []
            for path in history_images.get(msg.id or 0, []):
                blk = image_block(path)
                if blk is not None:
                    content.append(blk)
                    n_images += 1
            block: dict[str, Any] = {"type": "text", "text": msg.content}
            if i == len(history) - 1:
                block["cache_control"] = CACHE_5M
            content.append(block)
            messages.append({"role": msg.role, "content": content})

        current_images = [b for b in (image_block(p) for p in images or []) if b is not None]
        n_images += len(current_images)
        est += n_images * IMAGE_TOKENS

        now_ts = fmt_ts(segments[-1].t_end_ms) if segments else "00:00"
        tail = recent or "(chưa có dòng mới)"
        final = [
            _text(f"<transcript_recent>\n{tail}\n</transcript_recent>\nThời điểm hiện tại của buổi học: [{now_ts}]."),
            *current_images,
            _text(question),
        ]
        messages.append({"role": "user", "content": final})
        return ChatRequest(system=system, messages=messages, summary_mode=summary_mode, est_tokens=est)

    def _transcript_blocks(
        self, segments: list[Segment], summary_lines: list[str] | None
    ) -> tuple[list[dict[str, Any]], str]:
        blocks: list[dict[str, Any]] = []
        if summary_lines:
            blocks.append(
                _text("<transcript_summary>\n" + "\n\n".join(summary_lines) + "\n</transcript_summary>")
            )
        closed, tail = split_chunks(segments)
        for idx, chunk in enumerate(closed):
            body = "\n".join(transcript_line(sg) for sg in chunk)
            blocks.append(_text(f'<transcript part="{idx + 1}">\n{body}\n</transcript>'))
        if blocks:
            blocks[-1] = {**blocks[-1], "cache_control": CACHE_1H}
        recent = "\n".join(transcript_line(sg) for sg in tail)
        return blocks, recent

    # ------------------------------------------------------------------ Q&A
    async def answer(
        self,
        session_id: int,
        question: str,
        on_text: Callable[[str], None],
        thread_id: int | None = None,
        images: list[SavedImage] | None = None,
    ) -> tuple[ChatMessage, dict[str, Any]]:
        settings = self._settings.get()
        user_msg = self._save_message(session_id, "user", question, "qa", None, thread_id)
        if images:
            assert user_msg.id is not None
            self._attach(session_id, user_msg.id, images)
        image_paths = [img.image_path for img in images or []]
        req = await self.build_request(session_id, question, thread_id=thread_id, images=image_paths)
        try:
            result = await self._llm.stream(
                purpose="chat",
                session_id=session_id,
                model=settings.assistant_model,
                max_tokens=8000,
                system=req.system,
                messages=req.messages,
                route="fast",
                on_text=on_text,
            )
        except LLMError as e:
            if e.code != "context_too_long" or req.summary_mode:
                raise
            log.info("Ngữ cảnh quá dài, chuyển sang rolling summary (E-LLM-400)")
            req = await self.build_request(
                session_id, question, force_summary=True, thread_id=thread_id, images=image_paths
            )
            result = await self._llm.stream(
                purpose="chat",
                session_id=session_id,
                model=settings.assistant_model,
                max_tokens=8000,
                system=req.system,
                messages=req.messages,
                route="fast",
                on_text=on_text,
            )
        text = result.text
        if result.stop_reason == "max_tokens":
            text += "\n\n_(Câu trả lời bị cắt do quá dài.)_"
        msg = self._save_message(session_id, "assistant", text, "qa", result.model, thread_id)
        meta = {
            "usage": result.usage.as_dict(),
            "summary_mode": req.summary_mode,
            "model": result.model,
        }
        return msg, meta

    # ----------------------------------------------------------- compose_it
    async def compose_it(self, session_id: int, text_vi: str) -> tuple[ChatMessage, dict[str, Any]]:
        settings = self._settings.get()
        st = self._context.static(session_id)
        self._save_message(session_id, "user", text_vi, "compose_it", None)
        segments = self._segments(session_id)
        recent_from = (segments[-1].t_end_ms - 10 * 60 * 1000) if segments else 0
        recent = "\n".join(transcript_line(sg, with_vi=False) for sg in segments if sg.t_end_ms >= recent_from)
        system = self._prompts.render(
            "compose_it_v1",
            course_name=st.course_name + (f" ({st.course_name_it})" if st.course_name_it else ""),
            lecturer=st.lecturer or "(chưa rõ)",
        )
        user = (
            f"<glossary>\n{format_glossary(st.glossary)}\n</glossary>\n\n"
            f"<transcript_recent>\n{recent or '(chưa có)'}\n</transcript_recent>\n\n"
            f"Câu hỏi của học viên (tiếng Việt):\n{text_vi}"
        )
        data, result = await self._llm.json(
            purpose="compose",
            session_id=session_id,
            model=settings.assistant_model,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user}],
            schema=COMPOSE_SCHEMA,
            route="fast",
        )
        if not isinstance(data, dict) or not data.get("question_it"):
            raise LLMError("bad_request", "Claude không trả về câu hỏi tiếng Ý.")
        payload = {
            "question_it": str(data.get("question_it", "")).strip(),
            "back_translation_vi": str(data.get("back_translation_vi", "")).strip(),
            "notes": str(data.get("notes", "")).strip(),
        }
        msg = self._save_message(
            session_id, "assistant", json.dumps(payload, ensure_ascii=False), "compose_it", result.model
        )
        return msg, payload

    # ------------------------------------------------------------ speak_it
    async def compose_speak(self, session_id: int, text_vi: str) -> tuple[ChatMessage, dict[str, Any]]:
        """IC-US-10: câu hỏi tiếng Việt → câu tiếng Ý ≤ 25 từ để nói trong lớp, kèm phiên âm nhấn âm."""
        settings = self._settings.get()
        st = self._context.static(session_id)
        self._save_message(session_id, "user", text_vi, "speak_it", None)
        segments = self._segments(session_id)
        recent_from = (segments[-1].t_end_ms - 5 * 60 * 1000) if segments else 0
        recent = "\n".join(transcript_line(sg, with_vi=False) for sg in segments if sg.t_end_ms >= recent_from)
        system = self._prompts.render(
            "speak_it_v1",
            course_name=st.course_name + (f" ({st.course_name_it})" if st.course_name_it else ""),
            lecturer=st.lecturer or "(chưa rõ)",
        )
        user = (
            f"<glossary>\n{format_glossary(st.glossary)}\n</glossary>\n\n"
            f"<transcript_recent>\n{recent or '(chưa có)'}\n</transcript_recent>\n\n"
            f"Câu hỏi của học viên (tiếng Việt):\n{text_vi}"
        )
        data, result = await self._llm.json(
            purpose="speak",
            session_id=session_id,
            model=settings.assistant_model,
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": user}],
            schema=SPEAK_SCHEMA,
            route="fast",
        )
        if not isinstance(data, dict) or not data.get("question_it"):
            raise LLMError("bad_request", "Claude không trả về câu hỏi tiếng Ý.")
        payload = {
            "question_it": str(data.get("question_it", "")).strip(),
            "pronunciation": str(data.get("pronunciation", "")).strip(),
            "back_translation_vi": str(data.get("back_translation_vi", "")).strip(),
            "tip": str(data.get("tip", "")).strip(),
        }
        payload["word_count"] = len(payload["question_it"].split())
        msg = self._save_message(
            session_id, "assistant", json.dumps(payload, ensure_ascii=False), "speak_it", result.model
        )
        return msg, payload

    # ------------------------------------------------------------- consent
    async def consent_message(
        self, *, course_name: str, lecturer: str | None, student_name: str
    ) -> dict[str, str]:
        """IC-FR-81: email tiếng Ý xin phép giảng viên (Claude soạn, người dùng chỉnh sửa)."""
        settings = self._settings.get()
        system = self._prompts.render(
            "consent_message_v1",
            course_name=course_name,
            lecturer=lecturer or "(chưa rõ)",
            student_name=student_name or "(sinh viên)",
        )
        data, _ = await self._llm.json(
            purpose="consent",
            session_id=None,
            model=settings.assistant_model,
            max_tokens=2500,
            system=system,
            messages=[{"role": "user", "content": "Hãy soạn email xin phép theo đúng yêu cầu."}],
            schema=CONSENT_SCHEMA,
            route="fast",
        )
        if not isinstance(data, dict) or not data.get("email_it"):
            raise LLMError("bad_request", "Claude không trả về email tiếng Ý.")
        return {
            "subject_it": str(data.get("subject_it", "")).strip(),
            "email_it": str(data.get("email_it", "")).strip(),
            "back_translation_vi": str(data.get("back_translation_vi", "")).strip(),
        }


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}
