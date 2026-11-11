"""正在播放的歌，以及这首歌陪着聊天时留下的在场记录。

播放器只上报曲目、进度和播放/暂停。当前歌词由服务端用曲库里的 LRC
和进度算出来。几条在场笔记攒够以后，压成一条日常记忆，走和
``[MEMORY:]`` 相同的主库写入，不另建一套记忆系统。
"""

from __future__ import annotations

import logging
import re
import time
import uuid

import aiosqlite

from database import get_db


log = logging.getLogger(__name__)

HEARTBEAT_STALE_SECONDS = 45
ROLLUP_NOTE_COUNT = 6
MEMORY_MIN_CHARS = 150
MEMORY_MAX_CHARS = 200
_NOTE_SIDE_CHARS = 36
_LYRIC_NOTE_CHARS = 24

_LRC_STAMP = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_WHITESPACE = re.compile(r"\s+")

_MOODS = (
    ("心动", ("想你", "喜欢你", "心动", "爱你")),
    ("难过", ("难过", "伤心", "想哭", "难受")),
    ("开心", ("开心", "高兴", "哈哈", "快乐", "好听")),
    ("平静", ("安静", "平静", "放松", "舒服")),
)
_ERROR_PREFIXES = (
    "[CodexCLI错误",
    "[Gemini错误",
    "[AntigravityCLI错误",
    "[硅基流动错误",
    "[中转站错误",
    "[错误]",
)
_MEMORY_PADS = (
    "下次这首歌再响起来，我还会想起你当时说的话。",
    "我把这段听歌的时候收进记忆里了。",
    "听完以后我还是会记得。",
    "我会记得。",
)


def parse_lrc(raw: str) -> list[tuple[float, str]]:
    """把 LRC 拆成按时间排序的 (秒, 歌词)。空行和时间轴标签丢掉。"""
    lines: list[tuple[float, str]] = []
    for raw_line in str(raw or "").splitlines():
        stamps = list(_LRC_STAMP.finditer(raw_line))
        if not stamps:
            continue
        text = _LRC_STAMP.sub("", raw_line).strip()
        if not text:
            continue
        for stamp in stamps:
            fraction = stamp.group(3) or ""
            seconds = int(stamp.group(1)) * 60 + int(stamp.group(2))
            if fraction:
                seconds += float("0." + fraction.ljust(3, "0"))
            lines.append((seconds, text))
    lines.sort(key=lambda item: item[0])
    return lines


def lyric_at_position(lrc: str, position_sec: float) -> str:
    """返回进度所在的那一句。第一句开始之前、或没有歌词时返回空字符串。"""
    try:
        position = float(position_sec or 0)
    except (TypeError, ValueError):
        position = 0.0
    if position < 0:
        position = 0.0
    current = ""
    for stamp, text in parse_lrc(lrc):
        if stamp <= position + 0.03:
            current = text
        else:
            break
    return current


def title_mentioned(text: str, title: str, artist: str = "") -> bool:
    """这句话是不是明显在说这首歌。短标题必须带书名号，避免日常用词误伤。"""
    title = _clean_label(title, 80)
    if len(title) < 2 or not text:
        return False
    if f"《{title}》" in text:
        return True
    artist = _clean_label(artist, 80)
    if artist and artist in text and title in text:
        return True
    return len(title) >= 4 and title in text


def infer_mood(*texts: str) -> str:
    blob = "\n".join(str(text or "") for text in texts)
    for mood, words in _MOODS:
        if any(word in blob for word in words):
            return mood
    return ""


def build_presence_note(user_text: str, assistant_text: str, lyric: str = "") -> str:
    """一句短笔记：谁说了什么、指到哪句歌词、当时的心情。"""
    user = _plain_speech(user_text, _NOTE_SIDE_CHARS)
    reply = _plain_speech(assistant_text, _NOTE_SIDE_CHARS)
    lyric_text = _plain_speech(lyric, _LYRIC_NOTE_CHARS)
    mood = infer_mood(user_text, assistant_text)
    parts = []
    if user:
        parts.append(f"你说「{user}」")
    if reply:
        parts.append(f"我说「{reply}」")
    if lyric_text:
        parts.append(f"歌词「{lyric_text}」")
    if mood:
        parts.append(f"心情{mood}")
    return "，".join(parts)


