"""从聊天中收集生活片段，按事情发生的日期写入一日一夜日记本。

与后置哨兵的关系：共用同一批本人消息，但两者目标相反——后置哨兵维护"还要跟进的事"，
这里维护"已经发生的生活片段"。两者互不覆盖，各自的 prompt 互不混用。
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from config import SETTINGS, get_sentinel_config, load_worldbook
from database import get_db
from life_diary_format import (ALLOWED_FIELDS, BLOCK_TYPES, DATE_RE, merge_document,
                               new_document, weekday_label)
from life_diary_prompts import extraction_prompt

log = logging.getLogger(__name__)

DEFAULT_ROOT = Path(r'D:\obvault\DiaryInbox')
QUIET_MINUTES_DEFAULT = 30
FALLBACK_HOUR_DEFAULT = 23
FALLBACK_MINUTE_DEFAULT = 30
BATCH_LIMIT = 300
MAX_DAYS = 7
MAX_BLOCK_CHARS = 200

_worker: asyncio.Task | None = None
_wake: asyncio.Event | None = None
_processing = asyncio.Lock()
_write_lock = asyncio.Lock()


def enabled() -> bool:
    from capabilities import is_capability_enabled
    return is_capability_enabled('life_diary')


def diary_root() -> Path:
    override = os.environ.get('AIONSHOME_LIFE_DIARY_ROOT', '').strip()
    configured = str(SETTINGS.get('life_diary_root') or '').strip()
    return Path(override or configured) if (override or configured) else DEFAULT_ROOT


def quiet_seconds() -> int:
    try:
        minutes = int(SETTINGS.get('life_diary_quiet_minutes', QUIET_MINUTES_DEFAULT))
    except (TypeError, ValueError):
        minutes = QUIET_MINUTES_DEFAULT
    return max(0, minutes) * 60


def _fallback_moment() -> tuple[int, int]:
    try:
        hour = int(SETTINGS.get('life_diary_daily_hour', FALLBACK_HOUR_DEFAULT))
    except (TypeError, ValueError):
        hour = FALLBACK_HOUR_DEFAULT
    try:
        minute = int(SETTINGS.get('life_diary_daily_minute', FALLBACK_MINUTE_DEFAULT))
    except (TypeError, ValueError):
        minute = FALLBACK_MINUTE_DEFAULT
    return max(0, min(23, hour)), max(0, min(59, minute))


def _user_name() -> str:
    return (load_worldbook().get('user_name') or '').strip() or '你'


def file_for(date: str) -> Path:
    year, month, _ = date.split('-')
    return diary_root() / f'{year}-{month}' / f'{date}.md'


# ── 数据表 ──────────────────────────────────────────────────

async def ensure_schema():
    async with get_db() as db:
        await db.execute('CREATE TABLE IF NOT EXISTS life_diary_jobs ('
                         'source_id TEXT PRIMARY KEY, scope TEXT NOT NULL, parent_id TEXT NOT NULL, '
                         'message_id TEXT NOT NULL, source_ts REAL NOT NULL, content TEXT NOT NULL, '
                         "status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, "
                         "next_attempt_at REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', "
                         "model_output TEXT NOT NULL DEFAULT '')")
        await db.execute('CREATE INDEX IF NOT EXISTS idx_life_diary_pending '
                         'ON life_diary_jobs(status, source_ts)')
        await db.execute('CREATE TABLE IF NOT EXISTS life_diary_fragments ('
                         'id INTEGER PRIMARY KEY, diary_date TEXT NOT NULL, file_path TEXT NOT NULL, '
                         'field_key TEXT NOT NULL, value TEXT NOT NULL, source_ids TEXT NOT NULL DEFAULT "[]", '
                         'source_ts REAL NOT NULL DEFAULT 0, date_inferred INTEGER NOT NULL DEFAULT 0, '
                         "status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        await db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_life_diary_fragment_key '
                         'ON life_diary_fragments(diary_date, field_key, value)')
        await db.execute('CREATE TABLE IF NOT EXISTS life_diary_state ('
                         'key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        await db.commit()


async def _set_state(key: str, value: str):
    async with get_db() as db:
        await db.execute('INSERT OR REPLACE INTO life_diary_state (key,value) VALUES (?,?)', (key, value))
        await db.commit()


async def _get_state(key: str) -> str:
    async with get_db() as db:
        row = await (await db.execute('SELECT value FROM life_diary_state WHERE key=?', (key,))).fetchone()
        return (row[0] if row else '') or ''


# ── 入队 ────────────────────────────────────────────────────

async def enqueue_event(event: dict, *, wake=True) -> bool:
    """只收本人消息；AI 台词与环境语音不进日记。"""
    if not enabled() or event.get('type') not in ('msg_created', 'chatroom_msg_created'):
        return False
    value = event.get('data') or {}
    private = event['type'] == 'msg_created'
    role = value.get('role' if private else 'sender')
    if role != 'user' or value.get('duplicate'):
        return False
    if any(isinstance(item, dict) and item.get('type') == 'ambient_voice'
           for item in value.get('attachments', [])):
        return False
    msg_id = value.get('id')
    parent = value.get('conv_id' if private else 'room_id')
    text = value.get('content')
    ts = value.get('created_at')
    if not msg_id or not parent or not isinstance(text, str) or not text.strip():
        return False
    if not isinstance(ts, (int, float)) or not math.isfinite(ts):
        return False
    scope = 'private' if private else 'chatroom'
    async with get_db() as db:
        await db.execute('INSERT OR IGNORE INTO life_diary_jobs '
                         '(source_id,scope,parent_id,message_id,source_ts,content) VALUES (?,?,?,?,?,?)',
                         (f'{scope}:{msg_id}', scope, parent, msg_id, ts, text))
        await db.commit()
    if wake and _wake:
        _wake.set()
    return True


def wake():
    if _wake:
        _wake.set()


# ── 跑批 ────────────────────────────────────────────────────

async def _pending_jobs() -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM life_diary_jobs WHERE status='pending' AND next_attempt_at<=? "
            'ORDER BY source_ts LIMIT ?', (time.time(), BATCH_LIMIT))).fetchall()
    return [dict(row) for row in rows]


async def _daily_due(now: float) -> bool:
    hour, minute = _fallback_moment()
    local = datetime.fromtimestamp(now)
    if (local.hour, local.minute) < (hour, minute):
        return False
    return await _get_state('last_daily_run') != local.date().isoformat()


async def maybe_run_batch(*, force: bool = False) -> bool:
    if not enabled():
        return False
    jobs = await _pending_jobs()
    if not jobs:
        return False
    now = time.time()
    newest = max(job['source_ts'] for job in jobs)
    quiet_ok = (now - newest) >= quiet_seconds()
    daily_ok = await _daily_due(now)
    if not (force or quiet_ok or daily_ok):
        return False
    return await run_batch()


async def run_batch() -> bool:
    async with _processing:
        return await _run_batch()


async def _load_messages(jobs: list[dict]) -> tuple[list[dict], dict, list[dict], dict]:
    """读出仍然存在的本人消息，附带上下文、来源标签与来源时间。"""
    private = jobs[0]['scope'] == 'private'
    table, parent, role = ('messages', 'conv_id', 'role') if private else ('chatroom_messages', 'room_id', 'sender')
    ids = [job['message_id'] for job in jobs]
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        found: dict[str, dict] = {}
        for start in range(0, len(ids), 100):
            chunk = ids[start:start + 100]
            marks = ','.join('?' * len(chunk))
            rows = await (await db.execute(
                f'SELECT id,{parent} AS parent,{role} AS role,content,created_at FROM {table} '
                f'WHERE id IN ({marks})', chunk)).fetchall()
            for row in rows:
                found[row['id']] = dict(row)
    messages: list[dict] = []
    sources: dict[str, str] = {}
    source_times: dict[str, float] = {}
    alive: list[dict] = []
    for index, job in enumerate(jobs, 1):
        row = found.get(job['message_id'])
        if not row or row['content'] != job['content']:
            continue
        label = f'U{index}'
        sources[label] = job['source_id']
        source_times[label] = float(row['created_at'])
        messages.append({'speaker': 'user', 'source': label,
                         'time': datetime.fromtimestamp(row['created_at']).astimezone().isoformat(timespec='seconds'),
                         'text': row['content']})
        alive.append(job)
    context: list[dict] = []
    if alive:
        first_ts = min(job['source_ts'] for job in alive)
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(
                f'SELECT id,{role} AS role,content,created_at FROM {table} '
                f'WHERE {parent}=? AND created_at<? AND {role}=? ORDER BY created_at DESC LIMIT 12',
                (alive[0]['parent_id'], first_ts, 'user'))).fetchall()
        for row in reversed(rows):
            context.append({'time': datetime.fromtimestamp(row['created_at']).astimezone().isoformat(timespec='minutes'),
                            'text': row['content'][:200]})
    return messages, sources, context, source_times


def _existing_diary(dates: list[str]) -> dict:
    """给模型看已写入的内容，避免重复。"""
    existing: dict[str, str] = {}
    for date in dates[:3]:
        path = file_for(date)
        if not path.exists():
            continue
        try:
            existing[date] = path.read_text(encoding='utf-8')[:2000]
        except OSError:
            continue
    return existing


async def _call_model(prompt: str) -> dict:
    from memory import _call_sentinel_text
    from model_json import extract_json_object
    cfg = get_sentinel_config()
    if not cfg.get('ready'):
        raise RuntimeError('哨兵模型未配置')
    if cfg.get('provider') == 'codex':
        from ai_providers import call_codex_sentinel
        raw = await call_codex_sentinel(prompt, model=cfg['model'], timeout=120, use_chat_slot=False)
    else:
        raw = await _call_sentinel_text(cfg, prompt, timeout=120)
    parsed = extract_json_object(raw or '')
    if not isinstance(parsed, dict):
        raise ValueError('模型返回无法解析的 JSON 对象')
    return parsed


def _coerce_field(spec: str, value):
    if spec == 'int':
        if isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if 1 <= number <= 10 else None
    if spec == 'list':
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return None
        items = [str(item).strip() for item in value if str(item).strip()]
        return items[:8] or None
    text = str(value).strip()
    return text[:120] or None


def _normalize_days(parsed: dict, sources: dict) -> list[dict]:
    days: list[dict] = []
    raw_days = parsed.get('days')
    if not isinstance(raw_days, list):
        return days
    today = datetime.now().date()
    for item in raw_days[:MAX_DAYS]:
        if not isinstance(item, dict):
            continue
        date = str(item.get('date') or '').strip()
        if not DATE_RE.match(date):
            continue
        try:
            day = datetime.strptime(date, '%Y-%m-%d').date()
        except ValueError:
            continue
        if day > today or day < today - timedelta(days=730):
            continue
        fields: dict = {}
        raw_fields = item.get('fields')
        if isinstance(raw_fields, dict):
            for key, value in raw_fields.items():
                spec = ALLOWED_FIELDS.get(key)
                if not spec:
                    continue
                coerced = _coerce_field(spec, value)
                if coerced is not None:
                    fields[key] = coerced
        blocks: list[dict] = []
        raw_blocks = item.get('blocks')
        if isinstance(raw_blocks, list):
            for block in raw_blocks:
                if not isinstance(block, dict):
                    continue
                kind = str(block.get('kind') or '').strip()
                text = str(block.get('text') or '').strip()
                if kind not in BLOCK_TYPES or not text or len(text) > MAX_BLOCK_CHARS:
                    continue
                blocks.append({'kind': kind, 'text': text})
        refs = [ref for ref in (item.get('sources') or []) if isinstance(ref, str) and ref in sources]
        if not refs or (not fields and not blocks):
            continue
        days.append({'date': date, 'fields': fields, 'blocks': blocks,
                     'sources': list(dict.fromkeys(refs)),
                     'date_inferred': bool(item.get('date_inferred'))})
    return days


async def _write_diary(day: dict, sources: dict, source_times: dict) -> tuple[Path, list[dict]]:
    path = file_for(day['date'])
    async with _write_lock:
        existing = path.read_text(encoding='utf-8') if path.exists() else ''
        if existing:
            content = merge_document(existing, date=day['date'], fields=day['fields'], blocks=day['blocks'])
        else:
            content = new_document(day['date'], day['fields'], day['blocks'])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f'{path.name}.tmp-{os.getpid()}')
        try:
            tmp.write_text(content, encoding='utf-8')
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    rows: list[dict] = []
    source_ids = json.dumps([sources[ref] for ref in day['sources']], ensure_ascii=False)
    source_ts = max((source_times.get(ref, 0) for ref in day['sources']), default=0)
    for key, value in day['fields'].items():
        rows.append({'field_key': key, 'value': json.dumps(value, ensure_ascii=False)})
    for block in day['blocks']:
        rows.append({'field_key': f'block:{block["kind"]}', 'value': block['text']})
    return path, [{**row, 'source_ids': source_ids, 'source_ts': source_ts} for row in rows]


async def _record_fragments(date: str, path: Path, rows: list[dict], date_inferred: bool):
    now = time.time()
    async with get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        for row in rows:
            await db.execute(
                'INSERT OR IGNORE INTO life_diary_fragments '
                '(diary_date,file_path,field_key,value,source_ids,source_ts,date_inferred,status,created_at,updated_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?)',
                (date, str(path), row['field_key'], row['value'], row['source_ids'],
                 row['source_ts'], 1 if date_inferred else 0, 'active', now, now))
        await db.commit()


async def _run_batch() -> bool:
    jobs = await _pending_jobs()
    if not jobs:
        return False
    messages, sources, context, source_times = await _load_messages(jobs)
    if not messages:
        async with get_db() as db:
            await db.execute("UPDATE life_diary_jobs SET status='skipped' WHERE source_id IN "
                             '(' + ','.join('?' * len(jobs)) + ')', [job['source_id'] for job in jobs])
            await db.commit()
        return False
    dates = sorted({datetime.fromtimestamp(job['source_ts']).date().isoformat() for job in jobs})
    prompt = extraction_prompt(_user_name(), messages, context, _existing_diary(dates))
    status, error = 'done', ''
    try:
        parsed = await _call_model(prompt)
        days = _normalize_days(parsed, sources)
        for day in days:
            path, rows = await _write_diary(day, sources, source_times)
            await _record_fragments(day['date'], path, rows, day['date_inferred'])
        if not days:
            log.info('Life diary: no recordable fragment in this batch')
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        status = 'failed' if max(job['attempts'] for job in jobs) + 1 >= 3 else 'pending'
        error = f'{type(exc).__name__}：{exc}'[:500]
        log.warning('Life diary batch failed: %s', error)
    now = time.time()
    delay = 30 if status == 'pending' else 0
    async with get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        for job in jobs:
            attempts = job['attempts'] + 1
            await db.execute(
                'UPDATE life_diary_jobs SET status=?,error=?,attempts=?,next_attempt_at=? '
                'WHERE source_id=?',
                (status, error, attempts, now + delay, job['source_id']))
        await db.commit()
    await _set_state('last_run_at', str(now))
    if error:
        await _set_state('last_error', error)
    elif status == 'done':
        await _set_state('last_error', '')
    if status == 'done':
        local = datetime.fromtimestamp(now)
        if await _daily_due(now):
            await _set_state('last_daily_run', local.date().isoformat())
    await _notify_changed()
    return True


async def _notify_changed():
    try:
        from ws import manager
        await manager.broadcast({'type': 'life_diary_changed'})
    except Exception:
        pass


# ── 查询 ────────────────────────────────────────────────────

async def snapshot(date: str = '') -> dict:
    await ensure_schema()
    now = time.time()
    if not date:
        date = datetime.fromtimestamp(now).date().isoformat()
    path = file_for(date)
    content = ''
    exists = path.exists()
    if exists:
        try:
            content = path.read_text(encoding='utf-8')
        except OSError:
            content = ''
    async with get_db() as db:
        pending = (await (await db.execute(
            "SELECT count(*) FROM life_diary_jobs WHERE status='pending'")).fetchone())[0]
        failed = (await (await db.execute(
            "SELECT count(*) FROM life_diary_jobs WHERE status='failed'")).fetchone())[0]
        rows = await (await db.execute(
            'SELECT diary_date,field_key,value,source_ids,date_inferred,created_at '
            'FROM life_diary_fragments WHERE diary_date=? ORDER BY id', (date,))).fetchall()
    fragments = [{'field_key': row[1], 'value': row[2],
                  'sources': json.loads(row[3]) if row[3] else [],
                  'date_inferred': bool(row[4]), 'created_at': row[5]} for row in rows]
    last_run = await _get_state('last_run_at')
    return {
        'enabled': enabled(),
        'date': date,
        'weekday': weekday_label(date),
        'path': str(path),
        'exists': exists,
        'content': content,
        'fragments': fragments,
        'pending': pending,
        'failed': failed,
        'last_run_at': float(last_run) if last_run else 0,
        'last_error': await _get_state('last_error'),
        'quiet_minutes': quiet_seconds() // 60,
        'model': get_sentinel_config().get('model', ''),
        'root': str(diary_root()),
    }


# ── 后台循环 ────────────────────────────────────────────────

async def _loop():
    while True:
        try:
            _wake.clear()
            await maybe_run_batch()
            await asyncio.wait_for(_wake.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning('Life diary worker unavailable', exc_info=True)
            await asyncio.sleep(5)


async def start():
    global _worker, _wake
    if _worker is not None:
        return
    await ensure_schema()
    async with get_db() as db:
        await db.execute("UPDATE life_diary_jobs SET status='pending' WHERE status='running'")
        await db.commit()
    _wake = asyncio.Event()
    _worker = asyncio.create_task(_loop(), context=contextvars.Context())


async def stop():
    global _worker, _wake
    if _worker:
        _worker.cancel()
        try:
            await _worker
        except (asyncio.CancelledError, Exception):
            pass
    _worker, _wake = None, None
