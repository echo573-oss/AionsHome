# AGENTS.md — AionsHome 工程协作说明

面向在本仓库改代码的 Agent，人也可以读。

## 这份文件的边界

- 只讲**工程约定**：怎么跑、怎么改、哪里别动。
- `aion-chat/GEMINI.md` 是给 Gemini CLI 的人设与行为规则（保持角色扮演），**不是**给编码 Agent 的工程指令。做代码改动时不要因为读到它就切换到角色扮演。
- 用户可见的功能用法在 `aion-chat/README.md` 与 `aion-chat/docs/`，本文件不重复。

## 项目地图

| 位置 | 作用 |
| --- | --- |
| `aion-chat/` | 主服务：FastAPI + SQLite + WebSocket，端口 8080 |
| `aion-chat/routes/` | HTTP / WS 路由，59 个模块、约 508 个端点 |
| `aion-chat/static/` | 前端页面，原生 JS，无框架、无打包步骤 |
| `AionApp/` | Android WebView 外壳（Java） |
| `AionsHome-Visitor-Lounge/` | 独立的访客会客厅服务，有自己的 `pyproject.toml` 与 `tests/` |
| `AionPet/` | 桌宠素材与 manifest |
| `public/` | 公共静态素材，约 108 MB |
| `vendor/` | 离线安装用的 wheel 包，离线安装脚本依赖它 |
| `scripts/` | 运维脚本 |

## 环境与启停

- Python 3.11，虚拟环境在 `.venv/`（**不在仓库内**，需先跑 `一键安装环境.bat`）。
- 启动：`一键启动.bat`（内部调用 `aion-chat/restart-home-qq.ps1`）。
- 停止：`一键关闭AionsHome.bat` 或 `scripts/stop-aionshome.ps1`。
- 服务地址：`http://localhost:8080`。

## 测试

