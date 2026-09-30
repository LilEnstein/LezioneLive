"""Pin laptop (IC-FR-71/72, IC-E-07): mức pin, cắm sạc, ước tính thời gian còn lại theo mức tiêu thụ 10 phút gần nhất."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

LOW_PERCENT = 20.0
CRITICAL_PERCENT = 10.0
WINDOW_S = 600.0
MIN_SPAN_S = 120.0


@dataclass
class BatteryReading:
    percent: float
    plugged: bool
    secs_left: float | None  # ước tính của hệ điều hành (nếu có)


def read_os_battery() -> BatteryReading | None:
    try:
        import psutil

        b = psutil.sensors_battery()
    except Exception:  # noqa: BLE001
        return None
    if b is None:
        return None
    secs = b.secsleft if isinstance(b.secsleft, int | float) and b.secsleft > 0 else None
    return BatteryReading(float(b.percent), bool(b.power_plugged), float(secs) if secs else None)


class BatteryMonitor:
    def __init__(
        self, reader: Callable[[], BatteryReading | None] = read_os_battery, max_age_s: float = 15.0
    ) -> None:
        self._reader = reader
        self.max_age_s = max_age_s
        self._samples: deque[tuple[float, float]] = deque()
        self._last: BatteryReading | None = None
        self._last_read = float("-inf")

    def read(self) -> BatteryReading | None:
        now = time.monotonic()
        if self._last is None or now - self._last_read >= self.max_age_s:
            self._last = self._reader()
            self._last_read = now
            if self._last is not None:
                if self._last.plugged:
                    self._samples.clear()
                else:
                    self._samples.append((now, self._last.percent))
                    while self._samples and now - self._samples[0][0] > WINDOW_S:
                        self._samples.popleft()
        return self._last

    def minutes_left(self) -> float | None:
        """Theo tốc độ tụt pin trong 10 phút gần nhất; chưa đủ dữ liệu thì dùng ước tính của hệ điều hành."""
        reading = self._last
        if reading is None or reading.plugged:
            return None
        if len(self._samples) >= 2:
            (t0, p0), (t1, p1) = self._samples[0], self._samples[-1]
            if t1 - t0 >= MIN_SPAN_S and p0 > p1:
                rate_per_min = (p0 - p1) / ((t1 - t0) / 60)
                return round(reading.percent / rate_per_min, 0)
        return round(reading.secs_left / 60, 0) if reading.secs_left else None

    def status(self) -> dict[str, Any] | None:
        reading = self.read()
        if reading is None:
            return None
        return {
            "percent": round(reading.percent, 0),
            "plugged": reading.plugged,
            "minutes_left": self.minutes_left(),
            "low": not reading.plugged and reading.percent < LOW_PERCENT,
            "critical": not reading.plugged and reading.percent < CRITICAL_PERCENT,
        }
