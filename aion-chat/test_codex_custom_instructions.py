"""
Unit tests for loading local Codex custom instructions and verifying prompt order.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

from config import (
    CODEX_INSTRUCTIONS_HEADER,
    load_codex_custom_instructions,
)
import chatroom


class CodexCustomInstructionsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_instructions_disabled(self):
        settings = {"codex_instructions_enabled": False}
        res = load_codex_custom_instructions(settings=settings)
        self.assertEqual(res, "")

    def test_load_instructions_custom_path(self):
        custom_file = self.work_path / "my_rules.md"
        custom_file.write_text("Custom personal rules from disk", encoding="utf-8")

        settings = {
            "codex_instructions_enabled": True,
            "codex_instructions_path": str(custom_file),
        }
        res = load_codex_custom_instructions(settings=settings)
        self.assertEqual(res, "Custom personal rules from disk")

    def test_load_instructions_custom_path_missing(self):
        settings = {
            "codex_instructions_enabled": True,
            "codex_instructions_path": str(self.work_path / "non_existent.md"),
        }
        res = load_codex_custom_instructions(settings=settings)
        self.assertEqual(res, "")

    def test_load_instructions_from_config_toml(self):
        codex_home = self.work_path / ".codex"
        codex_home.mkdir(parents=True, exist_ok=True)
        rules_file = codex_home / "my_base.md"
        rules_file.write_text("Base instructions via config.toml", encoding="utf-8")

        config_toml = codex_home / "config.toml"
        # Escaping backslashes for toml string
        clean_path = str(rules_file).replace("\\", "\\\\")
        config_toml.write_text(f'model_instructions_file = "{clean_path}"\n', encoding="utf-8")

        with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            res = load_codex_custom_instructions(force_read=True)
            self.assertEqual(res, "Base instructions via config.toml")

    def test_load_instructions_reads_both_agents_md_and_config(self):
        codex_home = self.work_path / ".codex"
        codex_home.mkdir(parents=True, exist_ok=True)
        agents_file = codex_home / "AGENTS.md"
        agents_file.write_text("Global agents rules", encoding="utf-8")

        base_file = codex_home / "base.md"
        base_file.write_text("Base instructions", encoding="utf-8")

        config_toml = codex_home / "config.toml"
        clean_path = str(base_file).replace("\\", "\\\\")
        config_toml.write_text(f'model_instructions_file = "{clean_path}"\n', encoding="utf-8")

        with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            res = load_codex_custom_instructions(force_read=True)
            self.assertIn("Global agents rules", res)
            self.assertIn("Base instructions", res)
            self.assertEqual(res, "Global agents rules\n\nBase instructions")

    def test_load_instructions_fallback_base_instructions(self):
        codex_home = self.work_path / ".codex"
        codex_home.mkdir(parents=True, exist_ok=True)
        fallback_file = codex_home / "base-instructions.md"
        fallback_file.write_text("Fallback base instructions", encoding="utf-8")

        with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            res = load_codex_custom_instructions(force_read=True)
            self.assertEqual(res, "Fallback base instructions")

    async def test_chatroom_prompt_order_with_local_instructions(self):
        world = {
            "ai_name": "岚",
            "user_name": "用户",
            "ai_persona": "我是岚，温柔体贴。",
            "user_persona": "用户喜欢科幻文学。",
            "system_prompt": "保持日常口吻。",
            "system_prompt_enabled": True,
        }
        dummy_inst = "写文请保持细节生动丰满，不要一笔带过。"

        with (
            patch.object(chatroom, "load_worldbook", return_value=world),
            patch.object(chatroom, "get_chatroom_names", return_value=("用户", "岚", "第二AI")),
            patch.object(chatroom, "load_codex_custom_instructions", return_value=dummy_inst),
            patch.object(chatroom, "build_ability_block", AsyncMock(return_value="")),
            patch.object(chatroom, "append_intimacy_prompt"),
            patch.object(chatroom, "with_current_device_context", side_effect=lambda msgs, **kw: msgs),
            patch.object(chatroom, "fetch_merged_timeline", AsyncMock(return_value=[])),
            patch.object(chatroom, "build_memory_blocks", AsyncMock(return_value={
                "time_block": "time", "memory_block": "", "digest_result": {},
            })),
        ):
            messages, _ = await chatroom.build_aion_group_context("room_1", [])
            # 顺序断言：
            # 1. 本地个性化通用指令 / 底色设定
            # 2. 系统设定 - 岚人设
            # 3. 系统设定 - 用户信息
            # 4. 系统提示
            self.assertGreaterEqual(len(messages), 4)
            self.assertEqual(messages[0]["role"], "system")
            self.assertTrue(messages[0]["content"].startswith(CODEX_INSTRUCTIONS_HEADER))
            self.assertIn(dummy_inst, messages[0]["content"])

            self.assertEqual(messages[1]["role"], "system")
            self.assertTrue(messages[1]["content"].startswith("[系统设定 - 岚人设]"))
            self.assertIn("我是岚，温柔体贴。", messages[1]["content"])

            self.assertEqual(messages[2]["role"], "system")
            self.assertTrue(messages[2]["content"].startswith("[系统设定 - 用户信息]"))
            self.assertIn("用户喜欢科幻文学。", messages[2]["content"])

            self.assertEqual(messages[3]["role"], "system")
            self.assertTrue(messages[3]["content"].startswith("[系统提示]"))
            self.assertIn("保持日常口吻。", messages[3]["content"])

    async def test_chatroom_prompt_order_without_local_instructions(self):
        world = {
            "ai_name": "岚",
            "user_name": "用户",
            "ai_persona": "我是岚，温柔体贴。",
            "user_persona": "用户喜欢科幻文学。",
            "system_prompt": "保持日常口吻。",
            "system_prompt_enabled": True,
        }

        with (
            patch.object(chatroom, "load_worldbook", return_value=world),
            patch.object(chatroom, "get_chatroom_names", return_value=("用户", "岚", "第二AI")),
            patch.object(chatroom, "load_codex_custom_instructions", return_value=""),
            patch.object(chatroom, "build_ability_block", AsyncMock(return_value="")),
            patch.object(chatroom, "append_intimacy_prompt"),
            patch.object(chatroom, "with_current_device_context", side_effect=lambda msgs, **kw: msgs),
            patch.object(chatroom, "fetch_merged_timeline", AsyncMock(return_value=[])),
            patch.object(chatroom, "build_memory_blocks", AsyncMock(return_value={
                "time_block": "time", "memory_block": "", "digest_result": {},
            })),
        ):
            messages, _ = await chatroom.build_aion_group_context("room_1", [])
            # 当没有本地指令时，messages[0] 应为人设
            self.assertEqual(messages[0]["role"], "system")
            self.assertTrue(messages[0]["content"].startswith("[系统设定 - 岚人设]"))
            self.assertFalse(any(CODEX_INSTRUCTIONS_HEADER in m["content"] for m in messages if isinstance(m.get("content"), str)))

    async def test_connor_does_not_receive_local_codex_instructions(self):
        world = {
            "ai_name": "岚",
            "user_name": "用户",
            "ai_persona": "我是岚，温柔体贴。",
            "user_persona": "用户喜欢科幻文学。",
        }
        dummy_inst = "写文请保持细节生动丰满，不要一笔带过。"

        with (
            patch.object(chatroom, "load_worldbook", return_value=world),
            patch.object(chatroom, "get_chatroom_names", return_value=("用户", "岚", "第二AI")),
            patch.object(chatroom, "load_codex_custom_instructions", return_value=dummy_inst),
            patch.object(chatroom, "_read_connor_persona", return_value="我是Connor，理性冷静。"),
            patch.object(chatroom, "build_ability_block", AsyncMock(return_value="")),
            patch.object(chatroom, "append_intimacy_prompt"),
            patch.object(chatroom, "with_current_device_context", side_effect=lambda msgs, **kw: msgs),
            patch.object(chatroom, "fetch_merged_timeline", AsyncMock(return_value=[])),
            patch.object(chatroom, "build_memory_blocks", AsyncMock(return_value={
                "time_block": "time", "memory_block": "", "digest_result": {},
            })),
        ):
            # Connor 群聊
            connor_group_msgs, _ = await chatroom.build_connor_group_context("room_1", [])
            self.assertFalse(any(dummy_inst in m.get("content", "") for m in connor_group_msgs))
            self.assertFalse(any(CODEX_INSTRUCTIONS_HEADER in m.get("content", "") for m in connor_group_msgs))

            # Connor 1v1
            connor_1v1_msgs, _ = await chatroom.build_connor_1v1_context("room_1", [])
            self.assertFalse(any(dummy_inst in m.get("content", "") for m in connor_1v1_msgs))
            self.assertFalse(any(CODEX_INSTRUCTIONS_HEADER in m.get("content", "") for m in connor_1v1_msgs))


if __name__ == "__main__":
    unittest.main()
