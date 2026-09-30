"""Ảnh gửi kèm câu hỏi trong chat: bảng ``chat_attachment`` (idempotent)."""

from __future__ import annotations

from sqlalchemy import Connection
from sqlmodel import SQLModel

from app.db import models  # noqa: F401  (đăng ký metadata)


def upgrade(conn: Connection) -> None:
    SQLModel.metadata.create_all(conn)
