"""Trích flashcard từ vựng (FR-LE-01) và lịch ôn SM-2 đơn giản (FR-LE-02)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine
from sqlmodel import col, select

from app.db.models import Flashcard, Segment, utcnow
from app.db.session import db_session
from app.llm.client import LLMClient, LLMError
from app.llm.prompt_library import PromptLibrary
from app.pipeline.context import ContextProvider
from app.services.settings_store import SettingsStore

MIN_CARDS = 20
MAX_CARDS = 40
RELEARN_MINUTES = 10

FLASHCARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "front_it": {"type": "string"},
                    "back_vi": {"type": "string"},
                    "pos": {"type": "string"},
                    "example_it": {"type": "string"},
                    "example_vi": {"type": "string"},
                },
                "required": ["front_it", "back_vi", "pos", "example_it", "example_vi"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["cards"],
    "additionalProperties": False,
}

_ARTICLES = re.compile(r"^(il|lo|la|i|gli|le|l'|un|uno|una|un')\s*", re.I)


# ---------------------------------------------------------------------------
# SM-2
# ---------------------------------------------------------------------------


@dataclass
class ReviewResult:
    ease: float
    interval_days: int
    repetitions: int
    due_at: datetime


def sm2(ease: float, interval_days: int, repetitions: int, grade: int, now: datetime | None = None) -> ReviewResult:
    """SM-2: grade 0..5. Trả lời sai (<3) thì học lại sau 10 phút, lặp lại từ đầu."""
    if not 0 <= grade <= 5:
        raise ValueError("grade phải trong khoảng 0..5")
    now = now or utcnow()
    if grade < 3:
        repetitions = 0
        interval_days = 0
        due = now + timedelta(minutes=RELEARN_MINUTES)
    else:
        if repetitions == 0:
            interval_days = 1
        elif repetitions == 1:
            interval_days = 6
        else:
            interval_days = max(1, round(interval_days * ease))
        repetitions += 1
        due = now + timedelta(days=interval_days)
    ease = max(1.3, ease + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02)))
    return ReviewResult(round(ease, 4), interval_days, repetitions, due)


# ---------------------------------------------------------------------------
# Trích flashcard
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9' ]+", " ", text).strip()


def _headword(front_it: str) -> str:
    word = _ARTICLES.sub("", front_it.strip())
    return _norm(word).split(" ")[0] if word else ""


def find_example(front_it: str, example_it: str, segments: list[Segment]) -> Segment | None:
    """Ví dụ phải lấy từ transcript (AC-13): nếu câu Claude đưa không khớp, tìm segment chứa từ đó."""
    ex = _norm(example_it)
    if ex:
        for seg in segments:
            if ex in _norm(seg.source_text) or _norm(seg.source_text) in ex and len(_norm(seg.source_text)) > 20:
                return seg
    head = _headword(front_it)
    if len(head) >= 3:
        stem = head[: max(3, len(head) - 2)]  # chịu được biến cách: variabile/variabili
        for seg in segments:
            if re.search(rf"\b{re.escape(stem)}", _norm(seg.source_text)):
                return seg
    return None


class FlashcardService:
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

    def _known_words(self, exclude_session: int) -> list[str]:
        with db_session(self._engine) as s:
            rows = s.exec(
                select(Flashcard.front_it)
                .where((col(Flashcard.session_id).is_(None)) | (Flashcard.session_id != exclude_session))
                .order_by(col(Flashcard.id).desc())
                .limit(300)
            ).all()
        return list(rows)

    async def generate(self, session_id: int, *, replace: bool = True) -> list[Flashcard]:
        settings = self._settings.get()
        st = self._context.static(session_id)
        with db_session(self._engine) as s:
            segments = list(
                s.exec(
                    select(Segment)
                    .where(Segment.session_id == session_id, Segment.status != "redacted")
                    .order_by(Segment.seq)
                )
            )
        if not segments:
            raise LLMError("bad_request", "Phiên chưa có transcript để tạo flashcard.")
        known = self._known_words(session_id)
        system = self._prompts.render(
            "flashcards_v1",
            course_name=st.course_name,
            known_words=", ".join(known) if known else "(chưa có)",
        )
        transcript = "\n".join(sg.source_text for sg in segments)
        data, _ = await self._llm.json(
            purpose="flashcard",
            session_id=session_id,
            model=settings.assistant_model,
            max_tokens=12000,
            system=system,
            messages=[{"role": "user", "content": f"<transcript>\n{transcript}\n</transcript>"}],
            schema=FLASHCARD_SCHEMA,
            route="fast",
        )
        raw = data.get("cards", []) if isinstance(data, dict) else []
        known_norm = {_norm(k) for k in known}
        seen: set[str] = set()
        cards: list[Flashcard] = []
        now = utcnow()
        for item in raw:
            if not isinstance(item, dict):
                continue
            front = str(item.get("front_it", "")).strip()
            back = str(item.get("back_vi", "")).strip()
            key = _norm(front)
            if not front or not back or key in seen or key in known_norm:
                continue
            seen.add(key)
            example_it = str(item.get("example_it", "")).strip()
            example_vi = str(item.get("example_vi", "")).strip()
            seg = find_example(front, example_it, segments)
            if seg is not None and _norm(example_it) not in _norm(seg.source_text):
                example_it = seg.source_text
                example_vi = seg.text_vi or example_vi
            cards.append(
                Flashcard(
                    session_id=session_id,
                    front_it=front,
                    back_vi=back,
                    pos=str(item.get("pos", "")).strip() or None,
                    example_it=example_it or None,
                    example_vi=example_vi or None,
                    due_at=now,
                )
            )
            if len(cards) >= MAX_CARDS:
                break
        with db_session(self._engine) as s:
            if replace:
                for old in s.exec(select(Flashcard).where(Flashcard.session_id == session_id)):
                    if old.repetitions == 0 and old.last_reviewed_at is None:
                        s.delete(old)
            for c in cards:
                s.add(c)
            s.commit()
            for c in cards:
                s.refresh(c)
        return cards
