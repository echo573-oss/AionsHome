"""上下文构建与模型注入的回归测试。

覆盖统一上下文结构（人设与规则 / 能力 / 运行时状态 / 记忆 / 外部资料 / 真实对话），
各模型线路的指令通道与资料通道渲染，以及主私聊、聊天室、娱乐室三条路径不再注入
"user(配置) + assistant(收到，我会…)" 这种伪对话。
"""
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import ai_providers
import chatroom
import context_builder
from context_blocks import (
    CTX_ABILITY,
    CTX_EXTERNAL,
    CTX_MEMORY,
    CTX_PERSONA,
    CTX_RULES,
    CTX_RUNTIME,
    DATA_NOTICE,
    ContextSegment,
    context_message,
    insert_context_message,
    split_context_messages,
)
from context_builder import split_memory_blocks


ROOT = Path(__file__).resolve().parent

WORLD = {
    "ai_name": "Sol", "user_name": "Mira",
    "ai_persona": "AI persona", "user_persona": "User profile",
    "system_prompt": "Creative rules",
}

# 这些话术是旧版伪对话注入留下的痕迹，目标路径里不该再出现
LEGACY_ACK_PHRASES = (
    "收到，我会",
    "好的，需要时我会使用这些指令",
    "明白了，我会",
    "收到，我会在合适的时候自然提及",
    "收到，我了解当前的游戏状况了",
    "收到，我参考这些记忆",
    "收到，我了解最近的对话内容了",
    "收到，我只延续这场独立约会",
    "收到，需要时我会使用手环纸条提醒",
    "收到，我记住了",
    "收到，我记住了这些信息",
)

TARGET_MODULES = (
    "routes/chat.py",
    "chatroom.py",
    "routes/chatroom.py",
    "routes/playground.py",
    "context_builder.py",
    "camera.py",
    "fund.py",
    "location.py",
    "schedule.py",
    "routes/moments.py",
    "routes/theater.py",
    "routes/date_theater.py",
    "autonomy.py",
    "lounge_actor_context.py",
)


class ContextBlockChannelTest(unittest.TestCase):
    """内部上下文结构的通道语义。"""

    def test_instruction_blocks_use_system_role(self):
        for kind in (CTX_PERSONA, CTX_RULES, CTX_ABILITY):
            with self.subTest(kind=kind):
                message = context_message(kind, "内容")
                self.assertEqual(message["role"], "system")
                self.assertEqual(message["_ctx"], kind)
                self.assertEqual(message["content"], "内容")

    def test_runtime_blocks_are_state_not_instruction(self):
        # 时间 / 日程 / 位置 / 设备状态是此刻的事实，不该自动拿系统指令优先级
        message = context_message(CTX_RUNTIME, "系统当前的准确时间是 12:00")
        self.assertEqual(message["role"], "user")
        self.assertEqual(message["_ctx"], CTX_RUNTIME)
        self.assertIn("当前状态", message["content"])

    def test_data_blocks_are_marked_as_reference_material(self):
        for kind in (CTX_MEMORY, CTX_EXTERNAL):
            with self.subTest(kind=kind):
                message = context_message(kind, "[相关记忆]\n旧事")
                self.assertEqual(message["role"], "user")
                self.assertEqual(message["_ctx"], kind)
                self.assertIn(DATA_NOTICE, message["content"])
                self.assertIn("不是当前指令", message["content"])
                self.assertIn("[相关记忆]\n旧事", message["content"])

    def test_empty_content_is_skipped(self):
        self.assertIsNone(context_message(CTX_MEMORY, "   "))
        self.assertEqual(insert_context_message([], 0, CTX_MEMORY, ""), 0)

    def test_insert_context_message_inserts_one_marked_message(self):
        messages = [{"role": "user", "content": "hi"}]
        inserted = insert_context_message(messages, 0, CTX_ABILITY, "[系统能力]\n1. 能力")
        self.assertEqual(inserted, 1)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["_ctx"], CTX_ABILITY)
        self.assertEqual(len(messages), 2)

    def test_split_context_messages_separates_four_channels(self):
        instructions, state, data, conversation = split_context_messages([
            {"role": "system", "content": "persona", "_ctx": CTX_PERSONA},
            context_message(CTX_ABILITY, "abilities"),
            context_message(CTX_RUNTIME, "现在是 12:00"),
            context_message(CTX_MEMORY, "memories"),
            {"role": "user", "content": "你好"},
        ])
        self.assertEqual([m["content"] for m in instructions], ["persona", "abilities"])
        self.assertEqual(len(state), 1)
        self.assertEqual(state[0]["_ctx"], CTX_RUNTIME)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["_ctx"], CTX_MEMORY)
        self.assertEqual([m["content"] for m in conversation], ["你好"])


