"""Chụp bảng / màn chiếu (IC-FR-40..44, Flow D): chuẩn hoá ảnh → Claude vision trích văn bản, LaTeX, thuật ngữ →
lưu ``board_capture`` → đề xuất thuật ngữ vào glossary → đưa vào ngữ cảnh dịch 10 phút tiếp theo.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlmodel import col, select

from app.db.models import BoardCapture, GlossarySuggestion, LectureSession, Segment
from app.db.session import db_session
from app.llm.client import LLMError
from app.llm.translator import format_glossary
from app.schemas import iso
from app.services.timefmt import fmt_ts

if TYPE_CHECKING:
    from app.context import AppContext

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_EDGE = 1568  # IC-FR-44: cạnh dài tối đa trước khi gửi Claude
THUMB_EDGE = 480
CONTEXT_WINDOW_MS = 10 * 60 * 1000  # IC-FR-42: ngữ cảnh dịch 10 phút tiếp theo
UNREADABLE_MESSAGE = "Không đọc được ảnh. Thử chụp thẳng góc, tránh loá đèn."  # IC-E-06

BOARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text_it": {"type": "string"},
        "latex": {"type": "array", "items": {"type": "string"}},
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"it": {"type": "string"}, "vi": {"type": "string"}, "en": {"type": "string"}},
                "required": ["it", "vi", "en"],
                "additionalProperties": False,
            },
        },
        "translation_vi": {"type": "string"},
        "unreadable": {"type": "string"},
    },
    "required": ["text_it", "latex", "terms", "translation_vi", "unreadable"],
    "additionalProperties": False,
}


class BoardError(ValueError):
    pass


def normalize_image(data: bytes, min_edge: int = 64) -> tuple[bytes, bytes, int, int]:
    """Xoay theo EXIF, bỏ metadata (vị trí GPS...), thu nhỏ ≤ 1568 px, JPEG. Trả về (ảnh, thumbnail, w, h)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            img = ImageOps.exif_transpose(im)
    except (UnidentifiedImageError, OSError) as e:
        raise BoardError("File không phải ảnh hợp lệ (JPEG/PNG/WebP).") from e
    if img.mode in ("RGBA", "LA", "P"):
        # Nền trong suốt → trắng (convert thẳng sang RGB làm vùng trong suốt thành đen).
        rgba = img.convert("RGBA")
        img = Image.new("RGB", rgba.size, "white")
        img.paste(rgba, mask=rgba.getchannel("A"))
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((MAX_EDGE, MAX_EDGE))
    w, h = img.size
    if min(w, h) < min_edge:
        raise BoardError("Ảnh quá nhỏ.")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True)
    thumb = img.copy()
    thumb.thumbnail((THUMB_EDGE, THUMB_EDGE))
    tout = io.BytesIO()
    thumb.save(tout, format="JPEG", quality=75)
    return out.getvalue(), tout.getvalue(), w, h


def _loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def capture_out(c: BoardCapture) -> dict[str, Any]:
    return {
        "id": c.id,
        "session_id": c.session_id,
        "t_ms": c.t_ms,
        "status": c.status,
        "source": c.source,
        "width": c.width,
        "height": c.height,
        "image_url": f"/api/board-captures/{c.id}/image",
        "thumb_url": f"/api/board-captures/{c.id}/image?thumb=1",
        "extracted_it": c.extracted_it,
        "latex": _loads(c.extracted_latex, []),
        "terms": _loads(c.terms_json, []),
        "translation_vi": c.translation_vi,
        "unreadable": c.unreadable,
        "error": c.error,
        "created_at": iso(c.created_at),
    }


def recent_board_captures(engine: Any, session_id: int, t_ms: int) -> list[BoardCapture]:
    """Ảnh bảng đã trích xuất trong 10 phút trước mốc ``t_ms`` (mới nhất trước, tối đa 2)."""
    with db_session(engine) as s:
        return list(
            s.exec(
                select(BoardCapture)
                .where(
                    BoardCapture.session_id == session_id,
                    BoardCapture.status == "done",
                    BoardCapture.t_ms <= t_ms + 30_000,
                    BoardCapture.t_ms >= t_ms - CONTEXT_WINDOW_MS,
                )
                .order_by(col(BoardCapture.t_ms).desc())
                .limit(2)
            )
        )