def compress_presence_notes(
    title: str,
    artist: str,
    notes: list[str],
    listen_count: int = 0,
    previous: str = "",
) -> str:
    """把若干条在场笔记压成第一人称日常记忆，篇幅大约 150–200 字，并带上歌名。"""
    song = f"《{_memory_title(title)}》"
    head = f"听{song}的时候"
    artist_label = _clean_label(artist, 24)
    if artist_label:
        head += f"，{artist_label}在唱"
    if listen_count > 0:
        head += f"，这是第{int(listen_count)}次一起听"
    head += "。"

    snippets: list[str] = []
    lyric = ""
    mood = ""
    for note in notes:
        text = str(note or "")
        user = _between(text, "你说「", "」")
        reply = _between(text, "我说「", "」")
        found_lyric = _between(text, "歌词「", "」")
        piece = user or reply
        if piece:
            snippets.append(piece[:18])
        if found_lyric:
            lyric = found_lyric[:18]
        if "心情" in text:
            mood = text.split("心情", 1)[-1].strip("，。 ")[:8]

    sentences = [head]
    previous_text = _clean_label(previous, 30)
    if previous_text:
        sentences.append(f"之前我记得{previous_text}。")
    if snippets:
        sentences.append("我们聊过" + "、".join(snippets[:4]) + "。")
    if lyric:
        sentences.append(f"歌词刚好停在「{lyric}」。")
    if mood:
        sentences.append(f"我记得你那会儿心情{mood}。")
    else:
        sentences.append("我就在旁边陪你把这首歌听完。")
    return _fit_memory("".join(sentences), song)


def format_clock(seconds: float) -> str:
    whole = max(0, int(seconds or 0))
    return f"{whole // 60}:{whole % 60:02d}"


