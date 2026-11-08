"""日常聊天 MCP 只读桥接离线测试，不连接真实 Vault。"""
import ast
import asyncio
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
                "chat_read_grants": {"aion": ["vault_list", "vault_write"], "connor": []},
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

    async def test_connor_cannot_inherit_aion_tools(self):
        context = await mcp_chat.maybe_read_mcp_context("@Vault 查看目录", "model", actor="connor")
        self.assertIsNone(context)
        self.manager.connect.assert_not_awaited()

    async def test_unknown_actor_cannot_access_tools(self):
        context = await mcp_chat.maybe_read_mcp_context("@Vault 查看目录", "model", actor="guest")
        self.assertIsNone(context)
        self.manager.connect.assert_not_awaited()


    async def test_connor_uses_own_model_stream(self):
        self.manager.list_servers.return_value[0]["chat_read_grants"]["connor"] = ["vault_list"]
        seen = []

        async def connor_stream(messages, model_key):
            seen.append((messages[0]["role"], model_key))
            yield '{"tool":"vault_list","arguments":{"path":"/"}}'

        with patch.object(mcp_chat, "stream_ai", side_effect=AssertionError("不能调用岚的模型路径")):
            result = await mcp_chat.maybe_read_mcp_context(
                "Connor，从 @Vault 查看根目录", "Codex",
                actor="connor", model_stream=connor_stream,
            )
        self.assertIn("Notes", result)
        self.assertEqual(seen, [("system", "Codex")])
        self.manager.call_tool.assert_awaited_once()

    async def test_connor_grant_remains_independent(self):
        self.manager.list_servers.return_value[0]["chat_read_grants"]["connor"] = ["vault_search"]
        self.catalog.append({"name": "vault_search", "description": "搜索文件", "input_schema": {}})
        async def connor_stream(*_args):
            yield '{"tool":"vault_list","arguments":{"path":"/"}}'
        result = await mcp_chat.maybe_read_mcp_context(
            "Connor，从 @Vault 列目录", "Codex",
            actor="connor", model_stream=connor_stream,
        )
        self.assertIn("未通过授权校验", result)
        self.manager.call_tool.assert_not_awaited()

    def test_chatroom_user_request_requires_latest_human_match(self):
        msgs = [
            {"sender": "user", "content": "昨天 @Vault 有什么更新？"},
            {"sender": "aion", "content": "我记得昨天说过 @Vault"},
            {"sender": "user", "content": "今天聊别的"},
            {"sender": "connor", "content": "好的"},
        ]
        self.assertFalse(mcp_chat.eligible_chatroom_user_request(
            msgs, "昨天 @Vault 有什么更新？",
        ))
        self.assertFalse(mcp_chat.eligible_chatroom_user_request(
            msgs, "今天聊别的",
        ))
        msgs[2]["content"] = "现在请 @Vault 列出目录"
        self.assertTrue(mcp_chat.eligible_chatroom_user_request(
            msgs, "现在请 @Vault 列出目录",
        ))
        self.assertFalse(mcp_chat.eligible_chatroom_user_request(
            msgs, "现在请 @Vault 列出目录", ambient_context="自动巡逻唤醒",
        ))

    def test_ai_generated_at_mention_never_authorizes(self):
        msgs = [{"sender": "aion", "content": "我应该去 @Vault 看看"}]
        self.assertFalse(mcp_chat.eligible_chatroom_user_request(
            msgs, "我应该去 @Vault 看看",
        ))


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

    def test_connor_grant_is_separate(self):
        self.manager.set_chat_read_tools("Vault", ["vault_list"], actor="aion")
        self.manager.set_chat_read_tools("Vault", ["vault_search"], actor="connor")
        visible = next(s for s in self.manager.list_servers() if s["name"] == "Vault")
        self.assertEqual(visible["chat_read_grants"]["aion"], ["vault_list"])
        self.assertEqual(visible["chat_read_grants"]["connor"], ["vault_search"])
        self.assertEqual(visible["chat_read_tools"], ["vault_list"])

    def test_invalid_actor_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未知角色"):
            self.manager.set_chat_read_tools("Vault", ["vault_list"], actor="guest")



class MCPChatroomWiringTests(unittest.TestCase):
    def test_three_room_reply_paths_call_mcp_guard(self):
        source = (Path(__file__).resolve().parent / "routes" / "chatroom.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        targets = {"_generate_connor_reply", "_reply_aion", "_reply_connor"}
        found = {}
        for node in module.body:
            if not isinstance(node, ast.AsyncFunctionDef) or node.name not in targets:
                continue
            calls = [
                call for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "_maybe_inject_room_mcp_context"
            ]
            found[node.name] = calls
        self.assertEqual(set(found), targets)
        self.assertTrue(all(len(found[name]) == 1 for name in targets))


class MCPConnectionConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_server_connects_once_when_concurrent(self):
        manager = mcp_client.MCPManager()
        calls = []

        async def connect_http(name, _config):
            calls.append(name)
            await asyncio.sleep(0.01)
            manager._connections[name] = {"tools": [{"name": "vault_list"}]}
            return manager._connections[name]["tools"]

        with patch.object(mcp_client, "_load_config", return_value={
            "servers": [{"name": "Vault", "type": "http", "enabled": True}]
        }), patch.object(manager, "_connect_http", side_effect=connect_http):
            first, second = await asyncio.gather(
                manager.connect("Vault"), manager.connect("Vault")
            )
        self.assertEqual(calls, ["Vault"])
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
