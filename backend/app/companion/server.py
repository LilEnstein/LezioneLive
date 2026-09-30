"""Server HTTPS/WSS cho điện thoại đồng hành, chạy chung tiến trình và event loop với backend chính.

- Chỉ lắng nghe (mặc định ``0.0.0.0:8766``) khi người dùng bật ghép nối điện thoại; tắt được bất kỳ lúc nào.
- App riêng, chỉ gồm: trang đồng hành (PWA), tài nguyên tĩnh, ``/ca.crt`` và các endpoint ``/api/companion``,
  ``/ws/companion`` — mọi endpoint dữ liệu đều đòi token (xem ``routes.py``). Giao diện laptop và API
  ``/api`` đầy đủ vẫn chỉ ở ``127.0.0.1``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.companion.tls import CertManager, lan_addresses

if TYPE_CHECKING:
    from app.context import AppContext

log = logging.getLogger(__name__)

PUBLIC_FILES = {"audio-worklet.js", "favicon.svg", "companion.webmanifest", "companion-icon.svg"}


class _EmbeddedServer(uvicorn.Server):
    """uvicorn.Server không cài bộ xử lý tín hiệu (server chính đã cài; Ctrl+C dừng cả hai)."""

    @contextlib.contextmanager
    def capture_signals(self):  # type: ignore[override]
        yield

    def install_signal_handlers(self) -> None:  # uvicorn cũ
        return None


def companion_page(dist: Path) -> Path | None:
    page = dist / "companion.html"
    return page if page.exists() else None


def add_static_routes(app: FastAPI, dist: Path, *, index_redirect: bool) -> None:
    """Trang đồng hành ``/c`` + tài nguyên tĩnh của bản build frontend (không phục vụ giao diện laptop)."""
    root = dist.resolve()

    @app.get("/c", include_in_schema=False)
    def _companion() -> Response:
        page = companion_page(dist)
        if page is None:
            return Response(
                "Chưa build giao diện (chạy npm run build trong thư mục frontend).",
                media_type="text/plain; charset=utf-8",
                status_code=503,
            )
        return FileResponse(page, headers={"Cache-Control": "no-cache"})

    if not index_redirect:
        return

    @app.get("/", include_in_schema=False)
    def _root() -> RedirectResponse:
        return RedirectResponse("/c")

    @app.get("/assets/{name:path}", include_in_schema=False)
    def _assets(name: str) -> FileResponse:
        candidate = (root / "assets" / name).resolve()
        if not candidate.is_file() or not candidate.is_relative_to(root / "assets"):
            raise HTTPException(status_code=404)
        return FileResponse(candidate, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/{name}", include_in_schema=False)
    def _public(name: str) -> FileResponse:
        candidate = (root / name).resolve()
        if name not in PUBLIC_FILES or not candidate.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(candidate)


def build_companion_app(ctx: AppContext) -> FastAPI:
    from app.companion import routes  # import muộn: routes → routers.deps → app.context (vòng lặp import)

    app = FastAPI(title="LezioneLive companion", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    app.state.lan = True

    @app.middleware("http")
    async def _security_headers(request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.get("/ca.crt", include_in_schema=False)
    def _ca() -> Response:
        return Response(
            ctx.companion_server.certs.ca_pem(),
            media_type="application/x-x509-ca-cert",
            headers={"Content-Disposition": 'attachment; filename="LezioneLive-CA.crt"'},
        )

    app.include_router(routes.router)
    add_static_routes(app, ctx.env.frontend_dist, index_redirect=True)
    return app


class CompanionServer:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.certs = CertManager(ctx.env.data_dir / "companion")
        self.host = ctx.env.companion_host
        self._configured_port = ctx.env.companion_port
        self.port = ctx.env.companion_port
        self.error: str | None = None
        self._server: _EmbeddedServer | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._server is not None and self._task is not None and not self._task.done()

    def addresses(self) -> list[dict[str, Any]]:
        return [a.as_dict() for a in lan_addresses()]

    async def start(self, extra_ips: list[str] | None = None) -> None:
        """Bật server (nếu chưa chạy). Laptop đổi mạng (IP mới, bật hotspot) → tạo chứng chỉ mới và khởi động lại."""
        ips = sorted({a.ip for a in lan_addresses()} | set(extra_ips or []))
        if self.running and not self.certs.covers(ips):
            log.info("Mạng thay đổi (%s): khởi động lại server điện thoại với chứng chỉ mới", ", ".join(ips))
            await self.stop()
        async with self._lock:
            if self.running:
                return
            self.error = None
            cert, key = await asyncio.to_thread(self.certs.ensure, ips)
            sock = socket.socket(socket.AF_INET6 if ":" in self.host else socket.AF_INET, socket.SOCK_STREAM)
            if sys.platform == "win32":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((self.host, self._configured_port))
            except OSError as e:
                sock.close()
                self.error = (
                    f"Không mở được cổng {self._configured_port} ({e.strerror or e}). "
                    "Có thể một chương trình khác đang dùng."
                )
                raise RuntimeError(self.error) from e
            self.port = sock.getsockname()[1]
            sock.listen(64)
            sock.setblocking(False)
            config = uvicorn.Config(
                build_companion_app(self.ctx),
                ssl_certfile=str(cert),
                ssl_keyfile=str(key),
                log_config=None,
                lifespan="off",
                access_log=False,
                ws_ping_interval=20,
                ws_max_size=4 * 1024 * 1024,
                timeout_graceful_shutdown=2,
            )
            server = _EmbeddedServer(config)
            self._server = server

            async def run() -> None:
                try:
                    await server.serve(sockets=[sock])
                except SystemExit:  # uvicorn gọi sys.exit khi khởi động lỗi: không để lan ra event loop
                    self.error = "Server điện thoại dừng do lỗi khởi động."
                except Exception as e:  # noqa: BLE001
                    log.exception("Server điện thoại lỗi")
                    self.error = f"Server điện thoại lỗi: {type(e).__name__}"
                finally:
                    sock.close()

            self._task = asyncio.create_task(run())
            for _ in range(100):
                if server.started or self._task.done():
                    break
                await asyncio.sleep(0.05)
            if not server.started:
                await self.stop()
                raise RuntimeError(self.error or "Server điện thoại không khởi động được.")
            log.info("Server điện thoại đồng hành: https://%s:%s (%s)", self.host, self.port, ", ".join(ips) or "-")

    async def stop(self) -> None:
        server, task = self._server, self._task
        self._server, self._task = None, None
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()

    def url_for(self, ip: str, token: str | None = None) -> str:
        host = f"[{ip}]" if ":" in ip else ip
        base = f"https://{host}:{self.port}/c"
        return f"{base}?t={token}" if token else base
