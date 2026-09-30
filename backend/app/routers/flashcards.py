"""/flashcards: thẻ đến hạn, chấm điểm SM-2 (Flow F), sửa/xoá."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlmodel import col, select

from app.context import AppContext
from app.db.models import Flashcard, LectureSession, utcnow
from app.db.session import db_session
from app.llm.flashcards import sm2
from app.routers.deps import api_error, get_ctx
from app.schemas import FlashcardPatch, ReviewIn, flashcard_out

router = APIRouter()


@router.get("/flashcards")
def list_flashcards(
    session_id: int | None = None, limit: int = Query(500, ge=1, le=5000), ctx: AppContext = Depends(get_ctx)
) -> list[dict[str, Any]]:
    with db_session(ctx.engine) as s:
        stmt = select(Flashcard)
        if session_id is not None:
            stmt = stmt.where(Flashcard.session_id == session_id)
        rows = list(s.exec(stmt.order_by(col(Flashcard.id).desc()).limit(limit)))
    return [flashcard_out(c) for c in rows]


@router.get("/flashcards/due")
def due_flashcards(limit: int = Query(50, ge=1, le=500), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    now = utcnow()
    with db_session(ctx.engine) as s:
        rows = list(
            s.exec(select(Flashcard).where(Flashcard.due_at <= now).order_by(Flashcard.due_at).limit(limit))
        )
        total_due = s.exec(select(func.count(Flashcard.id)).where(Flashcard.due_at <= now)).one()
        titles = {x.id: x.title for x in s.exec(select(LectureSession))}
    cards = []
    for c in rows:
        item = flashcard_out(c)
        item["session_title"] = titles.get(c.session_id) if c.session_id else None
        cards.append(item)
    return {"cards": cards, "total_due": total_due}


@router.get("/flashcards/stats")
def flashcard_stats(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    now = utcnow()
    with db_session(ctx.engine) as s:
        total = s.exec(select(func.count(Flashcard.id))).one()
        due = s.exec(select(func.count(Flashcard.id)).where(Flashcard.due_at <= now)).one()
        new = s.exec(select(func.count(Flashcard.id)).where(col(Flashcard.last_reviewed_at).is_(None))).one()
        mature = s.exec(select(func.count(Flashcard.id)).where(Flashcard.interval_days >= 21)).one()
    return {"total": total, "due": due, "new": new, "mature": mature}


@router.post("/flashcards/{card_id}/review")
def review(card_id: int, body: ReviewIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        card = s.get(Flashcard, card_id)
        if card is None:
            raise api_error(404, "CARD_NOT_FOUND", "Không tìm thấy thẻ")
        now = utcnow()
        res = sm2(card.ease, card.interval_days, card.repetitions, body.grade, now)
        card.ease, card.interval_days, card.repetitions, card.due_at = (
            res.ease,
            res.interval_days,
            res.repetitions,
            res.due_at,
        )
        card.last_reviewed_at = now
        s.add(card)
        s.commit()
        s.refresh(card)
    return flashcard_out(card)


@router.put("/flashcards/{card_id}")
def update_card(card_id: int, body: FlashcardPatch, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        card = s.get(Flashcard, card_id)
        if card is None:
            raise api_error(404, "CARD_NOT_FOUND", "Không tìm thấy thẻ")
        for field in body.model_fields_set:
            setattr(card, field, getattr(body, field))
        s.add(card)
        s.commit()
        s.refresh(card)
    return flashcard_out(card)


@router.delete("/flashcards/{card_id}")
def delete_card(card_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        card = s.get(Flashcard, card_id)
        if card is None:
            raise api_error(404, "CARD_NOT_FOUND", "Không tìm thấy thẻ")
        s.delete(card)
        s.commit()
    return {"deleted": True}
