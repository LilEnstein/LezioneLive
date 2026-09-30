"""STT cloud Azure Speech (REST cho audio ngắn ≤ 60 s, ``it-IT``). Audio rời khỏi máy (NFR-07).

REST short-audio không hỗ trợ phrase list, nên ``keywords`` bị bỏ qua.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import numpy as np

from app.audio.pcm import to_wav_bytes
from app.stt.base import ProgressCallback, STTError, STTProvider, STTResult, clean_transcript

LANG_MAP = {"it": "it-IT"}


class AzureSpeechProvider(STTProvider):
    name = "azure"
    is_cloud = True

    def __init__(self, key_getter: Callable[[], str | None], region: str = "westeurope") -> None:
        self._key_getter = key_getter
        self.region = region
        self._client: httpx.AsyncClient | None = None

    @property
    def ready(self) -> bool:
        return bool(self._key_getter())

    def info(self) -> dict[str, Any]:
        return {"provider": self.name, "cloud": True, "region": self.region}

    async def prepare(self, progress: ProgressCallback | None = None) -> None:
        if not self._key_getter():
            raise STTError("Chưa có Azure Speech key (Cài đặt → STT).", code="no_key")

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
            raise STTError("Chưa có Azure Speech key", code="no_key")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        url = (
            f"https://{self.region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"
        )
        try:
            r = await self._client.post(
                url,
                params={"language": LANG_MAP.get(language, language), "format": "detailed", "profanity": "raw"},
                content=to_wav_bytes(audio),
                headers={
                    "Ocp-Apim-Subscription-Key": key,
                    "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as e:
            raise STTError(f"Không kết nối được Azure Speech: {type(e).__name__}", code="network", retryable=True) from e
        if r.status_code in (401, 403):
            raise STTError("Azure Speech key không hợp lệ hoặc sai region", code="auth")
        if r.status_code >= 400:
            raise STTError(f"Azure Speech lỗi {r.status_code}", retryable=r.status_code == 429 or r.status_code >= 500)
        data = r.json()
        status = data.get("RecognitionStatus")
        if status == "Success":
            best = (data.get("NBest") or [{}])[0]
            text = best.get("Display") or data.get("DisplayText", "")
            return STTResult(clean_transcript(text), float(best.get("Confidence", 0.0) or 0.0), language)
        if status in ("NoMatch", "InitialSilenceTimeout", "BabbleTimeout"):
            return STTResult("", 0.0, language)
        raise STTError(f"Azure Speech: {status}")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
