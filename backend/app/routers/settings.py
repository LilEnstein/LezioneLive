"""/health, /settings, /settings/api-key (mục 13.2), STT và thiết bị âm thanh."""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from app.audio import loopback
from app.audio.pcm import SAMPLE_RATE, pcm16_to_float32
from app.audio.preprocess import available_engines
from app.config import (
    ASSISTANT_MODEL_CHOICES,
    CLAUDE_CODE_MODEL_CHOICES,
    DEFAULT_PRICING,
    LOCAL_MODEL_CHOICES,
    TRANSLATE_MODEL_CHOICES,
    VERSION,
    WHISPER_MODEL_CHOICES,
)
from app.context import AppContext
from app.llm.client import LLMError
from app.routers.deps import api_error, get_ctx
from app.schemas import ApiKeyIn, SttKeyIn
from app.security import (
    ANTHROPIC_KEY_NAME,
    STT_KEY_NAMES,
    SecretStoreError,
    anthropic_key_info,
    last4,
    looks_like_anthropic_key,
)
from app.services.export_md import pdf_engine
from app.stt.base import STTError

router = APIRouter()

_caps_cache: tuple[float, dict[str, Any]] | None = None


def _capabilities(ctx: AppContext) -> dict[str, Any]:
    global _caps_cache
    now = time.monotonic()
    if _caps_cache is None or now - _caps_cache[0] > 60:
        engine = pdf_engine(ctx.env.chrome_path)
        _caps_cache = (
            now,
            {
                "loopback": loopback.available(),
                "microphone": loopback.available(),
                "pdf_export": engine is not None,
                "pdf_engine": engine,
                "keyring_backend": ctx.secrets.backend_name(),
                "platform": sys.platform,
                "denoise": available_engines(),
            },
        )
    return {
        **_caps_cache[1],
        "speaker_model": ctx.speaker_models.cached(),
        "draft_model": ctx.draft.status()["state"],
        "companion_running": ctx.companion_server.running,
    }


def _process_info() -> dict[str, Any]:
    """RAM tiến trình backend (theo dõi NFR-03 khi chạy soak)."""
    try:
        import psutil

        proc = psutil.Process()
        return {"rss_mb": round(proc.memory_info().rss / 1_048_576, 1), "threads": proc.num_threads()}
    except Exception:  # noqa: BLE001
        return {}


def _key_info(ctx: AppContext) -> dict[str, Any]:
    info = anthropic_key_info(ctx.secrets)
    return {"present": info.present, "last4": info.last4, "source": info.source}


