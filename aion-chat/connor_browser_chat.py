"""Connor 聊天中对独立浏览器的安全调度与控制。

仅支持 Connor 角色在私聊/群聊中调用。优先复用 Connor-BrowserTool (8787)。
通过 BrowserHandoff 严格管控控制权：人工接管期间阻断一切 Agent 动作。
不接触密码库。
"""
from __future__ import annotations

import re
from typing import Any

import browser_handoff
from browser_handoff import (
    BrowserControlError,
    BrowserHandoff,
    get_connor_browser_handoff,
)


_URL_PATTERN = re.compile(r"https://[^\s<>\"'()]+", re.I)

# 活跃会话记录表: (scope_type, scope_id) -> session_id
_ACTIVE_SESSIONS: dict[tuple[str, str], str] = {}


def _get_active_session_id(scope_type: str, scope_id: str) -> str | None:
    return _ACTIVE_SESSIONS.get((scope_type, scope_id))


def _set_active_session_id(scope_type: str, scope_id: str, session_id: str | None) -> None:
    key = (scope_type, scope_id)
    if session_id:
        _ACTIVE_SESSIONS[key] = session_id
    else:
        _ACTIVE_SESSIONS.pop(key, None)


async def execute_connor_browser_action(
    action: str,
    *,
    scope_type: str = "chatroom",
    scope_id: str = "connor_1v1",
    actor: str = "connor",
    url: str = "",
    x: int | None = None,
    y: int | None = None,
    text: str = "",
    handoff: BrowserHandoff | None = None,
) -> dict:
    """执行单个 Connor 浏览器动作，返回结构化结果。"""
    if actor != "connor":
        return {"ok": False, "error": "当前浏览器工具仅授权 Connor 使用"}

    ctrl = handoff or browser_handoff.get_connor_browser_handoff()
    session_id = _get_active_session_id(scope_type, scope_id)

    try:
        if action in ("open", "begin"):
            if not url:
                return {"ok": False, "error": "请提供要打开的有效 HTTPS 网址"}
            if session_id:
                # 若已存在活跃会话，执行导航
                res = await ctrl.navigate(session_id, actor, scope_type, scope_id, url)
                return {"ok": True, "action": "navigate", "session": res, "message": f"已在当前浏览器切换至：{res.get('title') or res.get('url')}"}
            # 开启新会话
            res = await ctrl.begin(actor, scope_type, scope_id, url)
            _set_active_session_id(scope_type, scope_id, res["session_id"])
            return {
                "ok": True, "action": "begin", "session": res,
                "message": f"已为 Connor 开启独立浏览器并打开：{res.get('title') or res.get('url')}。当前处于 Connor 控制模式；如需人工接管请在聊天中说「接管网页」。",
            }

        if action == "navigate":
            if not session_id:
                return {"ok": False, "error": "当前没有正在运行的浏览器任务，请先指定网址打开"}
            if not url:
                return {"ok": False, "error": "请提供要导航的有效 HTTPS 网址"}
            res = await ctrl.navigate(session_id, actor, scope_type, scope_id, url)
            return {"ok": True, "action": "navigate", "session": res, "message": f"已导航至：{res.get('title') or res.get('url')}"}

        if action in ("watch", "state", "observe"):
            if not session_id:
                return {"ok": False, "error": "当前没有运行中的浏览器任务"}
            res = await ctrl.watch(session_id, actor, scope_type, scope_id)
            state_label = "Connor 操作中" if res.get("state") == "agent" else ("人工接管中" if res.get("state") == "human" else res.get("state"))
            return {"ok": True, "action": "watch", "session": res, "message": f"当前网页：{res.get('title') or res.get('url')}（状态：{state_label}）"}

        if action in ("read", "text"):
            if not session_id:
                return {"ok": False, "error": "当前没有运行中的浏览器任务"}
            content = await ctrl.read_text(session_id, actor, scope_type, scope_id)
            truncated = content[:2500] + ("\n...(内容已截断)..." if len(content) > 2500 else "")
            return {"ok": True, "action": "text", "text": truncated, "message": f"页面文字提取完成：\n{truncated}"}

        if action == "screenshot":
            if not session_id:
                return {"ok": False, "error": "当前没有运行中的浏览器任务"}
            res = await ctrl.screenshot(session_id, actor, scope_type, scope_id)
            return {"ok": True, "action": "screenshot", "result": res, "message": f"网页截图已保存至：{res.get('path')}（标题：{res.get('title')}）"}

        if action == "click":
            if not session_id:
                return {"ok": False, "error": "当前没有运行中的浏览器任务"}
            if x is None or y is None:
                return {"ok": False, "error": "点击操作需要提供有效的 x 和 y 坐标"}
            res = await ctrl.click(session_id, actor, scope_type, scope_id, int(x), int(y))
            return {"ok": True, "action": "click", "session": res, "message": f"已在坐标 ({x}, {y}) 点击完成，当前页面：{res.get('title') or res.get('url')}"}

        if action in ("type", "input"):
            if not session_id:
                return {"ok": False, "error": "当前没有运行中的浏览器任务"}
            res = await ctrl.type_text(session_id, actor, scope_type, scope_id, str(text or ""))
            return {"ok": True, "action": "type", "session": res, "message": f"已在页面输入指定文字，当前页面：{res.get('title') or res.get('url')}"}

        if action in ("take_control", "pause"):
            if not session_id:
                return {"ok": False, "error": "当前没有运行中的浏览器任务"}
            res = await ctrl.take_control(session_id, actor, scope_type, scope_id)
            return {
                "ok": True, "action": "take_control", "session": res,
                "message": "【人工已接管浏览器】Connor 的操作已即时暂停。您现在可以自由在桌面独立浏览器窗口中点击、登录或查看；操作完成后对我说「交还网页」，Connor 才会恢复操作。",
            }

        if action in ("return_control", "resume"):
            if not session_id:
                return {"ok": False, "error": "当前没有运行中的浏览器任务"}
            res = await ctrl.return_control(session_id, actor, scope_type, scope_id)
            return {
                "ok": True, "action": "return_control", "session": res,
                "message": f"【控制权已交还】Connor 已恢复对浏览器的控制，重新捕获页面：{res.get('title') or res.get('url')}。Connor 可以继续辅助操作。",
            }

        if action in ("close", "finish"):
            if not session_id:
                return {"ok": True, "message": "当前没有打开的浏览器任务"}
            await ctrl.finish(session_id, actor, scope_type, scope_id)
            _set_active_session_id(scope_type, scope_id, None)
            return {"ok": True, "action": "finish", "message": "独立浏览器已安全关闭，释放进程资源。"}

        return {"ok": False, "error": f"未知的浏览器操作指令: {action}"}

    except BrowserControlError as exc:
        return {"ok": False, "error": str(exc), "session_id": session_id}
    except Exception as exc:
        return {"ok": False, "error": f"浏览器操作执行异常: {type(exc).__name__} ({str(exc)})"}


