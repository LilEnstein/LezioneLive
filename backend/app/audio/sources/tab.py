"""Nguồn ``tab``: âm thanh tab trình duyệt (getDisplayMedia) do frontend gửi qua WebSocket /ws/session."""

from __future__ import annotations

from app.audio.sources.base import QueuedSource


class TabSource(QueuedSource):
    kind = "tab"
    # WebSocket có thể dồn nhiều khung một lúc (kết nối lại gửi bù, script soak phát nhanh): không bỏ khung.
    max_queue = 6000

    def __init__(self) -> None:
        super().__init__()
        self.device_name = "Tab trình duyệt"
