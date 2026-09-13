---
title: F8 opencode 引擎桥（Engine Bridge）
description: VIBE_TRADING_ENGINE=opencode 时的 SessionService 置换层——vt 网关把 opencode 当外置 agent 引擎（driver/translator/service/recovery 四层），含双存储所有权规则（D3）、启动存活对账三分支恢复与级联生命周期、14 项降级清单（opencode 引擎不复制的原生便利）、多租户认证配方（计划 D9 行 / Phase-0 调查「F6」）、T15 Settings 面裁决（sse_timeout 取值 / data-sources B5）与 ENGINE=native 回滚程序。改 agent/src/opencode_bridge/、接 T7 工厂开关、排查会话恢复/重挂/补写、部署多租户认证、评估 opencode 引擎功能缺口时必读。触发词：engine-bridge、opencode_bridge、RecoverableOpencodeSessionService、reconcile、VIBE_TRADING_ENGINE、re-attach、backfill、interrupted、级联删除、降级清单、认证配方、API_ALLOWED_HOSTS、API_AUTH_KEY、sse_timeout、data-sources 热更新、B5、回滚、ENGINE=native。
type: delta
status: active
created: 2026-09-13
updated: 2026-09-13
tags: [engine-bridge, opencode, recovery, session-service, degradation, auth, settings]
related: [../branch/MYMAIN_DIVERGENCE.md, f7-opencode-agent.md, f2-mcp-memory-tools.md, f5-clickhouse-data-source.md]
---

# F8 opencode 引擎桥

> 一句话定位：把 F7 的 opencode harness 变成 vt 网关的外置 agent 引擎——`agent/src/opencode_bridge/` 在 SessionService 接缝下整体置换原生 Python agent loop，前端/16 个 IM 适配器/调度器零改动；opencode 是独立进程、比网关命长（T2 memo F2），恢复语义因此以「引擎活着」为常态设计。本卡是 mymain 血统 opencode 引擎的**运维圣经**：能力边界（降级清单）、认证配方、Settings 面裁决、回滚程序全在此。

## 能力

- 四层结构：`driver.py`（T3，EngineDriver 四原语 + legacy `/session` REST + 单条持久 SSE，subscribe-first）、`translator/`（T4，opencode 事件 → vt SSE 词汇，静默期 8.0s 定版）、`service.py`（T5，OpencodeSessionService 全 D6 契约）、`recovery.py`（T6，启动恢复 + 存活对账 + 级联生命周期）
- **双存储所有权规则（D3，计划原文）**：`messages.jsonl` = 用户可见转录（对引擎只写）；opencode store = 引擎上下文真相。会话删除/IM `/new` 必须级联到 opencode session；opencode compaction 对 vt 转录不可见（接受）
- 崩溃推论：网关崩溃后**活性以引擎为准**（回合还在不在跑只有 opencode 知道），**历史以桥存储为准**（转录只追加、不重写）
- 恢复**永不重发 prompt**（计划 T6 Must-NOT：永不重复执行）——对账只读引擎状态；重挂不是重启

## 启动恢复三分支（T6）

网关启动时对每个 pending/running attempt 查 `GET /session/:id/message`，恰好落入一支：

| 分支 | 引擎状态 | 动作 | 落盘 |
|---|---|---|---|
| 1 重挂 | 回合仍在跑（assistant 消息未完成 / finish=tool-calls / 完成但在 OmO continuation 窗口内） | 重挂 SSE（泵重连即自然重订阅）+ `note_attempt(engine_sid, attempt_id)` 重新广播 | attempt 保持 running；看门狗定时复查防挂死 |
| 2 补写 | 回合已完成（最后一次 natural completion 超出 continuation 窗口） | 从引擎消息列表补写终态：D4 定版文本 + D6 元数据枚举 + FTS 恢复锚点索引，走 T5 `_persist_terminal_attempt` 同一接缝 | attempt completed + 回复消息 + `attempt.completed` 事件 |
| 3 中断 | 引擎无此会话（404）/ 引擎不可达 / prompt 从未到达 | 按现有 interrupted 语义落盘（对齐原生 `session/service.py:101-154`，含 `recovery_reason: "service_restart"`） | attempt interrupted + 部分文本 surfaced；IM 轮询在预算内拿到终态 |

