"""Phân biệt giảng viên / người khác (IC-FR-30..33, IC-AC-05)."""

from __future__ import annotations

import numpy as np
import pytest

from app.audio.pcm import read_wav, resample
from app.audio.speaker_tagger import (
    SpeakerModelManager,
    SpeakerTagger,
    SpectralEmbedder,
    cluster,
    fbank,
)

from .conftest import FIXTURES


class VectorEmbedder:
    """Embedder giả: "âm thanh" là chỉ số của vector đã sinh sẵn."""

    name = "test-vectors"

    def __init__(self, kind: str = "neural") -> None:
        self.kind = kind

    def embed(self, audio):  # noqa: ANN001 - audio là vector luôn
        return audio


def make_vectors(n: int, n_other_every: int, seed: int = 0, dim: int = 64, noise: float = 0.55):
    """Giảng viên nói phần lớn thời gian; cứ ``n_other_every`` câu có một câu của 2 sinh viên khác."""
    rng = np.random.default_rng(seed)
    centers = [rng.standard_normal(dim) for _ in range(3)]
    centers = [c / np.linalg.norm(c) for c in centers]
    items = []
    for i in range(n):
        spk = 0 if (i % n_other_every) else 1 + (i // n_other_every) % 2
        v = centers[spk] + noise * rng.standard_normal(dim) / np.sqrt(dim) * 3
        items.append(("lecturer" if spk == 0 else "other", v / np.linalg.norm(v)))
    return items


def run_tagger(items, *, seg_ms: int = 4000) -> dict[int, str]:
    tagger = SpeakerTagger(VectorEmbedder())
    labels: dict[int, str] = {}
    for i, (_truth, v) in enumerate(items):
        for r in tagger.add(i, i * (seg_ms + 500), seg_ms, v):
            labels[r.segment_id] = r.speaker
    return labels


def test_enrollment_after_two_minutes_and_accuracy():
    items = make_vectors(120, n_other_every=6)
    tagger = SpeakerTagger(VectorEmbedder())
    first_results = []
    labels: dict[int, str] = {}
    for i, (_t, v) in enumerate(items):
        res = tagger.add(i, i * 4500, 4000, v)
        if res and not first_results:
            first_results = res
        for r in res:
            labels[r.segment_id] = r.speaker
    # Mẫu giảng viên lập sau ~2 phút, gán nhãn bù cho các segment trước đó.
    assert tagger.ready
    assert len(first_results) > 20
    assert first_results[0].segment_id == 0
    lect = [i for i, (t, _) in enumerate(items) if t == "lecturer"]
    other = [i for i, (t, _) in enumerate(items) if t == "other"]
    lect_acc = np.mean([labels[i] == "lecturer" for i in lect])
    other_acc = np.mean([labels[i] == "other" for i in other])
    assert lect_acc >= 0.85 and other_acc >= 0.70  # IC-AC-05


def test_accuracy_over_many_simulated_lectures():
    """Embedding rất nhiễu (xa micro): trung bình nhiều buổi vẫn đạt IC-AC-05 (≥ 85% / ≥ 70%)."""
    lect_accs, other_accs = [], []
    for seed in range(10):
        items = make_vectors(120, n_other_every=6, seed=seed)
        labels = run_tagger(items)
        lect_accs.append(np.mean([labels.get(i) == "lecturer" for i, (t, _) in enumerate(items) if t == "lecturer"]))
        other_accs.append(np.mean([labels.get(i) == "other" for i, (t, _) in enumerate(items) if t == "other"]))
    assert np.mean(lect_accs) >= 0.85 and np.mean(other_accs) >= 0.70
    assert min(lect_accs) >= 0.7


def test_unknown_before_enrollment_and_missing_embedding():
    tagger = SpeakerTagger(VectorEmbedder())
    items = make_vectors(5, n_other_every=100)
    for i, (_t, v) in enumerate(items):
        assert tagger.add(i, i * 4500, 4000, v) == []
    assert not tagger.ready
    assert tagger.add(99, 10_000, 4000, None) == []


def test_corrections_update_model():
    items = make_vectors(80, n_other_every=5, seed=3)
    tagger = SpeakerTagger(VectorEmbedder())
    for i, (_t, v) in enumerate(items):
        tagger.add(i, i * 4500, 4000, v)
    assert tagger.ready
    # Người dùng sửa một câu "other" thành lecturer → lần sau câu đó (và câu rất giống) là lecturer.
    target = next(i for i, (t, _) in enumerate(items) if t == "other")
    tagger.learn(target, "lecturer")
    assert tagger._classify(target, items[target][1], adapt=False).speaker == "lecturer"
    # Sửa ngược lại thành other → ví dụ "người khác" được ghi nhớ.
    tagger.learn(target, "other")
    assert tagger._classify(target, items[target][1], adapt=False).speaker == "other"
    before = tagger.threshold
    lect = next(i for i, (t, _) in enumerate(items) if t == "lecturer")
    tagger.learn(lect, "other")
    assert tagger.threshold >= before


def test_manual_label_before_enrollment_is_kept():
    items = make_vectors(60, n_other_every=4, seed=5)
    tagger = SpeakerTagger(VectorEmbedder())
    tagger.add(0, 0, 4000, items[0][1])
    tagger.learn(0, "other")
    labels = {}
    for i, (_t, v) in enumerate(items[1:], start=1):
        for r in tagger.add(i, i * 4500, 4000, v):
            labels[r.segment_id] = r.speaker
    assert tagger.ready
    assert labels.get(0) in (None, "other")


def test_cluster_groups_similar_vectors():
    a = np.array([1.0, 0.0])
    b = np.array([0.0, 1.0])
    labels = cluster([a, a * 0.99 + b * 0.01, b, b], 0.4)
    assert labels[0] == labels[1] and labels[2] == labels[3] and labels[0] != labels[2]


def speech_utterances() -> list[np.ndarray]:
    from app.audio.segmenter import Segmenter
    from app.audio.vad import make_vad

    audio, sr = read_wav(FIXTURES / "audio" / "speech_en_pauses.wav")
    x = resample(audio, sr, 16000)
    seg = Segmenter(make_vad())
    return [e.utterance.audio for e in seg.push(x) + seg.flush() if e.kind == "utterance" and e.utterance]


def test_fbank_matches_kaldi_layout():
    utt = speech_utterances()[0]
    feats = fbank(utt)
    assert feats.shape[1] == 80
    assert feats.shape[0] == 1 + (utt.size - 400) // 160
    assert np.allclose(feats.mean(axis=0), 0.0, atol=1e-4)  # CMN


def test_spectral_embedder_separates_pitch_shifted_voice():
    emb = SpectralEmbedder()
    utts = [u for u in speech_utterances() if u.size > 16000 * 2]
    same = [emb.embed(u) for u in utts]
    shifted = [emb.embed(resample(u, 16000, 12000)) for u in utts]
    s_same = np.mean([float(same[i] @ same[j]) for i in range(len(same)) for j in range(i + 1, len(same))])
    s_diff = np.mean([float(a @ b) for a in same for b in shifted])
    assert s_same > s_diff


@pytest.mark.skipif(not SpeakerModelManager().cached(), reason="Chưa tải model WeSpeaker ONNX")
def test_wespeaker_onnx_embeddings():
    emb = SpeakerModelManager().embedder()
    assert emb.kind == "neural"
    utts = [u for u in speech_utterances() if u.size > 16000 * 2]
    same = [emb.embed(u) for u in utts]
    other = [emb.embed(resample(u, 16000, 12800)) for u in utts]
    s_same = np.mean([float(same[i] @ same[j]) for i in range(len(same)) for j in range(i + 1, len(same))])
    s_diff = np.mean([float(a @ b) for a in same for b in other])
    assert s_same > 0.5 and s_diff < 0.35
