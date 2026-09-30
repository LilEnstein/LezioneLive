"""/sessions/{id}/export?format=md|html|pdf (FR-POST-02)."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.context import AppContext
from app.routers.deps import api_error, get_ctx, require_session
from app.services.export_md import ExportError, build_markdown, render_html, render_pdf

router = APIRouter()

MEDIA = {
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "pdf": "application/pdf",
}


def _disposition(filename: str) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode() or "export"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@router.get("/sessions/{session_id}/export")
async def export_session(
    session_id: int,
    format: str = Query("md", pattern="^(md|html|pdf)$"),
    ctx: AppContext = Depends(get_ctx),
) -> Response:
    require_session(ctx, session_id)
    bundle = build_markdown(ctx.engine, session_id)
    filename = f"{bundle.filename_base}.{format}"
    if format == "md":
        content: bytes = bundle.markdown.encode("utf-8")
    elif format == "html":
        content = render_html(bundle).encode("utf-8")
    else:
        try:
            content = await render_pdf(render_html(bundle), ctx.env.chrome_path)
        except ExportError as e:
            raise api_error(501, "PDF_UNAVAILABLE", str(e)) from e
    return Response(content, media_type=MEDIA[format], headers={"Content-Disposition": _disposition(filename)})
