"""Tiền xử lý âm thanh phòng học (IC-FR-10/11, IC-NFR-03, IC-E-05)."""

from __future__ import annotations

import time

import numpy as np
import pytest

from app.audio import preprocess as pp
from app.audio.pcm import read_wav, resample, rms_dbfs

from .conftest import FIXTURES

SR = 16000


def speech() -> np.ndarray:
    audio, sr = read_wav(FIXTURES / "audio" / "speech_en_pauses.wav")
    return resample(audio, sr, SR)


def chunks(x: np.ndarray, n: int = 1600):
    for i in range(0, x.size, n):
        yield x[i : i + n]


def run(stage, x: np.ndarray) -> np.ndarray:
    return np.concatenate([stage.process(c) for c in chunks(x)])


def frame_db(x: np.ndarray) -> np.ndarray:
    return np.array([rms_dbfs(x[i : i + 1600]) for i in range(0, x.size - 1599, 1600)])


def contrast(clean: np.ndarray, y: np.ndarray, delay: int) -> float:
    """Chênh lệch mức giữa khung có lời nói và khung im lặng (đo trên tín hiệu sạch đã biết)."""
    ref = frame_db(clean)
    out = frame_db(y[delay:])
    m = min(ref.size, out.size)
    ref, out = ref[:m], out[:m]
    return float(out[ref > -35].mean() - out[ref < -55].mean())


def mix(clean: np.ndarray, snr_db: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = np.convolve(rng.standard_normal(clean.size), np.ones(8) / 8, mode="same").astype(np.float32)
    gain = 10 ** ((rms_dbfs(clean) - snr_db - rms_dbfs(noise)) / 20)
    return (clean + gain * noise).astype(np.float32)


def test_highpass_removes_rumble_keeps_voice_band():
    t = np.arange(SR * 2) / SR

    def through(freq: float) -> float:
        tone = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        hpf = pp.HighPass()
        return rms_dbfs(run(hpf, tone)[SR // 2 :]) - rms_dbfs(tone)

    assert through(30) < -35  # tiếng ù điều hoà / rung bàn
    assert through(50) < -15  # tiếng ù điện lưới 50 Hz
    assert abs(through(1000)) < 0.5  # dải giọng nói đi qua nguyên vẹn
    assert abs(through(200)) < 0.5
    assert pp.HighPass().delay * 1000 / SR == pytest.approx(16.0)


@pytest.mark.parametrize("strength", ["light", "strong"])
def test_spectral_denoiser_improves_contrast(strength):
    clean = speech()
    noisy = mix(clean, 5)
    den = pp.SpectralDenoiser(strength)
    y = run(den, noisy)
    before = contrast(clean, noisy, 0)
    after = contrast(clean, y, den.delay)
    assert after > before + 5.0
    # bảo toàn số mẫu: chỉ còn phần trễ + chưa đủ một bước STFT nằm trong bộ đệm
    assert 0 <= noisy.size - y.size <= den.delay + den.HOP


def test_agc_normalises_speech_level():
    clean = speech() * 0.05  # micro xa: lời nói rất nhỏ (~ -48 dBFS)
    agc = pp.AutoGain()
    y = run(agc, clean)
    tail = y[SR * 10 :]  # sau khi AGC hội tụ
    voiced = frame_db(tail)
    level = 10 * np.log10(np.mean(10 ** (voiced[voiced > voiced.max() - 20] / 10)))
    assert -26.0 < level < -14.0
    assert np.max(np.abs(y)) <= 1.0


def test_preprocessor_chain_is_fast_and_streams():
    clean = speech()
    noisy = mix(clean, 10)
    for level in ("off", "light", "strong"):
        p = pp.Preprocessor(level)  # type: ignore[arg-type]
        started = time.perf_counter()
        y = run(p, noisy)
        elapsed = time.perf_counter() - started
        assert elapsed < noisy.size / SR * 0.5  # nhanh hơn thời gian thực nhiều lần
        assert p.processing_ms < (50.0 if level != "strong" else 200.0)  # IC-NFR-03
        assert abs(y.size - noisy.size) <= int(p.delay_ms * SR / 1000) + 512
        assert p.last_hpf_db < 0
        if level != "off":
            assert contrast(clean, y, int(round(p.delay_ms * SR / 1000))) > contrast(clean, noisy, 0) + 5
        p.close()


def test_battery_saver_prefers_cheap_engine():
    p = pp.Preprocessor("light", prefer_rnnoise=False)
    assert p.engine == "spectral_light"
    tail = p.set_level("strong", prefer_rnnoise=False)
    assert tail.size > 0 and p.engine == "spectral_strong"


@pytest.mark.skipif(not pp.rnnoise_available(), reason="Chưa cài pyrnnoise (RNNoise)")
def test_rnnoise_engine_when_available():
    clean = speech()
    noisy = mix(clean, 5)
    p = pp.Preprocessor("light", agc=False)
    assert p.engine == "rnnoise"
    y = run(p, noisy)
    assert contrast(clean, y, int(round(p.delay_ms * SR / 1000))) > contrast(clean, noisy, 0) + 10
    p.close()


def test_strong_over_budget_triggers_downgrade_flag(monkeypatch):
    p = pp.Preprocessor("strong")
    p._ema_ms = pp.STRONG_BUDGET_MS + 1
    assert p.over_budget()
    p.set_level("light")
    assert not p.over_budget()
