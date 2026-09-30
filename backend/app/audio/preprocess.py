"""Tiền xử lý âm thanh phòng học trước VAD (IC-FR-10/11, Flow C): lọc thông cao 80 Hz → khử nhiễu → AGC.

- Lọc thông cao: FIR pha tuyến tính (sinc cửa sổ Hamming, 513 hệ số), trễ 16 ms.
- Khử nhiễu ``light``: RNNoise (DLL đi kèm gói ``pyrnnoise``, gọi qua ctypes, không cần các thư viện phụ
  thuộc khác của gói) chạy ở 48 kHz; nếu máy chưa có RNNoise thì dùng bộ lọc Wiener phổ tích hợp.
- Khử nhiễu ``strong``: RNNoise + hậu lọc Wiener phổ mạnh (hoặc chỉ Wiener phổ nếu thiếu RNNoise).
- AGC: đưa mức lời nói về khoảng -20 dBFS, tăng tối đa +30 dB, có giới hạn đỉnh mềm.

Mọi tầng nhận khối float32 16 kHz độ dài tuỳ ý và giữ trạng thái giữa các khối (xử lý luồng). Độ dài đầu ra
mỗi lần gọi có thể khác đầu vào do đệm nội bộ, nhưng tổng số mẫu được bảo toàn (trừ phần trễ cố định).
"""

from __future__ import annotations

import ctypes
import importlib.util
import logging
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Literal, Protocol

import numpy as np

from app.audio.pcm import SAMPLE_RATE

log = logging.getLogger(__name__)

DenoiseLevel = Literal["off", "light", "strong"]

HPF_CUTOFF_HZ = 80.0
HPF_TAPS = 513
AGC_TARGET_DBFS = -20.0
# IC-NFR-03 / IC-E-05: xử lý chậm hơn mức này (ms cho mỗi 100 ms âm thanh) thì không theo kịp thời gian thực
STRONG_BUDGET_MS = 80.0


class Stage(Protocol):
    delay: int  # độ trễ thuật toán (mẫu 16 kHz)

    def process(self, x: np.ndarray) -> np.ndarray: ...

    def reset(self) -> None: ...


# ---------------------------------------------------------------------------
# FIR có trạng thái
# ---------------------------------------------------------------------------


def lowpass_taps(cutoff: float, sr: float, taps: int) -> np.ndarray:
    n = np.arange(taps) - (taps - 1) / 2
    fc = cutoff / sr
    h = 2 * fc * np.sinc(2 * fc * n) * np.hamming(taps)
    return (h / h.sum()).astype(np.float64)


