"""Local session index for the velvet board room (not the game engine)."""
from __future__ import annotations

import json
import time
from threading import Lock

from config import DATA_DIR

PATH = DATA_DIR / "spicy_board_games.json"
_LOCK = Lock()


def _load() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "games" in data:
            return data
    except Exception:
        pass
    return {"games": {}}


def _save(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PATH)


def all_games() -> list[dict]:
    with _LOCK:
        games = list(_load()["games"].values())
    games.sort(key=lambda g: g.get("updated_at", 0), reverse=True)
    return games


def get(sid: str) -> dict | None:
    with _LOCK:
        return _load()["games"].get(sid)


def put(game: dict) -> dict:
    game = {**game, "updated_at": time.time()}
    with _LOCK:
        data = _load()
        data["games"][game["id"]] = game
        _save(data)
    return game


def upsert(sid: str, **fields) -> dict:
    with _LOCK:
        data = _load()
        current = data["games"].get(sid) or {"id": sid, "created_at": time.time(), "messages": []}
        current.update(fields)
        current["id"] = sid
        current["updated_at"] = time.time()
        data["games"][sid] = current
        _save(data)
        return current


def append_message(sid: str, message: dict) -> dict:
    with _LOCK:
        data = _load()
        game = data["games"].get(sid)
        if not game:
            raise KeyError(sid)
        messages = list(game.get("messages") or [])
        messages.append(message)
        game["messages"] = messages[-200:]
        game["updated_at"] = time.time()
        data["games"][sid] = game
        _save(data)
        return game
