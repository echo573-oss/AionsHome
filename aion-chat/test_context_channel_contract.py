"""上下文通道契约的回归测试。

覆盖三件事：
1. 娱乐室发往供应商的请求载荷里不能出现内部元数据（_ctx / _timeline_sender）。
2. CTX_RUNTIME（时间 / 日程 / 位置 / 设备状态）是事实，不是指令——
   不该自动拿到 system 角色的高优先级。
3. MCP 工具的读取回执属于外部资料，走资料通道；写入提案回执保持原样，
   不动既有授权与确认流程。
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from context_blocks import (
    CTX_ABILITY,
    CTX_EXTERNAL,
    CTX_MEMORY,
    CTX_PERSONA,
    CTX_RULES,
    CTX_RUNTIME,
    DATA_NOTICE,
    INSTRUCTION_KINDS,
    STATE_KINDS,
    STATE_NOTICE,
    context_message,
    split_context_messages,
)


# ── 供应商请求的假客户端 ──

class _FakeResponse:
    def __init__(self, payload=None, lines=None):
        self.status_code = 200
        self._payload = payload or {}
        self._lines = lines or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def json(self):
        return self._payload

    async def aread(self):
        return json.dumps(self._payload).encode()

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeClient:
    def __init__(self, response):
        self.response = response
        self.posts = []
        self.streams = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return self.response

    def stream(self, method, url, json=None, headers=None):
        self.streams.append({"url": url, "json": json, "headers": headers})
        return self.response


def _client_factory(response):
    clients = []

    def _factory(*args, **kwargs):
        client = _FakeClient(response)
        clients.append(client)
        return client

    _factory.clients = clients
    return _factory


class PlaygroundPayloadTests(unittest.IsolatedAsyncioTestCase):
    """娱乐室直连供应商，请求体不能带内部元数据。"""

    MESSAGES = [
        {"role": "system", "content": "核心身份", "_ctx": "rules"},
        {"role": "system", "content": "人设正文", "_ctx": CTX_PERSONA},
        {"role": "user", "content": "记忆正文", "_ctx": CTX_MEMORY},
        {"role": "user", "content": "外部资料", "_ctx": CTX_EXTERNAL},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "x", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "工具输出"},
        {"role": "user", "content": "用户指令", "_timeline_sender": "user"},
    ]

    async def test_payload_contents(self):
        from routes import playground

        response = _FakeResponse(payload={
            "choices": [{"message": {"content": "好的", "tool_calls": None}}],
        })
        factory = _client_factory(response)
        with (
            patch("routes.playground.httpx.AsyncClient", new=factory),
            patch("routes.playground.get_key", return_value="test-key"),
        ):
            await playground._call_ai_with_tools(
                list(self.MESSAGES),
                tools=[{"type": "function", "function": {"name": "x"}}],
                model_cfg={"provider": "siliconflow", "model": "test-model"},
            )
        sent = factory.clients[0].posts[0]["json"]["messages"]
        for message in sent:
            self.assertNotIn("_ctx", message)
            self.assertNotIn("_timeline_sender", message)
            self.assertTrue(set(message) <= {
                "role", "content", "name", "tool_calls", "tool_call_id",
            }, message)
        # 合法的工具字段必须保留
        tool_message = next(m for m in sent if m["role"] == "tool")
        self.assertEqual("call_1", tool_message["tool_call_id"])
        assistant = next(m for m in sent if m["role"] == "assistant")
        self.assertIn("tool_calls", assistant)

    async def test_stream_payload_has_no_internal_metadata(self):
        from routes import playground

        response = _FakeResponse(lines=['data: {"choices":[{"delta":{"content":"hi"}}]}'])
        factory = _client_factory(response)
        with (
            patch("routes.playground.httpx.AsyncClient", new=factory),
            patch("routes.playground.get_key", return_value="test-key"),
        ):
            chunks = [
                chunk async for chunk in playground._stream_ai_text(
                    list(self.MESSAGES),
                    {"provider": "siliconflow", "model": "test-model"},
                )
            ]
        self.assertEqual(["hi"], chunks)
        sent = factory.clients[0].streams[0]["json"]["messages"]
        for message in sent:
            self.assertNotIn("_ctx", message)
            self.assertNotIn("_timeline_sender", message)


class RuntimeChannelTests(unittest.TestCase):
    """CTX_RUNTIME 是事实通道，不是指令通道。"""

    def test_runtime_is_not_an_instruction_kind(self):
        self.assertNotIn(CTX_RUNTIME, INSTRUCTION_KINDS)
        self.assertIn(CTX_RUNTIME, STATE_KINDS)

    def test_runtime_message_uses_user_role_and_state_notice(self):
        message = context_message(CTX_RUNTIME, "系统当前的准确时间是 12:00")
        self.assertEqual("user", message["role"])
        self.assertEqual(CTX_RUNTIME, message["_ctx"])
        self.assertIn(STATE_NOTICE, message["content"])
        self.assertIn("系统当前的准确时间是 12:00", message["content"])

    def test_instructions_still_use_system_role(self):
        for kind in (CTX_PERSONA, CTX_RULES, CTX_ABILITY):
            with self.subTest(kind=kind):
                message = context_message(kind, "内容")
                self.assertEqual("system", message["role"])

    def test_split_context_messages_separates_four_channels(self):
        instructions, state, data, conversation = split_context_messages([
            {"role": "system", "content": "persona", "_ctx": CTX_PERSONA},
            context_message(CTX_RUNTIME, "现在是 12:00"),
            context_message(CTX_MEMORY, "记忆"),
            {"role": "user", "content": "你好"},
        ])
        self.assertEqual(["persona"], [m["content"] for m in instructions])
        self.assertEqual(1, len(state))
        self.assertEqual(CTX_RUNTIME, state[0]["_ctx"])
        self.assertEqual(1, len(data))
        self.assertEqual(["你好"], [m["content"] for m in conversation])

    def test_cli_renders_state_in_its_own_section(self):
        import ai_providers

        prompt = ai_providers._build_cli_prompt([
            {"role": "system", "content": "persona", "_ctx": CTX_PERSONA},
            context_message(CTX_RUNTIME, "系统当前的准确时间是 12:00"),
            context_message(CTX_MEMORY, "记忆正文"),
            {"role": "user", "content": "你好"},
        ])
        instruction_part, _, rest = prompt.partition("[Current State]")
        self.assertIn("[System Instruction]\npersona", instruction_part)
        self.assertNotIn("12:00", instruction_part)
        self.assertIn("[Current State]", prompt)
        self.assertIn("系统当前的准确时间是 12:00", rest)
        self.assertIn("[Reference Data]", rest)
        self.assertNotIn("[System Instruction]\npersona", rest)


class McpContextChannelTests(unittest.IsolatedAsyncioTestCase):
    """MCP 读取回执归入资料通道；写入提案回执不动。"""

    async def test_read_receipt_goes_to_external_data_channel(self):
        import mcp_chat

        history = [
            {"role": "system", "content": "人设", "_ctx": CTX_PERSONA},
            {"role": "user", "content": "@Vault 列目录"},
        ]
        receipt = "[MCP 读取回执：Vault / list]\n<tool_data>\n外部内容\n</tool_data>"
        with (
            patch.object(mcp_chat, "maybe_read_mcp_context", new=AsyncMock(return_value=receipt)),
            patch.object(mcp_chat, "maybe_stage_mcp_write", new=AsyncMock(return_value=None)),
        ):
            injected = await mcp_chat.inject_mcp_context(history, "@Vault 列目录", "model")
        self.assertTrue(injected)
        inserted = history[-2]
        self.assertEqual(CTX_EXTERNAL, inserted["_ctx"])
        self.assertEqual("user", inserted["role"])
        self.assertIn(DATA_NOTICE, inserted["content"])
        self.assertIn("外部内容", inserted["content"])
        # 位置不变：仍然紧挨在最后一条用户发言之前
        self.assertEqual("@Vault 列目录", history[-1]["content"])

    async def test_write_receipt_keeps_role_and_position(self):
        import mcp_chat

        history = [
            {"role": "system", "content": "人设", "_ctx": CTX_PERSONA},
            {"role": "user", "content": "@Vault 新建文件"},
        ]
        receipt = "[MCP 写入待确认] Aion 已为 Vault 准备写入提案。"
        with (
            patch.object(mcp_chat, "maybe_read_mcp_context", new=AsyncMock(return_value=None)),
            patch.object(mcp_chat, "maybe_stage_mcp_write", new=AsyncMock(return_value=receipt)),
        ):
            injected = await mcp_chat.inject_mcp_context(history, "@Vault 新建文件", "model")
        self.assertTrue(injected)
        inserted = history[-2]
        # 写入回执是给模型的操作指引，不能被标成"资料不是指令"
        self.assertEqual("user", inserted["role"])
        self.assertNotIn("_ctx", inserted)
        self.assertEqual(receipt, inserted["content"])


class InjectorOrderingTests(unittest.IsolatedAsyncioTestCase):
    """运行时注入的 system 消息必须落在指令区，不能排在用户发言之后。"""

    async def test_autonomy_ability_lands_in_instruction_zone(self):
        import autonomy_state

        messages = [
            {"role": "system", "content": "[系统设定 - 人设]"},
            {"role": "user", "content": "[相关记忆]"},
            {"role": "user", "content": "当前用户消息"},
        ]
        with patch.object(
            autonomy_state, "autonomy_prompt_text", new=AsyncMock(return_value="【自主行动】")
        ):
            await autonomy_state.inject_autonomy_ability(messages, "aion")

        self.assertEqual([m["role"] for m in messages], ["system", "system", "user", "user"])
        self.assertEqual(messages[1]["content"], "【自主行动】")

    def test_proactive_ability_lands_in_instruction_zone(self):
        import proactive_companionship

        messages = [
            {"role": "system", "content": "[系统设定 - 人设]"},
            {"role": "user", "content": "[相关记忆]"},
            {"role": "user", "content": "当前用户消息"},
        ]
        with patch.object(
            proactive_companionship, "proactive_ability_text", return_value="【高优先级要求】"
        ):
            proactive_companionship.inject_proactive_ability(messages, "aion")

        self.assertEqual([m["role"] for m in messages], ["system", "system", "user", "user"])
        self.assertEqual(messages[1]["content"], "【高优先级要求】")

    def test_injectors_keep_system_before_user_in_a_camera_style_list(self):
        import proactive_companionship

        # camera.py 的消息形态：指令 + 状态 + 资料 + 真实对话
        messages = [
            {"role": "system", "content": "[系统设定 - 人设]"},
            {"role": "system", "content": "[历史记录说明]"},
            {"role": "user", "content": "[当前状态]"},
            {"role": "user", "content": "[相关记忆]"},
            {"role": "user", "content": "历史消息"},
            {"role": "user", "content": "当前用户消息"},
        ]
        with patch.object(
            proactive_companionship, "proactive_ability_text", return_value="【高优先级要求】"
        ):
            proactive_companionship.inject_proactive_ability(messages, "aion")

        roles = [m["role"] for m in messages]
        self.assertEqual(roles, ["system", "system", "system", "user", "user", "user", "user"])
        # system 必须全部排在 user 之前
        self.assertEqual(roles, sorted(roles, key=lambda r: 0 if r == "system" else 1))

    def test_instruction_insert_index_helper(self):
        from context_blocks import instruction_insert_index

        self.assertEqual(
            instruction_insert_index([{"role": "system"}, {"role": "system"}, {"role": "user"}]), 2
        )
        self.assertEqual(instruction_insert_index([{"role": "user"}, {"role": "user"}]), 0)
        self.assertEqual(
            instruction_insert_index([{"role": "user"}, {"role": "system"}, {"role": "user"}]), 2
        )


if __name__ == "__main__":
    unittest.main()
