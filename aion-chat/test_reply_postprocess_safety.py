"""???????????????????

???2026-11-06 ????? [MEMORY:...]?????? Emoji??????????
GBK ??????print() ? UnicodeEncodeError ????????????? messages?
"""

import ast
import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite

import routes.chat as chat_routes

HERE = Path(__file__).resolve().parent


class _DbFixture:
    async def __aenter__(self):
        self.db = await aiosqlite.connect(":memory:")
        await self.db.executescript("""
            CREATE TABLE conversations (id TEXT PRIMARY KEY, updated_at REAL);
            CREATE TABLE messages (id TEXT PRIMARY KEY, conv_id TEXT, role TEXT, content TEXT,
                                   created_at REAL, attachments TEXT, reasoning_content TEXT);
            INSERT INTO conversations VALUES ('conv', 0);
        """)

        @asynccontextmanager
        async def get_db():
            yield self.db

        self.broadcast = AsyncMock()
        self.export = AsyncMock()
        self._patches = [
            patch.object(chat_routes, "get_db", get_db),
            patch.object(chat_routes.manager, "broadcast", new=self.broadcast),
            patch.object(chat_routes, "export_conversation", new=self.export),
        ]
        for p in self._patches:
            p.start()
        return self

    async def __aexit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        await self.db.close()

    async def rows(self):
        cur = await self.db.execute("SELECT id, role, content FROM messages ORDER BY rowid")
        return await cur.fetchall()


class FallbackSaveTest(unittest.IsolatedAsyncioTestCase):
    async def test_saves_generated_reply_when_postprocess_crashed(self):
        async with _DbFixture() as fx:
            saved = await chat_routes._save_reply_after_postprocess_error(
                "conv", "msg_ai", "天，真的搬过来了😭\n[MEMORY:知把岚搬进了新家😭]", "思考")
            self.assertTrue(saved)
            rows = await fx.rows()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "msg_ai")
            self.assertEqual(rows[0][1], "assistant")
            self.assertIn("天，真的搬过来了😭", rows[0][2])
            # 记忆指令属于工具协议，不能原样露给用户
            self.assertNotIn("[MEMORY:", rows[0][2])
            event = fx.broadcast.await_args.args[0]
            self.assertEqual(event["type"], "msg_created")
            self.assertEqual(event["data"]["id"], "msg_ai")
            fx.export.assert_awaited_once_with("conv")

    async def test_does_not_duplicate_reply_already_saved(self):
        async with _DbFixture() as fx:
            await fx.db.execute(
                "INSERT INTO messages VALUES ('msg_ai','conv','assistant','原回复',1,'','')")
            await fx.db.commit()
            saved = await chat_routes._save_reply_after_postprocess_error("conv", "msg_ai", "另一份")
            self.assertFalse(saved)
            self.assertEqual([r[2] for r in await fx.rows()], ["原回复"])
            fx.broadcast.assert_not_awaited()

    async def test_skips_when_nothing_was_generated(self):
        async with _DbFixture() as fx:
            self.assertFalse(await chat_routes._save_reply_after_postprocess_error("conv", "msg_ai", "  "))
            self.assertEqual(await fx.rows(), [])


class PrivateMemoryRecordTest(unittest.IsolatedAsyncioTestCase):
    async def test_memory_links_to_saved_trigger_and_reply(self):
        async with _DbFixture() as fx:
            await fx.db.executescript("""
                CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT, type TEXT,
                    created_at REAL, source_conv TEXT, embedding BLOB, keywords TEXT,
                    importance REAL, source_start_ts REAL, source_end_ts REAL,
                    unresolved INTEGER, source_msg_id TEXT);
                INSERT INTO messages VALUES ('user','conv','user','Original',1,'','');
                INSERT INTO messages VALUES ('reply','conv','assistant','Reply',2,'','');
            """)
            with patch.object(chat_routes, "get_embedding", new=AsyncMock(return_value=None)):
                clean, pending = await chat_routes._prepare_private_memory_records(
                    "Reply [MEMORY:First card] [MEMORY:Second card]"
                )
            self.assertEqual(clean, "Reply")
            saved = await chat_routes._insert_private_memory_records(
                fx.db, "conv", "user", "reply", 2, pending,
            )
            rows = await (await fx.db.execute(
                "SELECT source_msg_id,source_start_ts,source_end_ts FROM memories"
            )).fetchall()
            self.assertEqual(len({item["id"] for item in saved}), 2)
            self.assertEqual(rows, [('["private:user", "private:reply"]', 1.0, 2.0)] * 2)
            queue = asyncio.Queue()
            await chat_routes._publish_private_memory_records(saved, "reply", queue)
            self.assertEqual((await queue.get())["mem_id"], saved[0]["id"])


class GenerationPathsUseFallbackTest(unittest.TestCase):
    def test_every_bg_generate_falls_back_on_exception(self):
        """send / regenerate / edit-resend 三条生成路径的 except 都要兜底存正文。"""
        tree = ast.parse((HERE / "routes" / "chat.py").read_text(encoding="utf-8"))
        checked = 0
        for fn in ast.walk(tree):
            if not (isinstance(fn, ast.AsyncFunctionDef) and fn.name == "_bg_generate"):
                continue
            calls = {
                node.func.id
                for handler in ast.walk(fn) if isinstance(handler, ast.ExceptHandler)
                for node in ast.walk(handler)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            with self.subTest(line=fn.lineno):
                self.assertIn("_save_reply_after_postprocess_error", calls)
            checked += 1
        self.assertEqual(checked, 3)


class RedirectedOutputEncodingTest(unittest.TestCase):
    def test_main_startup_makes_emoji_print_safe_when_redirected(self):
        """模拟一键启动：输出重定向到文件时，print 含 Emoji 的文本不能抛异常。"""
        src = (HERE / "main.py").read_text(encoding="utf-8")
        prelude = src.split('if __name__ == "__main__" and "--scheduled"', 1)[0]
        code = prelude + '\nprint("[MEMORY] AI 主动录入记忆: 搬过来了😭")\n'
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        env["PYTHONUTF8"] = "0"
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "stdout.log"
            with open(out_path, "wb") as out:
                proc = subprocess.run([sys.executable, "-c", code], stdout=out,
                                      stderr=subprocess.PIPE, env=env, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
            self.assertIn("搬过来了😭", out_path.read_bytes().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()