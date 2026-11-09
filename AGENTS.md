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
  - pytest 那批实测收集 **138 个用例**，约 12 秒，138 passed（全绿）
  - JavaScript（`node:test`）共 **329 个用例**，约 44 秒，329 passed（全绿）
- **基线已全绿（2026-11-07）**。历史漂移的缓存版本号断言、CSS/DOM 契约断言、Playwright 硬编码路径、以及 `lounge_visit.py` 的超时判定均已清理完毕，unittest / pytest / JavaScript 三套测试均可作为回归基线。
- **两个手工模拟工具原先叫 `test_simulate_home.py` / `test_simulate_outside.py`**，因文件名被 discover 收集、导入即发真实心跳。现已改名为 `simulate_home.py` / `simulate_outside.py` 并加 `__main__` 守卫，**只能手动执行**，不要再改回 `test_` 前缀。
- **测试数据目录已完全隔离**：`config.py` 的 `_is_test_process()` 与 `scripts/run-tests.ps1` 的 `AIONSHOME_DATA_DIR` 会把整个 `DATA_DIR`（包括 `chat.db`、`location_*.json`、`uploads/`、`settings.json` 等）重定向到临时目录，即使服务在线运行也可安全跑测试。
- **不要在测试里直接用 `sqlite3` 连 `config.DB_PATH`**：测试进程里 `DB_PATH` 已指向临时目录，但 schema 不存在，要先 `await init_db()`。用 `database.get_db()` 而不是 `sqlite3.connect()`。
- **图片合并类测试的时间窗口**：生产 `image_wait_seconds` 是 60–90 秒，测试里曾用 0.04–0.08 秒，那比一次同步 SQLite 写入还短，机器一忙就假失败。已放宽到 2.0 秒（回复由文本触发 `ready.set()` 立即释放，不增加耗时）。
- **测试进程自动隔离**：通过 `config._is_test_process()` 自动检测 `unittest` / `pytest` 并创建独立临时 `DATA_DIR`。

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

4. **主工作区会被并行会话写入**：仓库同时存在设计工作树（`D:\AionsHome-design`）与 Codex 工作树，
   前端改版曾直接写进主工作区的 `aion-chat/static/` 与 `public/`。
   提交前务必 `git status` 逐文件核对并**按路径显式 `git add`**，不要用 `git add -A`——
   否则会把别人还没完工的在制品裹进你的提交。推送前用 `git add -A --dry-run` 看清楚将要入暂存区的每个文件。

## 当前不要做的三件事

1. **不要把 `vendor/` 移出 git**。离线安装依赖它；要先有替代离线包，并在干净的 Windows 环境完成一次断网安装验证。
2. **不要改 SQLite 日志模式或 `synchronous`**。属于需单独决定的事：先在数据库副本上对比并发读写与恢复流程。
3. **不要改 `/static/*` 的缓存策略**。Android 侧已有 SHA-256 资源清单与本地缓存，先测清楚冷启动和二次打开各下载了什么再决定改哪一层。

## 当前状态（2026-11-09）

- 测试隔离、同秒翻页与记忆库跳转修复、AGY CLI 代理环境变量传递、以及历史漂移测试清理均已提交入库。
- **上下文构建已统一**：`context_blocks.py` 定义统一上下文结构（人设与规则 / 能力 / 运行时状态 / 记忆 / 外部资料 / 真实对话），
  分**指令通道**（system 角色）与**资料通道**（低优先级 user 角色 + 资料标注）。
  主私聊、聊天室、娱乐室以及相机 / 基金 / 位置 / 日程 / 动态 / 剧场 / 约会 / 自主行动 / 访客会客厅
  都已迁移，不再用 "user(配置) + assistant(收到，我会…)" 的伪对话注入；
  CLI 线路（Gemini / Codex / Antigravity）统一渲染成 `[System Instruction]` 与 `[Reference Data]` 两段。
  新增上下文或新模型线路请复用 `context_blocks.py`，不要再造伪对话对。
  人设前缀统一走 `context_blocks.build_persona_prefix()`；时间线读法说明走 `context_builder.render_transcript_note()`。
- **测试基线已漂移**：三套测试套件曾是 100% 全绿（2026-11-07 状态）。2026-11-09 实测 unittest 有 **9 个既有失败**
  （`test_app_supervision_v1`、`test_chatroom_persona_mobile_performance`、`test_chatroom_stream_safety`、
  `test_link_preview_ui`、`test_memory_count_api`、`test_chat_bubble_style_parity`），
  已逐个在干净 worktree（`git worktree add` 到 HEAD）上复现，**与上下文改动无关**。
  排查失败前先在干净副本上对比，别默认是自己的锅。
