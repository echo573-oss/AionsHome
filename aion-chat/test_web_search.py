import unittest
from unittest.mock import patch

from web_search import (
    TavilyError,
    WEB_EXTRACT_CMD_PATTERN,
    WEB_SEARCH_CMD_PATTERN,
    WebCommandStreamFilter,
    clean_web_command_text,
    format_tavily_context,
    format_web_system_message,
    parse_web_search_command,
    run_web_commands,
)


class WebSearchCommandTests(unittest.TestCase):
    def test_clean_web_commands_removes_search_and_extract_tags(self):
        text = "我去查一下[WEB_SEARCH: Tavily API pricing][WEB_EXTRACT:https://example.com/a]等我。"

        self.assertEqual(clean_web_command_text(text), "我去查一下等我。")

    def test_command_patterns_accept_full_width_colons(self):
        self.assertEqual(WEB_SEARCH_CMD_PATTERN.findall("[WEB_SEARCH：OpenAI news]"), ["OpenAI news"])
        self.assertEqual(WEB_EXTRACT_CMD_PATTERN.findall("[WEB_EXTRACT：https://example.com]"), ["https://example.com"])


class WebSearchOptionParsingTests(unittest.TestCase):
    def test_plain_query_has_no_options(self):
        self.assertEqual(parse_web_search_command("OpenAI news"), ("OpenAI news", {}))

    def test_topic_and_time_range_are_parsed(self):
        query, options = parse_web_search_command("AI新闻|topic=news|time_range=day")

        self.assertEqual(query, "AI新闻")
        self.assertEqual(options, {"topic": "news", "time_range": "day"})

    def test_fresh_shorthand_forces_live_fetch(self):
        query, options = parse_web_search_command("今天北京天气|fresh")

        self.assertEqual(query, "今天北京天气")
        self.assertEqual(options, {"max_age_hours": 0})

    def test_explicit_max_age_hours_is_kept(self):
        _, options = parse_web_search_command("iPhone 价格|max_age_hours=6")

        self.assertEqual(options, {"max_age_hours": 6})

    def test_explicit_depth_overrides_auto_selection(self):
        _, options = parse_web_search_command("小众资料|depth=advanced")

        self.assertEqual(options, {"search_depth": "advanced"})

    def test_invalid_option_values_are_dropped_without_breaking_query(self):
        query, options = parse_web_search_command("某查询|topic=不存在|time_range=世纪|max_age_hours=abc")

        self.assertEqual(query, "某查询")
        self.assertEqual(options, {})

    def test_full_width_separator_is_accepted(self):
        query, options = parse_web_search_command("AI新闻｜topic=news")

        self.assertEqual(query, "AI新闻")
        self.assertEqual(options, {"topic": "news"})

    def test_system_message_hides_option_syntax_from_users(self):
        message = format_web_system_message("小岚", ["AI新闻|topic=news"], [])

        self.assertIn("搜索「AI新闻」", message)
        self.assertNotIn("topic=news", message)


class TavilySearchRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_request_enables_auto_parameters_and_answer(self):
        captured = {}

        async def fake_post(url, key, payload, *, timeout):
            captured.update(payload)
            return {"query": payload["query"], "results": []}

        with patch("web_search.tavily_api_key", return_value="tvly-test"), \
             patch("web_search._post_tavily", new=fake_post):
            await run_web_commands(["AI新闻"], [])

        self.assertTrue(captured["auto_parameters"])
        self.assertTrue(captured["include_answer"])
        self.assertFalse(captured["include_raw_content"])
        self.assertTrue(captured["include_usage"])
        # search_depth 不写死，交给 auto_parameters 按查询意图判断。
        self.assertNotIn("search_depth", captured)

    async def test_options_are_forwarded_to_the_request(self):
        captured = {}

        async def fake_post(url, key, payload, *, timeout):
            captured.update(payload)
            return {"query": payload["query"], "results": []}

        with patch("web_search.tavily_api_key", return_value="tvly-test"), \
             patch("web_search._post_tavily", new=fake_post):
            await run_web_commands(["AI新闻|topic=news|time_range=day"], [])

        self.assertEqual(captured["topic"], "news")
        self.assertEqual(captured["time_range"], "day")

    async def test_capacity_rejection_retries_without_max_age_hours(self):
        calls = []

        async def fake_post(url, key, payload, *, timeout):
            calls.append(dict(payload))
            if "max_age_hours" in payload:
                raise TavilyError("Tavily 请求失败 [434]: 容量已满")
            return {"query": payload["query"], "results": []}

        with patch("web_search.tavily_api_key", return_value="tvly-test"), \
             patch("web_search._post_tavily", new=fake_post):
            await run_web_commands(["今天天气|fresh"], [])

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["max_age_hours"], 0)
        self.assertNotIn("max_age_hours", calls[1])

    async def test_other_errors_are_not_retried(self):
        calls = []

        async def fake_post(url, key, payload, *, timeout):
            calls.append(dict(payload))
            raise TavilyError("Tavily 请求失败 [401]: 无效 key")

        with patch("web_search.tavily_api_key", return_value="tvly-test"), \
             patch("web_search._post_tavily", new=fake_post):
            with self.assertRaises(Exception):
                await run_web_commands(["今天天气|fresh"], [])

        self.assertEqual(len(calls), 1)

    async def test_options_survive_the_route_extraction_step(self):
        captured = []

        async def fake_post(url, key, payload, *, timeout):
            captured.append(dict(payload))
            return {"query": payload["query"], "results": []}

        text = "我查一下。[WEB_SEARCH:今天北京天气|fresh] 顺便 [WEB_SEARCH:AI新闻|topic=news|time_range=day]"
        matches = WEB_SEARCH_CMD_PATTERN.findall(text)

        with patch("web_search.tavily_api_key", return_value="tvly-test"), \
             patch("web_search._post_tavily", new=fake_post):
            await run_web_commands(matches, [])

        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0]["query"], "今天北京天气")
        self.assertEqual(captured[0]["max_age_hours"], 0)
        self.assertEqual(captured[1]["query"], "AI新闻")
        self.assertEqual(captured[1]["topic"], "news")
        self.assertEqual(captured[1]["time_range"], "day")

    async def test_option_syntax_never_leaks_into_visible_text(self):
        stream_filter = WebCommandStreamFilter()

        visible = stream_filter.feed("我去查一下[WEB_SEARCH:今天北京天气|fresh]等我。")
        visible += stream_filter.flush()

        self.assertEqual(visible, "我去查一下等我。")
        self.assertEqual(
            clean_web_command_text("我去查一下[WEB_SEARCH:今天北京天气|fresh]等我。"),
            "我去查一下等我。",
        )


class TavilyContextFormattingTests(unittest.TestCase):
    def test_format_search_context_keeps_clean_short_sources(self):
        payload = {
            "query": "OpenAI web search",
            "answer": "A short answer from Tavily should be included when present.",
            "results": [
                {
                    "title": "OpenAI Web Search",
                    "url": "https://developers.openai.com/api/docs/guides/tools-web-search",
                    "content": "Useful summary " * 80,
                    "raw_content": "raw page content should not be included",
                    "published_date": "2026-07-01",
                    "score": 0.92,
                }
            ],
        }

        text = format_tavily_context(payload, kind="search", max_chars_per_item=180)

        self.assertIn("【联网搜索结果】", text)
        self.assertIn("查询：OpenAI web search", text)
        self.assertIn("1. OpenAI Web Search", text)
        self.assertIn("https://developers.openai.com/api/docs/guides/tools-web-search", text)
        self.assertIn("发布时间：2026-07-01", text)
        self.assertNotIn("raw page content", text)
        self.assertLess(len(text), 900)


if __name__ == "__main__":
    unittest.main()
