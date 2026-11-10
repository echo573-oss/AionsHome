"""跨写入通道去重：来源消息 id 重叠判定。

覆盖：
- 覆盖表的登记与查询
- 消息列表按覆盖表过滤
- 显式写入登记整轮对话（用户消息 + AI 回复），digest 能据此避开
- digest 写入后登记来源，反向也能避开
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

from memory_coverage import (
    OWNER_MAIN,
    OWNER_SECOND,
    covered_message_ids,
    ensure_coverage_schema,
    filter_uncovered_messages,
    mark_covered,
    normalize_source_ids,
)


class SourceIdNormalizeTests(unittest.TestCase):
    def test_prefixed_ids_are_kept(self):
        self.assertEqual(
            normalize_source_ids(["chatroom:cm_1", "private:msg_2"]),
            ["chatroom:cm_1", "private:msg_2"],
        )

    def test_json_string_is_parsed(self):
        self.assertEqual(
            normalize_source_ids('["chatroom:cm_1"]'), ["chatroom:cm_1"],
        )

    def test_duplicates_are_removed(self):
        self.assertEqual(
            normalize_source_ids(["chatroom:cm_1", "chatroom:cm_1"]),
            ["chatroom:cm_1"],
        )

    def test_empty_input_yields_empty_list(self):
        self.assertEqual(normalize_source_ids(None), [])
        self.assertEqual(normalize_source_ids([]), [])


class CoverageTableTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await ensure_coverage_schema(self.db)
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_mark_and_read_covered_ids(self):
        await mark_covered(self.db, OWNER_SECOND, ["chatroom:cm_1", "chatroom:cm_2"])
        await self.db.commit()
        covered = await covered_message_ids(
            self.db, OWNER_SECOND, ["chatroom:cm_1", "chatroom:cm_9"],
        )
        self.assertEqual(covered, {"chatroom:cm_1"})

    async def test_owners_are_isolated(self):
        await mark_covered(self.db, OWNER_MAIN, ["chatroom:cm_1"])
        await self.db.commit()
        covered = await covered_message_ids(self.db, OWNER_SECOND, ["chatroom:cm_1"])
        self.assertEqual(covered, set())

    async def test_mark_is_idempotent(self):
        await mark_covered(self.db, OWNER_SECOND, ["chatroom:cm_1"])
        await mark_covered(self.db, OWNER_SECOND, ["chatroom:cm_1"])
        await self.db.commit()
        rows = await (
            await self.db.execute(
                "SELECT COUNT(*) c FROM memory_message_coverage"
            )
        ).fetchall()
        self.assertEqual(rows[0][0], 1)

    async def test_invalid_owner_is_ignored(self):
        count = await mark_covered(self.db, "bogus", ["chatroom:cm_1"])
        self.assertEqual(count, 0)

    async def test_filter_removes_covered_messages(self):
        await mark_covered(self.db, OWNER_SECOND, ["chatroom:cm_2"])
        await self.db.commit()
        messages = [{"id": "cm_1"}, {"id": "cm_2"}, {"id": "cm_3"}]
        kept, hit = await filter_uncovered_messages(
            self.db, OWNER_SECOND, messages, namespace="chatroom",
        )
        self.assertEqual([m["id"] for m in kept], ["cm_1", "cm_3"])
        self.assertEqual(hit, {"chatroom:cm_2"})

    async def test_filter_keeps_all_when_nothing_covered(self):
        messages = [{"id": "cm_1"}, {"id": "cm_2"}]
        kept, hit = await filter_uncovered_messages(
            self.db, OWNER_SECOND, messages, namespace="chatroom",
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(hit, set())

    async def test_source_id_takes_priority_over_id_key(self):
        """主私聊 digest 的消息流混合 private: 与 chatroom: 两种命名空间，
        必须用消息自带的 _source_id，不能统一补前缀。"""
        await mark_covered(self.db, OWNER_MAIN, ["chatroom:cm_group"])
        await self.db.commit()
        messages = [
            {"id": "cm_group", "_source_id": "chatroom:cm_group"},
            {"id": "msg_priv", "_source_id": "private:msg_priv"},
        ]
        kept, hit = await filter_uncovered_messages(
            self.db, OWNER_MAIN, messages, namespace="private",
        )
        # 群聊消息按 chatroom: 命中并被剔除，私聊消息保留
        self.assertEqual([m["_source_id"] for m in kept], ["private:msg_priv"])
        self.assertEqual(hit, {"chatroom:cm_group"})


class ChatroomExplicitWriteCoverageTests(unittest.IsolatedAsyncioTestCase):
    """显式写入要登记整轮对话，digest 才能避开同一段内容。"""

    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript("""
            CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT, type TEXT,
                created_at REAL, source_conv TEXT, embedding BLOB, keywords TEXT,
                importance REAL, source_start_ts REAL, source_end_ts REAL,
                unresolved INTEGER, source_msg_id TEXT);
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
            INSERT INTO chatroom_messages VALUES ('cm_user','room','user','我想起来了',100,'');
            INSERT INTO chatroom_messages VALUES ('cm_reply','room','connor','记下了',101,'');
        """)
        await ensure_coverage_schema(self.db)
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_source_ids_cover_the_whole_turn(self):
        from routes import chatroom as chatroom_routes

        class _Ctx:
            def __init__(self, db):
                self.db = db

            async def __aenter__(self):
                return self.db

            async def __aexit__(self, *exc):
                return False

        with patch("routes.chatroom.get_db", lambda: _Ctx(self.db)):
            source_ids = await chatroom_routes._explicit_memory_source_ids(
                "room", "cm_reply",
            )
        self.assertEqual(
            source_ids, ["chatroom:cm_user", "chatroom:cm_reply"],
        )

    async def test_explicit_write_registers_coverage(self):
        from routes import chatroom as chatroom_routes

        class _Ctx:
            def __init__(self, db):
                self.db = db

            async def __aenter__(self):
                return self.db

            async def __aexit__(self, *exc):
                return False

        with patch("routes.chatroom.get_db", lambda: _Ctx(self.db)):
            source_ids = await chatroom_routes._explicit_memory_source_ids(
                "room", "cm_reply",
            )
            await chatroom_routes._register_explicit_memory_coverage(
                OWNER_SECOND, source_ids, "crm_test",
            )
            await self.db.commit()

        covered = await covered_message_ids(
            self.db, OWNER_SECOND,
            ["chatroom:cm_user", "chatroom:cm_reply", "chatroom:cm_other"],
        )
        self.assertEqual(
            covered, {"chatroom:cm_user", "chatroom:cm_reply"},
        )

    async def test_digest_would_skip_the_explicitly_covered_turn(self):
        """核心场景：显式记过的这轮，digest 取数时要跳过。"""
        from routes import chatroom as chatroom_routes

        class _Ctx:
            def __init__(self, db):
                self.db = db

            async def __aenter__(self):
                return self.db

            async def __aexit__(self, *exc):
                return False

        with patch("routes.chatroom.get_db", lambda: _Ctx(self.db)):
            source_ids = await chatroom_routes._explicit_memory_source_ids(
                "room", "cm_reply",
            )
            await chatroom_routes._register_explicit_memory_coverage(
                OWNER_SECOND, source_ids, "crm_test",
            )
            await self.db.commit()

        # digest 取数时消息 id 是裸 id，需要按 chatroom 命名空间补前缀
        pending = [{"id": "cm_user"}, {"id": "cm_reply"}, {"id": "cm_other"}]
        kept, hit = await filter_uncovered_messages(
            self.db, OWNER_SECOND, pending, namespace="chatroom",
        )
        self.assertEqual([m["id"] for m in kept], ["cm_other"])
        self.assertEqual(len(hit), 2)

    async def test_aion_chatroom_write_is_registered_to_main_owner(self):
        """Aion 在聊天室写的记忆落主库，归属必须是主私聊 digest，
        否则主私聊 digest 读群聊消息时不会避开这轮内容。"""
        from routes import chatroom as chatroom_routes

        class _Ctx:
            def __init__(self, db):
                self.db = db

            async def __aenter__(self):
                return self.db

            async def __aexit__(self, *exc):
                return False

        queue = asyncio.Queue()
        with patch("routes.chatroom.get_db", lambda: _Ctx(self.db)), \
             patch("chatroom.get_db", lambda: _Ctx(self.db)), \
             patch("chatroom.get_embedding", new=AsyncMock(return_value=None)), \
             patch("routes.chatroom.get_embedding", new=AsyncMock(return_value=None)), \
             patch("routes.chatroom._chatroom_sys_msg", new=AsyncMock(return_value={"id": "sys"})):
            await chatroom_routes._process_chatroom_commands(
                "记下了 [MEMORY:知今天上线了日记本功能]",
                "room", "aion", "cm_reply", queue, user_text="今天做了很多事",
            )
        await self.db.commit()

        main_hit = await covered_message_ids(
            self.db, OWNER_MAIN, ["chatroom:cm_user", "chatroom:cm_reply"],
        )
        second_hit = await covered_message_ids(
            self.db, OWNER_SECOND, ["chatroom:cm_user", "chatroom:cm_reply"],
        )
        self.assertEqual(len(main_hit), 2, "Aion 的显式记忆必须登记到主私聊 owner")
        self.assertEqual(second_hit, set(), "不应登记到聊天室 owner")


class PrivateExplicitWriteCoverageTests(unittest.IsolatedAsyncioTestCase):
    """私聊显式写入要登记这轮的 user + ai 消息，主私聊 digest 据此避开。"""

    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript("""
            CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT, type TEXT,
                created_at REAL, source_conv TEXT, embedding BLOB, keywords TEXT,
                importance REAL, source_start_ts REAL, source_end_ts REAL,
                unresolved INTEGER, source_msg_id TEXT);
            CREATE TABLE messages (id TEXT PRIMARY KEY, conv_id TEXT, role TEXT, content TEXT,
                created_at REAL, attachments TEXT, reasoning_content TEXT);
            INSERT INTO messages VALUES ('msg_user','conv','user','帮我记住这个',100,'','');
            INSERT INTO messages VALUES ('msg_ai','conv','assistant','记下了',101,'','');
        """)
        await ensure_coverage_schema(self.db)
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_private_write_registers_both_turn_messages(self):
        from routes import chat as chat_routes

        with patch("routes.chat.get_embedding", new=AsyncMock(return_value=None)):
            _, pending = await chat_routes._prepare_private_memory_records(
                "记下了 [MEMORY:这是必须永久记住的约定]", user_text="帮我记住这个",
            )
            await chat_routes._insert_private_memory_records(
                self.db, "conv", "msg_user", "msg_ai", 101, pending,
            )
        await self.db.commit()

        covered = await covered_message_ids(
            self.db, OWNER_MAIN, ["private:msg_user", "private:msg_ai"],
        )
        self.assertEqual(covered, {"private:msg_user", "private:msg_ai"})

        # 主私聊 digest 取数时能识别并跳过
        pending_msgs = [
            {"id": "msg_user", "_source_id": "private:msg_user"},
            {"id": "msg_ai", "_source_id": "private:msg_ai"},
            {"id": "msg_other", "_source_id": "private:msg_other"},
        ]
        kept, hit = await filter_uncovered_messages(
            self.db, OWNER_MAIN, pending_msgs, namespace="private",
        )
        self.assertEqual([m["_source_id"] for m in kept], ["private:msg_other"])
        self.assertEqual(len(hit), 2)

    async def test_private_write_keeps_long_term_type_when_requested(self):
        from routes import chat as chat_routes

        with patch("routes.chat.get_embedding", new=AsyncMock(return_value=None)):
            _, pending = await chat_routes._prepare_private_memory_records(
                "记下了 [MEMORY:长期重要|这是必须永久记住的约定]",
                user_text="帮我记住这个",
            )
            await chat_routes._insert_private_memory_records(
                self.db, "conv", "msg_user", "msg_ai", 101, pending,
            )
        await self.db.commit()

        row = await (
            await self.db.execute("SELECT type FROM memories")
        ).fetchone()
        self.assertEqual(row["type"], "important")


if __name__ == "__main__":
    unittest.main()
