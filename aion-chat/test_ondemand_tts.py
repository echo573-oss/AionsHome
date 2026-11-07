"""点播 TTS 的回归测试。

覆盖三件事：
1. 合成后的音频按消息 ID 落盘，朗读端能按 ID 找回
2. 分段音频（_s0/_s1）能按序号正确排列，供逐段播放
3. 消息 ID 会经过消毒，路径穿越（../）拿不到缓存目录外的文件
"""
import json
import tempfile
import unittest
from pathlib import Path

from theater_tts_cache import (
    _safe_message_id,
    list_message_audio_segments,
    message_id_from_audio_path,
)


class OnDemandTtsCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, payload=b'ID3'):
        path = self.cache / name
        path.write_bytes(payload)
        return path

    def test_message_id_sanitisation_blocks_path_traversal(self):
        self.assertEqual(_safe_message_id('abc-123_X'), 'abc-123_X')
        # 斜杠与点被剔除，不能借此跳出缓存目录
        self.assertEqual(_safe_message_id('../../etc/passwd'), 'etcpasswd')
        self.assertEqual(_safe_message_id('a/b'), 'ab')

    def test_segments_are_listed_in_numeric_order(self):
        # 文件名字面序会把 _s10 排在 _s2 前面，必须按数字排序
        self._write('msg1_s2.mp3')
        self._write('msg1_s10.mp3')
        self._write('msg1_s0.mp3')

        segments = list_message_audio_segments('msg1', self.cache)
        self.assertEqual([index for index, _ in segments], [0, 2, 10])
        self.assertEqual([path.name for _, path in segments],
                         ['msg1_s0.mp3', 'msg1_s2.mp3', 'msg1_s10.mp3'])

    def test_segments_of_other_messages_are_not_included(self):
        self._write('msg1_s0.mp3')
        self._write('msg2_s0.mp3')
        self._write('msg1x_s0.mp3')
        self._write('msg1_s0.txt')

        segments = list_message_audio_segments('msg1', self.cache)
        self.assertEqual([path.name for _, path in segments], ['msg1_s0.mp3'])

    def test_empty_or_invalid_message_id_yields_no_segments(self):
        self._write('msg1_s0.mp3')
        self.assertEqual(list_message_audio_segments('', self.cache), [])
        self.assertEqual(list_message_audio_segments('../..', self.cache), [])

    def test_audio_path_resolves_back_to_message_id(self):
        self.assertEqual(message_id_from_audio_path(Path('msg1_s3.mp3')), 'msg1')
        self.assertEqual(message_id_from_audio_path(Path('msg1.mp3')), 'msg1')
        # 非 mp3 不算音频
        self.assertIsNone(message_id_from_audio_path(Path('msg1_s0.wav')))
        # 空 stem 不返回消息 ID
        self.assertIsNone(message_id_from_audio_path(Path('.mp3')))


class OnDemandTtsRouteTests(unittest.TestCase):
    """点播音频的 HTTP 端点：按 ID 取缓存，读不到就 404。"""

    def setUp(self):
        from fastapi.testclient import TestClient
        from main import app
        self.client = TestClient(app)

    def test_unknown_message_returns_404(self):
        response = self.client.get('/api/tts/audio/does-not-exist-anywhere')
        self.assertEqual(response.status_code, 404)

    def test_invalid_message_id_is_rejected(self):
        response = self.client.get('/api/tts/audio/..%2F..%2Fsecret')
        self.assertIn(response.status_code, (400, 404))

    def test_segments_endpoint_rejects_invalid_message_id(self):
        response = self.client.get('/api/tts/segments/bad id!')
        self.assertEqual(response.status_code, 400)

    def test_segments_endpoint_returns_urls_for_cached_segments(self):
        from routes import settings as settings_routes

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / 'mseg_s0.mp3').write_bytes(b'ID3')
            (cache / 'mseg_s1.mp3').write_bytes(b'ID3')
            original = settings_routes.TTS_CACHE_DIR
            settings_routes.TTS_CACHE_DIR = cache
            try:
                response = self.client.get('/api/tts/segments/mseg')
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload['urls'],
                                 ['/api/tts/audio/mseg_s0', '/api/tts/audio/mseg_s1'])
            finally:
                settings_routes.TTS_CACHE_DIR = original


if __name__ == '__main__':
    unittest.main()
