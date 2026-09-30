"""Quản lý điện thoại đồng hành từ laptop (chỉ localhost): tạo QR ghép nối, trạng thái, thiết bị (mục 7.2)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from app.companion.pairing import qr_svg
from app.context import AppContext
from app.routers.deps import api_error, get_ctx
from app.schemas import PairIn, device_out

router = APIRouter()


def _status(ctx: AppContext) -> dict[str, Any]:
    server = ctx.companion_server
    online = {c.device_id for c in ctx.companion.conns.values()}
    return {
        "running": server.running,
        "port": server.port,
        "error": server.error,
        "addresses": server.addresses(),
        "ca_fingerprint": server.certs.ca_fingerprint() if server.certs.ca_cert.exists() else None,
        **ctx.companion.status(),
        "paired_devices": [device_out(d, online=d.id in online) for d in ctx.pairing.devices()],
    }


@router.post("/companion/pair")
async def create_pairing(body: PairIn | None = None, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Bật server HTTPS nội bộ (nếu chưa chạy) và tạo mã QR ghép nối dùng một lần, hết hạn sau 5 phút."""
    body = body or PairIn()
    server = ctx.companion_server
    addresses = server.addresses()
    ip = body.ip or next((a["ip"] for a in addresses if a["primary"]), None) or (addresses[0]["ip"] if addresses else None)
    if ip is None:
        raise api_error(
            409,
            "NO_NETWORK",
            "Laptop chưa kết nối mạng nội bộ nào. Hãy bật Wi-Fi hoặc hotspot của laptop rồi thử lại.",
        )
    try:
        # Chứng chỉ phải chứa IP trong mã QR; mạng đổi thì server tự khởi động lại với chứng chỉ mới.
        await server.start(extra_ips=[ip])
    except RuntimeError as e:
        raise api_error(503, "COMPANION_SERVER_ERROR", str(e)) from e
    if body.session_id is not None:
        ctx.companion.set_target(body.session_id)
    token = ctx.pairing.create(session_id=body.session_id)
    url = server.url_for(ip, token.token)
    return {
        "token": token.token,
        "url": url,
        "ip": ip,
        "port": server.port,
        "expires_at": int(token.expires),
        "expires_in": token.expires_in,
        "qr_svg": qr_svg(url),
        "ca_url": f"https://{ip}:{server.port}/ca.crt",
        "local_url": f"http://127.0.0.1:{ctx.env.port}/c?t={token.token}",
        "addresses": addresses,
        "server_time": int(time.time()),
    }


@router.get("/companion/status")
def companion_status(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return _status(ctx)


@router.post("/companion/stop")
async def stop_companion(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    await ctx.companion_server.stop()
    return _status(ctx)


@router.get("/companion/devices")
def list_devices(ctx: AppContext = Depends(get_ctx)) -> list[dict[str, Any]]:
    online = {c.device_id for c in ctx.companion.conns.values()}
    return [device_out(d, online=d.id in online) for d in ctx.pairing.devices()]


@router.delete("/companion/devices/{device_id}")
async def revoke_device(device_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    if not ctx.pairing.revoke(device_id):
        raise api_error(404, "DEVICE_NOT_FOUND", "Không tìm thấy thiết bị")
    ctx.companion.kick(device_id)
    return {"revoked": True}
