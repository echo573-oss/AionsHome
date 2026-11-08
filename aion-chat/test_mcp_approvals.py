"""MCP 写入审批离线测试：全部使用假服务器，不触碰真实文件。"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import mcp_approvals
import mcp_chat
import mcp_client


class ApprovalStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = mcp_approvals.ApprovalRegistry()
        self.kwargs = dict(
            scope_type="private", scope_id="conv_1", actor="aion",
            server="Vault", tool="vault_create",
            arguments={"path": "/Test.md", "content": "hello"},
            request_key="message-123",
        )

    async def test_creating_approval_never_calls_mcp(self):
        with patch.object(mcp_approvals.mcp_manager, "call_tool", new_callable=AsyncMock) as tool:
            item = await self.store.stage(**self.kwargs)
            self.assertEqual(item.state, "pending")
            pending = await self.store.pending_for("private", "conv_1")
            self.assertEqual(pending[0]["arguments"], self.kwargs["arguments"])
            tool.assert_not_awaited()

    async def test_single_use_and_decline(self):
        item = await self.store.stage(**self.kwargs)
        await self.store.claim(item.approval_id, item.nonce, "private", "conv_1", approve=False)
        self.assertEqual(await self.store.pending_for("private", "conv_1"), [])
        with self.assertRaises(ValueError):
            await self.store.claim(item.approval_id, item.nonce, "private", "conv_1", approve=True)

    async def test_claim_race_permits_one_only(self):
        item = await self.store.stage(**self.kwargs)
        async def attempt():
            try:
                await self.store.claim(item.approval_id, item.nonce, "private", "conv_1", approve=True)
                return True
            except ValueError:
                return False
        result = await asyncio.gather(attempt(), attempt())
        self.assertEqual(result, [True, False])

    async def test_processed_request_not_repeated_even_after_claim(self):
        item = await self.store.stage(**self.kwargs)
        await self.store.claim(item.approval_id, item.nonce, "private", "conv_1", approve=True)
        with self.assertRaisesRegex(ValueError, "重复提交"):
            await self.store.stage(**self.kwargs)

    async def test_wrong_nonce_or_wrong_room_rejected(self):
        item = await self.store.stage(**self.kwargs)
        with self.assertRaises(ValueError):
            await self.store.claim(item.approval_id, "wrong", "private", "conv_1", approve=True)
        with self.assertRaises(ValueError):
            await self.store.claim(item.approval_id, item.nonce, "chatroom", "room2", approve=True)
        self.assertEqual(len(await self.store.pending_for("private", "conv_1")), 1)

    async def test_expired_approval_pruned(self):
        item = await self.store.stage(**self.kwargs)
        item.created_at = time.time() - mcp_approvals.TTL_SECONDS - 1
        self.assertEqual(await self.store.pending_for("private", "conv_1"), [])
        with self.assertRaises(ValueError):
            await self.store.claim(item.approval_id, item.nonce, "private", "conv_1", approve=True)

    async def test_group_same_request_not_doubled(self):
        item = await self.store.stage(**self.kwargs)
        other = await self.store.stage(**{**self.kwargs, "actor": "connor"})
        self.assertIs(other, item)

    async def test_arguments_must_fit_full_preview(self):
        args = {**self.kwargs, "arguments": {"content": "x" * 5000}}
        with self.assertRaises(ValueError):
            await self.store.stage(**args)


class ApprovedExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.item = mcp_approvals.PendingWrite(
            approval_id="id", nonce="nonce", scope_type="private", scope_id="one",
            actor="aion", server="Vault", tool="vault_create",
            arguments={"path": "/note.md", "content": "hello"},
            request_key="test", created_at=time.time(),
        )
        self.manager = SimpleNamespace(
            list_servers=Mock(return_value=[{
                "name": "Vault", "enabled": True,
                "chat_write_grants": {"aion": ["vault_create"], "connor": []},
            }]),
            connect=AsyncMock(return_value=[{"name": "vault_create"}]),
            call_tool=AsyncMock(return_value=[{"type": "text", "text": "saved"}]),
        )
        a = patch.object(mcp_approvals, "mcp_manager", self.manager)
        b = patch.object(mcp_approvals, "is_capability_enabled", return_value=True)
        a.start(); b.start()
        self.addCleanup(a.stop); self.addCleanup(b.stop)

    async def test_only_previously_stored_arguments_execute(self):
        result = await mcp_approvals.execute_approved_write(self.item)
        self.assertEqual(result, "saved")
        self.manager.call_tool.assert_awaited_once_with(
            "Vault", "vault_create",
            {"path": "/note.md", "content": "hello"}, fail_on_error=True,
        )

    async def test_revoked_actor_permission_blocks_execution(self):
        self.manager.list_servers.return_value[0]["chat_write_grants"]["aion"] = []
        with self.assertRaises(ValueError):
            await mcp_approvals.execute_approved_write(self.item)
        self.manager.call_tool.assert_not_awaited()

    async def test_tool_disappeared_blocks_execution(self):
        self.manager.connect.return_value = [{"name": "other_create"}]
        with self.assertRaises(ValueError):
            await mcp_approvals.execute_approved_write(self.item)
        self.manager.call_tool.assert_not_awaited()

    async def test_disabled_capability_blocks_execution(self):
        with patch.object(mcp_approvals, "is_capability_enabled", return_value=False):
            with self.assertRaises(ValueError):
                await mcp_approvals.execute_approved_write(self.item)
        self.manager.call_tool.assert_not_awaited()


class ChatWriteStagingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = SimpleNamespace(
            list_servers=Mock(return_value=[{
                "name": "Vault", "enabled": True,
                "chat_write_grants": {"aion": ["vault_create"], "connor": []},
                "chat_read_grants": {"aion": ["vault_list"], "connor": []},
            }]),
            connect=AsyncMock(return_value=[{"name": "vault_create", "description": "create note", "input_schema": {
                "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}
            }}]),
            call_tool=AsyncMock(),
        )
        self.store = mcp_approvals.ApprovalRegistry()
        self.p1 = patch.object(mcp_chat, "mcp_manager", self.manager)
        self.p2 = patch.object(mcp_chat, "is_capability_enabled", return_value=True)
        self.p3 = patch.object(mcp_chat.approval_registry, "stage", wraps=self.store.stage)
        self.p1.start(); self.p2.start(); self.p3.start()
        self.addCleanup(self.p1.stop);self.addCleanup(self.p2.stop);self.addCleanup(self.p3.stop)

    async def test_explicit_write_only_stages(self):
        async def choose(*_args, **_kwargs):
            yield json.dumps({"tool": "vault_create", "arguments": {
                "path": "/note.md", "content": "test",
            }})
        with patch.object(mcp_chat, "stream_ai", choose):
            history = [{"role": "user", "content": "帮我向 @Vault 新建笔记 /note.md 内容test"}]
            ok = await mcp_chat.inject_mcp_context(
                history, history[0]["content"], "model",
                scope_type="private", scope_id="conv_22",
            )
        self.assertTrue(ok)
        self.assertIn("待确认", history[0]["content"])
        self.assertIn("待确认", history[0]["content"])
        self.manager.call_tool.assert_not_awaited()
        self.assertEqual(len(await self.store.pending_for("private", "conv_22")), 1)

    async def test_write_grant_missing_never_calls_model(self):
        self.manager.list_servers.return_value[0]["chat_write_grants"]["aion"] = []
        async def forbidden(*_args, **_kwargs):
            raise AssertionError("not allowed")
            yield ""
        with patch.object(mcp_chat, "stream_ai", forbidden):
            result = await mcp_chat.maybe_stage_mcp_write(
                "帮我向 @Vault 写入笔记", "model",
                actor="aion", scope_type="private", scope_id="conv_3",
            )
        self.assertIn("尚未获得", result)
        self.manager.connect.assert_not_awaited()

    def test_read_intent_is_not_write(self):
        self.assertFalse(mcp_chat.is_explicit_write_intent("从 @Vault 查看保存的笔记"))
        self.assertTrue(mcp_chat.is_explicit_write_intent("把这段话写入 @Vault"))

    def test_bad_write_names_are_blocked(self):
        for name in ("vault_delete", "vault_update_password", "run_shell", "send_email", "vault_share"):
            self.assertFalse(mcp_client.is_chat_write_tool_name(name))
        self.assertTrue(mcp_client.is_chat_write_tool_name("vault_create"))


class WriteGrantConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.patch = patch.object(mcp_client, "MCP_SERVERS_PATH", Path(temp.name) / "servers.json")
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.manager = mcp_client.MCPManager()
        self.manager.add_server("Vault", "http", "http://localhost:8000/mcp")
        self.manager._connections["Vault"] = {"tools": [
            {"name": "vault_create"}, {"name": "vault_delete"}, {"name": "vault_list"}
        ]}

    def test_grants_separate_by_role(self):
        self.manager.set_chat_write_tools("Vault", ["vault_create"], actor="aion")
        self.assertEqual(self.manager.list_servers()[0]["chat_write_grants"]["aion"], ["vault_create"])
        self.assertEqual(self.manager.list_servers()[0]["chat_write_grants"]["connor"], [])

    def test_destructive_tool_cannot_be_granted(self):
        with self.assertRaises(ValueError):
            self.manager.set_chat_write_tools("Vault", ["vault_delete"])


if __name__ == "__main__":
    unittest.main()
