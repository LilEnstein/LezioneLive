"""Interface chung ``AudioSource`` (feature_spec mục 6.2): pipeline không phụ thuộc nguồn âm thanh.

Mọi nguồn trả về khung PCM Int16 mono 16 kHz (thường 100 ms) qua ``frames()``. ``QueuedSource`` là lớp nền:
nguồn "đẩy" khung vào hàng đợi asyncio (từ WebSocket, từ luồng thu âm qua ``feed_threadsafe``...).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import numpy as np

from app.audio.pcm import SAMPLE_RATE, rms_dbfs

SourceKind = Literal["tab", "loopback", "mic_local", "mic_companion"]

STALE_AFTER_S = 2.0
MAX_QUEUED_FRAMES = 100  # 10 s; quá thì bỏ khung cũ nhất (xử lý bị kẹt)


class SourceError(RuntimeError):
    """Không mở được / mất nguồn âm thanh (thiếu quyền micro, rút thiết bị...)."""


@dataclass
class SourceHealth:
    source: str
    connected: bool
    level_dbfs: float | None = None
    snr_db: float | None = None
    device: str | None = None
    latency_ms: float | None = None
    error: str | None = None

    def as_event(self) -> dict[str, Any]:
        return {
            "type": "source.health",
            "source": self.source,
            "connected": self.connected,
            "level_dbfs": None if self.level_dbfs is None else round(self.level_dbfs, 1),
            "snr_db": None if self.snr_db is None else round(self.snr_db, 1),
            "device": self.device,
            "latency_ms": None if self.latency_ms is None else round(self.latency_ms),
            "error": self.error,
        }


class AudioSource(Protocol):
    kind: SourceKind
    sample_rate: int  # luôn 16000 sau khi chuẩn hoá

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def frames(self) -> AsyncIterator[np.ndarray]: ...  # khung int16, 100 ms

    def health(self) -> SourceHealth: ...


def to_int16(pcm: bytes | np.ndarray) -> np.ndarray:
    if isinstance(pcm, bytes | bytearray | memoryview):
        data = bytes(pcm)
        if len(data) % 2:
            data = data[:-1]
        return np.frombuffer(data, dtype="<i2").copy()
    arr = np.asarray(pcm)
    if arr.dtype == np.int16:
        return arr
    return (np.clip(arr, -1.0, 1.0) * 32767.0).astype(np.int16)


class QueuedSource:
    """Nguồn đẩy khung vào hàng đợi; ``frames()`` lấy ra theo thứ tự. Lớp con cài ``_open``/``_close``."""

    kind: SourceKind = "tab"
    sample_rate = SAMPLE_RATE
    max_queue = MAX_QUEUED_FRAMES

    def __init__(self) -> None:
        self._queue: asyncio.Queue[np.ndarray | None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._last_frame = 0.0
        self._level_db: float | None = None
        self.device_name: str | None = None
        self.error: str | None = None
        self.dropped = 0

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self.error = None
        self._running = True
        try:
            await self._open()
        except SourceError:
            self._running = False
            raise
        except Exception as e:  # noqa: BLE001
            self._running = False
            raise SourceError(str(e)) from e

    async def stop(self) -> None:
        was_running, self._running = self._running, False
        try:
            await self._close()  # cả khi nguồn đã tự dừng vì lỗi: vẫn dọn luồng thu âm
        finally:
            if was_running and self._queue is not None:
                self._queue.put_nowait(None)

    async def _open(self) -> None:  # pragma: no cover - lớp con cài đặt
        return None

    async def _close(self) -> None:  # pragma: no cover
        return None

    @property
    def running(self) -> bool:
        return self._running

    # ---------------------------------------------------------------- frames
    def feed(self, pcm: bytes | np.ndarray) -> None:
        """Nhận một khung PCM (gọi từ luồng của event loop)."""
        if not self._running or self._queue is None:
            return
        frame = to_int16(pcm)
        if frame.size == 0:
            return
        self._last_frame = time.monotonic()
        self._level_db = rms_dbfs(frame.astype(np.float32) / 32768.0)
        if self._queue.qsize() >= self.max_queue:
            try:
                self._queue.get_nowait()
                self.dropped += 1
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(frame)

    def drain(self) -> int:
        """Bỏ các khung đang chờ trong hàng đợi (giữ nguồn chạy)."""
        n = 0
        while self._queue is not None and not self._queue.empty():
            item = self._queue.get_nowait()
            if item is None:  # giữ tín hiệu kết thúc
                self._queue.put_nowait(None)
                break
            n += 1
        return n

    def feed_threadsafe(self, pcm: bytes | np.ndarray) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self.feed, pcm)

    def fail_threadsafe(self, message: str) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._fail, message)

    def _fail(self, message: str) -> None:
        self.error = message
        if self._queue is not None:
            self._queue.put_nowait(None)
        self._running = False

    async def frames(self) -> AsyncIterator[np.ndarray]:
        queue = self._queue
        if queue is None:
            return
        while True:
            item = await queue.get()
            if item is None:
                return
            yield item

    # ---------------------------------------------------------------- health
    @property
    def receiving(self) -> bool:
        return self._running and time.monotonic() - self._last_frame < STALE_AFTER_S

    def health(self) -> SourceHealth:
        return SourceHealth(
            source=self.kind,
            connected=self.receiving,
            level_dbfs=self._level_db if self.receiving else None,
            device=self.device_name,
            error=self.error,
        )
