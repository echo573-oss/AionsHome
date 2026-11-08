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


async def _legacy_save_recent_thought(actor: str, content: str, *, source_action: str, source_ref: str = "") -> dict | None:
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


async def _legacy_list_recent_thoughts(actor: str, limit: int = 30) -> list[dict]:
    if actor not in ACTORS:
        return []
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await ensure_recent_thoughts_table(db)
        now = time.time()
        await db.execute(
            "UPDATE recent_thoughts SET status='expired' "
            "WHERE status IN ('active','resting') AND expires_at<=?", (now,),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT id,actor,content,source_action,source_ref,status,shown_count,"
            "last_presented_at,created_at,expires_at FROM recent_thoughts "
            "WHERE actor=? ORDER BY created_at DESC LIMIT ?",
            (actor, max(1, min(80, int(limit)))),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def _legacy_archive_recent_thought(actor: str, thought_id: str) -> bool:
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


async def _legacy_take_recent_thoughts_for_chat(actor: str = "aion", limit: int = 2) -> str:
    """Offer a few ideas to the private-chat model, without asserting they were spoken."""
    if actor not in ACTORS:
        return ""
    now = time.time()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await ensure_recent_thoughts_table(db)
        await db.execute(
            "UPDATE recent_thoughts SET status='expired' "
            "WHERE status IN ('active','resting') AND expires_at<=?", (now,),
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


# Aion's thoughts now live as one atom in the existing vector memory library.
# The legacy table remains only for Connor compatibility; Aion rows are migrated
# into memories and removed from the legacy table.
LAN_THOUGHT_TYPE = "lan_thought"
LAN_THOUGHT_PREFIX = "岚的想法："
THOUGHT_SOURCE = "autonomy:"


def _normalize_thought(content: str) -> str:
    return " ".join(str(content or "").split()).strip()[:380]


def _thought_origin(action: str, ref: str = "") -> tuple[str, str]:
    action = str(action or "autonomy").replace("\n", " ").strip()[:80]
    ref = str(ref or "").replace("\n", " ").strip()[:160]
    return action, ref


def _thought_record(content: str, action: str, ref: str = "") -> tuple[str, str, str]:
    action, ref = _thought_origin(action, ref)
    origin = THOUGHT_SOURCE + action + (":" + ref if ref else "")
    evidence = f"岚在自主活动「{action}」后留下的想法"
    if ref:
        evidence += f"；来源 ID：{ref}"
    return LAN_THOUGHT_PREFIX + content, origin, evidence


def _thought_item(row) -> dict:
    item = dict(row)
    origin = str(item.get("source_conv") or "")
    parts = origin.split(":", 2)
    return {
        "id": item["id"], "actor": "aion",
        "content": str(item["content"] or "").removeprefix(LAN_THOUGHT_PREFIX),
        "source_action": parts[1] if len(parts) > 1 else "autonomy",
        "source_ref": parts[2] if len(parts) > 2 else "",
        "created_at": item["created_at"],
        "status": "active" if (item.get("archive_state") or "active") == "active" else "archived",
        "embedding_ready": item.get("embedding") is not None,
    }


async def _notify_main_memory(event_type: str, item: dict) -> None:
    # Same durable sync + websocket path used by the memory-library editor.
    from memory import memory_kind_label
    from routes.memory_library import _broadcast

    payload = {
        "id": item["id"],
        "content": LAN_THOUGHT_PREFIX + item["content"],
        "type": LAN_THOUGHT_TYPE,
        "memory_kind": LAN_THOUGHT_TYPE,
        "memory_kind_label": memory_kind_label(LAN_THOUGHT_TYPE),
        "created_at": item["created_at"],
        "archive_state": "active" if item["status"] == "active" else "cold",
    }
    await _broadcast("main", event_type, payload)
    try:
        from ws import manager
        await manager.broadcast({"type": "recent_thoughts_changed", "data": {"actor": "aion"}})
    except Exception:
        pass


async def _migrate_legacy_aion_thoughts(limit: int = 20) -> None:
    """Lossless, repeatable migration; delete a legacy row only after the atom exists."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        table = await (await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recent_thoughts'"
        )).fetchone()
        if not table:
            return
        rows = await (await db.execute(
            "SELECT id,content,source_action,source_ref,status,created_at "
            "FROM recent_thoughts WHERE actor='aion' ORDER BY created_at ASC LIMIT ?",
            (max(1, min(20, limit)),)
        )).fetchall()
    for row in rows:
        content = _normalize_thought(row["content"])
        mem_id = "mem_lan_" + str(row["id"])
        if len(content) < 8:
            # Retain malformed legacy records for manual inspection.
            continue
        content_text, origin, evidence = _thought_record(
            content, row["source_action"], row["source_ref"]
        )
        try:
            async with get_db() as db:
                exists = await (await db.execute("SELECT 1 FROM memories WHERE id=?", (mem_id,))).fetchone()
            vec = None
            if not exists:
                from memory import get_embedding
                vec = await get_embedding(content_text)
            from memory import _pack_embedding
            when = float(row["created_at"])
            state = "active" if row["status"] in ("active", "resting") else "cold"
            async with get_db() as db:
                await db.execute(
                    "INSERT OR IGNORE INTO memories "
                    "(id,content,type,created_at,source_conv,embedding,keywords,importance,"
                    "source_start_ts,source_end_ts,unresolved,source_msg_id,evidence_summary,"
                    "evidence_detail_level,archive_state,archived_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        mem_id, content_text, LAN_THOUGHT_TYPE, when, origin,
                        _pack_embedding(vec) if vec else None, "[]", .8, when, when,
                        0, "[]", evidence, "summary", state, when if state != "active" else None,
                    ),
                )
                # Delete only when the target row is present, including a prior partial migration.
                present = await (await db.execute("SELECT 1 FROM memories WHERE id=?", (mem_id,))).fetchone()
                if present:
                    await db.execute("DELETE FROM recent_thoughts WHERE id=? AND actor='aion'", (row["id"],))
                await db.commit()
        except Exception as exc:
            print(f"[lan_thought] legacy migration skipped: {type(exc).__name__}")
            break


async def save_recent_thought(actor: str, content: str, *, source_action: str, source_ref: str = "") -> dict | None:
    """Aion writes one typed vector memory; Connor keeps existing separate behavior."""
    if actor == "connor":
        return await _legacy_save_recent_thought(actor, content, source_action=source_action, source_ref=source_ref)
    if actor != "aion":
        return None
    body = _normalize_thought(content)
    if len(body) < 8:
        return None
    await _migrate_legacy_aion_thoughts()
    text, origin, evidence = _thought_record(body, source_action, source_ref)
    now = time.time()
    async with get_db() as db:
        existing = await (await db.execute(
            "SELECT id FROM memories WHERE type=? AND content=? "
            "AND created_at>=? LIMIT 1",
            (LAN_THOUGHT_TYPE, text, now - 3 * 86400),
        )).fetchone()
    if existing:
        return None
    from memory import get_embedding, _pack_embedding
    vec = await get_embedding(text)
    item_id = "mem_lan_thought_" + uuid.uuid4().hex
    async with get_db() as db:
        await db.execute(
            "INSERT INTO memories "
            "(id,content,type,created_at,source_conv,embedding,keywords,importance,"
            "source_start_ts,source_end_ts,unresolved,source_msg_id,evidence_summary,evidence_detail_level) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item_id, text, LAN_THOUGHT_TYPE, now, origin,
                _pack_embedding(vec) if vec else None, "[]", .8,
                now, now, 0, "[]", evidence, "summary",
            ),
        )
        await db.commit()
    item = {
        "id": item_id, "actor": "aion", "content": body,
        "source_action": source_action, "source_ref": source_ref,
        "created_at": now, "status": "active", "embedding_ready": bool(vec),
    }
    try:
        await _notify_main_memory("memory_added", item)
    except Exception as exc:
        print(f"[lan_thought] memory update notification skipped: {type(exc).__name__}")
    return item


async def list_recent_thoughts(actor: str, limit: int = 30) -> list[dict]:
    if actor == "connor":
        return await _legacy_list_recent_thoughts(actor, limit=limit)
    if actor != "aion":
        return []
    await _migrate_legacy_aion_thoughts()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT id,content,source_conv,created_at,archive_state,embedding "
            "FROM memories WHERE type=? AND COALESCE(archive_state,'active')='active' "
            "ORDER BY created_at DESC LIMIT ?",
            (LAN_THOUGHT_TYPE, max(1, min(80, int(limit)))),
        )).fetchall()
    return [_thought_item(row) for row in rows]


async def archive_recent_thought(actor: str, thought_id: str) -> bool:
    if actor == "connor":
        return await _legacy_archive_recent_thought(actor, thought_id)
    if actor != "aion":
        return False
    now = time.time()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT id,content,source_conv,created_at,archive_state,embedding "
            "FROM memories WHERE id=? AND type=? AND COALESCE(archive_state,'active')='active'",
            (thought_id, LAN_THOUGHT_TYPE),
        )).fetchone()
        if not row:
            return False
        await db.execute(
            "UPDATE memories SET archive_state='cold',archived_at=? WHERE id=? AND type=?",
            (now, thought_id, LAN_THOUGHT_TYPE),
        )
        await db.commit()
    item = _thought_item(row)
    item["status"] = "archived"
    try:
        await _notify_main_memory("memory_updated", item)
    except Exception as exc:
        print(f"[lan_thought] archive notification skipped: {type(exc).__name__}")
    return True


async def take_recent_thoughts_for_chat(actor: str = "aion", limit: int = 2) -> str:
    """Main chat recalls typed thoughts by topic; no expiry or limited prompt offers."""
    if actor == "connor":
        return await _legacy_take_recent_thoughts_for_chat(actor, limit)
    return ""
