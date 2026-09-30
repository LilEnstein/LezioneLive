"""Các nguồn âm thanh: tab (WebSocket), loopback, micro laptop, điện thoại đồng hành."""

from __future__ import annotations

from app.audio.sources.base import AudioSource, QueuedSource, SourceError, SourceHealth, SourceKind
from app.audio.sources.loopback import LoopbackSource
from app.audio.sources.mic_companion import CompanionSource
from app.audio.sources.mic_local import MicLocalSource
from app.audio.sources.tab import TabSource

__all__ = [
    "AudioSource",
    "CompanionSource",
    "LoopbackSource",
    "MicLocalSource",
    "QueuedSource",
    "SourceError",
    "SourceHealth",
    "SourceKind",
    "TabSource",
    "make_source",
]


def make_source(kind: str, *, device_id: str | None = None) -> QueuedSource:
    if kind == "loopback":
        return LoopbackSource(device_id)
    if kind == "mic_local":
        return MicLocalSource(device_id)
    if kind == "mic_companion":
        return CompanionSource()
    return TabSource()
