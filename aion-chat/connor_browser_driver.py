"""Connor 专用独立浏览器驱动器。

对接本地 Connor-BrowserTool (默认 http://127.0.0.1:8787) Playwright 独立浏览器服务。
实现 BrowserDriver 协议，严格遵循控制权交接规则：
人工接管（paused=True）期间，驱动层与状态机层双重阻断 Agent 的一切变动操作（open, navigate, click, type）。
仅供 Connor 角色在聊天中安全使用独立浏览器环境，不连接密码库。
"""
from __future__ import annotations

import os
from typing import Any
import httpx

from browser_handoff import BrowserControlError


DEFAULT_CONNOR_BROWSER_URL = os.getenv("CONNOR_BROWSER_URL", "http://127.0.0.1:8787")


class ConnorBrowserDriver:
    """对接 Connor-BrowserTool HTTP 服务的驱动实现。"""

    def __init__(
        self,
        base_url: str = DEFAULT_CONNOR_BROWSER_URL,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self.paused = False

    def _assert_not_paused(self, action: str = "操作") -> None:
        if self.paused:
            raise BrowserControlError(f"浏览器当前处于人工接管状态，拒绝 Connor 进行网页{action}")

    async def _request(self, method: str, path: str, *, json: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout) as client:
                res = await client.request(method, url, json=json)
                if res.status_code == 403:
                    data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
                    msg = data.get("error") or "该网址不在允许访问的白名单中"
                    raise BrowserControlError(f"浏览器操作受阻: {msg}")
                res.raise_for_status()
                if res.headers.get("content-type", "").startswith("application/json"):
                    return res.json()
                return {"ok": True}
        except httpx.ConnectError:
            raise BrowserControlError(f"无法连接 Connor 浏览器服务 ({self.base_url})，请先启动 Connor-BrowserTool") from None
        except httpx.TimeoutException:
            raise BrowserControlError("Connor 浏览器服务响应超时") from None
        except httpx.HTTPStatusError as exc:
            raise BrowserControlError(f"浏览器服务返回错误 HTTP {exc.response.status_code}") from None

    async def open(self, url: str) -> None:
        self._assert_not_paused("打开")
        await self._request("POST", "/browser/open", json={"url": url})

    async def navigate(self, url: str) -> None:
        self._assert_not_paused("导航")
        await self._request("POST", "/browser/open", json={"url": url})

    async def observe(self) -> dict:
        # 只读观察在接管和非接管期间均可安全调用
        data = await self._request("GET", "/browser/state")
        return {
            "url": data.get("url", ""),
            "title": data.get("title", ""),
            "open": bool(data.get("open", False)),
        }

    async def read_text(self) -> str:
        # 只读页面可见文字
        data = await self._request("GET", "/browser/text")
        return str(data.get("text", "") or "")

    async def screenshot(self) -> dict:
        # 截屏并返回本地图片路径
        data = await self._request("POST", "/browser/screenshot")
        return {
            "url": data.get("url", ""),
            "title": data.get("title", ""),
            "path": data.get("path", ""),
        }

    async def click(self, x: int, y: int) -> dict:
        self._assert_not_paused("点击")
        return await self._request("POST", "/browser/click", json={"x": x, "y": y, "confirm": True})

    async def type_text(self, text: str) -> dict:
        self._assert_not_paused("输入")
        return await self._request("POST", "/browser/type", json={"text": text, "confirm": True})

    async def press_key(self, key: str) -> dict:
        self._assert_not_paused("按键")
        return await self._request("POST", "/browser/press", json={"key": key, "confirm": True})

    async def scroll(self, delta_x: int = 0, delta_y: int = 700) -> dict:
        self._assert_not_paused("滚动")
        return await self._request("POST", "/browser/scroll", json={"deltaX": delta_x, "deltaY": delta_y, "confirm": True})

    async def pause_agent(self) -> None:
        """用户接管：暂停 Connor 的任何操作指令。"""
        self.paused = True

    async def resume_agent(self) -> None:
        """人工交还：恢复 Connor 操作能力。"""
        self.paused = False

    async def close(self) -> None:
        """关闭独立浏览器上下文。"""
        self.paused = False
        try:
            await self._request("POST", "/browser/close")
        except Exception:
            pass
