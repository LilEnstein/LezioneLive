"""API REST (mục 9.1) qua TestClient với LLM/STT giả."""

from __future__ import annotations

import json
import logging
import time

import pytest
from sqlmodel import select

from app.db.models import Segment
from app.db.session import db_session
from app.security import ANTHROPIC_KEY_NAME, SERVICE_NAME
from app.services.export_md import find_browser

from .conftest import make_pdf

VALID_KEY = "sk-ant-api03-" + "A" * 30 + "abcd"


def wait_for(fn, timeout: float = 5.0, interval: float = 0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("Hết thời gian chờ")


def new_session(client, **body) -> int:
    r = client.post("/api/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def add_segments(client, sid: int, texts: list[tuple[str, str | None]]) -> list[int]:
    ids = []
    with db_session(client.ctx.engine) as s:
        for i, (it, vi) in enumerate(texts):
            seg = Segment(session_id=sid, seq=i + 1, t_start_ms=i * 6000, t_end_ms=i * 6000 + 5000, text_it=it,
                          text_vi=vi, status="translated" if vi else "transcribed")
            s.add(seg)
            s.commit()
            s.refresh(seg)
            ids.append(seg.id)
    return ids


# ---------------------------------------------------------------------------


def test_health_and_settings(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["api_key"] == {"present": False, "last4": None, "source": None}
    assert data["stt"]["ready"] is True
    assert "pdf_export" in data["capabilities"]

    s = client.get("/api/settings").json()
    assert s["settings"]["translate_model"] == "claude-haiku-4-5-20251001"
    assert s["settings"]["assistant_model"] == "claude-sonnet-5"
    assert s["settings"]["save_raw_audio"] is False
    assert s["settings"]["vad_min_ms"] == 1500 and s["settings"]["vad_max_ms"] == 12000

    r = client.put("/api/settings", json={"cost_threshold_usd": 1.5, "secondary_language": "en"})
    assert r.status_code == 200
    assert r.json()["settings"]["cost_threshold_usd"] == 1.5
    assert client.put("/api/settings", json={"vad_min_ms": 20000}).status_code == 422
    assert client.put("/api/settings", json={"unknown_key": 1}).status_code == 422


def test_api_key_lifecycle_never_exposes_key(client, harness, memory_keyring, caplog):
    caplog.set_level(logging.DEBUG)
    harness.llm.valid_keys.add(VALID_KEY)
    r = client.put("/api/settings/api-key", json={"api_key": "sk-ant-api03-" + "B" * 34})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_API_KEY"
    assert memory_keyring.store == {}

    assert client.put("/api/settings/api-key", json={"api_key": "not-a-key-at-all"}).status_code == 400

    r = client.put("/api/settings/api-key", json={"api_key": VALID_KEY})
    assert r.status_code == 200
    assert r.json() == {"valid": True, "last4": "abcd", "source": "keychain"}
    assert memory_keyring.store[(SERVICE_NAME, ANTHROPIC_KEY_NAME)] == VALID_KEY

    settings = client.get("/api/settings")
    health = client.get("/api/health")
    for resp in (settings, health):
        assert VALID_KEY not in resp.text
    assert settings.json()["api_key"] == {"present": True, "last4": "abcd", "source": "keychain"}
    assert "sk-ant-" not in caplog.text  # AC-01: không log key

    assert client.delete("/api/settings/api-key").json()["api_key"]["present"] is False
    assert memory_keyring.store == {}


def test_session_lifecycle(client, harness):
    course = client.post("/api/courses", json={"name": "Statistica", "name_it": "Statistica Multivariata",
                                               "lecturer": "Prof. Bianchi"}).json()
    harness.stt.provider._ready = False
    r = client.post("/api/sessions", json={"course_id": course["id"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "STT_NOT_READY"
    harness.stt.provider._ready = True

    sid = new_session(client, course_id=course["id"], title="Lezione 1")
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["session"]["status"] == "live"
    assert detail["course"]["name_it"] == "Statistica Multivariata"
    assert detail["runner"]["state"] == "live"

    assert client.post(f"/api/sessions/{sid}/pause").json() == {"status": "paused"}
    assert client.get(f"/api/sessions/{sid}").json()["session"]["status"] == "paused"
    assert client.post(f"/api/sessions/{sid}/resume").json() == {"status": "live"}

    listed = client.get("/api/sessions").json()
    assert listed[0]["id"] == sid and listed[0]["course_name"] == "Statistica"
    assert client.get("/api/courses").json()[0]["session_count"] == 1

    add_segments(client, sid, [("Allora, oggi parliamo di regressione.", "Vậy thì hôm nay nói về hồi quy.")])
    r = client.post(f"/api/sessions/{sid}/end")
    assert r.json()["status"] == "ended"
    summary = wait_for(lambda: (d := client.get(f"/api/sessions/{sid}/summary").json())["status"] == "done" and d)
    md = summary["summary"]["content_md"]
    for heading in ("## Tổng quan buổi học", "## Ý chính", "## Khái niệm & định nghĩa", "## Công thức",
                    "## Bài tập / Deadline / Thông báo hành chính", "## Những đoạn cần xem lại",
                    "## Câu hỏi gợi ý để hỏi giảng viên"):
        assert heading in md
    cards = client.get(f"/api/sessions/{sid}/flashcards").json()
    assert 20 <= len(cards) <= 40
    assert client.post(f"/api/sessions/{sid}/resume").status_code == 409

    cost = client.get(f"/api/sessions/{sid}/cost").json()
    purposes = {p["purpose"] for p in cost["by_purpose"]}
    assert {"summary", "flashcard"} <= purposes
    assert cost["total_usd"] > 0

    assert client.delete(f"/api/sessions/{sid}").json() == {"deleted": True}
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_pins_default_to_latest_segment(client):
    sid = new_session(client)
    r = client.post(f"/api/sessions/{sid}/pins", json={})
    assert r.status_code == 409
    ids = add_segments(client, sid, [("uno", "một"), ("due", "hai"), ("tre", None)])
    pin = client.post(f"/api/sessions/{sid}/pins", json={"kind": "confused"}).json()
    assert pin["segment_id"] == ids[-1] and pin["kind"] == "confused"
    pin2 = client.post(f"/api/sessions/{sid}/pins", json={"segment_id": ids[0], "kind": "todo", "note": "hỏi lại"})
    assert pin2.status_code == 201
    patched = client.patch(f"/api/pins/{pin2.json()['id']}", json={"kind": "important"}).json()
    assert patched["kind"] == "important" and patched["note"] == "hỏi lại"
    assert len(client.get(f"/api/sessions/{sid}/pins").json()) == 2
    assert client.delete(f"/api/pins/{pin['id']}").json() == {"deleted": True}


def test_segment_edit_retranslates(client):
    sid = new_session(client)
    ids = add_segments(client, sid, [("regressione logistika", "hồi quy sai")])
    r = client.patch(f"/api/segments/{ids[0]}", json={"text_it_edited": "regressione logistica"})
    assert r.status_code == 200
    assert r.json()["text_it_edited"] == "regressione logistica"

    def translated():
        seg = client.get(f"/api/sessions/{sid}").json()["segments"][0]
        return seg["status"] == "translated" and seg
    seg = wait_for(translated)
    assert seg["text_vi"] == "VI: regressione logistica"


def test_glossary_crud_and_csv(client):
    course = client.post("/api/courses", json={"name": "ML"}).json()
    t = client.post("/api/glossary", json={"term_it": "regressione logistica", "term_vi": "hồi quy logistic",
                                           "term_en": "logistic regression", "course_id": course["id"]}).json()
    client.post("/api/glossary", json={"term_it": "media", "term_vi": "trung bình"})  # dùng chung
    assert len(client.get("/api/glossary", params={"course_id": course["id"]}).json()) == 2
    assert len(client.get("/api/glossary", params={"course_id": "shared"}).json()) == 1
    assert client.get("/api/glossary", params={"q": "logistic"}).json()[0]["id"] == t["id"]
    up = client.put(f"/api/glossary/{t['id']}", json={"term_it": "regressione logistica",
                                                      "term_vi": "hồi quy logistic (logit)",
                                                      "course_id": course["id"]})
    assert up.json()["term_vi"] == "hồi quy logistic (logit)"

    csv_data = "term_it;term_vi;term_en;note\nvarianza;phương sai;variance;\nregressione logistica;hồi quy logistic;;\n;thiếu;;\n"
    r = client.post("/api/glossary/import", params={"course_id": course["id"]},
                    files={"file": ("g.csv", csv_data.encode("utf-8-sig"), "text/csv")})
    assert r.json() == {"imported": 1, "updated": 1, "skipped": 1}
    exported = client.get("/api/glossary/export", params={"course_id": course["id"]})
    assert exported.headers["content-type"].startswith("text/csv")
    text = exported.content.decode("utf-8-sig")
    assert "varianza,phương sai,variance" in text
    assert client.delete(f"/api/glossary/{t['id']}").json() == {"deleted": True}


def test_slides_upload_validation_and_suggestions(client):
    sid = new_session(client)
    big = b"%PDF" + b"0" * (30 * 1024 * 1024 + 10)
    assert client.post(f"/api/sessions/{sid}/slides", files={"file": ("big.pdf", big, "application/pdf")}
                       ).status_code == 413
    bad = client.post(f"/api/sessions/{sid}/slides", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert bad.status_code == 400
    pdf = make_pdf(["Regressione logistica", "Verosimiglianza", "Esercizi"])
    r = client.post(f"/api/sessions/{sid}/slides", files={"file": ("lezione 1.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    deck = r.json()
    assert deck["page_count"] == 3 and deck["has_text"]

    def ready():
        decks = client.get(f"/api/sessions/{sid}/slides").json()
        return decks[0]["status"] == "ready" and decks[0]
    deck = wait_for(ready)
    assert deck["summary"] == "Slide về hồi quy." and deck["uploaded_to_claude"] is True
    sugg = client.get(f"/api/sessions/{sid}/glossary-suggestions").json()
    assert [s["term_it"] for s in sugg] == ["verosimiglianza"]
    term = client.post(f"/api/glossary-suggestions/{sugg[0]['id']}/accept", json={}).json()
    assert term["source"] == "slides"
    assert client.get(f"/api/sessions/{sid}/glossary-suggestions").json() == []
    pdf_resp = client.get(f"/api/slides/{deck['id']}/file")
    assert pdf_resp.headers["content-type"] == "application/pdf"
    assert client.delete(f"/api/slides/{deck['id']}").json() == {"deleted": True}


def test_search_is_diacritics_insensitive(client):
    sid = new_session(client, title="Buổi tìm kiếm")
    add_segments(client, sid, [("La regressione logistica stima le probabilità.",
                                "Hồi quy logistic ước lượng xác suất."),
                               ("Passiamo agli esercizi.", "Chuyển sang bài tập.")])
    res = client.get("/api/search", params={"q": "hoi quy"}).json()
    assert len(res["results"]) == 1
    hit = res["results"][0]
    hs, he = res["highlight"]
    assert f"{hs}Hồi{he}" in hit["text_vi"]
    assert hit["session_title"] == "Buổi tìm kiếm" and hit["session_started_at"].endswith("Z")
    assert client.get("/api/search", params={"q": "eserc"}).json()["results"][0]["seq"] == 2
    assert client.get("/api/search", params={"q": '"("'}).json()["results"] == []


def test_flashcard_review(client):
    sid = new_session(client)
    add_segments(client, sid, [("Parliamo della varianza.", "Nói về phương sai.")])
    client.post(f"/api/sessions/{sid}/flashcards/regenerate")
    due = wait_for(lambda: (d := client.get("/api/flashcards/due").json())["total_due"] >= 20 and d)
    card = due["cards"][0]
    reviewed = client.post(f"/api/flashcards/{card['id']}/review", json={"grade": 5}).json()
    assert reviewed["repetitions"] == 1 and reviewed["interval_days"] == 1
    assert client.post(f"/api/flashcards/{card['id']}/review", json={"grade": 9}).status_code == 422
    stats = client.get("/api/flashcards/stats").json()
    assert stats["total"] >= 20 and stats["new"] == stats["total"] - 1


def test_export_markdown_and_html(client):
    sid = new_session(client, title="Lezione <script>alert(1)</script>")
    add_segments(client, sid, [("Beta zero più beta uno x.", "β₀ + β₁x."), ("Consegna entro venerdì.", None)])
    client.post(f"/api/sessions/{sid}/pins", json={"kind": "todo", "note": "deadline"})
    client.post(f"/api/sessions/{sid}/end")
    wait_for(lambda: client.get(f"/api/sessions/{sid}/summary").json()["status"] == "done")

    md = client.get(f"/api/sessions/{sid}/export", params={"format": "md"})
    assert md.status_code == 200
    assert "attachment" in md.headers["content-disposition"]
    text = md.text
    assert text.startswith("---\ntitle:")
    for part in ("## Tóm tắt", "## Ghim", "## Transcript song ngữ", "## Chat với trợ lý", "## Flashcards",
                 "**[00:00]** 🇮🇹 Beta zero più beta uno x.", "🇻🇳 β₀ + β₁x.", "_(chưa dịch)_"):
        assert part in text
    assert "### Tổng quan buổi học" in text  # tiêu đề tóm tắt được hạ cấp

    html = client.get(f"/api/sessions/{sid}/export", params={"format": "html"}).text
    assert "<math" in html  # LaTeX → MathML
    assert "<script>" not in html
    assert client.get(f"/api/sessions/{sid}/export", params={"format": "docx"}).status_code == 422


@pytest.mark.skipif(find_browser() is None, reason="Không có Chrome/Edge để xuất PDF")
def test_export_pdf(client):
    sid = new_session(client, title="Buổi học có dấu tiếng Việt")
    add_segments(client, sid, [("Beta zero più beta uno x.", "Hồi quy tuyến tính: β₀ + β₁x.")])
    r = client.get(f"/api/sessions/{sid}/export", params={"format": "pdf"})
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF")


def test_chat_sse_qa_and_compose(client, harness):
    sid = new_session(client)
    add_segments(client, sid, [("Oggi parliamo di overfitting.", "Hôm nay nói về overfitting.")])
    with client.stream("POST", f"/api/sessions/{sid}/chat", json={"message": "Overfitting là gì?"}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = [(blk.split("\n")[0][7:], json.loads(blk.split("\n")[1][6:])) for blk in body.strip().split("\n\n")]
    kinds = [k for k, _ in events]
    assert kinds[0] == "start" and kinds[-1] == "done" and "delta" in kinds
    answer = "".join(d["text"] for k, d in events if k == "delta")
    assert answer == events[-1][1]["message"]["content"]

    with client.stream("POST", f"/api/sessions/{sid}/chat",
                       json={"message": "Thầy nhắc lại được không?", "mode": "compose_it"}) as r:
        body = "".join(r.iter_text())
    assert "event: result" in body and "Professore" in body
    history = client.get(f"/api/sessions/{sid}/chat").json()
    assert [m["mode"] for m in history] == ["qa", "qa", "compose_it", "compose_it"]
    assert history[-1]["data"]["question_it"].startswith("Professore")

    harness.llm.set_has_key(False)
    r = client.post(f"/api/sessions/{sid}/chat", json={"message": "x"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "NO_API_KEY"


def test_security_origin_and_host(client):
    r = client.post("/api/courses", json={"name": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/courses", json={"name": "x"}, headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 201
    r = client.get("/api/health", headers={"Host": "evil.example:8765"})
    assert r.status_code == 400
    pre = client.options("/api/courses", headers={"Origin": "http://localhost:5173",
                                                  "Access-Control-Request-Method": "POST"})
    assert pre.headers.get("access-control-allow-origin") == "http://localhost:5173"
    pre = client.options("/api/courses", headers={"Origin": "https://evil.example",
                                                  "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in pre.headers


def test_translation_toggle_and_retry(client):
    sid = new_session(client)
    ids = add_segments(client, sid, [("uno", None)])
    assert client.post(f"/api/sessions/{sid}/translation", json={"enabled": False}).json() == {
        "translation_enabled": False}
    assert client.get(f"/api/sessions/{sid}").json()["session"]["translation_enabled"] is False
    client.post(f"/api/sessions/{sid}/translation", json={"enabled": True})

    def done():
        with db_session(client.ctx.engine) as s:
            return s.exec(select(Segment).where(Segment.id == ids[0])).one().status == "translated"
    wait_for(done)
    assert client.post(f"/api/segments/{ids[0]}/retry").json() == {"queued": True}