def highpass_taps(cutoff: float = HPF_CUTOFF_HZ, sr: float = SAMPLE_RATE, taps: int = HPF_TAPS) -> np.ndarray:
    """Đảo phổ của bộ thông thấp: δ[n - M/2] - h_lp[n]."""
    h = -lowpass_taps(cutoff, sr, taps)
    h[(taps - 1) // 2] += 1.0
    return h


class FirFilter:
    def __init__(self, taps: np.ndarray) -> None:
        self.h = np.asarray(taps, dtype=np.float64)
        self.delay = (len(self.h) - 1) // 2
        self.reset()

    def reset(self) -> None:
        self._hist = np.zeros(len(self.h) - 1, dtype=np.float64)

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return x.astype(np.float32)
        buf = np.concatenate([self._hist, x.astype(np.float64, copy=False)])
        y = np.convolve(buf, self.h, mode="valid")
        self._hist = buf[-(len(self.h) - 1) :]
        return y.astype(np.float32)


class HighPass(FirFilter):
    def __init__(self, cutoff: float = HPF_CUTOFF_HZ) -> None:
        super().__init__(highpass_taps(cutoff))


# ---------------------------------------------------------------------------
# Khử nhiễu phổ (Wiener + ước lượng nhiễu MCRA)
# ---------------------------------------------------------------------------


class SpectralDenoiser:
    """Bộ lọc Wiener "decision-directed" trên STFT 32 ms, nhiễu ước lượng theo MCRA (cực tiểu có trượt).

    ``light``: sàn khuếch đại -12 dB. ``strong``: trừ nhiễu mạnh hơn (x1,8), sàn -20 dB.
    """

    N = 512
    HOP = 256

    def __init__(self, strength: Literal["light", "strong"] = "light") -> None:
        self.strength = strength
        self.floor = 0.25 if strength == "light" else 0.1
        self.over = 1.0 if strength == "light" else 1.8
        self.win = np.sqrt(np.hanning(self.N + 1)[:-1]).astype(np.float64)  # sqrt-Hann tuần hoàn: COLA 50%
        self.delay = self.N - self.HOP
        self.reset()

    def reset(self) -> None:
        bins = self.N // 2 + 1
        self._in = np.zeros(self.N - self.HOP, dtype=np.float64)
        self._ola = np.zeros(self.N, dtype=np.float64)
        self._frames = 0
        self._S = np.zeros(bins)
        self._Smin = np.full(bins, np.inf)
        self._Stmp = np.full(bins, np.inf)
        self._p = np.zeros(bins)
        self._noise = np.zeros(bins)
        self._gain_prev = np.ones(bins)
        self._gamma_prev = np.ones(bins)

    def _update_noise(self, P: np.ndarray) -> None:
        eps = 1e-12
        if self._frames == 0:
            self._S = P.copy()
            self._noise = P.copy()
            self._Smin = P.copy()
            self._Stmp = P.copy()
            return
        self._S = 0.8 * self._S + 0.2 * P
        self._Smin = np.minimum(self._Smin, self._S)
        self._Stmp = np.minimum(self._Stmp, self._S)
        if self._frames % 94 == 0:  # cửa sổ tìm cực tiểu ~1,5 s
            self._Smin = np.minimum(self._Stmp, self._S)
            self._Stmp = self._S.copy()
        speech = (self._S / (self._Smin + eps)) > 5.0
        self._p = 0.2 * self._p + 0.8 * speech
        ad = 0.95 + 0.05 * self._p
        if self._frames < 20:  # ~0,3 s đầu: coi như nhiễu để hội tụ nhanh
            ad = np.full_like(ad, 0.7)
        self._noise = ad * self._noise + (1 - ad) * P

    def _frame(self, frame: np.ndarray) -> np.ndarray:
        X = np.fft.rfft(frame * self.win)
        P = X.real**2 + X.imag**2
        self._update_noise(P)
        self._frames += 1
        noise = self.over * self._noise + 1e-10
        gamma = np.minimum(P / noise, 1e4)
        xi = 0.98 * (self._gain_prev**2) * self._gamma_prev + 0.02 * np.maximum(gamma - 1.0, 0.0)
        gain = xi / (1.0 + xi)
        # Làm mượt nhẹ theo tần số để giảm "musical noise".
        gain = np.convolve(gain, np.array([0.25, 0.5, 0.25]), mode="same")
        gain = np.maximum(gain, self.floor)
        self._gain_prev, self._gamma_prev = gain, gamma
        return np.fft.irfft(X * gain, n=self.N) * self.win

    def process(self, x: np.ndarray) -> np.ndarray:
        buf = np.concatenate([self._in, x.astype(np.float64, copy=False)])
        out: list[np.ndarray] = []
        pos = 0
        while pos + self.N <= buf.size:
            self._ola += self._frame(buf[pos : pos + self.N])
            out.append(self._ola[: self.HOP].copy())
            self._ola = np.concatenate([self._ola[self.HOP :], np.zeros(self.HOP)])
            pos += self.HOP
        self._in = buf[pos:]
        return (np.concatenate(out) if out else np.zeros(0)).astype(np.float32)


# ---------------------------------------------------------------------------
# RNNoise (tuỳ chọn)
# ---------------------------------------------------------------------------

_rnnoise_lib: ctypes.CDLL | None = None
_rnnoise_checked = False
_rnnoise_lock = threading.Lock()


def _rnnoise_path() -> Path | None:
    """Tìm DLL RNNoise trong gói ``pyrnnoise`` mà không import gói (tránh các phụ thuộc nặng của nó)."""
    spec = importlib.util.find_spec("pyrnnoise")
    if spec is None or not spec.submodule_search_locations:
        return None
    names = {"win32": "rnnoise.dll", "darwin": "librnnoise.dylib"}
    name = names.get(sys.platform, "librnnoise.so")
    for folder in spec.submodule_search_locations:
        path = Path(folder) / name
        if path.exists():
            return path
    return None


def load_rnnoise() -> ctypes.CDLL | None:
    global _rnnoise_lib, _rnnoise_checked
    with _rnnoise_lock:
        if _rnnoise_checked:
            return _rnnoise_lib
        _rnnoise_checked = True
        path = _rnnoise_path()
        if path is None:
            return None
        try:
            lib = ctypes.CDLL(str(path))
            lib.rnnoise_create.argtypes = [ctypes.c_void_p]
            lib.rnnoise_create.restype = ctypes.c_void_p
            lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
            lib.rnnoise_get_frame_size.restype = ctypes.c_int
            lib.rnnoise_process_frame.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float),
            ]
            lib.rnnoise_process_frame.restype = ctypes.c_float
            if lib.rnnoise_get_frame_size() != 480:
                raise OSError("RNNoise frame size khác 480")
        except (OSError, AttributeError) as e:
            log.warning("Không nạp được RNNoise (%s): %s", path, e)
            return None
        _rnnoise_lib = lib
        return lib