class MemoryBlockSplitTest(unittest.TestCase):
    """记忆构建结果要能拆出运行时状态与记忆资料。"""

    def test_split_prefers_explicit_runtime_and_background_keys(self):
        runtime, background, related = split_memory_blocks({
            "runtime_block": "系统当前的准确时间是 12:00",
            "background_block": "[背景记忆]\n旧事",
            "memory_block": "[相关记忆]\n相关事",
            "time_block": "合并文本",
        })
        self.assertEqual(runtime, "系统当前的准确时间是 12:00")
        self.assertEqual(background, "[背景记忆]\n旧事")
        self.assertEqual(related, "[相关记忆]\n相关事")

    def test_split_falls_back_to_legacy_combined_time_block(self):
        runtime, background, related = split_memory_blocks({
            "time_block": "系统当前的准确时间是 12:00\n\n[背景记忆]\n旧事",
            "memory_block": "[相关记忆]\n相关事",
        })
        self.assertEqual(runtime, "系统当前的准确时间是 12:00")
        self.assertEqual(background, "[背景记忆]\n旧事")
        self.assertEqual(related, "[相关记忆]\n相关事")

    def test_split_handles_time_block_without_background(self):
        runtime, background, related = split_memory_blocks({
            "time_block": "系统当前的准确时间是 12:00",
            "memory_block": "",
        })
        self.assertEqual(runtime, "系统当前的准确时间是 12:00")
        self.assertEqual(background, "")
        self.assertEqual(related, "")


class CliPromptRenderingTest(unittest.TestCase):
    """CLI 线路（Gemini CLI / Codex CLI / Antigravity CLI）共用同一套渲染。"""

    def test_instructions_and_reference_data_use_separate_sections(self):
        prompt = ai_providers._build_cli_prompt([
            {"role": "system", "content": "persona", "_ctx": CTX_PERSONA},
            context_message(CTX_ABILITY, "abilities"),
            context_message(CTX_MEMORY, "[相关记忆]\n旧事"),
            {"role": "user", "content": "你好"},
        ])
        instruction_part, _, data_part = prompt.partition("[Reference Data]")
        self.assertIn("[System Instruction]\npersona", instruction_part)
        self.assertIn("abilities", instruction_part)
        self.assertNotIn("[相关记忆]", instruction_part)
        self.assertIn("[Reference Data]\n", prompt)
        self.assertIn("[相关记忆]\n旧事", data_part)
        self.assertIn(DATA_NOTICE, data_part)
        self.assertIn("[User]\n你好", prompt)
        self.assertTrue(prompt.rstrip().endswith("[Assistant]"))

    def test_reference_data_section_is_omitted_without_data(self):
        prompt = ai_providers._build_cli_prompt([
            {"role": "system", "content": "persona"},
            {"role": "user", "content": "你好"},
        ])
        self.assertNotIn("[Reference Data]", prompt)
        self.assertIn("[System Instruction]\npersona", prompt)

    def test_adapter_no_longer_guesses_fake_acknowledgements(self):
        # 旧实现靠固定话术反推伪回执；上下文全部带显式标记后，适配层不再猜
        prompt = ai_providers._build_cli_prompt([
            {"role": "user", "content": "abilities"},
            {"role": "assistant", "content": "好的，需要时我会使用这些指令。"},
            {"role": "user", "content": "你好"},
        ])
        self.assertNotIn("[System Instruction]", prompt)
        self.assertIn("[User]\nabilities", prompt)
        self.assertIn("[Assistant]\n好的，需要时我会使用这些指令。", prompt)
        self.assertIn("[User]\n你好", prompt)

    def test_real_assistant_history_is_not_collapsed(self):
        prompt = ai_providers._build_cli_prompt([
            {"role": "system", "content": "persona"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "在的。"},
        ])
        self.assertIn("[Assistant]\n在的。", prompt)


