"""Schema ban đầu + bảng ảo FTS5 cho tìm kiếm full-text (FR-LE-04)."""

from __future__ import annotations

from sqlalchemy import Connection
from sqlmodel import SQLModel

from app.db import models  # noqa: F401  (đăng ký metadata)

FTS_STATEMENTS = [
    # Bảng FTS5 độc lập (rowid = segment.id). remove_diacritics 2: tìm "hoi quy" vẫn ra "hồi quy".
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS segment_fts USING fts5(
        text_it, text_vi, session_id UNINDEXED,
        tokenize = "unicode61 remove_diacritics 2"
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS segment_fts_ai AFTER INSERT ON segment BEGIN
        INSERT INTO segment_fts(rowid, text_it, text_vi, session_id)
        VALUES (new.id, COALESCE(new.text_it_edited, new.text_it), COALESCE(new.text_vi, ''), new.session_id);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS segment_fts_ad AFTER DELETE ON segment BEGIN
        DELETE FROM segment_fts WHERE rowid = old.id;
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS segment_fts_au AFTER UPDATE OF text_it, text_it_edited, text_vi ON segment
    BEGIN
        DELETE FROM segment_fts WHERE rowid = old.id;
        INSERT INTO segment_fts(rowid, text_it, text_vi, session_id)
        VALUES (new.id, COALESCE(new.text_it_edited, new.text_it), COALESCE(new.text_vi, ''), new.session_id);
    END
    """,
]


def upgrade(conn: Connection) -> None:
    SQLModel.metadata.create_all(conn)
    for stmt in FTS_STATEMENTS:
        conn.exec_driver_sql(stmt)
