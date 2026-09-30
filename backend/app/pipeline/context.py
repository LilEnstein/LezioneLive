"""Tải ngữ cảnh (môn học, thuật ngữ, tóm tắt slide, segment trước) từ DB cho dịch và trợ lý."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy import Engine, or_
from sqlmodel import col, select

from app.config import INCLASS_SOURCES
from app.db.models import Course, GlossaryTerm, LectureSession, Segment, SlideDeck
from app.db.session import db_session
from app.llm.board_ocr import board_context, recent_board_captures
from app.llm.translator import GlossaryEntry, TranslationContext
from app.services.settings_store import SettingsStore


@dataclass
class StaticContext:
    session_id: int
    title: str
    course_id: int | None
    course_name: str
    course_name_it: str | None
    lecturer: str | None
    glossary: list[GlossaryEntry] = field(default_factory=list)
    slide_summary: str | None = None
    inclass: bool = False


def load_glossary(engine: Engine, course_id: int | None) -> list[GlossaryEntry]:
    with db_session(engine) as s:
        stmt = select(GlossaryTerm)
        if course_id is None:
            stmt = stmt.where(col(GlossaryTerm.course_id).is_(None))
        else:
            stmt = stmt.where(or_(GlossaryTerm.course_id == course_id, col(GlossaryTerm.course_id).is_(None)))
        # Thứ tự ổn định để prompt không đổi byte giữa các request (prompt caching).
        rows = s.exec(stmt.order_by(GlossaryTerm.term_it, GlossaryTerm.id)).all()
    return [GlossaryEntry(r.term_it, r.term_vi, r.term_en, r.note) for r in rows]


class ContextProvider:
    def __init__(self, engine: Engine, settings: SettingsStore, ttl: float = 15.0) -> None:
        self._engine = engine
        self._settings = settings
        self._ttl = ttl
        self._cache: dict[int, tuple[float, StaticContext]] = {}

    def invalidate(self, session_id: int | None = None) -> None:
        if session_id is None:
            self._cache.clear()
        else:
            self._cache.pop(session_id, None)

    def static(self, session_id: int) -> StaticContext:
        now = time.monotonic()
        hit = self._cache.get(session_id)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        with db_session(self._engine) as s:
            sess = s.get(LectureSession, session_id)
            if sess is None:
                raise KeyError(session_id)
            course = s.get(Course, sess.course_id) if sess.course_id else None
            decks = s.exec(
                select(SlideDeck)
                .where(SlideDeck.session_id == session_id, SlideDeck.status == "ready")
                .order_by(SlideDeck.id)
            ).all()
        summaries = [f"[{d.filename}]\n{d.summary}" for d in decks if d.summary]
        ctx = StaticContext(
            session_id=session_id,
            title=sess.title,
            course_id=sess.course_id,
            course_name=(course.name if course else sess.title),
            course_name_it=course.name_it if course else None,
            lecturer=course.lecturer if course else None,
            glossary=load_glossary(self._engine, sess.course_id),
            slide_summary="\n\n".join(summaries) or None,
            inclass=(sess.capture_source or sess.audio_mode) in INCLASS_SOURCES,
        )
        self._cache[session_id] = (now, ctx)
        return ctx

    def translation_context(self, segment: Segment) -> TranslationContext:
        settings = self._settings.get()
        st = self.static(segment.session_id)
        previous: list[tuple[str, str | None]] = []
        n = settings.context_segments
        if n > 0:
            with db_session(self._engine) as s:
                rows = s.exec(
                    select(Segment)
                    .where(
                        Segment.session_id == segment.session_id,
                        Segment.seq < segment.seq,
                        Segment.status != "redacted",
                    )
                    .order_by(col(Segment.seq).desc())
                    .limit(n)
                ).all()
            # Bản dịch máy (nháp ngoại tuyến / model cục bộ) không đưa vào ngữ cảnh (tránh lan lỗi thuật ngữ).
            previous = [
                (r.source_text, r.text_vi if r.translation_kind not in ("draft", "local") else None) for r in reversed(rows)
            ]
        course = st.course_name
        if st.course_name_it:
            course = f"{st.course_name} ({st.course_name_it})"
        board_text = None
        if st.inclass:
            board_text = board_context(recent_board_captures(self._engine, segment.session_id, segment.t_start_ms))
        return TranslationContext(
            course_name=course,
            glossary=st.glossary,
            previous=previous,
            slide_summary=st.slide_summary,
            secondary_language=settings.secondary_language,
            inclass=st.inclass,
            speaker=segment.speaker or "unknown",
            recent_board_text=board_text or None,
        )

    def stt_prompt(self, session_id: int) -> tuple[str, list[str]]:
        """initial_prompt cho Whisper (≈ 200 token) + danh sách keyword cho STT cloud (FR-STT-04)."""
        st = self.static(session_id)
        terms = [g.term_it for g in st.glossary if g.term_it]
        head = f"Lezione universitaria di {st.course_name_it or st.course_name}."
        prompt = head
        if terms:
            prompt += " Termini: " + ", ".join(terms)
        if len(prompt) > 800:
            prompt = prompt[:800].rsplit(",", 1)[0] + "."
        return prompt, terms[:100]
