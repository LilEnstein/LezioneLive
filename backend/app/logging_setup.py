"""Cấu hình logging có lọc bí mật.

Mọi chuỗi giống API key Anthropic (``sk-ant-...``) bị thay bằng ``[REDACTED_API_KEY]`` ở mức formatter,
nên kể cả traceback cũng không lộ key (mục 13.2 bước 5, AC-01).
"""

from __future__ import annotations

import logging
import logging.config
import re
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]*"),
    # Header xác thực của Deepgram / Azure nếu có lỡ lọt vào log
    re.compile(r"(?i)(authorization:\s*token\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(ocp-apim-subscription-key:\s*)[A-Za-z0-9._\-]+"),
    # Token ghép nối / token thiết bị của điện thoại đồng hành (uvicorn log đường dẫn WebSocket)
    re.compile(r"(/ws/companion/)[A-Za-z0-9._~%\-]+"),
    re.compile(r"([?&]t=)[A-Za-z0-9._~%\-]+"),
    re.compile(r"(?i)(bearer\s+)[dp]_[A-Za-z0-9._\-]+"),
]


def redact(text: str) -> str:
    text = SECRET_PATTERNS[0].sub("[REDACTED_API_KEY]", text)
    for pat in SECRET_PATTERNS[1:]:
        text = pat.sub(lambda m: m.group(1) + "[REDACTED]", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class RedactingFilter(logging.Filter):
    """Lọc thêm ở mức record để handler bên thứ ba (không dùng formatter của ta) cũng an toàn."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - record lỗi định dạng
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg = clean
            record.args = None
        return True


class ProactorNoiseFilter(logging.Filter):
    """Windows: bỏ log ERROR vô hại khi trình duyệt đóng kết nối đột ngột (WinError 10054)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and isinstance(record.exc_info[1], ConnectionResetError):
            return "_call_connection_lost" not in record.getMessage()
        return True


def build_log_config(level: str, log_dir: Path | None) -> dict:
    handlers: dict[str, dict] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "filters": ["redact"],
        }
    }
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "default",
            "filters": ["redact"],
            "filename": str(log_dir / "backend.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf-8",
        }
    names = list(handlers)
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "redact": {"()": "app.logging_setup.RedactingFilter"},
            "proactor_noise": {"()": "app.logging_setup.ProactorNoiseFilter"},
        },
        "formatters": {
            "default": {
                "()": "app.logging_setup.RedactingFormatter",
                "fmt": "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            }
        },
        "handlers": handlers,
        "root": {"level": level.upper(), "handlers": names},
        "loggers": {
            "uvicorn": {"level": level.upper(), "handlers": names, "propagate": False},
            "uvicorn.error": {"level": level.upper(), "handlers": names, "propagate": False},
            "uvicorn.access": {"level": "WARNING", "handlers": names, "propagate": False},
            "asyncio": {"level": "WARNING", "filters": ["proactor_noise"]},
            "httpx": {"level": "WARNING"},
            "httpx2": {"level": "WARNING"},
            "anthropic": {"level": "WARNING"},
            "faster_whisper": {"level": "WARNING"},
        },
    }


def setup_logging(level: str, log_dir: Path | None) -> dict:
    config = build_log_config(level, log_dir)
    logging.config.dictConfig(config)
    return config
