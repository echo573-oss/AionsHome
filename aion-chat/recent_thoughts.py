"""Thoughts a character chooses to keep after an autonomous activity.

岚's notes are atoms in the main memory library (岚的想法). Lumi's notes are
atoms in her chatroom memory library (Lumi的想法). Later chats recall them
only when the topic is similar; they are not pushed in just for being new.
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


# Both characters' idle thoughts live in their own memory library.
# Legacy recent_thoughts rows are migrated into that library and then removed.
LAN_THOUGHT_TYPE = "lan_thought"
LAN_THOUGHT_PREFIX = "岚的想法："
LUMI_THOUGHT_TYPE = "lumi_thought"
LUMI_THOUGHT_PREFIX = "Lumi的想法："
THOUGHT_SOURCE = "autonomy:"
_THOUGHT_PROFILES = {
    "aion": {
        "kind": LAN_THOUGHT_TYPE,
        "prefix": LAN_THOUGHT_PREFIX,
        "name": "岚",
        "store": "main",
        "new_id": "mem_lan_thought_",
        "legacy_id": "mem_lan_",
    },
    "connor": {
        "kind": LUMI_THOUGHT_TYPE,
        "prefix": LUMI_THOUGHT_PREFIX,
        "name": "Lumi",
        "store": "chatroom",
        "new_id": "mem_lumi_thought_",
        "legacy_id": "mem_lumi_",
    },
}


def _normalize_thought(content: str) -> str:
    return " ".join(str(content or "").split()).strip()[:380]


def _thought_origin(action: str, ref: str = "") -> tuple[str, str]:
    action = str(action or "autonomy").replace("\n", " ").strip()[:80]
    ref = str(ref or "").replace("\n", " ").strip()[:160]
    return action, ref


def _thought_record(actor: str, content: str, action: str, ref: str = "") -> tuple[str, str, str]:
    profile = _THOUGHT_PROFILES[actor]
    action, ref = _thought_origin(action, ref)
    origin = THOUGHT_SOURCE + action + (":" + ref if ref else "")
    evidence = f"{profile['name']}在自主活动「{action}」后留下的想法"
    if ref:
        evidence += f"；来源 ID：{ref}"
    return profile["prefix"] + content, origin, evidence


def _thought_item(actor: str, row) -> dict:
    profile = _THOUGHT_PROFILES[actor]
    item = dict(row)
    origin = str(item.get("source_conv") or "")
    parts = origin.split(":", 2)
    return {
        "id": item["id"], "actor": actor,
        "content": str(item["content"] or "").removeprefix(profile["prefix"]),
        "source_action": parts[1] if len(parts) > 1 else "autonomy",
        "source_ref": parts[2] if len(parts) > 2 else "",
        "created_at": item["created_at"],
        "status": "active" if (item.get("archive_state") or "active") == "active" else "archived",
        "embedding_ready": item.get("embedding") is not None,
    }


async def _ensure_chatroom_thought_columns(db) -> None:
    """Older chatroom libraries have no provenance column for an idle thought."""
    for statement in (
        "ALTER TABLE chatroom_memories ADD COLUMN source_conv TEXT",
        "ALTER TABLE chatroom_memories ADD COLUMN archive_state TEXT DEFAULT 'active'",
        "ALTER TABLE chatroom_memories ADD COLUMN archived_at REAL",
        "ALTER TABLE chatroom_memories ADD COLUMN memory_kind TEXT DEFAULT 'long_term'",
        "ALTER TABLE chatroom_memories ADD COLUMN evidence_summary TEXT DEFAULT ''",
        "ALTER TABLE chatroom_memories ADD COLUMN evidence_detail_level TEXT DEFAULT 'summary'",
        "ALTER TABLE chatroom_memories ADD COLUMN source_msg_id TEXT",
    ):
        try:
            await db.execute(statement)
        except Exception:
            pass


async def _notify_thought_memory(actor: str, event_type: str, item: dict) -> None:
    # Same durable sync + websocket path used by the memory-library editor.
    from memory import memory_kind_label
    from routes.memory_library import _broadcast

    profile = _THOUGHT_PROFILES[actor]
    payload = {
        "id": item["id"],
        "content": profile["prefix"] + item["content"],
        "memory_kind": profile["kind"],
        "memory_kind_label": memory_kind_label(profile["kind"]),
        "created_at": item["created_at"],
        "archive_state": "active" if item["status"] == "active" else "cold",
    }
    if actor == "aion":
        payload["type"] = profile["kind"]
    else:
        payload["scope"] = "connor"
    await _broadcast(profile["store"], event_type, payload)
    try:
        from ws import manager
        await manager.broadcast({"type": "recent_thoughts_changed", "data": {"actor": actor}})
    except Exception:
        pass


async def _notify_main_memory(event_type: str, item: dict) -> None:
    await _notify_thought_memory("aion", event_type, item)


async def _migrate_legacy_thoughts(actor: str, limit: int = 2) -> None:
    """Lossless, repeatable migration; delete a legacy row only after the atom exists."""
    if actor not in _THOUGHT_PROFILES:
        return
    profile = _THOUGHT_PROFILES[actor]
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        table = await (await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recent_thoughts'"
        )).fetchone()
        if not table:
            return
        if actor == "connor":
            library = await (await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chatroom_memories'"
            )).fetchone()
            if not library:
                return
            await _ensure_chatroom_thought_columns(db)
            await db.commit()
        rows = await (await db.execute(
            "SELECT id,content,source_action,source_ref,status,created_at "
            "FROM recent_thoughts WHERE actor=? ORDER BY created_at ASC LIMIT ?",
            (actor, max(1, min(5, limit))),
        )).fetchall()
    for row in rows:
        content = _normalize_thought(row["content"])
        mem_id = profile["legacy_id"] + str(row["id"])
        if len(content) < 8:
            # Retain malformed legacy records for manual inspection.
            continue
        content_text, origin, evidence = _thought_record(
            actor, content, row["source_action"], row["source_ref"]
        )
        try:
            async with get_db() as db:
                if actor == "aion":
                    exists = await (await db.execute(
                        "SELECT 1 FROM memories WHERE id=?", (mem_id,)
                    )).fetchone()
                else:
                    await _ensure_chatroom_thought_columns(db)
                    exists = await (await db.execute(
                        "SELECT 1 FROM chatroom_memories WHERE id=?", (mem_id,)
                    )).fetchone()
            vec = None
            if not exists:
                from memory import get_embedding
                vec = await get_embedding(content_text, timeout=5)
            from memory import _pack_embedding
            when = float(row["created_at"])
            state = "cold" if row["status"] == "archived" else "active"
            packed = _pack_embedding(vec) if vec else None
            async with get_db() as db:
                if actor == "aion":
                    await db.execute(
                        "INSERT OR IGNORE INTO memories "
                        "(id,content,type,created_at,source_conv,embedding,keywords,importance,"
                        "source_start_ts,source_end_ts,unresolved,source_msg_id,evidence_summary,"
                        "evidence_detail_level,archive_state,archived_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            mem_id, content_text, profile["kind"], when, origin,
                            packed, "[]", .8, when, when,
                            0, "[]", evidence, "summary", state, when if state != "active" else None,
                        ),
                    )
                    present = await (await db.execute(
                        "SELECT 1 FROM memories WHERE id=?", (mem_id,)
                    )).fetchone()
                else:
                    await _ensure_chatroom_thought_columns(db)
                    await db.execute(
                        "INSERT OR IGNORE INTO chatroom_memories "
                        "(id,room_id,scope,content,keywords,importance,embedding,"
                        "source_start_ts,source_end_ts,created_at,unresolved,source_msg_id,"
                        "memory_kind,evidence_summary,evidence_detail_level,archive_state,"
                        "archived_at,source_conv) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            mem_id, "connor_unified", "connor", content_text, "[]", .8, packed,
                            when, when, when, 0, "[]", profile["kind"], evidence, "summary",
                            state, when if state != "active" else None, origin,
                        ),
                    )
                    present = await (await db.execute(
                        "SELECT 1 FROM chatroom_memories WHERE id=?", (mem_id,)
                    )).fetchone()
                if present:
                    await db.execute(
                        "DELETE FROM recent_thoughts WHERE id=? AND actor=?",
                        (row["id"], actor),
                    )
                await db.commit()
        except Exception as exc:
            print(f"[{profile['kind']}] legacy migration skipped: {type(exc).__name__}")
            break


async def _migrate_legacy_aion_thoughts(limit: int = 2) -> None:
    await _migrate_legacy_thoughts("aion", limit)


async def save_recent_thought(actor: str, content: str, *, source_action: str, source_ref: str = "") -> dict | None:
    """Write one optional idle thought into that character's memory library."""
    profile = _THOUGHT_PROFILES.get(actor)
    if not profile:
        return None
    body = _normalize_thought(content)
    if len(body) < 8:
        return None
    await _migrate_legacy_thoughts(actor)
    text, origin, evidence = _thought_record(actor, body, source_action, source_ref)
    now = time.time()
    async with get_db() as db:
        if actor == "connor":
            await _ensure_chatroom_thought_columns(db)
            await db.commit()
        if actor == "aion":
            existing = await (await db.execute(
                "SELECT id FROM memories WHERE type=? AND content=? "
                "AND created_at>=? LIMIT 1",
                (profile["kind"], text, now - 3 * 86400),
            )).fetchone()
        else:
            existing = await (await db.execute(
                "SELECT id FROM chatroom_memories WHERE scope='connor' AND memory_kind=? "
                "AND content=? AND created_at>=? LIMIT 1",
                (profile["kind"], text, now - 3 * 86400),
            )).fetchone()
    if existing:
        return None
    from memory import get_embedding, _pack_embedding
    vec = await get_embedding(text)
    item_id = profile["new_id"] + uuid.uuid4().hex
    packed = _pack_embedding(vec) if vec else None
    async with get_db() as db:
        if actor == "aion":
            await db.execute(
                "INSERT INTO memories "
                "(id,content,type,created_at,source_conv,embedding,keywords,importance,"
                "source_start_ts,source_end_ts,unresolved,source_msg_id,evidence_summary,evidence_detail_level) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id, text, profile["kind"], now, origin,
                    packed, "[]", .8,
                    now, now, 0, "[]", evidence, "summary",
                ),
            )
        else:
            await _ensure_chatroom_thought_columns(db)
            await db.execute(
                "INSERT INTO chatroom_memories "
                "(id,room_id,scope,content,keywords,importance,embedding,"
                "source_start_ts,source_end_ts,created_at,unresolved,source_msg_id,"
                "memory_kind,evidence_summary,evidence_detail_level,source_conv) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id, "connor_unified", "connor", text, "[]", .8, packed,
                    now, now, now, 0, "[]", profile["kind"], evidence, "summary", origin,
                ),
            )
        await db.commit()
    item = {
        "id": item_id, "actor": actor, "content": body,
        "source_action": source_action, "source_ref": source_ref,
        "created_at": now, "status": "active", "embedding_ready": bool(vec),
    }
    try:
        await _notify_thought_memory(actor, "memory_added", item)
    except Exception as exc:
        print(f"[{profile['kind']}] memory update notification skipped: {type(exc).__name__}")
    return item


