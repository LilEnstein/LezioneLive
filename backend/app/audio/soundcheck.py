"""Đo mức ồn nền / SNR và kiểm tra âm thanh trước giờ học (IC-FR-05, IC-FR-20..23, Flow A).

- ``SnrTracker``: SNR thời gian thực trong phiên, từ mức âm từng khung 100 ms (sau lọc thông cao, trước khử
  nhiễu) và nhãn VAD: ồn nền = trung vị các khung không có lời nói, mức lời nói = trung bình công suất các
  khung có lời nói.
- ``SoundCheck``: 10 s im lặng (đo ồn nền) + 20 s giảng viên nói (đo mức lời nói), rồi đánh giá
  Tốt / Tạm được / Kém kèm gợi ý và ngưỡng hiệu chỉnh cho ``room_profile``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from app.audio.pcm import FRAME_SAMPLES, SAMPLE_RATE, rms_dbfs
from app.audio.preprocess import HighPass
from app.audio.sources.base import AudioSource, SourceError
from app.audio.vad import make_vad

log = logging.getLogger(__name__)

Rating = Literal["good", "fair", "poor"]

GOOD_SNR_DB = 15.0
FAIR_SNR_DB = 10.0
LOW_SNR_WARN_DB = 8.0  # IC-E-04
NOISE_PHASE_S = 10
SPEECH_PHASE_S = 20
MIN_SPEECH_FRAMES = 20  # cần ≥ 2 s lời nói ở bước 2 để đánh giá
NO_AUDIO_TIMEOUT_S = 5.0

RATING_LABELS = {"good": "Tốt", "fair": "Tạm được", "poor": "Kém"}

TIPS: dict[str, str] = {
    "move_closer": "Ngồi gần giảng viên hơn (3 hàng đầu, cách bục giảng ≤ 6 m).",
    "phone_front": "Dùng điện thoại làm micro, đặt trên bàn hàng đầu và hướng micro về bục giảng.",
    "avoid_noise": "Tránh xa điều hoà, quạt, máy chiếu và cửa sổ đang mở.",
    "strong_denoise": "Bật khử nhiễu mạnh.",
    "external_mic": "Cân nhắc micro rời (micro cài áo không dây hoặc micro USB đa hướng).",
    "no_speech": "Không nghe thấy giọng nói ở bước 2. Kiểm tra đúng micro, âm lượng, và thử lại khi giảng viên bắt đầu nói.",
    "clipping": "Âm thanh bị vỡ (quá to). Đặt micro xa nguồn âm hơn hoặc giảm độ nhạy micro.",
    "quiet_room": "Phòng yên tĩnh: có thể giữ khử nhiễu nhẹ.",
}


def power_mean_db(levels: list[float] | np.ndarray) -> float:
    arr = np.asarray(levels, dtype=np.float64)
    return float(10 * np.log10(np.mean(10 ** (arr / 10)) + 1e-12))


def rate(snr_db: float | None) -> Rating:
    if snr_db is None or snr_db < FAIR_SNR_DB:
        return "poor"
    return "good" if snr_db >= GOOD_SNR_DB else "fair"


def recommend(noise_floor: float, speech_level: float | None, snr_db: float | None) -> dict[str, Any]:
    """Ngưỡng hiệu chỉnh lưu vào ``room_profile`` (IC-FR-12/13/23)."""
    snr = snr_db if snr_db is not None else 0.0
    vad_threshold = 0.55 if snr >= 20 else 0.6 if snr >= FAIR_SNR_DB else 0.65
    denoise = "light" if snr >= GOOD_SNR_DB else "strong"
    if speech_level is not None:
        min_level = max(noise_floor + 3.0, speech_level - 15.0)
    else:
        min_level = noise_floor + 6.0
    return {
        "vad_threshold": round(vad_threshold, 2),
        "recommended_denoise": denoise,
        "min_level_dbfs": round(min_level, 1),
    }


def tips_for(
    rating: Rating, *, noise_floor: float, speech_level: float | None, speech_found: bool, clipped: bool
) -> list[dict[str, str]]:
    codes: list[str] = []
    if not speech_found:
        codes.append("no_speech")
    if clipped:
        codes.append("clipping")
    if rating != "good":
        if speech_level is not None and speech_level < -38.0:
            codes += ["move_closer", "phone_front"]
        if noise_floor > -50.0:
            codes.append("avoid_noise")
        codes.append("strong_denoise")
        if rating == "poor":
            for extra in ("move_closer", "phone_front", "avoid_noise", "external_mic"):
                if extra not in codes:
                    codes.append(extra)
    elif noise_floor < -60.0:
        codes.append("quiet_room")
    seen: list[str] = []
    for c in codes:
        if c not in seen:
            seen.append(c)
    return [{"code": c, "text": TIPS[c]} for c in seen]


# ---------------------------------------------------------------------------
# SNR thời gian thực trong phiên
# ---------------------------------------------------------------------------


class SnrTracker:
    WINDOW_MS = 30_000
    MIN_FRAMES = 10

    def __init__(self) -> None:
        self._frames: deque[tuple[int, float, bool]] = deque()

    def reset(self) -> None:
        self._frames.clear()

    def add(self, t_ms: int, level_db: float, speech: bool) -> None:
        self._frames.append((t_ms, level_db, speech))
        cutoff = t_ms - self.WINDOW_MS
        while self._frames and self._frames[0][0] < cutoff:
            self._frames.popleft()

    def noise_floor(self) -> float | None:
        quiet = [lv for _, lv, sp in self._frames if not sp]
        if len(quiet) >= self.MIN_FRAMES:
            return float(np.median(quiet))
        if len(self._frames) >= self.MIN_FRAMES:
            return float(np.percentile([lv for _, lv, _ in self._frames], 10))
        return None

    def speech_level(self) -> float | None:
        voiced = [lv for _, lv, sp in self._frames if sp]
        if len(voiced) < self.MIN_FRAMES:
            return None
        return power_mean_db(voiced)

    def snr(self) -> float | None:
        noise, speech = self.noise_floor(), self.speech_level()
        if noise is None or speech is None:
            return None
        return float(min(60.0, max(0.0, speech - noise)))

    def level_between(self, t_start: int, t_end: int) -> float | None:
        """Mức (phân vị 90) của các khung trong [t_start, t_end] — cổng mức âm tối thiểu IC-FR-13."""
        levels = [lv for t, lv, _ in self._frames if t_start <= t <= t_end + 100]
        if not levels:
            return None
        return float(np.percentile(levels, 90))


# ---------------------------------------------------------------------------
# Sound check 30 giây
# ---------------------------------------------------------------------------


@dataclass
class SoundCheckResult:
    noise_floor_dbfs: float
    speech_level_dbfs: float | None
    snr_db: float | None
    rating: Rating
    speech_seconds: float
    clipped: bool
    tips: list[dict[str, str]]
    recommended: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "noise_floor_dbfs": round(self.noise_floor_dbfs, 1),
            "speech_level_dbfs": None if self.speech_level_dbfs is None else round(self.speech_level_dbfs, 1),
            "snr_db": None if self.snr_db is None else round(self.snr_db, 1),
            "rating": self.rating,
            "rating_label": RATING_LABELS[self.rating],
            "speech_seconds": round(self.speech_seconds, 1),
            "clipped": self.clipped,
            "tips": self.tips,
            "recommended": self.recommended,
        }


def evaluate(noise_audio: np.ndarray, speech_audio: np.ndarray, *, vad_threshold: float = 0.5) -> SoundCheckResult:
    """Đánh giá từ âm thanh 2 bước (float32 16 kHz, đã lọc thông cao)."""
    noise_frames = [
        rms_dbfs(noise_audio[i : i + FRAME_SAMPLES]) for i in range(0, noise_audio.size - FRAME_SAMPLES + 1, FRAME_SAMPLES)
    ]
    skip = min(5, max(0, len(noise_frames) - 5))  # bỏ 0,5 s đầu (tiếng bấm nút)
    noise_floor = float(np.median(noise_frames[skip:])) if noise_frames else -90.0

    vad = make_vad()
    probs: list[float] = []
    for i in range(0, speech_audio.size - vad.window + 1, vad.window):
        probs.append(vad(speech_audio[i : i + vad.window]))
    speech_levels: list[float] = []
    per_frame = FRAME_SAMPLES / vad.window
    for f in range(speech_audio.size // FRAME_SAMPLES):
        chunk = speech_audio[f * FRAME_SAMPLES : (f + 1) * FRAME_SAMPLES]
        lo, hi = int(f * per_frame), int((f + 1) * per_frame) + 1
        window_probs = probs[lo:hi]
        level = rms_dbfs(chunk)
        if window_probs and max(window_probs) >= vad_threshold and level > noise_floor + 3.0:
            speech_levels.append(level)
    speech_found = len(speech_levels) >= MIN_SPEECH_FRAMES
    speech_level = power_mean_db(speech_levels) if speech_found else None
    snr = None if speech_level is None else max(0.0, speech_level - noise_floor)
    peak = float(np.max(np.abs(speech_audio))) if speech_audio.size else 0.0
    clipped = peak >= 0.99 and float(np.mean(np.abs(speech_audio) >= 0.99)) > 0.001
    rating = rate(snr)
    return SoundCheckResult(
        noise_floor_dbfs=noise_floor,
        speech_level_dbfs=speech_level,
        snr_db=snr,
        rating=rating,
        speech_seconds=len(speech_levels) * FRAME_SAMPLES / SAMPLE_RATE,
        clipped=clipped,
        tips=tips_for(rating, noise_floor=noise_floor, speech_level=speech_level, speech_found=speech_found,
                      clipped=clipped),
        recommended=recommend(noise_floor, speech_level, snr),
    )


class SoundCheck:
    """Một lượt kiểm tra 30 s trên một nguồn âm thanh (micro laptop hoặc điện thoại)."""

    def __init__(
        self,
        source: AudioSource,
        *,
        room_profile_id: int | None = None,
        noise_s: float = NOISE_PHASE_S,
        speech_s: float = SPEECH_PHASE_S,
        on_done: Callable[[SoundCheck], Awaitable[None] | None] | None = None,
        on_finish: Callable[[SoundCheck], None] | None = None,
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.source = source
        self.room_profile_id = room_profile_id
        self.noise_s = noise_s
        self.speech_s = speech_s
        self._on_done = on_done
        self._on_finish = on_finish
        self.state: Literal["starting", "noise", "speech", "analyzing", "done", "failed", "cancelled"] = "starting"
        self.error: str | None = None
        self.level_dbfs = -120.0
        self.result: SoundCheckResult | None = None
        self.created = time.monotonic()
        self._hpf = HighPass()
        self._noise: list[np.ndarray] = []
        self._speech: list[np.ndarray] = []
        self._samples = 0
        self._task: asyncio.Task | None = None

    @property
    def elapsed_s(self) -> float:
        return self._samples / SAMPLE_RATE

    @property
    def total_s(self) -> float:
        return self.noise_s + self.speech_s

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self.state = "cancelled"
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def wait(self, timeout: float | None = None) -> None:
        if self._task is not None:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)

    async def _run(self) -> None:
        try:
            await self.source.start()
            self.state = "noise"
            frames = self.source.frames().__aiter__()
            while self.elapsed_s < self.total_s:
                try:
                    frame = await asyncio.wait_for(frames.__anext__(), timeout=NO_AUDIO_TIMEOUT_S)
                except StopAsyncIteration:
                    raise SourceError(self.source.health().error or "Nguồn âm thanh đã dừng.") from None
                except TimeoutError:
                    raise SourceError(
                        self.source.health().error or "Không nhận được âm thanh từ micro trong 5 giây."
                    ) from None
                self._on_frame(frame)
            self.state = "analyzing"
            noise = np.concatenate(self._noise) if self._noise else np.zeros(0, dtype=np.float32)
            speech = np.concatenate(self._speech) if self._speech else np.zeros(0, dtype=np.float32)
            self.result = await asyncio.to_thread(evaluate, noise, speech)
            self.state = "done"
        except asyncio.CancelledError:
            self.state = "cancelled"
            raise
        except SourceError as e:
            self.state, self.error = "failed", str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("Sound check lỗi")
            self.state, self.error = "failed", f"Lỗi nội bộ: {type(e).__name__}"
        finally:
            try:
                await self.source.stop()
            except Exception:  # noqa: BLE001
                log.debug("Dừng nguồn sound check lỗi", exc_info=True)
            if self._on_finish is not None:
                self._on_finish(self)
        if self._on_done is not None and self.state == "done":
            out = self._on_done(self)
            if asyncio.iscoroutine(out):
                await out

    def _on_frame(self, frame: np.ndarray) -> None:
        x = frame.astype(np.float32) / 32768.0 if frame.dtype == np.int16 else frame.astype(np.float32)
        self.level_dbfs = round(rms_dbfs(x), 1)
        y = self._hpf.process(x)
        if self.elapsed_s < self.noise_s:
            self._noise.append(y)
            if self.elapsed_s + x.size / SAMPLE_RATE >= self.noise_s:
                self.state = "speech"
        else:
            self.state = "speech"
            self._speech.append(y)
        self._samples += x.size

    def as_dict(self) -> dict[str, Any]:
        phase_elapsed = self.elapsed_s if self.elapsed_s < self.noise_s else self.elapsed_s - self.noise_s
        return {
            "id": self.id,
            "state": self.state,
            "source": self.source.kind,
            "room_profile_id": self.room_profile_id,
            "elapsed_s": round(min(self.elapsed_s, self.total_s), 1),
            "total_s": self.total_s,
            "noise_s": self.noise_s,
            "speech_s": self.speech_s,
            "phase_elapsed_s": round(phase_elapsed, 1),
            "progress": round(min(1.0, self.elapsed_s / self.total_s), 3),
            "level_dbfs": self.level_dbfs,
            "error": self.error,
            "result": self.result.as_dict() if self.result else None,
        }


@dataclass
class SoundCheckManager:
    """Giữ các lượt sound check (mỗi lúc chỉ một lượt đang chạy); kết quả giữ 30 phút."""

    checks: dict[str, SoundCheck] = field(default_factory=dict)

    async def start(self, check: SoundCheck) -> SoundCheck:
        for other in list(self.checks.values()):
            if other.state in ("starting", "noise", "speech"):
                await other.cancel()
        now = time.monotonic()
        for key, old in list(self.checks.items()):
            if now - old.created > 1800:
                self.checks.pop(key, None)
        self.checks[check.id] = check
        check.start()
        return check

    def get(self, check_id: str) -> SoundCheck | None:
        return self.checks.get(check_id)

    async def shutdown(self) -> None:
        for check in list(self.checks.values()):
            await check.cancel()
