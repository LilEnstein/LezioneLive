"""Đo WER cho chế độ lớp học trực tiếp (feature_spec mục 13 Phase IC-0, mục 15).

Trộn bản ghi bài giảng "sạch" với tạp âm ở nhiều mức SNR (tuỳ chọn thêm tiếng vang tổng hợp), chạy các mức khử nhiễu
(off / light / strong) × model Whisper, cắt câu bằng VAD như lúc học thật, rồi tính WER so với transcript chuẩn.

Ví dụ:
    backend/.venv/Scripts/python scripts/eval_room.py --wav bai_giang.wav --ref bai_giang.txt \
        --snr 20 15 10 5 --denoise off light strong --whisper small large-v3-turbo --reverb

    # dùng tạp âm thật thu trong phòng học (tiếng quạt, nói chuyện, gõ phím)
    ... --noise tap_am_phong_hoc.wav

Không gửi gì lên Internet (chỉ dùng Whisper cục bộ). Cần: pip install jiwer (có trong nhóm dev).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.audio.pcm import read_wav, resample, rms_dbfs  # noqa: E402
from app.audio.preprocess import Preprocessor  # noqa: E402
from app.audio.segmenter import Segmenter  # noqa: E402
from app.audio.vad import make_vad  # noqa: E402

SR = 16000


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text.lower())
    text = re.sub(r"[^\w\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def synthetic_noise(n: int, seed: int = 0) -> np.ndarray:
    """Tạp âm phòng học giả lập: nhiễu hồng (quạt/điều hoà) + ù 50 Hz + "tiếng xì xào" điều biên."""
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n)
    pink = np.convolve(white, np.ones(16) / 16, mode="same")
    t = np.arange(n) / SR
    hum = 0.3 * np.sin(2 * np.pi * 50 * t)
    babble = np.convolve(rng.standard_normal(n), np.ones(4) / 4, mode="same") * (0.5 + 0.5 * np.sin(2 * np.pi * 0.7 * t))
    x = pink + hum * pink.std() + 0.6 * babble
    return (x / (np.abs(x).max() + 1e-9)).astype(np.float32)


def load_noise(path: Path | None, n: int) -> np.ndarray:
    if path is None:
        return synthetic_noise(n)
    audio, sr = read_wav(path)
    noise = resample(audio, sr, SR)
    reps = int(np.ceil(n / max(1, noise.size)))
    return np.tile(noise, reps)[:n].astype(np.float32)


def reverb(x: np.ndarray, rt60: float = 0.6, drr_db: float = 0.0, seed: int = 1) -> np.ndarray:
    """RIR tổng hợp: âm trực tiếp + đuôi vang (nhiễu trắng suy giảm luỹ thừa, phòng học RT60 ~0,5–0,8 s).

    ``drr_db`` = tỉ lệ năng lượng âm trực tiếp / âm vang: ~ +5 dB khi ngồi gần (≤ 3 m), ~ 0 dB ở 6 m,
    ~ −5 dB ở cuối lớp (10 m).
    """
    rng = np.random.default_rng(seed)
    length = int(rt60 * SR)
    t = np.arange(1, length + 1) / SR
    tail = rng.standard_normal(length) * np.exp(-6.9 * t / rt60)
    tail *= 10 ** (-drr_db / 20) / np.sqrt(np.sum(tail**2))
    rir = np.concatenate([[1.0], tail])
    y = np.convolve(x, rir)[: x.size]
    return (y * 10 ** ((rms_dbfs(x) - rms_dbfs(y)) / 20)).astype(np.float32)  # giữ nguyên mức âm


def mix(clean: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    gain = 10 ** ((rms_dbfs(clean) - snr_db - rms_dbfs(noise)) / 20)
    return np.clip(clean + gain * noise, -1.0, 1.0).astype(np.float32)


def utterances(audio: np.ndarray, denoise: str) -> list[np.ndarray]:
    pre = Preprocessor(denoise)  # type: ignore[arg-type]
    seg = Segmenter(make_vad(), threshold=0.6, min_speech_ms=800)
    out: list[np.ndarray] = []
    for i in range(0, audio.size, 1600):
        for ev in seg.push(pre.process(audio[i : i + 1600])):
            if ev.kind == "utterance" and ev.utterance is not None:
                out.append(ev.utterance.audio)
    for ev in seg.push(pre.flush()) + seg.flush():
        if ev.kind == "utterance" and ev.utterance is not None:
            out.append(ev.utterance.audio)
    pre.close()
    return out


async def transcribe_all(provider, chunks: list[np.ndarray]) -> str:  # type: ignore[no-untyped-def]
    texts = []
    for chunk in chunks:
        result = await provider.transcribe(chunk, language="it")
        if result.text:
            texts.append(result.text)
    return " ".join(texts)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wav", type=Path, required=True, help="Bản ghi bài giảng sạch (WAV PCM 16-bit)")
    parser.add_argument("--ref", type=Path, required=True, help="Transcript chuẩn (UTF-8)")
    parser.add_argument("--noise", type=Path, help="Tạp âm thật (WAV); mặc định: tạp âm tổng hợp")
    parser.add_argument("--snr", type=float, nargs="+", default=[20.0, 15.0, 10.0, 5.0])
    parser.add_argument("--denoise", nargs="+", default=["off", "light", "strong"], choices=["off", "light", "strong"])
    parser.add_argument("--whisper", nargs="+", default=["small"])
    parser.add_argument("--reverb", action="store_true", help="Thêm tiếng vang tổng hợp (RT60 0,6 s)")
    parser.add_argument(
        "--drr", type=float, default=0.0, help="Tỉ lệ âm trực tiếp/âm vang (dB): +5 gần, 0 ≈ 6 m, −5 cuối lớp"
    )
    parser.add_argument("--seconds", type=float, default=0.0, help="Chỉ dùng N giây đầu (0 = toàn bộ)")
    args = parser.parse_args()

    try:
        import jiwer
    except ImportError:
        sys.exit("Cần cài jiwer: backend/.venv/Scripts/pip install jiwer")
    from app.stt.whisper_local import WhisperLocalProvider

    audio, sr = read_wav(args.wav)
    clean = resample(audio, sr, SR)
    if args.seconds > 0:
        clean = clean[: int(args.seconds * SR)]
    if args.reverb:
        clean = reverb(clean, drr_db=args.drr)
    reference = normalize(args.ref.read_text(encoding="utf-8"))
    noise = load_noise(args.noise, clean.size)
    duration = clean.size / SR

    print(f"Âm thanh: {duration:.0f} s · tham chiếu: {len(reference.split())} từ · tiếng vang: {'có' if args.reverb else 'không'}")
    print(f"{'model':<16}{'SNR':>6}  {'khử nhiễu':<10}{'WER':>8}{'câu':>6}{'RTF':>8}")
    for model in args.whisper:
        provider = WhisperLocalProvider(model_name=model)
        await provider.prepare()
        for snr in args.snr:
            noisy = mix(clean, noise, snr)
            for level in args.denoise:
                started = time.perf_counter()
                chunks = utterances(noisy, level)
                hypothesis = normalize(await transcribe_all(provider, chunks))
                rtf = (time.perf_counter() - started) / duration
                wer = jiwer.wer(reference, hypothesis) if hypothesis else 1.0
                flag = " ✓" if wer <= 0.25 else ""
                print(f"{model:<16}{snr:>5.0f}  {level:<10}{wer:>7.1%}{len(chunks):>6}{rtf:>8.2f}{flag}")
        await provider.close()
    print("Mục tiêu G1: WER ≤ 25% khi SNR ≥ 15 dB; ≤ 35% khi SNR 10–15 dB (IC-NFR-02).")


if __name__ == "__main__":
    asyncio.run(main())
