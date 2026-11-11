"""Table talk only. Dice, tiles, and tasks always come from the sidecar engine."""
from __future__ import annotations

import asyncio
import re
import time
import uuid

from expressive_voice_markup import strip_voice_blocks

from . import store


def spoken_text(text: str) -> str:
    text = re.sub(r'[\[【](?:心里嘀咕|内心嘀咕|内心独白|心理活动)\s*[：:][^\]】]*(?:[\]】]|$)', '', text)
    text = re.sub(r'<(think|thinking)>[\s\S]*?(?:</\1>|$)', '', text, flags=re.I)

    def voice(match):
        payload = match.group(1)
        content = re.search(r'(?:^|[|｜])\s*(?:内容|台词)\s*[=：:]\s*(.*)', payload, flags=re.S)
        return content.group(1).strip() if content else ('' if '方向=' in payload else payload.strip())

    text = re.sub(r'[\[【]语音\s*[|｜:：]\s*([^\]】]*)(?:[\]】]|$)', voice, text)
    text = strip_voice_blocks(text)
    text = re.sub(r'\[PAT\s*[：:]\s*[^\]]*(?:\]|$)', '', text, flags=re.I)
    return text.strip()


def table_context_text(text: str) -> str:
    text = re.sub(
        r'(?m)^\[(?:能力与工具规则|系统能力)\][ \t]*\r?\n[\s\S]*?(?=^\[[^\]\n:：|]+\][ \t]*$|\Z)',
        '', text,
    )
    return spoken_text(text)


def names() -> dict:
    from chatroom import get_chatroom_names
    user, main, companion = get_chatroom_names()
    return {"user": user, "aion": main, "connor": companion}


def _scene(game: dict) -> str:
    last = game.get("last") or {}
    pieces = [
        f"对手是{game.get('opponent_name') or game.get('p2_name') or '伴侣'}。",
        f"当前轮到：{last.get('next_turn') or last.get('turn') or game.get('turn') or '未知'}。",
    ]
    if last.get("say"):
        pieces.append("最近一句结算：" + str(last["say"])[:400])
    if last.get("action_needed"):
        pieces.append(f"此刻等待的操作：{last['action_needed']}。")
    return "\n".join(pieces)


async def model_text(actor: str, game: dict, instruction: str) -> str:
    from autonomy import _actor_context, _call_actor
    labels = names()
    context = await _actor_context(actor, 24)
    context = [{**message, "content": table_context_text(message["content"])} for message in context]
    context.insert(0, {"role": "system", "content": (
        "此刻在小家的棋盘室里，保留人设、关系和之前聊天的记忆，根据真实棋局自然闲聊。"
        "只输出说给在场人听的普通文字，不使用工具指令、语音标记或内心旁白；"
        "不要写旁白、动作、神态、心理描写或舞台提示；不要用括号、星号补充这些内容。"
        "不要替引擎掷骰、编造格子或任务内容，也不要替对方做决定。"
    )})
    recent = "\n".join(
        f"{labels.get(m['sender'], m['sender'])}：{table_context_text(m['text'])}"
        for m in (game.get("messages") or [])[-12:]
    )
    context.append({"role": "user", "content": (
        "[当前场景：小家棋盘室]\n"
        f"你是{labels[actor]}。这局在场的是{labels['user']}和{labels[actor]}。\n"
        "棋盘、金币和任务都由游戏引擎结算，下面是已经发生的事实，只需自然聊天。说话简短，通常一两句。\n"
        + _scene(game) + "\n最近桌边对话：\n" + (recent or "（还没有人说话）") + "\n\n" + instruction
    )})
    return (await asyncio.wait_for(_call_actor(actor, context), 150)).strip()


def append_message(sid: str, sender: str, text: str) -> dict | None:
    if sender != "user":
        text = spoken_text(text)
        if not text:
            return None
    message = {
        "id": "velvet_" + uuid.uuid4().hex,
        "sender": sender,
        "text": text[:2000],
        "created_at": time.time(),
    }
    store.append_message(sid, message)
    return message


_CHAT_LOCKS: dict[str, asyncio.Lock] = {}


async def chat(sid: str, text: str) -> dict:
    if not text.strip():
        raise ValueError("先写点想说的话")
    game = store.get(sid)
    if not game:
        raise ValueError("这局还不存在")
    actor = game.get("opponent") or "aion"
    append_message(sid, "user", text)
    lock = _CHAT_LOCKS.setdefault(sid, asyncio.Lock())
    async with lock:
        try:
            reply = await model_text(actor, store.get(sid), "用户刚在桌边说话，请自然回应，通常一到三句。")
            if reply:
                append_message(sid, actor, reply)
                store.upsert(sid, chat_error="")
        except Exception:
            store.upsert(sid, chat_error="这次没能接上话，棋局仍可以继续")
    return store.get(sid)
