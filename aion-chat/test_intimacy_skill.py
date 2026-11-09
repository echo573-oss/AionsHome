import asyncio, json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from context_blocks import CTX_RULES, CTX_RUNTIME


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class IntimacySkillTests(unittest.TestCase):
    def setUp(self):
        try:
            import intimacy_skill
        except ModuleNotFoundError:
            self.fail("intimacy_skill module is not installed")
        self.skill = intimacy_skill
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_state_path = intimacy_skill._STATE_PATH; self.old_pool_path = intimacy_skill._POOL_PATH; intimacy_skill._POOL_PATH = Path(self.tempdir.name) / "intimacy_play_pool.json"; intimacy_skill._POOL_PATH.write_text(json.dumps({"categories": [{"id": "cat1", "label": "测试分类", "modes": ["private", "group"], "subtypes": ["子项A", "子项B"]}], "locations": [{"id": "loc1", "label": "测试房间", "resources": ["bed"]}], "positions": [{"label": "主位A", "modes": ["private", "group"], "requires": ["bed"]}, {"label": "主位B", "modes": ["private", "group"], "requires": ["bed"]}], "group_positions": [{"label": "三人位A", "modes": ["group"], "requires": ["bed"]}], "psychology": ["心理A", "心理B", "心理C"], "group_dynamics": ["互动A"], "final_finish_targets": ["目标A"], "final_finish_relations_group": ["先后"]}, ensure_ascii=False), encoding="utf-8")
        intimacy_skill._STATE_PATH = Path(self.tempdir.name) / "intimacy_state.json"
        intimacy_skill._ENABLED.clear()
        intimacy_skill._SESSIONS.clear()
        intimacy_skill._RECENT_CARDS.clear()
        intimacy_skill._ACTIVE_GENERATIONS.clear()

    def tearDown(self):
        if hasattr(self, "skill"):
            self.skill._STATE_PATH = self.old_state_path; self.skill._POOL_PATH = self.old_pool_path; self.skill._POOL_PATH = self.old_pool_path
            self.skill._ENABLED.clear()
            self.skill._SESSIONS.clear()
            self.skill._RECENT_CARDS.clear()
            self.skill._ACTIVE_GENERATIONS.clear()
        if hasattr(self, "tempdir"):
            self.tempdir.cleanup()

    def test_disabled_and_armed_scopes_do_not_inject_a_card(self):
        self.assertEqual(
            self.skill.build_play_prompt("private", "conv-1", "private", actor="aion"),
            "",
        )
        state = self.skill.set_enabled("private", "conv-1", True)
        self.assertEqual(state["state"], "armed")
        self.assertEqual(
            self.skill.build_play_prompt("private", "conv-1", "private", actor="aion"),
            "",
        )

    def test_active_card_uses_configured_names_and_disable_clears_it(self):
        self.skill.set_enabled("room", "room-1", True)
        self.assertEqual(
            self.skill.start_session("room", "room-1", "group")["state"],
            "active",
        )

        prompt = self.skill.build_play_prompt(
            "room",
            "room-1",
            "group",
            actor="aion",
            user_name="Ithil",
            actor_names=("Aion", "Connor"),
        )
        self.assertIn("Ithil", prompt)
        self.assertIn("Aion", prompt)
        self.assertIn("Connor", prompt)
        self.assertNotIn("Mila", prompt)
        self.assertNotIn("Cove", prompt)
        self.assertNotIn("Shep", prompt)
        self.assertEqual(self.skill.get_state("room", "room-1")["seen_actors"], ["aion"])

        state = self.skill.set_enabled("room", "room-1", False)
        self.assertEqual(state["state"], "off")
        self.assertEqual(
            self.skill.build_play_prompt("room", "room-1", "group", actor="connor"),
            "",
        )

    def test_group_reroll_waits_until_current_generation_finishes(self):
        self.skill.set_enabled("room", "room-1", True)
        self.skill.start_session("room", "room-1", "group")
        key = "room:room-1"
        current = self.skill._SESSIONS[key]["card"]

        self.assertTrue(self.skill._begin_generation("room", "room-1", "group"))
        reroll = self.skill.start_session("room", "room-1", "group")
        self.assertTrue(reroll["reroll_queued"])
        self.assertIs(self.skill._SESSIONS[key]["card"], current)
        pending = self.skill._SESSIONS[key]["pending_card"]

        self.skill._end_generation("room", "room-1")
        self.assertIs(self.skill._SESSIONS[key]["card"], pending)

    def test_preview_only_exposes_the_active_card_without_marking_it_read(self):
        preview_card = getattr(self.skill, "get_card_preview", None)
        self.assertIsNotNone(preview_card, "active cards need a read-only preview API")

        self.skill.set_enabled("private", "conv-1", True)
        self.assertIsNone(preview_card("private", "conv-1")["card"])

        self.skill.start_session("private", "conv-1", "private")
        preview = preview_card("private", "conv-1")

        self.assertEqual(preview["state"], "active")
        self.assertEqual(preview["card"]["mode"], "private")
        self.assertTrue(preview["card"]["category"])
        self.assertTrue(preview["card"]["location"])
        self.assertIn("final_finish", preview["card"])
        self.assertNotIn("category_id", preview["card"])
        self.assertNotIn("location_resources", preview["card"])
        self.assertEqual(self.skill.get_state("private", "conv-1")["seen_actors"], [])


