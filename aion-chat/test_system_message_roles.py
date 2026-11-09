"""Check persona roles at context and provider boundaries without live model calls."""
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import ai_providers
import chatroom
from context_blocks import (
    CTX_ABILITY,
    CTX_PERSONA,
    CTX_RULES,
    build_persona_prefix,
    context_message,
)
from test_relay_provider_errors import FakeStreamResponse, fake_client_factory


WORLD = {
    'ai_name': 'Sol', 'user_name': 'Mira',
    'ai_persona': 'AI persona', 'user_persona': 'User profile',
    'system_prompt': 'Creative rules',
}

# 这些话术是旧版伪对话注入留下的痕迹，目标路径里不该再出现
LEGACY_ACK_PHRASES = (
    '收到，我会',
    '好的，需要时我会使用这些指令',
    '明白了，我会',
    '收到，我会在合适的时候自然提及',
    '收到，我了解当前的游戏状况了',
    '收到，我参考这些记忆',
    '收到，我了解最近的对话内容了',
    '收到，我只延续这场独立约会',
)


class SystemMessageRolesTest(unittest.IsolatedAsyncioTestCase):
    def test_persona_prefix_helper_builds_system_messages_in_fixed_order(self):
        messages = build_persona_prefix(
            WORLD,
            persona_title='[系统设定 - Sol人设]',
            user_title='[系统设定 - Mira信息]',
            codex_instructions='本地指令',
        )
        self.assertEqual([m['role'] for m in messages], ['system'] * 4)
        self.assertEqual(messages[0]['_ctx'], CTX_RULES)    # 本地个性化指令
        self.assertEqual(messages[1]['_ctx'], CTX_PERSONA)  # 人设
        self.assertEqual(messages[2]['_ctx'], CTX_PERSONA)  # 用户信息
        self.assertEqual(messages[3]['_ctx'], CTX_RULES)    # 系统提示
        self.assertIn('本地指令', messages[0]['content'])
        self.assertIn(WORLD['ai_persona'], messages[1]['content'])
        self.assertIn(WORLD['user_persona'], messages[2]['content'])
        self.assertIn(WORLD['system_prompt'], messages[3]['content'])

    def test_persona_prefix_respects_switches(self):
        # 系统提示关掉后不下发
        messages = build_persona_prefix(
            {**WORLD, 'system_prompt_enabled': False},
            persona_title='[系统设定 - Sol人设]',
            user_title='[系统设定 - Mira信息]',
        )
        self.assertEqual(len(messages), 2)
        # 人设关掉后不下发
        messages = build_persona_prefix(
            WORLD,
            persona_title='[系统设定 - Sol人设]',
            user_title='[系统设定 - Mira信息]',
            include_ai_persona=False,
        )
        self.assertEqual(len(messages), 2)
        self.assertNotIn(WORLD['ai_persona'], messages[0]['content'])

    def test_persona_prefix_can_supply_an_explicit_persona(self):
        messages = build_persona_prefix(
            WORLD,
            persona_title='',
            user_title='[系统设定 - Mira信息]',
            persona_text='Companion persona',
            include_ai_persona=False,
        )
        self.assertEqual(messages[0]['content'], 'Companion persona')
        self.assertEqual(messages[0]['_ctx'], CTX_PERSONA)

    def test_routes_use_the_shared_prefix_helper_instead_of_inline_copies(self):
        for filename in ('routes/chat.py', 'routes/chatroom.py', 'chatroom.py'):
            text = (Path(__file__).parent / filename).read_text(encoding='utf-8')
            with self.subTest(filename=filename):
                self.assertIn('build_persona_prefix(', text)
                self.assertNotIn('"role": "system", "content": f"[系统设定', text)

    def test_target_modules_have_no_legacy_acknowledgement_phrases(self):
        for filename in (
            'routes/chat.py', 'chatroom.py', 'routes/chatroom.py',
            'routes/playground.py', 'camera.py', 'fund.py', 'location.py',
            'schedule.py', 'routes/moments.py', 'routes/theater.py',
            'routes/date_theater.py', 'autonomy.py', 'lounge_actor_context.py',
            'context_builder.py',
        ):
            text = (Path(__file__).parent / filename).read_text(encoding='utf-8')
            for phrase in LEGACY_ACK_PHRASES:
                with self.subTest(filename=filename, phrase=phrase):
                    self.assertNotIn(phrase, text)

    async def test_group_and_companion_private_personas_are_system_messages(self):
        with (
            patch.object(chatroom, 'load_worldbook', return_value=WORLD),
            patch.object(chatroom, 'get_chatroom_names', return_value=('Mira', 'Sol', 'Nova')),
            patch.object(chatroom, '_read_connor_persona', return_value='Companion persona'),
            patch.object(chatroom, 'build_context_segments', AsyncMock(return_value=[])),
            patch.object(chatroom, 'append_intimacy_prompt'),
            patch.object(chatroom, 'with_current_device_context', side_effect=lambda messages, **kw: messages),
            patch.object(chatroom, 'fetch_merged_timeline', AsyncMock(return_value=[])),
            patch.object(chatroom, 'build_memory_blocks', AsyncMock(return_value={
                'time_block': 'time', 'memory_block': '', 'digest_result': {},
            })),
        ):
            for builder in (chatroom.build_aion_group_context, chatroom.build_connor_group_context,
                            chatroom.build_connor_1v1_context):
                messages, _ = await builder('room', [])
                persona = 'AI persona' if builder == chatroom.build_aion_group_context else 'Companion persona'
                expected = [persona, 'User profile']
                if builder == chatroom.build_aion_group_context:
                    expected.append('Creative rules')
                with self.subTest(builder=builder.__name__):
                    for index, text in enumerate(expected):
                        self.assertEqual(messages[index]['role'], 'system')
                        self.assertIn(text, messages[index]['content'])
                    self.assertFalse(any(m['content'].startswith(('收到，我会按照设定', '收到，我会记住',
                                                                  '收到，我会遵循')) for m in messages))

    async def test_gemini_separates_system_instructions_from_conversation(self):
        messages = [{'role': 'system', 'content': 'persona'}, {'role': 'system', 'content': 'rules'},
                    {'role': 'user', 'content': 'hello'}, {'role': 'assistant', 'content': 'hi'},
                    {'role': 'user', 'content': 'continue'}]
        factory = fake_client_factory(FakeStreamResponse())
        with patch('ai_providers.httpx.AsyncClient', new=factory), patch('ai_providers.get_key', return_value='test'):
            _ = [chunk async for chunk in ai_providers.call_gemini.__wrapped__(messages, 'test')]
        payload = factory.clients[0].calls[0][1]['json']
        self.assertEqual(payload.get('systemInstruction'), {'parts': [{'text': 'persona'}, {'text': 'rules'}]})
        self.assertEqual([m['role'] for m in payload['contents']], ['user', 'model', 'user'])
        self.assertEqual(ai_providers.build_multimodal_messages(messages), messages)

    def test_cli_keeps_system_prefix_and_marked_context_together(self):
        prompt = ai_providers._build_cli_prompt([
            {'role': 'system', 'content': 'persona'},
            {'role': 'system', 'content': 'rules'},
            context_message(CTX_ABILITY, 'abilities'),
            {'role': 'user', 'content': 'hello'},
        ])
        self.assertIn('[System Instruction]\npersona\n\nrules\n\nabilities', prompt)
        self.assertIn('[User]\nhello', prompt)

    async def test_companion_persona_is_added_even_with_other_system_messages(self):
        captured = []

        async def provider(messages, *args):
            captured.extend(messages)
            yield 'reply'

        with (patch.object(chatroom, '_read_connor_persona', return_value='Companion persona'),
              patch.object(chatroom, 'call_codex_cli', new=provider),
              patch.object(chatroom, 'with_current_device_context', side_effect=lambda messages: messages)):
            for initial in ([{'role': 'system', 'content': 'User profile'}],
                            [{'role': 'system', 'content': '[角色设定]\nCompanion persona'}]):
                captured.clear()
                _ = [chunk async for chunk in chatroom.stream_connor_cli(messages=initial)]
                self.assertEqual(sum('Companion persona' in m['content'] for m in captured), 1)


if __name__ == '__main__':
    unittest.main()
