"""Connor 浏览器 HTTP API 端点测试。"""
from __future__ import annotations

import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient

from main import app
from connor_browser_driver import ConnorBrowserDriver
from test_connor_browser_integration import MockTransport
from connor_browser_chat import _set_active_session_id
import browser_handoff


class ConnorBrowserRoutesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.transport = MockTransport()
        self.mock_driver = ConnorBrowserDriver("http://127.0.0.1:8787", transport=self.transport)
        self.handoff = browser_handoff.BrowserHandoff(lambda: self.mock_driver)
        self.handoff_patch = patch.object(browser_handoff, "get_connor_browser_handoff", return_value=self.handoff)
        self.handoff_patch.start()
        _set_active_session_id("chatroom", "connor_1v1", None)
        self.client = TestClient(app, base_url='http://localhost:8080', client=('127.0.0.1', 50000))
        self._raw_request = self.client.request

        def _local_request(method, url, **kwargs):
            headers = {"Host": "localhost:8080"}
            if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
                headers["Origin"] = "http://localhost:8080"
            extra = kwargs.pop("headers", None) or {}
            headers.update(extra)
            kwargs["headers"] = headers
            return self._raw_request(method, url, **kwargs)

        self.client.request = _local_request

    def tearDown(self):
        self.handoff_patch.stop()
        _set_active_session_id("chatroom", "connor_1v1", None)

    def test_routes_full_pipeline_and_control_guard(self):
        # 1. 打开网页
        res = self.client.post("/api/browser/connor/open", json={
            "url": "https://example.com/api-test",
            "scope_id": "connor_1v1",
            "scope_type": "chatroom",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertIn("已为 Connor 开启独立浏览器", data["message"])

        # 2. 状态查询
        res_state = self.client.get("/api/browser/connor/state?scope_id=connor_1v1&scope_type=chatroom")
        self.assertEqual(res_state.status_code, 200)
        self.assertIn("Mocked Page Title", res_state.json()["message"])

        # 3. 提取文本
        res_text = self.client.get("/api/browser/connor/text?scope_id=connor_1v1&scope_type=chatroom")
        self.assertEqual(res_text.status_code, 200)
        self.assertIn("example page text", res_text.json()["text"])

        # 4. 截图
        res_shot = self.client.post("/api/browser/connor/screenshot", json={
            "scope_id": "connor_1v1",
            "scope_type": "chatroom",
        })
        self.assertEqual(res_shot.status_code, 200)
        self.assertIn("mock.png", res_shot.json()["message"])

        # 5. 点击和打字
        res_click = self.client.post("/api/browser/connor/click", json={
            "x": 88, "y": 99, "scope_id": "connor_1v1", "scope_type": "chatroom",
        })
        self.assertEqual(res_click.status_code, 200)
        self.assertEqual(self.transport.last_payload["x"], 88)

        res_type = self.client.post("/api/browser/connor/type", json={
            "text": "test api typing", "scope_id": "connor_1v1", "scope_type": "chatroom",
        })
        self.assertEqual(res_type.status_code, 200)
        self.assertEqual(self.transport.last_payload["text"], "test api typing")

        # 6. 人工接管
        res_take = self.client.post("/api/browser/connor/take-control", json={
            "scope_id": "connor_1v1", "scope_type": "chatroom",
        })
        self.assertEqual(res_take.status_code, 200)
        self.assertIn("人工已接管浏览器", res_take.json()["message"])

        # 7. 接管期间阻断 Agent 变更操作
        res_blocked_click = self.client.post("/api/browser/connor/click", json={
            "x": 10, "y": 20, "scope_id": "connor_1v1", "scope_type": "chatroom",
        })
        self.assertEqual(res_blocked_click.status_code, 400)
        self.assertIn("用户接管期间不能由 Agent 操作网页", res_blocked_click.json()["detail"])

        res_blocked_type = self.client.post("/api/browser/connor/type", json={
            "text": "blocked", "scope_id": "connor_1v1", "scope_type": "chatroom",
        })
        self.assertEqual(res_blocked_type.status_code, 400)
        self.assertIn("用户接管期间不能由 Agent 操作网页", res_blocked_type.json()["detail"])

        # 8. 人工交还控制权
        res_return = self.client.post("/api/browser/connor/return-control", json={
            "scope_id": "connor_1v1", "scope_type": "chatroom",
        })
        self.assertEqual(res_return.status_code, 200)
        self.assertIn("控制权已交还", res_return.json()["message"])

        # 9. 恢复后操作
        res_resume_click = self.client.post("/api/browser/connor/click", json={
            "x": 11, "y": 22, "scope_id": "connor_1v1", "scope_type": "chatroom",
        })
        self.assertEqual(res_resume_click.status_code, 200)

        # 10. 关闭
        res_close = self.client.post("/api/browser/connor/close", json={
            "scope_id": "connor_1v1", "scope_type": "chatroom",
        })
        self.assertEqual(res_close.status_code, 200)
        self.assertIn("安全关闭", res_close.json()["message"])


if __name__ == "__main__":
    unittest.main()
