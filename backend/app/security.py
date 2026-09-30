"""Lưu/đọc API key trong keychain của hệ điều hành (mục 13.2, NFR-05).

- Windows: Credential Manager, macOS: Keychain, Linux: Secret Service (qua thư viện ``keyring``).
- Biến môi trường ``ANTHROPIC_API_KEY`` ghi đè keychain (dùng khi phát triển).
- Frontend không bao giờ nhận lại key đầy đủ, chỉ ``last4``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

log = logging.getLogger(__name__)

SERVICE_NAME = "lezionelive"
ANTHROPIC_KEY_NAME = "anthropic_api_key"
DEEPGRAM_KEY_NAME = "deepgram_api_key"
AZURE_KEY_NAME = "azure_speech_key"

STT_KEY_NAMES = {"deepgram": DEEPGRAM_KEY_NAME, "azure": AZURE_KEY_NAME}

KeySource = Literal["env", "keychain"]


class SecretStoreError(RuntimeError):
    pass


class SecretStore:
    def __init__(self, service: str = SERVICE_NAME) -> None:
        self.service = service

    def backend_name(self) -> str:
        try:
            return type(keyring.get_keyring()).__name__
        except Exception:  # pragma: no cover
            return "unknown"

    def get(self, name: str) -> str | None:
        try:
            return keyring.get_password(self.service, name)
        except KeyringError as e:
            log.warning("Không đọc được keychain (%s): %s", name, type(e).__name__)
            return None

    def set(self, name: str, value: str) -> None:
        try:
            keyring.set_password(self.service, name, value)
        except KeyringError as e:
            raise SecretStoreError(
                "Không lưu được vào keychain của hệ điều hành. "
                "Có thể đặt biến môi trường ANTHROPIC_API_KEY thay thế."
            ) from e

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(self.service, name)
        except PasswordDeleteError:
            pass
        except KeyringError as e:
            raise SecretStoreError("Không xoá được key trong keychain.") from e


@dataclass(frozen=True)
class KeyInfo:
    present: bool
    last4: str | None
    source: KeySource | None


def last4(key: str) -> str:
    return key[-4:]


def looks_like_anthropic_key(key: str) -> bool:
    key = key.strip()
    return key.startswith("sk-ant-") and len(key) >= 24 and " " not in key


def resolve_anthropic_key(store: SecretStore) -> tuple[str | None, KeySource | None]:
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env_key:
        return env_key, "env"
    stored = store.get(ANTHROPIC_KEY_NAME)
    if stored:
        return stored, "keychain"
    return None, None


def anthropic_key_info(store: SecretStore) -> KeyInfo:
    key, source = resolve_anthropic_key(store)
    if not key:
        return KeyInfo(False, None, None)
    return KeyInfo(True, last4(key), source)