验收锚点：恢复后 `replay=active`（`sessions_routes.py:806`）不再重播死回合——分支 2/3 的 attempt 均为终态，`replay_all` 恒为 False；分支 1 保持 running（活回合本就该重播）。

## 级联生命周期（D3）

- `delete_session` → 引擎侧 `DELETE /session/:id`（spike §7h 实证：200 + `session.deleted` 事件 + 子会话级联删除）；fire-and-forget，vt 侧删除永不被引擎阻塞，404 视为成功（幂等）
- IM `/new`：`runtime.reset_session`（保护区，零改动）只丢 channel→vt 映射；下一条消息建新 vt 会话 → 懒建**新** opencode 会话。旧引擎会话**故意不删**——其 vt 转录仍可浏览、引擎上下文仍在（D3），`session.config` 持久化映射保证重启后续用。T8 s3 实证：`/new` 后新回合起 `s2`+`e2 ≠ e1`，旧引擎会话 `GET /session/{e1}/message` 200 保留，全程恰好 2 个引擎会话

## 关键文件与开关

| 文件 / 开关 | 作用 |
|---|---|
| `agent/src/opencode_bridge/recovery.py` | `RecoverableOpencodeSessionService` + `reconcile()`（T7 工厂启动入口）+ 级联删除；模块 docstring 为 D3 规则正典 |
| `agent/src/opencode_bridge/recovery_branches.py` | 三分支落地机械（重挂 awaiter + 看门狗 / 补写 / 中断），复用 T5 持久化接缝 |
| `agent/src/opencode_bridge/engine_state.py` | 引擎消息列表解析：turn 分类、D4 定版文本、run_dir 收割（T4 冻结正则）、tool_trail 重建 |
| `agent/src/opencode_bridge/im_stream.py` | T9 `_stream_delta` 生产者（IM 渐进编辑流式，grinev 阶梯 1s→2s→5s→10s 叠加 manager coalescing）；全仓库首个 `_stream_delta` 生产者 |
| `agent/src/api/state.py:64-73` | 工厂开关：`get_env_config().opencode_bridge.vibe_trading_engine` → `opencode` 走 `build_session_service`，`native` 走原生 `SessionService`，未知值 `ValueError`（恰好 15 行分支） |
| `session.config["opencode_engine_session_id"]` | vt→引擎会话映射的持久化键（T5 仅内存，T6 落盘——重启后恢复与上下文续用的前提） |
| `VIBE_TRADING_ENGINE` | 工厂开关 env（`env_schema.py:687`，默认 `native`）；`OPENCODE_BASE_URL`/`OPENCODE_SERVER_PASSWORD`/`OPENCODE_BRIDGE_QUIESCENCE_S=8.0`/`OPENCODE_BRIDGE_CHILD_EVENTS=drop` 见 `env_schema.py` OpencodeBridgeConfig（T3） |

## 治理现实（T7 后冻结工具面）

T7 活体 rig 证伪了两个计划假设——根因均为**冻结的工具治理清单**（`OpencodeAgent/config/vibe-trading-tools.json`，15 个禁用工具），Phase-0 spike 从未演练过它。两者都不是前端契约变更，均在桥侧修复（前端保持为契约）。**T8/T9/T10 须以治理后的真实工具面为准。**

- **run_dir bash 收割**（T7 FINDINGS #1）：治理禁用了 `backtest` MCP 工具 → agent 经 **bash** 跑回测（`python -m backtest.runner <run_dir>`，strategy-generate skill 路径），而非 MCP `vibe-trading_backtest`。T4 原 run_dir 收割（门控于 MCP 工具 **OUTPUT**）永不触发，且 runner stdout 只带 metrics（无 run_dir）。修复：`translator/tools.py` 从 bash 工具的 **INPUT** 命令经 `_RUNNER_CMD_PATTERN`（`backtest.runner <path>` / `vibe-trading backtest <path>`）收割，同时保留 MCP-output 模式以备治理重新启用 `backtest`（两路共存，last wins）。否则 `attempt.completed.run_dir` 为 null → 无 run 卡片（前端 runId 唯一来源 `d.run_dir`，`Agent.tsx:876-877`）。
- **D8② tool-agnostic**（T7 FINDINGS #2）：治理禁用了 `read_file` vt MCP 工具 → D8② 上传注入块点名「vt MCP read_file」成为死信。B6 的真实防御（相对→绝对路径**解析**）按设计生效；引擎用其**原生** `read` 工具在注入的绝对路径读取上传。修复：`service.py _build_prompt_injection` 保留绝对路径解析、措辞改为 tool-agnostic（「用文件读取工具经该**绝对**路径读取，内置 read 工具即可」）。`/upload` 返回 SHADOW 文件名（`uploads/<hash>.csv`），注入解析内容中出现的任何相对路径（含 shadow 名）。

