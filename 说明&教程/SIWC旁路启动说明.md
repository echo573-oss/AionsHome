# SIWC + AionsHome 启动说明（短）

目标：私聊和聊天室 Aion 可用 ChatGPT 计划用量；Connor 角色仍走 Connor-Codex / Codex，不被替换。

## 1. 先开代理（如需）
Clash 本地端口：`http://127.0.0.1:7897`（sidecar 默认走这个）。

## 2. 启动 SIWC sidecar（可常驻）

**推荐**：双击 `D:\AionsHome\一键启动.bat` 会顺带拉起 sidecar（若 `18765` 已在监听则跳过，不重复开）。也可单独启动：
```powershell
cd D:\AionsHome-siwc-sidecar
$env:Path = 'D:\nodejs;' + $env:Path
npm run serve
```
看到 `HTTP listening on http://127.0.0.1:18765` 即可。

- 首次 / 失效时登录：`npm run signin`（浏览器 Continue with ChatGPT，勾选 plan usage）
- 检查：`npm run session`（要 `connected` + `sharing: true`）
- 探测：`npm run verify`（应回 Token sharing works.）

也可在小家「设置 → ChatGPT 计划用量」点 Continue with ChatGPT / 刷新状态 / Manage usage。

## 3. 启动 AionsHome
```powershell
# 或双击
D:\AionsHome\一键启动.bat
```
浏览器打开 http://localhost:8080

## 4. 怎么用
1. 私聊：模型选 `ChatGPT-6.1-Sol` / `ChatGPT-5.6-Sol` / `ChatGPT-6-Astra`
2. 聊天室：Aion 模型线路选同上；第二 AI（Connor）继续选 Codex-* 或房间默认 Codex
3. siliconflow / gemini / codex 原线路保留不动

## 5. 注意
- SIWC 当前文本流式；图片多模态请仍用 Gemini/Codex 视觉线路
- 改完 AionsHome 代码后需重启 `一键启动.bat` 才加载新 provider
- 不要注册/付费 API；用量看 https://chatgpt.com/settings/usage
