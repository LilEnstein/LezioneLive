"""Nguồn ``loopback``: âm thanh hệ thống do backend bắt (Teams desktop, mục 6.4 app_spec)."""

from __future__ import annotations

import asyncio

from app.audio import loopback as capture
from app.audio.sources.base import QueuedSource, SourceError


class LoopbackSource(QueuedSource):
    kind = "loopback"

    def __init__(self, device_id: str | None = None) -> None:
        super().__init__()
        self.device_id = device_id
        self._capture: capture.LoopbackCapture | None = None

    async def _open(self) -> None:
        if not capture.available():
            raise SourceError("Không bắt được âm thanh hệ thống trên máy này.")
        self._capture = capture.LoopbackCapture(
            self.device_id, on_frame=self.feed_threadsafe, on_error=self.fail_threadsafe, loopback=True
        )
        self._capture.start()

    async def _close(self) -> None:
        cap, self._capture = self._capture, None
        if cap is not None:
            await asyncio.to_thread(cap.stop)

    def health(self):  # type: ignore[no-untyped-def]
        h = super().health()
        if self._capture is not None and self._capture.device_name:
            h.device = self._capture.device_name
        return h