class ApiChannelTest(unittest.TestCase):
    """API 线路：系统指令进 systemInstruction，记忆留在会话内容里。"""

    def test_gemini_keeps_memory_out_of_system_instruction(self):
        messages = [
            {"role": "system", "content": "persona", "_ctx": CTX_PERSONA},
            context_message(CTX_MEMORY, "[相关记忆]\n旧事"),
            {"role": "user", "content": "你好"},
        ]
        system_parts = [
            {"text": m["content"]} for m in messages
            if m["role"] == "system" and m.get("content")
        ]
        self.assertEqual(system_parts, [{"text": "persona"}])
        contents = ai_providers.build_gemini_contents(messages)
        self.assertEqual([c["role"] for c in contents], ["user", "user"])
        self.assertIn("旧事", contents[0]["parts"][0]["text"])

    def test_openai_compatible_roles_stay_system_before_user(self):
        messages = [
            {"role": "system", "content": "persona", "_ctx": CTX_PERSONA},
            context_message(CTX_ABILITY, "abilities"),
            context_message(CTX_MEMORY, "[相关记忆]\n旧事"),
            {"role": "user", "content": "你好"},
        ]
        built = ai_providers.build_multimodal_messages(messages)
        roles = [m["role"] for m in built]
        self.assertEqual(roles, ["system", "system", "user", "user"])
        self.assertEqual(roles, sorted(roles, key=lambda r: 0 if r == "system" else 1))


class PrivateChatContextTest(unittest.IsolatedAsyncioTestCase):
    """主私聊的上下文构建。"""

    async def _build(self, **overrides):
        from routes import chat as chat_routes

        segments = overrides.pop("segments", [
            ContextSegment(CTX_ABILITY, "[系统能力]\n1. 能力"),
            ContextSegment(CTX_EXTERNAL, "外部回忆"),
        ])
        with (
            patch.object(chat_routes, "build_context_segments", new=AsyncMock(return_value=segments)),
            patch.object(chat_routes, "build_play_prompt", return_value=overrides.pop("play_prompt", "")),
            patch.object(chat_routes, "build_health_summary", new=AsyncMock(return_value=overrides.pop("health", ""))),
        ):
            return await chat_routes._build_private_context_messages(
                user_name="Mira",
                ai_name="Sol",
                model_key="unit-model",
                conv_id=overrides.pop("conv_id", ""),
                query_text="你好",
            )

    async def test_no_fake_acknowledgement_messages(self):
        messages = await self._build(play_prompt="hidden-card", conv_id="conv-1")
        self.assertFalse([m for m in messages if m.get("role") == "assistant"])

    async def test_channels_are_ordered_instruction_then_state_then_data(self):
        messages = await self._build(play_prompt="hidden-card", conv_id="conv-1")
        kinds = [m["_ctx"] for m in messages]
        # 能力 → 玩法提示（规则）→ 时间线说明（规则）→ 运行时状态 → 外部资料
        self.assertEqual(kinds, [CTX_ABILITY, CTX_RULES, CTX_RULES, CTX_RUNTIME, CTX_EXTERNAL])
        roles = [m["role"] for m in messages]
        # 运行时状态是事实，走 user 角色；只有指令通道拿 system
        self.assertEqual(roles, ["system", "system", "system", "user", "user"])

    async def test_ability_and_runtime_and_memory_are_all_present(self):
        messages = await self._build()
        text = "\n".join(m["content"] for m in messages)
        self.assertIn("[系统能力]\n1. 能力", text)
        self.assertIn("系统当前的准确时间是", text)
        self.assertIn("外部回忆", text)

    async def test_external_material_is_marked_as_data(self):
        messages = await self._build()
        external = next(m for m in messages if m["_ctx"] == CTX_EXTERNAL)
        self.assertIn(DATA_NOTICE, external["content"])
        self.assertEqual(external["role"], "user")


