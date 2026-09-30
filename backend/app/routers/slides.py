"""/sessions/{id}/slides: upload PDF (≤ 30 MB, ≤ 100 trang), xem và xoá slide."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import select

from app.context import AppContext
from app.db.models import SlideDeck
from app.db.session import db_session
from app.routers.deps import api_error, get_ctx, require_session
from app.schemas import deck_out
from app.services.slides import MAX_BYTES, SlideError

router = APIRouter()


@router.get("/sessions/{session_id}/slides")
def list_slides(session_id: int, ctx: AppContext = Depends(get_ctx)) -> list[dict[str, Any]]:
    require_session(ctx, session_id)
    with db_session(ctx.engine) as s:
        decks = list(s.exec(select(SlideDeck).where(SlideDeck.session_id == session_id).order_by(SlideDeck.id)))
    return [deck_out(d) for d in decks]


@router.post("/sessions/{session_id}/slides", status_code=201)
async def upload_slides(
    session_id: int, file: UploadFile = File(...), ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    require_session(ctx, session_id)
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise api_error(413, "FILE_TOO_LARGE", "File quá lớn. Giới hạn 30 MB.")
    try:
        deck = await ctx.slides.add(session_id, file.filename or "slides.pdf", data)
    except SlideError as e:
        raise api_error(400, "INVALID_PDF", str(e)) from e
    return deck_out(deck)


@router.get("/slides/{deck_id}/file")
def slide_file(deck_id: int, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    with db_session(ctx.engine) as s:
        deck = s.get(SlideDeck, deck_id)
    if deck is None or not Path(deck.path).exists():
        raise api_error(404, "SLIDE_NOT_FOUND", "Không tìm thấy slide")
    return FileResponse(deck.path, media_type="application/pdf", content_disposition_type="inline",
                        filename=deck.filename)


@router.delete("/slides/{deck_id}")
async def delete_slides(deck_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    if not await ctx.slides.delete(deck_id):
        raise api_error(404, "SLIDE_NOT_FOUND", "Không tìm thấy slide")
    return {"deleted": True}
