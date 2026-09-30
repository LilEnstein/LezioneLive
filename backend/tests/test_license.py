"""Cổng kích hoạt bản quyền (created by Turin)."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from fastapi.testclient import TestClient

from app import licensing
from app.licensing import code_hash, email_hash, is_well_formed, make_code, verify


def _registry(tmp_path: Path, email: str, code: str, revoked: bool = False) -> Path:
    salt = secrets.token_bytes(16)
    path = tmp_path / "licenses.json"
    entry = {"email_hash": email_hash(email), "salt": salt.hex(), "hash": code_hash(email, code, salt), "revoked": revoked}
    path.write_text(json.dumps({"issuer": "Turin", "licenses": [entry]}), encoding="utf-8")
    return path


def test_code_mixes_pin_and_turin() -> None:
    for _ in range(50):
        code = make_code()
        assert is_well_formed(code)
        assert "".join(c for c in code if c.isalpha()) == "TURIN"
        assert sum(c.isdigit() for c in code) == 6


def test_verify_is_bound_to_email(tmp_path: Path) -> None:
    code = make_code()
    reg = json.loads(_registry(tmp_path, "Ban@Example.com", code).read_text())
    assert verify("  ban@example.com ", code.lower(), reg)
    assert not verify("khac@example.com", code, reg)
    assert not verify("ban@example.com", make_code(), reg)
    reg["licenses"][0]["revoked"] = True
    assert not verify("ban@example.com", code, reg)


def test_api_blocked_until_activated(harness, tmp_path: Path, monkeypatch) -> None:
    from app.main import create_app
    from tests.conftest import context_factory

    code = make_code()
    monkeypatch.setattr(licensing, "REGISTRY_PATH", _registry(tmp_path, "ban@example.com", code))
    app = create_app(harness.env, context_factory=context_factory(harness))
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        r = c.get("/api/health")
        assert r.status_code == 403 and r.json()["detail"]["code"] == "LICENSE_REQUIRED"
        assert r.headers["x-created-by"] == "Turin"
        assert c.get("/api/license/status").json()["active"] is False

        bad = c.post("/api/license/activate", json={"email": "ban@example.com", "code": make_code()})
        assert bad.status_code == 400

        ok = c.post("/api/license/activate", json={"email": "ban@example.com", "code": code})
        assert ok.status_code == 200 and ok.json()["active"] is True
        assert c.get("/api/health").status_code == 200

    # Khởi động lại: đã lưu kích hoạt trong data_dir
    app2 = create_app(harness.env, context_factory=context_factory(harness))
    with TestClient(app2, base_url="http://127.0.0.1:8765") as c:
        assert c.get("/api/health").status_code == 200
