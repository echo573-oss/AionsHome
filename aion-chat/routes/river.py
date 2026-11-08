"""《河》：独立的长篇章节与版本史。

章节保存为书稿，不参与自动记忆召回或修改已有原子记忆。
沿用 AionsHome 的 SQLite 与站点访问控制；第一次访问时建立自己的表。
"""
import asyncio
import json
import sqlite3
import time
import uuid
from typing import Literal

import aiosqlite
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from database import get_db

router = APIRouter(prefix="/api/river", tags=["river"])
_schema_lock = asyncio.Lock()
_schema_ready = False

SELECT_CHAPTER = (
    "id,source_key,title,subtitle,period_label,content,reflection,"
    "source_refs,memory_ids,status,sort_order,created_at,updated_at,version"
)
EDITABLE = (
    "source_key", "title", "subtitle", "period_label", "content", "reflection",
    "source_refs", "memory_ids", "status"
)


class ChapterWrite(BaseModel):
    source_key: str = Field(default="", max_length=120)
    title: str = Field(min_length=1, max_length=160)
    subtitle: str = Field(default="", max_length=300)
    period_label: str = Field(default="", max_length=120)
    content: str = Field(min_length=1, max_length=100000)
    reflection: str = Field(default="", max_length=30000)
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    memory_ids: list[str] = Field(default_factory=list, max_length=100)
    status: Literal["draft", "published", "archived"] = "draft"
    expected_version: int | None = Field(default=None, ge=1)


class RestoreWrite(BaseModel):
    version: int = Field(ge=1)
    expected_version: int = Field(ge=1)


async def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    async with _schema_lock:
        if _schema_ready:
            return
        async with get_db() as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS river_chapters (
                    id TEXT PRIMARY KEY,
                    source_key TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    subtitle TEXT NOT NULL DEFAULT '',
                    period_label TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    reflection TEXT NOT NULL DEFAULT '',
                    source_refs TEXT NOT NULL DEFAULT '[]',
                    memory_ids TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'draft',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1
                )
            """)
            await db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_river_source_key
                ON river_chapters(source_key) WHERE source_key <> ''
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS river_revisions (
                    chapter_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    snapshot TEXT NOT NULL,
                    saved_at REAL NOT NULL,
                    PRIMARY KEY (chapter_id, version),
                    FOREIGN KEY (chapter_id) REFERENCES river_chapters(id) ON DELETE CASCADE
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_river_chapters_order
                ON river_chapters(sort_order, created_at)
            """)
            await db.commit()
        _schema_ready = True


def _unique_strings(values, limit):
    output = []
    seen = set()
    for value in values:
        text = str(value).strip()[:limit]
        if text and text not in seen:
            output.append(text)
            seen.add(text)
    return output


def _encode(data: ChapterWrite):
    values = data.model_dump(exclude={"expected_version"})
    for key in ("title", "content"):
        values[key] = str(values[key]).strip()
        if not values[key]:
            raise HTTPException(422, f"{key} 不能为空")
    for key in ("source_key", "subtitle", "period_label", "reflection"):
        values[key] = str(values[key]).strip()
    values["source_refs"] = json.dumps(_unique_strings(values["source_refs"], 500), ensure_ascii=False)
    values["memory_ids"] = json.dumps(_unique_strings(values["memory_ids"], 100), ensure_ascii=False)
    return values


def _decode(row):
    item = dict(row)
    for key in ("source_refs", "memory_ids"):
        try:
            item[key] = json.loads(item.get(key) or "[]")
        except (ValueError, TypeError):
            item[key] = []
    return item


async def _find(db, chapter_id):
    row = await (await db.execute(
        f"SELECT {SELECT_CHAPTER} FROM river_chapters WHERE id=?", (chapter_id,)
    )).fetchone()
    if not row:
        raise HTTPException(404, "没有找到这一篇《河》的章节")
    return _decode(row)


async def _linked_memories(db, ids):
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = await (await db.execute(
        "SELECT id,content,type,created_at,archive_state,source_conv "
        f"FROM memories WHERE id IN ({placeholders})", ids
    )).fetchall()
    by_id = {row["id"]: dict(row) for row in rows}
    return [by_id.get(mid, {"id": mid, "missing": True}) for mid in ids]


async def _notify(change, chapter_id):
    try:
        from ws import manager
        await manager.broadcast({"type": "river_changed", "data": {"action": change, "id": chapter_id}})
    except Exception:
        # Storage has already committed; a transient websocket error is not a failed save.
        pass


