"""Voice Activity Detection.

``SileroVAD`` chạy model Silero ONNX theo kiểu streaming (cửa sổ 512 mẫu = 32 ms ở 16 kHz, kèm 64 mẫu
ngữ cảnh), dùng lại file model đi kèm ``faster-whisper`` nên không cần tải thêm hay cài PyTorch.
``EnergyVAD`` là phương án dự phòng dựa trên năng lượng.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Protocol

import numpy as np

from app.audio.pcm import rms_dbfs

log = logging.getLogger(__name__)

WINDOW = 512
CONTEXT = 64


class VAD(Protocol):
    window: int

    def __call__(self, chunk: np.ndarray) -> float: ...

    def reset(self) -> None: ...


def default_silero_path() -> Path | None:
    try:
        import faster_whisper
    except ImportError:
        return None
    assets = Path(faster_whisper.__file__).parent / "assets"
    candidates = sorted(assets.glob("silero_vad*.onnx"), reverse=True)
    return candidates[0] if candidates else None


class SileroVAD:
    window = WINDOW

    def __init__(self, model_path: Path | None = None) -> None:
        import onnxruntime as ort

        path = model_path or default_silero_path()
        if path is None or not Path(path).exists():
            raise FileNotFoundError("Không tìm thấy model Silero VAD (silero_vad*.onnx)")
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self._session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"], sess_options=opts)
        names = {i.name for i in self._session.get_inputs()}
        # v6 (faster-whisper): input/h/c ; v5 (silero-vad gốc): input/state/sr
        self._mode = "hc" if {"h", "c"} <= names else "state"
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        if chunk.shape[0] != WINDOW:
            raise ValueError(f"Silero VAD cần đúng {WINDOW} mẫu")
        x = np.concatenate([self._context, chunk.astype(np.float32)])[None, :]
        if self._mode == "hc":
            out, self._h, self._c = self._session.run(None, {"input": x, "h": self._h, "c": self._c})
        else:
            out, self._state = self._session.run(
                None, {"input": x, "state": self._state, "sr": np.array(16000, dtype=np.int64)}
            )
        self._context = chunk[-CONTEXT:].astype(np.float32).copy()
        return float(np.asarray(out).reshape(-1)[0])


class EnergyVAD:
    """VAD theo năng lượng với ngưỡng thích nghi nhẹ theo nền nhiễu."""

    window = WINDOW

    def __init__(self, threshold_db: float = -42.0, margin_db: float = 10.0) -> None:
        self.threshold_db = threshold_db
        self.margin_db = margin_db
        self.reset()

    def reset(self) -> None:
        self._noise_db = -70.0

    def __call__(self, chunk: np.ndarray) -> float:
        db = max(-90.0, rms_dbfs(chunk))
        if db < self._noise_db + 3:
            self._noise_db = 0.9 * self._noise_db + 0.1 * db
        else:
            self._noise_db = min(self._noise_db + 0.02, -20.0)
        gate = max(self.threshold_db, self._noise_db + self.margin_db)
        return 1.0 / (1.0 + math.exp(-(db - gate) / 2.0))


def make_vad(prefer: str = "silero") -> VAD:
    if prefer == "silero":
        try:
            return SileroVAD()
        except Exception as e:  # noqa: BLE001
            log.warning("Không nạp được Silero VAD (%s), dùng EnergyVAD", e)
    return EnergyVAD()