class ChatroomContextTest(unittest.IsolatedAsyncioTestCase):
    """聊天室（群聊与副角色私聊）的上下文构建。"""

    async def _build(self, builder, memory_result):
        with (
            patch.object(chatroom, "load_worldbook", return_value=WORLD),
            patch.object(chatroom, "get_chatroom_names", return_value=("Mira", "Sol", "Nova")),
            patch.object(chatroom, "_read_connor_persona", return_value="Companion persona"),
            patch.object(chatroom, "build_context_segments", AsyncMock(return_value=[
                ContextSegment(CTX_ABILITY, "[系统能力]\n1. 能力"),
                ContextSegment(CTX_EXTERNAL, "外部回忆"),
            ])),
            patch.object(chatroom, "append_intimacy_prompt"),
            patch.object(chatroom, "with_current_device_context", side_effect=lambda m, **kw: m),
            patch.object(chatroom, "fetch_merged_timeline", AsyncMock(return_value=[])),
            patch.object(chatroom, "build_memory_blocks", AsyncMock(return_value=memory_result)),
        ):
            return await builder("room", [])

    MEMORY = {
        "runtime_block": "系统当前的准确时间是 12:00",
        "background_block": "[背景记忆]\n旧事",
        "memory_block": "[相关记忆]\n相关事",
        "time_block": "系统当前的准确时间是 12:00\n\n[背景记忆]\n旧事",
        "digest_result": {},
    }

    async def test_all_three_builders_avoid_fake_dialogue(self):
        for builder in (
            chatroom.build_aion_group_context,
            chatroom.build_connor_group_context,
            chatroom.build_connor_1v1_context,
        ):
            with self.subTest(builder=builder.__name__):
                messages, _ = await self._build(builder, dict(self.MEMORY))
                self.assertFalse([m for m in messages if m.get("role") == "assistant"])

    async def test_system_instructions_always_precede_reference_material(self):
        for builder in (
            chatroom.build_aion_group_context,
            chatroom.build_connor_group_context,
            chatroom.build_connor_1v1_context,
        ):
            with self.subTest(builder=builder.__name__):
                messages, _ = await self._build(builder, dict(self.MEMORY))
                roles = [m["role"] for m in messages]
                self.assertEqual(roles, sorted(roles, key=lambda r: 0 if r == "system" else 1))

    async def test_memory_and_abilities_survive_in_the_right_channel(self):
        messages, _ = await self._build(chatroom.build_aion_group_context, dict(self.MEMORY))
        text = "\n".join(m["content"] for m in messages)
        self.assertIn("[系统能力]\n1. 能力", text)
        self.assertIn("系统当前的准确时间是 12:00", text)
        self.assertIn("[背景记忆]\n旧事", text)
        self.assertIn("[相关记忆]\n相关事", text)
        memory_messages = [m for m in messages if m.get("_ctx") == CTX_MEMORY]
        self.assertEqual(len(memory_messages), 2)
        for message in memory_messages:
            self.assertEqual(message["role"], "user")
            self.assertIn(DATA_NOTICE, message["content"])

    async def test_persona_stays_a_system_message(self):
        messages, _ = await self._build(chatroom.build_aion_group_context, dict(self.MEMORY))
        persona = [m for m in messages if "AI persona" in m.get("content", "")]
        self.assertEqual(len(persona), 1)
        self.assertEqual(persona[0]["role"], "system")
        self.assertEqual(persona[0]["_ctx"], CTX_PERSONA)


class TranscriptOrderTest(unittest.IsolatedAsyncioTestCase):
    """真实对话的顺序不能被上下文重构打乱。"""

    async def test_timeline_keeps_original_order_and_is_last(self):
        merged = [
            {"sender": "user", "content": "第一条", "created_at": 1000.0, "attachments": "[]", "source": "group"},
            {"sender": "connor", "content": "第二条", "created_at": 2000.0, "attachments": "[]", "source": "group"},
            {"sender": "user", "content": "第三条", "created_at": 3000.0, "attachments": "[]", "source": "group"},
        ]
        with (
            patch.object(chatroom, "load_worldbook", return_value=WORLD),
            patch.object(chatroom, "get_chatroom_names", return_value=("Mira", "Sol", "Nova")),
            patch.object(chatroom, "_read_connor_persona", return_value="Companion persona"),
            patch.object(chatroom, "build_context_segments", AsyncMock(return_value=[
                ContextSegment(CTX_ABILITY, "[系统能力]\n1. 能力"),
            ])),
            patch.object(chatroom, "append_intimacy_prompt"),
            patch.object(chatroom, "with_current_device_context", side_effect=lambda m, **kw: m),
            patch.object(chatroom, "fetch_merged_timeline", AsyncMock(return_value=merged)),
            patch.object(chatroom, "build_memory_blocks", AsyncMock(return_value={
                "runtime_block": "系统当前的准确时间是 12:00",
                "background_block": "",
                "memory_block": "",
                "digest_result": {},
            })),
        ):
            messages, _ = await chatroom.build_aion_group_context("room", [], context_limit=10)

        texts = [m.get("content", "") for m in messages]
        positions = []
        for marker in ("第一条", "第二条", "第三条"):
            index = next(i for i, text in enumerate(texts) if marker in text)
            positions.append(index)
        self.assertEqual(positions, sorted(positions))
        # 真实对话在所有上下文之后
        self.assertGreater(positions[-1], max(
            i for i, text in enumerate(texts) if "[系统能力]" in text
        ))


