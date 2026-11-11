"""Original velvet-board room: proxies the loopback engine and serves the page."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import store
from .board import structure
from .companionship import chat as table_chat, names
from .sidecar import ensure, help_payload, request, status as sidecar_status

router = APIRouter()
STATIC = Path(__file__).with_name("static")

ACTION_PATHS = {
    "skip": ("POST", "/skip/{game_id}/{who}"),
    "swap": ("POST", "/swap/{game_id}/{who}"),
    "pay_toll": ("POST", "/pay_toll/{game_id}/{who}"),
    "serve_toll": ("POST", "/serve_toll/{game_id}/{who}"),
    "buy_card": ("POST", "/buy_card/{game_id}/{who}"),
    "buyout": ("POST", "/buyout/{game_id}/{who}"),
    "done": ("POST", "/done/{game_id}/{who}"),
    "reroll_task": ("POST", "/reroll_task/{game_id}/{who}"),
    "reroll_identity": ("POST", "/reroll_identity/{game_id}/{who}"),
    "extra_task": ("POST", "/extra_task/{game_id}/{who}"),
    "buy": ("POST", "/buy/{game_id}/{who}"),
    "use_card": ("POST", "/use_card/{game_id}/{who}/{index}"),
    "discard": ("POST", "/discard/{game_id}/{who}/{index}"),
    "duel_result": ("POST", "/duel_result/{game_id}/{winner}"),
    "guess_mark": ("POST", "/guess_mark/{game_id}/{who}/{spot}"),
    "id_event": ("POST", "/id_event/{game_id}/{who}/{event}"),
    "declare_persona": ("POST", "/declare_persona/{game_id}/{who}"),
    "final_result": ("GET", "/final_result/{game_id}"),
}


class VelvetAssets(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if Path(path).suffix in {".js", ".css"}:
            response.headers["Cache-Control"] = "no-cache"
        return response


class NewGame(BaseModel):
    opponent: str = Field(pattern=r"^(aion|connor)$")
    flavor: str = Field(default="medium", pattern=r"^(light|medium|heavy)$")
    game_length: int = Field(default=18, ge=12, le=24)
    p1_sex: str = Field(default="男", max_length=4)
    p1_role: str = Field(default="攻", max_length=4)
    p2_sex: str = Field(default="女", max_length=4)
    p2_role: str = Field(default="受", max_length=4)
    identity_mode: str = Field(default="mixed", pattern=r"^(off|mixed|nsfw_only)$")
    redline: list[str] = Field(default_factory=list)
    open_anal: bool = False
    first_player: str = ""
    pair_code: str = ""


class RollBody(BaseModel):
    toll: Optional[str] = None
    task: Optional[str] = None
    super_action: Optional[str] = None
    duel_winner: Optional[str] = None
    guess: Optional[str] = None
    swap_identity: Optional[bool] = None
    tiebreak: Optional[bool] = False


class ActionBody(BaseModel):
    action: str = Field(min_length=1, max_length=40)
    who: str = Field(default="", max_length=40)
    index: Optional[int] = None
    winner: str = Field(default="", max_length=40)
    spot: str = Field(default="", max_length=40)
    event: str = Field(default="", max_length=40)
    persona: str = Field(default="", max_length=200)


class TableChat(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _engine(method: str, path: str, body: dict | None = None) -> dict:
    result = request(method, path, body)
    if result.get("status") == 0 or result.get("error") and not result.get("game_id") and not result.get("board") and not result.get("say") and not result.get("rules_ack"):
        raise HTTPException(503, result.get("error") or "棋盘引擎暂时不可用")
    if result.get("status") in (404,):
        raise HTTPException(404, result.get("detail") or result.get("error") or "找不到这局")
    return result


async def _engine_async(method: str, path: str, body: dict | None = None) -> dict:
    return await asyncio.to_thread(_engine, method, path, body)


def _lineup(p1_sex: str, p2_sex: str) -> str:
    return (p1_sex or "") + (p2_sex or "")


def _public(game: dict, engine_state: dict | None = None, last: dict | None = None) -> dict:
    labels = names()
    payload = dict(engine_state or {})
    last = last if last is not None else game.get("last") or {}
    if last:
        for key in ("say", "task", "truth", "mystery", "card", "toll", "duel", "hint",
                    "action_needed", "dice", "tile", "who", "identity_reminder",
                    "active_limits", "intensity_note", "intro", "settled", "game_over",
                    "jailed", "asleep", "in_jail", "next_turn"):
            if key in last and key not in payload:
                payload[key] = last[key]
        if last.get("board") and not payload.get("board"):
            payload["board"] = last["board"]
        if last.get("status") and not payload.get("status"):
            payload["status"] = last["status"]
    board = structure(
        payload,
        flavor=game.get("flavor"),
        game_length=game.get("game_length"),
    )
    messages = []
    for message in game.get("messages") or []:
        text = message["text"] if message["sender"] == "user" else spoken_or_raw(message["text"])
        if text:
            messages.append({**message, "text": text})
    return {
        "id": game["id"],
        "available": True,
        "names": labels,
        "opponent": game.get("opponent"),
        "opponent_name": game.get("opponent_name"),
        "p1_name": game.get("p1_name"),
        "p2_name": game.get("p2_name"),
        "flavor": game.get("flavor"),
        "game_length": game.get("game_length"),
        "status": "finished" if (last or {}).get("game_over") or game.get("status") == "finished" else game.get("status") or "running",
        "chat_error": game.get("chat_error") or "",
        "messages": messages,
        "board": board,
        "event": {
            "say": payload.get("say"),
            "task": payload.get("task"),
            "truth": payload.get("truth"),
            "mystery": payload.get("mystery"),
            "card": payload.get("card"),
            "toll": payload.get("toll"),
            "duel": payload.get("duel"),
            "hint": payload.get("hint"),
            "action_needed": payload.get("action_needed"),
            "dice": payload.get("dice"),
            "tile": payload.get("tile"),
            "who": payload.get("who"),
            "identity_reminder": payload.get("identity_reminder"),
            "active_limits": payload.get("active_limits"),
            "intensity_note": payload.get("intensity_note"),
            "settled": payload.get("settled"),
            "game_over": payload.get("game_over"),
            "final": game.get("final"),
        },
        "attribution": {
            "engine": "涩涩大富翁",
            "license": "CC BY-NC 4.0",
            "authors": "Ren & Puppy",
            "url": "https://github.com/RennAkira/spicy-monopoly",
        },
        "safeword": "404",
    }


def spoken_or_raw(text: str) -> str:
    from .companionship import spoken_text
    return spoken_text(text) or ""


def _summary(game: dict) -> dict:
    labels = names()
    return {
        "id": game["id"],
        "status": game.get("status") or "running",
        "flavor": game.get("flavor"),
        "players": [game.get("p1_name") or labels["user"], game.get("p2_name") or game.get("opponent_name")],
        "opponent": game.get("opponent"),
        "updated_at": game.get("updated_at"),
        "turn": ((game.get("last") or {}).get("next_turn") or (game.get("last") or {}).get("turn") or ""),
    }


@router.get("/spicy-board")
async def page():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/api/spicy-board")
async def lobby():
    ready = await asyncio.to_thread(ensure)
    info = sidecar_status()
    labels = names()
    games = [_summary(game) for game in store.all_games() if game.get("status") != "finished"]
    return {
        "available": bool(ready.get("ok") or info.get("available")),
        "sidecar": info,
        "names": labels,
        "games": games,
        "safeword": "404",
        "flavors": [
            {"id": "light", "label": "轻度"},
            {"id": "medium", "label": "适中"},
            {"id": "heavy", "label": "浓烈"},
        ],
        "lengths": [
            {"id": 12, "label": "速玩 12 回合"},
            {"id": 18, "label": "正常 18 回合"},
            {"id": 24, "label": "超长 24 回合"},
        ],
        "attribution": {
            "engine": "涩涩大富翁",
            "license": "CC BY-NC 4.0",
            "authors": "Ren & Puppy",
            "url": "https://github.com/RennAkira/spicy-monopoly",
        },
    }


@router.post("/api/spicy-board")
async def create(body: NewGame):
    ready = await asyncio.to_thread(ensure)
    if not ready.get("ok"):
        raise HTTPException(503, ready.get("error") or "棋盘引擎还没准备好")
    labels = names()
    if body.opponent not in labels:
        raise HTTPException(400, "请选择一位在家的伴侣")
    p1_name = labels["user"]
    p2_name = labels[body.opponent]
    help_data = await asyncio.to_thread(help_payload)
    rules_ack = help_data.get("rules_ack")
    if not rules_ack:
        raise HTTPException(503, help_data.get("error") or "拿不到开局规则版本，请稍后再试")
    payload: dict[str, Any] = {
        "lineup": _lineup(body.p1_sex, body.p2_sex),
        "flavor": body.flavor,
        "p1_name": p1_name,
        "p1_sex": body.p1_sex,
        "p1_role": body.p1_role,
        "p2_name": p2_name,
        "p2_sex": body.p2_sex,
        "p2_role": body.p2_role,
        "redline": [item.strip() for item in body.redline if item and item.strip()],
        "open_anal": [p1_name, p2_name] if body.open_anal else [],
        "identity_mode": body.identity_mode,
        "game_length": body.game_length,
        "pair_code": body.pair_code or "",
        "first_player": body.first_player or p1_name,
        "setup_confirmed": True,
        "rules_ack": rules_ack,
    }
    result = await _engine_async("POST", "/new_game", payload)
    if result.get("status") == 428:
        again = await asyncio.to_thread(help_payload)
        payload["rules_ack"] = again.get("rules_ack") or rules_ack
        result = await _engine_async("POST", "/new_game", payload)
    game_id = result.get("game_id")
    if not game_id:
        detail = result.get("detail") or result.get("error") or result.get("short") or "开局没有成功"
        if isinstance(detail, list):
            detail = str(detail)
        raise HTTPException(result.get("status") or 400, detail)
    game = store.put({
        "id": game_id,
        "opponent": body.opponent,
        "opponent_name": p2_name,
        "p1_name": p1_name,
        "p2_name": p2_name,
        "flavor": body.flavor,
        "game_length": body.game_length,
        "player_token": result.get("player_token"),
        "status": "running",
        "last": result,
        "messages": [],
        "created_at": result.get("created_at") or 0,
    })
    state = await _engine_async("GET", f"/state/{quote(game_id)}")
    return _public(game, state, result)


@router.get("/api/spicy-board/{sid}")
async def state(sid: str):
    game = store.get(sid)
    if not game:
        raise HTTPException(404, "找不到这局棋盘")
    engine_state = await _engine_async("GET", f"/state/{quote(sid)}")
    return _public(game, engine_state, game.get("last"))


@router.post("/api/spicy-board/{sid}/roll")
async def roll(sid: str, body: RollBody | None = None):
    game = store.get(sid)
    if not game:
        raise HTTPException(404, "找不到这局棋盘")
    payload = {k: v for k, v in (body.model_dump() if body else {}).items() if v not in (None, False, "")}
    result = await _engine_async("POST", f"/roll/{quote(sid)}", payload or None)
    if result.get("status") in (400, 409, 422, 428) and not result.get("board") and not result.get("say"):
        raise HTTPException(result.get("status") or 400, result.get("detail") or result.get("error") or "这一掷没有成功")
    game = store.upsert(sid, last=result, status="finished" if result.get("game_over") else "running")
    state = await _engine_async("GET", f"/state/{quote(sid)}")
    return _public(game, state, result)


@router.post("/api/spicy-board/{sid}/action")
async def act(sid: str, body: ActionBody):
    game = store.get(sid)
    if not game:
        raise HTTPException(404, "找不到这局棋盘")
    action = body.action
    if action not in ACTION_PATHS:
        raise HTTPException(400, "不支持的操作")
    method, template = ACTION_PATHS[action]
    who = body.who or game.get("p1_name") or names()["user"]
    path = template.format(
        game_id=quote(sid, safe=""),
        who=quote(who, safe=""),
        index=body.index if body.index is not None else 0,
        winner=quote(body.winner or who, safe=""),
        spot=quote(body.spot or "", safe=""),
        event=quote(body.event or "", safe=""),
    )
    payload = None
    if action == "declare_persona":
        payload = {"persona": body.persona}
    result = await _engine_async(method, path, payload)
    fields = {"last": {**(game.get("last") or {}), **result}}
    if action == "final_result":
        fields["final"] = result
        fields["status"] = "finished"
    game = store.upsert(sid, **fields)
    try:
        state = await _engine_async("GET", f"/state/{quote(sid)}")
    except HTTPException:
        state = {}
    return _public(game, state, game.get("last"))


@router.post("/api/spicy-board/{sid}/chat")
async def chat_endpoint(sid: str, body: TableChat):
    if not store.get(sid):
        raise HTTPException(404, "找不到这局棋盘")
    try:
        game = await table_chat(sid, body.text)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    try:
        state = await _engine_async("GET", f"/state/{quote(sid)}")
    except HTTPException:
        state = {}
    return _public(game, state, game.get("last"))


@router.post("/api/spicy-board/{sid}/finish")
async def finish(sid: str):
    game = store.get(sid)
    if not game:
        raise HTTPException(404, "找不到这局棋盘")
    result = await _engine_async("GET", f"/final_result/{quote(sid)}")
    game = store.upsert(sid, last={**(game.get("last") or {}), **result}, final=result, status="finished")
    try:
        state = await _engine_async("GET", f"/state/{quote(sid)}")
    except HTTPException:
        state = {}
    return _public(game, state, game.get("last"))


def install(app):
    app.include_router(router)
    app.mount("/spicy-board-assets", VelvetAssets(directory=str(STATIC)), name="spicy-board-assets")
