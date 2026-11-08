"""Connor 专属独立浏览器管理路由。

提供打开、读取、截图、点击、输入以及人工接管和交还控制权的 HTTP 端点。
严格遵循 BrowserHandoff 权限约束，接管期间拒绝一切 Agent 操控。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from connor_browser_chat import execute_connor_browser_action


router = APIRouter(prefix="/api/browser/connor", tags=["connor-browser"])


class OpenRequest(BaseModel):
    url: str
    scope_id: str = "connor_1v1"
    scope_type: str = "chatroom"


class ClickRequest(BaseModel):
    x: int
    y: int
    scope_id: str = "connor_1v1"
    scope_type: str = "chatroom"


class TypeRequest(BaseModel):
    text: str = Field(max_length=2000)
    scope_id: str = "connor_1v1"
    scope_type: str = "chatroom"


class SessionActionRequest(BaseModel):
    scope_id: str = "connor_1v1"
    scope_type: str = "chatroom"


@router.post("/open")
async def open_browser(req: OpenRequest):
    res = await execute_connor_browser_action(
        "open", scope_type=req.scope_type, scope_id=req.scope_id, actor="connor", url=req.url,
    )
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "打开网页失败")
    return res


@router.get("/state")
async def get_browser_state(scope_id: str = Query("connor_1v1"), scope_type: str = Query("chatroom")):
    res = await execute_connor_browser_action(
        "watch", scope_type=scope_type, scope_id=scope_id, actor="connor",
    )
    if not res.get("ok"):
        raise HTTPException(404, res.get("error") or "当前无运行中的浏览器任务")
    return res


@router.get("/text")
async def get_browser_text(scope_id: str = Query("connor_1v1"), scope_type: str = Query("chatroom")):
    res = await execute_connor_browser_action(
        "read", scope_type=scope_type, scope_id=scope_id, actor="connor",
    )
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "读取文字失败")
    return res


@router.post("/screenshot")
async def take_screenshot(req: SessionActionRequest):
    res = await execute_connor_browser_action(
        "screenshot", scope_type=req.scope_type, scope_id=req.scope_id, actor="connor",
    )
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "截图失败")
    return res


@router.post("/click")
async def click_browser(req: ClickRequest):
    res = await execute_connor_browser_action(
        "click", scope_type=req.scope_type, scope_id=req.scope_id, actor="connor", x=req.x, y=req.y,
    )
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "点击执行失败")
    return res


@router.post("/type")
async def type_browser(req: TypeRequest):
    res = await execute_connor_browser_action(
        "type", scope_type=req.scope_type, scope_id=req.scope_id, actor="connor", text=req.text,
    )
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "输入文字失败")
    return res


@router.post("/take-control")
async def take_control_browser(req: SessionActionRequest):
    res = await execute_connor_browser_action(
        "take_control", scope_type=req.scope_type, scope_id=req.scope_id, actor="connor",
    )
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "接管控制权失败")
    return res


@router.post("/return-control")
async def return_control_browser(req: SessionActionRequest):
    res = await execute_connor_browser_action(
        "return_control", scope_type=req.scope_type, scope_id=req.scope_id, actor="connor",
    )
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "交还控制权失败")
    return res


@router.post("/close")
async def close_browser(req: SessionActionRequest):
    res = await execute_connor_browser_action(
        "finish", scope_type=req.scope_type, scope_id=req.scope_id, actor="connor",
    )
    return res
