"""Dịch bằng gói Pro qua Claude Code CLI: phân tích stream-json, phân loại lỗi, chọn nguồn dịch."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.llm import claude_code as cc_mod
from app.llm.claude_code import ClaudeCodeClient, classify_cli_error
from app.llm.client import LLMError
from app.llm.prompt_library import PromptLibrary
from app.llm.translator import TranslationContext, Translator


def _lines(*events: dict) -> str:
    return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)


def _delta(text: str) -> dict:
    return {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}}


class FakePopen:
    last_args: list[str] = []
    stdout_text = ""
    stderr_text = ""
    returncode = 0

    def __init__(self, args, **kwargs):
        FakePopen.last_args = args
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(FakePopen.stdout_text)
        self.stderr = io.StringIO(FakePopen.stderr_text)
        self.returncode = FakePopen.returncode
        self.sent = None
        FakePopen.system = Path(args[args.index("--system-prompt-file") + 1]).read_text(encoding="utf-8")

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cc_mod.subprocess, "Popen", FakePopen)
    return ClaudeCodeClient(tmp_path / "cc", exe_finder=lambda: str(tmp_path / "claude.exe"))


@pytest.fixture(autouse=True)
def _fake_exe(tmp_path):
    (tmp_path / "claude.exe").write_text("")


async def test_stream_parses_deltas_and_result(client):
    FakePopen.stdout_text = _lines(
        {"type": "system", "subtype": "init"},
        _delta("Nếu lần đầu "),
        _delta("điều kiện đúng,"),
        {"type": "result", "subtype": "success", "is_error": False, "result": "Nếu lần đầu điều kiện đúng,"},
    )
    got: list[str] = []
    res = await client.stream(
        purpose="translate",
        session_id=1,
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=[{"type": "text", "text": "QUY TẮC"}],
        messages=[{"role": "user", "content": "Se la prima volta la condizione è vera,"}],
        on_text=got.append,
    )
    assert res.text == "Nếu lần đầu điều kiện đúng,"
    assert "".join(got) == res.text
    args = FakePopen.last_args
    assert args[args.index("--model") + 1] == "claude-haiku-4-5-20251001"
    assert args[args.index("--tools") + 1] == ""
    assert FakePopen.system == "QUY TẮC"
    assert not list((client.workdir).glob("sys-*"))  # file system prompt tạm đã xoá


async def test_auth_error_maps_to_auth(client):
    FakePopen.stdout_text = _lines(
        {"type": "result", "subtype": "success", "is_error": True, "result": "Failed to authenticate: OAuth session expired"}
    )
    with pytest.raises(LLMError) as e:
        await client.stream(purpose="t", session_id=None, model="m", max_tokens=10, messages=[{"role": "user", "content": "x"}])
    assert e.value.code == "auth"
    assert "claude auth login" in e.value.message


def test_classify_quota_and_busy():
    assert classify_cli_error("Claude usage limit reached. Your limit will reset at 5pm").code == "auth"
    assert classify_cli_error("API Error: 529 overloaded").retryable
    assert classify_cli_error("something odd").code == "bad_request"


async def test_json_extracts_items(client):
    FakePopen.stdout_text = _lines(
        {"type": "result", "subtype": "success", "is_error": False, "result": '```json\n{"items": [{"n": 1, "vi": "Chào"}]}\n```'}
    )
    data, _ = await client.json(
        purpose="t", session_id=None, model="m", max_tokens=10, messages=[{"role": "user", "content": "x"}], schema={}
    )
    assert data == {"items": [{"n": 1, "vi": "Chào"}]}


class _Settings:
    def __init__(self, provider: str) -> None:
        self.value = SimpleNamespace(translate_provider=provider, translate_model="claude-haiku-4-5-20251001")

    def get(self):
        return self.value


class _ApiLLM:
    has_key = False

    async def stream(self, **kw):  # pragma: no cover - không được gọi
        raise AssertionError("không được gọi API khi chọn Claude Code")


async def test_translator_uses_claude_code_when_selected(client):
    FakePopen.stdout_text = _lines({"type": "result", "subtype": "success", "is_error": False, "result": "Xin chào"})
    t = Translator(_ApiLLM(), PromptLibrary(), _Settings("claude_code"), client)  # type: ignore[arg-type]
    assert t.ready  # không cần API key
    out = await t.translate_stream("Ciao", TranslationContext(course_name="x"), session_id=None)
    assert out.vi == "Xin chào"

    api_only = Translator(_ApiLLM(), PromptLibrary(), _Settings("api"), client)  # type: ignore[arg-type]
    assert not api_only.ready


# --------------------------------------------------------------- trợ lý học tập
from app.llm.claude_code import _user_text  # noqa: E402
from app.llm.router import AssistantLLM  # noqa: E402


def test_multi_turn_flattened_once():
    prompt = _user_text(
        [
            {"role": "user", "content": [{"type": "text", "text": "TRANSCRIPT"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Trả lời 1"}]},
            {"role": "user", "content": [{"type": "text", "text": "Câu hỏi mới?"}, {"type": "document", "title": "a.pdf"}]},
        ]
    )
    assert prompt.count("TRANSCRIPT") == 1
    assert "<assistant>\nTrả lời 1\n</assistant>" in prompt
    assert prompt.rstrip().endswith('(Tài liệu "a.pdf" không gửi kèm được qua Claude Code.)')


class _AsstSettings:
    def __init__(self, provider: str, effort: str = "high") -> None:
        self.value = SimpleNamespace(
            assistant_provider=provider,
            assistant_model="claude-sonnet-5",
            assistant_cc_model="opus",
            assistant_effort=effort,
        )

    def get(self):
        return self.value


async def test_assistant_router_uses_cc_model_and_effort(client):
    FakePopen.stdout_text = _lines(
        {"type": "system", "subtype": "init", "model": "claude-opus-5"},
        {"type": "result", "subtype": "success", "is_error": False, "result": "Giải thích…"},
    )
    router = AssistantLLM(_ApiLLM(), client, _AsstSettings("claude_code"))  # type: ignore[arg-type]
    assert router.has_key and not router.supports_documents
    res = await router.stream(
        purpose="chat", session_id=1, model="claude-sonnet-5", max_tokens=8000,
        messages=[{"role": "user", "content": "Vòng while là gì?"}],
    )  # fmt: skip
    args = FakePopen.last_args
    assert args[args.index("--model") + 1] == "opus"
    assert args[args.index("--effort") + 1] == "high"
    assert res.model == "claude-opus-5"  # model thật CLI báo về

    auto = AssistantLLM(_ApiLLM(), client, _AsstSettings("claude_code", effort="auto"))  # type: ignore[arg-type]
    await auto.stream(purpose="chat", session_id=1, model="x", max_tokens=10, messages=[{"role": "user", "content": "?"}])
    assert "--effort" not in FakePopen.last_args

    api = AssistantLLM(_ApiLLM(), client, _AsstSettings("api"))  # type: ignore[arg-type]
    assert not api.has_key and api.supports_documents
