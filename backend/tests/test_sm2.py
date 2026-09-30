"""Lịch ôn SM-2 (FR-LE-02)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.llm.flashcards import find_example, sm2

NOW = datetime(2026, 9, 28, 10, 0, 0)


def test_successful_reviews_grow_interval():
    ease, interval, reps = 2.5, 0, 0
    intervals = []
    for _ in range(4):
        r = sm2(ease, interval, reps, 4, NOW)
        ease, interval, reps = r.ease, r.interval_days, r.repetitions
        intervals.append(interval)
    assert intervals[:2] == [1, 6]
    assert intervals[2] == round(6 * 2.5)
    assert intervals[3] > intervals[2]
    assert ease == pytest.approx(2.5)


def test_perfect_grade_increases_ease_and_bad_grade_resets():
    r = sm2(2.5, 6, 2, 5, NOW)
    assert r.ease == pytest.approx(2.6)
    assert r.due_at == NOW + timedelta(days=15)
    fail = sm2(r.ease, r.interval_days, r.repetitions, 1, NOW)
    assert fail.repetitions == 0 and fail.interval_days == 0
    assert fail.due_at == NOW + timedelta(minutes=10)
    assert fail.ease < r.ease


def test_ease_never_below_floor():
    ease = 1.35
    for _ in range(5):
        ease = sm2(ease, 1, 1, 0, NOW).ease
    assert ease == pytest.approx(1.3)


def test_grade_out_of_range():
    with pytest.raises(ValueError):
        sm2(2.5, 0, 0, 6, NOW)


class _Seg:
    def __init__(self, text, vi=None):
        self.source_text = text
        self.text_vi = vi


def test_find_example_prefers_transcript_sentence():
    segs = [_Seg("Oggi parliamo di varianza."), _Seg("Le variabili aleatorie sono indipendenti.", "Các biến…")]
    assert find_example("la variabile", "frase inventata", segs) is segs[1]
    assert find_example("la varianza", "Oggi parliamo di varianza", segs) is segs[0]
    assert find_example("il gatto", "", segs) is None
