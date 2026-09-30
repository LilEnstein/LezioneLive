"""Nguồn ``mic_local``: micro của laptop đang chạy backend (tích hợp, USB, tai nghe) — IC-FR-01."""

from __future__ import annotations

import asyncio
import logging
import threading

import numpy as np

from app.audio import loopback as capture
from app.audio.pcm import FRAME_SAMPLES, SAMPLE_RATE
from app.audio.sources.base import QueuedSource, SourceError

log = logging.getLogger(__name__)

MIC_PERMISSION_HINT = (
    "Không mở được micro. Kiểm tra: Windows → Cài đặt → Quyền riêng tư & bảo mật → Micro → cho phép ứng dụng "
    "desktop dùng micro (macOS: Cài đặt hệ thống → Quyền riêng tư → Micro → cho phép Terminal/Python)."
)


def probe_microphone(device_id: str | None, timeout: float = 3.0) -> str:
    """Mở micro và đọc thử 100 ms (IC-E-01: báo lỗi quyền micro trước khi tạo phiên). Trả về tên thiết bị."""
    if not capture.available():
        raise SourceError("Thư viện thu âm (soundcard) không dùng được trên máy này.")
    result: dict[str, object] = {}

    def worker() -> None:
        com = None
        try:
            import sys

            if sys.platform == "win32":
                try:
                    from soundcard import mediafoundation

                    com = mediafoundation._COMLibrary()
                except Exception:  # noqa: BLE001
                    com = None
            mic = capture._open_microphone(device_id, loopback=False)
            with mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=FRAME_SAMPLES) as rec:
                data = rec.record(numframes=FRAME_SAMPLES)
            result["name"] = mic.name
            result["peak"] = float(np.max(np.abs(data))) if np.size(data) else 0.0
        except Exception as e:  # noqa: BLE001
            result["error"] = e
        finally:
            del com

    t = threading.Thread(target=worker, name="mic-probe", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise SourceError("Micro không phản hồi. " + MIC_PERMISSION_HINT)
    if "error" in result:
        log.warning("Không mở được micro %s: %s", device_id, result["error"])
        raise SourceError(f"{MIC_PERMISSION_HINT} (Chi tiết: {result['error']})")
    return str(result.get("name") or "")


class MicLocalSource(QueuedSource):
    kind = "mic_local"

    def __init__(self, device_id: str | None = None) -> None:
        super().__init__()
        self.device_id = device_id
        self._capture: capture.LoopbackCapture | None = None

    async def _open(self) -> None:
        if not capture.available():
            raise SourceError("Thư viện thu âm (soundcard) không dùng được trên máy này.")
        self._capture = capture.LoopbackCapture(
            self.device_id, on_frame=self.feed_threadsafe, on_error=self._on_error, loopback=False
        )
        self._capture.start()

    def _on_error(self, message: str) -> None:  # luồng thu âm
        self.fail_threadsafe(f"{MIC_PERMISSION_HINT} (Chi tiết: {message})")

    async def _close(self) -> None:
        cap, self._capture = self._capture, None
        if cap is not None:
            await asyncio.to_thread(cap.stop)

    def health(self):  # type: ignore[no-untyped-def]
        h = super().health()
        if self._capture is not None and self._capture.device_name:
            h.device = self._capture.device_name
        return h
