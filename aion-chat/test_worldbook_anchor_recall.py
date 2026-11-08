import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import database
from active_memory_search import MemorySearchRequest, search_actor_memories
from memory import _pack_embedding, build_surfacing_memories, recall_memories


class WorldbookAnchorRecallTests(unittest.IsolatedAsyncioTestCase):
    persona = "NA-01\uff5cSummary\nAn incidental reference to NA-02"

    async def asyncSetUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.object(database, "DB_PATH", Path(temporary) / "test.db"))
        self.enterContext(patch("memory.get_embedding", new=AsyncMock(return_value=[1.0, 0.0])))
        self.enterContext(patch("active_memory_search.get_embedding", new=AsyncMock(return_value=[1.0, 0.0])))
        await database.init_db()
        now = time.time()
        async with database.get_db() as db:
            await db.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("conv", "Test", now, now),
            )
            await db.execute(
                "INSERT INTO messages (id, conv_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                ("raw", "conv", "user", "Exact archived sentence", now),
            )
            await db.executemany(
                "INSERT INTO memories (id, content, type, created_at, embedding, importance, unresolved, "
                "source_conv, source_msg_id) VALUES (?, ?, 'important', ?, ?, ?, ?, 'conv', ?)",
                [
                    ("mem_lan_na01", "Orchard anchor", now, _pack_embedding([1.0, 0.0]), 1, 1, '["raw"]'),
                    ("mem_lan_na02", "Different anchor", now - 1, _pack_embedding([0.8, 0.6]), 0.8, 1, None),
                    ("mem_other", "Other memory", now - 2, _pack_embedding([0.8, 0.6]), 0.8, 1, None),
                ],
            )
            await db.commit()

    async def test_automatic_recall_skips_only_anchors_in_current_persona(self):
        matched, debug = await recall_memories("Orchard", top_k=1, worldbook_persona=self.persona)
        self.assertEqual(matched[0]["id"], "mem_lan_na02")
        self.assertNotIn("mem_lan_na01", {row["id"] for row in debug})

        matched, _ = await recall_memories("Orchard", top_k=1)
        self.assertEqual(matched[0]["id"], "mem_lan_na01")

    async def test_background_excludes_anchor_before_each_limit(self):
        for unresolved, topic in ((1, ""), (0, "Orchard"), (0, "")):
            with self.subTest(unresolved=unresolved, topic=topic):
                async with database.get_db() as db:
                    await db.execute("UPDATE memories SET unresolved=?", (unresolved,))
                    await db.commit()
                _, ids = await build_surfacing_memories(topic, max_total=2, worldbook_persona=self.persona)
                self.assertEqual(ids, {"mem_lan_na02", "mem_other"})

    async def test_active_search_can_still_find_anchor_and_open_raw(self):
        results = await search_actor_memories("aion", [MemorySearchRequest("Orchard", include_detail=True)])
        self.assertEqual(results[0].memory_id, "mem_lan_na01")
        self.assertIn("Exact archived sentence", " ".join(results[0].sources))
