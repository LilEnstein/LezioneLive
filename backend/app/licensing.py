"""Kích hoạt bản quyền LezioneLive — created by Turin.

Mỗi người dùng được Turin cấp một mã riêng theo email: 6 chữ số PIN trộn ngẫu nhiên với chữ "turin"
(ví dụ ``4TU82RI7N19``). Repo chỉ chứa ``licenses.json`` gồm SHA-256 của email và scrypt(email|mã),
không chứa email hay mã gốc. Cấp/thu hồi mã bằng ``scripts/issue_license.py``.

Chưa kích hoạt → mọi ``/api/*`` và ``/ws/*`` (trừ ``/api/license/*``) trả 403 ``LICENSE_REQUIRED``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import REPO_ROOT

log = logging.getLogger(__name__)

AUTHOR = "Turin"
CONTACT_EMAIL = "brolai1204@gmail.com"
REGISTRY_PATH = REPO_ROOT / "licenses.json"
WORD = "turin"
PIN_DIGITS = 6
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}

MAX_FAILURES = 5
LOCKOUT_SECONDS = 60


def normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_code(code: str) -> str:
    return re.sub(r"[\s\-_.]", "", code).lower()


def email_hash(email: str) -> str:
    return hashlib.sha256(normalize_email(email).encode()).hexdigest()


def code_hash(email: str, code: str, salt: bytes) -> str:
    secret = f"{normalize_email(email)}|{normalize_code(code)}".encode()
    return hashlib.scrypt(secret, salt=salt, **SCRYPT).hex()


def make_code(rng: secrets.SystemRandom | None = None) -> str:
    """PIN 6 số trộn với "turin": giữ thứ tự từng chuỗi, vị trí xen kẽ ngẫu nhiên cho mỗi email."""
    rng = rng or secrets.SystemRandom()
    pin = [str(rng.randrange(10)) for _ in range(PIN_DIGITS)]
    letters = list(WORD)
    slots = set(rng.sample(range(len(pin) + len(letters)), len(letters)))
    out = [letters.pop(0) if i in slots else pin.pop(0) for i in range(PIN_DIGITS + len(WORD))]
    return "".join(out).upper()


def is_well_formed(code: str) -> bool:
    c = normalize_code(code)
    return len(c) == PIN_DIGITS + len(WORD) and "".join(ch for ch in c if ch.isalpha()) == WORD and sum(
        ch.isdigit() for ch in c
    ) == PIN_DIGITS


def load_registry(path: Path | None = None) -> dict[str, Any]:
    path = path or REGISTRY_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"issuer": AUTHOR, "licenses": []}


def verify(email: str, code: str, registry: dict[str, Any]) -> bool:
    if not email.strip() or not is_well_formed(code):
        return False
    target = email_hash(email)
    for entry in registry.get("licenses", []):
        if entry.get("revoked") or not hmac.compare_digest(entry.get("email_hash", ""), target):
            continue
        if hmac.compare_digest(code_hash(email, code, bytes.fromhex(entry["salt"])), entry["hash"]):
            return True
    return False


def mask_email(email: str) -> str:
    name, _, domain = normalize_email(email).partition("@")
    return f"{name[:2]}***@{domain}" if domain else "***"


@dataclass
class LicenseState:
    active: bool = False
    email: str | None = None


class LicenseGate:
    """Trạng thái kích hoạt của máy này (lưu ở ``<data_dir>/license.json``)."""

    def __init__(self, state_path: Path, registry_path: Path | None = None) -> None:
        self.state_path = state_path
        self.registry_path = registry_path
        self.state = LicenseState()
        self._failures = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return
        email, code = saved.get("email", ""), saved.get("code", "")
        if verify(email, code, load_registry(self.registry_path)):
            self.state = LicenseState(active=True, email=normalize_email(email))
        else:
            log.warning("Mã kích hoạt đã lưu không còn hợp lệ (bị thu hồi hoặc sai), cần kích hoạt lại")

    def activate(self, email: str, code: str) -> bool:
        with self._lock:
            now = time.monotonic()
            if now < self._locked_until:
                raise HTTPException(
                    429,
                    {"code": "LICENSE_LOCKED", "message": f"Nhập sai quá nhiều lần, thử lại sau {int(self._locked_until - now) + 1} giây"},
                )
            if not verify(email, code, load_registry(self.registry_path)):
                self._failures += 1
                if self._failures >= MAX_FAILURES:
                    self._failures = 0
                    self._locked_until = now + LOCKOUT_SECONDS
                return False
            self._failures = 0
            self.state = LicenseState(active=True, email=normalize_email(email))
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"email": normalize_email(email), "code": normalize_code(code)}), encoding="utf-8"
            )
            return True

    def status(self) -> dict[str, Any]:
        return {
            "active": self.state.active,
            "email": mask_email(self.state.email) if self.state.email else None,
            "author": AUTHOR,
            "contact": CONTACT_EMAIL,
        }


class LicenseMiddleware:
    """Chặn API/WebSocket khi máy chưa kích hoạt."""

    OPEN_PREFIXES = ("/api/license",)

    def __init__(self, app: ASGIApp, gate: LicenseGate) -> None:
        self.app = app
        self.gate = gate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        guarded = scope["type"] in ("http", "websocket") and path.startswith(("/api/", "/ws/"))
        if not guarded or self.gate.state.active or path.startswith(self.OPEN_PREFIXES):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4403, "reason": "LICENSE_REQUIRED"})
            return
        response = JSONResponse(
            {"detail": {"code": "LICENSE_REQUIRED", "message": f"LezioneLive chưa được kích hoạt. Liên hệ {AUTHOR} ({CONTACT_EMAIL}) để nhận mã."}},
            status_code=403,
        )
        await response(scope, receive, send)


router = APIRouter(prefix="/license", tags=["license"])


class ActivateBody(BaseModel):
    email: str
    code: str


def _gate(request: Request) -> LicenseGate:
    return request.app.state.license


@router.get("/status")
def license_status(request: Request) -> dict[str, Any]:
    return _gate(request).status()


@router.post("/activate")
def license_activate(body: ActivateBody, request: Request) -> dict[str, Any]:
    gate = _gate(request)
    if not gate.activate(body.email, body.code):
        raise HTTPException(
            400, {"code": "LICENSE_INVALID", "message": "Email hoặc mã kích hoạt không đúng (hoặc đã bị thu hồi)"}
        )
    log.info("Đã kích hoạt LezioneLive cho %s", mask_email(body.email))
    return gate.status()
