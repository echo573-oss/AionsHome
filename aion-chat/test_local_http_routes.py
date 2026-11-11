"""本机校验已挂到浏览器、MCP 审批和娱乐室路由。"""
from __future__ import annotations

import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from local_http import require_local, require_local_request
from main import app


def fake_request(host="localhost:8080", client="127.0.0.1", origin=None, method="GET"):
    pairs = [(b"host", host.encode("ascii"))]
    if origin is not None:
        pairs.append((b"origin", origin.encode("ascii")))
    scope = {
        "type": "http",
        "method": method,
        "scheme": "http",
        "path": "/api/mcp-approvals/pending",
        "query_string": b"",
        "headers": pairs,
        "client": (client, 3000),
        "server": ("127.0.0.1", 8080),
    }
    return Request(scope)


LOCAL_HEADERS = {"Host": "localhost:8080"}
LOCAL_WRITE_HEADERS = {
    "Host": "localhost:8080",
    "Origin": "http://localhost:8080",
}


class RequireLocalTests(unittest.TestCase):
    def test_loopback_allowed_and_lan_rejected(self):
        require_local(fake_request())
        for client in ("192.168.1.20", "100.100.100.25", "203.0.113.20"):
            with self.subTest(client=client):
                with self.assertRaises(HTTPException) as caught:
                    require_local(fake_request(client=client))
                self.assertEqual(caught.exception.status_code, 403)

    def test_missing_origin_is_rejected_for_writes(self):
        with self.assertRaises(HTTPException) as caught:
            require_local(fake_request(origin=None, method="POST"), mutation=True)
        self.assertEqual(caught.exception.status_code, 403)
        require_local(
            fake_request(origin="http://localhost:8080", method="POST"),
            mutation=True,
        )


class LocalRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url='http://localhost:8080', client=('127.0.0.1', 50000))

    def test_browser_and_approvals_reject_non_loopback_host(self):
        bad = {"Host": "192.168.3.39:8080"}
        state = self.client.get(
            "/api/browser/connor/state?scope_id=connor_1v1&scope_type=chatroom",
            headers=bad,
        )
        self.assertEqual(state.status_code, 403)
        pending = self.client.get(
            "/api/mcp-approvals/pending?scope_type=private&scope_id=conv_1",
            headers=bad,
        )
        self.assertEqual(pending.status_code, 403)
        playground = self.client.get("/api/playground/servers", headers=bad)
        self.assertEqual(playground.status_code, 403)

    def test_browser_post_without_origin_rejected(self):
        res = self.client.post(
            "/api/browser/connor/open",
            json={"url": "https://example.com", "scope_id": "connor_1v1", "scope_type": "chatroom"},
            headers=LOCAL_HEADERS,
        )
        self.assertEqual(res.status_code, 403)

    def test_approval_decision_without_origin_rejected(self):
        res = self.client.post(
            "/api/mcp-approvals/demo/decision",
            json={
                "nonce": "unused",
                "scope_type": "private",
                "scope_id": "conv_1",
                "approve": True,
            },
            headers=LOCAL_HEADERS,
        )
        self.assertEqual(res.status_code, 403)

    def test_loopback_host_can_read_pending_list(self):
        res = self.client.get(
            "/api/mcp-approvals/pending?scope_type=private&scope_id=conv_1",
            headers=LOCAL_HEADERS,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("pending", res.json())


if __name__ == "__main__":
    unittest.main()
