import json
import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
import ai_providers
import system_logs


class SystemLogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        db = patch.object(system_logs, "LOG_DB_PATH", Path(folder.name) / "logs.db")
        db.start()
        self.addCleanup(db.stop)
        broadcast = patch.object(system_logs, "_broadcast", new=AsyncMock())
        broadcast.start()
        self.addCleanup(broadcast.stop)

    async def test_background_provider_call_records_prompt_and_usage(self):
        async def respond(request):
            return httpx.Response(200, text='data: ' + json.dumps({
                "choices": [{"delta": {"content": "hello"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14},
            }) + '\n\ndata: [DONE]\n\n')

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)
        meta = {}
        with patch("ai_providers.httpx.AsyncClient", return_value=client):
            chunks = [c async for c in ai_providers.call_siliconflow(
                [{"role": "user", "content": "wake up"}], "test-model", meta)]
        self.assertEqual(chunks, ["hello"])
        self.assertIn("system_log_id", meta)
        from system_logs import list_logs
        entry = next(e for e in list_logs() if e["log_id"] == meta["system_log_id"])
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["usage"]["total_tokens"], 14)
        self.assertEqual(entry["prompt_messages"][0]["content"], "wake up")

    async def test_persistence_retention_and_chat_details_share_one_entry(self):
        self.assertEqual(system_logs.LIMIT, 100)
        for i in range(105):
            system_logs.save_log({"log_id": str(i), "started_at": i, "status": "success"})
        entries = system_logs.list_logs()
        self.assertEqual(len(entries), 100)
        self.assertEqual([entries[0]["log_id"], entries[-1]["log_id"]], ["104", "5"])
        system_logs.enrich_debug({"usage": {"system_log_id": "104"}, "recall_topic": "wake"})
        app = FastAPI()
        app.include_router(system_logs.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            entries = (await client.get("/api/system-logs")).json()
            self.assertEqual(len(entries), 100)
            self.assertEqual(entries[0]["recall_topic"], "wake")
            self.assertEqual((await client.delete("/api/system-logs")).status_code, 200)
            self.assertEqual((await client.get("/api/system-logs")).json(), [])

    async def test_retention_is_grouped_per_room(self):
        for i in range(105):
            system_logs.save_log({"log_id": f"p{i}", "started_at": i, "status": "success"})
        for i in range(105):
            system_logs.save_log({"log_id": f"r{i}", "room_id": "r1", "started_at": 1000 + i, "status": "success"})
        with system_logs._connect() as db:
            total = db.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        self.assertEqual(total, 200)
        room = system_logs.list_logs("r1")
        self.assertEqual(len(room), 100)
        self.assertEqual([room[0]["log_id"], room[-1]["log_id"]], ["r104", "r5"])
        everything = system_logs.list_logs()
        self.assertEqual(len(everything), 100)
        self.assertEqual(everything[0]["log_id"], "r104")

    async def test_legacy_table_gains_room_column_and_backfills(self):
        legacy = Path(tempfile.mkdtemp()) / "legacy.db"
        con = sqlite3.connect(legacy)
        con.execute("CREATE TABLE logs (id TEXT PRIMARY KEY, started REAL, payload TEXT)")
        con.execute("INSERT INTO logs VALUES (?,?,?)",
                    ("old1", 1, json.dumps({"log_id": "old1", "room_id": "r9", "started_at": 1})))
        con.execute("INSERT INTO logs VALUES (?,?,?)",
                    ("old2", 2, json.dumps({"log_id": "old2", "started_at": 2})))
        con.commit()
        con.close()
        with patch.object(system_logs, "LOG_DB_PATH", legacy):
            self.assertEqual([e["log_id"] for e in system_logs.list_logs("r9")], ["old1"])
            self.assertEqual([e["log_id"] for e in system_logs.list_logs()], ["old2", "old1"])
        with sqlite3.connect(legacy) as con:
            columns = [row[1] for row in con.execute("PRAGMA table_info(logs)")]
        self.assertIn("room", columns)

    async def test_enrich_debug_keeps_room_column_in_sync(self):
        system_logs.save_log({"log_id": "x1", "started_at": 1, "room_id": "r1", "status": "success"})
        system_logs.enrich_debug({"usage": {"system_log_id": "x1"}, "recall_topic": "wake"})
        with system_logs._connect() as db:
            room = db.execute("SELECT room FROM logs WHERE id = 'x1'").fetchone()[0]
        self.assertEqual(room, "r1")
        self.assertEqual(system_logs.list_logs("r1")[0]["recall_topic"], "wake")

    async def test_room_keeps_each_kitchen_call_and_enrichment_preserves_dispatched_prompts(self):
        import kitchen_chat
        from capabilities import build_capability_prompt_items, format_ability_block
        sent = []
        def client(*args, **kwargs):
            def respond(request):
                sent.append(json.loads(request.content)["messages"])
                reply = "[KITCHEN:查看]" if len(sent)==1 else "库存看好啦。"
                delta = {"choices":[{"delta":{"content":reply},"finish_reason":"stop"}]}
                return httpx.Response(200,text="data: "+json.dumps(delta)+"\n\ndata: [DONE]\n\n")
            return real_client(transport=httpx.MockTransport(respond))
        real_client = httpx.AsyncClient
        room = "test-room"
        meta = {"_room_log":{"room_id":room,"actor":"aion","msg_id":"same-reply"}}
        snapshot = dict(enabled=True,epoch=1,items=[],menus=[],proposals=[],preferences={},pending=[],errors=[])
        def generate(messages):
            return ai_providers.stream_ai(messages,"test-model",meta)
        with patch.dict(ai_providers.MODELS,{"test-model":{"provider":"siliconflow","model":"test"}}), \
             patch.object(ai_providers.httpx,"AsyncClient",side_effect=client), \
             patch.object(ai_providers,"with_current_device_context",side_effect=lambda msgs:msgs+[{"role":"user","content":"当前设备样例"}]), \
             patch.object(kitchen_chat.kitchen,"enabled",return_value=True), \
             patch.object(kitchen_chat.kitchen,"state",AsyncMock(return_value=snapshot)), \
             patch.object(kitchen_chat.kitchen,"snapshot",AsyncMock(return_value=snapshot)), \
             patch("kitchen_sentinel.flush_pending",AsyncMock()):
            with patch("capabilities.is_capability_enabled", side_effect=lambda name: name in {"cam_check", "schedule"}):
                abilities = await build_capability_prompt_items("用户", include_kitchen=True)
            messages = [
                {"role":"user","content":format_ability_block(abilities)},
                {"role":"assistant","content":"好的，需要时我会使用这些指令。"},
                {"role":"user","content":"今天吃啥？"},
            ]
            text = "".join([s async for s in kitchen_chat.chat_stream(
                messages,generate,actor="aion",scope="chatroom",
                parent_id=room,query="今天吃啥？",queue=asyncio.Queue(),state={})])
        self.assertEqual(text,"库存看好啦。")
        queue, manager = asyncio.Queue(), AsyncMock()
        await system_logs.emit_chat_debug(queue,{"type":"debug","usage":meta,"prompt_messages":[],"prompt_count":0},manager)
        second,first = system_logs.list_logs(room)
        self.assertEqual([first["call_number"],second["call_number"]],[1,2])
        self.assertNotEqual(first["log_id"],second["log_id"])
        self.assertEqual([first["prompt_messages"],second["prompt_messages"]],sent)
        for dispatched in sent:
            prompt = ai_providers._build_cli_prompt(dispatched)
            self.assertEqual(prompt.count(kitchen_chat.ABILITY), 1)
            self.assertLess(prompt.index("【可用指令】"), prompt.index(kitchen_chat.ABILITY))
            self.assertLess(prompt.index(kitchen_chat.ABILITY), prompt.index("[ALARM:"))
            self.assertLess(prompt.index(kitchen_chat.ABILITY), prompt.index("今天吃啥？"))
        self.assertFalse(any("[小厨房本次查询]" in m["content"] for m in sent[0]))
        receipt_index = next(i for i,m in enumerate(sent[1]) if m["content"].startswith("[小厨房本次查询]"))
        self.assertEqual(sent[1][receipt_index - 1], {"role":"assistant","content":"[KITCHEN:查看]"})
        self.assertEqual(queue.get_nowait()["prompt_messages"],sent[1])
        self.assertEqual(manager.broadcast.await_args.args[0]["data"]["prompt_messages"],sent[1])
        self.assertEqual(system_logs.list_logs("other-room"),[])
        system_logs.clear_logs("other-room")
        self.assertEqual(len(system_logs.list_logs(room)),2)

    async def test_concurrent_cli_calls_record_their_own_final_text_and_instructions(self):
        profile = self.enterContext(tempfile.TemporaryDirectory())
        instructions = Path(profile)/"agent.md"
        instructions.write_text("角色指令",encoding="utf-8")
        @system_logs.logged_ai_call("test-cli")
        async def provider(prompt):
            yield "starting"
            await asyncio.sleep(0)
            await system_logs.record_cli_prompt("实际发送："+prompt,instructions_file=instructions,developer="工具指令")
            yield "ok"
        async def consume(value):
            return [chunk async for chunk in provider(value)]
        await asyncio.gather(consume("first"),consume("second"))
        prompts = [e["prompt_messages"] for e in system_logs.list_logs()]
        self.assertEqual({p[-1]["content"] for p in prompts},{"实际发送：first","实际发送：second"})
        self.assertTrue(all([m["content"] for m in p[:2]]==["角色指令","工具指令"] for p in prompts))

    async def test_error_chunks_exceptions_and_closed_streams_are_recorded(self):
        @system_logs.logged_ai_call("test")
        async def stream(messages, model, meta=None):
            yield "[测试错误 503] unavailable"
            raise RuntimeError("disconnected")

        with self.assertRaises(RuntimeError):
            async for _ in stream([], "test"):
                pass
        self.assertEqual(system_logs.list_logs()[0]["status"], "error")
        self.assertIn("disconnected", system_logs.list_logs()[0]["error_text"])

        @system_logs.logged_ai_call("test")
        async def cancelled(messages, model):
            yield "partial"
            await asyncio.sleep(60)

        generator = cancelled([], "test")
        self.assertEqual(await anext(generator), "partial")
        self.assertEqual(system_logs.list_logs()[0]["status"], "running")
        await generator.aclose()
        self.assertEqual(system_logs.list_logs()[0]["status"], "cancelled")

    async def test_sentinel_calls_are_logged_without_serializing_credentials(self):
        from memory import _call_sentinel_text

        async def respond(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "stay"}}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)
        with patch("memory.httpx.AsyncClient", return_value=client):
            result = await _call_sentinel_text({
                "use_openai": True, "base_url": "https://example.invalid",
                "api_key": "secret-test-key", "model": "sentinel-test",
            }, "Should I wake up?")
        self.assertEqual(result, "stay")
        entries = system_logs.list_logs()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["model"], "sentinel-test")
        self.assertNotIn("secret-test-key", json.dumps(entries))


if __name__ == "__main__":
    unittest.main()
