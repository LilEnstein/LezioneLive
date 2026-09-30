"""Dịch bằng model trên máy (translate_provider = local): phương án dự phòng khi không muốn/không thể dùng Claude."""

from __future__ import annotations

import asyncio

import pytest

from app.config import DRAFT_MODEL_REPO
from app.db.models import GlossaryTerm, Segment
from app.db.session import db_session
from app.offline.draft_translator import (
    DraftTranslator,
    GlossaryInjector,
    clean_output,
    restore_placeholders,
    split_sentences,
)

from .test_api import wait_for
from .test_translation_queue import drain_events, make_session, segments, wait_until


class FakeLocal:
    """Thay ``DraftTranslator``: dịch giả, đếm số lượt gọi (để kiểm tra gộp lô)."""

    def __init__(self, ready: bool = True) -> None:
        self._ready = ready
        self.repo = "fake/nllb"
        self.error: str | None = None
        self.calls: list[tuple[list[str], list[tuple[str, str]], bool]] = []
        self.listeners: list = []
        self.delay = 0.0

    @property
    def ready(self) -> bool:
        return self._ready

    def cached(self, repo: str | None = None) -> bool:
        return self._ready

    def start_prepare(self, download: bool = True) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(self._ready)
        return fut

    def add_ready_listener(self, fn) -> None:
        self.listeners.append(fn)

    def make_ready(self) -> None:
        self._ready = True
        for fn in self.listeners:
            fn()

    async def translate_many(self, texts, *, glossary=(), with_en=False):
        self.calls.append((list(texts), list(glossary), with_en))
        if self.delay:
            await asyncio.sleep(self.delay)
        return [(f"VI-máy: {t}", f"EN-máy: {t}" if with_en else None) for t in texts]

    async def translate(self, text: str, *, glossary=()) -> str:
        return (await self.translate_many([text], glossary=glossary))[0][0]


def use_local(ctx, fake: FakeLocal) -> None:
    ctx.translator.local = fake
    ctx.translation.draft = fake
    fake.add_ready_listener(ctx.translation._on_local_ready)
    ctx.settings.update({"translate_provider": "local"})


# ------------------------------------------------------------------ tiện ích


def test_split_sentences_keeps_short_fragments_attached():
    text = "Vediamo un esempio. Prendiamo un campione! Ok. Calcoliamo la media?"
    assert split_sentences(text) == ["Vediamo un esempio.", "Prendiamo un campione! Ok.", "Calcoliamo la media?"]
    assert split_sentences("  ") == []
    assert split_sentences("il valore 3.5 è alto") == ["il valore 3.5 è alto"]


def test_glossary_injection_longest_first_case_insensitive():
    inject = GlossaryInjector([("varianza", "phương sai"), ("varianza campionaria", "phương sai mẫu"), ("  ", "x")])
    assert inject("La Varianza  campionaria e la varianza.") == "La “phương sai mẫu” e la “phương sai”."
    assert inject("covarianza") == "covarianza"  # không thay giữa từ
    assert GlossaryInjector([])("testo") == "testo"


def test_glossary_placeholders_and_restore():
    inject = GlossaryInjector([("varianza", "phương sai"), ("minimi quadrati", "bình phương tối thiểu")])
    src, terms = inject.placeholders("I minimi quadrati e la varianza.")
    assert src == "I T1 e la T2." and terms == ["bình phương tối thiểu", "phương sai"]
    assert restore_placeholders("dùng T1 rồi t 2.", terms) == "dùng bình phương tối thiểu rồi phương sai."
    assert restore_placeholders("dùng T1 rồi …", terms) is None  # model làm mất mã → dịch lại cách khác
    # Câu gốc đã có chữ dạng "T1": không dùng mã, chèn thẳng thuật ngữ.
    assert inject.placeholders("Al tempo T1 la varianza cresce.") == ("Al tempo T1 la “phương sai” cresce.", [])
    assert restore_placeholders("T10 và T1", ["a"]) == "T10 và a"


