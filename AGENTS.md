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
- **⚠️ 改完代码要用 `重启小家与QQ.bat` 才会真正换进程**：`一键启动.bat` 走的是
  `restart-home-qq.ps1 -StartOnly`，该模式下**既不杀旧进程也不启新进程**——
  8080 已经在监听时它只做一次健康检查就返回，看起来"重启完了"其实代码没生效
  （症状：新加的能力开关报 `unknown capability`，新路由 404）。
  换完进程后先核对：`GET /api/capabilities` 里有没有新能力、进程 `CreationDate` 是不是刚更新。
- 直接在终端跑 `restart-home-qq.ps1` 会**挂住不退出**（`Start-Process -RedirectStandardOutput`
  会让父进程一直等子进程），用 `.bat` 走正常路径；重启完看进程启动时间即可，别等它返回。

## 测试

### 施工策略：默认增量，别每轮跑全量

- **改哪里就只跑哪里**：优先运行被改模块自己的单元测试 + 直接相关的回归测试
  （`-Name test_xxx` 单模块最快；再用 `-Filter "test_相关*"` 带上前缀）。
- **发现问题再扩范围**：定位到问题后，才把测试面扩大到受影响的上游 / 下游模块。
- **不要每轮修复都跑全量**：全量（unittest 约 9 分钟 + pytest + JS）留给
  **重要架构变更、合并 / 提交前验收、或用户明确要求**这三种情况。
- **多 Agent 并行施工期间，避开全量测试**：本仓库主工作区会被其它会话 / 工作树同时写入，
  全量测试里的前端断言（CSS / JS 契约）容易被**别人的未提交改动**干扰，产出无法归因的假失败。
  这类情况下只跑自己相关的模块；要归因某个失败，先在干净 worktree 上复现。
- 增量测试同样要跑够：改动跨了模块边界（例如改了 `context_blocks` 这种公共层），
  就把**所有直接 import 它的模块**的测试都跑一遍，而不是只跑改动文件所在的那一个。

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
- **改了 `static/` 下的 js / css，要顺手 bump 引用处的 `?v=` 版本号**（`<script src="/static/chat.js?v=...">`）：
  不 bump 的话浏览器和 Android WebView 会继续吃缓存里的旧文件，用户看不到改动。
  这不是改缓存策略——缓存策略本身别动（见下方"当前不要做的三件事"）。
  Android 端只有 `common.js` / `common.css` / `video-call.js` 有本地副本，
  `chat.js` / `chatroom.js` 等一律走服务端，bump 版本号即可让 App 拉到新版本。

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
  分三条通道：**指令**（system 角色）、**状态**（时间 / 日程 / 位置 / 设备等当下事实，user 角色 + 状态标注，
  不自动拿系统指令优先级）、**资料**（记忆 / 外部资料，user 角色 + 资料标注）。
  主私聊、聊天室、娱乐室以及相机 / 基金 / 位置 / 日程 / 动态 / 剧场 / 约会 / 自主行动 / 访客会客厅
  都已迁移，不再用 "user(配置) + assistant(收到，我会…)" 的伪对话注入；
  CLI 线路（Gemini / Codex / Antigravity）统一渲染成 `[System Instruction]` / `[Current State]` / `[Reference Data]` 三段。
  新增上下文或新模型线路请复用 `context_blocks.py`，不要再造伪对话对。
  人设前缀统一走 `context_blocks.build_persona_prefix()`；时间线读法说明走 `context_builder.render_transcript_note()`
  —— 它走 `CTX_TRANSCRIPT` 通道并**并进对话段的第一块**，不要退回指令通道：
  放指令区会隔着状态与资料两整块内容（实测 CLI prompt 里相隔 2005 字符），模型很难把说明和记录对上。
  说明里也必须写明「历史消息里包含你自己之前说过的内容」——历史消息统一渲染成 user 角色
  （`context_builder.py` 的 `render_merged_timeline`），只靠说话人区分 AI 自己和用户。
- **发往供应商前先剥内部元数据**：娱乐室直连 OpenAI 兼容接口，不经过 `build_multimodal_messages` 重建，
  需要自己用 `routes/playground._sanitize_messages()` 过滤 `_ctx` / `_timeline_sender` 这类内部键。