## 降级清单（14 项，T15 定版）

> opencode 引擎**不**复制的原生便利——本卡的诚实层。每项：原生行为 → opencode 行为 → 实证状态（T7/T8/T9 活体 rig 事件普查 + 代码读）。「缺席」= 事件/能力不出现；前端优雅降级（allowlist 订阅但无 handler 渲染，或 REST 字段保持 null）。普查来源：T7 FINDINGS G1（`sse-g1.json`）、T8 FINDINGS s1（`s1-long-turn-results.json`）、T9 FINDINGS（`sse-events.json`）。计划源：§降级清单（14 项）。

1. **聊天内 swarm 实时卡片**（`swarm.started`/`swarm.event`）— 原生：AgentLoop swarm 编排发聊天内实时卡片。opencode：OmO 子代理（`task` 工具）为编排主通道，子会话事件被丢弃（`OPENCODE_BRIDGE_CHILD_EVENTS=drop`，D4），桥不发 `swarm.*` 词汇；vt /swarm 面板仅覆盖 MCP `run_swarm` 路径（该工具在治理面上，但其实时卡片是原生 loop 驱动）。实证：T7/T8/T9 普查无 `swarm.*`。状态：**缺席**。
2. **`llm_usage` Run Detail 面板** — 原生：AgentLoop 写 `run_dir/llm_usage.json` 工件（`loop.py _record_llm_usage`）+ 发 `llm_usage` SSE，Run Detail REST 服务它（`runs_routes.py:768`）。opencode：桥从 `message.updated` tokens 合成 `llm_usage` SSE（`translator/lifecycle.py:171`、`routing.py:186`），形状与原生逐字一致 `{input_tokens,output_tokens,total_tokens,iter}`——但**不**写 run_dir 工件，故 `RunDetailResponse.llm_usage` 保持 null。前端现实（只读核验）：`RunData` 类型无 `llm_usage` 字段、RunDetail.tsx 不渲染它、Agent.tsx 未注册 `llm_usage` SSE handler → 面板在**两个引擎下都**优雅缺席。实证：`llm_usage` 在 T7 G1 + T8 s1 普查中（SSE 事件确实发出）。状态：**SSE 已合成（原生形状）；Run Detail 面板优雅缺席**（计划所期降级）。待接：写工件需桥 translator/service 编辑（见§待接入）。
3. **`compact` 事件渲染** — 原生：AgentLoop 上下文压缩时发 `compact`，前端有 handler（`Agent.tsx:793`）。opencode：压缩是 opencode 内部行为、对 vt 转录不可见（D3 所有权：opencode store 为引擎上下文真相，vt `messages.jsonl` 只追加），桥不发 `compact`。实证：普查无 `compact`。状态：**缺席（D3 接受）**。
4. **mandate 提案卡 + 实盘授权流**（`mandate.proposal`/`mandate.committed`）— 原生：mandate 工具驱动提案卡 + 实盘授权流。opencode：research-only 姿态（D7）——`propose_mandate_profiles`/`trading_place_order` 永不补 MCP wrapper，治理亦禁用 `trading_*`；前端有 handler（`useSSE.ts:94`）但收不到事件。状态：**缺席（设计使然，D7 显式接受）**。
5. **下单类工具**（`trading_place_order`/`trading_cancel_order`）— 同 research-only 姿态（D7），治理清单禁用（T2 memo §3）。状态：**缺席（设计使然，安全收益）**。
6. **Settings→LLM 页对对话引擎失效** — 原生：本页配置对话引擎的 provider/model/key。opencode：对话引擎由 opencode 配置（`opencode.json` + opencode auth）管理；本页 `LANGCHAIN_*` 仅驱动 auto-title（D11）+ swarm worker。前端**无只读 affordance**（disabled 态仅由 provider auth 驱动：`usesManagedAuth`/`api_key_required`），故按计划 T15 显式回退**降级为文档说明**（见§T15 Settings 面决策）。`sse_timeout_seconds` 在两引擎下继续服务。状态：**部分失效（已文档化，前端零改动）**。
7. **goal 面板 agent 侧创建依赖模型遵从 prompt 注入** — 原生：goal 工具直接绑定会话。opencode：D8① 在每条 prompt 前置注入 `vt_session_id`，agent 侧 goal 创建依赖模型遵从注入（透传 `session_id=`）。实证：D8① 注入已落地（T5 service）；遵从率未测（T14 待跑）。状态：**依赖模型遵从性（T14 监控遵从率，≥10 回合，<80% → 升级别名映射）**。
8. **`session_search` 跨会话搜索工具** — 原生：agent 可跨会话搜索。opencode：`session_search` MCP wrapper 可选（D7），未补 → agent 不可调用；但 FTS 索引仍写（T5 service，D6 契约 `index_message`/`index_session`），REST 搜索仍 work（sessions_routes）。状态：**agent 侧工具缺席；FTS 索引 + REST 搜索完好**。
9. **自动标题走 Python provider 栈** — 原生 + opencode：auto-title 保持 ChatLLM/Python provider 路由（D11，`sessions_routes.py:706` 直用 ChatLLM），**不**走 opencode 引擎；依赖 `LANGCHAIN_*` 存续（T11 按租户种子）。实证：T7 FINDINGS「Auto-title (D11) stayed on the ChatLLM route（dashscope provider）」；T8 佐证。状态：**工作正常（D11 路由不变）——依赖注记，非损失**。
10. **OpenBB bridge 仅契约形状覆盖** — 桥契约测试覆盖 OpenBB adapter 消费形状（subscribe/clear/append_message，T5），但不做真连接验证（engine-bridge 计划 out of scope）。状态：**仅契约形状（out of scope）**。
11. **`tool_progress` 阶段进度条** — 原生：MCP progress notification → `tool_progress`，前端渲染阶段进度条（`Agent.tsx:764`）。opencode：MCP progress 不透过 opencode `/event` 面（opencode 不把 MCP progress notification 中继为事件），桥无法发 `tool_progress`。实证：T7/T8 普查无 `tool_progress`。**关键区分**：`tool_heartbeat` **不是**降级项——它是桥的**必须合成**项（B3/D5）：tool part running 期间每 3s 一发，令前端 90s 看门狗在长静默回合保持满足。实证：T7 g7 `sleep 105` → 35 heartbeat（中位/最大间隙 3.00s，max elapsed_s 105.0），看门狗从未归档；T8 s1 170s 静默 → 57 heartbeat（中位 3.0s）。状态：**tool_progress 缺席；tool_heartbeat 在场（B3 防御活体验证）**。
12. **`goal.*` SSE 实时性** — 原生：MCP goal 写入发网关 EventBus 事件 → `goal.created`/`goal.evidence`/`goal.updated` SSE（前端有 handler，`useSSE.ts:93`）。opencode：MCP goal 工具在 MCP 子进程运行（与网关跨进程边界），直接写 goal store（`sessions.db`），**不**发网关 EventBus 事件 → 无 `goal.*` SSE；面板以 REST 重取兜底（「面板可见」= REST 重取后可见，计划 T14）。状态：**goal.* SSE 缺席；REST 重取兜底（T14 验证面板 E2E）**。
13. **Settings→data-sources 热更新** — MCP 子进程 env 在 spawn 时固定（`opencode serve` 拥有），写 `.env` 不热加载 agent 的 MCP 工具。T15 裁决：**文档化**（本项）——gateway 侧热应用，agent 侧需引擎/容器重启；否决 gateway 自重启（不能重 spawn MCP、丢在途回合、host-direct systemd `Restart=` 范畴）。见§T15 Settings 面决策 + T2 memo §2.3。状态：**已裁决（文档化，无 gateway 自重启）**。
14. **`stream_reset` provider 重试提示** — 原生：provider 中途重试发 `stream_reset`，前端清空流式视图（`Agent.tsx:703`）。opencode：无同构事件——opencode `session.status{retry}` 映射为 attempt 生命周期计数（D5），**非** `stream_reset`；桥 best-effort 映射产出空。实证：普查无 `stream_reset`。状态：**缺席（无 opencode 同构；best-effort 映射为空）**。

