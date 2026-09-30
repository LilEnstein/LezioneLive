"""Bắt âm thanh bằng thư viện ``soundcard``: âm thanh hệ thống (chế độ Loopback, mục 6.4) và micro của
laptop (nguồn ``mic_local`` của chế độ lớp học, IC-FR-01).

- Windows: WASAPI loopback của loa mặc định, WASAPI tự resample về 16 kHz mono.
- macOS: chọn thiết bị ảo BlackHole (cần tạo Multi-Output Device để vẫn nghe được).
- Linux: thiết bị "Monitor of ..." của PulseAudio/PipeWire.
Chạy trong luồng riêng; mỗi 100 ms gọi ``on_frame(pcm16_bytes)`` (callback phải thread-safe).
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

from app.audio.pcm import FRAME_SAMPLES, SAMPLE_RATE, float32_to_pcm16

log = logging.getLogger(__name__)


def available() -> bool:
    try:
        import soundcard  # noqa: F401
    except Exception:  # noqa: BLE001 - thiếu thư viện hệ thống (PulseAudio...) cũng coi như không có
        return False
    return True


def list_devices() -> list[dict[str, Any]]:
    import soundcard as sc

    try:
        default_name = sc.default_speaker().name
    except Exception:  # noqa: BLE001
        default_name = None
    devices = []
    for mic in sc.all_microphones(include_loopback=True):
        is_loop = bool(getattr(mic, "isloopback", False))
        name = mic.name
        hint = is_loop or "blackhole" in name.lower() or "monitor" in name.lower()
        devices.append(
            {
                "id": str(mic.id),
                "name": name,
                "loopback": is_loop,
                "recommended": bool(hint),
                "default": bool(is_loop and name == default_name),
            }
        )
    devices.sort(key=lambda d: (not d["default"], not d["recommended"], d["name"]))
    return devices


def list_microphones() -> list[dict[str, Any]]:
    """Micro thật (không gồm thiết bị loopback) cho nguồn ``mic_local`` (IC-FR-01)."""
    import soundcard as sc

    try:
        default_id = str(sc.default_microphone().id)
    except Exception:  # noqa: BLE001 - máy không có micro mặc định
        default_id = None
    mics = []
    for mic in sc.all_microphones(include_loopback=False):
        if getattr(mic, "isloopback", False):
            continue
        name = mic.name
        lower = name.lower()
        mics.append(
            {
                "id": str(mic.id),
                "name": name,
                "default": str(mic.id) == default_id,
                # Thiết bị ảo (phần mềm điều khiển từ xa, hội họp...) không phải micro thật.
                "virtual": any(k in lower for k in ("virtual", "cable", "voicemeeter", "blackhole", "stereo mix")),
                "channels": int(getattr(mic, "channels", 1) or 1),
            }
        )
    mics.sort(key=lambda d: (d["virtual"], not d["default"], d["name"]))
    return mics


def _open_microphone(device_id: str | None, loopback: bool = True):
    import soundcard as sc

    if not loopback:
        if device_id:
            return sc.get_microphone(device_id, include_loopback=False)
        return sc.default_microphone()
    if device_id:
        return sc.get_microphone(device_id, include_loopback=True)
    if sys.platform == "darwin":
        for mic in sc.all_microphones(include_loopback=True):
            if "blackhole" in mic.name.lower():
                return mic
        raise RuntimeError("Không thấy thiết bị BlackHole. Hãy cài BlackHole và tạo Multi-Output Device.")
    return sc.get_microphone(str(sc.default_speaker().name), include_loopback=True)


class LoopbackCapture:
    """Luồng thu âm qua ``soundcard``: loopback (mặc định) hoặc micro thật (``loopback=False``)."""

    def __init__(
        self,
        device_id: str | None,
        on_frame: Callable[[bytes], None],
        on_error: Callable[[str], None] | None = None,
        *,
        loopback: bool = True,
    ) -> None:
        self.device_id = device_id
        self.loopback = loopback
        self.device_name: str | None = None
        self._on_frame = on_frame
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        name = "loopback-capture" if self.loopback else "mic-capture"
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _run(self) -> None:
        com = None
        if sys.platform == "win32":
            try:  # khởi tạo COM cho luồng này (WASAPI)
                from soundcard import mediafoundation

                com = mediafoundation._COMLibrary()
            except Exception:  # noqa: BLE001
                com = None
        try:
            mic = _open_microphone(self.device_id, loopback=self.loopback)
            self.device_name = mic.name
            log.info("Bắt đầu %s: %s", "loopback" if self.loopback else "micro", mic.name)
            with mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=FRAME_SAMPLES) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=FRAME_SAMPLES)
                    if data.ndim == 2:
                        data = data.mean(axis=1)
                    self._on_frame(float32_to_pcm16(np.asarray(data, dtype=np.float32)))
        except Exception as e:  # noqa: BLE001
            log.warning("%s lỗi: %s", "Loopback" if self.loopback else "Micro", e)
            if self._on_error is not None:
                self._on_error(str(e))
        finally:
            del com
