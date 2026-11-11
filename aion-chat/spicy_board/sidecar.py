"""Keep the spicy-monopoly HTTP API running on loopback only."""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from config import BASE_DIR, DATA_DIR

HOST = "127.0.0.1"
PORT = 8069
BASE_URL = f"http://{HOST}:{PORT}"
PID_PATH = DATA_DIR / "spicy_board_sidecar.json"
TIMEOUT = 8


def engine_dir() -> Path:
    override = os.environ.get("SPICY_MONOPOLY_DIR", "").strip()
    candidates = []
    if override:
        candidates.append(Path(override))
    candidates.append(Path(r"D:\spicy-monopoly"))
    candidates.append(BASE_DIR.parent.parent / "spicy-monopoly")
    for path in candidates:
        if (path / "monopoly_api.py").is_file():
            return path
    return candidates[0]


def _url(path: str) -> str:
    return BASE_URL + (path if path.startswith("/") else f"/{path}")


def request(method: str, path: str, body: dict | None = None, timeout: int = TIMEOUT):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _url(path),
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"} if data else {"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {"ok": True, "status": resp.status}
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {"ok": False, "error": "引擎返回了无法解析的内容", "status": resp.status}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        payload = None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {"detail": raw.decode("utf-8", "replace")[:800]}
        if isinstance(payload, dict):
            payload.setdefault("ok", False)
            payload["status"] = exc.code
            return payload
        return {"ok": False, "status": exc.code, "detail": str(payload)}
    except Exception as exc:
        return {"ok": False, "error": f"无法连接棋盘引擎：{type(exc).__name__}", "status": 0}


def healthy() -> bool:
    result = request("GET", "/help", timeout=3)
    return bool(result and result.get("rules_ack") and result.get("status", 200) == 200) or (
        bool(result.get("rules_ack")) and "error" not in result and result.get("status", 200) not in (0, 500, 502, 503)
    )


def help_payload() -> dict:
    return request("GET", "/help")


def _port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((HOST, PORT)) == 0


def _read_pid() -> dict:
    try:
        return json.loads(PID_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_pid(info: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def start() -> dict:
    root = engine_dir()
    if not (root / "monopoly_api.py").is_file():
        return {"ok": False, "error": f"找不到棋盘引擎目录：{root}"}
    if healthy():
        return {"ok": True, "already": True, "url": BASE_URL, "dir": str(root)}
    python = sys.executable
    log_out = root / "_api.out.log"
    log_err = root / "_api.err.log"
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = 0x08000000  # CREATE_NO_WINDOW
        startupinfo = None
    import subprocess
    with open(log_out, "ab") as out, open(log_err, "ab") as err:
        proc = subprocess.Popen(
            [python, "-m", "uvicorn", "monopoly_api:app", "--host", HOST, "--port", str(PORT)],
            cwd=str(root),
            stdout=out,
            stderr=err,
            creationflags=creationflags,
        )
    _write_pid({"pid": proc.pid, "url": BASE_URL, "dir": str(root), "started_at": time.time()})
    deadline = time.time() + 12
    while time.time() < deadline:
        if healthy():
            return {"ok": True, "pid": proc.pid, "url": BASE_URL, "dir": str(root)}
        if proc.poll() is not None:
            tail = ""
            try:
                tail = log_err.read_text(encoding="utf-8", errors="replace")[-500:]
            except Exception:
                pass
            return {"ok": False, "error": "棋盘引擎启动失败", "log": tail}
        time.sleep(0.3)
    return {"ok": False, "error": "棋盘引擎启动超时", "pid": proc.pid}


def ensure() -> dict:
    if healthy() or _port_open() and healthy():
        return {"ok": True, "already": True, "url": BASE_URL, "dir": str(engine_dir())}
    return start()


def status() -> dict:
    ok = healthy()
    info = _read_pid()
    return {
        "available": ok,
        "url": BASE_URL,
        "dir": str(engine_dir()),
        "pid": info.get("pid"),
        "engine_present": (engine_dir() / "monopoly_api.py").is_file(),
    }
