"""显式记忆录入（``[MEMORY:...]``）的分类与来源规则。

模型可以在指令里声明类型，例如 ``[MEMORY:长期重要|内容]``；没有声明时
按内容判定：只有确实需要长期记住的事实才进「长期重要」，其余进「日常」。

这样显式录入就不会再把普通日常一股脑塞进「长期重要」，
与自动总结（digest）的 ``important`` 门槛保持同一套语义。
"""

from __future__ import annotations

import re

# 允许模型在指令里声明的分类别名。映射到记忆库里的 memory_kind。
LONG_TERM_ALIASES = {
    "长期重要", "长期", "重要", "important", "long_term", "longterm", "important_memory",
}
DAILY_ALIASES = {
    "日常", "daily", "日常记忆", "digest", "summary",
}
FRIEND_ALIASES = {
    "朋友", "朋友记忆", "friend", "friends",
}

# 判定「值得长期记住」的内容特征。命中任一即视为长期事实。
_LONG_TERM_SIGNALS = (
    # 关系与人生重大变故
    "分手", "离婚", "去世", "逝世", "走了", "过世", "病逝", "确诊", "绝症",
    # 承诺与约定
    "答应", "承诺", "约定", "说定", "决定", "签下", "入职", "离职",
    "永远", "这辈子", "一辈子", "再也不", "不会再",
    # 健康与禁忌
    "过敏", "禁忌", "不能吃", "不能碰", "忌口",
    # 长期规则
    "规则", "规矩", "以后都", "从今天起",
    # 稳定的相处偏好与性格特征（跨会话仍然成立的事实）
    "喜欢真诚", "讨厌", "厌恶", "受不了", "不喜欢",
    "偏好", "性格", "习惯", "在意",
)

# 用户明确要求记住的措辞：这类内容无论内容本身多轻，都属于用户主动托付。
_USER_EXPLICIT_MARKERS = (
    "记住", "记下", "记一下", "备忘", "别忘", "不要忘", "忘不得",
)

_PREFIX_SPLIT = re.compile(r"^\s*([^|｜:：]{1,12})\s*[|｜]\s*(.+)$", re.DOTALL)


def parse_memory_instruction(raw: str) -> tuple[str, str]:
    """解析 ``[MEMORY:...]`` 内容，返回 ``(memory_kind, content)``。

    支持模型声明类型：``[MEMORY:长期重要|内容]``、``[MEMORY:daily|内容]``。
    未声明类型时按内容判定。无法判定时返回空字符串，交由调用方决定兜底分类。
    """
    text = str(raw or "").strip()
    if not text:
        return "", ""

    matched = _PREFIX_SPLIT.match(text)
    if matched:
        alias = matched.group(1).strip().lower()
        body = matched.group(2).strip()
        if body:
            for aliases, kind in (
                (LONG_TERM_ALIASES, "long_term"),
                (DAILY_ALIASES, "daily"),
                (FRIEND_ALIASES, "friend"),
            ):
                if alias in aliases:
                    return kind, body

    return "", text


def classify_memory_content(content: str, *, user_requested: bool = False) -> str:
    """按内容判定记忆分类，返回 ``daily`` / ``long_term``。

    ``user_requested`` 表示用户明确要求记住这件事——这类内容属于用户主动托付，
    无论多轻都按长期事实保存。
    """
    text = str(content or "").strip()
    if not text:
        return "daily"
    if user_requested:
        return "long_term"
    if any(signal in text for signal in _LONG_TERM_SIGNALS):
        return "long_term"
    return "daily"


def mentions_explicit_memory_request(text: str) -> bool:
    """判断用户是否明确要求「记住」某件事。"""
    content = str(text or "")
    return any(marker in content for marker in _USER_EXPLICIT_MARKERS)


def resolve_memory_kind(
    raw_content: str,
    *,
    user_text: str = "",
    fallback: str = "daily",
) -> tuple[str, str]:
    """显式录入的最终入口：返回 ``(memory_kind, content)``。

    顺序：模型声明的类型优先 → 用户明确要求记住 → 内容特征判定 → 兜底分类。
    """
    declared_kind, content = parse_memory_instruction(raw_content)
    if declared_kind:
        return declared_kind, content

    user_requested = mentions_explicit_memory_request(user_text)
    kind = classify_memory_content(content, user_requested=user_requested)
    return (kind or fallback), content
