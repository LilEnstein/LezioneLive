"""Sound check 30 giây và SNR thời gian thực (IC-FR-05, IC-FR-20..23, IC-AC-02)."""

from __future__ import annotations

import asyncio

import numpy as np

from app.audio.pcm import float32_to_pcm16, read_wav, resample, rms_dbfs
from app.audio.soundcheck import SnrTracker, SoundCheck, evaluate, rate, recommend
from app.audio.sources.base import QueuedSource

from .conftest import FIXTURES

SR = 16000


def speech(seconds: float = 20.0) -> np.ndarray:
    audio, sr = read_wav(FIXTURES / "audio" / "speech_en_pauses.wav")
    x = resample(audio, sr, SR)
    return x[: int(seconds * SR)]


def noise(seconds: float, level_dbfs: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = np.convolve(rng.standard_normal(int(seconds * SR)), np.ones(6) / 6, mode="same")
    return (n * 10 ** ((level_dbfs - rms_dbfs(n)) / 20)).astype(np.float32)


def test_quiet_room_close_speaker_is_good():
    """IC-AC-02: phòng yên tĩnh, người nói cách 2 m → "Tốt"."""
    background = noise(10, -65)
    talk = speech(20) + noise(20, -65, seed=1)[: speech(20).size]
    result = evaluate(background, talk)
    assert result.rating == "good"
    assert result.snr_db is not None and result.snr_db >= 15
    assert result.noise_floor_dbfs < -60
    assert result.recommended["recommended_denoise"] == "light"


def test_noisy_room_is_poor_with_tips():
    """IC-AC-02: tiếng quạt mạnh, SNR < 10 dB → "Kém" và ≥ 2 gợi ý cụ thể."""
    fan = -30.0
    background = noise(10, fan)
    talk_clean = speech(20) * 0.3  # giảng viên ở xa
    talk = talk_clean + noise(20, fan, seed=2)[: talk_clean.size]
    result = evaluate(background, talk)
    assert result.rating == "poor"
    codes = [t["code"] for t in result.tips]
    assert len(codes) >= 2
    assert "strong_denoise" in codes and "phone_front" in codes
    assert result.recommended["recommended_denoise"] == "strong"
    assert result.recommended["vad_threshold"] >= 0.6


def test_no_speech_in_step_two():
    result = evaluate(noise(10, -55), noise(20, -55, seed=3))
    assert result.rating == "poor"
    assert result.speech_level_dbfs is None
    assert result.tips[0]["code"] == "no_speech"


def test_rating_boundaries_and_recommendation():
    assert rate(None) == "poor" and rate(9.9) == "poor"
    assert rate(10) == "fair" and rate(14.9) == "fair" and rate(15) == "good"
    rec = recommend(-50.0, -25.0, 25.0)
    assert rec == {"vad_threshold": 0.55, "recommended_denoise": "light", "min_level_dbfs": -40.0}


def test_snr_tracker_uses_vad_labels():
    tr = SnrTracker()
    t = 0
    for i in range(200):
        speaking = (i // 10) % 2 == 0
        tr.add(t, -25.0 if speaking else -50.0, speaking)
        t += 100
    assert tr.noise_floor() == -50.0
    assert abs(tr.speech_level() - -25.0) < 0.01
    assert abs(tr.snr() - 25.0) < 0.01
    assert tr.level_between(t - 1000, t) is not None
    tr.reset()
    assert tr.snr() is None


class ListSource(QueuedSource):
    """Nguồn giả: phát sẵn danh sách khung khi start (nhanh hơn thời gian thực)."""

    kind = "mic_local"
    max_queue = 100_000

    def __init__(self, audio: np.ndarray) -> None:
        super().__init__()
        pcm = float32_to_pcm16(audio)
        self._frames = [pcm[i : i + 3200] for i in range(0, len(pcm), 3200)]

    async def _open(self) -> None:
        for f in self._frames:
            self.feed(f)


async def test_soundcheck_runs_on_source_and_reports_progress():
    audio = np.concatenate([noise(10, -60), speech(20) + noise(20, -60, seed=4)[: speech(20).size]])
    done = []
    check = SoundCheck(ListSource(audio), on_done=lambda c: done.append(c.id))
    check.start()
    await check.wait(timeout=30)
    data = check.as_dict()
    assert data["state"] == "done" and data["progress"] == 1.0
    assert data["result"]["rating"] in ("good", "fair")
    assert data["result"]["rating_label"] in ("Tốt", "Tạm được")
    assert done == [check.id]


async def test_soundcheck_fails_when_no_audio(monkeypatch):
    from app.audio import soundcheck as sc

    monkeypatch.setattr(sc, "NO_AUDIO_TIMEOUT_S", 0.2)
    check = SoundCheck(ListSource(np.zeros(0, dtype=np.float32)))
    check.start()
    await asyncio.wait_for(check._task, timeout=5)  # type: ignore[arg-type]
    assert check.state == "failed"
    assert "Không nhận được âm thanh" in (check.error or "")