一条命令：

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-tests.ps1
```

| 用法 | 说明 |
| --- | --- |
| 默认 | 只跑 Python（stdlib unittest） |
| `-Suite pytest` | 只跑依赖 pytest 的那批模块 |
| `-Suite js` | 只跑 JavaScript |
| `-Suite all` | unittest + pytest + JavaScript |
| `-Name test_reply_timing` | 单个模块，最快的循环 |
| `-Filter "test_theater*"` | 按文件名子集 |
| `-Quiet` / `-FailFast` | 只出摘要 / 首个失败即停 |

- Python 侧主体是 **unittest**；但有一批模块用 `@pytest.fixture` / 参数化，**必须由 pytest 收集才会执行**，unittest 只会把它们报成导入错误。`-Suite pytest` 会自动挑出这些模块（扫描 `import pytest`），不依赖写死的清单。
- **pytest 已装进 `.venv`（9.2.0），但不在 `vendor/` 里**，所以新机器离线安装后需要联网补装一次，否则 `-Suite pytest` 无输出、unittest 侧继续报 9 个导入错误。
- JavaScript 侧用 **node:test**（Node 24 内置 runner），不是 jest / vitest。
- 规模：221 个 `test_*.py` + 92 个 `test_*.js`。
  - unittest 全量约 1751 个用例、9 分钟
  - pytest 那批实测收集 **138 个用例**，约 20 秒，2026-11-06 结果 137 passed / 1 failed
- **基线不是全绿**。unittest 侧 34 failures + 16 errors，多数是前端契约漂移（缓存版本号、CSS 数值）与文案断言过时；pytest 侧只剩 `test_lounge_visit.py::test_overall_timeout_finishes_started_visit_and_disconnects`（期望 `request_timeout`、实得 `cancelled`，需判断是代码还是测试该改）。判断是否引入回归，看「改动前后同一个模块的对比」，不要指望一次全绿。
- **两个手工模拟工具原先叫 `test_simulate_home.py` / `test_simulate_outside.py`**，因文件名被 discover 收集、导入即发真实心跳。现已改名为 `simulate_home.py` / `simulate_outside.py` 并加 `__main__` 守卫，**只能手动执行**，不要再改回 `test_` 前缀。
- **⚠️ 跑全量前先停服务**：`data/` 下有多处会被测试写入，临时数据库只重定向了 `chat.db`。2026-11-06 实测：在服务运行中跑全量，`data/location_config.json` 与 `data/location_status.json` 被真实改写。
- **不要在测试里直接用 `sqlite3` 连 `config.DB_PATH`**：测试进程里 `DB_PATH` 已指向临时目录，但 schema 不存在，要先 `await init_db()`。用 `database.get_db()` 而不是 `sqlite3.connect()`。
- **图片合并类测试的时间窗口**：生产 `image_wait_seconds` 是 60–90 秒，测试里曾用 0.04–0.08 秒，那比一次同步 SQLite 写入还短，机器一忙就假失败。已放宽到 2.0 秒（回复由文本触发 `ready.set()` 立即释放，不增加耗时）。
- **测试库是隔离的**：`config.py` 的 `_is_test_process()` 检测到 unittest / pytest 时会把 `DB_PATH` 指向临时目录，所以正常跑测试不会动 `aion-chat/data/chat.db`。但 `data/` 下其他目录（`uploads/`、`settings.json` 等）仍指向真实位置，会写这些的测试要逐个确认。

## 数据安全（最重要）

- `aion-chat/data/` 是这台机器上**真实的聊天记录、记忆与照片**。不要重置、清空、迁移，也不要"顺手整理"。
- `aion-chat/data/chat.db` 是真实用户数据。任何 schema 改动先备份，并在副本上验证。
- 不要把 `data/` 内容、日志、`.env`、证书或凭据提交进 git。
- 要跑会写数据的实验，先复制到临时目录，不要在真实库上试。

## 改代码的约定

- 注释与用户可见文案用中文，代码标识符用英文。
- 页面保持 HTML / CSS / JS 分离；共享样式与工具在 `common.css` / `common.js`。
- 新接口放 `routes/`，不要往 `main.py` 里继续堆。
- 改业务行为默认配测试。
- 提交信息：`<type>: <中文一句话>`，type 用 `feat` / `fix` / `refactor` / `docs` / `test` / `chore`。

## 已知结构陷阱（动手前先确认路径）

1. **29 对同名模块**：`aion-chat/X.py` 与 `aion-chat/routes/X.py` 同时存在，例如 `chatroom`、`kitchen`、`schedule`、`voice`、`xhs_lite`。业务逻辑多半在根目录那份，路由在 `routes/`。改之前先确认打开的是哪一个。
2. **两个巨型文件**：`routes/chat.py`（约 186 KB、24 个端点）与 `routes/chatroom.py`（约 196 KB、42 个端点）。改动尽量局部化，不要顺手整文件重构。
3. **行数与 `innerHTML` 计数不是重构依据**：它们只适合用来找审查重点，不能单独决定重构范围。

## 当前不要做的三件事

1. **不要把 `vendor/` 移出 git**。离线安装依赖它；要先有替代离线包，并在干净的 Windows 环境完成一次断网安装验证。
2. **不要改 SQLite 日志模式或 `synchronous`**。属于需单独决定的事：先在数据库副本上对比并发读写与恢复流程。
3. **不要改 `/static/*` 的缓存策略**。Android 侧已有 SHA-256 资源清单与本地缓存，先测清楚冷启动和二次打开各下载了什么再决定改哪一层。

## 当前状态（2026-11-06）

**在途修复，别碰**：5 个未提交文件 —— `aion-chat/routes/chat.py`、`static/chat.html`、`static/chat.js`、`static/memory-library.js`、`static/memory.html`，内容是最近的同秒消息翻页与记忆原文跳转修复。动这几个文件前先跟维护者确认，避免覆盖在途改动。

**同一批未提交的工程改动**（与本文件同期产生，可独立审阅）：

- 新增 `AGENTS.md`（本文件）、`scripts/run-tests.ps1`、`aion-chat/requirements-dev.txt`
- 新增回归测试：`test_message_paging_cursor.py`、`test_ondemand_tts.py`、`test_memory_source_jump.js`
- `test_simulate_home.py` → `simulate_home.py`，`test_simulate_outside.py` → `simulate_outside.py`（改名 + `__main__` 守卫）
- `test_svakom_ai.py` 补上 `ai_name` 参数；`test_qq_social.py` 修好 PowerShell 替身并放宽图片合并时间窗口
- `.gitignore` 追加本机临时产物与本地备份规则