def test_clean_output_removes_injected_quotes_and_leading_dash():
    assert clean_output("La varianza", "- “phương sai”  của lỗi") == "Phương sai của lỗi"
    assert clean_output('Dice "ciao"', 'Nói "xin chào"') == 'Nói "xin chào"'
    assert clean_output("La verosimiglianza", "hàm hợp lý tăng") == "Hàm hợp lý tăng"
    assert clean_output("e poi", "và sau đó") == "và sau đó"


# --------------------------------------------------------------- hàng đợi


async def test_local_engine_translates_offline_without_backlog(actx, harness):
    fake = FakeLocal()
    use_local(actx, fake)
    harness.online["value"] = False
    actx.connectivity.mark_offline()  # mất mạng không ảnh hưởng model trên máy
    sid, ids = make_session(actx, 3)
    events = actx.hub.subscribe(sid)
    for seg_id in ids:
        actx.translation.enqueue(sid, seg_id)
    await wait_until(lambda: all(s.status == "translated" for s in segments(actx, sid)))
    segs = segments(actx, sid)
    assert [s.text_vi for s in segs] == [f"VI-máy: frase {i + 1}" for i in range(3)]
    assert all(s.translation_kind == "local" for s in segs)
    assert not harness.llm.calls  # không gọi Claude
    evs = drain_events(events)
    translated = [e for e in evs if e["type"] == "segment.translated"]
    assert {e["translation_kind"] for e in translated} == {"local"}
    assert not any(e["type"] in ("segment.pending", "segment.draft") for e in evs)


async def test_local_engine_batches_waiting_segments_and_uses_glossary(actx):
    fake = FakeLocal()
    fake.delay = 0.1
    use_local(actx, fake)
    actx.settings.update({"translate_concurrency": 1, "secondary_language": "en"})
    with db_session(actx.engine) as s:
        s.add(GlossaryTerm(term_it="varianza", term_vi="phương sai"))
        s.commit()
    sid, ids = make_session(actx, 5)
    for seg_id in ids:
        actx.translation.enqueue(sid, seg_id)
    assert await actx.translation.drain(sid, timeout=5)
    assert len(fake.calls) < 5  # câu chờ được dịch gộp
    assert all(("varianza", "phương sai") in glossary for _, glossary, _ in fake.calls)
    assert all(with_en for _, _, with_en in fake.calls)
    assert all(s.text_en and s.text_en.startswith("EN-máy") for s in segments(actx, sid))


async def test_missing_local_model_warns_then_translates_once_ready(actx):
    fake = FakeLocal(ready=False)
    use_local(actx, fake)
    sid, ids = make_session(actx, 2)
    events = actx.hub.subscribe(sid)
    actx.translation.enqueue(sid, ids[0])
    actx.translation.enqueue(sid, ids[1])
    await asyncio.sleep(0.1)
    assert all(s.status == "transcribed" for s in segments(actx, sid))  # chưa có model: giữ transcript, chờ
    assert not actx.translator.ready
    fake.make_ready()  # người dùng bấm "Tải model" → tải + nạp xong
    await wait_until(lambda: all(s.status == "translated" for s in segments(actx, sid)))
    assert all(s.translation_kind == "local" for s in segments(actx, sid))
    assert not any(e["type"] == "warning" and e["code"].startswith("API_KEY") for e in drain_events(events))


async def test_load_error_publishes_local_model_warning(actx):
    fake = FakeLocal(ready=True)
    use_local(actx, fake)
    fake._ready = False  # model biến mất/không nạp được sau khi báo có sẵn
    fake.cached = lambda repo=None: True  # type: ignore[method-assign]
    fake.error = "Không nạp được model dịch cục bộ: RuntimeError"
    sid, ids = make_session(actx, 1)
    events = actx.hub.subscribe(sid)
    actx.translation.enqueue(sid, ids[0])
    await wait_until(lambda: segments(actx, sid)[0].status == "failed")
    warnings = [e for e in drain_events(events) if e["type"] == "warning"]
    assert [w["code"] for w in warnings] == ["LOCAL_MODEL_MISSING"]
    assert "Không nạp được" in warnings[0]["message"]


