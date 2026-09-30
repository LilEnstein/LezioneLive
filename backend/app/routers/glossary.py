"""/glossary: CRUD thuật ngữ + import/export CSV (FR-GL-01/02)."""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlmodel import col, select

from app.context import AppContext
from app.db.models import Course, GlossaryTerm, utcnow
from app.db.session import db_session
from app.routers.deps import api_error, get_ctx
from app.schemas import TermIn, term_out

router = APIRouter()

MAX_CSV_BYTES = 2 * 1024 * 1024
HEADER_ALIASES = {
    "term_it": {"term_it", "it", "italiano", "italian", "tiếng ý", "tieng y", "ý"},
    "term_vi": {"term_vi", "vi", "tiếng việt", "tieng viet", "vietnamese", "việt"},
    "term_en": {"term_en", "en", "english", "tiếng anh", "tieng anh"},
    "note": {"note", "notes", "ghi chú", "ghi chu"},
}


def _scope(course_id: str | None) -> tuple[bool, int | None]:
    """course_id: None = tất cả, "shared" = dùng chung (NULL), số = một môn học."""
    if course_id in (None, "", "all"):
        return False, None
    if course_id == "shared":
        return True, None
    try:
        return True, int(course_id)
    except ValueError as e:
        raise api_error(400, "BAD_COURSE", "course_id không hợp lệ") from e


@router.get("/glossary")
def list_terms(
    course_id: str | None = None,
    q: str | None = None,
    include_shared: bool = True,
    ctx: AppContext = Depends(get_ctx),
) -> list[dict[str, Any]]:
    filtered, cid = _scope(course_id)
    with db_session(ctx.engine) as s:
        stmt = select(GlossaryTerm)
        if filtered:
            if cid is None:
                stmt = stmt.where(col(GlossaryTerm.course_id).is_(None))
            elif include_shared:
                stmt = stmt.where(or_(GlossaryTerm.course_id == cid, col(GlossaryTerm.course_id).is_(None)))
            else:
                stmt = stmt.where(GlossaryTerm.course_id == cid)
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(GlossaryTerm.term_it).like(like),
                    func.lower(GlossaryTerm.term_vi).like(like),
                    func.lower(col(GlossaryTerm.term_en)).like(like),
                )
            )
        rows = list(s.exec(stmt.order_by(func.lower(GlossaryTerm.term_it))))
    return [term_out(t) for t in rows]


def _check_course(ctx: AppContext, course_id: int | None) -> None:
    if course_id is None:
        return
    with db_session(ctx.engine) as s:
        if s.get(Course, course_id) is None:
            raise api_error(404, "COURSE_NOT_FOUND", "Không tìm thấy môn học")


@router.post("/glossary", status_code=201)
def create_term(body: TermIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    _check_course(ctx, body.course_id)
    term = GlossaryTerm(
        course_id=body.course_id,
        term_it=body.term_it.strip(),
        term_vi=body.term_vi.strip(),
        term_en=(body.term_en or "").strip() or None,
        note=(body.note or "").strip() or None,
    )
    with db_session(ctx.engine) as s:
        s.add(term)
        s.commit()
        s.refresh(term)
    ctx.context.invalidate()
    return term_out(term)


@router.put("/glossary/{term_id}")
def update_term(term_id: int, body: TermIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    _check_course(ctx, body.course_id)
    with db_session(ctx.engine) as s:
        term = s.get(GlossaryTerm, term_id)
        if term is None:
            raise api_error(404, "TERM_NOT_FOUND", "Không tìm thấy thuật ngữ")
        term.course_id = body.course_id
        term.term_it = body.term_it.strip()
        term.term_vi = body.term_vi.strip()
        term.term_en = (body.term_en or "").strip() or None
        term.note = (body.note or "").strip() or None
        term.updated_at = utcnow()
        s.add(term)
        s.commit()
        s.refresh(term)
    ctx.context.invalidate()
    return term_out(term)


@router.delete("/glossary/{term_id}")
def delete_term(term_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        term = s.get(GlossaryTerm, term_id)
        if term is None:
            raise api_error(404, "TERM_NOT_FOUND", "Không tìm thấy thuật ngữ")
        s.delete(term)
        s.commit()
    ctx.context.invalidate()
    return {"deleted": True}


def parse_csv(raw: bytes) -> list[dict[str, str]]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 luôn decode được
        raise api_error(400, "BAD_CSV", "Không đọc được mã hoá file CSV")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [c.strip().lower() for c in rows[0]]
    mapping: dict[str, int] = {}
    for field, aliases in HEADER_ALIASES.items():
        for idx, name in enumerate(header):
            if name in aliases:
                mapping[field] = idx
                break
    if "term_it" in mapping and "term_vi" in mapping:
        body = rows[1:]
    else:
        mapping = {"term_it": 0, "term_vi": 1, "term_en": 2, "note": 3}
        body = rows
    out = []
    for r in body:

        def get(field: str, r: list[str] = r) -> str:
            i = mapping.get(field)
            return r[i].strip() if i is not None and i < len(r) else ""

        out.append({f: get(f) for f in ("term_it", "term_vi", "term_en", "note")})
    return out


@router.post("/glossary/import")
async def import_csv(
    file: UploadFile = File(...),
    course_id: str | None = Query(None, description='id môn học, hoặc "shared"'),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    _, cid = _scope(course_id)
    _check_course(ctx, cid)
    raw = await file.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise api_error(413, "CSV_TOO_LARGE", "File CSV quá lớn (tối đa 2 MB)")
    rows = parse_csv(raw)
    imported = updated = skipped = 0
    with db_session(ctx.engine) as s:
        stmt = select(GlossaryTerm)
        stmt = stmt.where(col(GlossaryTerm.course_id).is_(None) if cid is None else GlossaryTerm.course_id == cid)
        existing = {t.term_it.strip().lower(): t for t in s.exec(stmt)}
        for row in rows:
            if not row["term_it"] or not row["term_vi"]:
                skipped += 1
                continue
            key = row["term_it"].lower()
            term = existing.get(key)
            if term is None:
                term = GlossaryTerm(course_id=cid, term_it=row["term_it"], term_vi=row["term_vi"], source="csv")
                existing[key] = term
                imported += 1
            else:
                term.term_vi = row["term_vi"]
                term.updated_at = utcnow()
                updated += 1
            term.term_en = row["term_en"] or term.term_en
            term.note = row["note"] or term.note
            s.add(term)
        s.commit()
    ctx.context.invalidate()
    return {"imported": imported, "updated": updated, "skipped": skipped}


@router.get("/glossary/export")
def export_csv(course_id: str | None = None, ctx: AppContext = Depends(get_ctx)) -> Response:
    terms = list_terms(course_id=course_id, q=None, include_shared=False, ctx=ctx)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["term_it", "term_vi", "term_en", "note"])
    for t in terms:
        w.writerow([t["term_it"], t["term_vi"], t["term_en"] or "", t["note"] or ""])
    data = "﻿" + buf.getvalue()  # BOM để Excel mở đúng tiếng Việt
    name = f"glossary_{course_id or 'all'}.csv"
    return Response(
        data.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
