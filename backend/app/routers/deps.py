"""Dependency dùng chung cho router."""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.context import AppContext
from app.db.models import LectureSession
from app.db.session import db_session


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def require_session(ctx: AppContext, session_id: int) -> LectureSession:
    with db_session(ctx.engine) as s:
        sess = s.get(LectureSession, session_id)
    if sess is None:
        raise api_error(404, "SESSION_NOT_FOUND", "Không tìm thấy phiên học")
    return sess
