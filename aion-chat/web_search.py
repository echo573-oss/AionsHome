from __future__ import annotations

import os
import re
from typing import Any

import httpx

from config import SETTINGS
from expressive_voice_markup import LEGACY_OPEN, OPEN, VOICE_SPAN


TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
WEB_SEARCH_CMD_PATTERN = re.compile(r"\[WEB_SEARCH\s*[：:]\s*([^\]]+)\]", re.IGNORECASE)
WEB_EXTRACT_CMD_PATTERN = re.compile(r"\[WEB_EXTRACT\s*[：:]\s*([^\]]+)\]", re.IGNORECASE)
# 搜索指令的选项段：[WEB_SEARCH:查询|选项]。沿用 [MEMORY_SEARCH:查询|选项] 的写法。
WEB_SEARCH_OPTION_SEPARATOR = re.compile(r"[|｜]")
_WEB_CMD_START_RE = re.compile(
    rf"(?P<voice>{OPEN}|{LEGACY_OPEN})|"
    r"(?:\[|［|【)\s*(?:(?:WEB_SEARCH|WEB_EXTRACT|MEMORY_SEARCH|SELFIE|DRAW)\s*[：:]|BAND_VIBRATE\s*:"
    r"|BAND_NOTE_(?:SINGLE|CALL)\s*[：:]|APP_(?:LOCK|TEMP_UNLOCK|UNLOCK)\s*:"
    r"|DEVICE_(?:LOCK|TEMP_UNLOCK)\s*:|DEVICE_UNLOCK"
    r"|(?:COME_HOME|LOOK_AT_ME)\s*:"
    r"|拍拍抱枕:|PAT\s*[：:]|小游戏\s*[：:])",
    re.IGNORECASE,
)
_MAX_QUERIES = 3
_MAX_EXTRACT_URLS = 3
# Tavily 支持的取值，模型给的非法值一律忽略。
_TAVILY_TOPICS = {"general", "news", "finance"}
_TAVILY_TIME_RANGES = {"day", "week", "month", "year"}
_TAVILY_SEARCH_DEPTHS = {"basic", "advanced", "fast", "ultra-fast"}


class TavilyError(RuntimeError):
    pass


def tavily_api_key() -> str:
    return (SETTINGS.get("tavily_api_key") or os.environ.get("TAVILY_API_KEY") or "").strip()


def is_web_search_available() -> bool:
    return bool(tavily_api_key())


def parse_web_search_command(raw: str) -> tuple[str, dict[str, Any]]:
    """拆出 [WEB_SEARCH:查询|选项] 里的查询文本和 Tavily 参数。

    选项沿用 [MEMORY_SEARCH:查询|选项] 的写法，用 | 分隔，键值对用 = 连接。
    非法或无法识别的选项直接丢弃，不影响查询本身。
    """
    text = (raw or "").strip()
    if not text:
        return "", {}
    parts = [part.strip() for part in WEB_SEARCH_OPTION_SEPARATOR.split(text)]
    query = parts[0]
    options: dict[str, Any] = {}
    for option in parts[1:]:
        if not option:
            continue
        key, _, value = option.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key == "fresh" and not value:
            # 简写：总是实时抓取，不走缓存副本。
            options["max_age_hours"] = 0
        elif key == "topic" and value.lower() in _TAVILY_TOPICS:
            options["topic"] = value.lower()
        elif key == "time_range" and value.lower() in _TAVILY_TIME_RANGES:
            options["time_range"] = value.lower()
        elif key == "max_age_hours":
            try:
                hours = int(value)
            except ValueError:
                continue
            if -1 <= hours <= 720:
                options["max_age_hours"] = hours
        elif key == "depth" and value.lower() in _TAVILY_SEARCH_DEPTHS:
            options["search_depth"] = value.lower()
    return query, options


def clean_web_command_text(text: str) -> str:
    cleaned = WEB_SEARCH_CMD_PATTERN.sub("", text or "")
    cleaned = WEB_EXTRACT_CMD_PATTERN.sub("", cleaned)
    return cleaned.strip()


