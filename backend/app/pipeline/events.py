"""Pub/sub sự kiện theo phiên, đẩy tới các kết nối WebSocket (mục 9.2 Server → Client).

Mọi ``publish`` phải gọi từ thread của event loop (luồng loopback dùng ``call_soon_threadsafe``).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

log = logging.getLogger(__name__)

Event = dict[str, Any]


class EventHub:
    def __init__(self, queue_size: int = 5000) -> None:
        self._subs: dict[int, set[asyncio.Queue[Event]]] = defaultdict(set)
        self._queue_size = queue_size

    def subscribe(self, session_id: int) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_size)
        self._subs[session_id].add(queue)
        return queue

    def unsubscribe(self, session_id: int, queue: asyncio.Queue[Event]) -> None:
        subs = self._subs.get(session_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                self._subs.pop(session_id, None)

    def subscriber_count(self, session_id: int) -> int:
        return len(self._subs.get(session_id, ()))

    def publish(self, session_id: int, event: Event) -> None:
        for queue in list(self._subs.get(session_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("Hàng đợi sự kiện của phiên %s đầy, bỏ sự kiện %s", session_id, event.get("type"))

    def publish_all(self, event: Event) -> None:
        for session_id in list(self._subs):
            self.publish(session_id, event)
