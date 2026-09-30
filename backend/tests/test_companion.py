"""Điện thoại đồng hành (IC-FR-02/03, IC-NFR-06, Flow B, IC-AC-03/04)."""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import ssl
import time

import httpx
import pytest
from cryptography import x509

from app.companion.pairing import PairingManager, qr_svg
from app.companion.tls import CertManager, lan_addresses
from app.db.session import make_engine

from .test_api import wait_for
from .test_ws_pipeline import WS_HEADERS, collect, speech_frames

LOCAL = "http://127.0.0.1:8765"


def consented_course(client) -> int:
    course = client.post("/api/courses", json={"name": "Statistica", "lecturer": "Prof. Rossi"}).json()
    client.post(f"/api/courses/{course['id']}/consent", json={"confirmed": True})
    return course["id"]


# ---------------------------------------------------------------------------
# Ghép nối & chứng chỉ (đơn vị)
# ---------------------------------------------------------------------------


def test_pairing_token_is_one_time_and_expires(tmp_path):
    from app.db.migrations import run_migrations

    engine = make_engine(tmp_path / "t.db")
    run_migrations(engine)
    pm = PairingManager(engine, ttl_s=0.2)
    tok = pm.create()
    assert tok.token.startswith("p_") and len(tok.token) > 30
    assert pm.consume(tok.token)[0] == "ok"
    assert pm.consume(tok.token)[0] == "used"  # IC-AC-03: token đã dùng bị từ chối
    old = pm.create()
    time.sleep(0.3)
    assert pm.consume(old.token)[0] == "expired"  # quá hạn
    assert pm.consume("p_nope")[0] == "invalid"

    device, secret = pm.register_device("Pixel 8")
    assert pm.authenticate(secret).id == device.id
    assert pm.authenticate("d_wrong") is None
    assert device.token_hash != secret  # chỉ lưu hash
    assert pm.revoke(device.id)
    assert pm.authenticate(secret) is None
    engine.dispose()


def test_qr_svg_contains_path():
    svg = qr_svg("https://192.168.1.20:8766/c?t=p_abc")
    assert svg.lstrip().startswith("<?xml") or "<svg" in svg
    assert "<path" in svg


def test_self_signed_ca_and_server_cert(tmp_path):
    certs = CertManager(tmp_path / "companion")
    cert_path, key_path = certs.ensure(["192.168.1.20"])
    chain = x509.load_pem_x509_certificates(cert_path.read_bytes())
    leaf, ca = chain[0], chain[1]
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
    assert {"192.168.1.20", "127.0.0.1"} <= ips
    assert leaf.issuer == ca.subject
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert leaf.not_valid_after_utc - leaf.not_valid_before_utc <= dt.timedelta(days=398)  # iOS
    ca.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes, _ecdsa(leaf))
    # Cùng IP → giữ nguyên; IP mới (bật hotspot) → tạo lại chứng chỉ server, CA giữ nguyên.
    mtime = cert_path.stat().st_mtime_ns
    certs.ensure(["192.168.1.20"])
    assert cert_path.stat().st_mtime_ns == mtime
    certs.ensure(["192.168.137.1"])
    leaf2 = x509.load_pem_x509_certificates(cert_path.read_bytes())[0]
    assert "192.168.137.1" in {str(i) for i in leaf2.extensions.get_extension_for_class(
        x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)}
    assert x509.load_pem_x509_certificate(certs.ca_pem()).subject == ca.subject
    assert len(certs.ca_fingerprint().split(":")) == 32


def _ecdsa(cert):
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.ECDSA(cert.signature_hash_algorithm)


def test_lan_addresses_are_private_ipv4():
    for a in lan_addresses():
        ip = ipaddress.ip_address(a.ip)
        assert ip.version == 4 and ip.is_private and not ip.is_loopback


# ---------------------------------------------------------------------------
# Luồng đầy đủ qua API
# ---------------------------------------------------------------------------


