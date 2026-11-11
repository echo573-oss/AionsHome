"""正在播放的歌词、过期心跳，以及听歌在场笔记卷进日常记忆。"""

import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import get_db, init_db
from music_presence import (
    HEARTBEAT_STALE_SECONDS,
    MEMORY_MAX_CHARS,
    MEMORY_MIN_CHARS,
    build_now_playing_block,
    lyric_at_position,
    record_heartbeat,
    record_song_turn,
)
from music_station import get_music_station_store
from routes.music_presence import router as music_presence_router


LRC = "[00:01.00]第一句\n[00:05.00]第二句\n[01:02.00]第三句"


class LyricAtPositionTests(unittest.TestCase):
    def test_line_follows_position_and_ignores_metadata(self):
        self.assertEqual(lyric_at_position(LRC, 0), "")
        self.assertEqual(lyric_at_position(LRC, 1), "第一句")
        self.assertEqual(lyric_at_position(LRC, 5), "第二句")
        self.assertEqual(lyric_at_position(LRC, 62), "第三句")
        tagged = "[ar:周杰伦]\n[00:02.00]天青色等烟雨"
        self.assertEqual(lyric_at_position(tagged, 2), "天青色等烟雨")

    def test_repeated_timestamps_and_short_fraction(self):
        lrc = "[00:01.2][00:09.25]同一句"
        self.assertEqual(lyric_at_position(lrc, 0.5), "")
        self.assertEqual(lyric_at_position(lrc, 1.2), "同一句")
        self.assertEqual(lyric_at_position(lrc, 9.25), "同一句")
        self.assertEqual(lyric_at_position("", 3), "")


class NowPlayingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        await self._clear_presence()
        store = get_music_station_store()
        await store.init()
        await store.record_request(
            {
                "id": 7,
                "name": "晴天",
                "artist": "周杰伦",
                "lyrics_lrc": LRC,
            },
            requester_identity="aion",
            requester_name="小灯",
            source_type="private",
            source_id="conv-presence",
            source_message_id="msg-presence-song",
        )

    async def asyncTearDown(self):
        await self._clear_presence()
        async with get_db() as db:
            await db.execute(
                "DELETE FROM music_station_requests WHERE source_message_id='msg-presence-song'"
            )
            await db.execute("DELETE FROM music_station_tracks WHERE source_key='netease:7'")
            await db.commit()

    async def _clear_presence(self):
        async with get_db() as db:
            for sql in (
                "DELETE FROM music_now_playing",
                "DELETE FROM music_presence_notes",
                "DELETE FROM music_song_presence",
                "DELETE FROM memories WHERE source_conv='presence-test'",
            ):
                try:
                    await db.execute(sql)
                except Exception:
                    pass
            await db.commit()

    def _payload(self, position, event="time"):
        return {
            "track_id": "7",
            "external_id": "7",
            "title": "客户端标题不可信",
            "artist": "客户端歌手",
            "position_sec": position,
            "duration_sec": 269,
            "playing": True,
            "source": "chat_card",
            "event": event,
            "lyric": "客户端不该决定这一句",
        }

    async def _private_context(self):
        from routes import chat as chat_routes

        with (
            patch.object(chat_routes, "build_context_segments", new=AsyncMock(return_value=[])),
            patch.object(chat_routes, "build_play_prompt", return_value=""),
            patch.object(chat_routes, "build_health_summary", new=AsyncMock(return_value="")),
        ):
            return await chat_routes._build_private_context_messages(
                user_name="Mira",
                ai_name="Sol",
                model_key="unit-model",
                conv_id="conv-presence",
            )

    async def test_fresh_playback_injects_server_lyric(self):
        now = time.time()
        await record_heartbeat(self._payload(6, "play"), now=now)
        block = await build_now_playing_block(now=now)
        self.assertIn("【正在听的歌】", block)
        self.assertIn("《晴天》", block)
        self.assertIn("周杰伦", block)
        self.assertIn("进度 0:06", block)
        self.assertIn("当前歌词：第二句", block)
        self.assertNotIn("客户端不该决定这一句", block)
        self.assertNotIn("客户端标题不可信", block)

        messages = await self._private_context()
        runtime = [item["content"] for item in messages if item.get("_ctx") == "runtime"]
        self.assertTrue(any("当前歌词：第二句" in text for text in runtime))

        from chatroom import append_now_playing_state
        state = []
        await append_now_playing_state(state)
        self.assertEqual(state[0]["_ctx"], "runtime")
        self.assertIn("当前歌词：第二句", state[0]["content"])

    async def test_progress_moves_between_heartbeats_before_it_goes_stale(self):
        await record_heartbeat(self._payload(1, "play"), now=8000)
        block = await build_now_playing_block(now=8004)
        self.assertIn("当前歌词：第二句", block)
        self.assertNotIn("第一句", block)
        self.assertEqual(await build_now_playing_block(now=8000 + HEARTBEAT_STALE_SECONDS + 1), "")

    async def test_stale_or_paused_playback_injects_nothing(self):
        await record_heartbeat(self._payload(6, "play"), now=time.time() - 120)
        self.assertEqual(await build_now_playing_block(), "")
        messages = await self._private_context()
        blob = "\n".join(item["content"] for item in messages)
        self.assertNotIn("【正在听的歌】", blob)

        await record_heartbeat(
            {**self._payload(6), "playing": False, "event": "pause"},
            now=time.time(),
        )
        self.assertEqual(await build_now_playing_block(), "")
        from chatroom import append_now_playing_state
        state = [{"role": "user", "content": "原有状态"}]
        await append_now_playing_state(state)
        self.assertEqual(len(state), 1)

    async def test_generated_song_without_lyrics_keeps_title_and_progress(self):
        await record_heartbeat(
            {
                "title": "临时作的歌",
                "artist": "",
                "position_sec": 12,
                "duration_sec": 30,
                "playing": True,
                "source": "generated",
                "event": "play",
            },
            now=9000,
        )
        block = await build_now_playing_block(now=9000)
        self.assertIn("《临时作的歌》", block)
        self.assertIn("进度 0:12 / 0:30", block)
        self.assertNotIn("当前歌词", block)

    async def test_listen_count_increments_when_a_song_starts(self):
        base = 10_000
        await record_heartbeat(self._payload(1, "play"), now=base)
        self.assertIn("一起听过 1 次", await build_now_playing_block(now=base))
        await record_heartbeat(
            {**self._payload(2), "playing": False, "event": "pause"},
            now=base + 1,
        )
        await record_heartbeat(self._payload(2, "play"), now=base + 2)
        self.assertIn("一起听过 1 次", await build_now_playing_block(now=base + 2))
        await record_heartbeat(
            {**self._payload(3), "playing": False, "event": "stop"},
            now=base + 3,
        )
        await record_heartbeat(self._payload(3, "play"), now=base + 4)
        self.assertIn("一起听过 2 次", await build_now_playing_block(now=base + 4))

    async def test_presence_notes_roll_into_daily_memory(self):
        now = 20_000.0
        await record_heartbeat(self._payload(6, "play"), now=now)
        with patch("memory.get_embedding", new=AsyncMock(return_value=None)):
            for index in range(5):
                saved = await record_song_turn(
                    f"第{index}句想说晚风很好听",
                    f"我听见了第{index}句",
                    source_conv="presence-test",
                    ai_msg_id=f"ai-{index}",
                    now=now,
                )
                self.assertEqual(saved, [])
            saved = await record_song_turn(
                "第六句想说这首歌真好听",
                "我也喜欢停在这一句",
                source_conv="presence-test",
                ai_msg_id="ai-6",
                now=now,
            )
        self.assertEqual(len(saved), 1)
        content = saved[0]["content"]
        self.assertEqual(saved[0]["type"], "daily")
        self.assertIn("《晴天》", content)
        self.assertGreaterEqual(len(content), MEMORY_MIN_CHARS)
        self.assertLessEqual(len(content), MEMORY_MAX_CHARS)

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT type, content FROM memories WHERE source_conv='presence-test'"
            )
            rows = await cursor.fetchall()
            note_cursor = await db.execute("SELECT rolled FROM music_presence_notes")
            notes = await note_cursor.fetchall()
        self.assertEqual([row[0] for row in rows], ["daily"])
        self.assertIn("《晴天》", rows[0][1])
        self.assertTrue(notes)
        self.assertTrue(all(note[0] == 1 for note in notes))

        block = await build_now_playing_block(now=now)
        self.assertIn(content, block)
        self.assertNotIn("最近还聊到", block)

        with patch("memory.get_embedding", new=AsyncMock(return_value=None)):
            await record_song_turn(
                "后来又说了一句想你",
                "我在",
                source_conv="presence-test",
                ai_msg_id="ai-7",
                now=now + 1,
            )
        later = await build_now_playing_block(now=now + 1)
        self.assertIn(content, later)
        self.assertIn("最近还聊到", later)
        self.assertIn("想你", later)

    async def test_paused_unrelated_turn_is_ignored_until_the_song_is_named(self):
        now = 30_000.0
        await record_heartbeat(self._payload(4, "play"), now=now)
        await record_heartbeat(
            {**self._payload(4), "playing": False, "event": "pause"},
            now=now + 1,
        )
        with patch("memory.get_embedding", new=AsyncMock(return_value=None)):
            await record_song_turn(
                "今天只是随便聊聊",
                "嗯嗯",
                source_conv="presence-test",
                ai_msg_id="ai-off",
                now=now + 1,
            )
            await record_song_turn(
                "还是《晴天》里那句让我安静",
                "我记得",
                source_conv="presence-test",
                ai_msg_id="ai-named",
                now=now + 2,
            )
        async with get_db() as db:
            cursor = await db.execute("SELECT note FROM music_presence_notes")
            notes = [row[0] for row in await cursor.fetchall()]
        self.assertEqual(len(notes), 1)
        self.assertIn("晴天", notes[0])
        self.assertIn("安静", notes[0])


class NowPlayingRouteTests(unittest.TestCase):
    def setUp(self):
        import asyncio
        asyncio.run(NowPlayingTests().asyncSetUp())
        app = FastAPI()
        app.include_router(music_presence_router)
        self.client = TestClient(app)

    def tearDown(self):
        import asyncio
        asyncio.run(NowPlayingTests().asyncTearDown())

    def test_route_reports_lyric_from_stored_lrc(self):
        response = self.client.post("/api/music/now-playing", json={
            "track_id": "7",
            "external_id": "7",
            "title": "不要用这个标题",
            "artist": "不要用这个歌手",
            "position_sec": 6,
            "duration_sec": 200,
            "playing": True,
            "source": "station",
            "event": "play",
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["playing"])
        self.assertEqual(body["title"], "晴天")
        self.assertEqual(body["lyric"], "第二句")


if __name__ == "__main__":
    unittest.main()
