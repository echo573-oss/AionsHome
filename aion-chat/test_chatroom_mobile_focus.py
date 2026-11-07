import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class ChatroomMobileFocusTests(unittest.TestCase):
    def test_post_send_focus_is_guarded_on_touch_devices(self):
        js = (ROOT / "static" / "chatroom.js").read_text(encoding="utf-8")
        html = (ROOT / "static" / "chatroom.html").read_text(encoding="utf-8")

        self.assertIn("function crShouldAutoFocusComposer()", js)
        self.assertIn("matchMedia('(pointer: coarse)')", js)
        self.assertIn("navigator.maxTouchPoints", js)
        self.assertIn("function crRefocusComposerAfterSend()", js)
        self.assertIn("chatroom.js?v=20261104-image-retry", html)

        voice_call_send = re.search(
            r"window\.ChatroomVoiceCallAdapter = \{.*?async sendText\(text\).*?\n  \}\n\};",
            js,
            re.S,
        )
        self.assertIsNotNone(voice_call_send)
        self.assertNotIn("inputEl?.focus()", voice_call_send.group(0))
        self.assertIn("crRefocusComposerAfterSend();", voice_call_send.group(0))

        composer_submit = re.search(
            r"composer\.addEventListener\('submit', async \(e\) => \{.*?\n\}\);",
            js,
            re.S,
        )
        self.assertIsNotNone(composer_submit)
        self.assertNotIn("inputEl.focus();", composer_submit.group(0))
        self.assertIn("crRefocusComposerAfterSend();", composer_submit.group(0))

        voice_send = re.search(r"async function _crVoiceSend\([^)]*\).*?\n\}", js, re.S)
        self.assertIsNotNone(voice_send)
        self.assertNotIn("inputEl.focus();", voice_send.group(0))
        self.assertIn("crRefocusComposerAfterSend();", voice_send.group(0))


if __name__ == "__main__":
    unittest.main()
