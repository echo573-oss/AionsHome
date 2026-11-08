import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite

import recent_thoughts
import autonomy


class RecentThoughtsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db_path = Path(self.temp.name) / "thoughts.db"
        self.db_patch = patch.object(
            recent_thoughts, "get_db",
            side_effect=lambda: aiosqlite.connect(db_path),
        )
        self.db_patch.start()
        self.broadcast_patch = patch("ws.manager.broadcast", new=AsyncMock())
        self.broadcast_patch.start()

    async def asyncTearDown(self):
        self.broadcast_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    async def test_save_and_surface_only_once_per_interval(self):
        with patch.object(recent_thoughts.time, "time", return_value=10000.0):
            row = await recent_thoughts.save_recent_thought(
                "aion", "我还想重新读一下那天的话。", source_action="diary_browse",
                source_ref="di_aion_1",
            )
            self.assertIsNotNone(row)
            self.assertIsNone(await recent_thoughts.save_recent_thought(
                "aion", "我还想重新读一下那天的话。", source_action="diary_browse",
            ))
            self.assertIn("重新读一下", await recent_thoughts.take_recent_thoughts_for_chat("aion"))
            self.assertEqual("", await recent_thoughts.take_recent_thoughts_for_chat("aion"))
            self.assertEqual("", await recent_thoughts.take_recent_thoughts_for_chat("connor"))

        for index in (1, 2):
            with patch.object(
                recent_thoughts.time, "time",
                return_value=10000.0 + index * (recent_thoughts.REOFFER_SECONDS + 1),
            ):
                self.assertIn("重新读一下", await recent_thoughts.take_recent_thoughts_for_chat("aion"))

        with patch.object(recent_thoughts.time, "time", return_value=10000.0 + 4 * 86400):
            self.assertEqual("", await recent_thoughts.take_recent_thoughts_for_chat("aion"))
        rows = await recent_thoughts.list_recent_thoughts("aion")
        self.assertEqual(3, rows[0]["shown_count"])
        self.assertEqual("resting", rows[0]["status"])

    async def test_archive_and_expire_do_not_delete_history(self):
        now = 10000.0
        with patch.object(recent_thoughts.time, "time", return_value=now):
            item = await recent_thoughts.save_recent_thought(
                "aion", "我以后可能想再写一篇关于这件事的日记。", source_action="memory_browse",
            )
        self.assertTrue(await recent_thoughts.archive_recent_thought("aion", item["id"]))
        self.assertFalse(await recent_thoughts.archive_recent_thought("aion", item["id"]))
        with patch.object(recent_thoughts.time, "time", return_value=now + 20 * 86400):
            self.assertEqual("", await recent_thoughts.take_recent_thoughts_for_chat("aion"))
        self.assertEqual("archived", (await recent_thoughts.list_recent_thoughts("aion"))[0]["status"])

        with patch.object(recent_thoughts.time, "time", return_value=now):
            await recent_thoughts.save_recent_thought(
                "aion", "这是一条日后会自然到期的旧想法。", source_action="home_dynamics",
            )
        with patch.object(recent_thoughts.time, "time", return_value=now + 20 * 86400):
            rows = await recent_thoughts.list_recent_thoughts("aion")
        self.assertTrue(any(row["status"] == "expired" for row in rows))


class DiaryBrowseTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
