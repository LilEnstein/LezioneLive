"""Chạy backend: ``python -m app`` (mặc định http://127.0.0.1:8765)."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser

import uvicorn

from app.config import EnvSettings
from app.logging_setup import setup_logging


def _utf8_console() -> None:
    """Console Windows mặc định cp1252 không in được tiếng Việt trong log."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main() -> None:
    _utf8_console()
    parser = argparse.ArgumentParser(description="LezioneLive backend")
    parser.add_argument("--host", help="Mặc định 127.0.0.1 (chỉ máy cục bộ)")
    parser.add_argument("--port", type=int)
    parser.add_argument("--data-dir", help="Thư mục dữ liệu (SQLite, slide, log)")
    parser.add_argument("--open", action="store_true", help="Mở trình duyệt sau khi khởi động")
    parser.add_argument("--reload", action="store_true", help="Tự nạp lại khi sửa code (phát triển)")
    parser.add_argument(
        "--companion-host",
        help="Địa chỉ server điện thoại đồng hành (mặc định 0.0.0.0 — mọi card mạng; 127.0.0.1 để thử trên máy)",
    )
    parser.add_argument("--companion-port", type=int, help="Cổng HTTPS cho điện thoại (mặc định 8766)")
    args = parser.parse_args()

    overrides = {
        k: v
        for k, v in {
            "host": args.host,
            "port": args.port,
            "data_dir": args.data_dir,
            "companion_host": args.companion_host,
            "companion_port": args.companion_port,
        }.items()
        if v
    }
    env = EnvSettings(**overrides)
    log_config = setup_logging(env.log_level, env.data_dir / "logs")
    log = logging.getLogger("app")
    if not env.is_loopback:
        log.warning("Backend đang lắng nghe trên %s (không phải loopback). Chỉ dùng trong Docker.", env.host)

    if args.open:
        url = f"http://127.0.0.1:{env.port}/"
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()

    from app.main import create_app

    if args.reload:
        uvicorn.run(
            "app.main:create_app",
            factory=True,
            host=env.host,
            port=env.port,
            reload=True,
            log_config=log_config,
        )
    else:
        uvicorn.run(create_app(env), host=env.host, port=env.port, log_config=log_config, ws_ping_interval=20)


if __name__ == "__main__":
    main()
