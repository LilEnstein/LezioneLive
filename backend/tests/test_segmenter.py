"""Segmenter: cắt câu theo khoảng lặng, tối thiểu 1,5 s / tối đa 12 s (FR-STT-01)."""

from __future__ import annotations

import numpy as np
import pytest

from app.audio.pcm import read_wav, resample
from app.audio.segmenter import Segmenter
from app.audio.vad import EnergyVAD, SileroVAD, default_silero_path

from .conftest import FIXTURES, silence, tone


def run(seg: Segmenter, audio: np.ndarray, frame: int = 1600):
    events = []
    for i in range(0, len(audio), frame):
        events.extend(seg.push(audio[i : i + frame]))
    events.extend(seg.flush())
    return events


def utterances(events):
    return [e.utterance for e in events if e.kind == "utterance"]


def test_splits_on_silence_and_keeps_timestamps():
    audio = np.concatenate([silence(1.0), tone(2.0), silence(1.5), tone(3.0), silence(1.5)])
    seg = Segmenter(EnergyVAD(), min_ms=1500, max_ms=12000)
    utts = utterances(run(seg, audio))
    assert len(utts) == 2
    first, second = utts
    # pre-roll ~200 ms (làm tròn theo cửa sổ 32 ms) trước khi bắt đầu nói
    assert 740 <= first.t_start_ms <= 1000
    assert 2900 <= first.t_end_ms <= 3300
    assert 4200 <= second.t_start_ms <= 4500
    assert all(u.duration_ms >= 1500 for u in utts)
    assert first.audio.shape[0] == pytest.approx((first.t_end_ms - first.t_start_ms) * 16, abs=16)


def test_emits_speech_start_and_end_events():
    audio = np.concatenate([silence(0.5), tone(2.0), silence(1.2)])
    events = run(Segmenter(EnergyVAD()), audio)
    kinds = [e.kind for e in events]
    assert kinds == ["speech_start", "speech_end", "utterance"]


def test_short_utterance_merges_with_following_speech():
    # "Sì." (0.6 s) rồi nghỉ 0.6 s rồi câu tiếp → gộp thành 1 segment ≥ 1.5 s
    audio = np.concatenate([silence(0.5), tone(0.6), silence(0.6), tone(1.5), silence(1.5)])
    utts = utterances(run(Segmenter(EnergyVAD(), min_ms=1500), audio))
    assert len(utts) == 1
    assert utts[0].duration_ms >= 2500


def test_short_isolated_utterance_is_not_lost():
    audio = np.concatenate([silence(0.5), tone(0.6), silence(3.0)])
    utts = utterances(run(Segmenter(EnergyVAD(), min_ms=1500, max_pending_silence_ms=1200), audio))
    assert len(utts) == 1
    assert utts[0].duration_ms < 1500


def test_click_is_discarded():
    audio = np.concatenate([silence(0.5), tone(0.1), silence(3.0)])
    utts = utterances(run(Segmenter(EnergyVAD(), min_speech_ms=250), audio))
    assert utts == []


def test_long_speech_is_force_cut_at_max_length():
    # 30 s nói liên tục có vài chỗ nhỏ giọng → cắt ≤ 12 s tại điểm "yên" nhất, không mất audio
    parts = []
    for _ in range(6):
        parts += [tone(4.5, amp=0.3), tone(0.2, amp=0.004)]
    audio = np.concatenate([silence(0.5), *parts, silence(1.5)])
    utts = utterances(run(Segmenter(EnergyVAD(), max_ms=12000), audio))
    assert len(utts) >= 3
    assert all(u.duration_ms <= 12000 for u in utts)
    forced = [u for u in utts if u.forced]
    assert forced, "phải có ít nhất một lần cắt cưỡng bức"
    # các đoạn nối tiếp nhau liên tục
    for a, b in zip(utts, utts[1:], strict=False):
        assert b.t_start_ms >= a.t_end_ms - 1
    total = sum(u.duration_ms for u in utts)
    assert total >= 27000


def test_time_offset_and_reset_keep_clock():
    seg = Segmenter(EnergyVAD(), start_offset_ms=60_000)
    events = run(seg, np.concatenate([silence(0.5), tone(2.0), silence(1.0)]))
    utt = utterances(events)[0]
    assert utt.t_start_ms >= 60_000
    t_before = seg.t_ms
    seg.reset()
    assert seg.t_ms == t_before


@pytest.mark.skipif(default_silero_path() is None, reason="Không có model Silero (faster-whisper)")
def test_silero_vad_streaming_on_real_speech():
    audio, sr = read_wav(FIXTURES / "audio" / "speech_en_pauses.wav")
    audio = resample(audio, sr, 16000)
    seg = Segmenter(SileroVAD(), min_ms=1500, max_ms=12000)
    utts = utterances(run(seg, audio))
    # 4 câu, câu cuối ~14 s bị cắt đôi; "Yes." ngắn vẫn được giữ.
    assert 4 <= len(utts) <= 7
    assert all(u.duration_ms <= 12000 for u in utts)
    assert any(u.duration_ms < 1500 for u in utts), "câu ngắn 'Yes.' không được mất"
    assert utts[0].t_start_ms >= 1000  # 1,5 s im lặng đầu file


@pytest.mark.skipif(default_silero_path() is None, reason="Không có model Silero (faster-whisper)")
def test_silero_returns_probabilities():
    vad = SileroVAD()
    probs = [vad(np.zeros(512, dtype=np.float32)) for _ in range(10)]
    assert all(0.0 <= p <= 1.0 for p in probs)
    assert max(probs) < 0.5
    with pytest.raises(ValueError):
        vad(np.zeros(100, dtype=np.float32))
