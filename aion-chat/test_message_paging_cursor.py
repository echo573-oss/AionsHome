"""历史消息翻页游标的回归测试。

背景：同一秒内发出的多条消息，created_at 完全相同。旧的翻页实现只用
时间戳做游标（created_at < before），于是同一秒的其余消息会被跳过，
翻页时消息会"凭空少掉"。

修复方式是引入 (created_at, rowid) 复合游标：请求里带上上一页最早那条
消息的 ID，服务端用行号做第二级排序键精确切分。

这里固定住三件事：
1. 同秒消息翻页不丢、不重
2. 定位跳转（messages_around）能准确取到目标及其前后
3. 不带 before_id 的旧调用方式仍然可用（向后兼容）
"""
import asyncio
import unittest

from fastapi.testclient import TestClient

from main import app
from database import init_db, get_db


client = TestClient(app)

CONV_ID = 'conv-paging-cursor'


async def _seed_same_second_messages(count=6):
    """写入同一秒内的 count 条消息，外加一条更早的，构造真实的翻页场景。"""
    async with get_db() as db:
        await db.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (CONV_ID, 'paging cursor', 1000.0, 2000.0))
        # 更早的一条，用于确认第一页边界
        await db.execute(
            "INSERT INTO messages (id, conv_id, role, content, created_at) VALUES (?,?,?,?,?)",
            ('msg-older', CONV_ID, 'user', 'older', 1000.0))
        for index in range(count):
            await db.execute(
                "INSERT INTO messages (id, conv_id, role, content, created_at) VALUES (?,?,?,?,?)",
                (f'msg-same-{index}', CONV_ID, 'user', f'same-second-{index}', 2000.0))
        await db.commit()


async def _clear():
    async with get_db() as db:
        await db.execute("DELETE FROM messages WHERE conv_id=?", (CONV_ID,))
        await db.execute("DELETE FROM conversations WHERE id=?", (CONV_ID,))
        await db.commit()


def _seed_other_conversation():
    async def run():
        async with get_db() as db:
            await db.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?,?,?,?)",
                ('conv-other', 'other', 1000.0, 2000.0))
            await db.execute(
                "INSERT INTO messages (id, conv_id, role, content, created_at) VALUES (?,?,?,?,?)",
                ('msg-other', 'conv-other', 'user', 'other', 2000.0))
            await db.commit()
    return run()


async def _conversation_exists(conv_id):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, updated_at FROM conversations WHERE id=?",
            (conv_id,),
        )
        return await cursor.fetchone() is not None


def _drop_other_conversation():
    async def run():
        async with get_db() as db:
            await db.execute("DELETE FROM messages WHERE conv_id=?", ('conv-other',))
            await db.execute("DELETE FROM conversations WHERE id=?", ('conv-other',))
            await db.commit()
    return run()


def _message_order(message_id):
    """消息在时间轴上的位置；同秒内按写入顺序（rowid）排列。"""
    if message_id == 'msg-older':
        return (1000.0, 0)
    index = int(message_id.rsplit('-', 1)[1])
    return (2000.0, index)


class MessagePagingCursorTests(unittest.TestCase):
    def setUp(self):
        # The test process redirects DB_PATH to a temporary directory, but the
        # schema still has to be created there before the fixtures can insert.
        asyncio.run(init_db())
        asyncio.run(_clear())
        asyncio.run(_seed_same_second_messages())
        self.assertTrue(asyncio.run(_conversation_exists(CONV_ID)))

    def tearDown(self):
        asyncio.run(_clear())

    def _messages(self, **params):
        response = client.get(f'/api/conversations/{CONV_ID}/messages', params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_same_second_messages_are_not_skipped_when_paging(self):
        """翻页到同秒消息的末尾时，同一秒的其它消息仍然要能取到。"""
        # 接口统一按时间正序返回（旧的在前）
        first = self._messages(limit=3)
        self.assertEqual([m['id'] for m in first], ['msg-same-3', 'msg-same-4', 'msg-same-5'])

        # 带上上一页最早一条的 ID，才能在同一秒内继续往下翻
        oldest = first[0]
        second = self._messages(limit=3, before=oldest['created_at'], before_id=oldest['id'])
        self.assertEqual([m['id'] for m in second], ['msg-same-0', 'msg-same-1', 'msg-same-2'])

        # 继续翻到更早一秒的那条
        oldest2 = second[0]
        third = self._messages(limit=3, before=oldest2['created_at'], before_id=oldest2['id'])
        self.assertEqual([m['id'] for m in third], ['msg-older'])

        # 全部取到且没有重复
        seen = [m['id'] for m in first + second + third]
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), 7)

    def test_paging_without_before_id_keeps_legacy_behaviour(self):
        """旧的调用方式（只传 before）不能因为游标改动而失效。"""
        rows = self._messages(limit=3, before=2000.0)
        self.assertTrue(all(m['created_at'] < 2000.0 for m in rows))
        self.assertEqual([m['id'] for m in rows], ['msg-older'])

    def test_messages_around_locates_target_among_same_second_messages(self):
        """定位跳转要能在同秒消息里准确找到目标，并带上前后各若干条。"""
        response = client.get(f'/api/conversations/{CONV_ID}/messages-around/msg-same-3',
                               params={'limit': 2})
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        ids = [m['id'] for m in rows]
        self.assertIn('msg-same-3', ids)
        # 目标之前应包含更早的同秒消息
        self.assertIn('msg-same-2', ids)
        self.assertIn('msg-same-4', ids)
        # 结果按时间正序返回（旧的在前）
        self.assertEqual(ids, sorted(ids, key=_message_order))

    def test_messages_around_ignores_messages_from_other_conversations(self):
        """定位查询必须限定在同一个会话内，不能串到别的会话。"""
        asyncio.run(_seed_other_conversation())
        try:
            self.assertTrue(asyncio.run(_conversation_exists('conv-other')))
            response = client.get(f'/api/conversations/{CONV_ID}/messages-around/msg-same-3',
                                   params={'limit': 5})
            self.assertEqual(response.status_code, 200, response.text)
            ids = [m['id'] for m in response.json()]
            self.assertNotIn('msg-other', ids)

            foreign_target = client.get(f'/api/conversations/{CONV_ID}/messages-around/msg-other',
                                        params={'limit': 5})
            self.assertEqual(foreign_target.status_code, 200, foreign_target.text)
            self.assertEqual(foreign_target.json(), [])
        finally:
            asyncio.run(_drop_other_conversation())


if __name__ == '__main__':
    unittest.main()
