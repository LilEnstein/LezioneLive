"""Theo dõi kết nối tới Anthropic API (E-NET-01, NFR-09).

Thăm dò khi đã phát hiện lỗi mạng; khi kết nối trở lại thì báo cho các listener để dịch bù. Trong buổi học
trực tiếp (Wi-Fi trường chập chờn), còn chủ động kiểm tra mỗi 10 giây (feature_spec Flow E).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

HOST = "api.anthropic.com"
PORT = 443


async def tcp_check(host: str = HOST, port: int = PORT, timeout: float = 4.0) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    except (OSError, TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


class ConnectivityMonitor:
    def __init__(
        self,
        check: Callable[[], Awaitable[bool]] = tcp_check,
        interval: float = 5.0,
        watch_interval: float = 10.0,
    ) -> None:
        self.online = True
        self._check = check
        self._interval = interval
        self.watch_interval = watch_interval
        self._task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self._watchers = 0
        self._listeners: list[Callable[[bool], None]] = []

    def add_listener(self, listener: Callable[[bool], None]) -> None:
        self._listeners.append(listener)

    def _notify(self) -> None:
        for listener in self._listeners:
            try:
                listener(self.online)
            except Exception:  # pragma: no cover
                log.exception("Listener kết nối lỗi")

    def mark_offline(self) -> None:
        if self.online:
            self.online = False
            log.warning("Mất kết nối tới Anthropic API, sẽ dịch bù khi có mạng")
            self._notify()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll())

    def mark_online(self) -> None:
        if not self.online:
            self.online = True
            log.info("Đã có kết nối trở lại")
            self._notify()

    async def _poll(self) -> None:
        while not self.online:
            await asyncio.sleep(self._interval)
            if await self._check():
                self.mark_online()

    def watch(self, enabled: bool) -> None:
        """Bật/tắt kiểm tra chủ động (đếm tham chiếu theo số phiên lớp học đang chạy)."""
        self._watchers = max(0, self._watchers + (1 if enabled else -1))
        if self._watchers > 0 and (self._watch_task is None or self._watch_task.done()):
            self._watch_task = asyncio.create_task(self._watch_loop())

    async def _watch_loop(self) -> None:
        while self._watchers > 0:
            await asyncio.sleep(self.watch_interval)
            if self.online and not await self._check():
                self.mark_offline()

    async def close(self) -> None:
        for task in (self._task, self._watch_task):
            if task is not None:
                task.cancel()
