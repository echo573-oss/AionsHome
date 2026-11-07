import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wechat_bridge import create_wechat_binding
from wechat_mode import find_wechat_mode_for_sender, set_wechat_mode
from wechat_mode_dispatcher import WeChatModeDispatcher
from ws import ConnectionManager


def enabled_private_mode_settings():
    settings = {
        "wechat_bridge_enabled": True,
        "wechat_bridge_transport": "openclaw",
    }
    create_wechat_binding(
        source_type="aion_private",
        source_id="conv-1",
        account_id="bot-1",
        wechat_user_id="peer-1",
        context_token="ctx",
        settings=settings,
        now=1,
    )
    set_wechat_mode(
        settings,
        account_id="bot-1",
        wechat_user_id="peer-1",
        inbound_route={"source_type": "aion_private", "source_id": "conv-1"},
        outbound_routes=[{"source_type": "aion_private", "source_id": "conv-1"}],
        enabled=True,
        now=100,
    )
    return settings


def enabled_group_bound_mode_settings():
    settings = {
        "wechat_bridge_enabled": True,
        "wechat_bridge_transport": "openclaw",
    }
    create_wechat_binding(
        source_type="chatroom",
        source_id="room-1",
        account_id="bot-1",
        wechat_user_id="peer-1",
        context_token="group-context",
        settings=settings,
        now=1,
    )
    set_wechat_mode(
        settings,
        account_id="bot-1",
        wechat_user_id="peer-1",
        inbound_route={"source_type": "chatroom", "source_id": "room-1"},
        outbound_routes=[
            {"source_type": "chatroom", "source_id": "room-1"},
            {"source_type": "aion_private", "source_id": "conv-1"},
        ],
        enabled=True,
        now=100,
    )
    return settings


def assistant_event(message_id="msg-1", conv_id="conv-1", content="第一条\n第二条"):
    return {
        "type": "msg_created",
        "data": {
            "id": message_id,
            "conv_id": conv_id,
            "role": "assistant",
            "content": content,
            "attachments": [],
        },
    }


async def private_identity(_source_type, _source_id, _sender):
    return "私聊", "Companion"


class WeChatModeDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_rebinding_group_moves_mirroring_and_keeps_private_delivery(self):
        settings = enabled_group_bound_mode_settings()
        create_wechat_binding(
            source_type="chatroom", source_id="room-new",
            account_id="bot-1", wechat_user_id="peer-1",
            context_token="new-context", settings=settings, now=200,
        )
        sent = []
        dispatcher = WeChatModeDispatcher(
            settings=settings,
            send_text=lambda **kwargs: sent.append(kwargs),
            identity_resolver=private_identity,
        )
        for room_id in ("room-1", "room-new"):
            for sender in ("aion", "connor"):
                await dispatcher.process_event({
                    "type": "chatroom_msg_created",
                    "data": {"id": f"{room_id}-{sender}", "room_id": room_id,
                             "sender": sender, "content": f"{room_id}-{sender}"},
                })
        await dispatcher.process_event(assistant_event(content="private"))

        self.assertEqual([item["content"] for item in sent], [
            "Companion：room-new-aion", "Companion：room-new-connor", "Companion：private",
        ])
        self.assertTrue(all(item["context_token"] == "new-context" for item in sent))
        mode = find_wechat_mode_for_sender(settings, "bot-1", "peer-1")
        self.assertEqual(mode["inbound_route"]["source_id"], "room-new")
        self.assertEqual(mode["enabled_at"], 100)

    async def test_private_bubbles_send_sequentially(self):
        sent = []

        async def send_text(**kwargs):
            sent.append(kwargs["content"])
            return {"ok": True}

        dispatcher = WeChatModeDispatcher(
            settings=enabled_private_mode_settings(),
            send_text=send_text,
            identity_resolver=private_identity,
        )

        await dispatcher.process_event({**assistant_event(), "data": {**assistant_event()["data"], "content": "第一条", "attachments": [{"type": "file", "name": "第二条"}]}})

        self.assertEqual(sent, [
            "Companion：第一条",
            "Companion：📎 文件：第二条",
        ])

    async def test_unsubscribed_and_non_ai_events_do_not_send(self):
        sent = []

        async def send_text(**kwargs):
            sent.append(kwargs["content"])
            return {"ok": True}

        dispatcher = WeChatModeDispatcher(
            settings=enabled_private_mode_settings(),
            send_text=send_text,
            identity_resolver=private_identity,
        )
        await dispatcher.process_event({
            "type": "msg_created",
            "data": {
                "id": "user-1",
                "conv_id": "conv-1",
                "role": "user",
                "content": "用户消息",
                "attachments": [],
            },
        })
        await dispatcher.process_event(assistant_event(
            message_id="msg-other",
            conv_id="conv-other",
            content="其他会话",
        ))

        self.assertEqual(sent, [])

    async def test_private_reply_uses_existing_group_binding_without_changing_inbound_route(self):
        sent = []
        settings = enabled_group_bound_mode_settings()

        async def send_text(**kwargs):
            sent.append(kwargs)
            return {"ok": True}

        dispatcher = WeChatModeDispatcher(
            settings=settings,
            send_text=send_text,
            identity_resolver=private_identity,
        )

        await dispatcher.process_event(assistant_event(content="私聊主动消息"))

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["context_token"], "group-context")
        mode = next(iter(settings["wechat_mode_sessions"].values()))
        self.assertEqual(mode["inbound_route"], {
            "source_type": "chatroom",
            "source_id": "room-1",
        })

    async def test_chatroom_message_from_second_ai_is_mirrored(self):
        sent = []

        async def send_text(**kwargs):
            sent.append(kwargs["content"])
            return {"ok": True}

        async def group_identity(_source_type, _source_id, _sender):
            return "家庭群", "另一位 AI"

        dispatcher = WeChatModeDispatcher(
            settings=enabled_group_bound_mode_settings(),
            send_text=send_text,
            identity_resolver=group_identity,
        )

        await dispatcher.process_event({
            "type": "chatroom_msg_created",
            "data": {
                "id": "group-msg-1",
                "room_id": "room-1",
                "sender": "connor",
                "content": "群聊回复",
                "attachments": [],
            },
        })

        self.assertEqual(sent, ["另一位 AI：群聊回复"])

    async def test_duplicate_source_message_is_sent_once(self):
        sent = []

        async def send_text(**kwargs):
            sent.append(kwargs["content"])
            return {"ok": True}

        dispatcher = WeChatModeDispatcher(
            settings=enabled_private_mode_settings(),
            send_text=send_text,
            identity_resolver=private_identity,
        )
        event = assistant_event(message_id="msg-dup", content="只发一次")

        await dispatcher.process_event(event)
        await dispatcher.process_event(event)

        self.assertEqual(sent, ["Companion：只发一次"])

    async def test_retry_resumes_at_failed_bubble_without_resending_previous_bubble(self):
        attempts = []
        second_attempts = 0

        async def send_text(**kwargs):
            nonlocal second_attempts
            content = kwargs["content"]
            attempts.append(content)
            if content == "Companion：📎 文件：第二条" and second_attempts == 0:
                second_attempts += 1
                raise RuntimeError("temporary")
            return {"ok": True}

        dispatcher = WeChatModeDispatcher(
            settings=enabled_private_mode_settings(),
            send_text=send_text,
            identity_resolver=private_identity,
            sleep=lambda _seconds: asyncio.sleep(0),
        )

        await dispatcher.process_event({**assistant_event(message_id="msg-retry"), "data": {**assistant_event(message_id="msg-retry")["data"], "content": "第一条", "attachments": [{"type": "file", "name": "第二条"}]}})

        self.assertEqual(attempts, [
            "Companion：第一条",
            "Companion：📎 文件：第二条",
            "Companion：📎 文件：第二条",
        ])

    def test_offer_returns_false_when_queue_is_full_without_raising(self):
        dispatcher = WeChatModeDispatcher(settings={}, queue_size=1)

        self.assertTrue(dispatcher.offer({"type": "msg_created", "data": {"id": "one"}}))
        self.assertFalse(dispatcher.offer({"type": "msg_created", "data": {"id": "two"}}))

    async def test_dispatcher_start_and_stop_leave_no_worker(self):
        dispatcher = WeChatModeDispatcher(settings={})

        dispatcher.start()

        self.assertIsNotNone(dispatcher._task)
        self.assertFalse(dispatcher._task.done())
        await dispatcher.stop()
        self.assertIsNone(dispatcher._task)

    async def test_stop_drains_an_already_queued_message(self):
        sent = []

        async def send_text(**kwargs):
            sent.append(kwargs["content"])
            return {"ok": True}

        dispatcher = WeChatModeDispatcher(
            settings=enabled_private_mode_settings(),
            send_text=send_text,
            identity_resolver=private_identity,
        )
        dispatcher.start()
        dispatcher.offer(assistant_event(content="关机前送达"))

        await dispatcher.stop()

        self.assertEqual(sent, ["Companion：关机前送达"])


class RecordingWebSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        self.messages.append(text)


class WeChatModeBroadcastIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ws_broadcast_continues_when_wechat_offer_raises(self):
        manager = ConnectionManager()
        socket = RecordingWebSocket()
        manager.active.append(socket)
        event = {
            "type": "msg_created",
            "sync_seq": 1,
            "data": {
                "id": "msg-1",
                "conv_id": "conv-1",
                "role": "assistant",
                "content": "正文",
            },
        }

        with patch("ws._offer_wechat_mode_event", side_effect=RuntimeError("wechat down")) as offer:
            await manager.broadcast(event)

        self.assertGreaterEqual(offer.call_count, 1)
        self.assertGreaterEqual(len(socket.messages), 1)
        self.assertIn("正文", socket.messages[0])


if __name__ == "__main__":
    unittest.main()