## 认证配方（计划 D9 行；Phase-0 调查「F6 对冲」）

> 计划以「F6 配方」引用本节（多租户认证集成对冲，计划 TL;DR + D9 + T11 References）。**命名注意**：此「F6」非 wiki 历史 F6（ClickHouse 语义层 → 已并入 F5；见 [README.md](README.md)「为什么没有 F6」）——它是 Phase-0 调查对认证配方的编号，正典落在计划 D9 行。T10/T11 执行它。

多租户认证的精确配方（Oracle 二轮 B2 修正，行号已核验）：

- **Router 保留公网 Host**：薄 router 转发公网 Host 头（不改写为 loopback upstream host）。
- **每租户 gateway `API_ALLOWED_HOSTS=<公网host>`**（`env_schema.py:313`，经 `security.py:113 _get_extra_loopback_hosts` 生效，`security.py:166 _reject_untrusted_loopback_host` 消费）。⚠️ **B2 陷阱**：`EXTRA_LOOPBACK_HOSTS` 仅为内部 monkeypatch 注册键，**不是 env var**——写错则 router 转发的每个请求被 403。
- **强制 `API_AUTH_KEY`（fail-closed）**：无 key 拒绝启动（T10 入镜像启动断言）。否则 router 的 loopback peer IP 触发 `security.py:507 _is_local_client` = 零认证。`env_schema.py:304`（别名 `VIBE_TRADING_API_KEY` 经 `_resolve_api_key_alias` 归一）。
- **opencode permission 走 OpencodeAgent 既有 render_config 治理**（deny 编译，F7 卡）；headless 下不引入交互式 permission 依赖。
- **kimaki 分层纪律**（先例 §7.3 权限行）：目录 ALLOW 规则进 server 生成配置**永不进 session scope**（findLast 覆盖序）；`external_directory` 恒为对象非字符串（deep-merge 整体覆盖陷阱）；`OPENCODE_CONFIG=<file>` 非 `OPENCODE_CONFIG_CONTENT`（kimaki #90）；`experimental.continue_loop_on_deny:true` 兜底 TTL 自动 reject。
- **容器隔离必选**：opencode bash 面 + 租户凭据同边界（D2 全栈每租户容器）。
- **MCP 子进程 env 与 gateway 一致**（B6/T10 断言）：`HOME`/`VIBE_TRADING_HOME`/`VT_MEMORY_*`（F2）/`CLICKHOUSE_*`（F5）经 tmpl env block 显式钉死；上传目录经 server 生成配置的 `external_directory` ALLOW 对象覆盖（永不进 session scope）。

