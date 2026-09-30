"""FastAPI app: router, middleware bảo mật (NFR-06), phục vụ frontend đã build."""

from __future__ import annotations

import logging
import mimetypes
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.audio import ws_ingest
from app.companion import routes as companion_routes
from app.companion.server import add_static_routes
from app.config import VERSION, EnvSettings
from app.context import AppContext, build_context, recover_sessions
from app.llm.client import LLMError
from app.routers import (
    chat,
    companion,
    courses,
    export,
    flashcards,
    glossary,
    inclass,
    search,
    sessions,
    settings,
    slides,
)

# Windows không phải lúc nào cũng đăng ký .webp: FileResponse sẽ trả application/octet-stream.
mimetypes.add_type("image/webp", ".webp")

log = logging.getLogger("app")

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class LocalOnlyMiddleware:
    """Chặn request thay đổi dữ liệu đến từ trang web khác (CSRF tới localhost).

    CORS chỉ ngăn đọc phản hồi; form POST từ site lạ vẫn tới được server nên phải chặn theo Origin.
    """

    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        self.app = app
        self.allowed = set(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            origin = headers.get("origin")
            fetch_site = headers.get("sec-fetch-site")
            if (origin and origin not in self.allowed) or (fetch_site == "cross-site" and not origin):
                response = JSONResponse(
                    {"detail": {"code": "FORBIDDEN_ORIGIN", "message": "Origin không được phép"}}, status_code=403
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class TimingMiddleware:
    """Đo thời gian xử lý request (header Server-Timing), log request chậm."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                dur = (time.perf_counter() - start) * 1000
                headers = list(message.get("headers", []))
                headers.append((b"server-timing", f"app;dur={dur:.1f}".encode()))
                message["headers"] = headers
                if dur > 1000:
                    log.info("Request chậm %s %s: %.0f ms", scope["method"], scope["path"], dur)
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.exists():
        log.info("Chưa có frontend build tại %s (chạy 'npm run build' trong frontend/)", dist)

        @app.get("/", include_in_schema=False)
        def _no_frontend() -> JSONResponse:
            return JSONResponse(
                {
                    "message": "LezioneLive backend đang chạy. Frontend chưa được build: "
                    "chạy `npm run build` trong thư mục frontend, hoặc dùng Vite dev server tại http://localhost:5173.",
                    "api_docs": "/api/docs",
                }
            )

        return

    root = dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "ws/")):
            raise HTTPException(status_code=404)
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def create_app(
    env: EnvSettings | None = None,
    context_factory: Callable[[EnvSettings], AppContext] | None = None,
) -> FastAPI:
    env = env or EnvSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ctx = context_factory(env) if context_factory else build_context(env)
        app.state.ctx = ctx
        recover_sessions(ctx)
        if not env.disable_stt_autoload:
            ctx.stt.autoload()
        startup = ctx.settings.get()
        if not env.disable_stt_autoload and (startup.offline_draft or startup.translate_provider == "local"):
            ctx.draft.start_prepare(download=False)  # nạp sẵn model dịch cục bộ nếu đã tải
        log.info("LezioneLive %s sẵn sàng tại http://%s:%s (dữ liệu: %s)", VERSION, env.host, env.port, env.data_dir)
        try:
            yield
        finally:
            await ctx.shutdown()

    app = FastAPI(
        title="LezioneLive API",
        version=VERSION,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    # Thứ tự: middleware thêm sau cùng nằm ngoài cùng.
    app.add_middleware(TimingMiddleware)
    app.add_middleware(LocalOnlyMiddleware, allowed_origins=env.allowed_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=env.allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "Server-Timing"],
    )
    hosts = ["127.0.0.1", "localhost", "::1", "[::1]"]
    if not env.is_loopback:  # Docker: container lắng nghe 0.0.0.0 nhưng chỉ map ra 127.0.0.1 của máy chủ
        hosts.append("*")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.exception_handler(LLMError)
    async def _llm_error(_: Request, exc: LLMError) -> JSONResponse:
        status = {"no_key": 400, "auth": 400, "rate_limit": 429, "context_too_long": 400}.get(exc.code, 502)
        return JSONResponse({"detail": {"code": exc.code.upper(), "message": exc.message}}, status_code=status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors: list[Any] = exc.errors()
        first = errors[0] if errors else {}
        field = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return JSONResponse(
            {"detail": {"code": "VALIDATION_ERROR", "message": f"Dữ liệu không hợp lệ: {field} ({first.get('msg')})"}},
            status_code=422,
        )

    for module in (settings, courses, sessions, chat, glossary, slides, export, flashcards, search, inclass, companion):
        app.include_router(module.router, prefix="/api")
    app.include_router(ws_ingest.router)
    # Trang điện thoại đồng hành cũng mở được ngay trên laptop (http://127.0.0.1:8765/c?t=...).
    app.include_router(companion_routes.router)
    add_static_routes(app, env.frontend_dist, index_redirect=False)
    _mount_frontend(app, env.frontend_dist)
    return app
