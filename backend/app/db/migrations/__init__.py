"""Bộ chạy migration tối giản cho SQLite.

Mỗi migration là một hàm ``upgrade(conn)`` có số phiên bản tăng dần. Phiên bản đã áp dụng được ghi
vào bảng ``schema_migrations``. Migration sau này nên idempotent (kiểm tra cột/bảng đã tồn tại chưa),
vì migration 0001 tạo schema theo model hiện tại.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import Connection, Engine

from . import m0001_initial, m0002_inclass, m0003_chat_threads, m0004_chat_attachments

log = logging.getLogger(__name__)

Migration = tuple[int, str, Callable[[Connection], None]]

MIGRATIONS: list[Migration] = [
    (1, "initial", m0001_initial.upgrade),
    (2, "inclass", m0002_inclass.upgrade),
    (3, "chat_threads", m0003_chat_threads.upgrade),
    (4, "chat_attachments", m0004_chat_attachments.upgrade),
]


def run_migrations(engine: Engine) -> list[int]:
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        applied = {row[0] for row in conn.exec_driver_sql("SELECT version FROM schema_migrations")}

    newly: list[int] = []
    for version, name, upgrade in MIGRATIONS:
        if version in applied:
            continue
        with engine.begin() as conn:
            upgrade(conn)
            conn.exec_driver_sql(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, datetime('now'))",
                (version, name),
            )
        log.info("Đã áp dụng migration %04d_%s", version, name)
        newly.append(version)
    return newly
