"""Bảo mật API key (NFR-05, mục 13.2, AC-01) và lọc log."""

from __future__ import annotations

import logging

from app.logging_setup import RedactingFilter, redact, setup_logging
from app.security import ANTHROPIC_KEY_NAME, SecretStore, anthropic_key_info, looks_like_anthropic_key

KEY = "sk-ant-api03-Zq9_abc-DEF123456789012345678901234567890"


def test_redact_removes_every_key_fragment():
    text = f"key={KEY} and header x-api-key: {KEY}; bare sk-ant- prefix"
    out = redact(text)
    assert "sk-ant-" not in out
    assert out.count("[REDACTED_API_KEY]") == 3
    assert redact("Authorization: Token dg_secret_123") == "Authorization: Token [REDACTED]"


def test_redact_companion_tokens():
    """Token điện thoại đồng hành không được nằm trong log (uvicorn ghi đường dẫn WebSocket)."""
    line = '127.0.0.1:5 - "WebSocket /ws/companion/d_AbC-123_xyz" [accepted]'
    assert redact(line) == '127.0.0.1:5 - "WebSocket /ws/companion/[REDACTED]" [accepted]'
    assert redact("GET /c?t=p_secret-token&x=1") == "GET /c?t=[REDACTED]&x=1"
    assert redact("Authorization: Bearer d_device_secret") == "Authorization: Bearer [REDACTED]"


def test_log_file_never_contains_key(tmp_path):
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    try:
        setup_logging("DEBUG", tmp_path / "logs")
        log = logging.getLogger("app.test")
        log.info("Dùng key %s", KEY)
        log.warning(f"f-string {KEY}")
        try:
            raise RuntimeError(f"lỗi với {KEY}")
        except RuntimeError:
            log.exception("Có exception")
        for h in logging.getLogger().handlers:
            h.flush()
        content = (tmp_path / "logs" / "backend.log").read_text(encoding="utf-8")
        assert "Dùng key" in content and "Có exception" in content
        assert "sk-ant-" not in content
    finally:
        for h in root.handlers[:]:
            h.close()
        root.handlers[:] = old_handlers
        root.setLevel(old_level)


def test_filter_rewrites_record_args():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "key %s", (KEY,), None)
    RedactingFilter().filter(record)
    assert "sk-ant-" not in record.getMessage()


def test_secret_store_roundtrip_and_key_info(memory_keyring):
    store = SecretStore()
    assert anthropic_key_info(store).present is False
    store.set(ANTHROPIC_KEY_NAME, KEY)
    info = anthropic_key_info(store)
    assert info.present and info.last4 == KEY[-4:] and info.source == "keychain"
    store.delete(ANTHROPIC_KEY_NAME)
    store.delete(ANTHROPIC_KEY_NAME)  # xoá lần hai không lỗi
    assert store.get(ANTHROPIC_KEY_NAME) is None


def test_key_format_check():
    assert looks_like_anthropic_key(KEY)
    assert not looks_like_anthropic_key("sk-proj-abc")
    assert not looks_like_anthropic_key("sk-ant- has spaces inside it 000000")