## T15 Settings 面决策（ENGINE=opencode）

Settings HTTP 面是**引擎无关**的（契约由 `agent/tests/test_settings_engine_capability.py` 钉死）：`/settings/llm` 与 `/settings/data-sources` 在 native 与 opencode 下服务**逐字相同**的响应形状，零 diff 前端原样工作。`settings_routes.py` 永不读引擎开关（797 行，贴近 800 硬顶——决策记录于本卡 + 测试 docstring，不在该文件加 docstring 以免越顶）。三项决策：

### 1. `sse_timeout_seconds` — 两引擎同值（env 驱动，默认 90）

- **硬要求**（计划 T15 / Oracle 二轮注 4）：两引擎下都必须继续服务——它是前端流式看门狗输入（`Agent.tsx:1241-1243` 用 `s.sse_timeout_seconds * 1000` 武装 `sseTimeoutMsRef`；`Agent.tsx:1247-1269` 若流式中该窗口内无 SSE 事件则归档回合并丢弃后续事件）。
- **选定值**：`VIBE_TRADING_SSE_TIMEOUT` env（默认 90），opencode 下**不变**（`settings_routes.py:397` 引擎无关读取）。T11 按租户种子。
- **为何同值不同义**：opencode 下桥每 3s 合成 `tool_heartbeat`（tool part running 期间）+ 8.0s 静默期（`OPENCODE_BRIDGE_QUIESCENCE_S`）界定 natural-completion 后间隙，故健康回合的静默间隙恒 ≈8s——远低于 90s——看门狗仅在**真正挂死的引擎**（opencode serve 卡死 / 模型 provider 挂起）时触发。原生下它守卫工具静默 + provider 挂起。**同一外层界；弱化或按引擎分叉此值被禁止**（计划 Must-NOT）。实证：T7 g7 `sleep 105` → 35 heartbeat（中位 3.00s），看门狗从未归档。