def rnnoise_available() -> bool:
    return load_rnnoise() is not None


class RNNoiseDenoiser:
    """RNNoise ở 48 kHz: nâng mẫu x3 → khử nhiễu từng khung 10 ms → hạ mẫu /3 (lọc FIR có trạng thái)."""

    BLOCK = 160  # 10 ms ở 16 kHz = 480 mẫu ở 48 kHz

    def __init__(self) -> None:
        lib = load_rnnoise()
        if lib is None:
            raise RuntimeError("RNNoise không khả dụng")
        self._lib = lib
        self._state: int | None = None
        aa = lowpass_taps(7400.0, 48000.0, 63)
        self._up = FirFilter(aa * 3.0)
        self._down = FirFilter(aa)
        self.delay = self.BLOCK + (self._up.delay + self._down.delay) // 3
        self.last_vad = 0.0
        self.reset()

    def reset(self) -> None:
        if self._state is not None:
            self._lib.rnnoise_destroy(self._state)
        self._state = self._lib.rnnoise_create(None)
        self._buf = np.zeros(0, dtype=np.float32)
        self._up.reset()
        self._down.reset()

    def close(self) -> None:
        if self._state is not None:
            self._lib.rnnoise_destroy(self._state)
            self._state = None

    def __del__(self) -> None:  # pragma: no cover - dọn trạng thái C khi bị thu hồi
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass

    def process(self, x: np.ndarray) -> np.ndarray:
        buf = np.concatenate([self._buf, x.astype(np.float32, copy=False)])
        n = buf.size // self.BLOCK
        if n == 0:
            self._buf = buf
            return np.zeros(0, dtype=np.float32)
        block = buf[: n * self.BLOCK]
        self._buf = buf[n * self.BLOCK :]
        up = np.zeros(block.size * 3, dtype=np.float32)
        up[::3] = block
        x48 = self._up.process(up) * 32768.0
        y48 = np.empty_like(x48, dtype=np.float32)
        ptr_t = ctypes.POINTER(ctypes.c_float)
        vad = 0.0
        for i in range(n):
            src = np.ascontiguousarray(x48[i * 480 : (i + 1) * 480], dtype=np.float32)
            dst = np.empty(480, dtype=np.float32)
            vad = self._lib.rnnoise_process_frame(self._state, dst.ctypes.data_as(ptr_t), src.ctypes.data_as(ptr_t))
            y48[i * 480 : (i + 1) * 480] = dst
        self.last_vad = float(vad)
        return (self._down.process(y48 / 32768.0)[::3]).astype(np.float32)


# ---------------------------------------------------------------------------
# AGC
# ---------------------------------------------------------------------------


class AutoGain:
    """Chuẩn hoá mức lời nói về ``target`` dBFS; chỉ học mức khi khối có vẻ là lời nói (cao hơn nền ≥ 6 dB).

    Nền nhiễu = cực tiểu mức khối 10 ms trong 3 s gần nhất (+3 dB), nên nhiễu đều không bị coi là lời nói.
    """

    BLOCK = 160
    WINDOW = 300  # 3 s

    def __init__(self, target_dbfs: float = AGC_TARGET_DBFS, max_gain_db: float = 30.0, min_gain_db: float = -12.0):
        self.target = target_dbfs
        self.max_gain = max_gain_db
        self.min_gain = min_gain_db
        self.delay = 0
        self.reset()

    def reset(self) -> None:
        self._levels: deque[float] = deque(maxlen=self.WINDOW)
        self._speech_db: float | None = None
        self._gain_db = 0.0

    @property
    def gain_db(self) -> float:
        return self._gain_db

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return x.astype(np.float32)
        out = np.empty(x.size, dtype=np.float32)
        for start in range(0, x.size, self.BLOCK):
            blk = x[start : start + self.BLOCK].astype(np.float64)
            db = float(10 * np.log10(np.mean(blk * blk) + 1e-12))
            self._levels.append(db)
            noise_db = min(self._levels) + 3.0
            if db > noise_db + 6.0 and db > -70.0:
                if self._speech_db is None:
                    self._speech_db = db
                # Mức lời nói: lên nhanh (tránh vỡ tiếng), xuống chậm.
                rate = 0.2 if db > self._speech_db else 0.01
                self._speech_db += rate * (db - self._speech_db)
            desired = 0.0 if self._speech_db is None else self.target - self._speech_db
            desired = min(self.max_gain, max(self.min_gain, desired))
            step = 0.5 if desired < self._gain_db else 0.05  # giảm nhanh, tăng chậm
            new_gain = self._gain_db + step * (desired - self._gain_db)
            g = 10 ** (np.linspace(self._gain_db, new_gain, blk.size, endpoint=False) / 20)
            self._gain_db = new_gain
            y = blk * g
            # Giới hạn đỉnh mềm trên 0,7 (tránh méo khi giảng viên nói to đột ngột).
            mag = np.abs(y)
            over = mag > 0.7
            if over.any():
                y[over] = np.sign(y[over]) * (0.7 + 0.29 * np.tanh((mag[over] - 0.7) / 0.29))
            out[start : start + blk.size] = y
        return out


