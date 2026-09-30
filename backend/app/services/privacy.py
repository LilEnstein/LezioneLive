"""Quyền riêng tư trong lớp học (feature_spec mục 11): "Không lưu lời người khác".

Segment gán nhãn ``other`` chỉ hiển thị trong lúc học: sau khi dịch xong (và chắc chắn khi kết thúc phiên),
nội dung tiếng Ý/bản dịch bị xoá khỏi DB (trạng thái ``redacted``), nên không vào tìm kiếm, tóm tắt hay export.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Engine
from sqlmodel import col, select

from app.db.models import Segment
from app.db.session import db_session

REDACTED_STATUS = "redacted"


def redact_segments(engine: Engine, ids: Iterable[int]) -> list[int]:
    """Xoá nội dung các segment ``other`` (bỏ qua segment đang chờ dịch). Trả về id đã xoá."""
    wanted = list(ids)
    if not wanted:
        return []
    done: list[int] = []
    with db_session(engine) as s:
        for seg in s.exec(select(Segment).where(col(Segment.id).in_(wanted))):
            if seg.speaker != "other" or seg.status == REDACTED_STATUS:
                continue
            seg.text_it = ""
            seg.text_it_edited = None
            seg.text_vi = None
            seg.text_en = None
            seg.translation_kind = None
            seg.status = REDACTED_STATUS
            seg.error = None
            s.add(seg)
            done.append(seg.id)  # type: ignore[arg-type]
        s.commit()
    return done


def redact_session_others(engine: Engine, session_id: int) -> list[int]:
    """Kết thúc phiên: xoá mọi lời người khác còn lại (IC-AC-05)."""
    with db_session(engine) as s:
        ids = list(
            s.exec(
                select(Segment.id).where(
                    Segment.session_id == session_id, Segment.speaker == "other", Segment.status != REDACTED_STATUS
                )
            )
        )
    return redact_segments(engine, [i for i in ids if i is not None])