### 2. Settings→LLM — 文档说明降级（清单第 6 项）

- opencode 下**对话**引擎由 opencode 配置（`opencode.json` + opencode auth）管理，非本页。本页 `LANGCHAIN_*` 仅驱动 auto-title（D11）+ swarm worker → 写入仍有效、**不**阻断（阻断会破坏 auto-title 配置）。
- 前端**无只读 affordance**（disabled 态仅 provider-auth 驱动：`usesManagedAuth` = `auth_type !== "api_key"` 禁 base_url、`apiKeyDisabled` = `!api_key_required || clearApiKey` 禁 key），无任何既有响应字段能诚实承载「opencode 托管」信号（`api_key_hint` 仅 gh_cli 显示、`baostock_message` 是 baostock 专用）。按计划 T15 显式回退（「若必须前端改动则降级为文档说明，不改前端」）→ **文档说明**（第 6 项），前端零改动、响应形状零改动。

### 3. Settings→data-sources — B5 裁决：文档化（清单第 13 项）

- **二选一**（计划 T15）：文档化「重启后生效」（第 13 项）或 gateway 写成功后触发受控重启。**选定：文档化。**
- **理由**（诚实、最小、对 host-direct 生产形态安全）：
  - 写入**已经**热应用到 **gateway** 进程（`os.environ` + `registry.refresh_source_order_overrides` + `reset_env_config`，`settings_routes.py:783-793`）——gateway 自身 loader 即时更新（测试 `test_data_source_write_hot_applies_gateway_side_under_opencode` 钉死）。
  - **agent 的 MCP 子进程** env 在 spawn 时固定、由 `opencode serve` 拥有（非 gateway 子进程），gateway 重启**不能**重 spawn 它；只有 `opencode serve` / 容器重启能（T2 memo §2.3：「`.env` hot-apply is impossible for MCP subprocesses」）。
  - **否决 gateway 自重启**：(a) 不能重 spawn MCP 子进程（进程树错误）；(b) 丢弃所有在途回合 + SSE 流；(c) host-direct 生产形态（T2 memo：production = host-direct systemd）下 gateway 重启是 systemd `Restart=` 范畴，非请求处理器副作用。
- **响应字段**：无通用 caveat 字段（`baostock_message` 是 baostock 专用），故 caveat **不**进响应——仅文档化，前端零改动。
- **host-direct ENV_PATH 陷阱**（T2 memo §2.3，按要求提及）：systemd unit 跑 `User=root`，故 gateway Settings 写入落 `/root/.vibe-trading/.env`，**非**受管的 `/opt/my-vibe-trading/.env`（EnvironmentFile）。今日潜伏（host 上无 gateway）；T10 每租户 volume 必须挂在恰好 `$HOME/.vibe-trading` 且 `VIBE_TRADING_HOME` 不设或相等（B5 修复），否则写入落容器临时层、重建即丢。

## 回滚程序（ENGINE=native 一键回退）

桥是**增量**的（新模块 `agent/src/opencode_bridge/` + `state.py:64-73` 单点工厂分支），原生路径零触碰。回滚：

1. 设 `VIBE_TRADING_ENGINE=native`（或不设——schema 默认 `native`，`env_schema.py:687`），重启 gateway。
2. 工厂（`state.py:65-71`）构造原生 `SessionService`；opencode 分支不进入。
3. **无需清理桥状态**：vt 会话存储（`messages.jsonl`/`session.json`/attempts/FTS）引擎无关（两引擎写同一 `SessionStore`）；唯一引擎专属键 `session.config["opencode_engine_session_id"]`（vt→引擎映射，T6）在 native 下**惰性**（从不读取）。孤儿 opencode 会话（若 serve 仍活）引擎侧可浏览、无害；需要时经引擎级联删除。
4. 冒烟：native 下发一条 Web 聊天回合 + 一条 IM 回合 → Python 引擎全功能恢复（计划 T15 QA happy）。

回滚残留风险：vt 侧无（转录只追加、映射键惰性）。若 `opencode serve` 在跑，可独立停之；native 下 vt gateway 不依赖它。