- **两条线路的技术路线不同（主私聊 vs 聊天室）**，改任一侧前先确认自己在改哪条：

  | | 主私聊（Aion） | 聊天室（Gemini / Connor） |
  | --- | --- | --- |
  | 当前模型 | `Codex-5.6-Sol`（会话级可切 `ChatGPT-5.6-Sol`） | `AGY · Gemini 4 Argon (Low)`（`chatroom_config.json` 的 `connor_model`） |
  | provider | `codex_cli` / `siwc` | `antigravity_cli` |
  | 传输 | 本地 CLI 子进程 / sidecar HTTP | 本地 CLI 子进程（`agy.EXE`） |
  | 消息形态 | **多条消息**（system / user 分开） | **单条 prompt 灌 stdin**，三通道靠 `[System Instruction]` / `[Current State]` / `[Reference Data]` 标签表达 |
  | 附件 | base64 或本地路径 | `@绝对路径`，且路径要转正斜杠（Windows 反斜杠 `\u \a \t` 会被误读为转义） |

  影响与坑：
  - **CLI 只吃一条 prompt**，所以 `_build_cli_prompt()` 必须做「连续同角色合并」，否则出现连续 `[Assistant]` 这种伪 multi-turn。
  - **Gemini / AGY CLI 会把机器拼的标记当成 agent 工具调用上下文**，触发 thinking 模式输出大段内心戏。
    图片提示必须伪装成用户对话的自然延续（实测「帮我读一下 xxx.jpg」干净，`[图片附件] xxx` 必触发思考）。
  - **CLI 的 agent 配置会禁用工具**（`tools: []` / `commandExecutionPolicy: off`），所以 Gemini 只能发文本协议指令
    （`[MEMORY:]`、`[PAT:]` 等），不能真正调工具。
  - **安全过滤行为不同**：Gemini 原生过滤会被原样暴露成 `[AntigravityCLI错误]` 段进上下文；
    Codex 线路没有这一环。报错原文会每轮重复占用 prompt（实测占 30%）。
  - **设备状态注入次数不同**：主私聊走 `stream_ai()` 的统一入口只注一次；聊天室在组装阶段自己注入，
    所以「设备状态注入两遍」的 bug 只在聊天室出现。
  - `routes/chatroom.py::_resolve_connor_model()` 的解析顺序是：请求参数 → `chatroom_config.json` 的 `connor_model`
    → 第一个 `codex_cli` 模型。函数 docstring 写的「默认走 Codex CLI」只是**兜底**，实际配置会覆盖它。
  - 两条线路**共享同一个模型池**（`config.MODELS`），只是 provider 不同。
- **两个 digest 锚点的存储方式不同**：

  | | 主私聊 | 聊天室 |
  | --- | --- | --- |
  | 存储 | JSON 文件 `data/digest_anchor.json` | 数据库表 `chatroom_digest_anchors` |
  | 键 | `last_digest_ts` | `room_id='connor_unified'` |
  | 读写 | `config.load_digest_anchor()` / `save_digest_anchor()` | 直接查表 / `INSERT OR REPLACE` |

  影响与坑：
  - **JSON 写入不是原子的**：`save_digest_anchor()` 直接 `write_text`，没有「临时文件 + rename」。
    进程在写入中途被杀会留下半截 JSON。
  - **读取时把损坏静默当成「从未总结过」**：`load_digest_anchor()` 的 `except` 吞掉异常返回 `0.0`，
    而 `0.0` 正是「从未总结过」的语义 —— 结果是**整个 messages 表重新进入 digest 取数范围**
    （当前约 13791 条），造成大面积重复总结。改这个文件时优先考虑改成原子写，或读取失败时回退到
    「上次已知的好值」而不是 0.0。
  - **备份必须成对**：JSON 是 `data/` 下的独立小文件，数据库锚点在 `chat.db` 里。
    只备份其中一半会让两个锚点错位（一个前进了一个没前进）。
  - **测试隔离两者都 OK**：`_is_test_process()` 重定向 `DATA_DIR`，JSON 跟着走临时目录；
    数据库锚点随 `chat.db` 一起进临时库。
  - **并发写**：JSON 无锁，多进程会互相覆盖；数据库锚点走 sqlite 事务，与记忆写入天然一致。
- **设备状态只注入一次**：`build_context_segments()` 已把设备状态放进状态通道，
  聊天室再用 `with_current_device_context()` 往指令通道插一次就会重复
  （实测每次调用多拼一遍，白烧 token 且给模型两份同样事实）。
  该函数的去重只查传进去的那一个列表，看不到别的通道——所以调用方要用
  `existing_context=state` 把已组装的通道传进去才会生效。