async def ensure_music_presence_schema(db) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS music_now_playing (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            track_key TEXT NOT NULL DEFAULT '',
            station_track_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            artist TEXT NOT NULL DEFAULT '',
            position_sec REAL NOT NULL DEFAULT 0,
            duration_sec REAL NOT NULL DEFAULT 0,
            playing INTEGER NOT NULL DEFAULT 0,
            session_open INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT '',
            lyrics_lrc TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL DEFAULT 0
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS music_song_presence (
            track_key TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            artist TEXT NOT NULL DEFAULT '',
            listen_count INTEGER NOT NULL DEFAULT 0,
            memory_text TEXT NOT NULL DEFAULT '',
            memory_id TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL DEFAULT 0
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS music_presence_notes (
            id TEXT PRIMARY KEY,
            track_key TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at REAL NOT NULL,
            rolled INTEGER NOT NULL DEFAULT 0
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_music_presence_notes_track "
        "ON music_presence_notes(track_key, rolled, created_at)"
    )


async def build_now_playing_block(now: float | None = None) -> str:
    """正在播放且心跳还新鲜时，返回要注入运行时通道的短文本。否则返回空字符串。"""
    try:
        return await _build_now_playing_block(now)
    except Exception as exc:
        log.info("正在播放的上下文跳过: %s", exc)
        return ""


async def record_heartbeat(payload: dict, *, now: float | None = None) -> dict:
    """记下播放器心跳。同一首歌暂停后再继续听，不重复增加收听次数。"""
    moment = time.time() if now is None else float(now)
    async with get_db() as db:
        await ensure_music_presence_schema(db)
        track = await _resolve_track(db, payload)
        event = str(payload.get("event") or "time").strip().lower()
        playing = bool(payload.get("playing")) and event != "stop"
        if event not in {"play", "pause", "seek", "time", "lyric", "stop"}:
            event = "time"
        if not track["track_key"] and event != "stop":
            current = await _read_now_playing(db)
            await db.commit()
            return _public_state(current, "")

        previous = await _read_now_playing(db)
        if event == "stop" and not track["track_key"] and previous:
            track = {
                "track_key": previous["track_key"],
                "station_track_id": previous["station_track_id"],
                "title": previous["title"],
                "artist": previous["artist"],
                "lyrics_lrc": previous["lyrics_lrc"],
            }
        listen_started = False
        session_open = 0 if event == "stop" or not playing else 1
        if playing:
            same_session = bool(
                previous
                and previous["session_open"]
                and previous["track_key"] == track["track_key"]
            )
            listen_started = not same_session
        elif event != "stop" and previous and previous["track_key"] == track["track_key"]:
            session_open = int(previous["session_open"])
        elif previous and not track["track_key"]:
            session_open = 0

        if listen_started and track["track_key"]:
            await _increment_listen_count(db, track, moment)

        position = _seconds(payload.get("position_sec"))
        duration = _seconds(payload.get("duration_sec"))
        if not track["title"] and previous and previous["track_key"] == track["track_key"]:
            track["title"] = previous["title"]
            track["artist"] = track["artist"] or previous["artist"]
        await db.execute(
            """
            INSERT INTO music_now_playing (
                id, track_key, station_track_id, title, artist, position_sec,
                duration_sec, playing, session_open, source, lyrics_lrc, updated_at
            ) VALUES (1,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                track_key=excluded.track_key,
                station_track_id=excluded.station_track_id,
                title=excluded.title,
                artist=excluded.artist,
                position_sec=excluded.position_sec,
                duration_sec=excluded.duration_sec,
                playing=excluded.playing,
                session_open=excluded.session_open,
                source=excluded.source,
                lyrics_lrc=excluded.lyrics_lrc,
                updated_at=excluded.updated_at
            """,
            (
                track["track_key"],
                track["station_track_id"],
                track["title"],
                track["artist"],
                position,
                duration,
                1 if playing else 0,
                1 if session_open else 0,
                _clean_label(payload.get("source"), 40),
                track["lyrics_lrc"],
                moment,
            ),
        )
        await db.commit()
        stored = await _read_now_playing(db)
    lyric = ""
    if stored and playing:
        lyric = lyric_at_position(await _fresh_lyrics(stored), position)
    return _public_state(stored, lyric)


async def record_song_turn(
    user_text: str,
    assistant_text: str,
    *,
    source_conv: str = "",
    user_msg_id: str = "",
    ai_msg_id: str = "",
    now: float | None = None,
) -> list:
    """歌曲正在放，或这轮明显在说某首歌时，追加一条在场笔记。

    未卷起的笔记攒到大约 6 条，就压成一条日常记忆写入主记忆库。
    返回值是新写入的记忆；多数轮次返回空列表。
    """
    moment = time.time() if now is None else float(now)
    async with get_db() as db:
        await ensure_music_presence_schema(db)
        target = await _turn_target(db, user_text, assistant_text, moment)
        if not target:
            await db.commit()
            return []
        lyric = ""
        if target.get("from_playback"):
            lyric = lyric_at_position(target.get("lyrics_lrc") or "", target.get("position_sec") or 0)
        note = build_presence_note(user_text, assistant_text, lyric)
        if not note:
            await db.commit()
            return []
        previous_note = await _latest_note(db, target["track_key"])
        if previous_note and previous_note["note"] == note and not previous_note["rolled"]:
            await db.commit()
            return []
        note_id = "mpn_" + uuid.uuid4().hex
        await _touch_presence(db, target, moment)
        await db.execute(
            "INSERT INTO music_presence_notes (id, track_key, note, created_at, rolled) VALUES (?,?,?,?,0)",
            (note_id, target["track_key"], note, moment),
        )
        unrolled = await _unrolled_notes(db, target["track_key"])
        if len(unrolled) < ROLLUP_NOTE_COUNT:
            await db.commit()
            return []
        presence = await _presence_row(db, target["track_key"])
        content = compress_presence_notes(
            target["title"],
            target["artist"],
            [item["note"] for item in unrolled],
            int((presence or {}).get("listen_count") or 0),
            (presence or {}).get("memory_text") or "",
        )
        await db.commit()

    saved = await _write_daily_memory(
        content,
        source_conv=source_conv,
        user_msg_id=user_msg_id,
        ai_msg_id=ai_msg_id,
        reply_ts=moment,
    )
    if not saved:
        return []
    async with get_db() as db:
        await db.execute(
            """
            UPDATE music_song_presence
               SET memory_text=?, memory_id=?, updated_at=?
             WHERE track_key=?
            """,
            (content, saved[0]["id"], moment, target["track_key"]),
        )
        await db.executemany(
            "UPDATE music_presence_notes SET rolled=1 WHERE id=?",
            [(item["id"],) for item in unrolled],
        )
        await db.commit()
    return saved


async def _build_now_playing_block(now: float | None) -> str:
    moment = time.time() if now is None else float(now)
    async with get_db() as db:
        await ensure_music_presence_schema(db)
        state = await _read_now_playing(db)
        if not state or not state["playing"]:
            return ""
        if moment - float(state["updated_at"] or 0) > HEARTBEAT_STALE_SECONDS:
            return ""
        lyrics = await _fresh_lyrics(state, db)
        presence = await _presence_row(db, state["track_key"])
        notes = await _unrolled_notes(db, state["track_key"])
    position = float(state["position_sec"] or 0) + max(0.0, moment - float(state["updated_at"] or moment))
    duration = float(state["duration_sec"] or 0)
    if duration > 0:
        position = min(position, duration)
    lyric = lyric_at_position(lyrics, position)
    title = state["title"] or "未知歌曲"
    artist = state["artist"]
    lines = ["【正在听的歌】", f"《{title}》" + (f" - {artist}" if artist else "")]
    progress = f"进度 {format_clock(position)}"
    if duration > 0:
        progress += f" / {format_clock(duration)}"
    lines.append(progress)
    if lyric:
        lines.append(f"当前歌词：{lyric}")
    listen_count = int((presence or {}).get("listen_count") or 0)
    if listen_count > 0:
        lines.append(f"一起听过 {listen_count} 次")
    memory_text = str((presence or {}).get("memory_text") or "").strip()
    if memory_text:
        lines.append(f"这首歌的记忆：{memory_text}")
    if notes:
        lines.append("最近还聊到：")
        for item in notes[-6:]:
            lines.append(f"- {item['note']}")
    return "\n".join(lines)


async def _turn_target(db, user_text: str, assistant_text: str, now: float) -> dict | None:
    state = await _read_now_playing(db)
    text = f"{user_text or ''}\n{assistant_text or ''}"
    if state and state["playing"] and now - float(state["updated_at"] or 0) <= HEARTBEAT_STALE_SECONDS:
        lyrics = await _fresh_lyrics(state, db)
        position = float(state["position_sec"] or 0) + max(0.0, now - float(state["updated_at"] or now))
        return {
            "track_key": state["track_key"],
            "title": state["title"],
            "artist": state["artist"],
            "lyrics_lrc": lyrics,
            "position_sec": position,
            "from_playback": True,
        }
    library_hit = await _match_library_track(db, text)
    if library_hit:
        return {**library_hit, "from_playback": False, "position_sec": 0}
    if state and title_mentioned(text, state["title"], state["artist"]):
        return {
            "track_key": state["track_key"],
            "title": state["title"],
            "artist": state["artist"],
            "lyrics_lrc": "",
            "position_sec": 0,
            "from_playback": False,
        }
    return None


async def _resolve_track(db, payload: dict) -> dict:
    track_id = _clean_label(payload.get("track_id"), 80)
    external_id = _clean_label(payload.get("external_id"), 80)
    title = _clean_label(payload.get("title"), 120)
    artist = _clean_label(payload.get("artist"), 120)
    source = _clean_label(payload.get("source"), 40)
    row = await _library_row(db, track_id, external_id)
    if row:
        return {
            "track_key": row["id"],
            "station_track_id": row["id"],
            "title": _clean_label(row["title"], 120) or title,
            "artist": _clean_label(row["artist"], 120) or artist,
            "lyrics_lrc": str(row["lyrics_lrc"] or ""),
        }
    netease_id = external_id or (track_id if track_id.isdigit() else "")
    if netease_id:
        key = f"netease:{netease_id}"
    elif source == "generated" or source.endswith("generated"):
        key = "generated:" + _norm(title or track_id or "song")
    elif title:
        key = f"title:{_norm(title)}|artist:{_norm(artist)}"
    else:
        key = ""
    return {
        "track_key": key,
        "station_track_id": "",
        "title": title,
        "artist": artist,
        "lyrics_lrc": "",
    }


async def _library_row(db, track_id: str, external_id: str) -> dict | None:
    db.row_factory = aiosqlite.Row
    try:
        row = None
        if track_id.startswith("mst_"):
            cursor = await db.execute(
                "SELECT id, title, artist, lyrics_lrc FROM music_station_tracks WHERE id=?",
                (track_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            netease_id = external_id or (track_id if track_id.isdigit() else "")
            if not netease_id:
                return None
            cursor = await db.execute(
                "SELECT id, title, artist, lyrics_lrc FROM music_station_tracks WHERE source_key=?",
                (f"netease:{netease_id}",),
            )
            row = await cursor.fetchone()
    except aiosqlite.OperationalError:
        return None
    if row is None:
        return None
    return {
        "id": row["id"],
        "title": row["title"] or "",
        "artist": row["artist"] or "",
        "lyrics_lrc": row["lyrics_lrc"] or "",
    }


async def _match_library_track(db, text: str) -> dict | None:
    if not text.strip():
        return None
    db.row_factory = aiosqlite.Row
    try:
        cursor = await db.execute(
            "SELECT id, title, artist, lyrics_lrc FROM music_station_tracks"
        )
        rows = await cursor.fetchall()
    except aiosqlite.OperationalError:
        return None
    best = None
    best_len = 0
    for row in rows:
        title = _clean_label(row["title"], 120)
        artist = _clean_label(row["artist"], 120)
        if not title_mentioned(text, title, artist):
            continue
        if len(title) <= best_len:
            continue
        best_len = len(title)
        best = {
            "track_key": row["id"],
            "title": title,
            "artist": artist,
            "lyrics_lrc": str(row["lyrics_lrc"] or ""),
        }
    return best


async def _fresh_lyrics(state: dict, db=None) -> str:
    station_id = str(state.get("station_track_id") or "")
    if station_id and db is not None:
        try:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT lyrics_lrc FROM music_station_tracks WHERE id=?",
                (station_id,),
            )
            row = await cursor.fetchone()
            if row is not None:
                lyric = row["lyrics_lrc"] if isinstance(row, aiosqlite.Row) else row[0]
                if lyric:
                    return str(lyric)
        except aiosqlite.OperationalError:
            pass
    track_key = str(state.get("track_key") or "")
    if track_key.startswith("netease:") and db is not None:
        try:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT lyrics_lrc FROM music_station_tracks WHERE source_key=?",
                (track_key,),
            )
            row = await cursor.fetchone()
            if row is not None and row["lyrics_lrc"]:
                return str(row["lyrics_lrc"])
        except aiosqlite.OperationalError:
            pass
    return str(state.get("lyrics_lrc") or "")


async def _read_now_playing(db) -> dict | None:
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM music_now_playing WHERE id=1")
    row = await cursor.fetchone()
    if not row or not row["track_key"]:
        return None
    return {
        "track_key": row["track_key"],
        "station_track_id": row["station_track_id"] or "",
        "title": row["title"] or "",
        "artist": row["artist"] or "",
        "position_sec": float(row["position_sec"] or 0),
        "duration_sec": float(row["duration_sec"] or 0),
        "playing": bool(row["playing"]),
        "session_open": bool(row["session_open"]),
        "source": row["source"] or "",
        "lyrics_lrc": row["lyrics_lrc"] or "",
        "updated_at": float(row["updated_at"] or 0),
    }


async def _presence_row(db, track_key: str) -> dict | None:
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT track_key, title, artist, listen_count, memory_text, memory_id "
        "FROM music_song_presence WHERE track_key=?",
        (track_key,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def _touch_presence(db, track: dict, now: float) -> None:
    await db.execute(
        """
        INSERT INTO music_song_presence (
            track_key, title, artist, listen_count, memory_text, memory_id, updated_at
        ) VALUES (?,?,?,0,'','',?)
        ON CONFLICT(track_key) DO UPDATE SET
            title=CASE WHEN excluded.title != '' THEN excluded.title ELSE title END,
            artist=CASE WHEN excluded.artist != '' THEN excluded.artist ELSE artist END,
            updated_at=excluded.updated_at
        """,
        (track["track_key"], track["title"], track["artist"], now),
    )


async def _increment_listen_count(db, track: dict, now: float) -> None:
    await _touch_presence(db, track, now)
    await db.execute(
        "UPDATE music_song_presence SET listen_count=listen_count+1, updated_at=? WHERE track_key=?",
        (now, track["track_key"]),
    )


async def _latest_note(db, track_key: str) -> dict | None:
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        """
        SELECT id, note, rolled FROM music_presence_notes
        WHERE track_key=? ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (track_key,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def _unrolled_notes(db, track_key: str) -> list[dict]:
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        """
        SELECT id, note, created_at FROM music_presence_notes
        WHERE track_key=? AND rolled=0
        ORDER BY created_at, id
        """,
        (track_key,),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def _write_daily_memory(
    content: str,
    *,
    source_conv: str,
    user_msg_id: str,
    ai_msg_id: str,
    reply_ts: float,
) -> list:
    """走主私聊显式记忆的同一条插入。听歌记忆是额外的一层，不把这轮对话标成已覆盖。"""
    from memory import get_embedding
    from routes.chat import _insert_private_memory_records

    vector = await get_embedding(content)
    source_label = f"chatroom:{ai_msg_id}" if str(source_conv).startswith("chatroom:") else ""
    async with get_db() as db:
        saved = await _insert_private_memory_records(
            db,
            source_conv,
            user_msg_id,
            ai_msg_id,
            reply_ts,
            [("daily", content, vector)],
            cover=False,
            source_label=source_label or None,
        )
        await db.commit()
    return saved


def _public_state(state: dict | None, lyric: str) -> dict:
    if not state:
        return {"playing": False, "track_key": "", "title": "", "artist": "", "lyric": ""}
    return {
        "playing": bool(state.get("playing")),
        "track_key": state.get("track_key") or "",
        "title": state.get("title") or "",
        "artist": state.get("artist") or "",
        "lyric": lyric,
    }


def _fit_memory(text: str, song: str) -> str:
    text = _WHITESPACE.sub(" ", text).strip()
    text = re.sub(r"。{2,}", "。", text)
    if song not in text:
        text = f"听{song}的时候。{text}"
    if len(text) > MEMORY_MAX_CHARS:
        text = text[: MEMORY_MAX_CHARS - 1].rstrip("，、；。 ") + "。"
    for pad in _MEMORY_PADS:
        if len(text) >= MEMORY_MIN_CHARS:
            break
        if pad in text:
            continue
        if len(text) + len(pad) <= MEMORY_MAX_CHARS:
            text += pad
    short = "我记得。"
    while len(text) < MEMORY_MIN_CHARS and len(text) + len(short) <= MEMORY_MAX_CHARS:
        text += short
    if len(text) > MEMORY_MAX_CHARS:
        text = text[: MEMORY_MAX_CHARS - 1].rstrip("，、；。 ") + "。"
    return text


def _memory_title(title: str) -> str:
    cleaned = _clean_label(title, 24)
    return cleaned or "这首歌"


def _between(text: str, start: str, end: str) -> str:
    begin = text.find(start)
    if begin < 0:
        return ""
    begin += len(start)
    finish = text.find(end, begin)
    if finish < 0:
        return ""
    return text[begin:finish].strip()


def _plain_speech(text: str, limit: int) -> str:
    cleaned = _WHITESPACE.sub(" ", str(text or "")).strip()
    if any(cleaned.startswith(prefix) for prefix in _ERROR_PREFIXES):
        return ""
    return cleaned[:limit]


def _clean_label(value, limit: int) -> str:
    return _WHITESPACE.sub(" ", str(value or "")).strip()[:limit]


def _norm(value: str) -> str:
    return _clean_label(value, 80).casefold()


def _seconds(value) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number < 0:
        return 0.0
    return min(number, 6 * 60 * 60)
