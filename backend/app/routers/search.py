"""/search?q=: tìm kiếm full-text trên mọi transcript (FTS5, FR-LE-04)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.context import AppContext
from app.routers.deps import get_ctx

router = APIRouter()

HL_START = "\u0002"
HL_END = "\u0003"


def _raw_iso(value: Any) -> str | None:
    """Giá trị DATETIME đọc thô từ SQLite (UTC, không offset) → ISO-8601 có 'Z'."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
    except ValueError:
        return str(value)


def fts_query(q: str) -> str | None:
    """Chuyển chuỗi người dùng thành truy vấn FTS5 an toàn: mỗi từ là một cụm trong ngoặc kép, từ cuối có *."""
    tokens = [t for t in re.split(r"[\s\"'`^*():,.;!?]+", q) if t]
    if not tokens:
        return None
    parts = [f'"{t}"' for t in tokens]
    parts[-1] += "*"
    return " ".join(parts)


@router.get("/search")
def search(
    q: str = Query(..., min_length=1, max_length=200),
    course_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    query = fts_query(q)
    if query is None:
        return {"query": q, "results": []}
    sql = """
        SELECT s.id, s.session_id, s.seq, s.t_start_ms,
               highlight(segment_fts, 0, :hs, :he) AS it_hl,
               highlight(segment_fts, 1, :hs, :he) AS vi_hl,
               sess.title, sess.started_at, c.name AS course_name, sess.course_id
        FROM segment_fts
        JOIN segment s ON s.id = segment_fts.rowid
        JOIN session sess ON sess.id = s.session_id
        LEFT JOIN course c ON c.id = sess.course_id
        WHERE segment_fts MATCH :q {course_filter}
        ORDER BY bm25(segment_fts)
        LIMIT :limit
    """.format(course_filter="AND sess.course_id = :cid" if course_id is not None else "")
    params: dict[str, Any] = {"q": query, "hs": HL_START, "he": HL_END, "limit": limit}
    if course_id is not None:
        params["cid"] = course_id
    with ctx.engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    results = [
        {
            "segment_id": r["id"],
            "session_id": r["session_id"],
            "seq": r["seq"],
            "t_start_ms": r["t_start_ms"],
            "text_it": r["it_hl"],
            "text_vi": r["vi_hl"],
            "session_title": r["title"],
            "session_started_at": _raw_iso(r["started_at"]),
            "course_name": r["course_name"],
            "course_id": r["course_id"],
        }
        for r in rows
    ]
    return {"query": q, "highlight": [HL_START, HL_END], "results": results}