- **两个聊天页共用一套气泡拆段**：私聊与聊天室的 AI / 用户消息都走
  `chatroom-markdown.js` 的 `splitUserBubbleParts()`，标题独立成块、正文每段一个气泡、
  单换行也拆段（不再合并成同段里的 `<br>`）。改任一页的渲染都会同时影响另一页与相关 JS 测试，
  注意 `test_private_chat_markdown_messages.js`、`test_chatroom_markdown_messages.js`、
  `test_inner_monologue_bracket_fallback.js` 三处的契约。
- **聊天室页已与私聊页对齐**：`chatroom_messages` 表有 `starred` 列，消息菜单含复制 / 星标，
  顶部 bar 有"回到主页"，侧边栏有"星标消息"面板。
- **测试基线已漂移**：三套测试套件曾是 100% 全绿（2026-11-07 状态）。2026-11-09 实测 unittest 有 **9 个既有失败**
  （`test_app_supervision_v1`、`test_chatroom_persona_mobile_performance`、`test_chatroom_stream_safety`、
  `test_link_preview_ui`、`test_memory_count_api`、`test_chat_bubble_style_parity`），
  已逐个在干净 worktree（`git worktree add` 到 HEAD）上复现，**与上下文改动无关**。
  排查失败前先在干净副本上对比，别默认是自己的锅。

### 记忆写入的三条路径与两套锚点（2026-11-09 梳理）

改动记忆库前先认清这几条路径，它们**门槛与锚点各不相同**：

| 路径 | 入口 | 门槛 | 写入哪个表 |
| --- | --- | --- | --- |
| 自动 digest（主私聊） | `main._auto_digest_loop` → `memory.auto_digest()` | ≥40 条未总结消息 | `memories` |
| 自动 digest（聊天室） | `chatroom._connor_1v1_auto_digest_loop` → `digest_chatroom()` | ≥40 条 | `chatroom_memories` |
| 手动总结 | 记忆库「总结新消息」→ `memory.manual_digest()` | **无限制**（`min_messages=0`） | 同上 |
| 显式 `[MEMORY:...]` | `capabilities.memory_write` 开关打开后模型自发输出 | 单条即可 | `memories` / `chatroom_memories` |

- **手动总结没有条数门槛**，自动才有；`digest_chatroom()` 内部硬编码了 ≥40 条检查，
  所以主私聊手动（`manual_digest`）与聊天室手动（`digest_chatroom`）行为不一致。
- **两套锚点互不相通**：主私聊是 `data/digest_anchor.json`（`last_digest_ts`），
  聊天室是表 `chatroom_digest_anchors`（`room_id='connor_unified'`）。
  锚点 = "此时间之前已总结过"，每组成功后推一格，失败则不动。
  `test_digest_window_scope.py` 刻意锁定了"两个 digest 各用自己的锚点、各扫各的"——这是设计意图，不是缺陷。
- **显式 `[MEMORY:...]` 原先完全在锚点体系之外**：不推进任何锚点，导致同一段对话被显式写入和 digest 各记一遍。
  已新增 `memory_write_policy.py` 统一分类（模型可声明 `[MEMORY:长期重要|内容]`，否则按内容判定），
  并补齐 `source_msg_id` 溯源。
- **跨通道去重用 `memory_coverage.py`（来源消息 id 重叠，不用向量）**：
  新表 `memory_message_coverage(owner_id, message_id, memory_id, origin, created_at)`。
  显式写入登记「触发这轮的用户消息 + AI 回复」两条 id，digest 取数时跳过这些消息；
  digest 写入后也登记自己的来源，反向同样生效。
  **粒度是关键**：显式只挂 AI 回复自己那条时命中率仅 1/5，扩到整轮对话后 5/5。
  判重不要用向量相似度——实测不同事件也有 0.88，真重复只有 0.84，不可分。
  **两个易错点**：① 主私聊 digest 的消息流混合 `private:` 私聊与 `chatroom:` 群聊，
  过滤时必须用消息自带的 `_source_id`，统一补前缀会把群聊消息错标；
  ② Aion 在聊天室写的记忆落**主库**，覆盖登记要记 `OWNER_MAIN` 而不是 `OWNER_SECOND`。
  覆盖按 owner 隔离是刻意的——两个 AI 各记自己视角，同一条群聊消息两边都会总结。
- `homecoming/summary_coverage.filter_uncovered()` 是 fail-open 的，且
  `homecoming_summary_coverage` 表**只在归巢回传流程里才创建**——没用过回传就没有这张表，
  此时该调用静默放行。判断覆盖机制是否真的生效，要查表是否存在，不能只看代码被调用。


### 生活日记模块（2026-11-10 新增）

