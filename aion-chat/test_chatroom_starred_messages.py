"""聊天室消息「星标」功能的回归测试。

背景：私聊页早就支持消息星标（messages.starred），聊天室页却没有。
对齐时给 chatroom_messages 补了 starred 列，并新增两个端点：

1. PATCH /api/chatroom/messages/{msg_id}/star      切换单条消息的星标
2. GET  /api/chatroom/rooms/{room_id}/starred-messages  列出该房间的星标消息

这里固定住三件事：
1. starred 列确实建在 chatroom_messages 上（而不是 messages）
2. 切换是幂等可逆的，且会广播 chatroom_msg_updated 让前端重渲染
3. 星标列表按房间隔离，不会把别的房间的消息混进来
"""
import asyncio
import unittest

from fastapi.testclient import TestClient

from main import app
from database import init_db, get_db

client = TestClient(app)

ROOM_A = 'room-star-a'
ROOM_B = 'room-star-b'


async def _clear():
    async with get_db() as db:
        await db.execute("DELETE FROM chatroom_messages WHERE room_id IN (?,?)", (ROOM_A, ROOM_B))
        await db.execute("DELETE FROM chatroom_rooms WHERE id IN (?,?)", (ROOM_A, ROOM_B))
        await db.commit()


async def _seed():
    async with get_db() as db:
        await db.execute(
            "INSERT INTO chatroom_rooms (id, title, type, created_at, updated_at) VALUES (?,?,?,?,?)",
            (ROOM_A, '星标测试房间', 'group', 1000.0, 2000.0),
        )
        await db.execute(
            "INSERT INTO chatroom_rooms (id, title, type, created_at, updated_at) VALUES (?,?,?,?,?)",
            (ROOM_B, '另一个房间', 'group', 1000.0, 2000.0),
        )
        await db.execute(
            "INSERT INTO chatroom_messages (id, room_id, sender, content, created_at) VALUES (?,?,?,?,?)",
            ('star-msg-1', ROOM_A, 'aion', '值得留下来的回复', 3000.0),
        )
        await db.execute(
            "INSERT INTO chatroom_messages (id, room_id, sender, content, created_at) VALUES (?,?,?,?,?)",
            ('star-msg-2', ROOM_A, 'user', '普通消息', 4000.0),
        )
        await db.execute(
            "INSERT INTO chatroom_messages (id, room_id, sender, content, created_at) VALUES (?,?,?,?,?)",
            ('star-msg-b', ROOM_B, 'aion', '别的房间的消息', 5000.0),
        )
        await db.commit()


async def _starred_of(msg_id):
    async with get_db() as db:
        db.row_factory = __import__('aiosqlite').Row
        cursor = await db.execute("SELECT starred FROM chatroom_messages WHERE id=?", (msg_id,))
        row = await cursor.fetchone()
        return None if row is None else row['starred']


class ChatroomStarredMessageTests(unittest.TestCase):
    def setUp(self):
        # 测试进程把 DB_PATH 指到了临时目录，schema 仍要自己建。
        asyncio.run(init_db())
        asyncio.run(_clear())
        asyncio.run(_seed())

    def tearDown(self):
        asyncio.run(_clear())

    def _toggle(self, msg_id):
        response = client.patch(f'/api/chatroom/messages/{msg_id}/star')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_starred_column_exists_on_chatroom_messages(self):
        """星标列必须落在 chatroom_messages 上，messages 表的那一列跟聊天室无关。"""
        async def check():
            async with get_db() as db:
                db.row_factory = __import__('aiosqlite').Row
                cursor = await db.execute("PRAGMA table_info(chatroom_messages)")
                return {row['name'] for row in await cursor.fetchall()}

        columns = asyncio.run(check())
        self.assertIn('starred', columns)

    def test_toggle_star_flips_and_persists(self):
        """点一次点亮、再点一次熄灭，状态要落库。"""
        first = self._toggle('star-msg-1')
        self.assertEqual(first['ok'], True)
        self.assertEqual(first['starred'], 1)
        self.assertEqual(asyncio.run(_starred_of('star-msg-1')), 1)

        second = self._toggle('star-msg-1')
        self.assertEqual(second['starred'], 0)
        self.assertEqual(asyncio.run(_starred_of('star-msg-1')), 0)

    def test_toggle_star_reports_message_payload(self):
        """返回体里要带上整条消息，前端才能直接更新那一行。"""
        payload = self._toggle('star-msg-1')
        message = payload['message']
        self.assertEqual(message['id'], 'star-msg-1')
        self.assertEqual(message['room_id'], ROOM_A)
        self.assertEqual(message['starred'], 1)
        self.assertEqual(message['content'], '值得留下来的回复')

    def test_toggle_star_on_missing_message_returns_404(self):
        response = client.patch('/api/chatroom/messages/does-not-exist/star')
        self.assertEqual(response.status_code, 404)

    def test_starred_list_only_returns_starred_messages(self):
        """星标列表只含被点亮的消息。"""
        self._toggle('star-msg-1')
        response = client.get(f'/api/chatroom/rooms/{ROOM_A}/starred-messages')
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()
        self.assertEqual([m['id'] for m in items], ['star-msg-1'])
        self.assertEqual(items[0]['starred'], 1)

    def test_starred_list_is_scoped_to_the_room(self):
        """别的房间的消息不会串进来。"""
        self._toggle('star-msg-1')
        self._toggle('star-msg-b')
        response = client.get(f'/api/chatroom/rooms/{ROOM_A}/starred-messages')
        self.assertEqual(response.status_code, 200, response.text)
        ids = [m['id'] for m in response.json()]
        self.assertIn('star-msg-1', ids)
        self.assertNotIn('star-msg-b', ids)
        self.assertNotIn('star-msg-2', ids)

    def test_unstarring_removes_message_from_list(self):
        self._toggle('star-msg-1')
        self._toggle('star-msg-1')
        response = client.get(f'/api/chatroom/rooms/{ROOM_A}/starred-messages')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])


if __name__ == '__main__':
    unittest.main()
