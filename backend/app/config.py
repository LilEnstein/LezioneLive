"""Cấu hình ứng dụng.

- ``EnvSettings``: tham số khởi động (host, port, thư mục dữ liệu...) đọc từ biến môi trường / ``.env``.
- ``AppSettings``: cài đặt người dùng chỉnh qua API ``/settings`` (mục 13.1 của spec), lưu trong SQLite.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Literal

from platformdirs import user_data_dir
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "LezioneLive"
VERSION = "1.0.0"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


def _default_data_dir() -> Path:
    return Path(user_data_dir(APP_NAME, appauthor=False))


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEZIONELIVE_",
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = Field(default_factory=_default_data_dir)
    dev_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"
    log_level: str = "INFO"
    chrome_path: str | None = None
    disable_stt_autoload: bool = False
    # Server HTTPS/WSS cho điện thoại đồng hành (chế độ lớp học, mục 6.4 feature_spec). Chỉ mở khi
    # người dùng bật ghép nối điện thoại; mọi kết nối phải có token hợp lệ.
    companion_host: str = "0.0.0.0"
    companion_port: int = 8766

    @property
    def is_loopback(self) -> bool:
        if self.host in ("localhost",):
            return True
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return False

    @property
    def allowed_origins(self) -> list[str]:
        own = [f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"]
        return list(dict.fromkeys(own + list(self.dev_origins)))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lezionelive.db"


# ---------------------------------------------------------------------------
# Bảng giá (USD / 1 triệu token). Người dùng tự cập nhật theo trang giá Anthropic.
# Giá trị mặc định lấy theo bảng giá công bố (cache 5 phút = 1.25x, 1 giờ = 2x, đọc cache ~0.1x).
# ---------------------------------------------------------------------------


class ModelPricing(BaseModel):
    input: float = Field(ge=0, description="Input không cache")
    output: float = Field(ge=0)
    cache_write_5m: float = Field(ge=0)
    cache_write_1h: float = Field(ge=0)
    cache_read: float = Field(ge=0)


def _p(inp: float, out: float, cache_read: float | None = None) -> ModelPricing:
    return ModelPricing(
        input=inp,
        output=out,
        cache_write_5m=round(inp * 1.25, 4),
        cache_write_1h=round(inp * 2.0, 4),
        cache_read=round(inp * 0.1, 4) if cache_read is None else cache_read,
    )


DEFAULT_PRICING: dict[str, ModelPricing] = {
    "claude-haiku-4-5": _p(1.0, 5.0),
    "claude-sonnet-5": _p(2.0, 10.0),
    "claude-sonnet-4-6": _p(3.0, 15.0),
    "claude-opus-5": _p(5.0, 25.0),
    "claude-opus-5-5": _p(4.0, 20.0, cache_read=0.20),
    "claude-opus-4-8": _p(5.0, 25.0),
    "claude-fable-5-1": _p(10.0, 50.0, cache_read=0.25),
}

TRANSLATE_MODEL_CHOICES = ["claude-haiku-4-5-20251001", "claude-sonnet-5"]
ASSISTANT_MODEL_CHOICES = ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5-20251001"]
# Model chọn được khi dùng gói Pro/Max qua Claude Code (alias → CLI tự chọn bản mới nhất).
CLAUDE_CODE_MODEL_CHOICES = ["sonnet", "opus", "haiku", "fable"]
WHISPER_MODEL_CHOICES = ["large-v3-turbo", "medium", "small", "base", "tiny", "large-v3"]

# Chế độ lớp học trực tiếp (feature_spec.md)
DenoiseLevel = Literal["off", "light", "strong"]
CaptureSource = Literal["tab", "loopback", "mic_local", "mic_companion"]
INCLASS_SOURCES = ("mic_local", "mic_companion")
BATTERY_SAVER_WHISPER_MODEL = "small"  # IC-FR-70
SPEAKER_MODEL_REPO = "Wespeaker/wespeaker-voxceleb-resnet34-LM"
SPEAKER_MODEL_FILE = "voxceleb_resnet34_LM.onnx"
# Model dịch cục bộ NLLB-200 (CTranslate2 int8, chạy CPU): dịch nháp khi mất mạng / hết hạn mức Claude, hoặc
# làm nguồn dịch chính miễn phí (translate_provider = "local").
LOCAL_MODEL_CHOICES: list[dict[str, str | int]] = [
    {"repo": "JustFrederik/nllb-200-distilled-600M-ct2-int8", "label": "NLLB 600M", "size_mb": 620, "tier": "fast"},
    {"repo": "OpenNMT/nllb-200-distilled-1.3B-ct2-int8", "label": "NLLB 1.3B", "size_mb": 1400, "tier": "better"},
]
DRAFT_MODEL_REPO = str(LOCAL_MODEL_CHOICES[0]["repo"])


class AppSettings(BaseModel):
    """Cài đặt người dùng (mục 13.1)."""

    translate_model: str = "claude-haiku-4-5-20251001"
    # api = Anthropic API (key + credit); claude_code = gói Claude Pro/Max qua lệnh `claude -p` trên máy;
    # local = model NLLB trên máy (miễn phí, không cần mạng, chất lượng thấp hơn Claude)
    translate_provider: Literal["api", "claude_code", "local"] = "api"
    assistant_model: str = "claude-sonnet-5"
    # Trợ lý học tập (hỏi đáp, tóm tắt, flashcard, soạn câu hỏi): API hoặc gói Pro qua Claude Code
    assistant_provider: Literal["api", "claude_code"] = "api"
    assistant_cc_model: str = "sonnet"  # alias của Claude Code (tự lên bản mới nhất) hoặc tên model đầy đủ
    assistant_effort: Literal["auto", "low", "medium", "high", "xhigh", "max"] = "auto"
    secondary_language: Literal["none", "en"] = "none"
    stt_provider: Literal["whisper_local", "deepgram", "azure"] = "whisper_local"
    whisper_model: str = "large-v3-turbo"
    whisper_device: Literal["auto", "cuda", "cpu"] = "auto"
    whisper_compute_type: str = "auto"
    deepgram_model: str = "nova-2"
    azure_region: str = "westeurope"
    vad_min_ms: int = Field(1500, ge=500, le=5000)
    vad_max_ms: int = Field(12000, ge=3000, le=30000)
    vad_threshold: float = Field(0.5, ge=0.1, le=0.95)
    translate_concurrency: int = Field(3, ge=1, le=8)
    context_segments: int = Field(3, ge=0, le=10)
    cost_threshold_usd: float | None = Field(None, ge=0)
    pricing: dict[str, ModelPricing] = Field(default_factory=lambda: dict(DEFAULT_PRICING))
    save_raw_audio: bool = False
    ui_language: Literal["vi", "en"] = "vi"
    slides_mode: Literal["document", "text"] = "document"
    loopback_device: str | None = None
    onboarding_done: bool = False
    # --- Lớp học trực tiếp (feature_spec.md) ---
    mic_device: str | None = None  # micro laptop mặc định (None = micro mặc định của hệ điều hành)
    denoise_default: DenoiseLevel = "light"  # IC-FR-11
    inclass_vad_threshold: float = Field(0.6, ge=0.1, le=0.95)  # IC-FR-12
    inclass_min_speech_ms: int = Field(800, ge=200, le=3000)  # IC-FR-12: bỏ đoạn < 0,8 s
    speaker_tagging: bool = True  # IC-FR-30/33
    discard_other_speech: bool = False  # mục 11: "Không lưu lời người khác"
    companion_fallback_mic: bool = True  # IC-FR-07
    browser_audio_processing: bool = True  # IC-FR-04: noiseSuppression/AGC của trình duyệt trên điện thoại
    offline_draft: bool = False  # IC-FR-51
    draft_model: str = DRAFT_MODEL_REPO

    @model_validator(mode="after")
    def _check_vad(self) -> AppSettings:
        if self.vad_min_ms >= self.vad_max_ms:
            raise ValueError("vad_min_ms phải nhỏ hơn vad_max_ms")
        return self
