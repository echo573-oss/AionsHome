"""本机回环访问校验：拒绝 LAN、Tailscale、反向代理及跨站请求；不相信 X-Forwarded-For。"""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException, Request

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_local(request: Request, *, mutation: bool = False) -> None:
    remote = request.client.host if request.client else ""
    if remote not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "此接口只能在运行新家的 Windows 电脑上使用")
    host_header = request.headers.get("host", "")
    try:
        url = urlsplit("http://" + host_header)
        hostname = url.hostname
        port = url.port
    except ValueError:
        raise HTTPException(403, "非法访问地址") from None
    if hostname not in _LOOPBACK or port not in (None, 8080):
        raise HTTPException(403, "请通过 localhost:8080 访问此接口")
    if mutation:
        origin = request.headers.get("origin", "")
        expected = request.url.scheme + "://" + host_header
        if origin != expected or request.headers.get("sec-fetch-site", "") in {"cross-site", "same-site"}:
            raise HTTPException(403, "跨站请求已拒绝")


async def require_local_request(request: Request) -> None:
    """路由依赖：GET 只校验回环，写入方法还校验 Origin。"""
    require_local(request, mutation=request.method not in _SAFE_METHODS)
