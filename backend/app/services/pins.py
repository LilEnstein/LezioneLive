"""Tạo ghim (dùng chung cho laptop và điện thoại đồng hành)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlmodel import col, select

from app.db.models import Pin, Segment
from app.db.session import db_session
from app.routers.deps import api_error
from app.schemas import pin_out

if TYPE_CHECKING:
    from app.context import AppContext


def create_pin(
    ctx: AppContext, session_id: int, segment_id: int | None, kind: str, note: str | None
) -> dict[str, Any]:
    """Không có ``segment_id`` thì ghim segment mới nhất (FR-PIN-01)."""
    with db_session(ctx.engine) as s:
        seg_id = segment_id
        if seg_id is None:
            latest = s.exec(
                select(Segment).where(Segment.session_id == session_id).order_by(col(Segment.seq).desc()).limit(1)
            ).first()
            if latest is None:
                raise api_error(409, "NO_SEGMENT", "Chưa có đoạn nào để ghim")
            seg_id = latest.id
        else:
            seg = s.get(Segment, seg_id)
            if seg is None or seg.session_id != session_id:
                raise api_error(404, "SEGMENT_NOT_FOUND", "Không tìm thấy segment")
        existing = s.exec(select(Pin).where(Pin.session_id == session_id, Pin.segment_id == seg_id)).first()
        if existing is not None and segment_id is None:
            return pin_out(existing)  # bấm 📌 nhiều lần trên điện thoại: không tạo ghim trùng
        pin = Pin(session_id=session_id, segment_id=seg_id, kind=kind, note=note)
        s.add(pin)
        s.commit()
        s.refresh(pin)
    ctx.hub.publish(session_id, {"type": "pin.created", "pin": pin_out(pin)})
    return pin_out(pin)