def format_tavily_context(payload: dict, *, kind: str = "search", max_chars_per_item: int = 700) -> str:
    if not isinstance(payload, dict):
        return "【联网搜索结果】\n系统没有收到有效结果。"

    title = "联网搜索结果" if kind == "search" else "网页读取结果"
    lines = [f"【{title}】"]
    query = _clean_text(payload.get("query"))
    if query:
        lines.append(f"查询：{query}")

    answer = _clean_text(payload.get("answer"))
    if answer:
        lines.append(f"简短答案：{_truncate(answer, max_chars_per_item)}")

    results = payload.get("results")
    if not isinstance(results, list):
        results = []

    for idx, item in enumerate(results[:5], start=1):
        if not isinstance(item, dict):
            continue
        item_title = _clean_text(item.get("title")) or _clean_text(item.get("url")) or f"结果 {idx}"
        url = _clean_text(item.get("url"))
        date = _clean_text(item.get("published_date"))
        if kind == "extract":
            snippet = _clean_text(item.get("raw_content") or item.get("content"))
        else:
            snippet = _clean_text(item.get("content") or item.get("snippet") or item.get("description"))
        lines.append("")
        lines.append(f"{idx}. {item_title}")
        if url:
            lines.append(f"URL：{url}")
        if date:
            lines.append(f"发布时间：{date}")
        if snippet:
            lines.append(f"内容：{_truncate(snippet, max_chars_per_item)}")

    failed = payload.get("failed_results")
    if isinstance(failed, list) and failed:
        lines.append("")
        lines.append(f"未成功读取：{len(failed)} 个来源")

    usage = payload.get("usage")
    if isinstance(usage, dict) and usage.get("credits") is not None:
        lines.append("")
        lines.append(f"本次 Tavily credits：{usage.get('credits')}")

    return "\n".join(lines).strip()


def format_web_system_message(actor_name: str, searches: list[str], extracts: list[str]) -> str:
    parts: list[str] = []
    for raw in searches[:_MAX_QUERIES]:
        query, _ = parse_web_search_command(raw)
        if query.strip():
            parts.append(f"搜索「{query.strip()}」")
    for url in extracts[:_MAX_EXTRACT_URLS]:
        if url.strip():
            parts.append(f"读取网页「{url.strip()}」")
    action = "、".join(parts) if parts else "联网查询"
    return f"{actor_name}发起了联网搜索：{action}"


