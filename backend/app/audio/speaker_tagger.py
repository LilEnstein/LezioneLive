"""Phân biệt lời giảng viên (``lecturer``) và người khác (``other``) bằng speaker embedding (IC-FR-30..33).

- Embedding: WeSpeaker ResNet34 (ONNX, ~26 MB, cùng họ model pyannote dùng) chạy bằng onnxruntime, không cần
  PyTorch; đặc trưng fbank 80 chiều kiểu Kaldi tính bằng numpy. Khi chưa tải được model thì dùng embedding
  phổ (thống kê log-mel) — kém chính xác hơn nhưng không cần tải gì.
- Mẫu giọng giảng viên lấy tự động từ ~2 phút lời nói đầu tiên: gom cụm các segment, cụm có tổng thời
  lượng lớn nhất là giảng viên (IC-FR-31). Trước khi đủ mẫu, segment mang nhãn ``unknown`` và được gán
  nhãn bù khi mẫu sẵn sàng.
- Người dùng sửa nhãn → cập nhật tâm cụm, ngưỡng và danh sách ví dụ "người khác" (học lại từ sửa đổi).
- Embedding chỉ nằm trong bộ nhớ của phiên, không ghi xuống đĩa (dữ liệu sinh trắc học).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np

from app.audio.pcm import SAMPLE_RATE
from app.config import SPEAKER_MODEL_FILE, SPEAKER_MODEL_REPO

log = logging.getLogger(__name__)

Speaker = Literal["lecturer", "other", "unknown"]

MIN_EMBED_S = 0.6


# ---------------------------------------------------------------------------
# Fbank kiểu Kaldi (khớp torchaudio.compliance.kaldi.fbank mà WeSpeaker dùng)
# ---------------------------------------------------------------------------

_MEL_CACHE: dict[tuple[int, int, int], np.ndarray] = {}


def _mel(f: np.ndarray | float) -> np.ndarray | float:
    return 1127.0 * np.log(1.0 + np.asarray(f) / 700.0)


def mel_banks(n_mels: int, n_fft: int = 512, sr: int = SAMPLE_RATE, low: float = 20.0) -> np.ndarray:
    key = (n_mels, n_fft, sr)
    if key not in _MEL_CACHE:
        n_bins = n_fft // 2
        mel_low, mel_high = float(_mel(low)), float(_mel(sr / 2))
        delta = (mel_high - mel_low) / (n_mels + 1)
        idx = np.arange(n_mels)[:, None]
        left, center, right = mel_low + idx * delta, mel_low + (idx + 1) * delta, mel_low + (idx + 2) * delta
        mel = np.asarray(_mel(np.arange(n_bins) * sr / n_fft))[None, :]
        up = (mel - left) / (center - left)
        down = (right - mel) / (right - center)
        _MEL_CACHE[key] = np.maximum(0.0, np.minimum(up, down))
    return _MEL_CACHE[key]


def fbank(audio: np.ndarray, n_mels: int = 80, sr: int = SAMPLE_RATE, cmn: bool = True) -> np.ndarray:
    """Log mel filterbank (T, n_mels): cửa sổ 25 ms, bước 10 ms, povey, pre-emphasis 0,97."""
    win_len, shift, n_fft = int(0.025 * sr), int(0.010 * sr), 512
    x = np.asarray(audio, dtype=np.float64) * 32768.0
    if x.size < win_len:
        return np.zeros((0, n_mels), dtype=np.float32)
    n_frames = 1 + (x.size - win_len) // shift
    idx = np.arange(win_len)[None, :] + shift * np.arange(n_frames)[:, None]
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    prev = np.concatenate([frames[:, :1], frames[:, :-1]], axis=1)
    frames = frames - 0.97 * prev
    frames = frames * (np.hanning(win_len) ** 0.85)
    spec = np.abs(np.fft.rfft(frames, n=n_fft)) ** 2
    energies = spec[:, : n_fft // 2] @ mel_banks(n_mels, n_fft, sr).T
    feats = np.log(np.maximum(energies, np.finfo(np.float32).eps))
    if cmn:
        feats = feats - feats.mean(axis=0, keepdims=True)
    return feats.astype(np.float32)


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


# ---------------------------------------------------------------------------
# Embedder
# ---------------------------------------------------------------------------


class Embedder(Protocol):
    name: str
    kind: Literal["neural", "spectral"]

    def embed(self, audio: np.ndarray) -> np.ndarray | None: ...


class WeSpeakerEmbedder:
    name = "wespeaker-resnet34"
    kind: Literal["neural", "spectral"] = "neural"

    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 2
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"], sess_options=opts)
        self._input = self._session.get_inputs()[0].name
        self._output = self._session.get_outputs()[0].name

    def embed(self, audio: np.ndarray) -> np.ndarray | None:
        if audio.size < MIN_EMBED_S * SAMPLE_RATE:
            return None
        feats = fbank(audio)
        if feats.shape[0] < 20:
            return None
        emb = self._session.run([self._output], {self._input: feats[None, :, :]})[0][0]
        return _normalize(np.asarray(emb, dtype=np.float64))


class SpectralEmbedder:
    """Dự phòng: trung bình + độ lệch chuẩn log-mel 40 băng trên các khung có năng lượng."""

    name = "spectral-logmel"
    kind: Literal["neural", "spectral"] = "spectral"

    def embed(self, audio: np.ndarray) -> np.ndarray | None:
        if audio.size < MIN_EMBED_S * SAMPLE_RATE:
            return None
        feats = fbank(audio, n_mels=40, cmn=False)
        if feats.shape[0] < 20:
            return None
        energy = feats.mean(axis=1)
        voiced = feats[energy >= energy.max() - 6.0]  # log-mel tự nhiên: 6 ≈ 26 dB
        if voiced.shape[0] < 10:
            voiced = feats
        mean = voiced.mean(axis=0)
        vec = np.concatenate([mean - mean.mean(), voiced.std(axis=0)])
        return _normalize(vec.astype(np.float64))


# ---------------------------------------------------------------------------
# Quản lý model (tải theo yêu cầu từ Hugging Face)
# ---------------------------------------------------------------------------


class SpeakerModelManager:
    def __init__(self, repo: str = SPEAKER_MODEL_REPO, filename: str = SPEAKER_MODEL_FILE) -> None:
        self.repo = repo
        self.filename = filename
        self.state: Literal["idle", "downloading", "ready", "error"] = "idle"
        self.error: str | None = None
        self._path: Path | None = None
        self._neural: WeSpeakerEmbedder | None = None
        self._lock = threading.Lock()
        self._task: asyncio.Task | None = None

    def _cached_path(self) -> Path | None:
        if self._path is not None and self._path.exists():
            return self._path
        try:
            from huggingface_hub import hf_hub_download

            self._path = Path(hf_hub_download(self.repo, self.filename, local_files_only=True))
        except Exception:  # noqa: BLE001 - chưa có trong cache
            return None
        return self._path

    def cached(self) -> bool:
        return self._cached_path() is not None

    def status(self) -> dict[str, Any]:
        cached = self.cached()
        state = "ready" if cached and self.state != "downloading" else self.state
        return {"model": self.repo, "cached": cached, "state": state, "error": self.error, "size_mb": 26}

    def _download(self) -> None:
        from huggingface_hub import hf_hub_download

        self._path = Path(hf_hub_download(self.repo, self.filename))

    async def prepare(self) -> bool:
        if self.cached():
            self.state = "ready"
            return True
        self.state, self.error = "downloading", None
        try:
            await asyncio.to_thread(self._download)
        except Exception as e:  # noqa: BLE001
            self.state, self.error = "error", f"Không tải được model nhận diện giọng nói: {type(e).__name__}"
            log.warning("Tải model speaker lỗi: %s", e)
            return False
        self.state = "ready"
        return True

    def start_prepare(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.prepare())
        return self._task

    def embedder(self) -> Embedder:
        """Embedder cho một phiên mới: WeSpeaker nếu model đã có trên máy, không thì embedding phổ."""
        path = self._cached_path()
        if path is None:
            return SpectralEmbedder()
        with self._lock:
            if self._neural is None:
                try:
                    self._neural = WeSpeakerEmbedder(path)
                except Exception as e:  # noqa: BLE001
                    log.warning("Không nạp được model speaker ONNX (%s), dùng embedding phổ", e)
                    return SpectralEmbedder()
            return self._neural


# ---------------------------------------------------------------------------
# Gán nhãn người nói trong một phiên
# ---------------------------------------------------------------------------


@dataclass
class TagResult:
    segment_id: int
    speaker: Speaker
    score: float | None


@dataclass
class _Enrolled:
    segment_id: int
    t_start_ms: int
    dur_ms: int
    emb: np.ndarray


# Tham số theo loại embedding: (ngưỡng gộp cụm, ngưỡng mặc định, ngưỡng min, ngưỡng max)
PARAMS = {
    "neural": (0.40, 0.35, 0.20, 0.60),
    "spectral": (0.0, 0.90, 0.50, 0.995),
}


def otsu_split(values: np.ndarray, weights: np.ndarray, min_separation: float = 2.5) -> float | None:
    """Ngưỡng Otsu chia ``values`` thành hai nhóm; None nếu hai nhóm không tách rõ (phân bố một đỉnh)."""
    order = np.argsort(values)
    v, w = values[order], weights[order]
    if v.size < 4:
        return None
    best: tuple[float, int] | None = None
    for k in range(1, v.size):
        w0, w1 = w[:k].sum(), w[k:].sum()
        m0, m1 = np.average(v[:k], weights=w[:k]), np.average(v[k:], weights=w[k:])
        between = w0 * w1 * (m1 - m0) ** 2
        if best is None or between > best[0]:
            best = (float(between), k)
    assert best is not None
    k = best[1]
    lo, hi = v[:k], v[k:]
    spread = np.sqrt((lo.var() + hi.var()) / 2) + 1e-3
    if (hi.mean() - lo.mean()) / spread < min_separation:
        return None
    return float((lo.max() + hi.min()) / 2)


def cluster(embs: list[np.ndarray], merge_threshold: float) -> list[int]:
    """Gom cụm phân cấp (average linkage, cosine). Trả về nhãn cụm cho từng phần tử."""
    clusters: list[list[int]] = [[i] for i in range(len(embs))]
    if len(embs) < 2:
        return [0] * len(embs)
    sim = np.clip(np.stack(embs) @ np.stack(embs).T, -1.0, 1.0)
    while len(clusters) > 1:
        best, pair = -2.0, (0, 0)
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                s = float(sim[np.ix_(clusters[a], clusters[b])].mean())
                if s > best:
                    best, pair = s, (a, b)
        if best < merge_threshold:
            break
        a, b = pair
        clusters[a] = clusters[a] + clusters[b]
        clusters.pop(b)
    labels = [0] * len(embs)
    for ci, members in enumerate(clusters):
        for m in members:
            labels[m] = ci
    return labels


class SpeakerTagger:
    ENROLL_MS = 120_000  # IC-FR-31: 2 phút đầu
    MAX_ENROLL_MS = 300_000
    MIN_ENROLL_SEGMENTS = 3
    MIN_ENROLL_SPEECH_MS = 10_000
    MAX_CENTROID_WEIGHT = 50.0

    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self.kind = embedder.kind
        self.merge_thr, self.threshold, self.thr_min, self.thr_max = PARAMS[self.kind]
        self.centroid: np.ndarray | None = None
        self._weight = 0.0
        self._first_t: int | None = None
        self._enroll: list[_Enrolled] = []
        self._forced_lecturer: list[np.ndarray] = []
        self._others: list[np.ndarray] = []
        self._manual: dict[int, Speaker] = {}
        self._cache: OrderedDict[int, np.ndarray] = OrderedDict()

    # ---------------------------------------------------------------- state
    @property
    def ready(self) -> bool:
        return self.centroid is not None

    def info(self) -> dict[str, Any]:
        return {
            "embedder": self.embedder.name,
            "ready": self.ready,
            "threshold": round(self.threshold, 3),
            "enrolled_segments": len(self._enroll),
        }

    def embed(self, audio: np.ndarray) -> np.ndarray | None:
        """Tính embedding (chậm: gọi trong luồng phụ)."""
        try:
            return self.embedder.embed(audio)
        except Exception:  # noqa: BLE001
            log.exception("Lỗi tính speaker embedding")
            return None

    # ------------------------------------------------------------------ API
    def add(self, segment_id: int, t_start_ms: int, dur_ms: int, emb: np.ndarray | None) -> list[TagResult]:
        """Thêm segment mới. Trả về các nhãn cần cập nhật (segment này + nhãn bù khi vừa đủ mẫu giảng viên)."""
        if emb is None:
            return [TagResult(segment_id, self._manual.get(segment_id, "unknown"), None)] if self.ready else []
        self._remember(segment_id, emb)
        if self.ready:
            return [self._classify(segment_id, emb, adapt=True)]
        if self._first_t is None:
            self._first_t = t_start_ms
        if self._manual.get(segment_id) != "other":
            self._enroll.append(_Enrolled(segment_id, t_start_ms, dur_ms, emb))
        span = t_start_ms - self._first_t
        speech_ms = sum(e.dur_ms for e in self._enroll)
        enough = len(self._enroll) >= self.MIN_ENROLL_SEGMENTS and speech_ms >= self.MIN_ENROLL_SPEECH_MS
        if not ((span >= self.ENROLL_MS and enough) or (span >= self.MAX_ENROLL_MS and self._enroll)):
            return []
        self._build_template()
        seen = {e.segment_id for e in self._enroll}
        results = [self._classify(e.segment_id, e.emb, adapt=False) for e in self._enroll]
        if segment_id not in seen:
            results.append(self._classify(segment_id, emb, adapt=False))
        return results

    def learn(self, segment_id: int, label: Speaker) -> None:
        """Người dùng sửa nhãn (PATCH /segments/{id}/speaker)."""
        if label == "unknown":
            self._manual.pop(segment_id, None)
            return
        self._manual[segment_id] = label
        emb = self._cache.get(segment_id)
        if emb is None:
            return
        if self.centroid is None:
            if label == "lecturer":
                self._forced_lecturer.append(emb)
            else:
                self._enroll = [e for e in self._enroll if e.segment_id != segment_id]
                self._others.append(emb)
            return
        score = float(emb @ self.centroid)
        if label == "lecturer":
            self._update_centroid(emb, 3.0)
            if score < self.threshold:
                self.threshold = max(self.thr_min, score - 0.02)
            self._others = [o for o in self._others if float(o @ emb) < 0.9]
        else:
            self._others = (self._others + [emb])[-50:]
            if score >= self.threshold:
                self.threshold = min(self.thr_max, score + 0.02)

    # ------------------------------------------------------------- internals
    def _remember(self, segment_id: int, emb: np.ndarray) -> None:
        self._cache[segment_id] = emb
        while len(self._cache) > 500:
            self._cache.popitem(last=False)

    def _update_centroid(self, emb: np.ndarray, weight: float) -> None:
        if self.centroid is None:
            self.centroid, self._weight = emb.copy(), weight
            return
        self.centroid = _normalize(self.centroid * self._weight + emb * weight)
        self._weight = min(self.MAX_CENTROID_WEIGHT, self._weight + weight)

    def _dominant_cluster(self, embs: list[np.ndarray], durs: np.ndarray) -> list[int]:
        """Cụm có tổng thời lượng lớn nhất (IC-FR-31). Âm thanh xa/nhiễu làm embedding cùng người kém giống
        nhau hơn bình thường, nên hạ dần ngưỡng gộp tới khi có cụm chiếm ≥ 40% thời lượng."""
        total = float(durs.sum())
        forced_idx = [int(np.argmax([float(f @ e) for e in embs])) for f in self._forced_lecturer]
        thr = self.merge_thr
        while True:
            labels = cluster(embs, thr)
            totals: dict[int, float] = {}
            for lab, d in zip(labels, durs, strict=True):
                totals[lab] = totals.get(lab, 0.0) + d
            for idx in forced_idx:  # nhãn người dùng đặt trước khi đủ mẫu
                totals[labels[idx]] = totals.get(labels[idx], 0.0) + total
            main = max(totals, key=lambda k: totals[k])
            if totals[main] >= 0.4 * total or thr <= 0.1:
                return [i for i, lab in enumerate(labels) if lab == main]
            thr -= 0.05

    def _centroid(self, embs: list[np.ndarray], durs: np.ndarray, members: list[int]) -> np.ndarray:
        vecs = [embs[i] * durs[i] for i in members] + [f * float(durs.mean()) * 3 for f in self._forced_lecturer]
        return _normalize(sum(vecs))

    def _loo_sims(self, embs: list[np.ndarray], durs: np.ndarray, members: list[int]) -> np.ndarray:
        """Độ giống với tâm cụm, tính "bỏ-một-ra" cho thành viên (tránh thiên lệch do tự góp vào tâm)."""
        total = sum(embs[i] * durs[i] for i in members) + sum(
            f * float(durs.mean()) * 3 for f in self._forced_lecturer
        )
        member_set = set(members)
        out = np.empty(len(embs))
        for i, e in enumerate(embs):
            vec = total - e * durs[i] if i in member_set and len(members) > 1 else total
            out[i] = float(e @ _normalize(np.asarray(vec, dtype=np.float64)))
        return out

    def _build_template(self) -> None:
        items = self._enroll
        embs = [e.emb for e in items]
        durs = np.array([max(1, e.dur_ms) for e in items], dtype=np.float64)
        if self.kind == "neural" and len(embs) >= 2:
            members = self._dominant_cluster(embs, durs)
        else:
            members = list(range(len(embs)))
            for _ in range(2):  # trung bình cắt tỉa: bỏ dần các segment xa tâm nhất (người khác)
                if len(members) <= 3:
                    break
                c = self._centroid(embs, durs, members)
                sims = np.array([float(embs[i] @ c) for i in members])
                keep = sims >= np.quantile(sims, 0.25)
                members = [m for m, k in zip(members, keep, strict=True) if k]
        thr: float | None = None
        # Tinh chỉnh: chia hai nhóm theo độ giống với tâm (Otsu). Nếu tách rõ thì nhóm trên là giảng viên
        # và ngưỡng nằm giữa hai nhóm; nếu không (chỉ có một người nói) thì mọi segment là giảng viên.
        for _ in range(3):
            sims_all = self._loo_sims(embs, durs, members)
            split = otsu_split(sims_all, durs)
            if split is None:
                members = list(range(len(embs)))
                thr = None
            else:
                thr = split
                members = [i for i, s in enumerate(sims_all) if s >= split] or members
        centroid = self._centroid(embs, durs, members)
        self.centroid = centroid
        self._weight = float(min(self.MAX_CENTROID_WEIGHT, len(members) + 3 * len(self._forced_lecturer)))
        sims = self._loo_sims(embs, durs, members)[members]
        if thr is None and len(sims) >= 3:
            thr = float(sims.mean() - 3 * sims.std())
        if thr is not None:
            self.threshold = min(self.thr_max, max(self.thr_min, thr))
        log.info(
            "Đã lập mẫu giọng giảng viên từ %d/%d segment (%s, ngưỡng %.2f)",
            len(members), len(items), self.embedder.name, self.threshold,
        )

    def _classify(self, segment_id: int, emb: np.ndarray, *, adapt: bool) -> TagResult:
        assert self.centroid is not None
        score = float(emb @ self.centroid)
        manual = self._manual.get(segment_id)
        if manual is not None:
            return TagResult(segment_id, manual, round(score, 3))
        label: Speaker = "lecturer" if score >= self.threshold else "other"
        if label == "lecturer" and self._others:
            nearest_other = max(float(emb @ o) for o in self._others)
            if nearest_other > score:
                label = "other"
        if adapt and label == "lecturer" and score >= self.threshold + 0.1:
            self._update_centroid(emb, 1.0)
        return TagResult(segment_id, label, round(score, 3))
