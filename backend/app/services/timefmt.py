"""Định dạng mốc thời gian ``[mm:ss]`` (phút có thể > 59, ví dụ ``[83:05]``)."""

from __future__ import annotations

import re

TS_PATTERN = re.compile(r"\[(\d{1,3}):([0-5]\d)\]")


def fmt_ts(ms: int) -> str:
    total = max(0, int(ms) // 1000)
    return f"{total // 60:02d}:{total % 60:02d}"


def fmt_duration(ms: int) -> str:
    total = max(0, int(ms) // 1000)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def parse_ts(text: str) -> list[int]:
    """Trả về danh sách mốc (ms) xuất hiện trong văn bản."""
    return [(int(m.group(1)) * 60 + int(m.group(2))) * 1000 for m in TS_PATTERN.finditer(text)]