def board_context(captures: list[BoardCapture]) -> str:
    """Khối "nội dung bảng gần nhất" cho prompt dịch trong lớp."""
    parts = []
    for c in captures:
        lines = [f"[Ảnh bảng lúc {fmt_ts(c.t_ms)}]"]
        if c.extracted_it:
            text = c.extracted_it.strip()
            lines.append(text[:1500] + ("…" if len(text) > 1500 else ""))
        latex = _loads(c.extracted_latex, [])
        if latex:
            lines.append("Công thức: " + " ; ".join(f"${x}$" for x in latex[:10]))
        terms = _loads(c.terms_json, [])
        if terms:
            lines.append(
                "Thuật ngữ trên bảng: "
                + "; ".join(f"{t.get('it', '')} → {t.get('vi', '')}" for t in terms[:15] if isinstance(t, dict))
            )
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


class BoardCaptureService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def folder(self, session_id: int) -> Path:
        return self.ctx.env.data_dir / "board" / str(session_id)

    def current_t_ms(self, session_id: int) -> int:
        runner = self.ctx.runners.peek(session_id)
        if runner is not None:
            return runner.segmenter.t_ms
        with db_session(self.ctx.engine) as s:
            sess = s.get(LectureSession, session_id)
            last = s.exec(
                select(Segment.t_end_ms).where(Segment.session_id == session_id).order_by(col(Segment.seq).desc())
            ).first()
        return max(sess.active_ms if sess else 0, last or 0)

    def _update(self, capture_id: int, **fields: Any) -> BoardCapture | None:
        with db_session(self.ctx.engine) as s:
            row = s.get(BoardCapture, capture_id)
            if row is None:
                return None
            for k, v in fields.items():
                setattr(row, k, v)
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    async def capture(
        self, session_id: int, data: bytes, *, t_ms: int | None = None, source: str = "laptop"
    ) -> BoardCapture:
        if len(data) > MAX_UPLOAD_BYTES:
            raise BoardError("Ảnh quá lớn (tối đa 20 MB).")
        image, thumb, w, h = await asyncio.to_thread(normalize_image, data)
        when = self.current_t_ms(session_id) if t_ms is None else max(0, int(t_ms))
        folder = self.folder(session_id)
        folder.mkdir(parents=True, exist_ok=True)
        stem = uuid.uuid4().hex[:12]
        image_path, thumb_path = folder / f"{stem}.jpg", folder / f"{stem}_thumb.jpg"
        image_path.write_bytes(image)
        thumb_path.write_bytes(thumb)
        row = BoardCapture(
            session_id=session_id,
            t_ms=when,
            image_path=str(image_path),
            thumb_path=str(thumb_path),
            width=w,
            height=h,
            source=source,
        )
        with db_session(self.ctx.engine) as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        self.ctx.hub.publish(session_id, {"type": "board.processing", "capture": capture_out(row)})
        return await self._extract(row, image)

    async def _extract(self, row: BoardCapture, image: bytes) -> BoardCapture:
        assert row.id is not None
        if not self.ctx.llm.has_key:
            updated = self._update(row.id, status="failed", error="Chưa có API key Claude: ảnh đã được lưu nhưng chưa trích xuất.")
            return self._publish_done(updated or row)
        settings = self.ctx.settings.get()
        st = self.ctx.context.static(row.session_id)
        course = st.course_name + (f" ({st.course_name_it})" if st.course_name_it else "")
        system = self.ctx.prompts.render(
            "board_ocr_v1", course_name=course, glossary_table=format_glossary(st.glossary, limit=150)
        )
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(image).decode("ascii"),
                },
            },
            {"type": "text", "text": "Trích xuất nội dung bảng/màn chiếu trong ảnh theo đúng yêu cầu."},
        ]
        try:
            result, _ = await self.ctx.llm.json(
                purpose="board",
                session_id=row.session_id,
                model=settings.assistant_model,
                max_tokens=6000,
                system=system,
                messages=[{"role": "user", "content": content}],
                schema=BOARD_SCHEMA,
                route="fast",
            )
        except LLMError as e:
            updated = self._update(row.id, status="failed", error=f"Không trích xuất được ảnh: {e.message}")
            return self._publish_done(updated or row)
        data = result if isinstance(result, dict) else {}
        text_it = str(data.get("text_it") or "").strip()
        latex = [str(x).strip() for x in data.get("latex") or [] if str(x).strip()]
        terms = [
            {"it": str(t.get("it", "")).strip(), "vi": str(t.get("vi", "")).strip(), "en": str(t.get("en", "")).strip()}
            for t in data.get("terms") or []
            if isinstance(t, dict) and str(t.get("it", "")).strip()
        ]
        unreadable = str(data.get("unreadable") or "").strip()
        if not text_it and not latex:
            updated = self._update(
                row.id, status="unreadable", unreadable=unreadable or None, error=UNREADABLE_MESSAGE
            )
            return self._publish_done(updated or row)
        updated = self._update(
            row.id,
            status="done",
            extracted_it=text_it,
            extracted_latex=json.dumps(latex, ensure_ascii=False),
            terms_json=json.dumps(terms, ensure_ascii=False),
            translation_vi=str(data.get("translation_vi") or "").strip() or None,
            unreadable=unreadable or None,
            error=None,
        )
        suggestions = self._suggest(row, terms)
        return self._publish_done(updated or row, suggestions=suggestions)

    def _suggest(self, row: BoardCapture, terms: list[dict[str, str]]) -> int:
        """Thuật ngữ mới → đề xuất (người dùng duyệt ✓ mới vào glossary, IC-FR-42)."""
        st = self.ctx.context.static(row.session_id)
        known = {g.term_it.strip().lower() for g in st.glossary}
        added = 0
        with db_session(self.ctx.engine) as s:
            pending = {
                r.term_it.strip().lower()
                for r in s.exec(
                    select(GlossarySuggestion).where(
                        GlossarySuggestion.session_id == row.session_id, GlossarySuggestion.status == "pending"
                    )
                )
            }
            for t in terms:
                key = t["it"].lower()
                if not t["vi"] or key in known or key in pending:
                    continue
                pending.add(key)
                s.add(
                    GlossarySuggestion(
                        session_id=row.session_id,
                        capture_id=row.id,
                        course_id=st.course_id,
                        term_it=t["it"],
                        term_vi=t["vi"],
                        term_en=t["en"] or None,
                        note="Từ ảnh bảng",
                    )
                )
                added += 1
            s.commit()
        return added

    def _publish_done(self, row: BoardCapture, suggestions: int = 0) -> BoardCapture:
        out = capture_out(row)
        event: dict[str, Any] = {
            "type": "board.captured",
            "id": row.id,
            "t_ms": row.t_ms,
            "thumb_url": out["thumb_url"],
            "extracted_it": row.extracted_it,
            "terms": out["terms"],
            "status": row.status,
            "suggestions": suggestions,
            "capture": out,
        }
        self.ctx.hub.publish(row.session_id, event)
        return row

    def list(self, session_id: int) -> list[BoardCapture]:
        with db_session(self.ctx.engine) as s:
            return list(
                s.exec(select(BoardCapture).where(BoardCapture.session_id == session_id).order_by(BoardCapture.t_ms))
            )

    async def retry(self, capture_id: int) -> BoardCapture | None:
        with db_session(self.ctx.engine) as s:
            row = s.get(BoardCapture, capture_id)
        if row is None:
            return None
        image = Path(row.image_path).read_bytes()
        row = self._update(capture_id, status="processing", error=None) or row
        return await self._extract(row, image)

    def delete(self, capture_id: int) -> int | None:
        with db_session(self.ctx.engine) as s:
            row = s.get(BoardCapture, capture_id)
            if row is None:
                return None
            session_id = row.session_id
            paths = [row.image_path, row.thumb_path]
            s.delete(row)
            s.commit()
        for p in paths:
            if p:
                Path(p).unlink(missing_ok=True)
        self.ctx.hub.publish(session_id, {"type": "board.deleted", "id": capture_id})
        return session_id
