"""STT cloud Deepgram (REST pre-recorded, mỗi utterance một request). Audio rời khỏi máy (NFR-07)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import numpy as np

from app.audio.pcm import to_wav_bytes
from app.stt.base import ProgressCallback, STTError, STTProvider, STTResult, clean_transcript

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


def keyword_params(model: str, keywords: list[str]) -> list[tuple[str, str]]:
    if not keywords:
        return []
    if model.startswith("nova-3"):
        return [("keyterm", k) for k in keywords[:50]]
    words: list[str] = []
    for k in keywords:
        for w in k.split():
            w = w.strip(",.;:()").lower()
            if len(w) >= 4 and w not in words:
                words.append(w)
    return [("keywords", f"{w}:2") for w in words[:50]]


class DeepgramProvider(STTProvider):
    name = "deepgram"
    is_cloud = True

    def __init__(self, key_getter: Callable[[], str | None], model: str = "nova-2") -> None:
        self._key_getter = key_getter
        self.model = model
        self._client: httpx.AsyncClient | None = None

    @property
    def ready(self) -> bool:
        return bool(self._key_getter())

    def info(self) -> dict[str, Any]:
        return {"provider": self.name, "cloud": True, "model": self.model}

    async def prepare(self, progress: ProgressCallback | None = None) -> None:
        if not self._key_getter():
            raise STTError("Chưa có Deepgram API key (Cài đặt → STT).", code="no_key")

    async def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str = "it",
        prompt: str | None = None,
        keywords: list[str] | None = None,
    ) -> STTResult:
        key = self._key_getter()
        if not key:
            raise STTError("Chưa có Deepgram API key", code="no_key")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        params: list[tuple[str, str]] = [
            ("model", self.model),
            ("language", language),
            ("punctuate", "true"),
            ("smart_format", "true"),
        ]
        params += keyword_params(self.model, keywords or [])
        try:
            r = await self._client.post(
                DEEPGRAM_URL,
                params=params,
                content=to_wav_bytes(audio),
                headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav"},
            )
        except httpx.HTTPError as e:
            raise STTError(f"Không kết nối được Deepgram: {type(e).__name__}", code="network", retryable=True) from e
        if r.status_code in (401, 403):
            raise STTError("Deepgram API key không hợp lệ", code="auth")
        if r.status_code >= 400:
            raise STTError(
                f"Deepgram lỗi {r.status_code}", retryable=r.status_code == 429 or r.status_code >= 500
            )
        try:
            alt = r.json()["results"]["channels"][0]["alternatives"][0]
        except (KeyError, IndexError, ValueError) as e:
            raise STTError("Phản hồi Deepgram không hợp lệ") from e
        return STTResult(
            text=clean_transcript(alt.get("transcript", "")),
            confidence=float(alt.get("confidence", 0.0) or 0.0),
            language=language,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
