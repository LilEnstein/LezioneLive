"""Fixture dùng chung: keyring trong bộ nhớ, LLM/STT giả, app FastAPI với dữ liệu tạm."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import keyring
import numpy as np
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from app.config import EnvSettings
from app.context import AppContext, build_context
from app.llm.client import LLMError, LLMResult, TokenUsage
from app.pipeline.connectivity import ConnectivityMonitor
from app.security import SecretStore
from app.stt.base import STTProvider, STTResult

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Keyring
# ---------------------------------------------------------------------------


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.store:
            raise PasswordDeleteError("missing")
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def no_real_audio_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test không bao giờ mở micro / loa thật của máy (nguồn dự phòng, sound check...)."""
    from app.audio import loopback
    from app.routers import sessions as sessions_router

    monkeypatch.setattr(loopback.LoopbackCapture, "start", lambda self: None)
    monkeypatch.setattr(sessions_router, "probe_microphone", lambda device_id, timeout=3.0: "Fake microphone")


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch: pytest.MonkeyPatch) -> Iterator[MemoryKeyring]:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    previous = keyring.get_keyring()
    ring = MemoryKeyring()
    keyring.set_keyring(ring)
    yield ring
    keyring.set_keyring(previous)


# ---------------------------------------------------------------------------
# LLM giả
# ---------------------------------------------------------------------------


@dataclass
class LLMCall:
    purpose: str
    model: str
    system: Any
    messages: list[dict[str, Any]]
    kwargs: dict[str, Any] = field(default_factory=dict)


Responder = Callable[[LLMCall], "str | dict | list | Exception"]