async def test_switching_back_to_claude_and_retranslating_machine_segments(actx, harness):
    fake = FakeLocal()
    use_local(actx, fake)
    sid, ids = make_session(actx, 3)
    for seg_id in ids:
        actx.translation.enqueue(sid, seg_id)
    assert await actx.translation.drain(sid, timeout=5)
    assert actx.translation.retranslate_machine(sid) == 0  # đang dùng model máy: không có gì để dịch lại
    actx.settings.update({"translate_provider": "api"})
    assert actx.translation.retranslate_machine(sid) == 3
    await wait_until(lambda: all(s.translation_kind == "claude" for s in segments(actx, sid)))
    # câu tồn đọng được dịch gộp (bản dịch gộp giả: "bản dịch n")
    assert [s.text_vi for s in segments(actx, sid)] == [f"bản dịch {i + 1}" for i in range(3)]


def test_retranslate_endpoint_and_local_model_api(client):
    ctx = client.ctx
    with db_session(ctx.engine) as s:
        from app.db.models import LectureSession

        sess = LectureSession(title="Lezione")
        s.add(sess)
        s.commit()
        s.refresh(sess)
        seg = Segment(session_id=sess.id, seq=1, t_start_ms=0, t_end_ms=1000, text_it="ciao",
                      text_vi="xin chào", status="translated", translation_kind="local")
        s.add(seg)
        s.commit()
        sid, seg_id = sess.id, seg.id
    r = client.post(f"/api/sessions/{sid}/retranslate")
    assert r.status_code == 200 and r.json() == {"requeued": 1}

    def kind() -> str | None:
        with db_session(ctx.engine) as s:
            return s.get(Segment, seg_id).translation_kind

    wait_for(lambda: kind() == "claude")

    r = client.put("/api/settings", json={"translate_provider": "local"})
    assert r.status_code == 200
    assert [m["repo"] for m in r.json()["choices"]["local_models"]][0] == DRAFT_MODEL_REPO
    assert client.post(f"/api/sessions/{sid}/retranslate").status_code == 409
    health = client.get("/api/health").json()
    assert health["translation"]["provider"] == "local"
    assert health["translation"]["local_model"]["model"] == DRAFT_MODEL_REPO

    info = client.get("/api/offline/draft").json()
    assert {m["repo"] for m in info["models"]} >= {DRAFT_MODEL_REPO}
    assert sum(m["active"] for m in info["models"]) == 1
    r = client.post("/api/offline/draft/prepare", json={"repo": "someone/unknown-model"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "UNKNOWN_MODEL"


def test_switching_local_model_unloads_previous():
    dt = DraftTranslator(DRAFT_MODEL_REPO)
    dt._translator, dt._tokenizer, dt._loaded_repo = object(), object(), DRAFT_MODEL_REPO
    assert dt.ready
    dt.set_repo("OpenNMT/nllb-200-distilled-1.3B-ct2-int8")
    assert not dt.ready and dt._translator is None
    assert dt.status()["label"] == "NLLB 1.3B"


# ------------------------------------------------------------ model thật


@pytest.mark.skipif(not DraftTranslator(DRAFT_MODEL_REPO).cached(), reason="Chưa tải model NLLB về máy")
async def test_real_nllb_keeps_glossary_terms():
    dt = DraftTranslator(DRAFT_MODEL_REPO)
    assert await dt.prepare(download=False)
    (vi, en), = await dt.translate_many(
        ["Usiamo la regressione logistica quando la variabile dipendente è binaria. Se la varianza dell'errore "
         "non è costante, abbiamo un problema."],
        glossary=[("regressione logistica", "hồi quy logistic"), ("variabile dipendente", "biến phụ thuộc"),
                  ("varianza", "phương sai")],
        with_en=True,
    )
    assert "hồi quy logistic" in vi and "biến phụ thuộc" in vi and "phương sai" in vi
    assert "“" not in vi and "T1" not in vi and en and "logistic" in en.lower()
