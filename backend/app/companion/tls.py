"""Chứng chỉ TLS cho server điện thoại đồng hành (IC-NFR-06, mục 6.4).

Trình duyệt điện thoại chỉ cho dùng micro trên HTTPS, nên backend tạo:
- một CA cục bộ (10 năm) — người dùng có thể cài lên điện thoại một lần để không còn cảnh báo;
- một chứng chỉ server do CA ký, SAN gồm mọi IPv4 nội bộ hiện tại + tên máy + localhost. Khi máy đổi mạng
  (IP mới, ví dụ bật hotspot) thì chứng chỉ server được tạo lại, CA giữ nguyên.
Khoá riêng nằm trong thư mục dữ liệu của người dùng, không rời khỏi máy.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import socket
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

CA_DAYS = 3650
LEAF_DAYS = 397  # iOS chỉ tin chứng chỉ server ≤ 398 ngày
RENEW_BEFORE_DAYS = 30


@dataclass
class LanAddress:
    ip: str
    interface: str
    primary: bool
    hotspot: bool

    def as_dict(self) -> dict[str, object]:
        return {"ip": self.ip, "interface": self.interface, "primary": self.primary, "hotspot": self.hotspot}


def primary_ipv4() -> str | None:
    """IP của card mạng có route ra ngoài (không gửi gói tin nào: UDP connect chỉ chọn route)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if ip.startswith("127.") else ip


def lan_addresses() -> list[LanAddress]:
    """IPv4 nội bộ đang hoạt động (Wi-Fi, Ethernet, hotspot của laptop...)."""
    primary = primary_ipv4()
    found: dict[str, LanAddress] = {}
    try:
        import psutil

        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            if name in stats and not stats[name].isup:
                continue
            for a in addrs:
                if a.family != socket.AF_INET:
                    continue
                ip = ipaddress.ip_address(a.address)
                if ip.is_loopback or ip.is_link_local or not ip.is_private:
                    continue
                lowered = name.lower()
                hotspot = a.address.startswith("192.168.137.") or "hotspot" in lowered or "local area connection*" in lowered
                if any(k in lowered for k in ("vmware", "virtualbox", "vethernet", "wsl", "docker", "hyper-v")):
                    continue
                found[a.address] = LanAddress(a.address, name, a.address == primary, hotspot)
    except Exception:  # noqa: BLE001 - psutil lỗi: dùng IP chính
        pass
    if primary and primary not in found:
        found[primary] = LanAddress(primary, "", True, primary.startswith("192.168.137."))
    return sorted(found.values(), key=lambda a: (not a.primary, a.hotspot, a.ip))


class CertManager:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.ca_cert = folder / "ca.pem"
        self.ca_key = folder / "ca_key.pem"
        self.server_cert = folder / "server.pem"
        self.server_key = folder / "server_key.pem"

    # ------------------------------------------------------------------ CA
    def _load_or_create_ca(self):  # type: ignore[no-untyped-def]
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        if self.ca_cert.exists() and self.ca_key.exists():
            cert = x509.load_pem_x509_certificate(self.ca_cert.read_bytes())
            key = serialization.load_pem_private_key(self.ca_key.read_bytes(), password=None)
            if cert.not_valid_after_utc > dt.datetime.now(dt.UTC) + dt.timedelta(days=RENEW_BEFORE_DAYS):
                return cert, key
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, f"LezioneLive Local CA ({socket.gethostname()})"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LezioneLive"),
            ]
        )
        now = dt.datetime.now(dt.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                    key_encipherment=False, data_encipherment=False, key_agreement=False, encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256())
        )
        self.folder.mkdir(parents=True, exist_ok=True)
        self.ca_key.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        self.ca_cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        # CA mới → chứng chỉ server cũ không còn hợp lệ.
        self.server_cert.unlink(missing_ok=True)
        log.info("Đã tạo CA cục bộ cho điện thoại đồng hành")
        return cert, key

    # -------------------------------------------------------------- server
    def covers(self, ips: list[str]) -> bool:
        """Chứng chỉ server hiện tại có hợp lệ cho các IP này không (và chưa sắp hết hạn)."""
        return self._leaf_covers(sorted(set(ips) | {"127.0.0.1"}))

    def _leaf_covers(self, ips: list[str]) -> bool:
        from cryptography import x509

        if not (self.server_cert.exists() and self.server_key.exists()):
            return False
        try:
            cert = x509.load_pem_x509_certificate(self.server_cert.read_bytes())
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            have = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
        except (ValueError, x509.ExtensionNotFound):
            return False
        fresh = cert.not_valid_after_utc > dt.datetime.now(dt.UTC) + dt.timedelta(days=RENEW_BEFORE_DAYS)
        return fresh and set(ips) <= have

    def ensure(self, ips: list[str]) -> tuple[Path, Path]:
        """Bảo đảm có chứng chỉ server hợp lệ cho các IP. Trả về (cert chain, key)."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

        ca_cert, ca_key = self._load_or_create_ca()
        wanted = sorted(set(ips) | {"127.0.0.1"})
        if self._leaf_covers(wanted):
            return self.server_cert, self.server_key
        key = ec.generate_private_key(ec.SECP256R1())
        host = socket.gethostname()
        alt: list[x509.GeneralName] = [x509.DNSName("localhost")]
        if host and host.isascii():
            alt.append(x509.DNSName(host.lower()))
        alt += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in wanted]
        now = dt.datetime.now(dt.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LezioneLive companion")]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=LEAF_DAYS))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, key_encipherment=False, content_commitment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()), critical=False
            )
            .sign(ca_key, hashes.SHA256())
        )
        self.server_key.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        # Chuỗi đầy đủ: server + CA (điện thoại đã cài CA thì tin ngay).
        self.server_cert.write_bytes(
            cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM)
        )
        log.info("Đã tạo chứng chỉ HTTPS cho điện thoại: %s", ", ".join(wanted))
        return self.server_cert, self.server_key

    def ca_pem(self) -> bytes:
        self._load_or_create_ca()
        return self.ca_cert.read_bytes()

    def ca_fingerprint(self) -> str:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes

        cert = x509.load_pem_x509_certificate(self.ca_pem())
        digest = cert.fingerprint(hashes.SHA256()).hex().upper()
        return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))
