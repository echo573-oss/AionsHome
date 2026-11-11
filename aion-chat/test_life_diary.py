import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite

from life_diary_format import merge_document, new_document, parse_body, parse_frontmatter


class DiaryFormatTests(unittest.TestCase):
    def test_new_document_keeps_minimal_frontmatter(self):
        content = new_document('2026-11-08', {}, [{'kind': '自由记录', 'text': '窗外又有小鸟来了'}])
        self.assertIn('date: 2026-11-08', content)
        self.assertIn('weekday: 周日', content)
        self.assertIn('type: daily', content)
        self.assertIn('tags: [一日一页]', content)
        self.assertIn('> [!note] 🔄 自由记录', content)
        self.assertIn('> 窗外又有小鸟来了', content)

    def test_merge_preserves_external_blocks_and_unknown_keys(self):
        existing = (
            '---\n'
            'date: 2026-11-08\n'
            'weekday: 周日\n'
            'type: daily\n'
            'lunar: 十月廿一\n'
            'tags: [一日一页]\n'
            '---\n'
            '\n'
            '> [!note] 🔄 自由记录\n'
            '>\n'
            '> 探索新技术和玩法\n'
            '\n'
            '## Calendar\n'
            '\n'
            '- 10:00 全家吃饭\n'
            '\n'
            '## 📷 今日图片\n'
            '\n'
            '![[IMG_0001.jpg]]\n'
        )
        merged = merge_document(existing, date='2026-11-08',
                                fields={'exercise_type': ['网球']},
                                blocks=[{'kind': '自由记录', 'text': '去打网球了'}])
        self.assertIn('lunar: 十月廿一', merged)
        self.assertIn('## Calendar', merged)
        self.assertIn('- 10:00 全家吃饭', merged)
        self.assertIn('## 📷 今日图片', merged)
        self.assertIn('![[IMG_0001.jpg]]', merged)
        self.assertIn('exercise_type: [网球]', merged)
        self.assertIn('> 探索新技术和玩法', merged)
        self.assertIn('> 去打网球了', merged)

    def test_merge_appends_numbered_items_compactly(self):
        existing = ('---\ndate: 2026-11-08\n---\n\n'
                    '> [!success] ✅ 完成\n'
                    '>\n'
                    '> 1. 取快递\n')
        merged = merge_document(existing, date='2026-11-08', fields={},
                                blocks=[{'kind': '完成', 'text': '交了电费'}])
        self.assertIn('> 1. 取快递', merged)
        self.assertIn('> 2. 交了电费', merged)
        # 同一条目块内保持紧凑，不额外插入段落分隔
        self.assertNotIn('> 1. 取快递\n>\n', merged)

    def test_merge_does_not_duplicate_the_same_fragment(self):
        existing = ('---\ndate: 2026-11-08\n---\n\n'
                    '> [!note] 🔄 自由记录\n>\n> 去打网球了\n')
        once = merge_document(existing, date='2026-11-08', fields={},
                              blocks=[{'kind': '自由记录', 'text': '去打网球了'}])
        twice = merge_document(once, date='2026-11-08', fields={},
                               blocks=[{'kind': '自由记录', 'text': '去打网球了'}])
        self.assertEqual(once.count('去打网球了'), 1)
        self.assertEqual(twice.count('去打网球了'), 1)

    def test_merge_keeps_baby_age_line_untouched(self):
        existing = ('---\ndate: 2026-11-03\n---\n\n'
                    '> [!quote] 👶 元宝记录\n'
                    '>\n'
                    '> **今天元宝2岁9个月**\n'
                    '>\n'
                    '> 能记住班上几个小朋友的名字了\n')
        merged = merge_document(existing, date='2026-11-03', fields={},
                                blocks=[{'kind': '元宝记录', 'text': '自己取了个小名叫小包子'}])
        self.assertIn('> **今天元宝2岁9个月**', merged)
        self.assertIn('> 自己取了个小名叫小包子', merged)
        self.assertIn('能记住班上几个小朋友的名字了', merged)

    def test_new_blocks_follow_the_app_callout_order(self):
        existing = ('---\ndate: 2026-11-03\n---\n\n'
                    '> [!quote] 👶 元宝记录\n>\n> **今天元宝2岁9个月**\n\n'
                    '> [!note] 🔄 自由记录\n>\n> 探索新技术和玩法\n')
        merged = merge_document(existing, date='2026-11-03', fields={},
                                blocks=[{'kind': '完成', 'text': '把窗帘装好了'},
                                        {'kind': '小开心', 'text': '窗外又有小鸟来了'}])
        order = [chunk[0] for chunk in parse_body(merged.split('---\n', 2)[-1].splitlines())
                 if chunk and chunk[0].startswith('> [!')]
        self.assertEqual(order, ['> [!success] ✅ 完成', '> [!example] 🎁 小开心',
                                  '> [!quote] 👶 元宝记录', '> [!note] 🔄 自由记录'])

    def test_frontmatter_parser_keeps_block_lists(self):
        entries = parse_frontmatter(['mode:', '  - 维护', '  - 恢复', 'tags: [一日一页]'])
        self.assertEqual(entries[0]['value']['type'], 'list')
        self.assertEqual(entries[0]['value']['items'], ['维护', '恢复'])
        self.assertEqual(entries[1]['value']['items'], ['一日一页'])

    def test_body_parser_splits_on_blank_lines_only(self):
        chunks = parse_body(['> [!note] 🔄 自由记录', '>', '> 一段', '', '## Calendar', '', '- 事件'])
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0][0], '> [!note] 🔄 自由记录')
        self.assertEqual(chunks[1][0], '## Calendar')
        self.assertEqual(chunks[2][0], '- 事件')


class LifeDiaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import life_diary as service
        self.service = service
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'DiaryInbox'
        self.db_path = Path(self.tmp.name) / 'chat.db'
        self.db_patch = patch.object(service, 'get_db', self.connect)
        self.enabled_patch = patch.object(service, 'enabled', return_value=True)
        self.root_patch = patch.object(service, 'diary_root', return_value=self.root)
        self.model_patch = patch.object(service, '_call_model', AsyncMock(return_value={'days': []}))
        self.db_patch.start()
        self.enabled_patch.start()
        self.root_patch.start()
        self.model_mock = self.model_patch.start()
        async with self.connect() as db:
            await db.execute('CREATE TABLE messages (id TEXT, conv_id TEXT, role TEXT, content TEXT, created_at REAL)')
            await db.execute('CREATE TABLE chatroom_messages (id TEXT, room_id TEXT, sender TEXT, content TEXT, created_at REAL)')
            await db.commit()
        await service.ensure_schema()

    async def asyncTearDown(self):
        self.model_patch.stop()
        self.root_patch.stop()
        self.enabled_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def connect(self):
        return aiosqlite.connect(self.db_path)

    async def message(self, msg_id='one', *, text='今天去打网球了', role='user', scope='private', ts=None):
        ts = ts or time.time()
        private = scope == 'private'
        async with self.connect() as db:
            await db.execute(f'INSERT INTO {"messages" if private else "chatroom_messages"} VALUES (?,?,?,?,?)',
                             (msg_id, 'room', role, text, ts))
            await db.commit()
        return {'type': 'msg_created' if private else 'chatroom_msg_created', 'data': {
            'id': msg_id, 'conv_id' if private else 'room_id': 'room',
            'role' if private else 'sender': role, 'content': text, 'created_at': ts,
        }}

    def set_model(self, payload):
        self.model_mock.return_value = payload

    def diary_path(self, date):
        year, month, _ = date.split('-')
        return self.root / f'{year}-{month}' / f'{date}.md'

    async def test_only_user_messages_are_queued(self):
        await self.service.enqueue_event(await self.message('a', role='user'))
        await self.service.enqueue_event(await self.message('b', role='assistant'))
        async with self.connect() as db:
            rows = await (await db.execute('SELECT source_id FROM life_diary_jobs')).fetchall()
        self.assertEqual([row[0] for row in rows], ['private:a'])

    async def test_disabled_capability_collects_nothing(self):
        with patch.object(self.service, 'enabled', return_value=False):
            queued = await self.service.enqueue_event(await self.message('a'))
            ran = await self.service.maybe_run_batch(force=True)
        self.assertFalse(queued)
        self.assertFalse(ran)
        async with self.connect() as db:
            count = (await (await db.execute('SELECT count(*) FROM life_diary_jobs')).fetchone())[0]
        self.assertEqual(count, 0)

    async def test_batch_files_the_event_date_not_the_message_date(self):
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        await self.service.enqueue_event(await self.message('a', text='昨天去打网球了，回来有点累'))
        self.set_model({'days': [{
            'date': yesterday, 'date_inferred': True,
            'fields': {'exercise_type': ['网球']},
            'blocks': [{'kind': '自由记录', 'text': '去打网球了，回来有点累'}],
            'sources': ['U1'],
        }]})
        ran = await self.service.maybe_run_batch(force=True)
        self.assertTrue(ran)
        path = self.diary_path(yesterday)
        self.assertTrue(path.exists())
        content = path.read_text(encoding='utf-8')
        self.assertIn(f'date: {yesterday}', content)
        self.assertIn('exercise_type: [网球]', content)
        self.assertIn('去打网球了，回来有点累', content)
        self.assertNotIn('baby_age', content)

    async def test_day_without_valid_source_is_ignored(self):
        await self.service.enqueue_event(await self.message('a'))
        self.set_model({'days': [{'date': datetime.now().date().isoformat(),
                                  'blocks': [{'kind': '自由记录', 'text': '凭空多出来的一条'}],
                                  'sources': ['U9']}]})
        await self.service.maybe_run_batch(force=True)
        self.assertFalse(self.diary_path(datetime.now().date().isoformat()).exists())

    async def test_app_computed_fields_are_dropped(self):
        today = datetime.now().date().isoformat()
        await self.service.enqueue_event(await self.message('a'))
        self.set_model({'days': [{'date': today,
                                  'fields': {'baby_age_years': 2, 'period_phase': '黄体期', 'mood': 7},
                                  'blocks': [], 'sources': ['U1']}]})
        await self.service.maybe_run_batch(force=True)
        content = self.diary_path(today).read_text(encoding='utf-8')
        self.assertNotIn('baby_age_years', content)
        self.assertNotIn('period_phase', content)
        self.assertIn('mood: 7', content)

    async def test_writing_the_same_fragment_twice_does_not_duplicate(self):
        today = datetime.now().date().isoformat()
        await self.service.enqueue_event(await self.message('a'))
        self.set_model({'days': [{'date': today,
                                  'blocks': [{'kind': '自由记录', 'text': '窗外又有小鸟来了'}],
                                  'sources': ['U1']}]})
        await self.service.maybe_run_batch(force=True)
        day = {'date': today, 'fields': {}, 'blocks': [{'kind': '自由记录', 'text': '窗外又有小鸟来了'}],
               'sources': ['U1'], 'date_inferred': False}
        await self.service._write_diary(day, {'U1': 'private:a'}, {'U1': time.time()})
        content = self.diary_path(today).read_text(encoding='utf-8')
        self.assertEqual(content.count('窗外又有小鸟来了'), 1)

    async def test_fragments_are_indexed_for_later_withdrawal(self):
        today = datetime.now().date().isoformat()
        await self.service.enqueue_event(await self.message('a'))
        self.set_model({'days': [{'date': today,
                                  'blocks': [{'kind': '自由记录', 'text': '今天心情不错'}],
                                  'sources': ['U1']}]})
        await self.service.maybe_run_batch(force=True)
        async with self.connect() as db:
            rows = await (await db.execute(
                'SELECT diary_date, field_key, value, source_ids FROM life_diary_fragments')).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], today)
        self.assertEqual(rows[0][1], 'block:自由记录')
        self.assertIn('private:a', rows[0][3])

    async def test_quiet_window_defers_the_batch_until_forced(self):
        await self.service.enqueue_event(await self.message('a'))
        self.assertFalse(await self.service.maybe_run_batch())
        self.assertTrue(await self.service.maybe_run_batch(force=True))

    async def test_snapshot_reports_path_and_pending_count(self):
        await self.service.enqueue_event(await self.message('a'))
        data = await self.service.snapshot()
        self.assertTrue(data['enabled'])
        self.assertEqual(data['pending'], 1)
        self.assertIn('DiaryInbox', data['path'])
        self.assertEqual(data['quiet_minutes'], 30)


if __name__ == '__main__':
    unittest.main()
