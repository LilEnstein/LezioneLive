"""Prompt dịch, tách luồng VI/EN, dịch gộp (FR-TR-01..04)."""

from __future__ import annotations

import pytest

from app.llm.client import LLMError
from app.llm.prompt_library import PromptLibrary
from app.llm.translator import (
    DualStreamSplitter,
    GlossaryEntry,
    TranslationContext,
    Translator,
    clean_translation,
    format_glossary,
)

from .conftest import FakeLLM


class _Settings:
    def __init__(self, **kw):
        from app.config import AppSettings

        self.s = AppSettings(**kw)

    def get(self):
        return self.s


def ctx(**kw) -> TranslationContext:
    base = dict(
        course_name="Statistica Multivariata",
        glossary=[GlossaryEntry("regressione logistica", "hồi quy logistic", "logistic regression")],
        previous=[("Buongiorno a tutti.", "Chào mọi người."), ("Oggi iniziamo.", None)],
        slide_summary="Slide về hồi quy.",
    )
    base.update(kw)
    return TranslationContext(**base)


def test_system_prompt_contains_rules_glossary_and_context():
    t = Translator(FakeLLM(), PromptLibrary(), _Settings())
    blocks = t.build_system(ctx())
    assert len(blocks) == 2
    stable, volatile = blocks[0]["text"], blocks[1]["text"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]
    assert "Môn học: Statistica Multivariata" in stable
    assert "- regressione logistica → hồi quy logistic (EN: logistic regression)" in stable
    assert "β₀ + β₁x" in stable  # quy tắc giữ ký hiệu toán
    assert "Slide về hồi quy." in stable
    assert "[IT] Buongiorno a tutti." in volatile and "[VI] Chào mọi người." in volatile
    assert "[VI] (chưa dịch)" in volatile
    assert "<<<EN>>>" not in stable


def test_secondary_language_rule_only_when_enabled():
    t = Translator(FakeLLM(), PromptLibrary(), _Settings())
    assert "<<<EN>>>" in t.build_system(ctx(secondary_language="en"))[0]["text"]


def test_empty_glossary_placeholder():
    assert format_glossary([]) == "(chưa có thuật ngữ)"


@pytest.mark.parametrize(
    "chunks",
    [
        ["Xin chào", " các bạn\n<<<EN>>>\nHello", " everyone"],
        ["Xin chào các bạn\n<<", "<E", "N>>>\nHello everyone"],
        ["Xin chào các bạn\n<<<EN>", ">>Hello everyone"],
    ],
)
def test_dual_stream_splitter_handles_marker_across_chunks(chunks):
    sp = DualStreamSplitter()
    emitted = "".join(sp.feed(c) for c in chunks) + sp.finish()
    assert emitted.strip() == "Xin chào các bạn"
    assert "<" not in emitted
    assert sp.en == "Hello everyone"


def test_dual_stream_splitter_without_marker_keeps_everything():
    sp = DualStreamSplitter()
    out = sp.feed("Chỉ có ") + sp.feed("tiếng Việt <") + sp.finish()
    assert out == "Chỉ có tiếng Việt <"
    assert sp.en is None


def test_clean_translation_strips_quotes():
    assert clean_translation('  "Vậy thì, hôm nay"  ') == "Vậy thì, hôm nay"
    assert clean_translation("«Ciao»") == "Ciao"


async def test_translate_stream_uses_haiku_params_and_streams_deltas():
    llm = FakeLLM(lambda call: "Vậy thì, hôm nay chúng ta nói về hồi quy logistic.")
    t = Translator(llm, PromptLibrary(), _Settings())
    deltas: list[str] = []
    out = await t.translate_stream("Allora, oggi parliamo di regressione logistica.", ctx(),
                                   session_id=None, on_delta=deltas.append)
    assert out.vi == "Vậy thì, hôm nay chúng ta nói về hồi quy logistic."
    assert "".join(deltas) == out.vi
    call = llm.calls[0]
    assert call.model == "claude-haiku-4-5-20251001"
    assert call.kwargs["max_tokens"] == 400
    assert call.kwargs["temperature"] == 0.2
    assert call.kwargs["max_retries"] == 0
    assert call.messages == [{"role": "user", "content": "Allora, oggi parliamo di regressione logistica."}]


async def test_translate_stream_with_english():
    llm = FakeLLM(lambda call: "Xin chào\n<<<EN>>>\nHello")
    t = Translator(llm, PromptLibrary(), _Settings(secondary_language="en"))
    deltas: list[str] = []
    out = await t.translate_stream("Ciao", ctx(secondary_language="en"), session_id=None, on_delta=deltas.append)
    assert out.vi == "Xin chào"
    assert out.en == "Hello"
    assert "Hello" not in "".join(deltas)


async def test_translate_batch_maps_numbers_and_detects_missing():
    llm = FakeLLM(lambda call: {"items": [{"n": 2, "vi": "hai"}, {"n": 1, "vi": "một"}]})
    t = Translator(llm, PromptLibrary(), _Settings())
    outs = await t.translate_batch(["uno", "due"], ctx(), session_id=None)
    assert [o.vi for o in outs] == ["một", "hai"]
    user = llm.calls[0].messages[0]["content"]
    assert "[1] uno" in user and "[2] due" in user
    assert llm.calls[0].kwargs["schema"]["properties"]["items"]["items"]["required"] == ["n", "vi"]

    llm.responder = lambda call: {"items": [{"n": 1, "vi": "một"}]}
    with pytest.raises(LLMError):
        await t.translate_batch(["uno", "due"], ctx(), session_id=None)
