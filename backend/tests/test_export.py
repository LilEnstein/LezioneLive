"""Chuyển Markdown → HTML (MathML), thoát HTML, CSV thuật ngữ, prompt STT, dọn transcript."""

from __future__ import annotations

from app.llm.summarizer import REQUIRED_SECTIONS, ensure_sections
from app.routers.glossary import parse_csv
from app.routers.search import fts_query
from app.services.export_md import markdown_to_html, slugify
from app.stt.base import clean_transcript
from app.stt.deepgram import keyword_params


def test_math_is_converted_to_mathml_and_currency_is_not():
    html = markdown_to_html("Mô hình: $\\beta_0 + \\beta_1 x$ và giá $5 hay $6.\n\n$$\\hat{y} = X\\beta$$")
    assert html.count("<math") == 2
    assert 'display="block"' in html
    assert "$5 hay $6" in html


def test_raw_html_is_escaped():
    html = markdown_to_html("**[00:01]** \\<script>alert(1)\\</script> và <img src=x onerror=alert(1)>")
    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;" in html or "&lt;script>" in html


def test_slugify_handles_vietnamese():
    assert slugify("Buổi học Đầu tiên: Hồi quy!") == "Buoi-hoc-Dau-tien-Hoi-quy"


def test_ensure_sections_appends_missing_headings():
    md = "## Tổng quan buổi học\nabc\n\n## Ý chính (theo trình tự)\n- x\n"
    out = ensure_sections(md)
    for heading in REQUIRED_SECTIONS:
        assert f"## {heading}" in out or heading == "Ý chính"
    assert out.count("## Ý chính") == 1  # tiêu đề có chú thích trong ngoặc vẫn được nhận
    assert out.count("_Không có._") == 5


def test_parse_csv_variants():
    rows = parse_csv("Italiano,Tiếng Việt\nmedia,trung bình\n".encode())
    assert rows == [{"term_it": "media", "term_vi": "trung bình", "term_en": "", "note": ""}]
    rows = parse_csv(b"varianza;varianza VI;variance;nota\n")
    assert rows[0]["term_en"] == "variance"
    rows = parse_csv("term_it\tterm_vi\nmoda\tmốt\n".encode("cp1252", errors="ignore"))
    assert rows[0]["term_it"] == "moda"


def test_fts_query_is_sanitised():
    assert fts_query('regressione "logistica') == '"regressione" "logistica"*'
    assert fts_query("  ()  ") is None


def test_clean_transcript_filters_hallucinations():
    assert clean_transcript("  Sottotitoli creati dalla comunità Amara.org ") == ""
    assert clean_transcript("...") == ""
    assert clean_transcript("Allora,   oggi\nparliamo") == "Allora, oggi parliamo"


def test_clean_transcript_collapses_whisper_loops():
    loop = "Siamo in due classi " + "in due classi " * 40 + "e poi"
    assert clean_transcript(loop) == "Siamo in due classi… e poi"
    assert clean_transcript("no no no no, non è così") == "no…, non è così"
    # lặp tự nhiên (≤ 3 lần) giữ nguyên
    assert clean_transcript("La media, la media è importante") == "La media, la media è importante"
    assert clean_transcript("molto molto molto bene") == "molto molto molto bene"


def test_deepgram_keywords():
    assert keyword_params("nova-3", ["regressione logistica"]) == [("keyterm", "regressione logistica")]
    params = keyword_params("nova-2", ["regressione logistica", "odds ratio"])
    assert ("keywords", "regressione:2") in params and ("keywords", "odds:2") in params


def test_stt_prompt_contains_glossary(actx):
    from app.db.models import Course, GlossaryTerm, LectureSession
    from app.db.session import db_session

    with db_session(actx.engine) as s:
        course = Course(name="Stat", name_it="Statistica")
        s.add(course)
        s.commit()
        s.refresh(course)
        s.add(GlossaryTerm(course_id=course.id, term_it="verosimiglianza", term_vi="hợp lý"))
        s.add(GlossaryTerm(course_id=None, term_it="media", term_vi="trung bình"))
        sess = LectureSession(title="x", course_id=course.id)
        s.add(sess)
        s.commit()
        s.refresh(sess)
    prompt, keywords = actx.context.stt_prompt(sess.id)
    assert prompt.startswith("Lezione universitaria di Statistica.")
    assert "verosimiglianza" in prompt and "media" in prompt
    assert set(keywords) == {"verosimiglianza", "media"}
