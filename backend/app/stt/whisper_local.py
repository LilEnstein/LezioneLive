"""STT cục bộ bằng faster-whisper (FR-STT-03, E-STT-01/02).

- Model mặc định ``large-v3-turbo`` trên GPU; CPU dùng ``int8`` (gợi ý model ``small``).
- Hết VRAM → thử ``int8``, rồi model nhỏ hơn trên CPU, kèm cảnh báo chất lượng giảm.
- Tiến trình tải model được ước lượng bằng dung lượng thư mục cache Hugging Face.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import numpy as np

from app.stt.base import ProgressCallback, STTError, STTProvider, STTResult, clean_transcript

log = logging.getLogger(__name__)

ALLOW_PATTERNS = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
FALLBACK_MODEL = "small"
SMALL_MODELS = {"tiny", "base", "small"}

_dll_dirs_added = False


def add_cuda_dll_dirs() -> None:
    """Windows: thêm thư mục ``bin`` của nvidia-cublas-cu12 / nvidia-cudnn-cu12 (pip) vào đường dẫn DLL."""
    global _dll_dirs_added
    if _dll_dirs_added or sys.platform != "win32":
        return
    _dll_dirs_added = True
    try:
        import nvidia  # type: ignore[import-not-found]
    except ImportError:
        return
    for base in getattr(nvidia, "__path__", []):
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            bin_dir = Path(base) / sub / "bin"
            if bin_dir.is_dir():
                try:
                    os.add_dll_directory(str(bin_dir))
                except OSError:
                    pass
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


CUDA_LIBS = {
    "win32": ["cublas64_12.dll", "cudnn64_9.dll"],
    "linux": ["libcublas.so.12", "libcudnn.so.9"],
}


def missing_cuda_libs() -> list[str]:
    """Thư viện CUDA 12 mà ctranslate2 cần nhưng không nạp được.

    Kiểm tra trước là bắt buộc: nếu ctranslate2 đã lỗi "cublas64_12.dll not found" một lần thì lần thử
    GPU tiếp theo trong cùng tiến trình có thể treo vĩnh viễn.
    """
    import ctypes

    add_cuda_dll_dirs()
    names = CUDA_LIBS.get(sys.platform)
    if not names:
        return []
    missing = []
    for name in names:
        try:
            if sys.platform == "win32":
                ctypes.WinDLL(name)
            else:
                ctypes.CDLL(name)
        except OSError:
            missing.append(name)
    return missing


def gpu_info() -> dict[str, Any]:
    info: dict[str, Any] = {"available": False, "name": None, "vram_mb": None, "cuda_libs_missing": []}
    try:
        import ctranslate2

        info["available"] = ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return info
    if info["available"]:
        info["cuda_libs_missing"] = missing_cuda_libs()
    smi = shutil.which("nvidia-smi")
    if info["available"] and smi:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.strip()
            if out:
                name, mem = [x.strip() for x in out.splitlines()[0].split(",")[:2]]
                info["name"] = name
                info["vram_mb"] = int(float(mem))
        except Exception:  # noqa: BLE001
            pass
    return info


def repo_for(model_name: str) -> str:
    from faster_whisper.utils import _MODELS

    return _MODELS.get(model_name, model_name)


def is_cached(model_name: str) -> bool:
    if Path(model_name).is_dir():
        return True
    from faster_whisper.utils import download_model

    try:
        download_model(model_name, local_files_only=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def _repo_cache_dir(repo_id: str) -> Path:
    from huggingface_hub import constants

    return Path(constants.HF_HUB_CACHE) / f"models--{repo_id.replace('/', '--')}"


def _expected_size(repo_id: str) -> int | None:
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo_id, files_metadata=True)
        total = 0
        for sib in info.siblings or []:
            if any(fnmatch(sib.rfilename, p) for p in ALLOW_PATTERNS):
                total += sib.size or 0
        return total or None
    except Exception:  # noqa: BLE001
        return None


def _dir_size(path: Path) -> int:
    total = 0
    if path.is_dir():
        for f in path.rglob("*"):
            try:
                if f.is_file() and not f.is_symlink():
                    total += f.stat().st_size
            except OSError:
                continue
    return total


def _is_oom(err: BaseException) -> bool:
    msg = str(err).lower()
    return "out of memory" in msg or "cuda_error_out_of_memory" in msg or "cublas_status_alloc_failed" in msg


class WhisperLocalProvider(STTProvider):
    name = "whisper_local"
    is_cloud = False

    def __init__(
        self,
        model_name: str = "large-v3-turbo",
        device: str = "auto",
        compute_type: str = "auto",
        on_warning: Callable[[str, str], None] | None = None,
    ) -> None:
        self.model_name = model_name
        self.device_pref = device
        self.compute_pref = compute_type
        self.on_warning = on_warning
        self._model: Any = None
        self._device: str | None = None
        self._compute_type: str | None = None
        self._active_model = model_name
        self._candidates: list[tuple[str, str, str]] = []
        self._lock = asyncio.Lock()
        self._prepare_lock = threading.Lock()
        self.degraded: str | None = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def info(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "cloud": False,
            "model": self._active_model,
            "device": self._device,
            "compute_type": self._compute_type,
            "degraded": self.degraded,
        }

    # ---------------------------------------------------------------- prepare
    def _plan(self) -> list[tuple[str, str, str]]:
        """Danh sách (model, device, compute_type) thử lần lượt: GPU theo cấu hình → CPU với model nhỏ."""
        gpu = gpu_info()
        usable_gpu = gpu["available"] and not gpu["cuda_libs_missing"]
        if gpu["available"] and gpu["cuda_libs_missing"]:
            self.degraded = (
                "Có GPU NVIDIA nhưng thiếu thư viện CUDA 12 ("
                + ", ".join(gpu["cuda_libs_missing"])
                + "). Cài: pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 để dùng GPU. Tạm chạy CPU."
            )
        use_cuda = usable_gpu and self.device_pref in ("auto", "cuda")
        plan: list[tuple[str, str, str]] = []
        if use_cuda:
            if self.compute_pref != "auto":
                plan.append((self.model_name, "cuda", self.compute_pref))
            else:
                vram = gpu.get("vram_mb") or 0
                first = "float16" if vram >= 6000 else "int8_float16"
                plan.append((self.model_name, "cuda", first))
                if first != "int8_float16":
                    plan.append((self.model_name, "cuda", "int8_float16"))
            plan.append((self.model_name, "cuda", "int8"))
            if self.device_pref == "auto":
                cpu_model = self.model_name if self.model_name in SMALL_MODELS else FALLBACK_MODEL
                plan.append((cpu_model, "cpu", "int8"))
        else:
            ctype = self.compute_pref if self.compute_pref != "auto" else "int8"
            plan.append((self.model_name, "cpu", ctype))
            if self.model_name not in SMALL_MODELS:
                plan.append((FALLBACK_MODEL, "cpu", "int8"))
        return plan

    async def prepare(self, progress: ProgressCallback | None = None) -> None:
        if self.ready:
            return
        await asyncio.to_thread(self._prepare_sync, progress)

    def _download(self, model_name: str, progress: ProgressCallback | None) -> str:
        from faster_whisper.utils import download_model

        if Path(model_name).is_dir():
            return model_name
        try:
            return download_model(model_name, local_files_only=True)
        except Exception:  # noqa: BLE001 - chưa có trong cache -> tải
            pass
        repo_id = repo_for(model_name)
        total = _expected_size(repo_id)
        cache_dir = _repo_cache_dir(repo_id)
        base = _dir_size(cache_dir)
        done = threading.Event()
        result: dict[str, Any] = {}

        def worker() -> None:
            try:
                result["path"] = download_model(model_name)
            except Exception as e:  # noqa: BLE001
                result["error"] = e
            finally:
                done.set()

        threading.Thread(target=worker, name="whisper-download", daemon=True).start()
        while not done.wait(0.5):
            if progress is not None:
                size = max(0, _dir_size(cache_dir) - base)
                frac = min(0.99, size / total) if total else 0.0
                mb = size / 1_048_576
                msg = f"Đang tải model {model_name}: {mb:.0f} MB" + (f" / {total / 1_048_576:.0f} MB" if total else "")
                progress(frac, msg)
        if "error" in result:
            raise STTError(f"Tải model {model_name} thất bại: {result['error']}", code="download_failed")
        return result["path"]

    def _prepare_sync(self, progress: ProgressCallback | None) -> None:
        with self._prepare_lock:
            if self._model is not None:
                return
            add_cuda_dll_dirs()
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise STTError("Chưa cài faster-whisper (pip install faster-whisper)", code="missing_dep") from e
            self._candidates = self._plan()
            last_err: BaseException | None = None
            cuda_broken = False
            for idx, (model_name, device, ctype) in enumerate(self._candidates):
                if device == "cuda" and cuda_broken:
                    continue
                try:
                    path = self._download(model_name, progress)
                    if progress is not None:
                        progress(0.99, f"Đang nạp model {model_name} ({device}, {ctype})…")
                    model = WhisperModel(path, device=device, compute_type=ctype)
                    # Chạy thử 1 giây im lặng để lộ lỗi CUDA ngay lúc nạp.
                    segs, _ = model.transcribe(
                        np.zeros(16000, dtype=np.float32), language="it", beam_size=1, without_timestamps=True
                    )
                    list(segs)
                except STTError:
                    raise
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    log.warning("Nạp Whisper %s trên %s/%s thất bại: %s", model_name, device, ctype, e)
                    if device == "cuda" and not _is_oom(e):
                        # Lỗi CUDA không phải hết VRAM (thiếu DLL, driver...): thử GPU tiếp có thể treo.
                        cuda_broken = True
                    continue
                self._model = model
                self._device, self._compute_type, self._active_model = device, ctype, model_name
                self._candidates = [c for c in self._candidates[idx + 1 :] if not (cuda_broken and c[1] == "cuda")]
                if idx > 0:
                    self.degraded = f"Đang dùng {model_name} trên {device} ({ctype}) do cấu hình ưu tiên không chạy được"
                    if self.on_warning:
                        self.on_warning("STT_DEGRADED", self.degraded)
                log.info("Whisper sẵn sàng: %s trên %s (%s)", model_name, device, ctype)
                return
            raise STTError(f"Không nạp được model Whisper: {last_err}", code="load_failed")

    def _degrade(self) -> None:
        """E-STT-02: hết VRAM → chuyển sang cấu hình kế tiếp nhẹ hơn."""
        from faster_whisper import WhisperModel

        self._model = None
        while self._candidates:
            model_name, device, ctype = self._candidates.pop(0)
            try:
                path = self._download(model_name, None)
                self._model = WhisperModel(path, device=device, compute_type=ctype)
                self._device, self._compute_type, self._active_model = device, ctype, model_name
                self.degraded = f"GPU hết bộ nhớ, chuyển sang {model_name} ({device}, {ctype}); chất lượng có thể giảm"
                if self.on_warning:
                    self.on_warning("STT_DEGRADED", self.degraded)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("Hạ cấp Whisper sang %s/%s/%s thất bại: %s", model_name, device, ctype, e)
        raise STTError("Không còn cấu hình Whisper nào chạy được", code="load_failed")

    # ------------------------------------------------------------- transcribe
    def _transcribe_sync(self, audio: np.ndarray, language: str, prompt: str | None) -> STTResult:
        start = time.perf_counter()
        segments, info = self._model.transcribe(
            audio,
            language=language,
            beam_size=5 if self._device == "cuda" else 1,
            initial_prompt=prompt or None,
            vad_filter=False,  # đã cắt bằng Silero VAD
            condition_on_previous_text=False,
            without_timestamps=True,
            temperature=[0.0, 0.2, 0.4],
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )
        texts: list[str] = []
        weighted = 0.0
        weight = 0
        for seg in segments:
            if seg.no_speech_prob > 0.8 and seg.avg_logprob < -1.0:
                continue
            t = seg.text.strip()
            if not t:
                continue
            texts.append(t)
            weighted += seg.avg_logprob * len(t)
            weight += len(t)
        text = clean_transcript(" ".join(texts))
        confidence = math.exp(weighted / weight) if weight else 0.0
        return STTResult(
            text=text,
            confidence=max(0.0, min(1.0, confidence)),
            language=getattr(info, "language", language),
            extra={"elapsed_ms": int((time.perf_counter() - start) * 1000)},
        )

    async def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str = "it",
        prompt: str | None = None,
        keywords: list[str] | None = None,
    ) -> STTResult:
        if self._model is None:
            raise STTError("Model Whisper chưa sẵn sàng", code="not_ready")
        async with self._lock:
            try:
                return await asyncio.to_thread(self._transcribe_sync, audio, language, prompt)
            except Exception as e:  # noqa: BLE001
                if not _is_oom(e):
                    raise STTError(f"Whisper lỗi: {e}", retryable=True) from e
                log.warning("Whisper hết VRAM, hạ cấp cấu hình")
                await asyncio.to_thread(self._degrade)
                return await asyncio.to_thread(self._transcribe_sync, audio, language, prompt)

    async def close(self) -> None:
        self._model = None
