"""Tóm tắt sau buổi học (FR-POST-01, prompt mục 11.3)."""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import Engine
from sqlmodel import select

from app.db.models import BoardCapture, Pin, Segment, Summary, utcnow
from app.db.session import db_session
from app.llm.board_ocr import board_context
from app.llm.client import LLMClient
from app.llm.prompt_library import PromptLibrary
from app.llm.translator import format_glossary
from app.pipeline.context import ContextProvider
from app.services.settings_store import SettingsStore
from app.services.timefmt import fmt_ts

REQUIRED_SECTIONS = [
    "Tổng quan buổi học",
    "Ý chính",
    "Khái niệm & định nghĩa",
    "Công thức",
    "Bài tập / Deadline / Thông báo hành chính",
    "Những đoạn cần xem lại",
    "Câu hỏi gợi ý để hỏi giảng viên",
]

PIN_LABELS = {"confused": "chưa hiểu", "important": "quan trọng", "todo": "việc cần làm"}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c)).replace("đ", "d")
    text = re.sub(r"\(.*?\)", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def ensure_sections(md: str) -> str:
    """Bảo đảm đủ 7 mục cố định (AC-09); mục nào thiếu thì thêm vào cuối với nội dung 'Không có'."""
    present = {_norm(m.group(1)) for m in re.finditer(r"^##\s+(.+?)\s*$", md, flags=re.M)}
    missing = [h for h in REQUIRED_SECTIONS if _norm(h) not in present]
    out = md.rstrip()
    for heading in missing:
        out += f"\n\n## {heading}\n\n_Không có._"
    return out + "\n"


class Summarizer:
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

    def build_user_content(self, session_id: int) -> str:
        with db_session(self._engine) as s:
            # Lời người khác đã xoá ("Không lưu lời người khác") không được đưa vào tóm tắt (IC-AC-05).
            segments = list(
                s.exec(
                    select(Segment)
                    .where(Segment.session_id == session_id, Segment.status != "redacted")
                    .order_by(Segment.seq)
                )
            )
            pins = list(s.exec(select(Pin).where(Pin.session_id == session_id).order_by(Pin.id)))
            boards = list(
                s.exec(
                    select(BoardCapture)
                    .where(BoardCapture.session_id == session_id, BoardCapture.status == "done")
                    .order_by(BoardCapture.t_ms)
                )
            )
        by_id = {sg.id: sg for sg in segments}
        st = self._context.static(session_id)

        transcript = "\n".join(
            f"[{fmt_ts(sg.t_start_ms)}]{' (sinh viên hỏi)' if sg.speaker == 'other' else ''} "
            f"{sg.source_text}{' (?)' if sg.confidence < 0.5 else ''}"
            for sg in segments
        )
        pin_lines = []
        for p in pins:
            seg = by_id.get(p.segment_id) if p.segment_id else None
            where = f"[{fmt_ts(seg.t_start_ms)}] {seg.source_text}" if seg else "(không gắn segment)"
            note = f" — ghi chú: {p.note}" if p.note else ""
            pin_lines.append(f"- ({PIN_LABELS.get(p.kind, p.kind)}) {where}{note}")
        low = [f"- [{fmt_ts(sg.t_start_ms)}] {sg.source_text}" for sg in segments if sg.confidence < 0.5]

        parts = [
            f"<transcript>\n{transcript or '(trống)'}\n</transcript>",
            f"<pins>\n{chr(10).join(pin_lines) or '(không có)'}\n</pins>",
            f"<low_confidence>\n{chr(10).join(low[:80]) or '(không có)'}\n</low_confidence>",
            f"<glossary>\n{format_glossary(st.glossary)}\n</glossary>",
        ]
        if st.slide_summary:
            parts.append(f"<slides_summary>\n{st.slide_summary}\n</slides_summary>")
        if boards:
            parts.append(f"<board_captures>\n{board_context(boards)}\n</board_captures>")
        parts.append("Hãy viết ghi chú sau buổi học theo đúng 7 mục đã quy định.")
        return "\n\n".join(parts)

    async def generate(self, session_id: int) -> Summary:
        settings = self._settings.get()
        st = self._context.static(session_id)
        system = self._prompts.render(
            "summary_v1",
            course_name=st.course_name + (f" ({st.course_name_it})" if st.course_name_it else ""),
            lecturer=st.lecturer or "(chưa rõ)",
        )
        result = await self._llm.stream(
            purpose="summary",
            session_id=session_id,
            model=settings.assistant_model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": self.build_user_content(session_id)}],
            route="deep",
        )
        content = ensure_sections(result.text)
        with db_session(self._engine) as s:
            row = s.get(Summary, session_id)
            if row is None:
                row = Summary(session_id=session_id, content_md=content, model=result.model)
            else:
                row.content_md = content
                row.model = result.model
                row.created_at = utcnow()
            s.add(row)
            s.commit()
            s.refresh(row)
        return row
