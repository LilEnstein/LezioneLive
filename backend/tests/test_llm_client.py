"""LLMClient với SDK anthropic thật nhưng HTTP giả (MockTransport): tham số request, stream, usage, lỗi."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from app.llm.client import LLMClient, LLMError, TokenUsage, build_model_params, extract_json, model_caps
from app.security import ANTHROPIC_KEY_NAME, SecretStore

KEY = "sk-ant-api03-" + "x" * 40


def sse_body(chunks: list[str], *, model: str = "claude-haiku-4-5-20251001", stop_reason: str = "end_turn",
             input_tokens: int = 50, output_tokens: int = 12, cache_read: int = 0, cache_write: int = 0,
             cache_write_1h: int = 0) -> bytes:
    usage: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": 1,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
    }
    if cache_write:
        usage["cache_creation"] = {
            "ephemeral_5m_input_tokens": cache_write - cache_write_1h,
            "ephemeral_1h_input_tokens": cache_write_1h,
        }
    events: list[tuple[str, dict]] = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_test", "type": "message", "role": "assistant", "model": model, "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": usage}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
    ]
    for c in chunks:
        events.append(("content_block_delta", {"type": "content_block_delta", "index": 0,
                                               "delta": {"type": "text_delta", "text": c}}))
    events += [
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                           "usage": {"output_tokens": output_tokens}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


def error_response(status: int, etype: str, message: str, headers: dict | None = None) -> httpx2.Response:
    return httpx2.Response(status, json={"type": "error", "error": {"type": etype, "message": message}},
                           headers=headers or {})


class Recorder:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, *, session_id, purpose, model, usage: TokenUsage):
        self.rows.append({"session_id": session_id, "purpose": purpose, "model": model, "usage": usage})


def make_client(handler) -> tuple[LLMClient, list[httpx2.Request], Recorder]:
    requests: list[httpx2.Request] = []

    def wrapped(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return handler(request)

    def factory(**kw):
        return AsyncAnthropic(http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(wrapped)), **kw)

    store = SecretStore()
    store.set(ANTHROPIC_KEY_NAME, KEY)
    rec = Recorder()
    return LLMClient(store, rec, client_factory=factory), requests, rec  # type: ignore[arg-type]


def body_of(request: httpx2.Request) -> dict:
    return json.loads(request.content)


# ---------------------------------------------------------------------------


def test_model_caps_and_params():
    assert model_caps("claude-haiku-4-5-20251001").sampling
    assert not model_caps("claude-sonnet-5").sampling
    assert model_caps("claude-opus-5-5").can_disable_thinking is False
    assert model_caps("claude-opus-5").can_disable_thinking is True
    assert build_model_params("claude-haiku-4-5-20251001", "fast", 0.2) == {"extra_body": {"temperature": 0.2}}
    assert build_model_params("claude-sonnet-5", "fast", 0.2) == {"thinking": {"type": "disabled"}}
    assert build_model_params("claude-sonnet-5", "deep") == {
        "thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}}
    assert build_model_params("claude-opus-5-5", "fast") == {"output_config": {"effort": "low"}}
    assert build_model_params("claude-haiku-4-5", "deep") == {}
    assert build_model_params("some-future-model", "fast", 0.3) == {}


async def test_stream_haiku_translation_request_and_usage():
    client, requests, rec = make_client(
        lambda r: httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                  content=sse_body(["Vậy thì, ", "hôm nay ", "chúng ta…"]))
    )
    chunks: list[str] = []
    result = await client.stream(
        purpose="translate", session_id=7, model="claude-haiku-4-5-20251001", max_tokens=400,
        system=[{"type": "text", "text": "Quy tắc", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "Allora, oggi..."}],
        route="fast", temperature=0.2, on_text=chunks.append, max_retries=0,
    )
    assert result.text == "Vậy thì, hôm nay chúng ta…"
    assert "".join(chunks) == result.text
    body = body_of(requests[0])
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["max_tokens"] == 400
    assert body["stream"] is True
    assert body["temperature"] == 0.2
    assert "thinking" not in body
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert requests[0].headers["x-api-key"] == KEY
    assert rec.rows[0]["purpose"] == "translate" and rec.rows[0]["session_id"] == 7
    assert rec.rows[0]["usage"].input_tokens == 50 and rec.rows[0]["usage"].output_tokens == 12


async def test_sonnet_chat_disables_thinking_and_never_sends_temperature():
    client, requests, rec = make_client(
        lambda r: httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                  content=sse_body(["ok"], model="claude-sonnet-5", cache_read=900,
                                                   cache_write=300, cache_write_1h=200))
    )
    await client.stream(purpose="chat", session_id=1, model="claude-sonnet-5", max_tokens=8000,
                        messages=[{"role": "user", "content": "hi"}], route="fast", temperature=0.5,
                        cache_control={"type": "ephemeral"})
    body = body_of(requests[0])
    assert body["thinking"] == {"type": "disabled"}
    assert "temperature" not in body
    assert body["cache_control"] == {"type": "ephemeral"}
    usage = rec.rows[0]["usage"]
    assert (usage.cache_read_tokens, usage.cache_write_tokens, usage.cache_write_1h_tokens) == (900, 300, 200)


async def test_json_uses_structured_output_and_deep_route():
    payload = {"question_it": "Professore, ...", "back_translation_vi": "Thầy ...", "notes": ""}
    client, requests, _ = make_client(
        lambda r: httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                  content=sse_body([json.dumps(payload)], model="claude-sonnet-5"))
    )
    schema = {"type": "object", "properties": {"question_it": {"type": "string"}}, "required": ["question_it"],
              "additionalProperties": False}
    data, _ = await client.json(purpose="compose", session_id=None, model="claude-sonnet-5", max_tokens=2000,
                                messages=[{"role": "user", "content": "câu hỏi"}], schema=schema, route="deep")
    assert data == payload
    body = body_of(requests[0])
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": schema}
    assert body["output_config"]["effort"] == "medium"
    assert body["thinking"] == {"type": "adaptive"}


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (error_response(401, "authentication_error", "invalid x-api-key"), "auth", False),
        (error_response(429, "rate_limit_error", "slow down", {"retry-after": "7"}), "rate_limit", True),
        (error_response(529, "overloaded_error", "Overloaded"), "overloaded", True),
        (error_response(400, "invalid_request_error", "prompt is too long: 250000 tokens > 200000 maximum"),
         "context_too_long", False),
        (error_response(400, "invalid_request_error", "messages: bad"), "bad_request", False),
        (error_response(500, "api_error", "boom"), "server", True),
    ],
)
async def test_error_mapping(response, code, retryable):
    client, _, rec = make_client(lambda r: response)
    with pytest.raises(LLMError) as ei:
        await client.stream(purpose="translate", session_id=None, model="claude-haiku-4-5-20251001",
                            max_tokens=10, messages=[{"role": "user", "content": "x"}], max_retries=0)
    assert ei.value.code == code
    assert ei.value.retryable is retryable
    if code == "rate_limit":
        assert ei.value.retry_after == 7
    assert rec.rows == []


async def test_network_error_maps_to_network():
    def handler(request):
        raise httpx2.ConnectError("no route to host", request=request)

    client, _, _ = make_client(handler)
    with pytest.raises(LLMError) as ei:
        await client.stream(purpose="translate", session_id=None, model="claude-haiku-4-5-20251001",
                            max_tokens=10, messages=[{"role": "user", "content": "x"}], max_retries=0)
    assert ei.value.code == "network"


async def test_refusal_is_reported():
    client, _, _ = make_client(
        lambda r: httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                  content=sse_body([""], stop_reason="refusal"))
    )
    with pytest.raises(LLMError) as ei:
        await client.stream(purpose="chat", session_id=None, model="claude-sonnet-5", max_tokens=10,
                            messages=[{"role": "user", "content": "x"}])
    assert ei.value.code == "refusal"


async def test_validate_key():
    ok = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-4-5-20251001",
          "content": [{"type": "text", "text": "pong"}], "stop_reason": "end_turn", "stop_sequence": None,
          "usage": {"input_tokens": 8, "output_tokens": 1}}

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.headers["x-api-key"] == KEY:
            body = json.loads(request.content)
            assert body["max_tokens"] == 5
            return httpx2.Response(200, json=ok)
        return error_response(401, "authentication_error", "invalid x-api-key")

    client, _, _ = make_client(handler)
    usage = await client.validate_key(KEY, "claude-haiku-4-5-20251001")
    assert usage.input_tokens == 8
    with pytest.raises(LLMError) as ei:
        await client.validate_key("sk-ant-api03-wrong-key-000000000000", "claude-haiku-4-5-20251001")
    assert ei.value.code == "auth"


async def test_no_key_raises():
    client = LLMClient(SecretStore())
    assert not client.has_key
    with pytest.raises(LLMError) as ei:
        await client.stream(purpose="chat", session_id=None, model="claude-sonnet-5", max_tokens=10,
                            messages=[{"role": "user", "content": "x"}])
    assert ei.value.code == "no_key"


def test_env_key_overrides_keychain(monkeypatch):
    store = SecretStore()
    store.set(ANTHROPIC_KEY_NAME, KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-env-key-000000000000")
    from app.security import anthropic_key_info

    info = anthropic_key_info(store)
    assert info.source == "env" and info.last4 == "0000"


def test_extract_json_tolerates_fences():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Kết quả: {"a": [1, 2]} xong') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("không có json")
