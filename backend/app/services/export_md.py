"""Xuất ghi chú buổi học (FR-POST-02, AC-10).

- Markdown: front matter YAML (metadata) + tóm tắt + ghim + transcript song ngữ + chat + flashcard.
  Mở tốt trong VS Code / Obsidian.
- HTML: Markdown → HTML, công thức LaTeX → MathML (không cần JavaScript).
- PDF: in HTML bằng Chrome/Edge headless (hiển thị đúng tiếng Việt có dấu và MathML);
  nếu không có trình duyệt thì thử WeasyPrint.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import markdown as md_lib
from sqlalchemy import Engine
from sqlmodel import select

from app.config import INCLASS_SOURCES
from app.db.models import (
    BoardCapture,
    ChatAttachment,
    ChatMessage,
    ChatThread,
    Course,
    Flashcard,
    LectureSession,
    Pin,
    Segment,
    SessionMarker,
    Summary,
    utcnow,
)
from app.db.session import db_session
from app.schemas import iso
from app.services.timefmt import fmt_duration, fmt_ts

log = logging.getLogger(__name__)

PIN_LABELS = {"confused": "chưa hiểu", "important": "quan trọng", "todo": "việc cần làm"}
# Mục 11 feature_spec: file export có dòng nhắc này ở đầu.
PRIVACY_NOTE = (
    "Tài liệu học tập cá nhân — không chia sẻ lại, không công bố transcript/bản ghi bài giảng "
    "khi chưa được giảng viên cho phép."
)
EXPORT_IMAGE_EDGE = 1024


def _image_data_uri(path: str | None) -> str | None:
    """Ảnh bảng nhúng thẳng vào file (data URI) để Markdown/PDF tự chứa, thu nhỏ ≤ 1024 px."""
    if not path or not Path(path).exists():
        return None
    try:
        import base64
        import io

        from PIL import Image

        with Image.open(path) as im:
            img = im.convert("RGB")
        img.thumbnail((EXPORT_IMAGE_EDGE, EXPORT_IMAGE_EDGE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        log.warning("Không nhúng được ảnh %s vào file export", path)
        return None


def _board_lines(c: BoardCapture) -> list[str]:
    lines = [f"**[{fmt_ts(c.t_ms)}]** 📷 *Ảnh bảng/màn chiếu*  "]
    uri = _image_data_uri(c.image_path)
    if uri:
        lines.append(f"![Ảnh bảng {fmt_ts(c.t_ms)}]({uri})")
        lines.append("")
    if c.status == "done":
        if c.extracted_it:
            lines.append("> " + _esc(c.extracted_it).replace("\n", "  \n> "))
            lines.append("")
        try:
            latex = json.loads(c.extracted_latex or "[]")
        except ValueError:
            latex = []
        for formula in latex:
            lines.append(f"$${formula}$$")
            lines.append("")
        if c.translation_vi:
            lines.append(f"🇻🇳 {_esc(c.translation_vi)}")
            lines.append("")
    elif c.error:
        lines.append(f"_{_esc(c.error)}_")
        lines.append("")
    return lines


class ExportError(RuntimeError):
    pass


@dataclass
class ExportBundle:
    filename_base: str
    title: str
    markdown: str


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).replace("đ", "d").replace("Đ", "D")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return text[:max_len].strip("-") or "session"


def _esc(text: str) -> str:
    """Thoát ký tự Markdown dễ gây hiểu nhầm trong văn bản thô (transcript, ghi chú)."""
    return re.sub(r"([\\`*_<\[\]])", r"\\\1", text)


def _demote(md: str) -> str:
    return re.sub(r"^(#{1,5}) ", r"#\1 ", md, flags=re.M)


def _iso(dt: datetime | None) -> str | None:
    return iso(dt)


def _local(dt: datetime) -> datetime:
    """Giờ địa phương của máy (ngày/giờ hiển thị cho người đọc)."""
    return dt.astimezone() if dt.tzinfo is not None else dt


def build_markdown(engine: Engine, session_id: int) -> ExportBundle:
    with db_session(engine) as s:
        sess = s.get(LectureSession, session_id)
        if sess is None:
            raise ExportError("Không tìm thấy phiên")
        course = s.get(Course, sess.course_id) if sess.course_id else None
        segments = list(s.exec(select(Segment).where(Segment.session_id == session_id).order_by(Segment.seq)))
        pins = list(s.exec(select(Pin).where(Pin.session_id == session_id).order_by(Pin.id)))
        chats = list(s.exec(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)))
        image_counts: dict[int, int] = {}
        for a in s.exec(select(ChatAttachment).where(ChatAttachment.session_id == session_id)):
            image_counts[a.message_id or 0] = image_counts.get(a.message_id or 0, 0) + 1
        threads = {
            t.id: t for t in s.exec(select(ChatThread).where(ChatThread.session_id == session_id).order_by(ChatThread.id))
        }
        summary = s.get(Summary, session_id)
        cards = list(s.exec(select(Flashcard).where(Flashcard.session_id == session_id).order_by(Flashcard.id)))
        boards = list(s.exec(select(BoardCapture).where(BoardCapture.session_id == session_id).order_by(BoardCapture.t_ms)))
        markers = list(
            s.exec(select(SessionMarker).where(SessionMarker.session_id == session_id).order_by(SessionMarker.t_ms))
        )
    inclass = sess.capture_source in INCLASS_SOURCES

    duration_ms = max([sess.active_ms or 0] + [sg.t_end_ms for sg in segments])
    started_local = _local(sess.started_at)
    date = started_local.strftime("%Y-%m-%d")
    front = {
        "title": sess.title,
        "course": course.name if course else None,
        "course_it": course.name_it if course else None,
        "lecturer": course.lecturer if course else None,
        "date": date,
        "started_at": _iso(sess.started_at),
        "ended_at": _iso(sess.ended_at),
        "duration": fmt_duration(duration_ms),
        "segments": len(segments),
        "stt_provider": sess.stt_provider,
        "cost_usd_est": round(sess.cost_usd_est or 0.0, 4),
        "exported_at": _iso(utcnow()),
    }
    lines = ["---"]
    for key, value in front.items():
        if value is None:
            continue
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("tags: [lezionelive]")
    lines.append("---")
    lines.append("")
    lines.append(f"> ⚠️ {PRIVACY_NOTE}")
    lines.append("")
    lines.append(f"# {sess.title}")
    lines.append("")
    meta = [f"**Ngày:** {started_local.strftime('%d/%m/%Y')}", f"**Thời lượng:** {fmt_duration(duration_ms)}"]
    if course:
        meta.insert(0, f"**Môn:** {course.name}" + (f" ({course.name_it})" if course.name_it else ""))
        if course.lecturer:
            meta.insert(1, f"**Giảng viên:** {course.lecturer}")
    meta.append(f"**Chi phí API ước tính:** ${sess.cost_usd_est or 0:.2f}")
    lines.append("> " + " · ".join(meta))
    lines.append("")

    lines.append("## Tóm tắt")
    lines.append("")
    lines.append(_demote(summary.content_md.strip()) if summary else "_Chưa có tóm tắt._")
    lines.append("")

    by_id = {sg.id: sg for sg in segments}
    lines.append("## Ghim")
    lines.append("")
    if not pins:
        lines.append("_Không có ghim._")
    for p in pins:
        seg = by_id.get(p.segment_id) if p.segment_id else None
        label = PIN_LABELS.get(p.kind, p.kind)
        if seg:
            lines.append(f"- **[{fmt_ts(seg.t_start_ms)}]** 📌 *{label}* — {_esc(seg.source_text)}  ")
            if seg.text_vi:
                lines.append(f"  ↳ {_esc(seg.text_vi)}  ")
        else:
            lines.append(f"- 📌 *{label}*  ")
        if p.note:
            lines.append(f"  _Ghi chú: {_esc(p.note)}_")
    lines.append("")

    lines.append("## Transcript song ngữ")
    lines.append("")
    if not segments and not boards:
        lines.append("_Trống._")
    if any(sg.text_vi and sg.translation_kind == "local" for sg in segments):
        lines.append("> 💻 = câu dịch bằng model trên máy (NLLB), chất lượng thấp hơn Claude.")
        lines.append("")
    # Ảnh bảng và mốc gián đoạn xen kẽ đúng vị trí thời gian (IC-FR-43, IC-AC-06).
    extras: list[tuple[int, int, list[str]]] = [(c.t_ms, 0, _board_lines(c)) for c in boards]
    extras += [(m.t_ms, 1, [f"**[{fmt_ts(m.t_ms)}]** ⚡ _{_esc(m.message)}_", ""]) for m in markers]
    extras.sort(key=lambda e: (e[0], e[1]))
    pos = 0
    for sg in segments:
        while pos < len(extras) and extras[pos][0] <= sg.t_start_ms:
            lines.extend(extras[pos][2])
            pos += 1
        if sg.status == "redacted":
            lines.append(f"**[{fmt_ts(sg.t_start_ms)}]** 🙋 _(Lời người khác — không lưu)_")
            lines.append("")
            continue
        flag = " ⚠️" if sg.confidence < 0.5 else ""
        who = ""
        if inclass and sg.speaker == "other":
            who = "🙋 *Sinh viên hỏi:* "
        elif inclass and sg.speaker == "lecturer":
            who = "🎓 "
        lines.append(f"**[{fmt_ts(sg.t_start_ms)}]** {who}🇮🇹 {_esc(sg.source_text)}{flag}  ")
        vi = _esc(sg.text_vi) if sg.text_vi else "_(chưa dịch)_"
        if sg.text_vi and sg.translation_kind == "draft":
            vi += " _(bản nháp ngoại tuyến)_"
        elif sg.text_vi and sg.translation_kind == "local":
            vi = f"💻 {vi}"
        lines.append(f"🇻🇳 {vi}" + ("  " if sg.text_en else ""))
        if sg.text_en:
            lines.append(f"🇬🇧 {_esc(sg.text_en)}")
        lines.append("")
    for extra in extras[pos:]:
        lines.extend(extra[2])

    qa = [c for c in chats if c.mode == "qa"]
    compose = [c for c in chats if c.mode == "compose_it"]
    lines.append("## Chat với trợ lý")
    lines.append("")
    if not qa:
        lines.append("_Không có._")
    # Nhiều cuộc trò chuyện → mỗi thread một mục con (theo thứ tự tạo).
    multi = len({c.thread_id for c in qa}) > 1
    current: int | None | bool = False
    for c in sorted(qa, key=lambda c: (c.thread_id or 0, c.id or 0)) if multi else qa:
        if multi and c.thread_id != current:
            current = c.thread_id
            thread = threads.get(c.thread_id) if c.thread_id is not None else None
            lines.append(f"### 💬 {_esc(thread.title) if thread and thread.title else 'Cuộc trò chuyện'}")
            lines.append("")
        who = "🧑 **Bạn**" if c.role == "user" else "🤖 **Claude**"
        body = _esc(c.content) if c.role == "user" else _demote(_demote(c.content.strip()))
        if c.role == "user" and image_counts.get(c.id or 0):
            body += f" _(kèm {image_counts[c.id or 0]} ảnh)_"
        lines.append(f"{who}:")
        lines.append("")
        lines.append(body)
        lines.append("")
    if compose:
        lines.append("### Câu hỏi soạn cho giảng viên")
        lines.append("")
        for c in compose:
            if c.role == "user":
                lines.append(f"- **Câu hỏi (VI):** {_esc(c.content)}")
            else:
                try:
                    data = json.loads(c.content)
                except ValueError:
                    continue
                lines.append(f"  - **Tiếng Ý:** {_esc(data.get('question_it', ''))}")
                lines.append(f"  - **Dịch ngược:** {_esc(data.get('back_translation_vi', ''))}")
                if data.get("notes"):
                    lines.append(f"  - **Lưu ý:** {_esc(data['notes'])}")
        lines.append("")

    if cards:
        lines.append("## Flashcards")
        lines.append("")
        lines.append("| Tiếng Ý | Tiếng Việt | Loại từ | Ví dụ |")
        lines.append("|---|---|---|---|")
        for card in cards:
            example = card.example_it or ""
            if card.example_vi:
                example += f" — {card.example_vi}"
            row = [card.front_it, card.back_vi, card.pos or "", example]
            lines.append("| " + " | ".join(_esc(x).replace("|", "\\|").replace("\n", " ") for x in row) + " |")
        lines.append("")

    course_part = slugify(course.name, 30) if course else "lezione"
    base = f"{date}_{course_part}_{slugify(sess.title, 40)}"
    return ExportBundle(filename_base=base, title=sess.title, markdown="\n".join(lines).rstrip() + "\n")


# ---------------------------------------------------------------------------
# Markdown → HTML (MathML)
# ---------------------------------------------------------------------------

_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.S)
# Quy tắc kiểu Pandoc: $ mở không theo sau bởi khoảng trắng, $ đóng không đứng sau khoảng trắng và không
# theo sau bởi chữ số (tránh nhầm "$5 và $6").
_INLINE_MATH = re.compile(r"(?<![\\$])\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)")


def _to_mathml(latex: str, display: bool) -> str:
    try:
        from latex2mathml.converter import convert

        return convert(latex.strip(), display="block" if display else "inline")
    except Exception:  # noqa: BLE001 - công thức lỗi cú pháp: hiện nguyên văn
        return f"<code>{html_lib.escape(latex)}</code>"


def markdown_to_html(md: str) -> str:
    body = re.sub(r"^---\n.*?\n---\n", "", md, count=1, flags=re.S)  # bỏ front matter
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"LZMATH{len(stash) - 1}ZL"

    body = _DISPLAY_MATH.sub(lambda m: keep(_to_mathml(m.group(1), True)), body)
    body = _INLINE_MATH.sub(lambda m: keep(_to_mathml(m.group(1), False)), body)
    # Không cho HTML thô từ nội dung (kể cả "\<" đã thoát trong Markdown).
    body = body.replace("\\<", "&lt;").replace("<", "&lt;")
    html_body = md_lib.markdown(body, extensions=["tables", "fenced_code", "sane_lists"], output_format="html")
    html_body = re.sub(r"LZMATH(\d+)ZL", lambda m: stash[int(m.group(1))], html_body)
    return html_body


HTML_TEMPLATE = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{ size: A4; margin: 16mm 14mm; }}
  body {{ font-family: "Segoe UI", "Noto Sans", "Helvetica Neue", Arial, sans-serif; font-size: 11pt;
         line-height: 1.5; color: #1f2328; max-width: 820px; margin: 0 auto; padding: 0 12px; }}
  h1 {{ font-size: 20pt; border-bottom: 2px solid #d0d7de; padding-bottom: 4px; }}
  h2 {{ font-size: 15pt; margin-top: 1.6em; border-bottom: 1px solid #d0d7de; padding-bottom: 2px; }}
  h3 {{ font-size: 12.5pt; margin-top: 1.2em; }}
  blockquote {{ margin: 0.6em 0; padding: 0.3em 0.9em; border-left: 4px solid #d0d7de; color: #57606a; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 9.5pt; margin: 0.6em 0; }}
  th, td {{ border: 1px solid #d0d7de; padding: 4px 6px; vertical-align: top; text-align: left; }}
  th {{ background: #f6f8fa; }}
  code {{ font-family: Consolas, "Cascadia Mono", monospace; background: #f6f8fa; padding: 0 3px; }}
  math[display="block"] {{ margin: 0.5em 0; }}
  p {{ margin: 0.35em 0; }}
  h2, h3 {{ break-after: avoid; }}
  tr, blockquote {{ break-inside: avoid; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def render_html(bundle: ExportBundle) -> str:
    return HTML_TEMPLATE.format(title=html_lib.escape(bundle.title), body=markdown_to_html(bundle.markdown))


# ---------------------------------------------------------------------------
# HTML → PDF
# ---------------------------------------------------------------------------


def find_browser(explicit: str | None = None) -> str | None:
    if explicit and Path(explicit).exists():
        return explicit
    candidates: list[str] = []
    if sys.platform == "win32":
        for root in filter(None, [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                                  os.environ.get("LOCALAPPDATA")]):
            candidates += [
                str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"),
                str(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            ]
    elif sys.platform == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def _chrome_pdf(browser: str, html: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="lezionelive-pdf-") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "export.html"
        out = tmp_path / "export.pdf"
        src.write_text(html, encoding="utf-8")
        cmd = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            # Không tắt JavaScript bằng --blink-settings: headless dùng JS nội bộ để in PDF.
            # Nội dung đã được thoát HTML nên không có script từ transcript/LLM.
            f"--user-data-dir={tmp_path / 'profile'}",
            "--no-pdf-header-footer",
            "--print-to-pdf-no-header",
            f"--print-to-pdf={out}",
            src.as_uri(),
        ]
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if not out.exists() or out.stat().st_size == 0:
            raise ExportError("Trình duyệt headless không tạo được PDF")
        return out.read_bytes()


def _weasy_pdf(html: str) -> bytes:
    from weasyprint import HTML  # type: ignore[import-not-found]

    return HTML(string=html).write_pdf()


def pdf_engine(chrome_path: str | None = None) -> str | None:
    if find_browser(chrome_path):
        return "chrome"
    try:
        import weasyprint  # type: ignore[import-not-found]  # noqa: F401

        return "weasyprint"
    except Exception:  # noqa: BLE001 - WeasyPrint thiếu thư viện Pango cũng báo lỗi lúc import
        return None


async def render_pdf(html: str, chrome_path: str | None = None) -> bytes:
    browser = find_browser(chrome_path)
    if browser:
        try:
            return await asyncio.to_thread(_chrome_pdf, browser, html)
        except (ExportError, subprocess.TimeoutExpired, OSError) as e:
            log.warning("Xuất PDF bằng %s lỗi: %s", browser, e)
    try:
        return await asyncio.to_thread(_weasy_pdf, html)
    except Exception as e:  # noqa: BLE001
        raise ExportError(
            "Không tìm thấy Chrome/Edge để xuất PDF (và WeasyPrint không dùng được). "
            "Hãy xuất HTML rồi dùng 'In → Lưu thành PDF' trong trình duyệt."
        ) from e
