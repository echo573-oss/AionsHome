"""浏览器执行器的控制权协议。

本模块不启动 Chrome、不暴露路由，也不读取密码。未来的 Connor-BrowserTool
适配器通过 BrowserDriver 接口接入。接管期间禁止 Agent 动作，观察不等于接管。
"""
from __future__ import annotations

import asyncio
import ipaddress
import secrets
from dataclasses import dataclass, field
from typing import Callable, Protocol
from urllib.parse import urlsplit


class BrowserControlError(ValueError):
    pass


class BrowserDriver(Protocol):
    """每个任务一个独立浏览器会话；驱动器自身负责进程/页签生命周期。"""

    async def open(self, url: str) -> None: ...
    async def navigate(self, url: str) -> None: ...
    async def observe(self) -> dict: ...
    async def pause_agent(self) -> None: ...
    async def resume_agent(self) -> None: ...
    async def close(self) -> None: ...


def _safe_url(url: str) -> str:
    """第一阶段只允许普通公开 HTTPS 站点；不得用 URL 携带明文凭据。"""
    if not isinstance(url, str) or len(url) > 2048:
        raise BrowserControlError("浏览器网址无效")
    try:
        parsed = urlsplit(url.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise BrowserControlError("浏览器网址无效") from None
    if (parsed.scheme.lower() != "https" or not host or parsed.username
            or parsed.password or port is not None):
        raise BrowserControlError("试用阶段仅允许标准 HTTPS 网站")
    host = host.rstrip(".").lower()
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise BrowserControlError("暂不允许直接访问 IP 地址")
    if ("." not in host or host.endswith((
        ".localhost", ".local", ".lan", ".internal", ".home",
        ".test", ".example",
    ))):
        raise BrowserControlError("暂不允许访问本地或内部域名")
    return url.strip()


def _display_url(raw: str) -> str:
    """对话/日志只显示 HTTPS 主机，不回显路径、query 或 fragment。"""
    try:
        parts = urlsplit(str(raw))
        if parts.scheme != "https" or not parts.hostname:
            return ""
        host = parts.hostname.lower()
        return "https://" + host
    except ValueError:
        return ""


@dataclass
class BrowserSession:
    session_id: str
    scope_type: str
    scope_id: str
    actor: str
    driver: BrowserDriver = field(repr=False)
    state: str = "starting"
    version: int = 0
    page_url: str = ""
    title: str = ""
    error: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def public(self) -> dict:
        return {
            "session_id": self.session_id,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "actor": self.actor,
            "state": self.state,
            "version": self.version,
            "url": self.page_url,
            "title": self.title,
            "error": self.error,
        }


class BrowserHandoff:
    """进程内状态机，供未来本地浏览器适配器调用；不对聊天开放任意命令。"""

    def __init__(self, make_driver: Callable[[], BrowserDriver]):
        self._make_driver = make_driver
        self._session: BrowserSession | None = None
        self._manager_lock = asyncio.Lock()

    def _authorized(self, session: BrowserSession, actor: str,
                    scope_type: str, scope_id: str) -> None:
        if (actor != session.actor or scope_type != session.scope_type
                or scope_id != session.scope_id):
            raise BrowserControlError("此浏览器任务属于其他聊天")

    async def _find(self, session_id: str, actor: str,
                    scope_type: str, scope_id: str) -> BrowserSession:
        async with self._manager_lock:
            session = self._session
            if not session or session.session_id != session_id:
                raise BrowserControlError("浏览器任务不存在或已结束")
            self._authorized(session, actor, scope_type, scope_id)
            return session

    @staticmethod
    def _update_observation(session: BrowserSession, result: dict) -> None:
        if not isinstance(result, dict):
            raise BrowserControlError("浏览器观察结果格式不正确")
        session.page_url = _display_url(result.get("url", ""))
        session.title = str(result.get("title", "")).replace("\n", " ")[:120]
        session.version += 1

    async def begin(self, actor: str, scope_type: str, scope_id: str, url: str) -> dict:
        if actor not in {"aion", "connor"} or scope_type not in {"private", "chatroom"} or not scope_id:
            raise BrowserControlError("必须在有效的聊天窗口中启动")
        target = _safe_url(url)
        async with self._manager_lock:
            if self._session is not None:
                raise BrowserControlError("另一个浏览器任务正在使用本机执行器")
            session = BrowserSession(
                session_id=secrets.token_urlsafe(18),
                scope_type=scope_type, scope_id=scope_id,
                actor=actor, driver=self._make_driver(),
            )
            self._session = session
        try:
            async with session.lock:
                await asyncio.wait_for(session.driver.open(target), timeout=20)
                self._update_observation(
                    session, await asyncio.wait_for(session.driver.observe(), timeout=8),
                )
                session.state = "agent"
                return session.public()
        except BaseException:
            try:
                await self._close_driver(session)
            finally:
                async with self._manager_lock:
                    if self._session is session:
                        self._session = None
            raise

    async def watch(self, session_id: str, actor: str,
                    scope_type: str, scope_id: str) -> dict:
        session = await self._find(session_id, actor, scope_type, scope_id)
        # watch 为只读预览，不改变控制权。
        async with session.lock:
            if session.state == "closed":
                raise BrowserControlError("浏览器任务已结束")
            self._update_observation(
                session, await asyncio.wait_for(session.driver.observe(), timeout=8),
            )
            return session.public()

    async def navigate(self, session_id: str, actor: str,
                       scope_type: str, scope_id: str, url: str) -> dict:
        target = _safe_url(url)
        session = await self._find(session_id, actor, scope_type, scope_id)
        async with session.lock:
            if session.state != "agent":
                raise BrowserControlError("用户接管期间不能由 Agent 操作网页")
            session.state = "running"
            try:
                await asyncio.wait_for(session.driver.navigate(target), timeout=20)
                self._update_observation(
                    session, await asyncio.wait_for(session.driver.observe(), timeout=8),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                session.error = type(exc).__name__
                raise
            finally:
                # 不自动重试已发送的网页操作。
                if session.state == "running":
                    session.state = "agent"
            return session.public()

    async def take_control(self, session_id: str, actor: str,
                           scope_type: str, scope_id: str) -> dict:
        session = await self._find(session_id, actor, scope_type, scope_id)
        async with session.lock:
            if session.state != "agent":
                raise BrowserControlError("此任务当前无法接管")
            session.state = "pausing"
            try:
                await asyncio.wait_for(session.driver.pause_agent(), timeout=10)
            except BaseException:
                session.state = "agent"
                raise
            session.state = "human"
            session.version += 1
            return session.public()

    async def return_control(self, session_id: str, actor: str,
                             scope_type: str, scope_id: str) -> dict:
        session = await self._find(session_id, actor, scope_type, scope_id)
        async with session.lock:
            if session.state != "human":
                raise BrowserControlError("仅能交还用户已接管的浏览器")
            session.state = "resuming"
            try:
                await asyncio.wait_for(session.driver.resume_agent(), timeout=10)
                observation = await asyncio.wait_for(session.driver.observe(), timeout=8)
                self._update_observation(session, observation)
            except BaseException:
                # 交接失败时保持暂停状态，不能偷偷恢复模型操作。
                session.state = "human"
                raise
            session.state = "agent"
            session.version += 1
            return session.public()

    async def finish(self, session_id: str, actor: str,
                     scope_type: str, scope_id: str) -> None:
        session = await self._find(session_id, actor, scope_type, scope_id)
        async with session.lock:
            if session.state == "closed":
                return
            session.state = "closed"
            await self._close_driver(session)
        async with self._manager_lock:
            if self._session is session:
                self._session = None

    @staticmethod
    async def _close_driver(session: BrowserSession) -> None:
        try:
            await asyncio.wait_for(session.driver.close(), timeout=8)
        except Exception:
            # 适配器负责保证对浏览器进程的最终清理；此层不重启。
            pass
