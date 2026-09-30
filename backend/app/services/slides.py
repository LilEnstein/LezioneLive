"""Upload slide PDF (FR-SL-01/02): lưu cục bộ, trích text, upload Files API, tóm tắt + đề xuất thuật ngữ."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlmodel import select

from app.db.models import GlossarySuggestion, GlossaryTerm, SlideDeck
from app.db.session import db_session
from app.llm.client import LLMError

if TYPE_CHECKING:
    from app.context import AppContext

log = logging.getLogger(__name__)

MAX_BYTES = 30 * 1024 * 1024
MAX_PAGES = 100
INLINE_ANALYSIS_MAX_BYTES = 20 * 1024 * 1024
MIN_TEXT_CHARS_PER_PAGE = 80

SLIDES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term_it": {"type": "string"},
                    "term_vi": {"type": "string"},
                    "term_en": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["term_it", "term_vi", "term_en", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "terms"],
    "additionalProperties": False,
}


class SlideError(ValueError):
    pass


def extract_pages(data: bytes) -> list[str]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:  # noqa: BLE001
                raise SlideError("PDF có mật khẩu, không đọc được.") from e
        pages = []
        for page in reader.pages:
            try:
                pages.append((page.extract_text() or "").strip())
            except Exception:  # noqa: BLE001 - trang lỗi vẫn giữ chỗ
                pages.append("")
        return pages
    except PdfReadError as e:
        raise SlideError("File PDF bị lỗi hoặc không đúng định dạng.") from e


def pages_to_text(pages: list[str]) -> str:
    return "\n\n".join(f"--- Trang {i + 1} ---\n{text}" for i, text in enumerate(pages))


def safe_filename(name: str) -> str:
    base = Path(name).name
    base = re.sub(r"[^\w.\- ]+", "_", base, flags=re.UNICODE).strip() or "slides.pdf"
    return base[:120]


class SlideService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._tasks: set[asyncio.Task] = set()

    def validate(self, filename: str, data: bytes) -> None:
        if len(data) > MAX_BYTES:
            raise SlideError(f"File quá lớn ({len(data) / 1_048_576:.1f} MB). Giới hạn 30 MB.")
        if not data.startswith(b"%PDF"):
            raise SlideError("Chỉ hỗ trợ file PDF.")

    async def add(self, session_id: int, filename: str, data: bytes) -> SlideDeck:
        self.validate(filename, data)
        pages = await asyncio.to_thread(extract_pages, data)
        if len(pages) > MAX_PAGES:
            raise SlideError(f"PDF có {len(pages)} trang, vượt giới hạn {MAX_PAGES} trang.")
        name = safe_filename(filename)
        folder = self.ctx.env.data_dir / "slides" / str(session_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{uuid.uuid4().hex[:8]}_{name}"
        path.write_bytes(data)
        deck = SlideDeck(
            session_id=session_id,
            filename=name,
            path=str(path),
            page_count=len(pages),
            size_bytes=len(data),
            extracted_text=pages_to_text(pages),
            status="processing",
        )
        with db_session(self.ctx.engine) as s:
            s.add(deck)
            s.commit()
            s.refresh(deck)
        task = asyncio.create_task(self._process(deck.id))  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return deck

    def _update(self, deck_id: int, **fields: Any) -> SlideDeck | None:
        with db_session(self.ctx.engine) as s:
            deck = s.get(SlideDeck, deck_id)
            if deck is None:
                return None
            for k, v in fields.items():
                setattr(deck, k, v)
            s.add(deck)
            s.commit()
            s.refresh(deck)
            return deck

    async def _process(self, deck_id: int) -> None:
        with db_session(self.ctx.engine) as s:
            deck = s.get(SlideDeck, deck_id)
        if deck is None:
            return
        llm = self.ctx.llm
        settings = self.ctx.settings.get()
        # Không có API key nhưng trợ lý dùng gói Pro: phân tích text trích từ PDF qua Claude Code.
        via_cc = (
            not llm.has_key and self.ctx.assistant_llm.uses_claude_code and self.ctx.assistant_llm.has_key
        )
        if not llm.has_key and not via_cc:
            self._update(deck_id, status="ready", error="Chưa có API key: chỉ dùng text trích từ PDF.")
            self._publish(deck.session_id, deck_id)
            return
        data = Path(deck.path).read_bytes()
        file_id = None
        if settings.slides_mode == "document" and not via_cc:
            try:
                file_id = await llm.upload_file(deck.filename, data, "application/pdf")
                self._update(deck_id, file_id=file_id)
            except LLMError as e:
                log.warning("Upload slide lên Files API thất bại (%s), dùng text", e.code)
        try:
            summary, terms = await self._analyze(deck, data, file_id, via_cc=via_cc)
        except LLMError as e:
            self._update(deck_id, status="ready", error=f"Không phân tích được slide: {e.message}")
            self._publish(deck.session_id, deck_id)
            return
        self._update(deck_id, summary=summary, status="ready", error=None)
        added = self._save_suggestions(deck, terms)
        self.ctx.context.invalidate(deck.session_id)
        self._publish(deck.session_id, deck_id, suggestions=added)

    async def _analyze(
        self, deck: SlideDeck, data: bytes, file_id: str | None, *, via_cc: bool = False
    ) -> tuple[str, list[dict]]:
        settings = self.ctx.settings.get()
        st = self.ctx.context.static(deck.session_id)
        existing = ", ".join(g.term_it for g in st.glossary[:200]) or "(chưa có)"
        system = self.ctx.prompts.render("slides_v1", course_name=st.course_name, existing_terms=existing)
        text_ok = len(deck.extracted_text) >= MIN_TEXT_CHARS_PER_PAGE * max(1, deck.page_count)
        if text_ok or via_cc:
            content: list[dict[str, Any]] = [
                {"type": "text", "text": f'<slides file="{deck.filename}">\n{deck.extracted_text}\n</slides>'}
            ]
        elif file_id:
            content = [
                {"type": "document", "source": {"type": "file", "file_id": file_id}, "title": deck.filename}
            ]
        elif len(data) <= INLINE_ANALYSIS_MAX_BYTES:
            b64 = base64.standard_b64encode(data).decode("ascii")
            content = [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                    "title": deck.filename,
                }
            ]
        else:
            content = [{"type": "text", "text": f"<slides>\n{deck.extracted_text}\n</slides>"}]
        content.append({"type": "text", "text": "Hãy tóm tắt slide và trích thuật ngữ theo yêu cầu."})
        llm: Any = self.ctx.assistant_llm if via_cc else self.ctx.llm
        result, _ = await llm.json(
            purpose="slides",
            session_id=deck.session_id,
            model=settings.translate_model,
            max_tokens=6000,
            system=system,
            messages=[{"role": "user", "content": content}],
            schema=SLIDES_SCHEMA,
            route="fast",
        )
        summary = str(result.get("summary", "")).strip() if isinstance(result, dict) else ""
        terms = result.get("terms", []) if isinstance(result, dict) else []
        return summary, [t for t in terms if isinstance(t, dict)]

    def _save_suggestions(self, deck: SlideDeck, terms: list[dict]) -> int:
        st = self.ctx.context.static(deck.session_id)
        known = {g.term_it.strip().lower() for g in st.glossary}
        added = 0
        with db_session(self.ctx.engine) as s:
            pending = {
                r.term_it.strip().lower()
                for r in s.exec(
                    select(GlossarySuggestion).where(GlossarySuggestion.session_id == deck.session_id)
                )
            }
            for t in terms:
                term_it = str(t.get("term_it", "")).strip()
                term_vi = str(t.get("term_vi", "")).strip()
                key = term_it.lower()
                if not term_it or not term_vi or key in known or key in pending:
                    continue
                pending.add(key)
                s.add(
                    GlossarySuggestion(
                        session_id=deck.session_id,
                        deck_id=deck.id,
                        course_id=st.course_id,
                        term_it=term_it,
                        term_vi=term_vi,
                        term_en=str(t.get("term_en", "")).strip() or None,
                        note=str(t.get("note", "")).strip() or None,
                    )
                )
                added += 1
            s.commit()
        return added

    def _publish(self, session_id: int, deck_id: int, suggestions: int = 0) -> None:
        self.ctx.hub.publish(
            session_id, {"type": "slides.updated", "deck_id": deck_id, "suggestions": suggestions}
        )

    async def delete(self, deck_id: int) -> bool:
        with db_session(self.ctx.engine) as s:
            deck = s.get(SlideDeck, deck_id)
            if deck is None:
                return False
            session_id, path, file_id = deck.session_id, deck.path, deck.file_id
            s.delete(deck)
            s.commit()
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
        if file_id and self.ctx.llm.has_key:
            try:
                await self.ctx.llm.delete_file(file_id)
            except LLMError as e:
                log.warning("Không xoá được file %s trên Files API: %s", file_id, e.code)
        self.ctx.context.invalidate(session_id)
        return True

    def accept_suggestion(self, suggestion_id: int, *, term_vi: str | None = None) -> GlossaryTerm | None:
        with db_session(self.ctx.engine) as s:
            sug = s.get(GlossarySuggestion, suggestion_id)
            if sug is None or sug.status != "pending":
                return None
            term = GlossaryTerm(
                course_id=sug.course_id,
                term_it=sug.term_it,
                term_vi=term_vi or sug.term_vi,
                term_en=sug.term_en,
                note=sug.note,
                source="slides",
            )
            sug.status = "accepted"
            s.add(term)
            s.add(sug)
            s.commit()
            s.refresh(term)
            session_id = sug.session_id
        self.ctx.context.invalidate(session_id)
        return term

    def reject_suggestion(self, suggestion_id: int) -> bool:
        with db_session(self.ctx.engine) as s:
            sug = s.get(GlossarySuggestion, suggestion_id)
            if sug is None:
                return False
            sug.status = "rejected"
            s.add(sug)
            s.commit()
        return True
