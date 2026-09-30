"""Interface ``STTProvider`` (FR-STT-02, NFR-10: thêm provider mới chỉ cần hiện thực 1 class)."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

ProgressCallback = Callable[[float, str], None]


class STTError(RuntimeError):
    def __init__(self, message: str, *, code: str = "stt_error", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class STTResult:
    text: str
    confidence: float
    language: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# Câu "ảo giác" quen thuộc của Whisper khi gặp nhạc/im lặng (phụ đề cộng đồng...).
_HALLUCINATIONS = re.compile(
    r"amara\.org|sottotitoli (creati|e revisione|a cura)|qtss|iscriviti al canale|grazie per la visione",
    re.I,
)


# Một cụm 1–6 từ lặp liên tiếp ≥ 4 lần: Whisper bị "kẹt vòng" (hay gặp với âm thanh phòng học nhiễu).
_REPEAT_LOOP = re.compile(r"\b(\w[\w'’]*(?:\s+\w[\w'’]*){0,5}?)(?:[\s,.;:!?-]+\1\b){3,}", re.I)


def collapse_repetitions(text: str) -> str:
    """"in due classi in due classi in due classi in due classi" → "in due classi…"."""
    return _REPEAT_LOOP.sub(lambda m: m.group(1) + "…", text)


def clean_transcript(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if not text or _HALLUCINATIONS.search(text):
        return ""
    if re.fullmatch(r"[\W_]+", text):
        return ""
    return collapse_repetitions(text)


class STTProvider(ABC):
    name: str = "base"
    #: Nhà cung cấp gửi audio ra ngoài máy (hiện cảnh báo quyền riêng tư, NFR-07)
    is_cloud: bool = False

    @property
    @abstractmethod
    def ready(self) -> bool: ...

    @abstractmethod
    async def prepare(self, progress: ProgressCallback | None = None) -> None:
        """Tải/nạp model hoặc kiểm tra cấu hình. Có thể mất nhiều phút (tải model)."""

    @abstractmethod
    async def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str = "it",
        prompt: str | None = None,
        keywords: list[str] | None = None,
    ) -> STTResult:
        """Nhận dạng một utterance float32 16 kHz mono."""

    def info(self) -> dict[str, Any]:
        return {"provider": self.name, "cloud": self.is_cloud}

    async def close(self) -> None:  # pragma: no cover - mặc định không cần dọn
        return None