## 待接入（后续 todo + 已知残余）

> 2026-09-13 收口：T13/T14/T8-1 三项已全部落地，本节只剩真实残余。

- ~~**T13 — `scheduled_research` MCP wrapper**~~ **已落地**（`0c4ca311`）：冻结预检 LAPSED→PROCEED（8 引用）；78/83 工具、proposal_id 前 200 字符双断言、IM confirm 流 E2E 复活、6 README 计数同步、eval 地板逐位持平（0.4367，19 域零变化，定向组 0/8→6/8）。
- ~~**T14 — goal 绑定 E2E**~~ **已落地**（`02731637`）：遵从率 **12/12=100%**（门 ≥80%，别名映射升级不需要）；面板 REST 重取可见性全链绿；降级项 12 诚实佐证（MCP 侧写入无 `goal.*` SSE 帧）。
- ~~**T8-1 — 活体引擎死亡检测**~~ **已修复**（`90a4378a`）：`stream_liveness` 双有界信号（4 零帧连接周期 ≈3.5s / 15s 无帧窗，`server.heartbeat` 10s 为存活基准）；实测杀 serve 后 **8.03s** 落 failed + IM 同刻显式失败回复（原 590s 挂起）；引擎回归免网关重启自愈（`_ensure_pumps`）；scenario g 真实数据无误杀守卫。有界残余（诚实记录）：<3.5s 快死快活的僵尸回合按长静默工具等待。
- **llm_usage Run Detail 工件**（清单第 2 项，唯一遗留增强）：要在 opencode 下服务 `RunDetailResponse.llm_usage`，桥须在 terminal 写 `run_dir/llm_usage.json`（从 `message.updated` tokens 累积）——需 `translator/`/`service.py` 编辑。前端当前不渲染它（`RunData` 类型无 `llm_usage`），故为潜在增强、非可见缺口。
- **VT_ROUTER_RECLAIM_INTERVAL_S 未接线**（T12 发现，记录不修）：env 已解析但 router 内无周期循环——生产形态用 cron/systemd timer 触发回收，或 ~10 行 router 特性提交补周期循环。
- **nano-search-mcp 容器内降级**（T10 发现）：mcp v1 API 与 VT 拉入的 mcp 2.2.0 不兼容 → 容器内 12 个辅助中文财经搜索工具缺席（核心 VT MCP 82→83 工具不受影响）；修复 = 独立的 fastmcp-4.x 迁移，用户门控。

## 开发历史

> 考古注：本节与全卡引用的任务级 SHA（`14bf3fe3..163f6852`）与里程碑 merge（`50675965`/`4e0e7662`）为 2026-09-13 restructure+rebase 之前的原史，完整保留于备份分支 `backup/mymain-pre-restructure-20260913`；当前线性历史中，引擎桥本体以单一功能 commit `feat(engine-bridge): F8 opencode engine bridge (SessionService seam replacement)` 落账，Phase 3 四件（租户容器/wrapper/router/隔离矩阵）各为独立功能 commit。

- 2026-09-12 Phase 0：T2 基线盘点 memo（`14bf3fe3`）+ T1 spike GO 与三份强制条件（`aaad7546`，8 份 golden traces）。
- 2026-09-12 Wave 2：T3 driver（`03332696`）→ T5 service（`6530a7f3`）→ T4 translator（`7967210f`）。
- 2026-09-13 T6 恢复对账 + 级联生命周期落地（`8fc1fe75`，本卡创建）；T7 工厂开关 + Web E2E 汇合门（`225ba2f3`，八组 67 检查全绿，含两处治理现实修复）。
- 2026-09-13 Wave 3：T8 IM 零改动验证（`84293175`，s0/s1/s3/s4 PASS、s2 xfail = T8-1 修复进行中）∥ T9 IM 流式彩蛋（`361e1a41`，`_stream_delta` 首产者、mock 3/3 PASS）。
- 2026-09-13 T15 收尾：Settings 面引擎能力契约（`test_settings_engine_capability.py`）+ 降级清单 14 项定版 + 认证配方/B5 裁决/回滚程序成文（本卡扩写）。
- 2026-09-13 T8-1 修复（`90a4378a`，stream_liveness 双有界信号，8.03s 落终态）+ T14 goal 绑定（`02731637`，遵从 12/12）。**Phase 0-2 里程碑并回 mymain（原 merge `50675965`，2026-09-13 restructure+rebase 后以功能 commit 线性落账）+ F8 账本记账**。
- 2026-09-13 Phase 3：T10 租户容器（`06507813`，钉版 1.18.30/4.19.4/base v3.0.0-tenant、supervisord 双进程、B5/B6 修正实证、前端 dist 入镜像、Rosetta amd64 compose E2E 全绿含崩溃自愈）→ T13 wrapper（`0c4ca311`）∥ T11 router+provisioning（`05c8058b`，双租户 E2E 59/59）→ T12 隔离矩阵（`dc64dffc`，**93/93 零跨租户可达，Phase 3 门 PASS**）。
- 2026-09-13 **计划执行完毕：15/15 todos 全绿**；第二次里程碑并回 mymain；剩余全部为用户门控项（部署/凭据/迁移/上游提交时机）。

