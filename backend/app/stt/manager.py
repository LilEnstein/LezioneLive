"""Quản lý vòng đời STT provider theo cài đặt (tạo, nạp model, báo tiến trình cho /health)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from app.config import BATTERY_SAVER_WHISPER_MODEL, AppSettings
from app.security import STT_KEY_NAMES, SecretStore
from app.services.settings_store import SettingsStore
from app.stt.azure import AzureSpeechProvider
from app.stt.base import STTError, STTProvider
from app.stt.deepgram import DeepgramProvider
from app.stt.whisper_local import WhisperLocalProvider, gpu_info, is_cached

log = logging.getLogger(__name__)

_RELEVANT = (
    "stt_provider",
    "whisper_model",
    "whisper_device",
    "whisper_compute_type",
    "deepgram_model",
    "azure_region",
)


def _signature(s: AppSettings) -> tuple:
    return tuple(getattr(s, k) for k in _RELEVANT)


class STTManager:
    def __init__(self, settings: SettingsStore, secrets: SecretStore) -> None:
        self._settings = settings
        self._secrets = secrets
        self._provider: STTProvider | None = None
        self._sig: tuple | None = None
        self._task: asyncio.Task | None = None
        self.state = "idle"  # idle | loading | ready | error
        self.progress = 0.0
        self.message = ""
        self.error: str | None = None
        self.on_warning: Callable[[str, str], None] | None = None
        self._gpu: dict[str, Any] | None = None
        self._saver_sessions: set[int] = set()
        settings.add_listener(self._on_settings)

    # --------------------------------------------------------- tiết kiệm pin
    @property
    def power_saver(self) -> bool:
        """IC-FR-70: có phiên bật tiết kiệm pin → Whisper ``small`` int8 trên CPU (chỉ với Whisper cục bộ)."""
        return bool(self._saver_sessions) and self._settings.get().stt_provider == "whisper_local"

    def set_power_saver(self, session_id: int, enabled: bool) -> str | None:
        """Bật/tắt cho một phiên. Trả về cảnh báo nếu không đổi được model (chưa tải model small)."""
        s = self._settings.get()
        if enabled:
            if s.stt_provider != "whisper_local":
                return None
            if s.whisper_model != BATTERY_SAVER_WHISPER_MODEL and not is_cached(BATTERY_SAVER_WHISPER_MODEL):
                return (
                    f"Chưa có model Whisper {BATTERY_SAVER_WHISPER_MODEL} trên máy nên vẫn dùng {s.whisper_model}. "
                    f"Tải model {BATTERY_SAVER_WHISPER_MODEL} trong Cài đặt (khi có mạng) để tiết kiệm pin hơn."
                )
            self._saver_sessions.add(session_id)
        else:
            self._saver_sessions.discard(session_id)
        return None

    # ------------------------------------------------------------ providers
    def _key(self, provider: str) -> Callable[[], str | None]:
        return lambda: self._secrets.get(STT_KEY_NAMES[provider])

    def _signature(self) -> tuple:
        return (*_signature(self._settings.get()), self.power_saver)

    def _build(self, s: AppSettings) -> STTProvider:
        if s.stt_provider == "deepgram":
            return DeepgramProvider(self._key("deepgram"), model=s.deepgram_model)
        if s.stt_provider == "azure":
            return AzureSpeechProvider(self._key("azure"), region=s.azure_region)
        if self.power_saver:
            return WhisperLocalProvider(
                model_name=BATTERY_SAVER_WHISPER_MODEL, device="cpu", compute_type="int8", on_warning=self._warn
            )
        return WhisperLocalProvider(
            model_name=s.whisper_model,
            device=s.whisper_device,
            compute_type=s.whisper_compute_type,
            on_warning=self._warn,
        )

    def _warn(self, code: str, message: str) -> None:
        if self.on_warning is not None:
            self.on_warning(code, message)

    @property
    def provider(self) -> STTProvider:
        s = self._settings.get()
        sig = self._signature()
        if self._provider is None or sig != self._sig:
            old = self._provider
            self._provider = self._build(s)
            self._sig = sig
            self.state, self.progress, self.message, self.error = "idle", 0.0, "", None
            if old is not None:
                _spawn(old.close())
        return self._provider

    @property
    def ready(self) -> bool:
        return self.provider.ready

    def gpu(self) -> dict[str, Any]:
        if self._gpu is None:
            self._gpu = gpu_info()
        return self._gpu

    # -------------------------------------------------------------- prepare
    def _on_progress(self, frac: float, message: str) -> None:
        self.progress = frac
        self.message = message

    def start_prepare(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.prepare())
        return self._task

    async def prepare(self) -> bool:
        provider = self.provider
        if provider.ready:
            self.state, self.progress = "ready", 1.0
            return True
        self.state, self.error, self.progress = "loading", None, 0.0
        self.message = "Đang chuẩn bị nhận dạng giọng nói…"
        try:
            await provider.prepare(self._on_progress)
        except STTError as e:
            self.state, self.error = "error", str(e)
            log.warning("Chuẩn bị STT thất bại: %s", e)
            return False
        except Exception as e:  # noqa: BLE001
            self.state, self.error = "error", f"{type(e).__name__}: {e}"
            log.exception("Chuẩn bị STT lỗi")
            return False
        if provider is not self._provider:  # cài đặt đổi trong lúc nạp
            return False
        self.state, self.progress, self.message = "ready", 1.0, "Sẵn sàng"
        return True

    def autoload(self) -> None:
        """Khi khởi động: nạp sẵn nếu model đã có trong máy (không tự tải hàng GB)."""
        s = self._settings.get()
        if s.stt_provider != "whisper_local" or is_cached(s.whisper_model):
            self.start_prepare()

    def model_cached(self, model: str | None = None) -> bool | None:
        s = self._settings.get()
        if s.stt_provider != "whisper_local":
            return None
        return is_cached(model or s.whisper_model)

    def status(self) -> dict[str, Any]:
        provider = self.provider
        ready = provider.ready
        if ready and self.state != "ready":
            self.state, self.progress = "ready", 1.0
        return {
            **provider.info(),
            "ready": ready,
            "state": self.state,
            "progress": round(self.progress, 3),
            "message": self.message,
            "error": self.error,
        }

    def _on_settings(self, old: AppSettings, new: AppSettings) -> None:
        if _signature(old) != _signature(new):
            was_ready = self._provider is not None and self._provider.ready
            _ = self.provider  # tạo provider mới
            # Chỉ tự nạp khi không phải tải hàng GB (model đã có sẵn) hoặc là STT cloud.
            if new.stt_provider != "whisper_local" or (was_ready and is_cached(new.whisper_model)):
                try:
                    self.start_prepare()
                except RuntimeError:  # không có event loop (test đồng bộ)
                    pass

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self._provider is not None:
            await self._provider.close()


def _spawn(coro) -> None:
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
