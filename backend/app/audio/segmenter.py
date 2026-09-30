"""Cắt luồng audio thành utterance theo khoảng lặng (FR-STT-01: tối thiểu 1,5 s, tối đa 12 s).

Quy tắc:
- Vào trạng thái nói khi xác suất VAD ≥ ``threshold``; giữ ``pad_ms`` âm thanh trước đó (pre-roll).
- Kết thúc câu khi im lặng liên tục ≥ ``min_silence_ms`` và câu đã đủ ``min_ms``.
- Câu ngắn hơn ``min_ms`` thì chờ thêm để gộp với câu sau; nếu im lặng kéo dài ≥ ``max_pending_silence_ms``
  thì vẫn phát (không làm mất lời nói ngắn như "Sì."), trừ khi phần có tiếng < ``min_speech_ms`` (tiếng click/ho).
- Câu dài tới ``max_ms`` bị cắt cưỡng bức tại cửa sổ có xác suất nói thấp nhất trong ``lookback_ms`` cuối,
  phần còn lại chuyển sang câu tiếp theo.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.audio.pcm import SAMPLE_RATE
from app.audio.vad import VAD


@dataclass
class Utterance:
    audio: np.ndarray
    t_start_ms: int
    t_end_ms: int
    forced: bool = False

    @property
    def duration_ms(self) -> int:
        return self.t_end_ms - self.t_start_ms


@dataclass
class SegEvent:
    kind: Literal["speech_start", "speech_end", "utterance"]
    t_ms: int
    utterance: Utterance | None = None


class Segmenter:
    def __init__(
        self,
        vad: VAD,
        *,
        sample_rate: int = SAMPLE_RATE,
        min_ms: int = 1500,
        max_ms: int = 12000,
        threshold: float = 0.5,
        neg_threshold: float | None = None,
        min_silence_ms: int = 500,
        pad_ms: int = 200,
        max_pending_silence_ms: int = 1200,
        min_speech_ms: int = 250,
        lookback_ms: int = 3000,
        start_offset_ms: int = 0,
    ) -> None:
        self.vad = vad
        self.sr = sample_rate
        self.W = getattr(vad, "window", 512)
        self.threshold = threshold
        self.neg_threshold = neg_threshold if neg_threshold is not None else max(0.05, threshold - 0.15)
        self.min_len = self._samples(min_ms)
        self.max_len = self._samples(max_ms)
        self.min_silence = self._samples(min_silence_ms)
        self.pad = self._samples(pad_ms)
        self.max_pending_silence = self._samples(max_pending_silence_ms)
        self.min_speech = self._samples(min_speech_ms)
        self.lookback = max(1, self._samples(lookback_ms) // self.W)
        self.offset_ms = start_offset_ms

        self._pending = np.zeros(0, dtype=np.float32)
        self._seen = 0  # số mẫu đã qua VAD
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, -(-self.pad // self.W)))
        self._reset_utterance()

    # ---------------------------------------------------------------- helpers
    def _samples(self, ms: int) -> int:
        return int(ms * self.sr / 1000)

    def _ms(self, sample: int) -> int:
        return self.offset_ms + int(sample * 1000 / self.sr)

    def _reset_utterance(self) -> None:
        self._in_speech = False
        self._buf: list[np.ndarray] = []
        self._probs: list[float] = []
        self._start = 0
        self._last_voice = 0
        self._silence_run = 0
        self._voiced = 0

    @property
    def t_ms(self) -> int:
        """Thời điểm hiện tại của luồng audio (ms, tính cả offset)."""
        return self._ms(self._seen)

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    # ------------------------------------------------------------------- API
    def push(self, samples: np.ndarray) -> list[SegEvent]:
        data = np.concatenate([self._pending, samples.astype(np.float32, copy=False)])
        n = data.shape[0] // self.W
        events: list[SegEvent] = []
        for i in range(n):
            events.extend(self._process(data[i * self.W : (i + 1) * self.W]))
        self._pending = data[n * self.W :].copy()
        return events

    def flush(self) -> list[SegEvent]:
        """Phát nốt câu đang dở (khi tạm dừng / kết thúc phiên)."""
        events: list[SegEvent] = []
        if self._in_speech:
            if self._voiced >= self.min_speech:
                cut = min(self._start + len(self._buf) * self.W, self._last_voice + self.pad)
                events.extend(self._finalize(cut))
            else:
                events.append(SegEvent("speech_end", self._ms(self._last_voice)))
                self._reset_utterance()
        self._pending = np.zeros(0, dtype=np.float32)
        return events

    def reset(self) -> None:
        """Xoá trạng thái (giữ nguyên đồng hồ luồng để mốc thời gian liên tục)."""
        self._reset_utterance()
        self._preroll.clear()
        self._pending = np.zeros(0, dtype=np.float32)
        self.vad.reset()

    # -------------------------------------------------------------- internals
    def _process(self, win: np.ndarray) -> list[SegEvent]:
        p = self.vad(win)
        start = self._seen
        self._seen += self.W
        end = self._seen
        if not self._in_speech:
            if p >= self.threshold:
                pre = list(self._preroll)
                self._preroll.clear()
                self._in_speech = True
                self._buf = [*pre, win]
                self._probs = [0.0] * len(pre) + [p]
                self._start = start - len(pre) * self.W
                self._last_voice = end
                self._silence_run = 0
                self._voiced = self.W
                return [SegEvent("speech_start", self._ms(self._start))]
            self._preroll.append(win)
            return []

        self._buf.append(win)
        self._probs.append(p)
        if p >= self.neg_threshold:
            self._last_voice = end
            self._silence_run = 0
            if p >= self.threshold:
                self._voiced += self.W
        else:
            self._silence_run += self.W

        if self._silence_run >= self.min_silence:
            voiced_len = self._last_voice - self._start + self.pad
            if voiced_len >= self.min_len:
                return self._finalize(min(end, self._last_voice + self.pad))
            if self._silence_run >= self.max_pending_silence:
                if self._voiced >= self.min_speech:
                    return self._finalize(min(end, self._last_voice + self.pad))
                ev = [SegEvent("speech_end", self._ms(self._last_voice))]
                self._reset_utterance()
                return ev
            return []
        if end - self._start >= self.max_len:
            return self._force_cut()
        return []

    def _finalize(self, cut_sample: int) -> list[SegEvent]:
        keep = max(1, min(len(self._buf), -(-(cut_sample - self._start) // self.W)))
        audio = np.concatenate(self._buf[:keep])
        t_start = self._ms(self._start)
        t_end = self._ms(self._start + keep * self.W)
        leftover = self._buf[keep:]
        self._reset_utterance()
        for w in leftover[-self._preroll.maxlen :] if self._preroll.maxlen else []:
            self._preroll.append(w)
        return [SegEvent("speech_end", t_end), SegEvent("utterance", t_end, Utterance(audio, t_start, t_end))]

    def _force_cut(self) -> list[SegEvent]:
        n = len(self._buf)
        min_windows = max(1, self.min_len // self.W)
        lo = max(min_windows, n - self.lookback)
        lo = min(lo, n - 1)
        window_probs = self._probs[lo:n]
        idx = lo + int(np.argmin(window_probs)) if window_probs else n - 1
        keep = idx + 1
        audio = np.concatenate(self._buf[:keep])
        t_start = self._ms(self._start)
        t_end = self._ms(self._start + keep * self.W)
        # Phần còn lại trở thành đầu câu tiếp theo (vẫn đang nói).
        self._buf = self._buf[keep:]
        self._probs = self._probs[keep:]
        self._start += keep * self.W
        self._voiced = sum(self.W for q in self._probs if q >= self.threshold)
        if not self._buf:
            self._last_voice = self._start
        return [SegEvent("utterance", t_end, Utterance(audio, t_start, t_end, forced=True))]
