"""Wrapper quanh ``anthropic.AsyncAnthropic``: quản lý key, tham số theo model, phân loại lỗi, đếm token.

Lưu ý SDK 1.x: ``temperature`` không còn là tham số của ``messages.create/stream``; model nào còn nhận
(ví dụ Haiku 4.5) thì truyền qua ``extra_body``. Sonnet 5 / Opus 5 trở lên từ chối tham số sampling.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import anthropic
from anthropic import AsyncAnthropic

from app.security import SecretStore, resolve_anthropic_key

if TYPE_CHECKING:
    from app.services.cost import UsageRecorder

log = logging.getLogger(__name__)

Route = Literal["fast", "deep"]


# ---------------------------------------------------------------------------
# Khả năng của từng dòng model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelCaps:
    sampling: bool  # còn nhận temperature
    adaptive: bool  # hỗ trợ thinking {type: adaptive}
    thinking_default_on: bool  # bỏ trống thinking thì model vẫn suy nghĩ
    can_disable_thinking: bool  # chấp nhận thinking {type: disabled}
    effort: bool  # hỗ trợ output_config.effort


_CAPS: list[tuple[str, ModelCaps]] = [
    ("claude-fable-5", ModelCaps(False, True, True, False, True)),
    ("claude-mythos-5", ModelCaps(False, True, True, False, True)),
    ("claude-opus-5-5", ModelCaps(False, True, True, False, True)),
    ("claude-opus-5", ModelCaps(False, True, True, True, True)),
    ("claude-sonnet-5", ModelCaps(False, True, True, True, True)),
    ("claude-opus-4-8", ModelCaps(False, True, False, True, True)),
    ("claude-opus-4-7", ModelCaps(False, True, False, True, True)),
    ("claude-opus-4-6", ModelCaps(True, True, False, True, True)),
    ("claude-sonnet-4-6", ModelCaps(True, True, False, True, True)),
    ("claude-haiku-4-5", ModelCaps(True, False, False, True, False)),
]
_UNKNOWN = ModelCaps(sampling=False, adaptive=False, thinking_default_on=False, can_disable_thinking=False, effort=False)


def model_caps(model: str) -> ModelCaps:
    m = model.lower()
    best: tuple[int, ModelCaps] | None = None
    for prefix, caps in _CAPS:
        if m.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), caps)
    return best[1] if best else _UNKNOWN


def build_model_params(model: str, route: Route, temperature: float | None = None) -> dict[str, Any]:
    """Tham số phụ thuộc model.

    - ``fast``: tác vụ cần độ trễ thấp (dịch, chat trực tiếp) -> tắt thinking nếu model cho phép,
      nếu không thì hạ effort xuống ``low``.
    - ``deep``: tác vụ cần chất lượng (tóm tắt, flashcard) -> adaptive thinking, effort ``medium``.
    """
    caps = model_caps(model)
    params: dict[str, Any] = {}
    output_config: dict[str, Any] = {}
    if route == "fast":
        if caps.thinking_default_on:
            if caps.can_disable_thinking:
                params["thinking"] = {"type": "disabled"}
            elif caps.effort:
                output_config["effort"] = "low"
    else:
        if caps.adaptive:
            params["thinking"] = {"type": "adaptive"}
        if caps.effort:
            output_config["effort"] = "medium"
    if output_config:
        params["output_config"] = output_config
    if temperature is not None and caps.sampling:
        params["extra_body"] = {"temperature": temperature}
    return params


# ---------------------------------------------------------------------------
# Lỗi & usage
# ---------------------------------------------------------------------------

ErrorCode = Literal[
    "no_key", "auth", "rate_limit", "overloaded", "server", "network", "context_too_long", "bad_request", "refusal",
    "unknown",
]

RETRYABLE_CODES = {"rate_limit", "overloaded", "server"}


class LLMError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES


_TOO_LONG = re.compile(r"prompt is too long|too many tokens|context (window|length)|exceeds? .*(context|maximum)", re.I)


_NO_CREDIT = re.compile(r"credit balance is too low|purchase credits", re.I)


def _retry_after(e: anthropic.APIStatusError) -> float | None:
    try:
        value = e.response.headers.get("retry-after")
        return float(value) if value else None
    except (ValueError, AttributeError):
        return None


def classify_error(e: BaseException) -> LLMError:
    """Chuyển exception của SDK thành LLMError (thứ tự: cụ thể trước, tổng quát sau)."""
    if isinstance(e, LLMError):
        return e
    if isinstance(e, anthropic.AuthenticationError):
        return LLMError("auth", "API key không hợp lệ hoặc đã bị thu hồi.", status=401)
    if isinstance(e, anthropic.PermissionDeniedError):
        return LLMError("auth", "API key không có quyền dùng model này.", status=403)
    if isinstance(e, anthropic.RateLimitError):
        return LLMError("rate_limit", "Bị giới hạn tốc độ (429).", status=429, retry_after=_retry_after(e))
    if isinstance(e, anthropic.OverloadedError):
        return LLMError("overloaded", "Máy chủ Claude đang quá tải (529).", status=529)
    if isinstance(e, anthropic.RequestTooLargeError):
        return LLMError("context_too_long", "Yêu cầu quá lớn.", status=413)
    if isinstance(e, anthropic.BadRequestError):
        msg = str(getattr(e, "message", e))
        if _TOO_LONG.search(msg):
            return LLMError("context_too_long", "Ngữ cảnh quá dài.", status=400)
        if _NO_CREDIT.search(msg):
            # Key đúng nhưng hết credit: xử lý như lỗi key để chuyển sang dịch nháp, không thử lại từng câu.
            return LLMError(
                "auth", "Tài khoản Anthropic API hết credit — nạp thêm ở Plans & Billing (platform.claude.com).", status=400
            )
        return LLMError("bad_request", f"Yêu cầu không hợp lệ: {msg}", status=400)
    if isinstance(e, anthropic.NotFoundError):
        return LLMError("bad_request", "Model hoặc tài nguyên không tồn tại (404). Kiểm tra tên model.", status=404)
    if isinstance(e, anthropic.APITimeoutError):
        return LLMError("network", "Hết thời gian chờ phản hồi từ Anthropic API.")
    if isinstance(e, anthropic.APIConnectionError):
        return LLMError("network", "Không kết nối được tới Anthropic API.")
    if isinstance(e, anthropic.APIStatusError):
        if e.status_code == 529:
            return LLMError("overloaded", "Máy chủ Claude đang quá tải (529).", status=529)
        if e.status_code >= 500:
            return LLMError("server", f"Lỗi máy chủ Anthropic ({e.status_code}).", status=e.status_code)
        return LLMError("bad_request", f"Lỗi API ({e.status_code}).", status=e.status_code)
    return LLMError("unknown", f"Lỗi không xác định: {type(e).__name__}")


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cache_write_1h_tokens: int = 0

    @classmethod
    def from_api(cls, usage: Any) -> TokenUsage:
        if usage is None:
            return cls()
        cw1h = 0
        cc = getattr(usage, "cache_creation", None)
        if cc is not None:
            cw1h = int(getattr(cc, "ephemeral_1h_input_tokens", 0) or 0)
        return cls(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            cache_write_1h_tokens=cw1h,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


@dataclass
class LLMResult:
    text: str
    model: str
    stop_reason: str | None
    usage: TokenUsage = field(default_factory=TokenUsage)
    message_id: str | None = None


def extract_json(text: str) -> Any:
    """Parse JSON từ phản hồi (chịu được ```json fences``` hoặc chữ thừa hai đầu)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("Phản hồi không chứa JSON")
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start : end + 1])


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class LLMClient:
    """Một client dùng chung cho cả app. Key được đọc từ keychain / biến môi trường."""

    def __init__(
        self,
        secrets: SecretStore,
        usage: UsageRecorder | None = None,
        client_factory: Callable[..., AsyncAnthropic] | None = None,
    ) -> None:
        self._secrets = secrets
        self.usage = usage
        # Cho phép test thay HTTP transport; mặc định là AsyncAnthropic thật.
        self._factory = client_factory or AsyncAnthropic
        self._key: str | None = None
        self._client: AsyncAnthropic | None = None
        self.reload_key()

    # -- key -----------------------------------------------------------------
    def reload_key(self) -> None:
        key, _source = resolve_anthropic_key(self._secrets)
        if key == self._key and (self._client is not None or key is None):
            return
        self._key = key
        self._client = self._factory(api_key=key, max_retries=2) if key else None

    @property
    def has_key(self) -> bool:
        return self._key is not None

    def _require(self) -> AsyncAnthropic:
        if self._client is None:
            raise LLMError("no_key", "Chưa cấu hình API key Claude. Vào Cài đặt để nhập key.")
        return self._client

    async def validate_key(self, key: str, model: str) -> TokenUsage:
        """Gọi thử Messages API (max_tokens=5) để xác thực key (mục 13.2 bước 2)."""
        client = self._factory(api_key=key, max_retries=1, timeout=30.0)
        try:
            msg = await client.messages.create(
                model=model,
                max_tokens=5,
                messages=[{"role": "user", "content": "ping"}],
            )
            return TokenUsage.from_api(msg.usage)
        except Exception as e:  # noqa: BLE001 - phân loại bên dưới
            raise classify_error(e) from e
        finally:
            await client.close()

    # -- messages --------------------------------------------------------------
    def _params(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str | list[dict[str, Any]] | None,
        messages: list[dict[str, Any]],
        route: Route,
        temperature: float | None,
        output_format: dict[str, Any] | None,
        cache_control: dict[str, Any] | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            params["system"] = system
        params.update(build_model_params(model, route, temperature))
        if output_format is not None:
            params.setdefault("output_config", {})["format"] = output_format
        if cache_control is not None:
            params["cache_control"] = cache_control
        return params

    async def stream(
        self,
        *,
        purpose: str,
        session_id: int | None,
        model: str,
        max_tokens: int,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] | None = None,
        route: Route = "fast",
        temperature: float | None = None,
        output_format: dict[str, Any] | None = None,
        cache_control: dict[str, Any] | None = None,
        on_text: Callable[[str], None] | None = None,
        max_retries: int | None = None,
        timeout: float | None = None,
    ) -> LLMResult:
        """Gọi Messages API dạng stream; ``on_text`` nhận từng mẩu text. Ghi usage sau khi xong."""
        client = self._require()
        if max_retries is not None or timeout is not None:
            opts: dict[str, Any] = {}
            if max_retries is not None:
                opts["max_retries"] = max_retries
            if timeout is not None:
                opts["timeout"] = timeout
            client = client.with_options(**opts)
        params = self._params(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            route=route,
            temperature=temperature,
            output_format=output_format,
            cache_control=cache_control,
        )
        try:
            async with client.messages.stream(**params) as stream:
                async for text in stream.text_stream:
                    if on_text is not None and text:
                        on_text(text)
                final = await stream.get_final_message()
        except Exception as e:  # noqa: BLE001
            err = classify_error(e)
            if err.code == "unknown":
                log.exception("Lỗi không xác định khi gọi Claude (%s)", purpose)
            raise err from e

        usage = TokenUsage.from_api(final.usage)
        used_model = getattr(final, "model", None) or model
        if self.usage is not None:
            self.usage.record(session_id=session_id, purpose=purpose, model=used_model, usage=usage)
        text = "".join(block.text for block in final.content if getattr(block, "type", None) == "text")
        if final.stop_reason == "refusal":
            raise LLMError("refusal", "Claude từ chối yêu cầu này.")
        return LLMResult(text=text, model=used_model, stop_reason=final.stop_reason, usage=usage, message_id=final.id)

    async def json(
        self,
        *,
        purpose: str,
        session_id: int | None,
        model: str,
        max_tokens: int,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        system: str | list[dict[str, Any]] | None = None,
        route: Route = "fast",
        on_text: Callable[[str], None] | None = None,
    ) -> tuple[Any, LLMResult]:
        """Structured output (``output_config.format``); nếu model không hỗ trợ thì fallback parse tự do."""
        fmt = {"type": "json_schema", "schema": schema}
        try:
            result = await self.stream(
                purpose=purpose,
                session_id=session_id,
                model=model,
                max_tokens=max_tokens,
                messages=messages,
                system=system,
                route=route,
                output_format=fmt,
                on_text=on_text,
            )
        except LLMError as e:
            if e.code != "bad_request" or not re.search(r"output_config|format|schema", e.message, re.I):
                raise
            log.warning("Model %s không nhận structured output, chuyển sang parse JSON tự do", model)
            hint = "\n\nChỉ trả về JSON hợp lệ đúng schema sau, không kèm chữ nào khác:\n" + json.dumps(
                schema, ensure_ascii=False
            )
            patched = [*messages[:-1], _append_text(messages[-1], hint)]
            result = await self.stream(
                purpose=purpose,
                session_id=session_id,
                model=model,
                max_tokens=max_tokens,
                messages=patched,
                system=system,
                route=route,
                on_text=on_text,
            )
        if result.stop_reason == "max_tokens":
            raise LLMError("bad_request", "Phản hồi JSON bị cắt do vượt max_tokens.")
        try:
            return extract_json(result.text), result
        except (ValueError, json.JSONDecodeError) as e:
            raise LLMError("bad_request", "Claude trả về JSON không hợp lệ.") from e

    async def count_tokens(
        self, *, model: str, messages: list[dict[str, Any]], system: str | list[dict[str, Any]] | None = None
    ) -> int:
        client = self._require()
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if system:
            kwargs["system"] = system
        try:
            resp = await client.messages.count_tokens(**kwargs)
        except Exception as e:  # noqa: BLE001
            raise classify_error(e) from e
        return int(resp.input_tokens)

    # -- Files API ------------------------------------------------------------
    async def upload_file(self, filename: str, data: bytes, mime: str) -> str:
        client = self._require()
        try:
            uploaded = await client.files.upload(file=(filename, data, mime))
        except Exception as e:  # noqa: BLE001
            raise classify_error(e) from e
        return uploaded.id

    async def delete_file(self, file_id: str) -> None:
        client = self._require()
        try:
            await client.files.delete(file_id)
        except Exception as e:  # noqa: BLE001
            raise classify_error(e) from e


def _append_text(message: dict[str, Any], extra: str) -> dict[str, Any]:
    content = message["content"]
    if isinstance(content, str):
        return {**message, "content": content + extra}
    return {**message, "content": [*content, {"type": "text", "text": extra}]}
