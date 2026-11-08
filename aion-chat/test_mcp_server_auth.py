"""MCP 服务器认证配置的离线单元测试。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mcp_client


class MCPServerAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path = Path(self.temp_dir.name) / "mcp_servers.json"
        path_patch = patch.object(mcp_client, "MCP_SERVERS_PATH", self.config_path)
        path_patch.start()
        self.addCleanup(path_patch.stop)
        self.manager = mcp_client.MCPManager()

    def test_headers_saved_but_not_returned_by_list(self):
        self.manager.add_server(
            "Vault",
            "http",
            "http://192.168.1.5:8000/mcp",
            headers={"Authorization": "Bearer local-secret"},
        )
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        vault = next(s for s in saved["servers"] if s["name"] == "Vault")
        self.assertEqual(vault["headers"], {"Authorization": "Bearer local-secret"})
        public = next(s for s in self.manager.list_servers() if s["name"] == "Vault")
        self.assertTrue(public["auth_configured"])
        self.assertNotIn("headers", public)
        self.assertNotIn("local-secret", repr(public))

    def test_no_auth_remains_compatible(self):
        server = self.manager.add_server("local", "sse", "http://127.0.0.1:8000/sse")
        self.assertEqual(server["headers"], {})

    def test_invalid_header_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "名称无效"):
            self.manager.add_server(
                "bad", "http", "http://localhost/mcp", headers={"Bad\nName": "secret"}
            )
        self.assertFalse(self.config_path.exists())

    def test_header_value_rejects_newlines(self):
        with self.assertRaisesRegex(ValueError, "内容无效"):
            self.manager.add_server(
                "bad", "http", "http://localhost/mcp",
                headers={"Authorization": "Bearer secret\r\nOther: nope"},
            )
        self.assertFalse(self.config_path.exists())


if __name__ == "__main__":
    unittest.main()
