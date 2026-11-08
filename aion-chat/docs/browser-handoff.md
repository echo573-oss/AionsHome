# 浏览器自动化｜第一步：控制权交接骨架

这次先交付底层控制权协议和离线测试，**尚未接入真实 Chrome / Playwright / Connor-BrowserTool，也没有在 Aion 页面开放按钮**。

## 仓库检查

2026-11-08 的远程施工分支 `feat/chat-mcp-readonly` 中没有 `Connor-BrowserTool/` 目录；现有 `Connor-Codex/` 只有 `package.json`。无法据此确认本地 BrowserTool 的接口、Chrome 启动方式、用户资料路径和现有接管机制。先不要另造第二套 Chrome 自动化，等本地执行 Agent 审查后实现适配器。

## 已实现

`browser_handoff.py` 定义一个浏览器任务的状态机和 `BrowserDriver` 适配器接口：

- 人类明确发起任务才创建；同一服务进程同时最多一个任务，避免抢占本机浏览器。
- 只接受公开 HTTPS URL，暂不支持内网、IP、普通 HTTP、端口和携带用户名/密码的网址；后续驱动器还须检查重定向和 DNS 结果。
- 观察 `watch` 与接管 `take_control` 分离；只看进度不会暂停 Agent。
- `take_control` 等当前操作完成再暂停。暂停期间禁止 Agent 导航。
- `return_control` 由人明确发起；续接当前网页，只观察，不重放历史点击。交还失败则维持人工控制。
- 每个请求绑定角色、聊天范围和任务 ID，防止另一位角色或别的聊天抢走会话。
- 浏览器状态对外只返回 HTTPS 域名和页面标题，不返回 URL 参数、页面内容、Cookie 或密码。

目前只定义 `open/navigate/observe/pause_agent/resume_agent/close`，没有执行 JavaScript、自动点击、自动提交、登录、上传、下载和填表。浏览器驱动器没有连接前，不会打开任何网页。

## 如何接下一块

本地 Agent 先检查 `Connor-BrowserTool` 及其与 AionsHome 的通信接口，尽量复用现有浏览器的进程、会话和用户接管能力。适配器必须：

1. 只接管属于该任务的隔离页面；严禁接管用户当前常用 Chrome 资料目录。
2. 保证暂停后没有后台自动点击、异步重试或并行导航。
3. 人工交还后重新观察页面及登录状态，且不能自动重复上次提交。
4. 提供由浏览器执行器产出的、**不包含表单明文值**的状态/截图；不将整个 DOM、网页脚本执行权限交给模型。
5. 确保独立浏览器任务退出后释放进程与锁；空闲会话应能关闭。
6. 记录每步耗时与失败类别，不写 Cookie、Token、URL query 或密码。
7. 用户在 Aion 中能区分「只看进度」「接管操作」「交还继续」，在手机端同样可用。

## 测试

`test_browser_handoff.py` 为隔离假浏览器测试：无真实网页、密码、网络或用户数据。包含只看不接管、暂停后阻断操作、交还后不重放、跨角色隔离、并发操作串行化、浏览器启动失败时释放锁、URL 安全检查。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-tests.ps1 -Name test_browser_handoff
```

**验收前提**：待本地适配器接上后，先用公开测试页面演示观看、接管、交还，再选真实常用网站对比成功率。暂不开放密码库与浏览器的对接，避免填写进可被 Agent 读取的 DOM 后泄露。
