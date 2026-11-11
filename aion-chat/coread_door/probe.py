"""Resolve whether the shared reading room is reachable on the LAN."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Any

# 地址不写进仓库：放在本机 data/coread_door.json（data/ 不入 git），
# 或用环境变量 AIONSHOME_COREAD_LAN_URL / AIONSHOME_COREAD_PUBLIC_URL 覆盖。
# 文件格式：{"lan_url": "http://<NAS局域网IP>:<端口>/", "public_url": "https://<公网域名>/"}
CONFIG_NAME = "coread_door.json"
PROBE_TIMEOUT = 3.0
CACHE_TTL = 20.0
_MAX_BODY = 64_000

_cache_lock = threading.Lock()
_cache: tuple[float, dict[str, Any]] | None = None


def looks_like_coread(status: int, body: str) -> bool:
    if status != 200:
        return False
    text = body.lower()
    return "共读室" in body or "coread" in text


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _fetch(url: str, timeout: float = PROBE_TIMEOUT, origin: str | None = None) -> tuple[int, str]:
    headers = {
        "User-Agent": "AionsHome/coread-door",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(_MAX_BODY)
            return int(getattr(response, "status", 200) or 200), _decode(raw)
    except Exception:
        return 0, ""


def _normalize(url: Any) -> str:
    text = str(url or "").strip()
    if not text.startswith(("http://", "https://")):
        return ""
    return text if text.endswith("/") else text + "/"


def load_urls() -> tuple[str, str]:
    """Return (lan_url, public_url) from env or the local, untracked config file."""
    data: dict[str, Any] = {}
    try:
        from config import DATA_DIR

        path = DATA_DIR / CONFIG_NAME
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    lan = _normalize(os.environ.get("AIONSHOME_COREAD_LAN_URL") or data.get("lan_url"))
    public = _normalize(os.environ.get("AIONSHOME_COREAD_PUBLIC_URL") or data.get("public_url"))
    return lan, public


def _fallback(public_url: str) -> dict[str, Any]:
    if public_url:
        return {"url": public_url, "via": "public", "lan_ok": False}
    return {"url": "", "via": "unconfigured", "lan_ok": False}


def _probe() -> dict[str, Any]:
    LAN_URL, PUBLIC_URL = load_urls()
    if not LAN_URL:
        return _fallback(PUBLIC_URL)
    status, body = _fetch(LAN_URL)
    # Browsers load the app's module JS/CSS with an Origin header; if the NAS
    # rejects that origin the page renders blank, so treat it as LAN-down.
    asset_status, _ = _fetch(LAN_URL + "manifest.webmanifest", origin=LAN_URL.rstrip("/"))
    if looks_like_coread(status, body) and asset_status == 200:
        return {"url": LAN_URL, "via": "lan", "lan_ok": True}
    return _fallback(PUBLIC_URL)


def resolve_door(force: bool = False) -> dict[str, Any]:
    """Return the preferred coread URL. LAN first, public as fallback."""
    global _cache
    now = time.monotonic()
    with _cache_lock:
        if not force and _cache and now - _cache[0] < CACHE_TTL:
            return dict(_cache[1])
    payload = _probe()
    with _cache_lock:
        _cache = (time.monotonic(), payload)
    return dict(payload)
