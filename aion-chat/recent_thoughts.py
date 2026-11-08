"""Short-lived thoughts born during autonomous activity.

A thought can be offered to the main private-chat model a few times; this does
not mean it has been said aloud. Old thoughts remain viewable in the notebook.
"""

import time
import uuid

import aiosqlite

from database import get_db


ACTORS = {"aion", "connor"}
LIFETIME_SECONDS = 14 * 86400
REOFFER_SECONDS = 12 * 3600
MAX_OFFERS = 3


async def ensure_recent_thoughts_table(db):
    await db.execute("""
        CREATE TABLE IF NOT EXISTS recent_thoughts (
            id TEXT PRIMARY KEY,
            actor TEXT NOT NULL,
            content TEXT NOT NULL,
            source_action TEXT NOT NULL DEFAULT '',
            source_ref TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            shown_count INTEGER NOT NULL DEFAULT 0,
            last_presented_at REAL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_recent_thoughts_actor_status "
        "ON recent_thoughts(actor, status, created_at DESC)"
    )


async def save_recent_thought(actor: str, content: str, *, source_action: str, source_ref: str = "") -> dict | None:
    """Save a genuinely chosen thought, deduplicating recent identical entries."""
    if actor not in ACTORS:
        return None
    body = " ".join(str(content or "").split()).strip()[:380]
    if len(body) < 8:
        return None
    now = time.time()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await ensure_recent_thoughts_table(db)
        cursor = await db.execute(
            "SELECT id FROM recent_thoughts WHERE actor=? AND content=? "
            "AND created_at>=? AND status!='archived' LIMIT 1",
            (actor, body, now - 3 * 86400),
        )
        if await cursor.fetchone():
            return None
        item = {
            "id": "thought_" + uuid.uuid4().hex,
            "actor": actor,
            "content": body,
            "source_action": str(source_action or "")[:80],
            "source_ref": str(source_ref or "")[:160],
            "status": "active",
            "shown_count": 0,
            "last_presented_at": None,
            "created_at": now,
            "expires_at": now + LIFETIME_SECONDS,
        }
        await db.execute(
            "INSERT INTO recent_thoughts "
            "(id,actor,content,source_action,source_ref,status,shown_count,last_presented_at,created_at,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            tuple(item[key] for key in (
                "id", "actor", "content", "source_action", "source_ref", "status",
                "shown_count", "last_presented_at", "created_at", "expires_at"
            )),
        )
        await db.commit()
    try:
        from ws import manager
        await manager.broadcast({"type": "recent_thoughts_changed", "data": {"actor": actor}})
    except Exception:
        pass
    return item


async def list_recent_thoughts(actor: str, limit: int = 30) -> list[dict]:
    if actor not in ACTORS:
        return []
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await ensure_recent_thoughts_table(db)
        now = time.time()
        await db.execute(
            "UPDATE recent_thoughts SET status='expired' "
            "WHERE status='active' AND expires_at<=?", (now,),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT id,actor,content,source_action,source_ref,status,shown_count,"
            "last_presented_at,created_at,expires_at FROM recent_thoughts "
            "WHERE actor=? ORDER BY created_at DESC LIMIT ?",
            (actor, max(1, min(80, int(limit)))),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def archive_recent_thought(actor: str, thought_id: str) -> bool:
    if actor not in ACTORS:
        return False
    async with get_db() as db:
        await ensure_recent_thoughts_table(db)
        cur = await db.execute(
            "UPDATE recent_thoughts SET status='archived' "
            "WHERE id=? AND actor=? AND status!='archived'",
            (thought_id, actor),
        )
        await db.commit()
        return cur.rowcount > 0


async def take_recent_thoughts_for_chat(actor: str = "aion", limit: int = 2) -> str:
    """Offer a few ideas to the private-chat model, without asserting they were spoken."""
    if actor not in ACTORS:
        return ""
    now = time.time()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await ensure_recent_thoughts_table(db)
        await db.execute(
            "UPDATE recent_thoughts SET status='expired' "
            "WHERE status='active' AND expires_at<=?", (now,),
        )
        cur = await db.execute(
            "SELECT id,content,source_action,created_at,shown_count FROM recent_thoughts "
            "WHERE actor=? AND status='active' AND expires_at>? AND shown_count<? "
            "AND (last_presented_at IS NULL OR last_presented_at<=?) "
            "ORDER BY created_at DESC LIMIT ?",
            (actor, now, MAX_OFFERS, now - REOFFER_SECONDS, max(1, min(3, limit))),
        )
        rows = [dict(row) for row in await cur.fetchall()]
        for row in rows:
            next_count = row["shown_count"] + 1
            await db.execute(
                "UPDATE recent_thoughts SET shown_count=?, last_presented_at=?, status=? WHERE id=?",
                (next_count, now, "resting" if next_count >= MAX_OFFERS else "active", row["id"]),
            )
        await db.commit()
    if not rows:
        return ""
    lines = []
    for row in rows:
        date = time.strftime("%m-%d", time.localtime(row["created_at"]))
        action = row["source_action"] or "自主活动"
        lines.append(f"- {date}（{action}）{row['content']}")
    return (
        "[你自己最近留下的想法]\n"
        "这些是你在小家自主活动后想继续想一想的念头，不是知提出的任务，也不表示你已经告诉过她。"
        "如果这次聊天自然关联、你自己也愿意，可以顺势说出来；否则照常聊天，不要为了完成任务硬提。\n"
        + "\n".join(lines)
    )