# ---------------------------------------------------------------------------
# Chuỗi tiền xử lý
# ---------------------------------------------------------------------------


def available_engines() -> dict[str, bool]:
    return {"rnnoise": rnnoise_available(), "spectral": True}


class Preprocessor:
    """HPF → khử nhiễu → AGC, đo thời gian xử lý để tự hạ ``strong`` → ``light`` khi quá tải (IC-E-05)."""

    def __init__(
        self, level: DenoiseLevel = "light", *, hpf: bool = True, agc: bool = True, prefer_rnnoise: bool = True
    ) -> None:
        self.level: DenoiseLevel = level
        self._hpf = hpf
        self._agc = agc
        # Chế độ tiết kiệm pin: bỏ RNNoise (tốn CPU hơn ~30 lần) và dùng bộ lọc Wiener phổ.
        self.prefer_rnnoise = prefer_rnnoise
        self.last_hpf_db = -120.0  # mức sau lọc thông cao, trước khử nhiễu (đo SNR)
        self._build()
        self._ema_ms: float | None = None

    def _build(self) -> None:
        stages: list[Stage] = []
        engines: list[str] = []
        if self._hpf:
            stages.append(HighPass())
        if self.level != "off":
            rnn: RNNoiseDenoiser | None = None
            try:
                rnn = RNNoiseDenoiser() if self.prefer_rnnoise and rnnoise_available() else None
            except RuntimeError:
                rnn = None
            if rnn is not None:
                stages.append(rnn)
                engines.append("rnnoise")
            if self.level == "strong" or rnn is None:
                stages.append(SpectralDenoiser(self.level))
                engines.append(f"spectral_{self.level}")
        if self._agc:
            self.agc: AutoGain | None = AutoGain()
            stages.append(self.agc)
        else:
            self.agc = None
        self.stages = stages
        self.engines = engines

    @property
    def delay_ms(self) -> float:
        return sum(s.delay for s in self.stages) * 1000 / SAMPLE_RATE

    @property
    def processing_ms(self) -> float:
        """Thời gian xử lý trung bình cho mỗi 100 ms âm thanh."""
        return round(self._ema_ms or 0.0, 2)

    @property
    def engine(self) -> str:
        return "+".join(self.engines) or "off"

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return x.astype(np.float32)
        started = time.perf_counter()
        y = x.astype(np.float32, copy=False)
        for i, stage in enumerate(self.stages):
            y = stage.process(y)
            if i == 0 and self._hpf and y.size:
                self.last_hpf_db = float(10 * np.log10(np.mean(np.square(y, dtype=np.float64)) + 1e-12))
        per_100ms = (time.perf_counter() - started) * 1000 * (SAMPLE_RATE / 10) / x.size
        self._ema_ms = per_100ms if self._ema_ms is None else 0.9 * self._ema_ms + 0.1 * per_100ms
        return y

    def over_budget(self) -> bool:
        return self.level == "strong" and (self._ema_ms or 0.0) > STRONG_BUDGET_MS

    def set_level(self, level: DenoiseLevel, *, prefer_rnnoise: bool | None = None) -> np.ndarray:
        """Đổi mức khử nhiễu; trả về phần âm thanh còn đệm của chuỗi cũ (để không mất mẫu)."""
        tail = self.flush()
        self.close()
        self.level = level
        if prefer_rnnoise is not None:
            self.prefer_rnnoise = prefer_rnnoise
        self._build()
        self._ema_ms = None
        return tail

    def flush(self) -> np.ndarray:
        """Đẩy phần đệm ra bằng khoảng lặng (khi tạm dừng / đổi nguồn)."""
        pad = int(sum(s.delay for s in self.stages)) + 512
        return self.process(np.zeros(pad, dtype=np.float32)) if self.stages else np.zeros(0, dtype=np.float32)

    def reset(self) -> None:
        for s in self.stages:
            s.reset()
        self._ema_ms = None

    def close(self) -> None:
        for s in self.stages:
            if isinstance(s, RNNoiseDenoiser):
                s.close()