def pair(client, **body) -> dict:
    r = client.post("/api/companion/pair", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def phone_ws(client, token: str):
    return client.websocket_connect(f"ws://127.0.0.1:8765/ws/companion/{token}", headers={"origin": LOCAL})


def test_pair_flow_one_time_token_and_device_reconnect(client):
    info = pair(client)
    assert info["url"].startswith("https://") and "/c?t=p_" in info["url"]
    assert info["expires_in"] <= 300 and "<path" in info["qr_svg"]
    status = client.get("/api/companion/status").json()
    assert status["running"] and status["port"] == info["port"]

    with phone_ws(client, info["token"]) as ws:
        paired = ws.receive_json()
        assert paired["type"] == "paired" and paired["device_token"].startswith("d_")
        ws.send_json({"type": "hello", "name": "iPhone 15", "mode": "subtitle"})
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and snap["session"] is None
        ws.send_json({"type": "ping", "t": 123})
        assert ws.receive_json() == {"type": "pong", "t": 123}
    device_token = paired["device_token"]

    # IC-E-08: dùng lại QR → bị từ chối với thông báo rõ ràng
    with phone_ws(client, info["token"]) as ws:
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "TOKEN_EXPIRED"
        assert "hết hạn" in err["message"]

    # Kết nối lại bằng token thiết bị (không cần QR)
    with phone_ws(client, device_token) as ws:
        assert ws.receive_json()["type"] == "welcome"
    devices = client.get("/api/companion/devices").json()
    assert devices[0]["name"] == "iPhone 15"

    # Thu hồi thiết bị → không kết nối được nữa
    assert client.delete(f"/api/companion/devices/{devices[0]['id']}").json() == {"revoked": True}
    with phone_ws(client, device_token) as ws:
        assert ws.receive_json()["code"] == "TOKEN_INVALID"
    r = client.post("/api/companion/pins", json={}, headers={"Authorization": f"Bearer {device_token}"})
    assert r.status_code == 401


def test_new_network_regenerates_certificate_and_restarts(client):
    """Bật hotspot (IP mới) → mã QR trỏ tới IP đó và chứng chỉ server chứa IP đó."""
    pair(client)
    certs = client.ctx.companion_server.certs
    assert not certs.covers(["10.42.0.1"])
    info = pair(client, ip="10.42.0.1")
    assert info["ip"] == "10.42.0.1" and info["url"].startswith("https://10.42.0.1:")
    assert certs.covers(["10.42.0.1"])
    assert client.get("/api/companion/status").json()["running"]


def test_real_tls_server_accepts_wss_with_local_ca(client):
    from websockets.sync.client import connect

    info = pair(client)
    ca_path = client.ctx.companion_server.certs.ca_cert
    ctx = ssl.create_default_context(cafile=str(ca_path))
    port = info["port"]
    # Trang đồng hành qua HTTPS (chưa build frontend trong môi trường test → 503, nhưng TLS hợp lệ)
    r = httpx.get(f"https://127.0.0.1:{port}/c", verify=ctx)
    assert r.status_code in (200, 503)
    assert r.headers["x-content-type-options"] == "nosniff"
    assert httpx.get(f"https://127.0.0.1:{port}/api/health", verify=ctx).status_code == 404  # API laptop không lộ ra LAN
    ca = httpx.get(f"https://127.0.0.1:{port}/ca.crt", verify=ctx)
    assert ca.status_code == 200 and b"BEGIN CERTIFICATE" in ca.content
    with connect(f"wss://127.0.0.1:{port}/ws/companion/{info['token']}", ssl=ctx,
                 additional_headers={"Origin": f"https://127.0.0.1:{port}"}) as ws:
        assert json.loads(ws.recv(timeout=5))["type"] == "paired"
    with pytest.raises(Exception):  # noqa: B017 - không tin CA → bắt tay TLS thất bại
        httpx.get(f"https://127.0.0.1:{port}/c", verify=True)
    client.post("/api/companion/stop")
    assert not client.get("/api/companion/status").json()["running"]


def test_phone_mic_streams_audio_into_inclass_session(client):
    """IC-US-03/04: điện thoại làm micro; điện thoại khác xem phụ đề; ghim từ điện thoại hiện trên laptop."""
    course_id = consented_course(client)
    r = client.post("/api/sessions", json={"course_id": course_id, "audio_mode": "mic_companion", "title": "Aula 5"})
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    assert client.get("/api/companion/status").json()["target_session_id"] == sid

    mic_info = pair(client)
    sub_info = pair(client)
    with client.websocket_connect(f"ws://127.0.0.1:8765/ws/session/{sid}", headers=WS_HEADERS) as laptop, \
            phone_ws(client, mic_info["token"]) as mic, phone_ws(client, sub_info["token"]) as sub:
        laptop.receive_json()
        mic.receive_json()
        sub_paired = sub.receive_json()
        mic.send_json({"type": "hello", "name": "Pixel", "mode": "mic"})
        sub.send_json({"type": "hello", "name": "iPhone", "mode": "subtitle"})
        assert mic.receive_json()["type"] == "snapshot"
        snap = sub.receive_json()
        assert snap["type"] == "snapshot" and snap["session"]["id"] == sid and snap["session"]["inclass"]
        for frame in speech_frames():
            mic.send_bytes(frame)
        for _ in range(20):
            mic.send_bytes(bytes(3200))

        events = collect(laptop, lambda evs: sum(e["type"] == "segment.transcribed" for e in evs) >= 3, timeout=30)
        types = {e["type"] for e in events}
        assert "companion.connected" in types or "companion.mic" in types
        sub_events = collect(sub, lambda evs: sum(e["type"] == "segment.transcribed" for e in evs) >= 3, timeout=30)
        assert all(e["type"] != "level" for e in sub_events)  # phụ đề: không gửi VU 10 Hz

        # 📌 trên điện thoại → ghim xuất hiện trên laptop (IC-AC-04)
        r = client.post("/api/companion/pins", json={},
                        headers={"Authorization": f"Bearer {sub_paired['device_token']}"})
        assert r.status_code == 201
        pin_ev = collect(laptop, lambda evs: any(e["type"] == "pin.created" for e in evs))
        assert any(e["type"] == "pin.created" for e in pin_ev)
        status = client.get("/api/companion/status").json()
        assert status["active_mic"]["name"] == "Pixel"
        assert {d["mode"] for d in status["devices"]} == {"mic", "subtitle"}

    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["session"]["capture_source"] == "mic_companion"
    assert len(detail["segments"]) >= 3
    assert all(s["snr_db"] is None or s["snr_db"] >= 0 for s in detail["segments"])
    wait_for(lambda: all(s["status"] == "translated" for s in client.get(f"/api/sessions/{sid}").json()["segments"]))
