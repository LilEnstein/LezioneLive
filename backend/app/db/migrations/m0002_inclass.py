"""Chế độ lớp học trực tiếp (feature_spec.md mục 7.1): bảng mới + cột mới cho session/segment/course.

Idempotent: DB mới đã có đủ cột nhờ migration 0001 (tạo schema theo model hiện tại), nên chỉ thêm cột
còn thiếu ở DB cũ.
"""

from __future__ import annotations

from sqlalchemy import Connection
from sqlmodel import SQLModel

from app.db import models  # noqa: F401  (đăng ký metadata)

# (bảng, cột, định nghĩa SQL khi ALTER TABLE)
COLUMNS: list[tuple[str, str, str]] = [
    ("course", "consent_confirmed_at", "DATETIME"),
    ("session", "capture_source", "VARCHAR NOT NULL DEFAULT 'tab'"),
    ("session", "room_profile_id", "INTEGER REFERENCES room_profile(id) ON DELETE SET NULL"),
    ("session", "denoise_level", "VARCHAR"),
    ("session", "battery_saver", "BOOLEAN NOT NULL DEFAULT 0"),
    ("session", "mic_device", "VARCHAR"),
    ("segment", "speaker", "VARCHAR NOT NULL DEFAULT 'unknown'"),
    ("segment", "speaker_score", "FLOAT"),
    ("segment", "snr_db", "FLOAT"),
    ("segment", "translation_kind", "VARCHAR"),
    ("glossary_suggestion", "capture_id", "INTEGER REFERENCES board_capture(id) ON DELETE CASCADE"),
]


def _columns(conn: Connection, table: str) -> set[str]:
    return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def upgrade(conn: Connection) -> None:
    # Bảng mới (room_profile, board_capture, session_marker, companion_device); bảng đã có thì bỏ qua.
    SQLModel.metadata.create_all(conn)
    added: set[tuple[str, str]] = set()
    for table, column, ddl in COLUMNS:
        if column not in _columns(conn, table):
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            added.add((table, column))
    if ("session", "capture_source") in added:
        conn.exec_driver_sql("UPDATE session SET capture_source = audio_mode")
    if ("segment", "translation_kind") in added:
        conn.exec_driver_sql("UPDATE segment SET translation_kind = 'claude' WHERE text_vi IS NOT NULL")
