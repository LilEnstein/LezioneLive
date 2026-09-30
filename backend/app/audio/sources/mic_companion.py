"""Nguồn ``mic_companion``: điện thoại đồng hành gửi PCM 16 kHz qua WSS (IC-FR-02).

``CompanionHub`` gắn nguồn này làm "đích" âm thanh; mỗi khung điện thoại gửi tới được chuyển vào ``feed``.
"""

from __future__ import annotations

import time

from app.audio.sources.base import QueuedSource, SourceHealth


class CompanionSource(QueuedSource):
    kind = "mic_companion"
    # Điện thoại giữ tạm âm thanh khi mất WebSocket rồi gửi bù (tối đa 30 s): đủ chỗ cho đợt gửi dồn.
    max_queue = 600

    def __init__(self) -> None:
        super().__init__()
        self.device_connected = False
        self.latency_ms: float | None = None
        self._disconnected_at: float | None = time.monotonic()

    def set_device(self, connected: bool, name: str | None = None) -> None:
        if connected:
            self.device_connected = True
            self.device_name = name or self.device_name
            self._disconnected_at = None
        else:
            if self.device_connected or self._disconnected_at is None:
                self._disconnected_at = time.monotonic()
            self.device_connected = False

    def disconnected_for(self) -> float:
        """Số giây đã mất kết nối (0 nếu đang kết nối và có âm thanh, hoặc còn khung chờ xử lý)."""
        if self.device_connected and self.receiving:
            return 0.0
        if self._queue is not None and not self._queue.empty():
            return 0.0  # âm thanh đã nhận vẫn đang được xử lý: chưa phải mất nguồn
        if self._disconnected_at is None:
            # Vẫn kết nối nhưng ngừng gửi âm thanh (điện thoại khoá màn hình...).
            return max(0.0, time.monotonic() - self._last_frame) if self._last_frame else 0.0
        return time.monotonic() - self._disconnected_at

    def health(self) -> SourceHealth:
        h = super().health()
        h.connected = self.device_connected and self.receiving
        h.latency_ms = self.latency_ms
        return h