## 验证

- 桥测试套件 **241 passed / 7 skipped**（终态基线，含 T13 wrapper/confirm 流测试；s2 已从 xfail 翻真断言 **8.03s PASS**）。OpencodeAgent 套件 **169 passed / 1 skipped**（config render 47 + router/provision + tenancy matrix）。全套件 **12070 passed**，失败集 = 已知 9 既有基线严格子集（零新增）。
- T15 Settings 契约：`agent/tests/test_settings_engine_capability.py` **6 passed**（sse_timeout 两引擎服务 + env 驱动 + LLM/data-sources 形状引擎全等 + opencode 下 gateway 侧热应用）；既有 `test_settings_api.py` **33 passed**（native 行为不变）。
- T7 Web E2E：八组 67 检查全绿（活体 rig，opencode 1.18.30 + OmO 4.19.4）；T8 IM：s2 修复后全绿（原 5 passed/1 xfailed）；T9 流式：mock 3/3 PASS；T10 容器 compose E2E 全绿（无 key 拒启/Web 9⁄9/崩溃自愈 9/9/B2 200⁄403/SPA/持久性）；T11 双租户 59/59；**T12 隔离矩阵 93/93（零跨租户可达）**；T14 goal 遵从 12/12。
- 关键测试：恢复三分支 + golden trace 重挂重放 + `replay=active` 死回合不重播 + 级联删除 + reconcile 幂等（T6）；run_dir bash 收割 + D8② tool-agnostic（T7）；D4 94 轮询全空 + B3 57 heartbeat（T8）；`_stream_end` 不带 `_stream_delta` + 一次性 `_streamed` tagger（T9）。
- 工件：计划 `.omo/plans/opencode-engine-bridge-v2.md`（D3-D11 正典）、`OpencodeAgent/docs/spike_report.md`（§7h DELETE 级联）、`OpencodeAgent/docs/baseline_memo.md`（§2.3 B5、F2 恢复分叉）、FINDINGS `.omo/evidence/opencode-engine-bridge-v2/{t7-e2e,t8-im,t9-im-stream}/`、traces `agent/tests/fixtures/opencode_bridge/traces/`（只读夹具）。

## 状态与上游关系

- 本分支独有，**不回流**（计划 guardrail：NO 上游 PR，候选记入 [../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md) 待裁决）。上游候选（计划 T15）：**SessionService Protocol 抽取**（D6 契约面）、**scheduled_research wrapper**（T13）、**ChannelRuntime `_streamed` 原生修复**（T9 FINDINGS #3：runtime 在 inbound 带 `_wants_stream` 时设 `_streamed`，`base.py:225-226` 已 stamp——channels/ 改动，本 wave 冻结）。
- 保护区零触碰：`src/agent/`、`src/session/`、`src/providers/`、`src/channels/`（16 适配器 + runtime/manager/base/bus/pairing）、`frontend/`、`src/api/state.py` 均零 diff；对原生 oracle 只 import 复用（`_format_interrupted_message` 等静态方法），不修改。
- 已知残余边界（全部成文，无进行中项）：T8-1 已修复（8.03s，<3.5s 快死快活僵尸回合为有界残余）；llm_usage Run Detail 工件为潜在增强；VT_ROUTER_RECLAIM_INTERVAL_S 未接线（生产用 cron/systemd）；nano-search-mcp 容器内降级（独立迁移，用户门控）。
