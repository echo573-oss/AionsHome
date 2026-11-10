"""跨写入通道的记忆去重登记。

同一段对话可能被两条通道各记一遍：模型在对话中主动发 ``[MEMORY:...]``，
之后自动 digest 又把这段对话总结成原子记忆。两者内容相近但分类不同，
在记忆库里表现为「日常」和「长期重要」里各有一条重复。

去重靠**来源消息 id 重叠**判定，不靠向量相似度：实测中文短文本的
embedding 区分度不足（不同事件也有 0.88，真重复只有 0.84），阈值无法区分。
来源 id 是结构性信号，命中即确定。

登记语义：某条消息已经被记忆覆盖，digest 再次取数时应跳过它。
"""

from __future__ import annotations

import json
import re


# 与 homecoming 的 owner_id 语义保持一致：main=主私聊, second=聊天室。
OWNER_MAIN = "main"
OWNER_SECOND = "second"

_SCHEMA_READY = False

_ID_SPLIT = re.compile(r"^\s*([A-Za-z_]+)\s*:\s*(.+?)\s*$")


def _normalize_id(raw: str) -> str:
    """统一来源 id 格式为 ``namespace:id``。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    matched = _ID_SPLIT.match(text)
    if matched:
        namespace, ident = matched.group(1).lower(), matched.group(2).strip()
        if ident:
            return f"{namespace}:{ident}"
    return text


def normalize_source_ids(values) -> list[str]:
    """把各种形态的来源 id 列表归一化并去重。"""
    if values is None:
        return []
    if isinstance(values, str):
        try:
            parsed = json.loads(values)
            values = parsed if isinstance(parsed, list) else [values]
        except (json.JSONDecodeError, TypeError):
            values = [values]
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    result = []
    for item in values:
        normalized = _normalize_id(item)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


async def ensure_coverage_schema(db) -> None:
    """创建覆盖表。幂等，可在任意事务里调用。"""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_message_coverage (
            owner_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            memory_id TEXT DEFAULT '',
            origin TEXT DEFAULT 'explicit',
            created_at REAL NOT NULL,
            PRIMARY KEY (owner_id, message_id)
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_message_coverage_memory "
        "ON memory_message_coverage(memory_id)"
    )


async def mark_covered(
    db,
    owner_id: str,
    message_ids,
    *,
    memory_id: str = "",
    origin: str = "explicit",
    now: float = 0.0,
) -> int:
    """登记「这些消息已被记忆覆盖」。返回登记条数。

    显式写入与 digest 共用同一张表：digest 写入的每条记忆也会登记它的来源，
    这样显式写入可以反过来避开 digest 已经记过的内容。
    """
    if owner_id not in {OWNER_MAIN, OWNER_SECOND}:
        return 0
    ids = normalize_source_ids(message_ids)
    if not ids:
        return 0
    await ensure_coverage_schema(db)
    stamp = float(now) if now else 0.0
    if not stamp:
        import time
        stamp = time.time()
    await db.executemany(
        "INSERT OR IGNORE INTO memory_message_coverage "
        "(owner_id, message_id, memory_id, origin, created_at) VALUES (?,?,?,?,?)",
        [
            (owner_id, message_id, str(memory_id or ""), str(origin or ""), stamp)
            for message_id in ids
        ],
    )
    return len(ids)


async def covered_message_ids(db, owner_id: str, message_ids) -> set[str]:
    """查出给定消息中已被覆盖的那些。表不存在时返回空集合（fail-open）。"""
    if owner_id not in {OWNER_MAIN, OWNER_SECOND}:
        return set()
    ids = normalize_source_ids(message_ids)
    if not ids:
        return set()
    covered: set[str] = set()
    try:
        for offset in range(0, len(ids), 400):
            batch = ids[offset:offset + 400]
            placeholders = ",".join("?" for _ in batch)
            cursor = await db.execute(
                "SELECT message_id FROM memory_message_coverage "
                f"WHERE owner_id=? AND message_id IN ({placeholders})",
                (owner_id, *batch),
            )
            covered.update(str(row[0]) for row in await cursor.fetchall())
    except Exception:
        return set()
    return covered


async def filter_uncovered_messages(
    db,
    owner_id: str,
    messages: list[dict],
    id_key: str = "id",
    namespace: str = "",
) -> tuple[list[dict], set[str]]:
    """从消息列表中剔除已被记忆覆盖的消息。

    优先用消息自带的 ``_source_id``（已含命名空间，如 ``private:msg_x`` /
    ``chatroom:cm_x``）；没有时才用 ``id_key`` + ``namespace`` 兜底补前缀。
    主私聊 digest 的消息流混合私聊与群聊，两种命名空间并存，不能统一补前缀。
    """
    if not messages:
        return list(messages), set()
    candidates = []
    for item in messages:
        raw = str(item.get("_source_id") or item.get(id_key) or "")
        if not raw:
            return list(messages), set()
        normalized = _normalize_id(raw)
        if namespace and ":" not in normalized:
            normalized = f"{namespace}:{normalized}"
        candidates.append(normalized)
    covered = await covered_message_ids(db, owner_id, candidates)
    if not covered:
        return list(messages), set()
    kept = []
    hit: set[str] = set()
    for item, normalized in zip(messages, candidates):
        if normalized in covered:
            hit.add(normalized)
        else:
            kept.append(item)
    return kept, hit
