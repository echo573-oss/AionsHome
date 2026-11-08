"""日常聊天 MCP 只读桥接离线测试，不连接真实 Vault。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import mcp_chat
import mcp_client


class MCPReadBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.catalog = [{"name": "vault_list", "description": "列出目录", "input_schema": {
            "type": "object", "properties": {"path": {"type": "string"}}
        }}, {"name": "vault_write", "description": "写文件", "input_schema": {}}]
        self.manager = SimpleNamespace(
            list_servers=Mock(return_value=[{
                "name": "Vault", "enabled": True, "chat_read_tools": ["vault_list", "vault_write"],
            }]),
            connect=AsyncMock(return_value=self.catalog),
            call_tool=AsyncMock(return_value=[{"type": "text", "text": '["Notes", "Lan"]'}]),
        )
        patcher = patch.object(mcp_chat, "mcp_manager", self.manager)
        patcher.start()
        self.addCleanup(patcher.stop)
        enabled = patch.object(mcp_chat, "is_capability_enabled", return_value=True)
        enabled.start()
        self.addCleanup(enabled.stop)

    async def test_idle_chat_never_contacts_server(self):
        self.assertIsNone(await mcp_chat.maybe_read_mcp_context("今天过得怎么样", "model"))
        self.manager.connect.assert_not_awaited()

    async def test_mention_without_at_sign_stays_quiet(self):
        self.assertIsNone(await mcp_chat.maybe_read_mcp_context("Vault 最近有点大", "model"))
        self.manager.connect.assert_not_awaited()

    async def test_explicit_read_one_whitelisted_tool(self):
        async def fake_stream(*_args, **_kwargs):
            yield '{"tool":"vault_list","arguments":{"path":"/"}}'
        with patch.object(mcp_chat, "stream_ai", fake_stream):
            context = await mcp_chat.maybe_read_mcp_context("帮我从 @Vault 列出根目录", "model")
        self.assertIn("Notes", context)
        self.assertIn("vault_list", context)
        self.manager.call_tool.assert_awaited_once_with("Vault", "vault_list", {"path": "/"})

    async def test_write_tool_is_never_offered_or_executed(self):
        async def fake_stream(*args, **_kwargs):
            self.assertNotIn('"name": "vault_write"', args[0][0]["content"])
            yield '{"tool":"vault_write","arguments":{"path":"/x","content":"bad"}}'
        with patch.object(mcp_chat, "stream_ai", fake_stream):
            context = await mcp_chat.maybe_read_mcp_context("用 @Vault 查笔记", "model")
        self.assertIn("未通过授权校验", context)
        self.manager.call_tool.assert_not_awaited()

    async def test_disabled_global_capability_stays_quiet(self):
        with patch.object(mcp_chat, "is_capability_enabled", return_value=False):
            self.assertIsNone(await mcp_chat.maybe_read_mcp_context("@Vault 列出目录", "model"))
        self.manager.list_servers.assert_not_called()

    async def test_context_injected_before_user_message(self):
        async def fake_stream(*_args, **_kwargs):
            yield '{"tool":"vault_list","arguments":{"path":"/"}}'
        history = [{"role":"system","content":"设定"}, {"role":"user","content":"@Vault 列目录"}]
        with patch.object(mcp_chat, "stream_ai", fake_stream):
            injected = await mcp_chat.inject_private_mcp_context(history, "@Vault 列目录", "model")
        self.assertTrue(injected)
        self.assertEqual([item["role"] for item in history], ["system", "user", "user"])
        self.assertEqual(history[-1]["content"], "@Vault 列目录")

    async def test_remote_errors_do_not_leak_credentials(self):
        self.manager.connect.side_effect = RuntimeError("Authorization: Bearer top-secret")
        context = await mcp_chat.maybe_read_mcp_context("@Vault 目录", "model")
        self.assertNotIn("top-secret", context)
        self.assertIn("读取失败", context)


class MCPChatReadConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path_patch = patch.object(mcp_client, "MCP_SERVERS_PATH", Path(temp.name) / "servers.json")
        path_patch.start()
        self.addCleanup(path_patch.stop)
        self.manager = mcp_client.MCPManager()
        self.manager.add_server("Vault", "http", "http://127.0.0.1:8787/mcp")
        self.manager._connections["Vault"] = {
            "tools": [{"name":"vault_list"}, {"name":"vault_write"}, {"name":"vault_search"}],
        }

    def test_selected_read_tools_persist(self):
        selected = self.manager.set_chat_read_tools("Vault", ["vault_list", "vault_search"])
        self.assertEqual(selected, ["vault_list", "vault_search"])
        displayed = next(s for s in self.manager.list_servers() if s["name"] == "Vault")
        self.assertEqual(displayed["chat_read_tools"], selected)

    def test_write_tool_is_refused(self):
        with self.assertRaises(ValueError):
            self.manager.set_chat_read_tools("Vault", ["vault_write"])

    def test_unknown_tool_is_refused(self):
        with self.assertRaises(ValueError):
            self.manager.set_chat_read_tools("Vault", ["secret_read"])


if __name__ == "__main__":
    unittest.main()