class FakeLLM:
    """Thay thế ``LLMClient``: trả lời theo hàm ``responder`` và ghi usage như thật."""

    def __init__(self, responder: Responder | None = None, *, has_key: bool = True, delay: float = 0.0) -> None:
        self.responder = responder or default_responder
        self._has_key = has_key
        self.delay = delay
        self.calls: list[LLMCall] = []
        self.usage = None
        self.valid_keys: set[str] = set()
        self.active = 0
        self.max_active = 0

    @property
    def has_key(self) -> bool:
        return self._has_key

    def set_has_key(self, value: bool) -> None:
        self._has_key = value

    def reload_key(self) -> None:
        self._has_key = True

    async def validate_key(self, key: str, model: str) -> TokenUsage:
        if key not in self.valid_keys:
            raise LLMError("auth", "API key không hợp lệ", status=401)
        return TokenUsage(input_tokens=8, output_tokens=1)

    async def _respond(self, call: LLMCall) -> Any:
        self.calls.append(call)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            out = self.responder(call)
            if isinstance(out, Exception):
                raise out
            return out
        finally:
            self.active -= 1

    def _record(self, call: LLMCall, session_id: int | None, text: str) -> TokenUsage:
        usage = TokenUsage(input_tokens=100 + len(json.dumps(call.messages, ensure_ascii=False)) // 4,
                           output_tokens=max(1, len(text) // 4))
        if self.usage is not None:
            self.usage.record(session_id=session_id, purpose=call.purpose, model=call.model, usage=usage)
        return usage

    async def stream(self, *, purpose: str, session_id: int | None, model: str, max_tokens: int,
                     messages: list[dict[str, Any]], system: Any = None, on_text=None, **kwargs: Any) -> LLMResult:
        if not self._has_key:
            raise LLMError("no_key", "Chưa có key")
        call = LLMCall(purpose, model, system, messages, {"max_tokens": max_tokens, **kwargs})
        out = await self._respond(call)
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        if on_text is not None:
            for i in range(0, len(text), 7):
                on_text(text[i : i + 7])
        usage = self._record(call, session_id, text)
        return LLMResult(text=text, model=model, stop_reason="end_turn", usage=usage)

    async def json(self, *, purpose: str, session_id: int | None, model: str, max_tokens: int,
                   messages: list[dict[str, Any]], schema: dict, system: Any = None, **kwargs: Any):
        result = await self.stream(purpose=purpose, session_id=session_id, model=model, max_tokens=max_tokens,
                                   messages=messages, system=system, schema=schema, **kwargs)
        return json.loads(result.text), result

    async def count_tokens(self, *, model: str, messages: list[dict[str, Any]], system: Any = None) -> int:
        return len(json.dumps(messages, ensure_ascii=False)) // 3

    async def upload_file(self, filename: str, data: bytes, mime: str) -> str:
        return "file_test_123"

    async def delete_file(self, file_id: str) -> None:
        return None


def last_user_text(call: LLMCall) -> str:
    content = call.messages[-1]["content"]
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


SUMMARY_MD = """## Tổng quan buổi học
Buổi học về hồi quy logistic.

## Ý chính
- [00:01] Giới thiệu.

## Khái niệm & định nghĩa
| IT | VI | EN | Giải thích |
|---|---|---|---|
| regressione logistica | hồi quy logistic | logistic regression | mô hình |

## Công thức
$\\beta_0 + \\beta_1 x$

## Bài tập / Deadline / Thông báo hành chính
- "entro venerdì" → trước thứ Sáu.

## Những đoạn cần xem lại
Không có.

## Câu hỏi gợi ý để hỏi giảng viên
- Professore, ...
"""


def default_responder(call: LLMCall) -> Any:
    if call.purpose == "translate":
        if "schema" in call.kwargs:  # dịch gộp
            text = last_user_text(call)
            import re

            nums = [int(n) for n in re.findall(r"^\[(\d+)\]", text, flags=re.M)]
            return {"items": [{"n": n, "vi": f"bản dịch {n}"} for n in nums]}
        return "VI: " + last_user_text(call)
    if call.purpose == "chat":
        return "Giảng viên nói về hồi quy logistic [00:01]."
    if call.purpose == "compose":
        return {"question_it": "Professore, potrebbe ripetere?", "back_translation_vi": "Thầy có thể nhắc lại?",
                "notes": ""}
    if call.purpose == "summary":
        return SUMMARY_MD
    if call.purpose == "flashcard":
        return {"cards": [
            {"front_it": f"parola{i}", "back_vi": f"từ {i}", "pos": "danh từ",
             "example_it": f"frase {i}", "example_vi": f"câu {i}"} for i in range(25)
        ]}
    if call.purpose == "slides":
        return {"summary": "Slide về hồi quy.", "terms": [
            {"term_it": "verosimiglianza", "term_vi": "hợp lý (likelihood)", "term_en": "likelihood", "note": ""}
        ]}
    if call.purpose == "rolling_summary":
        return "Tóm tắt khối [00:00] ..."
    if call.purpose == "board":
        return {
            "text_it": "Varianza campionaria\ns^2 = 1/(n-1) somma (x_i - media)^2",
            "latex": ["s^2 = \\frac{1}{n-1}\\sum_{i=1}^{n}(x_i-\\bar{x})^2"],
            "terms": [
                {"it": "varianza campionaria", "vi": "phương sai mẫu", "en": "sample variance"},
                {"it": "media", "vi": "trung bình", "en": "mean"},
            ],
            "translation_vi": "Phương sai mẫu",
            "unreadable": "",
        }
    if call.purpose == "consent":
        return {
            "subject_it": "Richiesta di utilizzo di sottotitoli",
            "email_it": "Gentile Professore, sono uno studente internazionale... non registrerò l'audio e non "
                        "condividerò i contenuti. Cordiali saluti",
            "back_translation_vi": "Kính gửi Giáo sư, em là sinh viên quốc tế... không lưu âm thanh, không chia sẻ.",
        }
    if call.purpose == "speak":
        return {
            "question_it": "Professore, scusi, può ripetere l'ultima formula?",
            "pronunciation": "pro-fes-SO-re, SCU-si, PUÒ ri-PE-te-re l'UL-ti-ma FOR-mu-la?",
            "back_translation_vi": "Thưa thầy, xin lỗi, thầy có thể nhắc lại công thức cuối không?",
            "tip": "",
        }
    return "ok"


# ---------------------------------------------------------------------------
# STT giả
# ---------------------------------------------------------------------------


class FakeSTTProvider(STTProvider):
    name = "fake"

    def __init__(self) -> None:
        self.count = 0
        self.prompts: list[str | None] = []
        self._ready = True

    @property
    def ready(self) -> bool:
        return self._ready

    async def prepare(self, progress=None) -> None:
        self._ready = True

    async def transcribe(self, audio: np.ndarray, *, language: str = "it", prompt=None, keywords=None) -> STTResult:
        self.count += 1
        self.prompts.append(prompt)
        return STTResult(text=f"frase numero {self.count}", confidence=0.9 if self.count % 2 else 0.4)


class FakeSTTManager:
    def __init__(self, provider: STTProvider | None = None) -> None:
        self.provider = provider or FakeSTTProvider()
        self.on_warning = None
        self.error = None
        self.saver_sessions: set[int] = set()

    def set_power_saver(self, session_id: int, enabled: bool) -> str | None:
        if enabled:
            self.saver_sessions.add(session_id)
        else:
            self.saver_sessions.discard(session_id)
        return None

    @property
    def ready(self) -> bool:
        return self.provider.ready

    async def prepare(self) -> bool:
        await self.provider.prepare()
        return True

    def start_prepare(self):
        return None

    def autoload(self) -> None:
        return None

    def model_cached(self, model: str | None = None) -> bool | None:
        return True

    def gpu(self) -> dict:
        return {"available": False, "name": None, "vram_mb": None}

    def status(self) -> dict:
        return {"provider": "fake", "ready": self.ready, "state": "ready" if self.ready else "idle",
                "progress": 1.0, "message": "", "error": None, "cloud": False}

    async def close(self) -> None:
        return None


class FakeSpeakerModels:
    """Không tải model ONNX trong test: dùng embedding phổ (hoặc embedder test gán vào)."""

    def __init__(self, embedder=None) -> None:
        from app.audio.speaker_tagger import SpectralEmbedder

        self._embedder = embedder or SpectralEmbedder()
        self.prepared = 0

    def embedder(self):
        return self._embedder

    def cached(self) -> bool:
        return True

    def start_prepare(self):
        self.prepared += 1
        return None

    def status(self) -> dict:
        return {"model": "fake", "cached": True, "state": "ready", "error": None, "size_mb": 0}


@dataclass
class FakeBattery:
    percent: float = 100.0
    plugged: bool = True

    def __call__(self):
        from app.services.power import BatteryReading

        return BatteryReading(self.percent, self.plugged, None)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def make_env(tmp_path: Path) -> EnvSettings:
    return EnvSettings(
        data_dir=tmp_path / "data",
        frontend_dist=tmp_path / "no-frontend",
        disable_stt_autoload=True,
        dev_origins=["http://localhost:5173"],
        companion_host="127.0.0.1",  # không mở cổng ra mạng (và không bật hộp thoại tường lửa) khi test
        companion_port=0,
    )


@dataclass
class Harness:
    env: EnvSettings
    llm: FakeLLM
    stt: FakeSTTManager
    connectivity: ConnectivityMonitor
    online: dict[str, bool]
    battery: FakeBattery = field(default_factory=FakeBattery)
    speaker_models: FakeSpeakerModels = field(default_factory=FakeSpeakerModels)


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    online = {"value": True}

    async def check() -> bool:
        return online["value"]

    return Harness(
        env=make_env(tmp_path),
        llm=FakeLLM(),
        stt=FakeSTTManager(),
        connectivity=ConnectivityMonitor(check=check, interval=0.05, watch_interval=0.1),
        online=online,
    )


def context_factory(h: Harness) -> Callable[[EnvSettings], AppContext]:
    def factory(env: EnvSettings) -> AppContext:
        from app.services.power import BatteryMonitor

        return build_context(env, secrets=SecretStore(), llm=h.llm, connectivity=h.connectivity,
                             stt=h.stt, power=BatteryMonitor(reader=h.battery),  # type: ignore[arg-type]
                             speaker_models=h.speaker_models)  # type: ignore[arg-type]

    return factory


@pytest.fixture
def client(harness: Harness):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(harness.env, context_factory=context_factory(harness), require_license=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        c.ctx = app.state.ctx  # type: ignore[attr-defined]
        yield c


@pytest.fixture
async def actx(harness: Harness) -> AppContext:
    """AppContext chạy trong event loop của test (không qua HTTP)."""
    ctx = context_factory(harness)(harness.env)
    yield ctx
    await ctx.shutdown()


def tone(duration_s: float, freq: float = 220.0, amp: float = 0.3, sr: int = 16000) -> np.ndarray:
    t = np.arange(int(duration_s * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(duration_s: float, sr: int = 16000) -> np.ndarray:
    return np.zeros(int(duration_s * sr), dtype=np.float32)


def make_pdf(pages: list[str]) -> bytes:
    """PDF tối giản có text (Helvetica) để test trích xuất."""
    objects: list[bytes] = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for i, text in enumerate(pages):
        content_id = 4 + 2 * i
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 18 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
