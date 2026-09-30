"""Nhiều cuộc trò chuyện (thread) trong một buổi học: bảng ``chat_thread`` + cột ``chat_message.thread_id``.

Idempotent. Tin nhắn hỏi đáp cũ (chưa có thread) của mỗi buổi được gom vào một thread đặt tên theo câu hỏi
đầu tiên, để giao diện mới vẫn hiển thị lịch sử cũ.
"""

from __future__ import annotations

from sqlalchemy import Connection
from sqlmodel import SQLModel

from app.db import models  # noqa: F401  (đăng ký metadata)
from app.services.chat_threads import thread_title


def upgrade(conn: Connection) -> None:
    SQLModel.metadata.create_all(conn)
    cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(chat_message)")}
    if "thread_id" not in cols:
        conn.exec_driver_sql(
            "ALTER TABLE chat_message ADD COLUMN thread_id INTEGER REFERENCES chat_thread(id) ON DELETE CASCADE"
        )
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_chat_message_thread_id ON chat_message(thread_id)")

    orphans = conn.exec_driver_sql(
        "SELECT session_id, MIN(created_at), MAX(created_at) FROM chat_message "
        "WHERE mode = 'qa' AND thread_id IS NULL GROUP BY session_id"
    ).fetchall()
    for session_id, first_at, last_at in orphans:
        first_q = conn.exec_driver_sql(
            "SELECT content FROM chat_message WHERE session_id = ? AND mode = 'qa' AND role = 'user' "
            "AND thread_id IS NULL ORDER BY id LIMIT 1",
            (session_id,),
        ).scalar()
        cur = conn.exec_driver_sql(
            "INSERT INTO chat_thread(session_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, thread_title(first_q or ""), first_at, last_at),
        )
        conn.exec_driver_sql(
            "UPDATE chat_message SET thread_id = ? WHERE session_id = ? AND mode = 'qa' AND thread_id IS NULL",
            (cur.lastrowid, session_id),
        )