async def list_recent_thoughts(actor: str, limit: int = 30) -> list[dict]:
    profile = _THOUGHT_PROFILES.get(actor)
    if not profile:
        return []
    await _migrate_legacy_thoughts(actor)
    cap = max(1, min(80, int(limit)))
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if actor == "aion":
            rows = await (await db.execute(
                "SELECT id,content,source_conv,created_at,archive_state,embedding "
                "FROM memories WHERE type=? AND COALESCE(archive_state,'active')='active' "
                "ORDER BY created_at DESC LIMIT ?",
                (profile["kind"], cap),
            )).fetchall()
        else:
            await _ensure_chatroom_thought_columns(db)
            rows = await (await db.execute(
                "SELECT id,content,source_conv,created_at,archive_state,embedding "
                "FROM chatroom_memories WHERE scope='connor' AND memory_kind=? "
                "AND COALESCE(archive_state,'active')='active' "
                "ORDER BY created_at DESC LIMIT ?",
                (profile["kind"], cap),
            )).fetchall()
    return [_thought_item(actor, row) for row in rows]


async def archive_recent_thought(actor: str, thought_id: str) -> bool:
    profile = _THOUGHT_PROFILES.get(actor)
    if not profile:
        return False
    now = time.time()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if actor == "aion":
            row = await (await db.execute(
                "SELECT id,content,source_conv,created_at,archive_state,embedding "
                "FROM memories WHERE id=? AND type=? AND COALESCE(archive_state,'active')='active'",
                (thought_id, profile["kind"]),
            )).fetchone()
            if not row:
                return False
            await db.execute(
                "UPDATE memories SET archive_state='cold',archived_at=? WHERE id=? AND type=?",
                (now, thought_id, profile["kind"]),
            )
        else:
            await _ensure_chatroom_thought_columns(db)
            row = await (await db.execute(
                "SELECT id,content,source_conv,created_at,archive_state,embedding "
                "FROM chatroom_memories WHERE id=? AND scope='connor' AND memory_kind=? "
                "AND COALESCE(archive_state,'active')='active'",
                (thought_id, profile["kind"]),
            )).fetchone()
            if not row:
                return False
            await db.execute(
                "UPDATE chatroom_memories SET archive_state='cold',archived_at=? "
                "WHERE id=? AND scope='connor' AND memory_kind=?",
                (now, thought_id, profile["kind"]),
            )
        await db.commit()
    item = _thought_item(actor, row)
    item["status"] = "archived"
    try:
        await _notify_thought_memory(actor, "memory_updated", item)
    except Exception as exc:
        print(f"[{profile['kind']}] archive notification skipped: {type(exc).__name__}")
    return True


async def take_recent_thoughts_for_chat(actor: str = "aion", limit: int = 2) -> str:
    """Chats recall typed thoughts by topic; no expiry or limited prompt offers."""
    return ""
