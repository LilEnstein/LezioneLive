"""Quản lý các điện thoại đang kết nối: định tuyến âm thanh tới phiên / sound check, chọn phiên hiển thị phụ đề.

- Chỉ một điện thoại được làm micro tại một thời điểm (thiết bị đầu tiên gửi âm thanh ở chế độ micro).
- "Đích" âm thanh (``CompanionSource``) do phiên đang chạy hoặc sound check gắn vào.
- "Phiên mục tiêu" là phiên lớp học đang chạy trên laptop; điện thoại ở chế độ Phụ đề nhận sự kiện của phiên này.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from app.audio.sources.mic_companion import CompanionSource
from app.pipeline.events import EventHub

log = logging.getLogger(__name__)

Mode = Literal["mic", "subtitle", "both"]
MODES = ("mic", "subtitle", "both")


@dataclass
class DeviceConn:
    device_id: int
    name: str
    mode: Mode = "subtitle"
    conn_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    connected_at: float = field(default_factory=time.monotonic)
    rtt_ms: float | None = None
    battery: dict[str, Any] | None = None
    frames: int = 0
    last_audio: float = 0.0
    retarget: asyncio.Event = field(default_factory=asyncio.Event)
    ws: Any = None  # WebSocket (để đóng khi thiết bị bị thu hồi)

    @property
    def wants_mic(self) -> bool:
        return self.mode in ("mic", "both")

    def as_dict(self, active_mic: str | None) -> dict[str, Any]:
        return {
            "conn_id": self.conn_id,
            "device_id": self.device_id,
            "name": self.name,
            "mode": self.mode,
            "active_mic": self.conn_id == active_mic,
            "streaming": time.monotonic() - self.last_audio < 2.0,
            "rtt_ms": None if self.rtt_ms is None else round(self.rtt_ms),
            "battery": self.battery,
            "connected_s": int(time.monotonic() - self.connected_at),
        }


class CompanionHub:
    def __init__(self, events: EventHub) -> None:
        self.events = events
        self.conns: dict[str, DeviceConn] = {}
        self.active_mic: str | None = None
        self.target_session_id: int | None = None
        self._sink: CompanionSource | None = None
        self.sink_owner: str | None = None

    # ------------------------------------------------------------------ sink
    def attach_sink(self, source: CompanionSource, owner: str) -> None:
        self._sink, self.sink_owner = source, owner
        mic = self.mic_conn
        source.set_device(mic is not None, mic.name if mic else None)

    def detach_sink(self, source: CompanionSource) -> None:
        if self._sink is source:
            self._sink, self.sink_owner = None, None

    @property
    def sink(self) -> CompanionSource | None:
        return self._sink

    # --------------------------------------------------------------- target
    def set_target(self, session_id: int) -> None:
        if self.target_session_id == session_id:
            return
        self.target_session_id = session_id
        for conn in self.conns.values():
            conn.retarget.set()

    def clear_target(self, session_id: int) -> None:
        if self.target_session_id != session_id:
            return
        self.target_session_id = None
        for conn in self.conns.values():
            conn.retarget.set()

    # ---------------------------------------------------------- connections
    @property
    def mic_conn(self) -> DeviceConn | None:
        return self.conns.get(self.active_mic) if self.active_mic else None

    def add(self, conn: DeviceConn) -> None:
        self.conns[conn.conn_id] = conn
        self._announce("companion.connected", conn)

    def remove(self, conn: DeviceConn) -> None:
        if self.conns.pop(conn.conn_id, None) is None:
            return
        if self.active_mic == conn.conn_id:
            self._release_mic()
        self._announce("companion.disconnected", conn)

    def kick(self, device_id: int) -> int:
        """Thiết bị bị thu hồi: đóng mọi kết nối đang mở của nó."""
        closed = 0
        for conn in list(self.conns.values()):
            if conn.device_id == device_id and conn.ws is not None:
                asyncio.ensure_future(_close_quietly(conn.ws))
                closed += 1
        return closed

    def set_mode(self, conn: DeviceConn, mode: str) -> None:
        if mode not in MODES:
            return
        conn.mode = mode  # type: ignore[assignment]
        if not conn.wants_mic and self.active_mic == conn.conn_id:
            self._release_mic()
        self._announce("companion.mode", conn)

    def _release_mic(self) -> None:
        self.active_mic = None
        if self._sink is not None:
            self._sink.set_device(False)

    def on_audio(self, conn: DeviceConn, pcm: bytes) -> bool:
        """Âm thanh từ điện thoại. Trả về False nếu bị bỏ qua (không phải micro đang dùng)."""
        if not conn.wants_mic:
            return False
        current = self.mic_conn
        if current is None:
            self.active_mic = conn.conn_id
            current = conn
            if self._sink is not None:
                self._sink.set_device(True, conn.name)
            self._announce("companion.mic", conn)
        if current.conn_id != conn.conn_id:
            return False
        conn.frames += 1
        conn.last_audio = time.monotonic()
        sink = self._sink
        if sink is not None:
            if not sink.device_connected:
                sink.set_device(True, conn.name)
            sink.latency_ms = conn.rtt_ms / 2 if conn.rtt_ms is not None else None
            sink.feed(pcm)
        return True

    # --------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        return {
            "devices": [c.as_dict(self.active_mic) for c in self.conns.values()],
            "active_mic": self.mic_conn.as_dict(self.active_mic) if self.mic_conn else None,
            "target_session_id": self.target_session_id,
            "sink": self.sink_owner,
        }

    def _announce(self, kind: str, conn: DeviceConn) -> None:
        event = {"type": kind, "device": conn.as_dict(self.active_mic)}
        if self.target_session_id is not None:
            self.events.publish(self.target_session_id, event)


async def _close_quietly(ws: Any) -> None:
    try:
        await ws.close(code=4403)
    except Exception:  # noqa: BLE001
        pass
