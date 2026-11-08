"""Connor 浏览器集成与控制权交接测试。

验证：
1. 网页打开、读取、截图、点击、输入；
2. 人工接管（take_control）后，严格阻断 Connor 的一切点击、输入与导航操作；
3. 人工交还（return_control）后，恢复 Connor 操作；
4. 仅限 Connor 角色，非 Connor 角色受阻；
5. 独立浏览器环境，不连接密码库。
"""
from __future__ import annotations

import unittest
import httpx

from browser_handoff import BrowserControlError, BrowserHandoff
from connor_browser_driver import ConnorBrowserDriver
from connor_browser_chat import (
    execute_connor_browser_action,
    maybe_handle_connor_browser_intent,
    _set_active_session_id,
)


class MockTransport(httpx.AsyncBaseTransport):
    """虚拟 Connor-BrowserTool 8787 服务 HTTP 响应。"""

    def __init__(self):
        self.state = {"url": "https://example.com", "title": "Example Domain", "open": True}
        self.last_action = ""
        self.last_payload = {}
        self.actions_log = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        self.actions_log.append((method, path))

        if path == "/health" and method == "GET":
            return httpx.Response(200, json={"ok": True, "service": "connor-browser-tool", "version": "0.2.0"})

        if path == "/browser/open" and method == "POST":
            import json
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            self.state["url"] = body.get("url", "")
            self.state["title"] = "Mocked Page Title"
            self.state["open"] = True
            return httpx.Response(200, json=self.state)

        if path == "/browser/state" and method == "GET":
            return httpx.Response(200, json=self.state)

        if path == "/browser/text" and method == "GET":
            return httpx.Response(200, json={**self.state, "text": "This is example page text content for Connor."})

        if path == "/browser/screenshot" and method == "POST":
            return httpx.Response(200, json={**self.state, "path": "D:/AionsHome/Connor-BrowserTool/screenshots/mock.png"})

        if path == "/browser/click" and method == "POST":
            import json
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            self.last_action = "click"
            self.last_payload = body
            return httpx.Response(200, json=self.state)

        if path == "/browser/type" and method == "POST":
            import json
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            self.last_action = "type"
            self.last_payload = body
            return httpx.Response(200, json=self.state)

        if path == "/browser/press" and method == "POST":
            import json
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            self.last_action = "press"
            self.last_payload = body
            return httpx.Response(200, json=self.state)

        if path == "/browser/scroll" and method == "POST":
            import json
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            self.last_action = "scroll"
            self.last_payload = body
            return httpx.Response(200, json=self.state)

        if path == "/browser/close" and method == "POST":
            self.state["open"] = False
            return httpx.Response(200, json={"closed": True})

        return httpx.Response(404, json={"error": "Not found"})


class ConnorBrowserDriverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.transport = MockTransport()
        self.driver = ConnorBrowserDriver("http://127.0.0.1:8787", transport=self.transport)

    async def test_full_lifecycle_and_takeover_defense(self):
        # 1. 打开网页
        await self.driver.open("https://example.com/test")
        obs = await self.driver.observe()
        self.assertTrue(obs["open"])
        self.assertEqual(obs["title"], "Mocked Page Title")

        # 2. 读取文字与截图
        text = await self.driver.read_text()
        self.assertIn("example page text", text)
        shot = await self.driver.screenshot()
        self.assertIn("mock.png", shot["path"])

        # 3. Agent 操作：点击与输入
        await self.driver.click(150, 250)
        self.assertEqual(self.transport.last_payload["x"], 150)
        self.assertEqual(self.transport.last_payload["y"], 250)
        await self.driver.type_text("search keywords")
        self.assertEqual(self.transport.last_payload["text"], "search keywords")

        # 4. 人工接管：触发暂停
        await self.driver.pause_agent()
        self.assertTrue(self.driver.paused)

        # 5. 接管期间：阻断一切 Agent 变动操作
        with self.assertRaises(BrowserControlError) as cm:
            await self.driver.click(200, 300)
        self.assertIn("人工接管", str(cm.exception))

        with self.assertRaises(BrowserControlError) as cm:
            await self.driver.type_text("forbidden input")
        self.assertIn("人工接管", str(cm.exception))

        with self.assertRaises(BrowserControlError) as cm:
            await self.driver.navigate("https://example.com/other")
        self.assertIn("人工接管", str(cm.exception))

        # 接管期间只读观察依然安全可用
        state_during_human = await self.driver.observe()
        self.assertTrue(state_during_human["open"])

        # 6. 人工交还控制权：恢复 Agent 操作
        await self.driver.resume_agent()
        self.assertFalse(self.driver.paused)
        await self.driver.click(300, 400)
        self.assertEqual(self.transport.last_payload["x"], 300)

        # 7. 关闭会话
        await self.driver.close()
        self.assertFalse(self.transport.state["open"])


class ConnorBrowserHandoffAndChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.transport = MockTransport()
        self.ctrl = BrowserHandoff(lambda: ConnorBrowserDriver("http://127.0.0.1:8787", transport=self.transport))
        self.where = ("connor", "chatroom", "connor_1v1")
        _set_active_session_id("chatroom", "connor_1v1", None)

    def tearDown(self):
        _set_active_session_id("chatroom", "connor_1v1", None)

    async def test_handoff_and_chat_intent_integration(self):
        scope_type, room_id = "chatroom", "connor_1v1"

        # 1. 对话指令：打开网页
        msg = await maybe_handle_connor_browser_intent(
            "Connor，帮我打开网页 https://example.com/doc",
            room_id=room_id, scope_type=scope_type, actor="connor", handoff=self.ctrl,
        )
        self.assertIsNotNone(msg)
        self.assertIn("已为 Connor 开启独立浏览器", msg)

        # 2. 对话指令：读网页
        text_msg = await maybe_handle_connor_browser_intent(
            "读一下网页内容",
            room_id=room_id, scope_type=scope_type, actor="connor", handoff=self.ctrl,
        )
        self.assertIn("页面文字提取完成", text_msg)

        # 3. 对话指令：点击与输入
        click_msg = await maybe_handle_connor_browser_intent(
            "点击坐标 120 340",
            room_id=room_id, scope_type=scope_type, actor="connor", handoff=self.ctrl,
        )
        self.assertIn("点击完成", click_msg)
        self.assertEqual(self.transport.last_payload["x"], 120)

        type_msg = await maybe_handle_connor_browser_intent(
            "在网页输入：hello world",
            room_id=room_id, scope_type=scope_type, actor="connor", handoff=self.ctrl,
        )
        self.assertIn("输入指定文字", type_msg)
        self.assertEqual(self.transport.last_payload["text"], "hello world")

        # 4. 对话指令：用户接管
        take_msg = await maybe_handle_connor_browser_intent(
            "我来操作，接管网页",
            room_id=room_id, scope_type=scope_type, actor="connor", handoff=self.ctrl,
        )
        self.assertIn("人工已接管浏览器", take_msg)

        # 5. 接管期间：Connor 的操作请求被彻底拒绝！
        blocked_click = await execute_connor_browser_action(
            "click", scope_type=scope_type, scope_id=room_id, actor="connor", x=10, y=20, handoff=self.ctrl,
        )
        self.assertFalse(blocked_click["ok"])
        self.assertIn("用户接管期间不能由 Agent 操作网页", blocked_click["error"])

        blocked_type = await execute_connor_browser_action(
            "type", scope_type=scope_type, scope_id=room_id, actor="connor", text="try hacking", handoff=self.ctrl,
        )
        self.assertFalse(blocked_type["ok"])
        self.assertIn("用户接管期间不能由 Agent 操作网页", blocked_type["error"])

        # 6. 对话指令：交还控制权
        return_msg = await maybe_handle_connor_browser_intent(
            "操作好了，交还网页",
            room_id=room_id, scope_type=scope_type, actor="connor", handoff=self.ctrl,
        )
        self.assertIn("控制权已交还", return_msg)

        # 7. 恢复后：可以再次点击
        after_click = await execute_connor_browser_action(
            "click", scope_type=scope_type, scope_id=room_id, actor="connor", x=50, y=60, handoff=self.ctrl,
        )
        self.assertTrue(after_click["ok"])
        self.assertEqual(self.transport.last_payload["x"], 50)

        # 8. 对话指令：关闭网页
        close_msg = await maybe_handle_connor_browser_intent(
            "关闭浏览器",
            room_id=room_id, scope_type=scope_type, actor="connor", handoff=self.ctrl,
        )
        self.assertIn("已安全关闭", close_msg)

    async def test_non_connor_actor_is_rejected(self):
        res = await execute_connor_browser_action(
            "open", scope_type="chatroom", scope_id="room_1", actor="aion",
            url="https://example.com", handoff=self.ctrl,
        )
        self.assertFalse(res["ok"])
        self.assertIn("仅授权 Connor", res["error"])


if __name__ == "__main__":
    unittest.main()