@router.get("/health")
def health(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    settings = ctx.settings.get()
    return {
        "status": "ok",
        "version": VERSION,
        "stt": {**ctx.stt.status(), "model_cached": ctx.stt.model_cached()},
        "gpu": ctx.stt.gpu(),
        "api_key": _key_info(ctx),
        "network": {"online": ctx.connectivity.online},
        "translation": {
            "slow": ctx.translation.slow,
            "auth_failed": ctx.translation.auth_failed,
            "ready": ctx.translator.ready,
            "provider": settings.translate_provider,
            "local_model": ctx.draft.status(),
        },
        "assistant": {
            "ready": ctx.assistant_llm.has_key,
            "provider": settings.assistant_provider,
            "model": settings.assistant_cc_model
            if settings.assistant_provider == "claude_code"
            else settings.assistant_model,
            "effort": settings.assistant_effort,
        },
        "capabilities": _capabilities(ctx),
        "onboarding_done": settings.onboarding_done,
        "process": _process_info(),
    }


@router.get("/settings")
def get_settings(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    settings = ctx.settings.get()
    return {
        "settings": settings.model_dump(),
        "api_key": _key_info(ctx),
        "stt_keys": {p: bool(ctx.secrets.get(name)) for p, name in STT_KEY_NAMES.items()},
        "choices": {
            "translate_models": TRANSLATE_MODEL_CHOICES,
            "claude_code_models": CLAUDE_CODE_MODEL_CHOICES,
            "assistant_models": ASSISTANT_MODEL_CHOICES,
            "whisper_models": WHISPER_MODEL_CHOICES,
            "local_models": LOCAL_MODEL_CHOICES,
        },
        "default_pricing": {k: v.model_dump() for k, v in DEFAULT_PRICING.items()},
    }


@router.put("/settings")
async def put_settings(request: Request, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        patch = await request.json()
    except ValueError as e:
        raise api_error(400, "BAD_JSON", "Body phải là JSON") from e
    if not isinstance(patch, dict):
        raise api_error(400, "BAD_JSON", "Body phải là object JSON")
    try:
        ctx.settings.update(patch)
    except ValidationError as e:
        first = e.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", []))
        raise api_error(422, "INVALID_SETTINGS", f"Giá trị không hợp lệ: {field} ({first.get('msg')})") from e
    except ValueError as e:
        raise api_error(422, "INVALID_SETTINGS", str(e)) from e
    return get_settings(ctx)


@router.put("/settings/api-key")
async def put_api_key(body: ApiKeyIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    key = body.api_key.strip()
    if not looks_like_anthropic_key(key):
        raise api_error(400, "INVALID_API_KEY", "API key không hợp lệ (phải bắt đầu bằng sk-ant-).")
    model = ctx.settings.get().translate_model
    try:
        usage = await ctx.llm.validate_key(key, model)
    except LLMError as e:
        if e.code == "auth":
            raise api_error(400, "INVALID_API_KEY", "API key không hợp lệ") from e
        if e.code == "network":
            raise api_error(502, "NETWORK", "Không kết nối được tới Anthropic API để xác thực key.") from e
        if e.code in ("rate_limit", "overloaded", "server"):
            raise api_error(503, "API_BUSY", "Anthropic API đang bận, thử lại sau ít phút.") from e
        raise api_error(400, "VALIDATION_FAILED", e.message) from e
    try:
        ctx.secrets.set(ANTHROPIC_KEY_NAME, key)
    except SecretStoreError as e:
        raise api_error(500, "KEYCHAIN_ERROR", str(e)) from e
    ctx.usage.record(session_id=None, purpose="validate", model=model, usage=usage)
    ctx.llm.reload_key()
    ctx.translation.on_api_key_changed()
    info = _key_info(ctx)
    return {"valid": True, "last4": last4(key), "source": info["source"]}


@router.delete("/settings/api-key")
async def delete_api_key(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        ctx.secrets.delete(ANTHROPIC_KEY_NAME)
    except SecretStoreError as e:
        raise api_error(500, "KEYCHAIN_ERROR", str(e)) from e
    ctx.llm.reload_key()
    return {"deleted": True, "api_key": _key_info(ctx)}


@router.get("/settings/claude-code")
async def claude_code_status(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Dịch bằng gói Pro: Claude Code đã cài và đăng nhập chưa."""
    cc = ctx.translator.claude_code
    if cc is None:
        return {"installed": False, "logged_in": False}
    status = await asyncio.to_thread(cc.auth_status)
    if status.get("logged_in") and ctx.settings.get().translate_provider == "claude_code":
        ctx.translation.retry_auth_failures()  # vừa đăng nhập: dịch lại các câu lỗi "chưa đăng nhập"
    return status


@router.post("/settings/claude-code/test")
async def claude_code_test(request: Request, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Nút "Thử model": gọi thử model/effort qua Claude Code, báo model thật và thời gian phản hồi."""
    cc = ctx.translator.claude_code
    if cc is None or not cc.available:
        raise api_error(400, "NO_CLAUDE_CODE", "Không tìm thấy Claude Code trên máy.")
    body = await request.json()
    settings = ctx.settings.get()
    model = str(body.get("model") or settings.assistant_cc_model)
    effort = body.get("effort", settings.assistant_effort)
    try:
        return await cc.ping(model, None if effort in (None, "auto") else str(effort))
    except LLMError as e:
        return {"ok": False, "model": model, "error": e.message}


@router.put("/settings/stt-key")
async def put_stt_key(body: SttKeyIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        ctx.secrets.set(STT_KEY_NAMES[body.provider], body.api_key.strip())
    except SecretStoreError as e:
        raise api_error(500, "KEYCHAIN_ERROR", str(e)) from e
    if ctx.settings.get().stt_provider == body.provider:
        ctx.stt.start_prepare()
    return {"saved": True, "provider": body.provider, "last4": last4(body.api_key.strip())}


@router.delete("/settings/stt-key/{provider}")
def delete_stt_key(provider: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    name = STT_KEY_NAMES.get(provider)
    if name is None:
        raise api_error(404, "UNKNOWN_PROVIDER", "Provider không hợp lệ")
    try:
        ctx.secrets.delete(name)
    except SecretStoreError as e:
        raise api_error(500, "KEYCHAIN_ERROR", str(e)) from e
    return {"deleted": True}


@router.get("/stt/status")
def stt_status(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return {**ctx.stt.status(), "model_cached": ctx.stt.model_cached(), "gpu": ctx.stt.gpu()}


@router.post("/stt/prepare")
async def stt_prepare(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    ctx.stt.start_prepare()
    return ctx.stt.status()


@router.post("/stt/test")
async def stt_test(request: Request, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Onboarding: gửi ≤ 30 s PCM Int16 16 kHz mono (application/octet-stream), trả về text tiếng Ý."""
    body = await request.body()
    if not body:
        raise api_error(400, "EMPTY_AUDIO", "Không có dữ liệu âm thanh")
    if len(body) > SAMPLE_RATE * 2 * 30:
        body = body[: SAMPLE_RATE * 2 * 30]
    if not ctx.stt.ready:
        ok = await ctx.stt.prepare()
        if not ok:
            raise api_error(409, "STT_NOT_READY", ctx.stt.error or "Nhận dạng giọng nói chưa sẵn sàng")
    audio = pcm16_to_float32(body)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    started = time.perf_counter()
    try:
        result = await ctx.stt.provider.transcribe(audio, language="it")
    except STTError as e:
        raise api_error(502, "STT_ERROR", str(e)) from e
    return {
        "text": result.text,
        "confidence": round(result.confidence, 3),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "audio_ms": int(audio.size * 1000 / SAMPLE_RATE),
        "peak": round(peak, 3),
    }


@router.get("/audio/devices")
def audio_devices() -> dict[str, Any]:
    """Thiết bị loopback (âm thanh hệ thống) và micro của laptop (nguồn ``mic_local``, IC-FR-01)."""
    if not loopback.available():
        return {
            "available": False,
            "devices": [],
            "microphones": [],
            "error": "Thư viện soundcard không dùng được trên máy này.",
        }
    out: dict[str, Any] = {"available": True, "devices": [], "microphones": []}
    try:
        out["devices"] = loopback.list_devices()
        out["microphones"] = loopback.list_microphones()
    except Exception as e:  # noqa: BLE001
        out.update(available=False, error=str(e))
    return out
