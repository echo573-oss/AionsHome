import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite

import recent_thoughts
import autonomy
from memory import memory_kind_for_type, memory_kind_label


CREATE_MEMORY_TABLE = """
CREATE TABLE memories (
    id TEXT PRIMARY KEY, content TEXT NOT NULL, type TEXT DEFAULT 'event',
    created_at REAL NOT NULL, source_conv TEXT, embedding BLOB,
    keywords TEXT DEFAULT '', importance REAL DEFAULT 0.5,
    source_start_ts REAL, source_end_ts REAL, unresolved INTEGER DEFAULT 0,
    source_msg_id TEXT, evidence_summary TEXT DEFAULT '',
    evidence_detail_level TEXT DEFAULT 'summary',
    archive_state TEXT DEFAULT 'active', archived_at REAL
)
"""

CREATE_CHATROOM_MEMORY_TABLE = """
CREATE TABLE chatroom_memories (
    id TEXT PRIMARY KEY, room_id TEXT, scope TEXT, content TEXT,
    keywords TEXT, importance REAL, embedding BLOB,
    source_start_ts REAL, source_end_ts REAL, created_at REAL,
    unresolved INTEGER, source_msg_id TEXT, memory_kind TEXT,
    evidence_summary TEXT, evidence_detail_level TEXT,
    archive_state TEXT DEFAULT 'active', archived_at REAL, source_conv TEXT
)
"""


class LanThoughtMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "thoughts.db"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(CREATE_MEMORY_TABLE)
            await db.execute(CREATE_CHATROOM_MEMORY_TABLE)
            await db.commit()
        self.db_patch = patch.object(
            recent_thoughts, "get_db", side_effect=lambda: aiosqlite.connect(self.db_path)
        )
        self.db_patch.start()
        self.vector_patch = patch(
            "memory.get_embedding", new=AsyncMock(return_value=[0.1, 0.2, 0.3])
        )
        self.embedding = self.vector_patch.start()
        self.notify_patch = patch.object(
            recent_thoughts, "_notify_thought_memory", new=AsyncMock()
        )
        self.notify = self.notify_patch.start()

    async def asyncTearDown(self):
        self.notify_patch.stop()
        self.vector_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    async def test_lan_thought_is_an_independent_memory_category(self):
        self.assertEqual("lan_thought", memory_kind_for_type("lan_thought"))
        self.assertEqual("岚的想法", memory_kind_label("lan_thought"))
        self.assertEqual("lumi_thought", memory_kind_for_type("lumi_thought"))
        self.assertEqual("Lumi的想法", memory_kind_label("lumi_thought"))
        self.assertEqual("long_term", memory_kind_for_type("important"))

    async def test_saves_one_vector_atom_and_no_three_offer_expiry(self):
        item = await recent_thoughts.save_recent_thought(
            "aion", "我还想重新读一下那天的话。",
            source_action="diary_browse", source_ref="di_aion_1",
        )
        self.assertIsNotNone(item)
        self.assertTrue(item["embedding_ready"])
        self.embedding.assert_awaited_once()
        self.notify.assert_awaited_once()
        self.assertIsNone(await recent_thoughts.save_recent_thought(
            "aion", "我还想重新读一下那天的话。", source_action="diary_browse"
        ))
        self.assertEqual("", await recent_thoughts.take_recent_thoughts_for_chat("aion"))
        rows = await recent_thoughts.list_recent_thoughts("aion")
        self.assertEqual(1, len(rows))
        self.assertEqual("diary_browse", rows[0]["source_action"])
        self.assertEqual("di_aion_1", rows[0]["source_ref"])
        self.assertEqual("我还想重新读一下那天的话。", rows[0]["content"])

        async with aiosqlite.connect(self.db_path) as db:
            record = await (await db.execute(
                "SELECT type,content,embedding,source_msg_id,source_conv,importance FROM memories"
            )).fetchone()
        self.assertEqual("lan_thought", record[0])
        self.assertTrue(record[1].startswith("岚的想法："))
        self.assertIsNotNone(record[2])
        self.assertEqual("[]", record[3])
        self.assertEqual("autonomy:diary_browse:di_aion_1", record[4])
        self.assertEqual(0.8, record[5])

    async def test_vector_recall_finds_thought_but_recency_does_not_push_it(self):
        import memory

        item = await recent_thoughts.save_recent_thought(
            "aion", "记忆应当保留理解变化的过程，而不仅是当时的结论。",
            source_action="diary_browse", source_ref="di_aion_history",
        )
        # The same main-memory vector search used in real chat.
        with patch.object(memory, "get_db", side_effect=lambda: aiosqlite.connect(self.db_path)):
            recalled, _ = await memory.recall_memories(
                "记忆和理解怎样变化", query_keywords=[], threshold=0.45,
            )
            recent, _ = await memory.build_surfacing_memories(
                topic="", keywords=[], max_total=8,
            )
        self.assertIn(item["id"], [m["id"] for m in recalled])
        self.assertNotIn(item["id"], [m["id"] for m in recent])

    async def test_archive_keeps_atom_but_disables_recall(self):
        item = await recent_thoughts.save_recent_thought(
            "aion", "这条想法暂时不再需要参与召回了。", source_action="home_dynamics",
        )
        self.assertTrue(await recent_thoughts.archive_recent_thought("aion", item["id"]))
        self.assertFalse(await recent_thoughts.archive_recent_thought("aion", item["id"]))
        self.assertEqual([], await recent_thoughts.list_recent_thoughts("aion"))
        async with aiosqlite.connect(self.db_path) as db:
            row = await (await db.execute(
                "SELECT archive_state,embedding FROM memories WHERE id=?", (item["id"],)
            )).fetchone()
        self.assertEqual("cold", row[0])
        self.assertIsNotNone(row[1])

    async def test_legacy_aion_thoughts_migrate_without_duplicates(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE recent_thoughts (
                    id TEXT PRIMARY KEY, actor TEXT, content TEXT,
                    source_action TEXT,source_ref TEXT,status TEXT,created_at REAL
                )
            """)
            await db.execute(
                "INSERT INTO recent_thoughts VALUES(?,?,?,?,?,?,?)",
                ("thought_old_1", "aion", "一段以前留下的想法，应该还记得。",
                 "memory_browse", "day_1", "active", time.time()-3600),
            )
            await db.commit()
        first = await recent_thoughts.list_recent_thoughts("aion")
        second = await recent_thoughts.list_recent_thoughts("aion")
        self.assertEqual(1, len(first))
        self.assertEqual(first, second)
        self.assertEqual("mem_lan_thought_old_1", first[0]["id"])
        async with aiosqlite.connect(self.db_path) as db:
            legacy_count = (await (await db.execute(
                "SELECT COUNT(*) FROM recent_thoughts WHERE actor='aion'"
            )).fetchone())[0]
            memory_count = (await (await db.execute(
                "SELECT COUNT(*) FROM memories WHERE type='lan_thought'"
            )).fetchone())[0]
        self.assertEqual(0, legacy_count)
        self.assertEqual(1, memory_count)

    async def test_lumi_thought_lands_in_her_library_and_not_the_recent_filler(self):
        import chatroom

        item = await recent_thoughts.save_recent_thought(
            "connor", "安静本身也可以是一种回答，不必急着说出来。",
            source_action="diary_browse", source_ref="di_lumi_1",
        )
        self.assertIsNotNone(item)
        self.assertTrue(item["embedding_ready"])
        self.assertEqual("connor", item["actor"])
        self.assertIsNone(await recent_thoughts.save_recent_thought(
            "connor", "安静本身也可以是一种回答，不必急着说出来。",
            source_action="diary_browse",
        ))
        self.assertEqual("", await recent_thoughts.take_recent_thoughts_for_chat("connor"))
        rows = await recent_thoughts.list_recent_thoughts("connor")
        self.assertEqual(1, len(rows))
        self.assertEqual("安静本身也可以是一种回答，不必急着说出来。", rows[0]["content"])
        self.assertEqual("diary_browse", rows[0]["source_action"])
        self.assertEqual("di_lumi_1", rows[0]["source_ref"])
        async with aiosqlite.connect(self.db_path) as db:
            record = await (await db.execute(
                "SELECT scope,memory_kind,content,source_conv,importance FROM chatroom_memories"
            )).fetchone()
            main_count = (await (await db.execute(
                "SELECT COUNT(*) FROM memories WHERE type='lumi_thought'"
            )).fetchone())[0]
            await db.execute(
                "INSERT INTO chatroom_memories (id,scope,content,memory_kind,created_at,archive_state) "
                "VALUES ('daily-new','connor','今天吃了热汤。','daily',?, 'active')",
                (time.time(),),
            )
            await db.commit()
        self.assertEqual("connor", record[0])
        self.assertEqual("lumi_thought", record[1])
        self.assertTrue(record[2].startswith("Lumi的想法："))
        self.assertEqual("autonomy:diary_browse:di_lumi_1", record[3])
        self.assertEqual(0.8, record[4])
        self.assertEqual(0, main_count)

        with patch.object(chatroom, "get_db", side_effect=lambda: aiosqlite.connect(self.db_path)), \
             patch.object(chatroom, "get_embedding", new=AsyncMock(return_value=[0.1, 0.2, 0.3])):
            recalled = await chatroom.recall_chatroom_memories(
                "安静和回答", scope="connor", threshold=0.45,
            )
            recent, _ = await chatroom.build_surfacing_chatroom_memories(
                topic="", keywords=[], max_total=8,
            )
            topical, _ = await chatroom.build_surfacing_chatroom_memories(
                topic="安静和回答", keywords=[], max_total=8,
            )
        self.assertIn(item["id"], [row["id"] for row in recalled])
        self.assertIn(item["id"], [row["id"] for row in topical])
        self.assertNotIn(item["id"], [row["id"] for row in recent])
        self.assertIn("daily-new", [row["id"] for row in recent])

        with patch.object(chatroom, "get_db", side_effect=lambda: aiosqlite.connect(self.db_path)), \
             patch.object(chatroom, "get_embedding", new=AsyncMock(return_value=[1.0, 0.0, 0.0])):
            unrelated = await chatroom.recall_chatroom_memories(
                "完全无关的话题", scope="connor", threshold=0.45, min_results=3,
            )
        self.assertNotIn(item["id"], [row["id"] for row in unrelated])

        self.assertTrue(await recent_thoughts.archive_recent_thought("connor", item["id"]))
        self.assertEqual([], await recent_thoughts.list_recent_thoughts("connor"))
        with patch.object(chatroom, "get_db", side_effect=lambda: aiosqlite.connect(self.db_path)), \
             patch.object(chatroom, "get_embedding", new=AsyncMock(return_value=[0.1, 0.2, 0.3])):
            after_archive = await chatroom.recall_chatroom_memories("安静和回答", scope="connor")
        self.assertNotIn(item["id"], [row["id"] for row in after_archive])

    async def test_legacy_connor_thoughts_migrate_without_touching_aion(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE recent_thoughts (
                    id TEXT PRIMARY KEY, actor TEXT, content TEXT,
                    source_action TEXT, source_ref TEXT, status TEXT, created_at REAL
                )
            """)
            await db.executemany(
                "INSERT INTO recent_thoughts VALUES(?,?,?,?,?,?,?)",
                [
                    ("thought_lumi_old", "connor", "以前逛完想留下来的一句话。",
                     "web_roam", "trip_1", "active", time.time() - 7200),
                    ("thought_aion_keep", "aion", "岚这条还留在旧表里，不该被 Lumi 迁移带走。",
                     "diary_browse", "day_2", "active", time.time() - 3600),
                    ("thought_lumi_short", "connor", "太短", "album_browse", "", "active", time.time()),
                ],
            )
            await db.commit()
        migrated = await recent_thoughts.list_recent_thoughts("connor")
        self.assertEqual(["mem_lumi_thought_lumi_old"], [row["id"] for row in migrated])
        self.assertEqual("以前逛完想留下来的一句话。", migrated[0]["content"])
        async with aiosqlite.connect(self.db_path) as db:
            legacy = await (await db.execute(
                "SELECT id,actor FROM recent_thoughts ORDER BY id"
            )).fetchall()
            stored = (await (await db.execute(
                "SELECT COUNT(*) FROM chatroom_memories WHERE memory_kind='lumi_thought'"
            )).fetchone())[0]
        self.assertEqual(
            [("thought_aion_keep", "aion"), ("thought_lumi_short", "connor")],
            legacy,
        )
        self.assertEqual(1, stored)

    async def test_idle_wake_optionally_writes_each_characters_own_thought(self):
        import json

        written = {
            "aion": "我还想把那天的光线再想一遍。",
            "connor": "原来安静本身也可以是一种回答。",
        }
        for actor, thought in written.items():
            native = {
                "after_read_action": "none",
                "reason": "看看就好",
                "recent_thought": thought,
            }
            with patch.object(autonomy, "_select_action", new=AsyncMock(return_value={
                     "action": "home_dynamics", "reason": "想看看家里", "message": "",
                 })), \
                 patch.object(autonomy, "_home_dynamics_snapshot", new=AsyncMock(return_value=("家里很安静", []))), \
                 patch.object(autonomy, "_actor_context", new=AsyncMock(return_value=[])), \
                 patch.object(autonomy, "_call_actor", new=AsyncMock(return_value=json.dumps(native, ensure_ascii=False))) as model, \
                 patch("proactive_companionship.inject_proactive_ability"), \
                 patch.object(autonomy, "append_idle_event", new=AsyncMock(return_value={"id": "event"})):
                outcome = await autonomy._run_actor_once(actor)
            self.assertTrue(outcome["ok"])
            self.assertEqual(thought, outcome["recent_thought"]["content"])
            self.assertEqual(actor, outcome["recent_thought"]["actor"])
            prompt = model.await_args.args[1][-1]["content"]
            self.assertIn("recent_thought", prompt)
            self.assertIn("没有就省略", prompt)
            model.assert_awaited_once()

        for actor in ("aion", "connor"):
            native = {"after_read_action": "none", "reason": "没什么想留下"}
            with patch.object(autonomy, "_select_action", new=AsyncMock(return_value={
                     "action": "home_dynamics", "reason": "想看看家里", "message": "",
                 })), \
                 patch.object(autonomy, "_home_dynamics_snapshot", new=AsyncMock(return_value=("没什么新鲜事", []))), \
                 patch.object(autonomy, "_actor_context", new=AsyncMock(return_value=[])), \
                 patch.object(autonomy, "_call_actor", new=AsyncMock(return_value=json.dumps(native, ensure_ascii=False))), \
                 patch("proactive_companionship.inject_proactive_ability"), \
                 patch.object(autonomy, "append_idle_event", new=AsyncMock(return_value={"id": "quiet"})):
                skipped = await autonomy._run_actor_once(actor)
            self.assertNotIn("recent_thought", skipped)
            for action in ("rest", "private_chat", "role_chat"):
                self.assertIsNone(await autonomy._maybe_save_autonomous_thought(
                    actor, action,
                    {"recent_thought": "这条不该在休息或直接聊天时被记成想法。"},
                    "session",
                ))
            too_short = {"after_read_action": "none", "reason": "只是闪过", "recent_thought": "太短"}
            with patch.object(autonomy, "_select_action", new=AsyncMock(return_value={
                     "action": "home_dynamics", "reason": "看看", "message": "",
                 })), \
                 patch.object(autonomy, "_home_dynamics_snapshot", new=AsyncMock(return_value=("安静", []))), \
                 patch.object(autonomy, "_actor_context", new=AsyncMock(return_value=[])), \
                 patch.object(autonomy, "_call_actor", new=AsyncMock(return_value=json.dumps(too_short, ensure_ascii=False))), \
                 patch("proactive_companionship.inject_proactive_ability"), \
                 patch.object(autonomy, "append_idle_event", new=AsyncMock(return_value={"id": "short"})):
                brief = await autonomy._run_actor_once(actor)
            self.assertNotIn("recent_thought", brief)

        async with aiosqlite.connect(self.db_path) as db:
            lan = await (await db.execute(
                "SELECT content FROM memories WHERE type='lan_thought'"
            )).fetchall()
            lumi = await (await db.execute(
                "SELECT content FROM chatroom_memories WHERE memory_kind='lumi_thought'"
            )).fetchall()
        self.assertEqual(["岚的想法：" + written["aion"]], [row[0] for row in lan])
        self.assertEqual(["Lumi的想法：" + written["connor"]], [row[0] for row in lumi])

class DiaryBrowseTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_dynamics_can_choose_silence(self):
        with patch.object(autonomy, "_home_dynamics_snapshot", new=AsyncMock(return_value=("今天很安静", []))), \
             patch.object(autonomy, "_ask_actor_json", new=AsyncMock(return_value={
                 "after_read_action": "none", "reason": "暂时没有想说的",
             })), \
             patch.object(autonomy, "append_idle_event", new=AsyncMock(return_value={"id": "quiet_event"})), \
             patch("diary.save_diary_entry", new=AsyncMock()) as save:
            result = await autonomy._run_home_dynamics("aion")

        self.assertEqual("quiet_event", result["event"]["id"])
        save.assert_not_awaited()

    async def test_blank_home_dynamics_diary_is_not_filled_with_boilerplate(self):
        with patch("diary.save_diary_entry", new=AsyncMock()) as save:
            result = await autonomy._write_home_dynamics_diary(
                "aion", {"diary_title": "", "diary_content": ""}, "家庭时间轴"
            )
        self.assertIsNone(result)
        save.assert_not_awaited()

    async def test_actor_may_read_full_personal_diary_without_sharing(self):
        class Cursor:
            async def fetchone(self):
                return {
                    "id": "di_aion_1", "title": "旧日子", "mood": "平静",
                    "content": "这是我写给自己的一篇旧日记，里面有真正的正文。",
                    "created_at": 100000,
                }

        class Db:
            row_factory = None

            async def execute(self, sql, params):
                self_params = params
                assert self_params == ("aion",)
                assert "WHERE author=?" in sql
                return Cursor()

        class Context:
            async def __aenter__(self):
                return Db()

            async def __aexit__(self, *_args):
                return False

        with patch.object(autonomy, "get_db", return_value=Context()), \
             patch.object(autonomy, "_ask_actor_json", new=AsyncMock(return_value={
                 "reflection": "还记得这个时刻。", "recent_thought": "",
             })) as read, \
             patch.object(autonomy, "append_idle_event", new=AsyncMock(return_value={"id": "event_1"})):
            result = await autonomy._run_diary_browse("aion")

        self.assertEqual(result["read_diary_id"], "di_aion_1")
        self.assertEqual("", result["recent_thought"])
        prompt = read.await_args.args[1]
        self.assertIn("真正的正文", prompt)
        self.assertIn("不必把读后感发给用户", prompt)
        self.assertIn("recent_thought", prompt)
        self.assertIn("否则留空", prompt)
        self.assertNotIn("主聊天", prompt)


if __name__ == "__main__":
    unittest.main()
