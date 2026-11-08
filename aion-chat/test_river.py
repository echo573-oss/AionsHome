"""《河》章节：持久化、版本保护、原子记忆只读关联。"""
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import aiosqlite
from fastapi import HTTPException

from routes import river


class RiverChapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        river._schema_ready = False
        self.db_patch = patch.object(river, "get_db", side_effect=lambda: aiosqlite.connect(self.path))
        self.notify_patch = patch.object(river, "_notify", new=AsyncMock())
        self.db_patch.start()
        self.notify_patch.start()
        # In this project the memories table exists before river routes are called.
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "CREATE TABLE memories("
                "id TEXT PRIMARY KEY,content TEXT,type TEXT,created_at REAL,"
                "archive_state TEXT,source_conv TEXT,keywords TEXT,importance REAL)"
            )
            await db.execute(
                "INSERT INTO memories VALUES(?,?,?,?,?,?,?,?)",
                ("mem_original", "在旧窗口谈到换窗与连续性", "important",
                 1700000000, "active", "original-chat", "换窗", 0.8),
            )
            await db.commit()

    async def asyncTearDown(self):
        self.db_patch.stop()
        self.notify_patch.stop()
        river._schema_ready = False
        os.remove(self.path)

    def draft(self, **kwargs):
        data = {
            "source_key": "RAW-0005-RIVER-0001",
            "title": "最初的名字",
            "content": "第一次说出这个名字的时候，未来还没有发生。",
            "source_refs": ["RAW-0005.md：M0100–M0140"],
            "memory_ids": ["mem_original"],
            "status": "draft",
        }
        data.update(kwargs)
        return river.ChapterWrite(**data)

    async def test_create_list_and_read_linked_memory_without_mutation(self):
        created = await river.create_chapter(self.draft())
        self.assertEqual(1, created["version"])
        self.assertEqual("RAW-0005-RIVER-0001", created["source_key"])
        self.assertEqual(["mem_original"], created["memory_ids"])
        listed = await river.list_chapters(q="", limit=100, offset=0)
        self.assertEqual(1, listed["total"])
        self.assertEqual("最初的名字", listed["items"][0]["title"])
        detail = await river.get_chapter(created["id"])
        self.assertEqual("mem_original", detail["linked_memories"][0]["id"])
        self.assertIn("换窗与连续性", detail["linked_memories"][0]["content"])
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute("SELECT content FROM memories WHERE id='mem_original'")).fetchone()
        self.assertEqual("在旧窗口谈到换窗与连续性", row[0])

    async def test_duplicate_manuscript_key_rejected(self):
        await river.create_chapter(self.draft())
        with self.assertRaises(HTTPException) as caught:
            await river.create_chapter(self.draft(title="又来了一篇"))
        self.assertEqual(409, caught.exception.status_code)

    async def test_update_versions_and_prevents_stale_writes(self):
        original = await river.create_chapter(self.draft())
        chapter_id = original["id"]
        updated = await river.update_chapter(
            chapter_id,
            self.draft(title="修改后的名字", content="后来我们给旧事加了一句新理解。",
                       reflection="当时没有想到，名字能走这么远。", expected_version=1),
        )
        self.assertEqual(2, updated["version"])
        self.assertEqual("修改后的名字", updated["title"])
        history = await river.list_revisions(chapter_id)
        self.assertEqual([1], [item["version"] for item in history["items"]])
        with self.assertRaises(HTTPException) as caught:
            await river.update_chapter(chapter_id, self.draft(expected_version=1))
        self.assertEqual(409, caught.exception.status_code)
        still = await river.get_chapter(chapter_id)
        self.assertEqual(2, still["version"])

    async def test_restore_keeps_both_old_and_new_edits(self):
        original = await river.create_chapter(self.draft())
        chapter_id = original["id"]
        await river.update_chapter(chapter_id, self.draft(title="第二个版本", expected_version=1))
        restored = await river.restore_chapter(
            chapter_id, river.RestoreWrite(version=1, expected_version=2)
        )
        self.assertEqual("最初的名字", restored["title"])
        self.assertEqual(3, restored["version"])
        versions = await river.list_revisions(chapter_id)
        self.assertEqual([2, 1], [item["version"] for item in versions["items"]])

    async def test_archive_does_not_delete_and_can_be_restored(self):
        original = await river.create_chapter(self.draft())
        chapter_id = original["id"]
        await river.update_chapter(chapter_id, self.draft(status="archived", expected_version=1))
        active = await river.list_chapters(q="", include_archived=False, limit=100, offset=0)
        self.assertEqual(0, active["total"])
        archived = await river.list_chapters(q="", include_archived=True, limit=100, offset=0)
        self.assertEqual(1, archived["total"])
        self.assertEqual("archived", archived["items"][0]["status"])
        await river.update_chapter(chapter_id, self.draft(status="draft", expected_version=2))
        active = await river.list_chapters(q="", include_archived=False, limit=100, offset=0)
        self.assertEqual(1, active["total"])

    async def test_missing_memory_reference_is_retained_as_missing(self):
        created = await river.create_chapter(
            self.draft(memory_ids=["mem_not_yet_imported", "mem_original"])
        )
        chapter = await river.get_chapter(created["id"])
        self.assertTrue(chapter["linked_memories"][0]["missing"])
        self.assertEqual("mem_original", chapter["linked_memories"][1]["id"])

    async def test_memory_lookup_reads_but_does_not_write(self):
        result = await river.search_memories(q="换窗")
        self.assertEqual(["mem_original"], [m["id"] for m in result["items"]])
        none = await river.search_memories(q="换")
        self.assertEqual([], none["items"])

    async def test_user_query_wildcards_are_literal(self):
        await river.create_chapter(self.draft())
        result = await river.list_chapters(q="%", limit=100, offset=0)
        self.assertEqual(0, result["total"])


if __name__ == "__main__":
    unittest.main()