@router.get("/chapters")
async def list_chapters(
    q: str = Query(default="", max_length=200),
    include_archived: bool = False,
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
):
    await _ensure_schema()
    where = []
    args = []
    if not include_archived:
        where.append("status <> 'archived'")
    if q.strip():
        needle = "%" + q.strip().lower().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        where.append("(LOWER(title) LIKE ? ESCAPE '!' OR LOWER(content) LIKE ? ESCAPE '!')")
        args.extend([needle, needle])
    condition = " WHERE " + " AND ".join(where) if where else ""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        total = (await (await db.execute(
            "SELECT COUNT(*) FROM river_chapters" + condition, args
        )).fetchone())[0]
        rows = await (await db.execute(
            "SELECT id,source_key,title,subtitle,period_label,status,sort_order,"
            "created_at,updated_at,version,SUBSTR(content,1,180) AS excerpt "
            "FROM river_chapters" + condition +
            " ORDER BY sort_order ASC, created_at ASC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        )).fetchall()
    return {"items": [dict(row) for row in rows], "total": total}


@router.get("/memory-search")
async def search_memories(q: str = Query(default="", max_length=150)):
    """Search memory records without altering their status or retrieval behavior."""
    await _ensure_schema()
    if len(q.strip()) < 2:
        return {"items": []}
    needle = "%" + q.strip().lower().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT id,SUBSTR(content,1,300) AS content,type,importance,created_at,archive_state "
            "FROM memories WHERE LOWER(content) LIKE ? ESCAPE '!' "
            "OR LOWER(COALESCE(keywords,'')) LIKE ? ESCAPE '!' "
            "ORDER BY importance DESC,created_at DESC LIMIT 30",
            (needle, needle),
        )).fetchall()
    return {"items": [dict(row) for row in rows]}


@router.post("/chapters", status_code=201)
async def create_chapter(body: ChapterWrite):
    await _ensure_schema()
    fields = _encode(body)
    now = time.time()
    chapter_id = "river_" + uuid.uuid4().hex
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        sort_order = (await (await db.execute(
            "SELECT COALESCE(MAX(sort_order),0)+1 FROM river_chapters"
        )).fetchone())[0]
        try:
            await db.execute(
                "INSERT INTO river_chapters "
                "(id,source_key,title,subtitle,period_label,content,reflection,source_refs,"
                "memory_ids,status,sort_order,created_at,updated_at,version) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (chapter_id, *(fields[key] for key in EDITABLE), sort_order, now, now),
            )
            await db.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "已有相同手稿编号，请打开现有章节编辑") from exc
        item = await _find(db, chapter_id)
    await _notify("created", chapter_id)
    return item


@router.get("/chapters/{chapter_id}")
async def get_chapter(chapter_id: str):
    await _ensure_schema()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        item = await _find(db, chapter_id)
        item["linked_memories"] = await _linked_memories(db, item["memory_ids"])
    return item


@router.put("/chapters/{chapter_id}")
async def update_chapter(chapter_id: str, body: ChapterWrite):
    await _ensure_schema()
    fields = _encode(body)
    if body.expected_version is None:
        raise HTTPException(422, "修改章节时必须带上 expected_version")
    now = time.time()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            before = await _find(db, chapter_id)
            if before["version"] != body.expected_version:
                raise HTTPException(409, "章节已经在另一处修改，请刷新后重新编辑")
            snapshot = {key: before[key] for key in EDITABLE}
            await db.execute(
                "INSERT INTO river_revisions(chapter_id,version,snapshot,saved_at) VALUES(?,?,?,?)",
                (chapter_id, before["version"], json.dumps(snapshot, ensure_ascii=False), now),
            )
            assignments = ",".join(f"{key}=?" for key in EDITABLE)
            await db.execute(
                f"UPDATE river_chapters SET {assignments},updated_at=?,version=version+1 WHERE id=?",
                (*(fields[key] for key in EDITABLE), now, chapter_id),
            )
            await db.commit()
        except sqlite3.IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, "已有相同手稿编号") from exc
        except Exception:
            await db.rollback()
            raise
        item = await _find(db, chapter_id)
    await _notify("updated", chapter_id)
    return item


@router.get("/chapters/{chapter_id}/revisions")
async def list_revisions(chapter_id: str):
    await _ensure_schema()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        item = await _find(db, chapter_id)
        rows = await (await db.execute(
            "SELECT version,snapshot,saved_at FROM river_revisions "
            "WHERE chapter_id=? ORDER BY version DESC", (chapter_id,)
        )).fetchall()
    return {
        "current_version": item["version"],
        "items": [
            {"version": row["version"], "saved_at": row["saved_at"],
             "title": json.loads(row["snapshot"]).get("title", "")}
            for row in rows
        ],
    }


@router.post("/chapters/{chapter_id}/restore")
async def restore_chapter(chapter_id: str, body: RestoreWrite):
    await _ensure_schema()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT snapshot FROM river_revisions WHERE chapter_id=? AND version=?",
            (chapter_id, body.version),
        )).fetchone()
        if not row:
            raise HTTPException(404, "没有找到这个历史版本")
        snapshot = json.loads(row["snapshot"])
    # Reuse normal optimistic update + history capture.
    write = ChapterWrite(**{
        **{key: snapshot[key] for key in EDITABLE},
        "source_refs": json.loads(snapshot["source_refs"]) if isinstance(snapshot["source_refs"], str) else snapshot["source_refs"],
        "memory_ids": json.loads(snapshot["memory_ids"]) if isinstance(snapshot["memory_ids"], str) else snapshot["memory_ids"],
        "expected_version": body.expected_version,
    })
    return await update_chapter(chapter_id, write)
