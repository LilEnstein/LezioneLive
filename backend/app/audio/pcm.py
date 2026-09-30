"""Tiện ích PCM: Int16 LE 16 kHz mono ↔ float32, mức âm, WAV."""

from __future__ import annotations

import io
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1600  # 100 ms


def pcm16_to_float32(data: bytes) -> np.ndarray:
    if len(data) % 2:
        data = data[:-1]
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def rms_dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return max(-120.0, 20.0 * np.log10(rms + 1e-12))


def to_wav_bytes(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(float32_to_pcm16(samples))
    return buf.getvalue()


def read_wav(path_or_bytes: Path | bytes) -> tuple[np.ndarray, int]:
    """Đọc WAV PCM 16-bit (mono hoặc stereo -> mono)."""
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else open(path_or_bytes, "rb")  # noqa: SIM115
    with src, wave.open(src, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("Chỉ hỗ trợ WAV PCM 16-bit")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, sr


def resample(samples: np.ndarray, sr_in: int, sr_out: int = SAMPLE_RATE) -> np.ndarray:
    """Resample đơn giản (lọc trung bình + nội suy tuyến tính), đủ tốt cho giọng nói."""
    if sr_in == sr_out or samples.size == 0:
        return samples.astype(np.float32)
    if sr_in > sr_out:
        factor = sr_in / sr_out
        k = max(1, int(round(factor)))
        if k > 1:
            kernel = np.ones(k, dtype=np.float32) / k
            samples = np.convolve(samples, kernel, mode="same")
    n_out = int(round(samples.size * sr_out / sr_in))
    x_old = np.linspace(0.0, 1.0, num=samples.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


class WavAppender:
    """Ghi dần PCM vào file WAV (tuỳ chọn lưu âm thanh gốc, FR-AC-06, mặc định tắt)."""

    def __init__(self, path: Path, sample_rate: int = SAMPLE_RATE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._w = wave.open(str(path), "wb")  # noqa: SIM115
        self._w.setnchannels(1)
        self._w.setsampwidth(2)
        self._w.setframerate(sample_rate)

    def write(self, pcm16: bytes) -> None:
        self._w.writeframesraw(pcm16)

    def close(self) -> None:
        try:
            self._w.close()
        except Exception:  # pragma: no cover
            pass