async def maybe_handle_connor_browser_intent(
    query_text: str,
    *,
    room_id: str = "connor_1v1",
    scope_type: str = "chatroom",
    actor: str = "connor",
    handoff: BrowserHandoff | None = None,
) -> str | None:
    """分析用户对话内容，如果包含 Connor 浏览网页指令，执行并返回结果提示。"""
    if actor != "connor":
        return None

    text = query_text.strip()

    # 1. 人工接管 / 暂停指令
    if any(k in text for k in ("接管网页", "接管浏览器", "暂停connor", "暂停Connor", "我来操作", "[BROWSER:pause]", "[BROWSER:take_control]")):
        res = await execute_connor_browser_action("take_control", scope_type=scope_type, scope_id=room_id, actor=actor, handoff=handoff)
        return res.get("message") or res.get("error")

    # 2. 交还控制权 / 继续指令
    if any(k in text for k in ("交还网页", "交还浏览器", "交还connor", "交还Connor", "继续浏览", "你继续", "[BROWSER:resume]", "[BROWSER:return_control]")):
        res = await execute_connor_browser_action("return_control", scope_type=scope_type, scope_id=room_id, actor=actor, handoff=handoff)
        return res.get("message") or res.get("error")

    # 3. 关闭浏览器指令
    if any(k in text for k in ("关闭网页", "关闭浏览器", "退出浏览器", "[BROWSER:close]", "[BROWSER:finish]")):
        res = await execute_connor_browser_action("finish", scope_type=scope_type, scope_id=room_id, actor=actor, handoff=handoff)
        return res.get("message") or res.get("error")

    # 4. 截图指令
    if any(k in text for k in ("网页截图", "截个图", "截屏", "看下网页画面", "[BROWSER:screenshot]")):
        res = await execute_connor_browser_action("screenshot", scope_type=scope_type, scope_id=room_id, actor=actor, handoff=handoff)
        return res.get("message") or res.get("error")

    # 5. 读取文字指令
    if any(k in text for k in ("读一下网页", "读网页", "网页说了什么", "当前网页内容", "[BROWSER:read]", "[BROWSER:text]")):
        res = await execute_connor_browser_action("read", scope_type=scope_type, scope_id=room_id, actor=actor, handoff=handoff)
        return res.get("message") or res.get("error")

    # 6. 点击指令 (例如: "点击坐标 100 200" 或 "[BROWSER:click 100 200]")
    click_match = re.search(r"(?:点击|click)[^\d]*(\d+)[,\s]+(\d+)", text, re.I)
    if click_match:
        cx, cy = int(click_match.group(1)), int(click_match.group(2))
        res = await execute_connor_browser_action("click", scope_type=scope_type, scope_id=room_id, actor=actor, x=cx, y=cy, handoff=handoff)
        return res.get("message") or res.get("error")

    # 7. 输入指令 (例如: "输入文字 搜索词" 或 "在网页输入 xxx" 或 "[BROWSER:type xxx]")
    type_match = re.search(r"(?:输入文字|在网页输入|键入|type)[:：\s]+([^\n]+)", text, re.I)
    if type_match:
        input_content = type_match.group(1).strip()
        res = await execute_connor_browser_action("type", scope_type=scope_type, scope_id=room_id, actor=actor, text=input_content, handoff=handoff)
        return res.get("message") or res.get("error")

    # 8. 打开网址指令
    url_match = _URL_PATTERN.search(text)
    if url_match and any(w in text for w in ("打开", "访问", "浏览", "看看", "搜", "open", "goto")):
        target_url = url_match.group(0).rstrip("，。！？.,!?")
        res = await execute_connor_browser_action("open", scope_type=scope_type, scope_id=room_id, actor=actor, url=target_url, handoff=handoff)
        return res.get("message") or res.get("error")

    return None
