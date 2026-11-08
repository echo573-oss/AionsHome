"""浏览器控制权协议测试（完全离线，虚拟浏览器；不碰 Chrome 或用户数据）。"""
import asyncio
import unittest

from browser_handoff import BrowserControlError, BrowserHandoff, _safe_url, _display_url


class FakeBrowser:
    def __init__(self):
        self.url = ""
        self.actions = []
        self.paused = False
        self.fail_open = False
        self.fail_resume = False
        self.navigating = None

    async def open(self, url):
        self.actions.append("open")
        if self.fail_open:
            raise RuntimeError("launch_failed")
        self.url = url

    async def navigate(self, url):
        self.actions.append("navigate")
        if self.navigating:
            self.navigating.set()
            await asyncio.sleep(0.02)
        if self.paused:
            raise AssertionError("browser used during human handoff")
        self.url = url

    async def observe(self):
        self.actions.append("observe")
        return {"url": self.url, "title": "测试页"}

    async def pause_agent(self):
        self.actions.append("pause")
        self.paused = True

    async def resume_agent(self):
        self.actions.append("resume")
        if self.fail_resume:
            raise RuntimeError("resume_failed")
        self.paused = False

    async def close(self):
        self.actions.append("close")


class BrowserHandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.driver = FakeBrowser()
        self.ctrl = BrowserHandoff(lambda: self.driver)
        self.where = ("aion", "private", "chat_1")

    async def begin(self):
        return await self.ctrl.begin(*self.where, "https://example.org/start")

    async def test_begin_and_watch_dont_steal_control(self):
        session = await self.begin()
        self.assertEqual(session["state"], "agent")
        seen = await self.ctrl.watch(session["session_id"], *self.where)
        self.assertEqual(seen["state"], "agent")
        self.assertEqual(self.driver.actions, ["open", "observe", "observe"])
        await self.ctrl.finish(session["session_id"], *self.where)

    async def test_human_takeover_and_explicit_return(self):
        session = await self.begin()
        ident = session["session_id"]
        handed = await self.ctrl.take_control(ident, *self.where)
        self.assertEqual(handed["state"], "human")
        self.assertTrue(self.driver.paused)
        with self.assertRaises(BrowserControlError):
            await self.ctrl.navigate(ident, *self.where, "https://example.org/new")
        observed = await self.ctrl.watch(ident, *self.where)
        self.assertEqual(observed["state"], "human")
        # 模拟人在浏览器里换页；返回后记录新页面，不重放以前的点击。
        self.driver.url = "https://example.org/after-human"
        resumed = await self.ctrl.return_control(ident, *self.where)
        self.assertEqual(resumed["state"], "agent")
        self.assertEqual(resumed["url"], "https://example.org")
        self.assertFalse(self.driver.paused)
        self.assertEqual(self.driver.actions.count("navigate"), 0)
        await self.ctrl.navigate(ident, *self.where, "https://example.org/next")
        self.assertEqual(self.driver.actions.count("navigate"), 1)

    async def test_failed_return_keeps_human_control(self):
        session = await self.begin()
        ident = session["session_id"]
        await self.ctrl.take_control(ident, *self.where)
        self.driver.fail_resume = True
        with self.assertRaises(RuntimeError):
            await self.ctrl.return_control(ident, *self.where)
        seen = await self.ctrl.watch(ident, *self.where)
        self.assertEqual(seen["state"], "human")
        with self.assertRaises(BrowserControlError):
            await self.ctrl.navigate(ident, *self.where, "https://example.org/again")

    async def test_second_session_rejected_and_close_releases_slot(self):
        session = await self.begin()
        with self.assertRaises(BrowserControlError):
            await self.ctrl.begin("connor", "chatroom", "room_2", "https://example.org")
        await self.ctrl.finish(session["session_id"], *self.where)
        second = await self.ctrl.begin("connor", "chatroom", "room_2", "https://example.org")
        self.assertEqual(second["actor"], "connor")
        await self.ctrl.finish(second["session_id"], "connor", "chatroom", "room_2")

    async def test_wrong_actor_or_room_cannot_watch_or_take(self):
        session = await self.begin()
        ident = session["session_id"]
        with self.assertRaises(BrowserControlError):
            await self.ctrl.watch(ident, "connor", "private", "chat_1")
        with self.assertRaises(BrowserControlError):
            await self.ctrl.take_control(ident, "aion", "private", "some_other_chat")
        self.assertEqual(self.driver.actions.count("pause"), 0)

    async def test_navigation_serialized_with_takeover(self):
        session = await self.begin()
        ident = session["session_id"]
        self.driver.navigating = asyncio.Event()
        task = asyncio.create_task(
            self.ctrl.navigate(ident, *self.where, "https://example.org/changed")
        )
        await asyncio.wait_for(self.driver.navigating.wait(), timeout=2)
        takeover = asyncio.create_task(self.ctrl.take_control(ident, *self.where))
        await asyncio.gather(task, takeover)
        self.assertTrue(self.driver.paused)
        self.assertLess(self.driver.actions.index("navigate"), self.driver.actions.index("pause"))

    async def test_launch_failure_closes_driver_and_slot(self):
        self.driver.fail_open = True
        with self.assertRaises(RuntimeError):
            await self.begin()
        self.assertIn("close", self.driver.actions)
        self.driver.fail_open = False
        session = await self.begin()
        self.assertEqual(session["state"], "agent")

    async def test_url_credentials_and_tracking_query_are_not_in_preview(self):
        session = await self.ctrl.begin(*self.where, "https://example.org/start?token=local-secret")
        self.assertEqual(session["url"], "https://example.org")
        self.assertNotIn("local-secret", str(session))
        await self.ctrl.finish(session["session_id"], *self.where)

    def test_first_phase_rejects_intranet_and_untrusted_schemes(self):
        for url in (
            "http://example.org", "file:///etc/passwd", "https://127.0.0.1/",
            "https://192.168.1.3/", "https://router.local/",
            "https://localhost/", "https://intranet", "https://user:pass@example.org/",
            "https://example.org:9443/", "javascript:alert(1)",
        ):
            with self.subTest(url=url):
                with self.assertRaises(BrowserControlError):
                    _safe_url(url)
        self.assertEqual(_display_url("https://example.org/a?token=abc#frag"),
                         "https://example.org")


if __name__ == "__main__":
    unittest.main()
