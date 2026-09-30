"""Ghép nối điện thoại bằng mã QR (IC-FR-03, Flow B).

- Token ghép nối: ngẫu nhiên 192 bit, dùng một lần, hết hạn sau 5 phút (IC-E-08).
- Khi ghép thành công, điện thoại nhận một token thiết bị dài hạn (lưu trong trình duyệt điện thoại) để kết
  nối lại mà không cần quét QR. Backend chỉ lưu hash SHA-256 của token thiết bị; thu hồi được bất kỳ lúc nào.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Engine
from sqlmodel import col, select

from app.db.models import CompanionDevice, utcnow
from app.db.session import db_session

PAIR_TTL_S = 300
PAIR_PREFIX = "p_"
DEVICE_PREFIX = "d_"

ConsumeResult = Literal["ok", "expired", "used", "invalid"]


@dataclass
class PairingToken:
    token: str
    created: float
    expires: float
    session_id: int | None = None
    used: bool = False

    @property
    def expires_in(self) -> int:
        return max(0, int(self.expires - time.time()))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def qr_svg(data: str) -> str:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(
        data,
        image_factory=qrcode.image.svg.SvgPathImage,
        box_size=10,
        border=2,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
    )
    svg = img.to_string(encoding="unicode")
    return svg if isinstance(svg, str) else svg.decode("utf-8")


class PairingManager:
    def __init__(self, engine: Engine, ttl_s: float = PAIR_TTL_S) -> None:
        self.engine = engine
        self.ttl_s = ttl_s
        self._tokens: dict[str, PairingToken] = {}

    # --------------------------------------------------------- pairing token
    def create(self, session_id: int | None = None) -> PairingToken:
        now = time.time()
        for tok, item in list(self._tokens.items()):
            if item.expires < now - 3600:
                self._tokens.pop(tok, None)
        token = PAIR_PREFIX + secrets.token_urlsafe(24)
        item = PairingToken(token, now, now + self.ttl_s, session_id)
        self._tokens[token] = item
        return item

    def consume(self, token: str) -> tuple[ConsumeResult, PairingToken | None]:
        item = self._tokens.get(token)
        if item is None:
            return "invalid", None
        if item.used:
            return "used", item
        if time.time() > item.expires:
            return "expired", item
        item.used = True  # dùng một lần
        return "ok", item

    @staticmethod
    def is_pairing_token(token: str) -> bool:
        return token.startswith(PAIR_PREFIX)

    # ----------------------------------------------------------- thiết bị
    def register_device(self, name: str = "Điện thoại") -> tuple[CompanionDevice, str]:
        device_token = DEVICE_PREFIX + secrets.token_urlsafe(32)
        device = CompanionDevice(name=name[:80] or "Điện thoại", token_hash=hash_token(device_token),
                                 last_seen_at=utcnow())
        with db_session(self.engine) as s:
            s.add(device)
            s.commit()
            s.refresh(device)
        return device, device_token

    def authenticate(self, device_token: str) -> CompanionDevice | None:
        if not device_token.startswith(DEVICE_PREFIX):
            return None
        digest = hash_token(device_token)
        with db_session(self.engine) as s:
            device = s.exec(select(CompanionDevice).where(CompanionDevice.token_hash == digest)).first()
            if device is None or device.revoked:
                return None
            if not secrets.compare_digest(device.token_hash, digest):  # pragma: no cover - phòng thủ
                return None
            device.last_seen_at = utcnow()
            s.add(device)
            s.commit()
            s.refresh(device)
        return device

    def rename(self, device_id: int, name: str) -> None:
        with db_session(self.engine) as s:
            device = s.get(CompanionDevice, device_id)
            if device is not None and name:
                device.name = name[:80]
                s.add(device)
                s.commit()

    def touch(self, device_id: int) -> None:
        with db_session(self.engine) as s:
            device = s.get(CompanionDevice, device_id)
            if device is not None:
                device.last_seen_at = utcnow()
                s.add(device)
                s.commit()

    def devices(self, include_revoked: bool = False) -> list[CompanionDevice]:
        with db_session(self.engine) as s:
            stmt = select(CompanionDevice)
            if not include_revoked:
                stmt = stmt.where(col(CompanionDevice.revoked).is_(False))
            return list(s.exec(stmt.order_by(col(CompanionDevice.paired_at).desc())))

    def revoke(self, device_id: int) -> bool:
        with db_session(self.engine) as s:
            device = s.get(CompanionDevice, device_id)
            if device is None:
                return False
            device.revoked = True
            s.add(device)
            s.commit()
        return True