`aion-chat/life_diary.py` + `life_diary_format.py` + `life_diary_prompts.py`：
从本人聊天里捕捉生活片段，按**事情发生的日期**写进本机 Obsidian 日记库
（`D:\obvault\DiaryInbox\YYYY-MM\YYYY-MM-DD.md`，一日一夜 APP 的格式）。

- **能力开关 `life_diary`，默认关闭**；UI 在「近期生活与待办」页底部（只读文本框 + 立即整理）。
- **与后置哨兵是互补的两半，不要合并**：`post_sentinel` 的 prompt 明确写着
  「日常琐事、梦一律跳过」，它维护"还要跟进的事"；本模块专门收那些被跳过的生活片段。
  两套 prompt 混用会互相打架，所以分开写但共用 `sentinel_dispatch` 的扇出。
- **节奏不同**：哨兵是消息级（每条消息一次模型调用）；本模块是**聚合窗口**
  （静默 30 分钟后跑当天批次 + 每天一次兜底），因为日记的单位是"天"。
  配置项：`life_diary_quiet_minutes` / `life_diary_daily_hour` / `life_diary_daily_minute` / `life_diary_root`。
- **写入必须最小侵入**：`life_diary_format.merge_document()` 只改自己负责的 frontmatter 键
  和 APP 已知 Callout，`## Calendar`、`## 📷 今日图片`、手写正文、未知键原样保留；
  写文件用临时文件 + `os.replace` 原子替换，**不许整篇覆盖**。
- **不许写的字段**：`baby_age_*` / `lunar` / `solar_term` / `period_*` / `weight` 由 APP 自己算，
  白名单在 `life_diary_format.ALLOWED_FIELDS`；模型返回这些字段会被丢弃。
- **分数不许折算**："心情不错"不写 `mood`，只有"心情7分"才写。
- **日期按事件归属**：模型从原话推断日期（今天/昨天/上周三），拿不准才用消息当天日期，
  并在 `life_diary_fragments.date_inferred` 标记 1。
- `life_diary_fragments` 表记录"哪条片段来自哪些 message id"，
  **目前只用于幂等与溯源**；删消息后同步改写文件（用户同意后置）尚未实现。
- 测试：`aion-chat/test_life_diary.py`（17 例）。**不许在测试里碰真实 `D:\obvault`**，
  用临时目录 + `AIONSHOME_LIFE_DIARY_ROOT` 覆盖。


### 本机 HTTP 限制（2026-11-11）

浏览器接管、MCP 审批、表单保险库，以及娱乐房的 playground 工具执行，
都只允许在运行新家的这台 Windows 电脑上、通过 `http://localhost:8080`
（Host 为 `localhost` / `127.0.0.1` / `::1`，客户端为回环地址）访问。
家庭局域网（`192.168.3.39`）和 Tailscale（`100.66.80.37`）仍可打开聊天页，
但不能调这些接口。

- 共用校验在 `aion-chat/local_http.py`：`require_local` / `require_local_request`。
  已挂到 `routes/form_vault.py`、`routes/connor_browser.py`、`mcp_approvals.py`、
  `routes/playground.py`（整路由，含工具执行）。
- GET/HEAD/OPTIONS：只检查回环客户端 + Host。写操作还要求
  `Origin` 等于 `scheme://Host`，并拒绝 `sec-fetch-site` 为 `cross-site` 或 `same-site`。
  **不要信任 `X-Forwarded-For`**。不要写成「只有带了 Origin 才核对」——缺 Origin 的 POST 必须拒绝，
  否则审批接口会被绕过。
- 新增加密、接管、审批、可执行工具一类接口时，复用 `Depends(require_local_request)`，不要另写一套更松的检查。
- 测这些路由时，`TestClient` 默认 `Host=testserver`、`client=testclient`，会被 403。
  使用：`TestClient(..., base_url='http://localhost:8080', client=('127.0.0.1', 50000))`，
  非 GET 请求还要带上匹配的 `Origin`。参考 `test_local_http_routes.py`、`test_connor_browser_routes.py`。
- `routes/chatroom.py` 的 `_reply_connor` 必须用 `_get_room_type(room_id)`，不要引用未定义的房间类型名（本地提交 `f51f124` 已修，不要回退）。

### 提交与推送

- 用户约定：**只有明确说要推送时才 `git push`**；日常本机改动不要自动同步到 GitHub。
- 提交前按路径显式 `git add`（见上方「主工作区会被并行会话写入」），不要把别人的在制品和密钥推进去。
