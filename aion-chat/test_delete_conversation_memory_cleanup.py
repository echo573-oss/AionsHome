"""
Unit test for conversation and message deletion cleanup:
Ensures deleted conversations and messages do not leave orphaned memories,
pending/failed post_sentinel_jobs, or active entries in post_sentinel_document.
"""

import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

import aiosqlite
from database import get_db, init_db
import post_sentinel
from routes import chat as chat_routes


class TestDeleteConversationMemoryCleanup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        await post_sentinel.ensure_schema()
        self.conv_id = f"conv_test_del_{int(time.time()*1000)}"
        self.msg_id_1 = f"msg_u_{int(time.time()*1000)}"
        self.msg_id_2 = f"msg_a_{int(time.time()*1000)}"
        self.mem_id_1 = f"mem_del_1_{int(time.time()*1000)}"
        self.mem_id_2 = f"mem_del_2_{int(time.time()*1000)}"
        self.entry_key = f"fact.test_{int(time.time()*1000)}"

        now = time.time()
        async with get_db() as db:
            # 1. 创建对话与消息
            await db.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (self.conv_id, "测试对话", now, now),
            )
            await db.execute(
                "INSERT INTO messages (id, conv_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (self.msg_id_1, self.conv_id, "user", "测试问题", now),
            )
            await db.execute(
                "INSERT INTO messages (id, conv_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (self.msg_id_2, self.conv_id, "assistant", "测试回复", now + 1),
            )

            # 2. 插入属于该对话的记忆（一条按 source_conv，一条按 source_msg_id）
            await db.execute(
                "INSERT INTO memories (id, content, type, created_at, source_conv) VALUES (?, ?, ?, ?, ?)",
                (self.mem_id_1, "测试对话记忆1", "重要事件", now, self.conv_id),
            )
            await db.execute(
                "INSERT INTO memories (id, content, type, created_at, source_msg_id) VALUES (?, ?, ?, ?, ?)",
                (self.mem_id_2, "测试对话记忆2", "重要事件", now, json.dumps([f"private:{self.msg_id_1}"])),
            )

            # 3. 插入 post_sentinel_jobs
            await db.execute(
                "INSERT INTO post_sentinel_jobs (source_id, scope, parent_id, message_id, source_ts, content, epoch) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"private:{self.msg_id_1}", "private", self.conv_id, self.msg_id_1, now, "测试问题", 0),
            )

            # 4. 在 post_sentinel_document 中插入基于该消息的画像条目与状态
            row, doc = await post_sentinel._read(db)
            doc["entries"][self.entry_key] = {
                "key": self.entry_key,
                "category": "recent",
                "topic": "测试主题",
                "content": "测试待办内容",
                "kind": "project",
                "status": "active",
                "sources": [f"private:{self.msg_id_1}"],
                "updated_at": now,
                "source_ts": now,
            }
            doc["current_state"] = {
                "text": "测试中活动",
                "phase": "ongoing",
                "expires_at": now + 3600,
                "sources": [f"private:{self.msg_id_1}"],
            }
            await db.execute(
                "UPDATE post_sentinel_document SET payload=?, revision=revision+1, updated_at=? WHERE id=1",
                (json.dumps(doc, ensure_ascii=False), now),
            )
            await db.commit()

    async def test_delete_conversation_cleans_memories_jobs_and_profile(self):
        with patch("routes.files.delete_exported_file"), \
             patch.object(chat_routes.manager, "broadcast", new=AsyncMock()):
            res = await chat_routes.delete_conversation(self.conv_id)
            self.assertTrue(res.get("ok"))

        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            # 对话与消息已删除
            conv = await (await db.execute("SELECT * FROM conversations WHERE id=?", (self.conv_id,))).fetchone()
            self.assertIsNone(conv)
            msgs = await (await db.execute("SELECT * FROM messages WHERE conv_id=?", (self.conv_id,))).fetchall()
            self.assertEqual(len(msgs), 0)

            # 关联记忆已被删除
            mem1 = await (await db.execute("SELECT * FROM memories WHERE id=?", (self.mem_id_1,))).fetchone()
            self.assertIsNone(mem1)
            mem2 = await (await db.execute("SELECT * FROM memories WHERE id=?", (self.mem_id_2,))).fetchone()
            self.assertIsNone(mem2)

            # 哨兵作业已被删除
            jobs = await (await db.execute("SELECT * FROM post_sentinel_jobs WHERE parent_id=?", (self.conv_id,))).fetchall()
            self.assertEqual(len(jobs), 0)

            # 画像文档中条目已被标记删除，状态已被清空
            _, doc = await post_sentinel._read(db)
            entry = doc.get("entries", {}).get(self.entry_key)
            self.assertTrue(entry.get("deleted"))
            self.assertIsNone(doc.get("current_state"))

    async def test_delete_single_message_cleans_associated_memory_and_profile(self):
        with patch.object(chat_routes, "export_conversation", new=AsyncMock()), \
             patch.object(chat_routes.manager, "broadcast", new=AsyncMock()):
            res = await chat_routes.delete_message(self.msg_id_1)
            self.assertTrue(res.get("ok"))

        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            # 消息1已删除
            msg = await (await db.execute("SELECT * FROM messages WHERE id=?", (self.msg_id_1,))).fetchone()
            self.assertIsNone(msg)

            # 仅挂载在消息1上的记忆已删除
            mem2 = await (await db.execute("SELECT * FROM memories WHERE id=?", (self.mem_id_2,))).fetchone()
            self.assertIsNone(mem2)

            # 属于消息1的 job 已删除
            job = await (await db.execute("SELECT * FROM post_sentinel_jobs WHERE message_id=?", (self.msg_id_1,))).fetchone()
            self.assertIsNone(job)

            # 画像文档中依赖消息1的条目已删除，状态已清空
            _, doc = await post_sentinel._read(db)
            entry = doc.get("entries", {}).get(self.entry_key)
            self.assertTrue(entry.get("deleted"))
            self.assertIsNone(doc.get("current_state"))


if __name__ == "__main__":
    unittest.main()