async def tavily_search(
    query: str,
    *,
    max_results: int = 5,
    timeout: float = 30.0,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    query = query.strip()
    if not query:
        raise TavilyError("搜索内容为空")
    key = tavily_api_key()
    if not key:
        raise TavilyError("未配置 Tavily API key")
    payload = {
        "query": query,
        # 让 Tavily 按查询意图自动配置 topic / time_range 等参数；显式传入的选项优先。
        "auto_parameters": True,
        "max_results": max(1, min(8, int(max_results or 5))),
        "include_answer": True,
        "include_raw_content": False,
        "include_images": False,
        "include_usage": True,
    }
    # search_depth 不写死：auto_parameters 会按查询意图自动选择，需要精确控制时由选项显式指定。
    payload.update(options or {})
    try:
        return await _post_tavily(TAVILY_SEARCH_URL, key, payload, timeout=timeout)
    except TavilyError as error:
        # 免费额度下 max_age_hours 的实时抓取可能因容量被拒（434）。退回缓存副本重试，
        # 保住这次搜索本身，而不是整条失败。
        if "max_age_hours" not in payload or "[434]" not in str(error):
            raise
        retry = {k: v for k, v in payload.items() if k != "max_age_hours"}
        return await _post_tavily(TAVILY_SEARCH_URL, key, retry, timeout=timeout)


async def tavily_extract(url: str, *, query: str = "", timeout: float = 30.0) -> dict[str, Any]:
    url = url.strip()
    if not url:
        raise TavilyError("网页地址为空")
    key = tavily_api_key()
    if not key:
        raise TavilyError("未配置 Tavily API key")
    payload: dict[str, Any] = {
        "urls": [url],
        "extract_depth": "basic",
        "format": "markdown",
        "include_images": False,
        "include_usage": True,
        "timeout": min(max(timeout, 1.0), 60.0),
    }
    if query.strip():
        payload["query"] = query.strip()
        payload["chunks_per_source"] = 3
    return await _post_tavily(TAVILY_EXTRACT_URL, key, payload, timeout=timeout + 5)


async def run_web_commands(searches: list[str], extracts: list[str]) -> list[str]:
    contexts: list[str] = []
    for raw in [item.strip() for item in searches if item.strip()][:_MAX_QUERIES]:
        query, options = parse_web_search_command(raw)
        if not query:
            continue
        payload = await tavily_search(query, options=options)
        contexts.append(format_tavily_context(payload, kind="search"))
    for url in [item.strip() for item in extracts if item.strip()][:_MAX_EXTRACT_URLS]:
        payload = await tavily_extract(url)
        contexts.append(format_tavily_context(payload, kind="extract", max_chars_per_item=1200))
    return contexts


class WebCommandStreamFilter:
    """Suppress hidden tool tags from streamed UI and TTS chunks."""

    def __init__(self):
        self._pending = ""
        self._in_command = False

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        buf = self._pending + chunk
        self._pending = ""
        out: list[str] = []

        while buf:
            if self._in_command:
                ends = [pos for pos in (buf.find("]"), buf.find("］"), buf.find("】")) if pos >= 0]
                end = min(ends) if ends else -1
                if end < 0:
                    return "".join(out)
                buf = buf[end + 1:]
                self._in_command = False
                continue

            match = _WEB_CMD_START_RE.search(buf)
            if match:
                out.append(buf[:match.start()])
                if match['voice']:
                    voice = VOICE_SPAN.match(buf, match.start())
                    if not (voice['close'] or voice['legacy_close']):
                        self._pending = buf[match.start():]
                        return "".join(out)
                    buf = buf[voice.end():]
                    continue
                buf = buf[match.end():]
                self._in_command = True
                continue

            keep = _possible_command_prefix_len(buf)
            if keep:
                out.append(buf[:-keep])
                self._pending = buf[-keep:]
            else:
                out.append(buf)
            break

        return "".join(out)

    def flush(self) -> str:
        pending = self._pending
        self._pending = ""
        if VOICE_SPAN.match(pending):
            return ""
        if self._in_command:
            self._in_command = False
            return ""
        return pending


async def _post_tavily(url: str, key: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code >= 400:
        detail = resp.text[:300]
        raise TavilyError(f"Tavily 请求失败 [{resp.status_code}]: {detail}")
    data = resp.json()
    if not isinstance(data, dict):
        raise TavilyError("Tavily 返回了非 JSON 对象")
    return data


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate(text: str, max_chars: int) -> str:
    max_chars = max(80, int(max_chars or 700))
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _possible_command_prefix_len(text: str) -> int:
    voice_prefix = re.search(r'[\[【［]语(?:音\s*)?$', text)
    if voice_prefix:
        return len(voice_prefix[0])
    # Keep an incomplete memory tag even when whitespace arrives in its own chunk.
    memory_prefix = re.search(r"[\[［【]\s*([A-Za-z_]*)\s*$", text)
    if memory_prefix and "MEMORY_SEARCH".startswith(memory_prefix.group(1).upper()):
        return len(text) - memory_prefix.start()
    memory_probes = tuple(
        f"{opening}{spacing}MEMORY_SEARCH{colon}"
        for opening in ("[", "［", "【")
        for spacing in ("", " ")
        for colon in (":", "：")
    )
    probes = (
        "[WEB_SEARCH:", "[WEB_SEARCH：", "[WEB_EXTRACT:", "[WEB_EXTRACT：",
        "[BAND_VIBRATE:", "[SELFIE:", "[DRAW:",
        "[BAND_NOTE_SINGLE:", "[BAND_NOTE_SINGLE：",
        "[BAND_NOTE_CALL:", "[BAND_NOTE_CALL：",
        "[APP_LOCK:", "[APP_TEMP_UNLOCK:", "[APP_UNLOCK:",
        "[DEVICE_LOCK:", "[DEVICE_TEMP_UNLOCK:", "[DEVICE_UNLOCK]",
        "[COME_HOME:", "[LOOK_AT_ME:",
        "[拍拍抱枕:", "[PAT:", "[PAT：", "[小游戏:", "[小游戏：",
    ) + memory_probes
    max_len = min(len(text), max(len(item) for item in probes) - 1)
    upper = text.upper()
    for size in range(max_len, 0, -1):
        suffix = upper[-size:]
        if any(probe.upper().startswith(suffix) for probe in probes):
            return size
    return 0