class PrivateIntimacyInjectionTests(unittest.TestCase):
    def test_private_context_injects_nothing_when_play_prompt_is_empty(self):
        from routes import chat as chat_routes

        with (
            patch.object(chat_routes, "build_context_segments", new=AsyncMock(return_value=[])),
            patch.object(chat_routes, "build_play_prompt", return_value=""),
            patch.object(chat_routes, "build_health_summary", new=AsyncMock(return_value="")),
        ):
            result = asyncio.run(chat_routes._build_private_context_messages(
                user_name="Ithil",
                ai_name="Aion",
                model_key="unit-model",
                conv_id="conv-1",
            ))

        # 玩法提示为空时只剩时间线说明与运行时状态（当前时间），没有伪造的确认回复
        self.assertEqual([message["_ctx"] for message in result], [CTX_RULES, CTX_RUNTIME])
        self.assertFalse(any(message.get("role") == "assistant" for message in result))

    def test_private_context_adds_active_play_prompt_as_a_rule_message(self):
        from routes import chat as chat_routes

        with (
            patch.object(chat_routes, "build_context_segments", new=AsyncMock(return_value=[])),
            patch.object(chat_routes, "build_play_prompt", return_value="hidden-card"),
            patch.object(chat_routes, "build_health_summary", new=AsyncMock(return_value="")),
        ):
            result = asyncio.run(chat_routes._build_private_context_messages(
                user_name="Ithil",
                ai_name="Aion",
                model_key="unit-model",
                conv_id="conv-1",
            ))

        kinds = [message["_ctx"] for message in result]
        self.assertIn(CTX_RULES, kinds)
        rules = next(message for message in result if message["_ctx"] == CTX_RULES)
        self.assertIn("hidden-card", rules["content"])
        self.assertEqual(rules["role"], "system")
        # 玩法提示不再配一条 assistant 确认回复
        self.assertFalse(any(message.get("role") == "assistant" for message in result))


class ChatroomIntimacyInjectionTests(unittest.TestCase):
    def setUp(self):
        import intimacy_skill

        self.skill = intimacy_skill
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_state_path = intimacy_skill._STATE_PATH; self.old_pool_path = intimacy_skill._POOL_PATH; intimacy_skill._POOL_PATH = Path(self.tempdir.name) / "intimacy_play_pool.json"; intimacy_skill._POOL_PATH.write_text(json.dumps({"categories": [{"id": "cat1", "label": "测试分类", "modes": ["private", "group"], "subtypes": ["子项A", "子项B"]}], "locations": [{"id": "loc1", "label": "测试房间", "resources": ["bed"]}], "positions": [{"label": "主位A", "modes": ["private", "group"], "requires": ["bed"]}, {"label": "主位B", "modes": ["private", "group"], "requires": ["bed"]}], "group_positions": [{"label": "三人位A", "modes": ["group"], "requires": ["bed"]}], "psychology": ["心理A", "心理B", "心理C"], "group_dynamics": ["互动A"], "final_finish_targets": ["目标A"], "final_finish_relations_group": ["先后"]}, ensure_ascii=False), encoding="utf-8")
        intimacy_skill._STATE_PATH = Path(self.tempdir.name) / "intimacy_state.json"
        intimacy_skill._ENABLED.clear()
        intimacy_skill._SESSIONS.clear()
        intimacy_skill._RECENT_CARDS.clear()
        intimacy_skill._ACTIVE_GENERATIONS.clear()

    def tearDown(self):
        self.skill._STATE_PATH = self.old_state_path; self.skill._POOL_PATH = self.old_pool_path
        self.skill._ENABLED.clear()
        self.skill._SESSIONS.clear()
        self.skill._RECENT_CARDS.clear()
        self.skill._ACTIVE_GENERATIONS.clear()
        self.tempdir.cleanup()

    def test_chatroom_context_only_appends_an_active_card(self):
        import chatroom

        names = ("Ithil", "Aion", "Connor")
        disabled_history = []
        chatroom.append_intimacy_prompt(
            disabled_history, "room-1", "group", "aion", names
        )
        self.assertEqual(disabled_history, [])

        self.skill.set_enabled("room", "room-1", True)
        self.skill.start_session("room", "room-1", "group")
        active_history = []
        chatroom.append_intimacy_prompt(
            active_history, "room-1", "group", "aion", names
        )
        self.assertEqual(len(active_history), 1)
        self.assertIn("Ithil", active_history[0]["content"])
        self.assertIn("Aion", active_history[0]["content"])
        self.assertIn("Connor", active_history[0]["content"])
        self.assertEqual(active_history[0]["role"], "system")
        self.assertEqual(active_history[0]["_ctx"], CTX_RULES)


if __name__ == "__main__":
    unittest.main()
