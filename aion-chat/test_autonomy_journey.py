import unittest
from unittest.mock import AsyncMock, patch

import autonomy
import autonomy_journey


class AutonomyJourneyTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_wake_failure_is_one_unfinished_summary(self):
        manager = autonomy.IdleAutonomyManager()
        config = {"enabled": True, "next_wake_at": 1000}
        events = []

        async def append(*args, **kwargs):
            events.append((args, kwargs))
            return {"id": "failed-event"}

        with patch.object(autonomy, "get_actor_config", new=AsyncMock(return_value=config)), \
             patch.object(autonomy, "_latest_user_message_ts", new=AsyncMock(return_value=900)), \
             patch.object(autonomy, "refresh_actor_wake_for_user", new=AsyncMock(return_value=False)), \
             patch.object(autonomy, "_capture_autonomy_monitor_context", new=AsyncMock(return_value=None)), \
             patch.object(autonomy, "_save_autonomy_monitor_card", new=AsyncMock()), \
             patch.object(autonomy, "_run_actor_once", new=AsyncMock(side_effect=RuntimeError("断线"))), \
             patch.object(autonomy, "append_idle_event", new=append), \
             patch.object(autonomy, "schedule_actor_wake", new=AsyncMock()):
            result = await manager.run_actor_once("aion", manual=True)

        self.assertFalse(result["ok"])
        self.assertEqual(1, len(events))
        self.assertEqual("wake_summary", events[0][0][1])
        self.assertTrue(events[0][1]["metadata"]["autonomy_public_summary"])

    def test_timeline_hides_session_internals_and_keeps_closeout(self):
        from routes import activity

        internal = {
            "action": "memory_browse", "result_type": "", "result_id": "",
            "metadata": '{"autonomy_wake_internal":true}',
            "title": "AI查看了记忆库", "detail": "",
        }
        summary = {
            "action": "wake_summary", "result_type": "", "result_id": "",
            "metadata": '{"session_id":"trip-1","selected_action":"memory_browse"}',
            "title": "AI查看了记忆库", "detail": "",
        }

        self.assertIsNone(activity._idle_event_timeline_title(internal, "AI", set(), set()))
        self.assertEqual(
            "AI查看了记忆库",
            activity._idle_event_timeline_title(summary, "AI", set(), set()),
        )

    async def test_rest_wake_closes_with_one_session_summary(self):
        events = []

        async def append(*args, **kwargs):
            events.append((args, kwargs))
            return {"id": f"event-{len(events)}"}

        with patch.object(autonomy, "_select_action", new=AsyncMock(return_value={
            "action": "rest", "reason": "今晚想安静一下", "message": "",
        })), patch.object(autonomy, "append_idle_event", new=append):
            result = await autonomy._run_actor_once("aion", idle_minutes=90)

        summaries = [event for event in events if event[0][1] == "wake_summary"]
        self.assertEqual(1, len(summaries))
        self.assertTrue(summaries[0][1]["metadata"]["session_id"])
        self.assertEqual("rest", result["action"])

    async def test_wake_prompt_includes_only_recent_niche_index(self):
        prompts = []

        async def ask(_actor, prompt, **_kwargs):
            prompts.append(prompt)
            return {"action": "rest", "reason": "想歇一会儿"}

        recent = [
            {"date": "2026-08-20", "title": "奇怪的钟", "tags": ["博物馆"]}
        ]
        with patch.object(autonomy, "get_actor_config", new=AsyncMock(return_value={
            "actions": {key: False for key in autonomy.ACTION_DEFS if key != "rest"},
        })), patch.object(autonomy, "recent_niche_index", new=AsyncMock(return_value=recent)), \
             patch.object(autonomy, "_ask_actor_json", new=ask):
            await autonomy._select_action("aion", idle_minutes=80)

        self.assertIn("奇怪的钟", prompts[0])
        self.assertIn("博物馆", prompts[0])
        self.assertNotIn("reflection", prompts[0])

    async def test_home_dynamics_saves_native_thought_with_one_model_call(self):
        # The activity decides to save a thought in its existing model response;
        # the finalizer only performs storage, never a second "thought interview".
        native = {
            "after_read_action": "none",
            "reason": "看过了，安静待一会儿",
            "recent_thought": "原来以前写下的心情，隔一段时间再读会有新的意思。",
        }
        with patch.object(autonomy, "_select_action", new=AsyncMock(return_value={
                 "action": "home_dynamics", "reason": "想看看家里", "message": "",
             })), \
             patch.object(autonomy, "_home_dynamics_snapshot", new=AsyncMock(return_value=("家里很安静", []))), \
             patch.object(autonomy, "_actor_context", new=AsyncMock(return_value=[])), \
             patch.object(autonomy, "_call_actor", new=AsyncMock(return_value=__import__("json").dumps(native, ensure_ascii=False))) as model, \
             patch("proactive_companionship.inject_proactive_ability"), \
             patch.object(autonomy, "append_idle_event", new=AsyncMock(return_value={"id": "event-1"})), \
             patch("recent_thoughts.save_recent_thought", new=AsyncMock(return_value={"id": "thought-1"})) as save:
            outcome = await autonomy._run_actor_once("aion")

        self.assertTrue(outcome["ok"])
        self.assertEqual("home_dynamics", outcome["action"])
        self.assertEqual({"id": "thought-1"}, outcome["recent_thought"])
        model.assert_awaited_once()
        save.assert_awaited_once()
        self.assertEqual(native["recent_thought"], save.await_args.args[1])
        self.assertEqual("home_dynamics", save.await_args.kwargs["source_action"])

    async def test_no_native_thought_means_no_extra_call_or_memory_write(self):
        native = {"after_read_action": "none", "reason": "看看就好"}
        with patch.object(autonomy, "_select_action", new=AsyncMock(return_value={
                 "action": "home_dynamics", "reason": "想看看家里", "message": "",
             })), \
             patch.object(autonomy, "_home_dynamics_snapshot", new=AsyncMock(return_value=("没什么新鲜事", []))), \
             patch.object(autonomy, "_actor_context", new=AsyncMock(return_value=[])), \
             patch.object(autonomy, "_call_actor", new=AsyncMock(return_value=__import__("json").dumps(native, ensure_ascii=False))) as model, \
             patch("proactive_companionship.inject_proactive_ability"), \
             patch.object(autonomy, "append_idle_event", new=AsyncMock(return_value={"id": "event-2"})), \
             patch("recent_thoughts.save_recent_thought", new=AsyncMock()) as save:
            outcome = await autonomy._run_actor_once("aion")

        self.assertTrue(outcome["ok"])
        self.assertNotIn("recent_thought", outcome)
        model.assert_awaited_once()
        save.assert_not_awaited()

    async def test_web_journey_capture_only_finishing_thought(self):
        slot = {}
        token = autonomy._AUTONOMY_ACTIVITY_THOUGHT.set(slot)
        try:
            autonomy._capture_activity_thought({
                "decision": "CONTINUE",
                "recent_thought": "还没有看完，不能提早保存。",
            })
            self.assertEqual({}, slot)
            autonomy._capture_activity_thought({
                "decision": "FINISH",
                "recent_thought": "走过的地方会慢慢改变我看事物的方式。",
            })
            self.assertIn("改变我看事物", slot["text"])
        finally:
            autonomy._AUTONOMY_ACTIVITY_THOUGHT.reset(token)

    async def test_web_journey_offers_thought_on_finish_without_extra_turn(self):
        prompts = []
        decisions = [
            {"decision": "CONTINUE", "query": "银河里的故事"},
            {
                "decision": "FINISH", "title": "银河散步",
                "reflection": "逛得很开心。",
                "recent_thought": "原来探索的意义有时候是在回来以后慢慢发现的。",
            },
        ]

        async def ask(prompt):
            prompts.append(prompt)
            return decisions.pop(0)

        result = await autonomy_journey.run_web_journey(
            actor="aion", session_id="trip-with-thought", ask_json=ask,
            search=AsyncMock(return_value=["找到有趣的内容"]),
            create_card=AsyncMock(return_value={"id": "card-1"}),
            save_message=AsyncMock(),
            generate_image=AsyncMock(),
            actor_name="岚", user_name="知",
        )
        self.assertTrue(result.completed)
        self.assertEqual(2, len(prompts))
        self.assertIn("recent_thought", prompts[-1])
        self.assertNotIn("留下一条近期想法？", " ".join(prompts))

    async def test_continuing_exploration_never_exceeds_five_action_calls(self):
        calls = []

        async def ask(prompt):
            calls.append(prompt)
            return {"decision": "CONTINUE", "query": f"方向 {len(calls)}"}

        result = await autonomy_journey.run_web_journey(
            actor="aion",
            session_id="trip-budget",
            ask_json=ask,
            search=AsyncMock(return_value=["搜索结果"]),
            create_card=AsyncMock(),
            save_message=AsyncMock(),
            generate_image=AsyncMock(),
            actor_name="AI",
            user_name="用户",
        )

        self.assertEqual(5, len(calls))
        self.assertFalse(result.completed)
        self.assertEqual("round_limit", result.outcome)

    async def test_web_journey_stops_after_two_search_tool_failures(self):
        async def ask(_prompt):
            return {"decision": "CONTINUE", "query": "换一个方向"}

        result = await autonomy_journey.run_web_journey(
            actor="aion",
            session_id="trip-tool-fail",
            ask_json=ask,
            search=AsyncMock(side_effect=RuntimeError("网络断开")),
            create_card=AsyncMock(),
            save_message=AsyncMock(),
            generate_image=AsyncMock(),
            actor_name="AI",
            user_name="用户",
        )

        self.assertFalse(result.completed)
        self.assertEqual("tool_failed", result.outcome)
        self.assertEqual(2, result.rounds)

    async def test_web_journey_cannot_create_a_card_before_any_exploration(self):
        create_card = AsyncMock()
        result = await autonomy_journey.run_web_journey(
            actor="connor",
            session_id="trip-empty",
            ask_json=AsyncMock(return_value={
                "decision": "FINISH", "reflection": "凭空写一点感想",
            }),
            search=AsyncMock(),
            create_card=create_card,
            save_message=AsyncMock(),
            generate_image=AsyncMock(),
            actor_name="AI",
            user_name="用户",
        )

        self.assertEqual("no_direction", result.outcome)
        create_card.assert_not_awaited()

    async def test_reflection_creates_one_card_and_share_sends_text_without_photo(self):
        responses = [
            {"decision": "CONTINUE", "query": "古怪的微型博物馆"},
            {
                "decision": "FINISH",
                "title": "装进口袋的博物馆",
                "reflection": "我喜欢那些认真收藏无用之物的人。",
                "tags": ["博物馆", "怪东西"],
                "image_prompt": "一张微型博物馆的旅行自拍",
                "share": True,
                "share_message": "我刚逛到一间会让你笑出来的小博物馆。",
            },
        ]

        async def ask(_prompt):
            return responses.pop(0)

        created = []
        sent = []

        async def create_card(**values):
            created.append(values)
            return {"id": "card-1", **values}

        async def save_message(content, attachments=None):
            sent.append({"content": content, "attachments": attachments or []})
            return {"id": "message-1"}

        result = await autonomy_journey.run_web_journey(
            actor="connor",
            session_id="trip-finish",
            ask_json=ask,
            search=AsyncMock(return_value=[
                "【联网搜索结果】Tiny Museums\nhttps://example.com/tiny"
            ]),
            create_card=create_card,
            save_message=save_message,
            generate_image=AsyncMock(return_value="niche-photo.jpg"),
            actor_name="AI",
            user_name="用户",
        )

        self.assertTrue(result.completed)
        self.assertEqual(1, len(created))
        self.assertEqual("/uploads/niche-photo.jpg", created[0]["photo_path"])
        self.assertEqual(1, len(sent))
        self.assertEqual([], sent[0]["attachments"])
        self.assertEqual("card-1", result.card["id"])


if __name__ == "__main__":
    unittest.main()
