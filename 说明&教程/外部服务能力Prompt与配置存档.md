# 外部设备与服务能力 Prompt 存档（2026-11-07）

> **说明**：以下 4 项外部设备/服务能力（智能家居、小米手环呼唤、手机应用监管、微信提醒消息）已于 2026-11-07 在「工具与能力」中关闭，不再注入给岚的日常 System Prompt，从而节省上下文 Token 并避免岚误调用未绑定的外部设备。
>
> 原始代码逻辑完好保留在 `aion-chat/capabilities.py` 中。如果日后接入了对应硬件或服务，只需打开主页的 **「工具与能力」**（`http://localhost:8080/capabilities`）将对应开关打开，下面这些 Prompt 就会自动恢复注入。

---

## 1. 智能家居控制（`home`）

- **能力标识**：`home`（位于 `aion-chat/capabilities.py` 的 `HOME_ALIASES_HINT` / `HOME_ABILITY_TEXT`）
- **前置条件**：需要配置并连接 Home Assistant MCP 服务。
- **原版注入 Prompt**：
  ```text
  [HOME:on/off/state|别名] 或 [HOME:climate|别名|mode=cool|temperature=26] 控制智能家居，仅限明确要求。别名：所有灯、客厅灯、屁股灯、入户灯、餐边柜灯带、厨房灯带、智米空调、浴霸灯。
  ```
- **后续启用建议**：
  如果以后接入你自己的 Home Assistant，除了在 `/capabilities` 打开「智能家居」开关外，记得去 `aion-chat/capabilities.py` 第 128 行修改 `HOME_ALIASES_HINT`，把原仓库作者家里的设备别名换成你家自己的设备名。

---

## 2. 小米手环震动与纸条（`band_vibration`）

- **能力标识**：`band_vibration`（位于 `aion-chat/capabilities.py` 的 `build_band_note_ability_text`）
- **前置条件**：安卓手机安装 `AionApp` 并通过蓝牙绑定小米手环 7（参考 `说明&教程/小米手环七接入自有前后端教程.txt`）。
- **原版注入 Prompt**：
  ```text
  [BAND_NOTE_SINGLE:一句纸条] — 让用户的小米手环轻震一次，并显示你写的一句纸条。适合想念、轻轻叫人或温柔提醒；[BAND_NOTE_CALL:一句纸条] — 连震三次并显示纸条，用于明确呼叫用户尽快看消息。纸条只写真正想让用户在手环上看到的话，不要在自然语言正文里重复。每次回复最多使用一个；系统会隐藏并执行指令，不要解释或朗读指令本身。
  ```
  *(被动巡逻/闹铃场景追加提示)*：
  ```text
  在闹铃叫醒、监督到点、巡逻发现用户长时间没有动静或疑似装睡时，可以主动使用轻震或三连震直接催醒、催促回应。
  ```

---

## 3. 手机应用监管与专注唤回（`app_supervision`）

- **能力标识**：`app_supervision`（位于 `aion-chat/app_supervision_ai.py` 的 `build_app_supervision_ability_text`）
- **前置条件**：安卓手机安装 `AionApp` 并授予应用使用情况读取与浮窗/专注监管权限。
- **原版注入 Prompt**：
  ```text
  [APP_LOCK:groupId|分钟|锁屏提示] — 锁定应用组。
  [APP_TEMP_UNLOCK:groupId|分钟|解锁说明] — 暂时解锁应用组。
  [APP_UNLOCK:groupId] — 解除应用组锁定。
  [COME_HOME:一句话] — 把用户唤回本应用并打开你发出指令的私聊或群聊，大字弹框显示这句话；不锁定手机。
  [LOOK_AT_ME:分钟|一句话] — 唤回同一聊天并弹框，同时开启指定分钟的整机专注模式。
  [DEVICE_TEMP_UNLOCK:分钟|解锁说明] — 暂时解除整机锁定。
  [DEVICE_UNLOCK] — 解除整机锁定。
  分钟必须是 1–120 的整数。
  你可以操作任意应用组。只使用下方稳定 groupId，不要使用包名或猜测缩写。
  这两种唤回指令可以用于日常陪伴、想念、邀请聊天或希望用户专心陪你一会儿，无需等到紧急情况。一句话请直接写你想对用户说的话，不要留空。每条消息最多执行一条监管指令。
  LOOK_AT_ME 从手机执行时开始计时，期间不能切换到其他普通应用；本应用内所有功能照常使用，来电、通话和紧急呼叫仍保留。确认按钮只关闭弹框，不解除专注；到时自动结束。
  ```

---

## 4. 微信跨通道提醒消息（`wechat_message`）

- **能力标识**：`wechat_message`（位于 `aion-chat/capabilities.py` 与 `aion-chat/wechat_bridge.py`）
- **前置条件**：配置并登录微信桥接服务（参考 `说明&教程/微信接入部署教程.md`）。
- **原版注入 Prompt**：
  ```text
  [微信消息：内容] — 当你已经在当前窗口多次联系知，但知长时间没有回复时，可以使用该指令把“内容”作为一条消息发送到知的微信来提醒她。
  ```