class TranscriptNoteTest(unittest.TestCase):
    """时间线读法说明属于规则通道，不是用户发言。"""

    def test_note_is_an_instruction_channel_message(self):
        note = context_builder.render_transcript_note()
        self.assertIsNotNone(note)
        self.assertEqual(note["role"], "system")
        self.assertEqual(note["_ctx"], "rules")
        self.assertIn("[历史记录说明]", note["content"])

    def test_timeline_keeps_the_note_out_of_the_conversation(self):
        merged = [{
            "sender": "user", "content": "你好", "created_at": 1000.0,
            "attachments": "[]", "source": "group",
        }]
        with patch("context_builder.load_worldbook", return_value={"ai_name": "A", "user_name": "B"}):
            with_note = context_builder.render_merged_timeline(merged, "aion")
            without_note = context_builder.render_merged_timeline(merged, "aion", include_note=False)
        self.assertEqual(with_note[0]["role"], "system")
        self.assertEqual(with_note[0]["_ctx"], "rules")
        self.assertEqual(len(without_note), len(with_note) - 1)
        # 真实对话部分完全一致
        self.assertEqual(with_note[1:], without_note)


class MigratedModuleTest(unittest.IsolatedAsyncioTestCase):
    """已迁移模块不再产生伪对话，且角色顺序合法。"""

    async def test_actor_context_has_no_fake_dialogue(self):
        import autonomy

        with (
            patch.object(autonomy, "load_worldbook", return_value={
                "ai_name": "Sol", "user_name": "Mira",
                "ai_persona": "AI persona", "user_persona": "User profile",
            }),
            patch.object(autonomy, "_names", return_value=("Mira", "Sol", "Nova")),
            patch.object(autonomy, "fetch_merged_timeline", AsyncMock(return_value=[])),
        ):
            messages = await autonomy._actor_context("aion", include_history=False)
        self.assertFalse([m for m in messages if m.get("role") == "assistant"])
        self.assertTrue(messages)
        for message in messages:
            self.assertEqual(message["role"], "system")
            self.assertIn(message["_ctx"], ("persona", "rules"))

    async def test_actor_context_orders_system_before_user(self):
        import autonomy

        merged = [{
            "sender": "user", "content": "你好", "created_at": 1000.0,
            "attachments": "[]", "source": "group",
        }]
        with (
            patch.object(autonomy, "load_worldbook", return_value={
                "ai_name": "Sol", "user_name": "Mira",
                "ai_persona": "AI persona", "user_persona": "User profile",
            }),
            patch.object(autonomy, "_names", return_value=("Mira", "Sol", "Nova")),
            patch.object(autonomy, "_latest_group_room_id", AsyncMock(return_value=None)),
            patch.object(autonomy, "fetch_merged_timeline", AsyncMock(return_value=merged)),
        ):
            messages = await autonomy._actor_context("aion", limit=10)
        roles = [m.get("role") for m in messages]
        self.assertEqual(roles, sorted(roles, key=lambda r: 0 if r == "system" else 1))
        self.assertFalse([m for m in messages if m.get("role") == "assistant"])


class NoFakeDialogueSourceTest(unittest.TestCase):
    """目标路径里不应再出现旧的伪对话注入。"""

    def test_target_modules_have_no_legacy_acknowledgement_phrases(self):
        for name in TARGET_MODULES:
            text = (ROOT / name).read_text(encoding="utf-8")
            for phrase in LEGACY_ACK_PHRASES:
                with self.subTest(module=name, phrase=phrase):
                    self.assertNotIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
