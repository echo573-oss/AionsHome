import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class ChatBubbleStyleParityTests(unittest.TestCase):
    def _css_block(self, css, marker, start=0):
        marker_pos = css.index(marker, start)
        open_pos = css.index("{", marker_pos)
        depth = 0
        for i in range(open_pos, len(css)):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    return css[open_pos + 1:i]
        self.fail(f"CSS block for {marker!r} was not closed")

    def _decls(self, block):
        decls = {}
        for raw in block.split(";"):
            if ":" not in raw:
                continue
            key, value = raw.split(":", 1)
            decls[key.strip()] = value.strip()
        return decls

    def _chat_bubble_blocks(self, chat_css):
        theme_start = chat_css.index(".chat-area::before")
        mobile_start = chat_css.index("@media (max-width: 768px)")
        themed_mobile_start = chat_css.index("@media (max-width: 768px)", theme_start)
        narrow_start = chat_css.index("@media (max-width: 430px)")
        return [
            self._css_block(chat_css, ".msg-bubble {"),
            self._css_block(chat_css, ".msg-bubble {", mobile_start),
            self._css_block(chat_css, ".msg-bubble {", theme_start),
            self._css_block(chat_css, ".msg-bubble {", themed_mobile_start),
            self._css_block(chat_css, ".msg-bubble {", narrow_start),
        ]

    def test_main_chat_bubble_size_matches_chatroom(self):
        root = ROOT / "static"
        chat_css = (root / "chat.css").read_text(encoding="utf-8")
        chatroom_css = (root / "chatroom.css").read_text(encoding="utf-8")

        chatroom_bubble = self._decls(self._css_block(chatroom_css, ".bubble {"))
        expected = {
            "padding": chatroom_bubble["padding"],
            "border-radius": chatroom_bubble["border-radius"],
            "font-size": chatroom_bubble["font-size"],
            "line-height": chatroom_bubble["line-height"],
        }

        for block in self._chat_bubble_blocks(chat_css):
            bubble = self._decls(block)
            for key, value in expected.items():
                if key == "font-size" and value == "var(--chatroom-message-font-size)": value = "13.5px"
                self.assertEqual(
                    bubble.get(key),
                    value,
                    f"{key} should stay aligned with chatroom bubble sizing",
                )

    def test_main_chat_bubble_tail_radius_matches_chatroom(self):
        root = ROOT / "static"
        chat_css = (root / "chat.css").read_text(encoding="utf-8")
        chatroom_css = (root / "chatroom.css").read_text(encoding="utf-8")
        theme_start = chat_css.index(".chat-area::before")
        themed_mobile_start = chat_css.index("@media (max-width: 768px)", theme_start)

        chatroom_user = self._decls(
            self._css_block(chatroom_css, ".message-row.user .msg-content .bubble")
        )
        chatroom_aion = self._decls(
            self._css_block(chatroom_css, ".message-row.aion .msg-content .bubble")
        )

        user_blocks = [
            self._css_block(chat_css, ".msg-row.user .msg-bubble"),
            self._css_block(chat_css, ".msg-row.user .msg-bubble", theme_start),
            self._css_block(chat_css, ".msg-row.user .msg-bubble", themed_mobile_start),
        ]
        assistant_blocks = [
            self._css_block(chat_css, ".msg-row.assistant .msg-bubble"),
            self._css_block(chat_css, ".msg-row.assistant .msg-bubble", theme_start),
            self._css_block(chat_css, ".msg-row.assistant .msg-bubble", themed_mobile_start),
        ]

        for block in user_blocks:
            self.assertEqual(
                self._decls(block).get("border-bottom-right-radius"),
                chatroom_user["border-bottom-right-radius"],
            )
        for block in assistant_blocks:
            self.assertEqual(
                self._decls(block).get("border-bottom-left-radius"),
                chatroom_aion["border-bottom-left-radius"],
            )

    def test_main_chat_assistant_bubble_colors_match_chatroom_aion(self):
        root = ROOT / "static"
        chat_css = (root / "chat.css").read_text(encoding="utf-8")
        chatroom_css = (root / "chatroom.css").read_text(encoding="utf-8")

        chat_root = self._decls(self._css_block(chat_css, ":root"))
        chatroom_root = self._decls(self._css_block(chatroom_css, ":root"))
        self.assertEqual(chat_root["--ai-bg"], chatroom_root["--aion-bg"])

        chat_base_assistant = self._decls(
            self._css_block(chat_css, ".msg-row.assistant .msg-bubble")
        )
        self.assertEqual(chat_base_assistant.get("background"), "var(--ai-bg)")

        chat_theme_start = chat_css.index(".chat-area::before")
        chatroom_theme_start = chatroom_css.index(".bubble {\n  position: relative")
        chat_assistant = self._decls(
            self._css_block(chat_css, ".msg-row.assistant .msg-bubble", chat_theme_start)
        )
        chatroom_aion = self._decls(
            self._css_block(
                chatroom_css,
                ".message-row.aion .msg-content .bubble",
                chatroom_theme_start,
            )
        )

        for key in ("background", "border-color", "box-shadow"):
            self.assertEqual(chat_assistant.get(key), chatroom_aion[key])

        chat_light_assistant = self._decls(
            self._css_block(
                chat_css,
                'body[data-theme="light"] .msg-row.assistant .msg-bubble',
            )
        )
        chatroom_light_aion = self._decls(
            self._css_block(
                chatroom_css,
                'body[data-theme="light"] .message-row.aion .msg-content .bubble',
            )
        )

        for key in ("background", "box-shadow"):
            self.assertEqual(chat_light_assistant.get(key), chatroom_light_aion[key])


if __name__ == "__main__":
    unittest.main()
