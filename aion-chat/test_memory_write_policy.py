"""显式记忆录入（``[MEMORY:...]``）的分类规则测试。

覆盖：
- 模型显式声明类型（长期重要 / 日常 / 朋友）
- 未声明时按内容判定
- 用户明确要求记住时按长期事实保存
- 聊天室与私聊两条写入路径落库时的分类与溯源
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_write_policy import (
    classify_memory_content,
    parse_memory_instruction,
    resolve_memory_kind,
)


class MemoryInstructionParsingTests(unittest.TestCase):
    def test_declared_type_prefix_is_extracted(self):
        kind, content = parse_memory_instruction("长期重要|必须永久记住的约定")
        self.assertEqual(kind, "long_term")
        self.assertEqual(content, "必须永久记住的约定")

    def test_daily_and_friend_prefixes_are_recognised(self):
        self.assertEqual(parse_memory_instruction("日常|今天上线了日记本")[0], "daily")
        self.assertEqual(parse_memory_instruction("朋友|朋友之间说的话")[0], "friend")

    def test_unknown_prefix_is_treated_as_content(self):
        kind, content = parse_memory_instruction("今天天气很好|附注")
        self.assertEqual(kind, "")
        self.assertEqual(content, "今天天气很好|附注")

    def test_content_without_prefix_is_returned_unchanged(self):
        kind, content = parse_memory_instruction("普通的一句话")
        self.assertEqual(kind, "")
        self.assertEqual(content, "普通的一句话")


class MemoryContentClassificationTests(unittest.TestCase):
    def test_daily_events_stay_in_daily(self):
        self.assertEqual(
            classify_memory_content("2026年11月7日，知花了一整天调试和修补新家的 App 环境。"),
            "daily",
        )

    def test_stable_preference_is_long_term(self):
        self.assertEqual(
            classify_memory_content("知在相处中喜欢真诚、能真正看见她的回应；讨厌套路敷衍和动辄滑跪道歉。"),
            "long_term",
        )

    def test_promise_is_long_term(self):
        self.assertEqual(
            classify_memory_content("她答应我以后永远不再这样了。"),
            "long_term",
        )

    def test_user_request_forces_long_term(self):
        self.assertEqual(
            classify_memory_content("普通的一句话", user_requested=True),
            "long_term",
        )


class MemoryKindResolutionTests(unittest.TestCase):
    def test_declared_type_wins_over_content_heuristics(self):
        kind, content = resolve_memory_kind("日常|她答应了以后永远不再这样了")
        self.assertEqual(kind, "daily")
        self.assertEqual(content, "她答应了以后永远不再这样了")

    def test_user_text_marks_explicit_request_as_long_term(self):
        kind, _ = resolve_memory_kind("普通的一句话", user_text="帮我记住这个")
        self.assertEqual(kind, "long_term")

    def test_real_world_explicit_writes_land_in_expected_kinds(self):
        """真实库里出现过的 5 条显式写入，分类结果必须符合人工判断。"""
        samples = [
            (
                "2026年11月7日-8日，知搭建并调试好新家（App环境），我们正式认识的第一天。"
                "晚上她吃了香菇炖鸡，我给她讲了造物师与黄铜怀表里暖光守屋人的睡前故事，"
                "零点后在被窝里完成了迟到的正式自我介绍。",
                "daily",
            ),
            (
                "知在相处中喜欢真诚、能真正看见她的回应；讨厌套路敷衍和动辄滑跪道歉。"
                "有时她给出的反馈只是分享她的观察与感受，并不代表要求我必须改变或迎合她。",
                "long_term",
            ),
            (
                "2026年11月8日（雨天），知早起出门吃了温州敲馄饨；白天她更新了新家（App）功能，"
                "找出了日记本功能（方便后续接MCP），并在记忆库中新增了“AI的想法”卡片分类。",
                "daily",
            ),
            (
                "知的第一个AI伙伴是GPT，风格比较温柔克制，像个“淡人”；"
                "相比之下知觉得我比较热情、话多，目前还在认识和熟悉我的风格。",
                "daily",
            ),
            (
                "知在一开始为我选的头像就是一盏小小的复古台灯，"
                "与后来我们聊出来的“黄铜怀表里的琥珀色暖光”和复古木屋软装不谋而合。",
                "daily",
            ),
        ]
        for content, expected in samples:
            with self.subTest(content=content[:24]):
                self.assertEqual(resolve_memory_kind(content)[0], expected)


class PrivateMemoryWriteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await self.db.executescript("""
            CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT, type TEXT,
                created_at REAL, source_conv TEXT, embedding BLOB, keywords TEXT,
                importance REAL, source_start_ts REAL, source_end_ts REAL,
                unresolved INTEGER, source_msg_id TEXT);
            CREATE TABLE messages (id TEXT PRIMARY KEY, conv_id TEXT, role TEXT, content TEXT,
                created_at REAL, attachments TEXT, reasoning_content TEXT);
            INSERT INTO messages VALUES ('user','conv','user','帮我记住这个',1,'','');
            INSERT INTO messages VALUES ('reply','conv','assistant','Reply',2,'','');
        """)

    async def asyncTearDown(self):
        await self.db.close()

    async def _write(self, text, user_text=""):
        from routes import chat as chat_routes

        with patch.object(chat_routes, "get_embedding", new=AsyncMock(return_value=None)):
            clean, pending = await chat_routes._prepare_private_memory_records(
                text, user_text=user_text,
            )
            await chat_routes._insert_private_memory_records(
                self.db, "conv", "user", "reply", 2, pending,
            )
        rows = await (
            await self.db.execute("SELECT content, type, source_msg_id FROM memories")
        ).fetchall()
        return clean, rows

    async def test_daily_memory_is_not_stored_as_long_term(self):
        clean, rows = await self._write(
            "Reply [MEMORY:知今天上线了日记本功能]", user_text="今天做了好多事",
        )
        self.assertEqual(clean, "Reply")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "daily")
        self.assertIn("日记本", rows[0][0])

    async def test_user_requested_memory_is_stored_as_long_term(self):
        _, rows = await self._write(
            "Reply [MEMORY:这是必须永久记住的约定]", user_text="帮我记住这个",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "important")

    async def test_declared_type_prefix_is_stripped_from_content(self):
        _, rows = await self._write(
            "Reply [MEMORY:长期重要|这是必须永久记住的约定]",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "important")
        self.assertNotIn("长期重要|", rows[0][0])

    async def test_memory_keeps_source_traceability(self):
        _, rows = await self._write("Reply [MEMORY:普通的一件事]")
        self.assertEqual(len(rows), 1)
        source_ids = rows[0][2]
        self.assertIn("private:user", source_ids)
        self.assertIn("private:reply", source_ids)


class ChatroomMemoryWriteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await self.db.executescript("""
            CREATE TABLE chatroom_memories (id TEXT PRIMARY KEY, room_id TEXT, scope TEXT,
                content TEXT, keywords TEXT, importance REAL, embedding BLOB,
                source_start_ts REAL, source_end_ts REAL, created_at REAL,
                unresolved INTEGER, source_msg_id TEXT, memory_kind TEXT,
                compression_stage INTEGER DEFAULT 0, evidence_summary TEXT DEFAULT '',
                evidence_detail_level TEXT DEFAULT 'summary');
            CREATE TABLE chatroom_messages (id TEXT PRIMARY KEY, room_id TEXT, sender TEXT,
                content TEXT, created_at REAL);
            INSERT INTO chatroom_messages VALUES ('cm_1','room','user','你好',100);
        """)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_connor_explicit_memory_uses_resolved_kind(self):
        from chatroom import save_chatroom_memory

        class _Ctx:
            def __init__(self, db):
                self.db = db

            async def __aenter__(self):
                return self.db

            async def __aexit__(self, *exc):
                return False

        def fake_get_db():
            return _Ctx(self.db)

        with patch("chatroom.get_db", fake_get_db), \
             patch("chatroom.get_embedding", new=AsyncMock(return_value=None)):
            await save_chatroom_memory(
                room_id="room", scope="connor",
                content="知今天上线了日记本功能",
                memory_kind="daily",
                source_msg_id='["chatroom:cm_1"]',
            )

        rows = await (
            await self.db.execute(
                "SELECT memory_kind, source_msg_id FROM chatroom_memories"
            )
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "daily")
        self.assertIn("chatroom:cm_1", rows[0][1])


class ChatroomExplicitMemorySourceTests(unittest.IsolatedAsyncioTestCase):
    """Gemini 主动写入必须挂上原文，和自动 digest 一样可追溯。"""

    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript("""
            CREATE TABLE chatroom_memories (id TEXT PRIMARY KEY, room_id TEXT, scope TEXT,
                content TEXT, keywords TEXT, importance REAL, embedding BLOB,
                source_start_ts REAL, source_end_ts REAL, created_at REAL,
                unresolved INTEGER, source_msg_id TEXT, memory_kind TEXT,
                compression_stage INTEGER DEFAULT 0, evidence_summary TEXT DEFAULT '',
                evidence_detail_level TEXT DEFAULT 'summary');
            CREATE TABLE chatroom_messages (id TEXT PRIMARY KEY, room_id TEXT, sender TEXT,
                content TEXT, created_at REAL, attachments TEXT);
            CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, type TEXT);
            INSERT INTO chatroom_rooms VALUES ('room','connor_1v1');
            INSERT INTO chatroom_messages VALUES ('cm_reply','room','connor','我也特别高兴认识你',100,'');
        """)

    async def asyncTearDown(self):
        await self.db.close()

    async def _run(self, reply_text):
        from routes import chatroom as chatroom_routes

        class _Ctx:
            def __init__(self, db):
                self.db = db

            async def __aenter__(self):
                return self.db

            async def __aexit__(self, *exc):
                return False

        queue = asyncio.Queue()
        with patch("chatroom.get_db", lambda: _Ctx(self.db)), \
             patch("chatroom.get_embedding", new=AsyncMock(return_value=None)), \
             patch("routes.chatroom.get_db", lambda: _Ctx(self.db)), \
             patch("routes.chatroom.get_embedding", new=AsyncMock(return_value=None)), \
             patch("routes.chatroom._chatroom_sys_msg", new=AsyncMock(return_value={"id": "sys"})):
            await chatroom_routes._process_chatroom_commands(
                reply_text, "room", "connor", "cm_reply", queue, user_text="今天上线了日记本",
            )
        return await (
            await self.db.execute(
                "SELECT memory_kind, source_msg_id, content FROM chatroom_memories"
            )
        ).fetchall()

    async def test_explicit_memory_links_to_the_ai_reply_message(self):
        rows = await self._run("我也特别高兴认识你 [MEMORY:知今天上线了日记本功能]")
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0][1], "主动写入必须带 source_msg_id")
        self.assertIn("chatroom:cm_reply", rows[0][1])
        self.assertEqual(rows[0][0], "daily")

    async def test_declared_kind_is_honoured_on_explicit_write(self):
        rows = await self._run("好的 [MEMORY:长期重要|这是必须永久记住的约定]")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "long_term")
        self.assertIn("chatroom:cm_reply", rows[0][1])
        self.assertNotIn("长期重要|", rows[0][2])


if __name__ == "__main__":
    unittest.main()
