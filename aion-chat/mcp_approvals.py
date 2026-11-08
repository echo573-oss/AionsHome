"""MCP 写入审批：本机内存短期保存操作提案，用户确认后执行一次。"""
from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from capabilities import is_capability_enabled
from mcp_client import is_chat_write_tool_name, mcp_manager


TTL_SECONDS = 300
MAX_PENDING = 24
MAX_ARGUMENT_CHARS = 3500
router = APIRouter(prefix="/api/mcp-approvals", tags=["mcp-approvals"])


@dataclass
class PendingWrite:
    approval_id: str
    nonce: str
    scope_type: str
    scope_id: str
    actor: str
    server: str
    tool: str
    arguments: dict
    request_key: str
    created_at: float
    state: str = "pending"

    def public(self) -> dict:
        return {
            "id": self.approval_id,
            "nonce": self.nonce,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "actor": self.actor,
            "server": self.server,
            "tool": self.tool,
            "arguments": self.arguments,
            "expires_at": self.created_at + TTL_SECONDS,
        }


class ApprovalRegistry:
    def __init__(self):
        self._items: dict[str, PendingWrite] = {}
        self._recent: dict[tuple[str, str, str], float] = {}
        self._lock = asyncio.Lock()

    def _purge(self):
        now = time.time()
        self._items = {
            key: item for key, item in self._items.items()
            if item.state == "pending" and item.created_at + TTL_SECONDS > now
        }
        self._recent = {key: expiry for key, expiry in self._recent.items() if expiry > now}

    async def stage(
        self, *, scope_type: str, scope_id: str, actor: str,
        server: str, tool: str, arguments: dict, request_key: str,
    ) -> PendingWrite:
        if scope_type not in {"private", "chatroom"} or not scope_id:
            raise ValueError("写入提案必须绑定到真实聊天")
        if actor not in {"aion", "connor"}:
            raise ValueError("未知角色")
        if not is_chat_write_tool_name(tool):
            raise ValueError("此工具不允许进入写入审批")
        if not isinstance(arguments, dict):
            raise ValueError("工具参数格式无效")
        try:
            preview = json.dumps(arguments, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("写入参数必须为可预览的 JSON") from None
        if len(preview) > MAX_ARGUMENT_CHARS:
            raise ValueError("写入内容过长，暂不支持自动审批")
        async with self._lock:
            self._purge()
            request_id = (scope_type, scope_id, request_key)
            for item in self._items.values():
                if (item.scope_type, item.scope_id, item.request_key) == request_id:
                    return item
            if request_id in self._recent:
                raise ValueError("相同写入请求最近已处理，请勿重复提交")
            if len(self._items) >= MAX_PENDING:
                raise ValueError("待确认操作过多，请先处理已有提案")
            item = PendingWrite(
                approval_id=secrets.token_urlsafe(24),
                nonce=secrets.token_urlsafe(32),
                scope_type=scope_type, scope_id=scope_id, actor=actor,
                server=server, tool=tool, arguments=json.loads(preview),
                request_key=request_key, created_at=time.time(),
            )
            self._items[item.approval_id] = item
            self._recent[request_id] = item.created_at + TTL_SECONDS
            if len(self._recent) > 256:
                oldest = min(self._recent, key=self._recent.get)
                self._recent.pop(oldest, None)
            return item

    async def pending_for(self, scope_type: str, scope_id: str) -> list[dict]:
        if scope_type not in {"private", "chatroom"} or not scope_id:
            return []
        async with self._lock:
            self._purge()
            return [
                item.public() for item in self._items.values()
                if (item.scope_type, item.scope_id) == (scope_type, scope_id)
            ]

    async def claim(
        self, approval_id: str, nonce: str, scope_type: str, scope_id: str,
        *, approve: bool,
    ) -> PendingWrite:
        async with self._lock:
            self._purge()
            item = self._items.get(approval_id)
            if (item is None or item.state != "pending"
                or not hmac.compare_digest(item.nonce, nonce)
                or item.scope_type != scope_type or item.scope_id != scope_id):
                raise ValueError("该操作已失效或确认信息不匹配")
            # 先销毁凭据和待审批状态，网络超时也不允许重复执行。
            item.state = "claimed"
            self._items.pop(approval_id, None)
            return item


approval_registry = ApprovalRegistry()


async def execute_approved_write(item: PendingWrite) -> str:
    """二次验证开关、角色授权与工具实例；失败不做自动重试。"""
    if not is_capability_enabled("mcp_chat_write"):
        raise ValueError("MCP 写入能力当前已关闭")
    servers = {srv["name"]: srv for srv in mcp_manager.list_servers()}
    server = servers.get(item.server)
    if not server or not server.get("enabled", True):
        raise ValueError("服务器已不可用")
    allow = set(server.get("chat_write_grants", {}).get(item.actor, []))
    if item.tool not in allow or not is_chat_write_tool_name(item.tool):
        raise ValueError("角色写入权限已撤销")
    async with asyncio.timeout(12):
        tools = await mcp_manager.connect(item.server)
    if item.tool not in {tool.get("name") for tool in tools}:
        raise ValueError("原工具不再存在")
    async with asyncio.timeout(20):
        contents = await mcp_manager.call_tool(
            item.server, item.tool, item.arguments, fail_on_error=True,
        )
    result = "\n".join(
        entry.get("text", "") for entry in contents
        if isinstance(entry, dict) and entry.get("type") == "text"
    )[:1300]
    return result or "服务器已接收操作，但未返回可读结果。请在目标应用确认。"


class DecisionRequest(BaseModel):
    nonce: str
    scope_type: str
    scope_id: str
    approve: bool


@router.get("/pending")
async def list_pending(scope_type: str, scope_id: str):
    return {"pending": await approval_registry.pending_for(scope_type, scope_id)}


@router.post("/{approval_id}/decision")
async def decide_write(approval_id: str, body: DecisionRequest, request: Request):
    # 浏览器之外的跨域网页不得代替用户点击审批。
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="跨站操作被拒绝")
    try:
        item = await approval_registry.claim(
            approval_id, body.nonce, body.scope_type, body.scope_id,
            approve=body.approve,
        )
    except ValueError:
        raise HTTPException(status_code=409, detail="审批已过期或已处理") from None
    if not body.approve:
        return {"ok": True, "status": "declined", "message": "已取消，本次未执行"}
    try:
        message = await execute_approved_write(item)
        return {"ok": True, "status": "submitted", "message": message}
    except (ValueError, asyncio.TimeoutError):
        return {"ok": False, "status": "failed", "message": "操作未确认成功；可能已送达远端，请先检查目标应用，勿立即重试"}
    except asyncio.CancelledError:
        raise
    except Exception:
        # 错误信息可能包含认证凭据，不能返回给浏览器。
        return {"ok": False, "status": "failed", "message": "远端操作状态未知，请检查目标应用，勿直接重复提交"}
