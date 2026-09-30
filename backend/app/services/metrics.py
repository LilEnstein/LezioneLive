"""Đo độ trễ từng stage (NFR-01/02, mục 17 "Hiệu năng") và xuất báo cáo p50/p95 + CSV."""

from __future__ import annotations

import csv
import io
import time
from typing import Any

import numpy as np
from sqlalchemy import Engine
from sqlmodel import select

from app.db.models import Segment, SegmentMetric
from app.db.session import db_session


def percentiles(values: list[int]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "max": None}
    arr = np.asarray(values, dtype=np.float64)
    return {
        "p50": round(float(np.percentile(arr, 50)), 1),
        "p95": round(float(np.percentile(arr, 95)), 1),
        "max": round(float(arr.max()), 1),
    }


class MetricsStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._speech_end: dict[int, float] = {}

    def on_transcribed(
        self, *, segment_id: int, session_id: int, speech_end_wall: float, stt_ms: int, audio_ms: int
    ) -> None:
        it_latency = int((time.monotonic() - speech_end_wall) * 1000)
        self._speech_end[segment_id] = speech_end_wall
        with db_session(self._engine) as s:
            s.merge(
                SegmentMetric(
                    segment_id=segment_id,
                    session_id=session_id,
                    audio_ms=audio_ms,
                    stt_ms=stt_ms,
                    it_latency_ms=it_latency,
                )
            )
            s.commit()

    def on_translated(self, segment_id: int) -> None:
        start = self._speech_end.pop(segment_id, None)
        if start is None:  # dịch lại / sửa tay: không phải độ trễ trực tiếp
            return
        vi_latency = int((time.monotonic() - start) * 1000)
        with db_session(self._engine) as s:
            row = s.get(SegmentMetric, segment_id)
            if row is not None:
                row.vi_latency_ms = vi_latency
                s.add(row)
                s.commit()

    def rows(self, session_id: int) -> list[tuple[SegmentMetric, int]]:
        with db_session(self._engine) as s:
            return list(
                s.exec(
                    select(SegmentMetric, Segment.seq)
                    .join(Segment, Segment.id == SegmentMetric.segment_id)
                    .where(SegmentMetric.session_id == session_id)
                    .order_by(Segment.seq)
                ).all()
            )

    def summary(self, session_id: int) -> dict[str, Any]:
        rows = [r for r, _ in self.rows(session_id)]
        return {
            "segments": len(rows),
            "stt_ms": percentiles([r.stt_ms for r in rows if r.stt_ms is not None]),
            "it_latency_ms": percentiles([r.it_latency_ms for r in rows if r.it_latency_ms is not None]),
            "vi_latency_ms": percentiles([r.vi_latency_ms for r in rows if r.vi_latency_ms is not None]),
            "targets": {"it_p50": 2500, "it_p95": 4000, "vi_p50": 5000, "vi_p95": 8000},
        }

    def csv(self, session_id: int) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["seq", "segment_id", "audio_ms", "stt_ms", "it_latency_ms", "vi_latency_ms"])
        for row, seq in self.rows(session_id):
            w.writerow([seq, row.segment_id, row.audio_ms, row.stt_ms, row.it_latency_ms, row.vi_latency_ms])
        return buf.getvalue()
