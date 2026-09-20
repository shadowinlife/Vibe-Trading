# opencode-engine-bridge-v2 - Work Plan

> ⚠️ **状态：研究/隔离任务已执行并留证，但 T10 容器形态未进生产（2026-09-13 执行完毕）**
> 本计划 15/15 todos 全绿；T12（Phase-3 出口门）**PASS — 93/93 矩阵检查绿、零跨租户可达**
> （`OpencodeAgent/docs/tenancy_report.md`）。但**多租户容器形态（T10 容器 + T11 薄路由）未被
> 生产采用**：实际生产是**宿主机裸部署（systemd 双进程），非 T10 容器**
> （`OpencodeAgent/docs/DEPLOYMENT-PROD-ENGINE-BRIDGE.md:16`）；多租户为**后续迭代项**
> （同文件 :22）。T1/T2/T11/T12 已执行并留证（`spike_report.md` / `baseline_memo.md` /
> `.omo/evidence/opencode-engine-bridge-v2/t11-router/` / `tenancy_report.md`）。
>
> - **生产实际是什么** → `OpencodeAgent/docs/DEPLOYMENT-PROD-ENGINE-BRIDGE.md`。
> - **§T10/§T11 多租户"容器 + 薄路由"规格** → `OpencodeAgent/docs/TENANT-IMAGE-GUIDE.md`
>   （由 deprecated `DEPLOY-GUIDE.md` 中 §T10+§T11 抽取而成，勿再从 DEPLOY-GUIDE 取规程）。

## TL;DR (For humans)

**What you'll get:** 在 mymain（opencode + OmO + vt MCP 生产 harness）之上补全"身体"：vibe-trading 的 React 前端和 16 个 IM channel 原样保留，通过一个新的引擎桥接模块（`OpencodeSessionService`）把会话运行时接到 headless `opencode serve` 上；再以"每租户一个全栈容器 + 薄路由"实现多租户。mymain 现有资产（OpencodeAgent 镜像、F2 记忆工具、F5 ClickHouse 层、12 子代理、工具治理）全部复用，不重做。

**Why this approach:** 四路代码级调查 + Oracle 对抗审查已验证：前端与 IM 对引擎的全部依赖收敛在 `SessionService`+`EventBus` 一个接缝上（唯一构造点 `agent/src/api/state.py:64`）；而多租户因 vt 状态层全部是进程级单例（import 时固化 home），**只能**走全栈每租户容器——这恰好与已有的 OpencodeAgent 镜像部署形态同构。

**What it will NOT do:** 不改前端一行；不改 16 个 channel 适配器一行；不碰上游保护区（`src/agent|session|providers`）；不接实盘下单（mandate/交易工具保持 research-only）；不动 harness-evolution 冻结面（mcp_server 工具面/opencode CLI 版本/OmO 钉版，Phase 4 受冻结门控）；不做共享 bot 的 channel-ingress 路由（每租户自带 bot）；不回流上游（个人部署，上游候选只记账）。

**Effort:** Large（Phase 0 ≤1d；Phase 1 约 1-2 周；Phase 2 约 3-5d；Phase 3 约 1-2 周；Phase 4 约 1 周 + 长尾）
**Risk:** Medium-High —— 最高风险是 OmO headless 行为（continuation/subagent 的 idle 语义）与事件翻译保真度，两者都由 Phase 0 录制门前置证伪；多租户认证集成有已知精确配方（F6）对冲。
**Decisions to sanity-check:**
1. 基线分支 = `mymain`（生产血统，含 F2/F5/OpencodeAgent），工作分支 `mymain-engine-bridge` 从 mymain 切出，里程碑并回 mymain 并记 wiki/divergence 账。
2. 桥内留一层薄 `EngineDriver` 协议——若 harness-evolution S3 决策门最终选 PydanticAI，换 driver 不换桥。
3. 实盘工具面维持 research-only：不给 `propose_mandate_profiles`/`trading_place_order` 补 MCP wrapper（安全收益，显式接受）。
4. Router 用 Host 路由（每租户子域 + 泛域名证书）为主、token→tenant 为辅（IM/API 客户端）。
5. Goal 绑定采用 prompt 注入 vt_session_id + Web UI REST-first（接受模型遵从性脆弱），别名映射留作 Phase 4 可选增强。
6. opencode API 只用有文档保证的 legacy `/session` 表面；`/api/*`（preview）观望不用。
7. 文本流式契约按表面各取一种（业界三种已验证契约，无项目混用超过两种）：Web SSE 面 = token-delta 直通（`message.part.delta`→`text_delta` 同构映射）；IM 面 = 渐进编辑节流（grinev 模式，叠加 manager coalescing）。

审查状态：**Momus [OKAY]**（引用/QA 完备性核验，2026-09-12）+ **Oracle 二轮 [approve-with-conditions]**（阻塞项 B1-B6 + 非阻塞 8 条已全部落进本版修订；一轮 F1-F7 保真度复核 = F1/F2/F3/F4/F7 faithful、F5/F6 partial→已修正。二轮报告归档 `.omo/evidence/opencode-engine-bridge-v2/oracle-rereview-report.md`）。

**Phase 0 结果（2026-09-12）：GO + 3 项强制条件。** T2 完成（commit `14bf3fe3`，`OpencodeAgent/docs/baseline_memo.md` 210 行）；T1 完成（commit `aaad7546`，`OpencodeAgent/docs/spike_report.md` 242 行 + 8 份 golden traces 972KB + record/analyze 脚本；按用户指令纯本地 serve 1.18.30，无 docker，版本 delta 已标记）。强制条件已回写 D4/D5：① QUIESCENCE_S=3.0 被证伪（OmO idle 后 6.4s re-prompt）→ 默认 8.0s；② delta 的 `field:"text"` 不区分 text/reasoning → 翻译器必须 join partID→part.type；③ idle 处理必须 sessionID-scoped（子会话 idle 早于父 14s 到达）。T2 关键发现：生产实为 host-direct 部署（非容器，08-28 切换）；三方版本分裂 1.18.18(镜像)/1.18.23(宿主生产)/1.18.30(npm latest)；OmO 事实 4.19.4 + 5.0 翻牌活体风险；B5 实锤且宿主部署有同源陷阱；registry `latest` 指向 v2.1.0（回退陷阱）；base 镜像 split-brain。

Your next move: **计划执行完毕（2026-09-13）——15/15 todos 全绿**。Phase 3 出口门（T12）93/93 零跨租户可达。剩余全部为用户门控项：①第二次里程碑并回 mymain（执行中）②真平台冒烟（钉钉/飞书/Telegram 测试 bot 凭据：real_platform_smoke.py / telegram_smoke.py / real_dual_bot_smoke.py）③ECS 部署 + registry push（含 base 字面 manifest digest 钉死）④nano-search-mcp fastmcp-4.x 迁移裁决（容器内 12 辅助搜索工具降级中）⑤上游候选三件（队列 ⑦）提交时机。Full execution detail follows below.

---

> TL;DR (machine): Large / Medium-High risk — 15 todos in 5 waves（Phase 0 spike 门 → Phase 1 桥接 → Phase 2 IM → Phase 3 多租户 → Phase 4 保真回填），基线 mymain 分支，交付 OpencodeSessionService 桥 + 租户容器 + 薄 router，前端/适配器/保护区零改动，证伪门全部 agent-executed。

## Context

### 架构裁决来源（本计划的设计依据，执行时不得重新争论）

四路调查（前端协议 / IM 通道 / agent 核心 / opencode server API）+ Oracle 对抗审查（verdict: sound-with-conditions）。Oracle 缺陷编号 F1-F7 全文见审查记录，要点已内化为下方决策 D1-D12。

### mymain 现状（复用基线）

- `mymain` 分支 = 生产分支：`OpencodeAgent/`（Docker 镜像 opencode-serve：opencode CLI + OmO + vt MCP + nano-search-mcp + ClickHouse 层，ECS 部署，2026-08-31 起 12 子代理 live）、`mymain-wiki/`（知识库 + `branch/MYMAIN_DIVERGENCE.md` 分叉账本）、F2 记忆 MCP 工具（`VT_MEMORY_MCP_TOOLS` 门控，计数 OFF=77/ON=82）、F5 ch_* 语义层工具。
  > ⚠️ 本计划所有文件引用以 **mymain 分支基线**解析：当前 main 检出缺 `OpencodeAgent/` 部署文件（仅存 pycache 残留）与 `mymain-wiki/`（main 上的 `wiki/` 是另一目录）。Momus 审查（2026-09-12，verdict OKAY）确认的两处"引用偏差"均为分支上下文伪影，非阻塞。
- swarm 编排已迁 OMO（`.sisyphus/swarm/`），VT swarm 与 OMO 子代理为两条不嵌套通道（F7 卡）。
- harness-evolution（XL 评测裁决计划）冻结面：`agent/mcp_server.py` 工具面、`OpencodeAgent/config/vibe-trading-tools.json`、opencode CLI 版本、OmO 插件钉版、`VT_MEMORY_MCP_TOOLS`、ClickHouse 凭据状态（评测窗口内）。
- 定期 rebase 上游（最近 899d3c75，零冲突，门禁绿）；提交纪律：DCO `-s`、Conventional Commits、禁 Co-Authored-By/AI 追溯行（本地 AGENTS.md 覆盖全局）。

## 锁定决策（D1-D12）

| # | 决策 | 依据 |
|---|---|---|
| D1 | 置换点：新模块 `agent/src/opencode_bridge/`，在 `src/api/state.py::_get_session_service()` 加 `VIBE_TRADING_ENGINE=native\|opencode` 工厂开关。保护区零触碰（CI 断言 diff 为空） | Oracle §7"接缝成立"；AGENTS.md 保护区 |
| D2 | 多租户 = 全栈每租户容器（gateway+opencode serve+MCP+home 同容器），router = 薄反代（token/Host→tenant→upstream，无业务逻辑）。**不存在共享网关+每租户 home 的形态** | F1：`helpers.py:27-28` import 时固化 RUNS_DIR/SESSIONS_DIR；GoalStore/pairing/agent.json/FTS 全部进程级单例 |
| D3 | 双存储所有权规则（成文）：`messages.jsonl` = 用户可见转录（对引擎只写）；opencode store = 引擎上下文真相。会话删除/IM `/new` 必须级联到 opencode session；opencode compaction 对 vt 转录不可见（接受） | F7 |
| D4 | attempt 边界 = 一次 `prompt_async` run；**回合自然完成** = `message.time.completed && finish≠"tool-calls"`（kimaki 模式，OmO stop-hook continuation 正解）；**定版文本 = terminal 发出前最后一次 natural completion 的文本**——continuation 使静默计时器重置并重新定版；**assistant Message 持久化仅发生在 terminal 时**（IM `_wait_for_reply` 轮询在此之前拿不到回复，600s 预算内）；`session.idle` 仅排空队列，terminal 仅在"静默计时器（`OPENCODE_BRIDGE_QUIESCENCE_S`）到期的 idle"或显式 abort 时发出；**QUIESCENCE_S 默认 8.0s——T1 实测校准完成：OmO continuation 在 idle 后 6.4s re-prompt（n=2，σ<0.02s，定时器驱动），3.0s 初始值被证伪**（spike_report §5c/§9；T4 golden 钉死 trace 导出值）；**idle 处理必须 sessionID-scoped**（子会话 idle 在同一 /event 流上、实测早于父 idle 14s 到达——把任意 idle 当回合结束会提前终态化）+ abort 双 idle 容错 + post-idle 簿记事件再发射（~12ms）需 novelty 过滤；**OmO continuation 注入的 user 消息无 synthetic 标志，按文本前缀 `[SYSTEM DIRECTIVE: OH-MY-OPENCODE` 识别并在转录/持久化层抑制**（T1 精化）；abort 由桥自记状态（idle≠completed）；**terminal 后到达的同 attempt 事件：丢弃 + warn 日志**——mymain 前端仅对 stopped/timeout 态有丢弃守卫（`Agent.tsx:658-684`），done 归档后放行迟到 delta 会产生**重复答案气泡**（`Agent.tsx:855-889`），故必须在桥侧丢弃；**`session.error` 后无 idle 时注入合成 idle**（kimaki #74：否则队列永卡）；子会话事件默认丢弃（`OPENCODE_BRIDGE_CHILD_EVENTS=drop`），子会话正典 id = `task` tool part 的 `state.metadata.sessionId`（**T1 精化：仅完成时填充**，live 跟踪用子 `session.created` 的 `info.parentID`；勿解析 state.output） | F5 + Oracle 二轮 B4 + 先例 §7.3 |
| D5 | 事件翻译规则：text **直接透传 `message.part.delta`**（`properties.delta`→`text_delta{delta}`，零差分计算；**T1 实测确认：1.18.30 上存在、单一形状 `{sessionID,messageID,partID,field,delta}`、946 样本、与 kimaki 1.2.15 fixtures 同形→1.x 线版本稳定，主路径 GO**；**但 `field=="text"` 不区分 text/reasoning——reasoning part 的 delta 同样携带 field:"text"（scenario a 中 16/17 个 delta 是 reasoning），翻译器必须 join partID→part.type（自 `message.part.updated` 快照）后再路由 text_delta vs reasoning-tail，否则思维链泄漏进聊天文本**——强制条件②）；`message.part.updated` 累积快照仅作 delta 缺失时的 fallback 差分 + `part.time.end` 完成标记；**翻译器按事件类型 allowlist 实现（未知类型忽略不报错）——版本漂移防御**（spike_report §8：新事件类型在 1.18.18 上存在性未验证，allowlist 使漂移无害）；reasoning→600 字符滚动 tail（前端替换语义，`Agent.tsx:694-699`）；tool part→`tool_call`/`tool_result`，工具名剥 MCP server 前缀取裸名（中继精确匹配 `sessions_routes.py:220,244`），**preview = `state.output` 前 200 字符（非 `state.title`）**（`loop.py:2600` 对齐——中继正则须在 preview 内命中 proposal_id/run_id 模式），绝不重新解析 output；**tool part running 期间桥每 3s 合成 `tool_heartbeat{tool,call_id,elapsed_s,attempt_id}`**（`loop.py:2283` 节奏对齐；前端 90s 不活动看门狗只有内容事件刷新时钟、keep-alive heartbeat 是 no-op，`Agent.tsx:1247-1269`——不合成则每个 >90s 的长回测回合被 timeout 归档且后续事件全部丢弃，B3）；MCP progress notification→`tool_progress` best-effort（缺则降级清单第 11 项）；`stream_reset` 由 opencode retry 类事件 best-effort 映射（缺则降级清单第 14 项）；`run_dir` 从回测类工具输出正则提取进 `attempt.completed`（run 卡片唯一来源 `Agent.tsx:876-877`）；**终态 payload 全字段枚举**：`attempt.completed{attempt_id,status,summary,run_dir,elapsed_ms,provider,model,reasoning_effort?}` / `attempt.failed{attempt_id,error}`（`service.py:408-414` 对齐；前端优先取 `d.summary` 而非累积 deltas）；`session.status{retry,attempt}`→attempt 生命周期计数；事件稀疏时从 `message.updated` 的 msg.parts 种子化 part buffer（kimaki prompt_async 路径坑）；`message.summary===true` 的压缩摘要消息过滤不进聊天 | F5 + Oracle 二轮 B3/B4 + 先例 §7.1/§7.3 |
| D6 | 桥契约 = SessionService 7 方法 + `.store` + `.event_bus` + 同类 `SessionBusyError` + `send_message` 位置/kwarg 双调用形（`scheduled_routes.py:88` 用位置参数）+ 回复 `metadata["status"]`（**失败 attempt 也必须写**，`scheduled_routes.py:109-115` 读取）+ `tool_trail` + **`session.last_attempt_id` 维护**（`replay=active` 读取，`sessions_routes.py:806`）+ `include_shell_tools` 语义裁决（opencode 下忽略或映射 permission-profile，T5 成文）+ FTS 索引写入（`service.py:133/227/284/407` 对齐）+ 启动恢复（含 opencode 存活对账：网关重启后先查 opencode 会话状态再决定 interrupted/reattach/complete） | F2 + Oracle 二轮保真度表 |
| D7 | 工具面姿态：research-only。Phase 4 仅补 `scheduled_research` MCP wrapper（救活 IM confirm 流）；`remember` 系已由 F2 覆盖（确认 `VT_MEMORY_MCP_TOOLS=1` 即可）；`session_search` wrapper 可选；mandate/下单类**永不**补 | F3 + mymain F2 卡 |
| D8 | Goal 绑定 + 上传解析（prompt 注入块）：桥在每条 prompt 前置网关注入块——① `vt_session_id`（指示 goal 工具透传 `session_id=`）；② **上传文件解析指令**：`uploads/<name>` → `<UPLOADS_DIR 绝对路径>/<name>`，指示用 vibe-trading MCP `read_file` 读取（`/upload` 返回相对路径 `uploads/<safe_name>`（`uploads_routes.py:176`），opencode 原生 read 对 /workspace 解析会 miss，B6）；Web UI goal 走 REST 原样工作；MCP `_resolve_session_id` 回退链（`mcp_server.py:350-389`）不改 | F4 + Oracle 二轮 B6 |
| D9 | 认证配方（精确执行）：router 保留公网 Host；每租户 gateway 设 `API_ALLOWED_HOSTS=<公网host>`（`env_schema.py:312`，经 `security.py:113 _get_extra_loopback_hosts` 生效——Oracle 二轮 B2 修正：`EXTRA_LOOPBACK_HOSTS` 仅为内部 monkeypatch 注册键，**不是 env var**，写错则 router 转发的每个请求被 `_reject_untrusted_loopback_host` 403） + 强制 `API_AUTH_KEY`（**fail-closed：无 key 拒绝启动**，否则 router 的 loopback peer IP 触发 `_is_local_client` = 零认证，`security.py:507-518`）；opencode permission 走 OpencodeAgent 既有 render_config 治理（deny 编译），headless 下不引入交互式 permission 依赖，并遵守 kimaki 分层纪律：目录 ALLOW 规则进 server 生成配置**永不进 session scope**（findLast 覆盖序）、`external_directory` 恒为对象非字符串（deep-merge 陷阱）、`OPENCODE_CONFIG=<file>` 非 `CONFIG_CONTENT`（kimaki #90）、`experimental.continue_loop_on_deny:true` 兜底 TTL 自动 reject；**容器隔离必选**（opencode bash 面 + 租户凭据同边界） | F6 + 先例 §7.3 权限行 |
| D10 | 版本钉死（**是执行动作，不是现状**——Oracle 二轮 B1）：现 `OpencodeAgent/Dockerfile:16` 为 `opencode-ai@latest`、tmpl 插件为 `oh-my-openagent@latest`，**声明式钉版并不存在**，"冻结"只是 2026-08-31 镜像构建态的事实；T2 从运行中镜像提取实际版本，T10 将 `@latest` 改写为精确版本 + base image 钉 digest（此编辑 = 冻结的执行，不算违反 Must-NOT）。桥只用 legacy `/session` REST 表面 + `GET /event` SSE（全部 Python/桥接先例同款选择）；`@opencode-ai/sdk`/`@opencode-ai/client` 不引入（Python 侧 httpx 直连，**SSE 帧解析手写**——httpx-sse 不在 pyproject，httpx>=0.28 已在；T3 本就含帧解析单测）；**两代 API 并存且移动中** → 桥启动做能力探测（CodeNomad 模式），T1 golden trace 语料即版本漂移报警器。**T10 钉版裁决输入已齐（T2 memo §1.4 + T1 report §8）：三方分裂 1.18.18(全部镜像)/1.18.23(宿主生产)/1.18.30(npm latest，spike traces 录制版)；若钉非 1.18.30，必须按 drift alarm 用 `record_traces.py` 对钉版目标复跑并 diff 词汇/形状（重点：permission.asked payload 字段、新事件类型存在性——§8 标记 MED 敏感）** | Oracle 二轮 B1 + librarian 调查 + harness 冻结 |
| D11 | `LANGCHAIN_*` env 保留：auto-title（`sessions_routes.py:706` 直用 ChatLLM）与 swarm worker（`swarm/worker.py:24`）仍走 Python provider 栈（DashScope qwen3.8-max，与 harness 统一模型一致） | F2/F3 验证 |
| D12 | 桥内抽象一层薄 `EngineDriver` 协议（create_session/prompt/abort/events 四原语），opencode driver 为首个实现；若 harness-evolution S3 裁决换基座，仅替换 driver | 与评测决策门对齐 |

## Scope

### Must have
- `agent/src/opencode_bridge/`：`driver.py`（EngineDriver 协议 + OpencodeDriver：httpx REST+SSE、Basic Auth）、`translator.py`（事件翻译状态机）、`service.py`（OpencodeSessionService）、`recovery.py`（启动恢复 + 存活对账）、`config.py`（env 接入 `src/config/env_schema.py`，遵守 EnvConfig AST 门）
- `agent/src/api/state.py` 工厂开关（≤15 行改动）
- 契约测试：移植现有 fake 形状（`tests/test_channels_runtime.py:32`、`test_session_restart_recovery.py`、`test_api_live_runtime.py:355`）+ OpenBB adapter 形状（`openbb_bridge/adapter.py:106,125,144,215,253`）
- Phase 0 事件 trace 语料库（`.omo/evidence/opencode-engine-bridge-v2/traces/`）——同时是 translator 的 golden 测试夹具
- 租户容器：扩展 `OpencodeAgent/`（gateway 进程入镜像、entrypoint/supervisord、per-tenant home volume、fail-closed key）
- Router 容器：薄反代（tenant_registry.json、Host+token 双路由、wake-on-inbound）+ 租户开通脚本
- IM 流式彩蛋：translator → `bus.publish_outbound(OutboundMessage(metadata={"_stream_delta":True,"_stream_id":...}))`（session.config 已含 channel/chat_id，`runtime.py:329`；合并节流已存在 `manager.py:323-367`）
- Phase 4：`scheduled_research` MCP wrapper（双表面 docstring 同步 + 6 份 README 计数同步 + tool_selection eval before/after）；goal prompt 注入；Settings LLM 区在 ENGINE=opencode 下的重映射/隐藏
- mymain-wiki 新特性卡（F8 engine-bridge）+ `MYMAIN_DIVERGENCE.md` 记账 + 降级清单成文（10 项，见 §风险）

### Must NOT have (guardrails)
- **NO** 前端改动（`frontend/**` 零 diff）；**NO** channel 适配器改动（`src/channels/*.py` 16 个适配器零 diff；仅允许 bridge 侧新增 producer）
- **NO** 保护区改动：`agent/src/agent/**`、`agent/src/session/**`、`agent/src/providers/**` 零 diff（CI 断言）
- **NO** opencode `/api/*` preview 表面调用；**NO** opencode CLI/OmO 版本变更（冻结面）
- **NO** 评测冻结窗口内的 `agent/mcp_server.py` 工具面改动（Phase 4 wrapper 前置检查冻结状态，冻结中则顺延）
- **NO** `propose_mandate_profiles`/`trading_place_order`/`trading_cancel_order` 的 MCP 暴露（research-only 姿态）
- **NO** 共享 bot 的 channel-ingress 服务；**NO** desktop Electron 适配；**NO** 上游 PR（候选记入 divergence 账本待裁决）
- **NO** 硬编码密钥；**NO** 超 400 行文件（800 硬顶）；**NO** `os.getenv` 散落（走 EnvConfig schema）

## Verification strategy
> Zero human intervention - all verification is agent-executed.（Stage 门处的 HIL 呈报除外）
- Test decision: tests-after + pytest（仓库 ~4700 测试文化）；Python 用 `legonanobot` conda env；新依赖仅 httpx/httpx-sse（若 pyproject 已有则零新增）
- 全局门：`pytest --ignore=agent/tests/e2e_backtest --tb=short -q` 全绿；black + ruff；保护区/前端/适配器三个零 diff 断言脚本
- Golden 测试：Phase 0 录制的 trace 语料 → translator 单测夹具（多工具回合/abort/continuation/subagent/permission 五类各≥1）
- 契约测试：现有 fake-service 测试套件形状移植跑在 OpencodeSessionService 上
- E2E：脚本化 Web 聊天回合（SSE 全词汇断言 + replay=active + run_dir→/runs/{id} 200 + cancel + 409 busy + 刷新恢复）；IM mock channel 往返 + 真平台（钉钉或飞书其一）冒烟
- 多租户验收：双租户隔离矩阵（A key 打 B→401；浏览器 POST 过 router→200 非 403；SSE ticket 流；IM 双 bot 独立）+ RSS 实测（空闲/活跃）+ 冷启动唤醒延迟
- Evidence: `.omo/evidence/opencode-engine-bridge-v2/`
- Phase 门：Phase 0 = go/no-go 硬门（S1-S3 任一证伪且无缓解 → 停止，启用应急预案见风险 R1/R2）；Phase 1→2→3 顺序门；Phase 4 受冻结门控可与 Phase 3 并行

## Execution strategy
### Parallel execution waves
- **Wave 0（纪律行，非 todo）**：自 `mymain` 切 `mymain-engine-bridge` 分支 + worktree 施工；确认 harness-evolution 冻结窗口状态并记录
- **Wave 1（Phase 0 spike，2 todos 并行）**：T1 事件录制 rig ∥ T2 镜像基线盘点 → **go/no-go 门**
- **Wave 2（Phase 1 桥接，5 todos）**：T3 driver+config → {T4 translator ∥ T5 service 契约} → T6 recovery+所有权规则 → T7 工厂开关+E2E（汇合点）
- **Wave 3（Phase 2 IM，2 todos）**：T8 通道验证 ∥ T9 流式彩蛋（T8 先行冒烟后 T9 可并行）
- **Wave 4（Phase 3 多租户，3 todos）**：T10 租户镜像 → T11 router+开通脚本 → T12 隔离矩阵+资源实测
- **Wave 5（Phase 4 保真，3 todos 并行，冻结门控）**：T13 scheduled_research wrapper ∥ T14 goal 绑定 ∥ T15 Settings 重映射+wiki 记账

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
|---|---|---|---|
| T1 事件录制 rig | — | T4,T5,go/no-go | T2 |
| T2 镜像基线盘点 | — | T10,go/no-go | T1 |
| T3 driver+config | T1,T2 | T4,T5 | — |
| T4 translator | T3 | T7 | T5 |
| T5 service 契约 | T3 | T6,T7 | T4 |
| T6 recovery+所有权 | T5 | T7 | T4 |
| T7 工厂开关+E2E | T4,T5,T6 | T8 | — |
| T8 IM 验证 | T7 | T9(软),T10 | T9 |
| T9 流式彩蛋 | T7 | — | T8 |
| T10 租户镜像 | T2,T8 | T11,T12 | — |
| T11 router+开通 | T10 | T12 | — |
| T12 隔离矩阵 | T10,T11 | — | T13-T15 |
| T13 scheduled wrapper | 冻结门 | — | T14,T15 |
| T14 goal 绑定 | T7 | — | T13,T15 |
| T15 Settings+wiki | T7 | — | T13,T14 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

### Wave 1: Phase 0 — Spike（go/no-go 硬门）

- [x] 1. 事件录制 rig：真实 trace 语料库 + 五项断言 — **完成 2026-09-12**（commit `aaad7546`；verdict GO + 3 强制条件已回写 D4/D5；按用户指令纯本地 serve 1.18.30 + OmO 4.19.4，无 docker，版本 delta 标记；产物：spike_report.md 242 行、8 份 sanitized traces 972KB、record_traces.py + analyze_traces.py（ActivityScanner = T4 复用的静默语义）；五项断言全过 + (f)/(g)/(h)/(i) 测量完成；成本 $0.66/35 model steps 已如实披露）
  What to do: 起生产同构环境：**必须用 OpencodeAgent 镜像内的 serve**（如本地安装则必须与 T2 记录版本逐位一致——B1：防 trace/生产版本分裂；spike_report 首行记录 opencode + OmO 版本）。脚本驱动五类场景并录制 `GET /event` 原始 SSE：(a) 多工具回合（含 MCP 工具 + bash）；(b) 中途 abort；(c) OmO stop-hook/todo continuation 触发的再 prompt；(d) `task()` 子代理 spawn（含 parentID 链）；(e) permission 事件 + POST `/session/:id/permissions/:permissionID` 应答解锁。录制范围须覆盖：`message.part.delta`（token 增量是否存在及形状）、`session.status{busy|idle|retry}`、`session.error` 后是否跟随 idle（kimaki #74 验证）、natural-completion 标记（`message.time.completed`+`finish`）。**附加测量（后续 Phase 依赖，Oracle 二轮 §5）**：(f) idle→continuation 首事件间隙分布（校准 `QUIESCENCE_S`，B4）；(g) 长时 MCP 工具内部静默间隙长度分布（定 heartbeat 合成参数，B3）；(h) `DELETE /session/:id` 在钉版上可用（F7 级联依赖）；(i) `message.part.delta` 在钉版存在性（D5 主路径 vs fallback 裁决，B1）。断言：插件加载日志存在；每逻辑回合 idle 计数；abort 终态可与正常完成区分；子代理事件带父链（对照 kimaki 公开 fixtures `cli/src/session-handler/event-stream-fixtures/*.jsonl`）。产出 `spike_report.md`（go/no-go 结论 + 与 D4/D5 假设的偏差表 + (f)-(i) 测量值）+ traces/ 语料（Phase 1 golden 夹具）。
  Must NOT do: 不改镜像内任何钉版；不录含真实凭据的 trace（脱敏）；不超过半天——超时即按现有证据出报告并标注未覆盖项。
  Parallelization: Wave 1 | Blocked by: — | Blocks: T3, go/no-go
  References: opencode docs/server（legacy 表面）；OmO continuation 机制（其 docs stop-hook/ralph）；D4/D5
  Acceptance criteria: 五类 trace 各≥1 份落盘；spike_report.md 含明确 go/no-go 与偏差表
  QA scenarios: happy: 断言脚本全过 → go；failure: idle 计数≠1/回合或 continuation 不可区分 → 记录缓解选项（静默计时器调参/禁用 OmO continuation 于 serve 模式）交 HIL 裁决
  Commit: Y | eval(engine-bridge): phase-0 event trace corpus and spike report

- [x] 2. 镜像基线盘点：冻结面清单 + gateway 依赖可行性 — **完成 2026-09-12**（commit `14bf3fe3`；baseline_memo.md 210 行：版本矩阵实测值、vendored VT blob 级溯源（v2.1.1=pre-rebase mymain@3b132b3a MCP 73/78——计划的 77/82 校验在领先候选镜像上不通过；v2.2.0=NewAgentMain@01b07974 77/82 ✓）、B5 实锤+宿主同源陷阱、冻结兼容清单 6 面 + @latest→pin 精确转换表、.[channels] 干跑干净（linux-amd64 218 包零冲突）、T10 缺口（镜像无前端 dist/无 supervisord）、生产候选歧义诚实裁决（host-direct 为事实生产））
  What to do: 盘点 OpencodeAgent 镜像：vt 源码分支（须为 mymain 血统，含 F2/F5）、**opencode CLI / OmO 实际版本——从运行中镜像提取**（`docker run --rm <image> opencode --version`；OmO 读其 node_modules/oh-my-openagent/package.json——Dockerfile/tmpl 当前为 `@latest`，无声明式钉版，记为"事实漂移风险"并把"@latest→精确版本"列为 T10 前置项，B1）、`VT_MEMORY_MCP_TOOLS` 状态、MCP 工具计数（77/82 校验）、nano-search-mcp 版本、**`ENV_PATH`/`HOME`/`VT_MEMORY_BASE_DIR` 与 volume 布局现状**（B5 输入）；验证 gateway 增量依赖可装入镜像：`pip install -e ".[channels]"` 干跑（或至少 telegram+dingtalk/feishu extras）、前端 dist 构建产物可被 SPAStaticFiles 服务、supervisord（或等价）可管理双进程。产出 `baseline_memo.md` + 冻结兼容清单（哪些面在评测窗口内禁触）+ 版本矩阵（实际值，非 Dockerfile 声明值）。
  Must NOT do: 不升级任何钉版；不改 render_config/工具治理；不实际推送镜像。
  Parallelization: Wave 1 | Blocked by: — | Blocks: T10, go/no-go
  References: mymain-wiki/features/f7-opencode-agent.md、f2-mcp-memory-tools.md；OpencodeAgent/Dockerfile、entrypoint.sh、docs/TENANT-IMAGE-GUIDE.md（原 docs/DEPLOY-GUIDE.md 已于 2026-09-20 拆分：§T10+§T11 抽出为该活文档，其余归档至 docs/archive/DEPLOY-GUIDE-opencode-web-host-direct.md）；harness-evolution 冻结清单
  Acceptance criteria: baseline_memo.md 落盘，含版本矩阵 + 依赖干跑结果 + 冻结清单
  QA scenarios: happy: 镜像内 `python -c "import src.api"` 成功且 channels extras 可解析；failure: 依赖冲突 → 记录为 T10 的镜像分层整改项
  Commit: Y | eval(engine-bridge): phase-0 image baseline and freeze-compat memo

### Wave 2: Phase 1 — 桥接模块（单租户）

- [x] 3. EngineDriver 协议 + OpencodeDriver + 配置接入 — **完成 2026-09-12**（commit `03332696`：四原语协议 + subscribe-first 成文 + 手写 SSE + backoff 500ms→30s + 工具名映射双钉版源码验证；62 测试）
  What to do: `agent/src/opencode_bridge/driver.py`：EngineDriver 协议（create_session/prompt_async/abort/events 四原语，D12）+ OpencodeDriver 实现（httpx AsyncClient，Basic Auth，legacy `/session` 表面：POST /session、POST /session/:id/prompt_async、GET /event SSE 解析、POST /session/:id/abort、GET /session/:id/message；启动时拉 MCP tools/list 建立"前缀名→裸名"映射表）。`config.py`：新 env 进 `src/config/env_schema.py`（`VIBE_TRADING_ENGINE`、`OPENCODE_BASE_URL`、`OPENCODE_SERVER_PASSWORD`、`OPENCODE_BRIDGE_QUIESCENCE_S=8.0`（T1 实测校准，原 3.0 被证伪）、`OPENCODE_BRIDGE_CHILD_EVENTS=drop`）。单测：SSE 帧解析、映射表构建、driver 原语（httpx mock + T1 真实 trace 回放）。SSE 客户端健壮性为必做项（修复 opencode-runtime 无 backoff 局限）：每租户实例单一持久连接、backoff 500ms→30s 收到事件清零、断线自动重连（kimaki global-listener 模式）；prompt_async→订阅时序二选一（先发后订，opencode-runtime 理由：受理即返、delta 待模型输出才开始；或先订+缓冲去重）并在模块 docstring 成文。
  Must NOT do: 不用 `/api/*`；不在 driver 里做事件语义翻译（那是 T4）；不散落 os.getenv。
  Parallelization: Wave 2 | Blocked by: T1,T2 | Blocks: T4,T5
  References: D5/D10/D12；opencode OpenAPI（/doc）；env_schema AST 门（tools/ci_grep_gates.sh 约定）
  Acceptance criteria: driver 单测绿；env 门通过；文件≤400行
  QA scenarios: happy: 对 live serve 建会话+prompt_async+收事件+abort 全链路脚本通过；failure: serve 不可达 → 明确 ConnectionError 包络，不静默
  Commit: Y | feat(engine-bridge): opencode driver and env config

- [x] 4. EventTranslator：opencode 事件 → vt SSE 词汇表（状态机 + golden 测试） — **完成 2026-09-12**（commit `7967210f`：translator/ 6 模块；3 强制条件代码级落实；golden 覆盖全 8 trace——scenario g 真实 120.75s 静默→40 个精确 3s heartbeat；T7 追加 run_dir bash-runner INPUT 收割修复（治理禁用 backtest MCP 工具的真实路径，FINDINGS.md #1））
  What to do: `translator.py`：按 D4/D5 实现——text part 差分 `text_delta{delta,iter,attempt_id}`；reasoning 600 字符滚动 tail `reasoning_delta{tail,iter,chars}`；tool part 状态机 → `tool_call{tool(裸名),arguments(每值≤200),call_id}` / `tool_result{tool,status,elapsed_ms,preview(前200字符),call_id}`；`run_dir` 正则提取（run_backtest 类输出）暂存至 attempt 终态；attempt 状态机：RUNNING→(idle+静默计时器到期)→completed /(abort 标志)→cancelled /(session.error)→failed；子会话事件按 `OPENCODE_BRIDGE_CHILD_EVENTS` 丢弃；`llm_usage` 从 message.updated tokens 合成（best-effort）。Golden 测试：T1 五类 trace 逐份回放，断言输出事件序列与 vt 词汇表/负载形状完全匹配（含 `Agent.tsx` 消费字段）。**Golden 增补（Oracle 二轮 B3/B4）**：静默间隙 >90s 的长工具 trace（可合成）→ 断言 `tool_heartbeat` 序列存在且间隔 ~3s；终态 payload 逐字段断言（`summary/run_dir/elapsed_ms/provider/model`，对齐 `service.py:408-414`，前端优先取 `d.summary`）；terminal 后同 attempt 迟到事件 → 断言丢弃 + warn 日志。translator.py 预计超 400 行软上限——**预授权拆分子模块** `translator/{text,tools,lifecycle}.py`。
  Must NOT do: 不做持久化（T5 职责）；不改前端期望形状去迁就翻译（前端是契约）。
  Parallelization: Wave 2 | Blocked by: T3 | Blocks: T7 | 可并行: T5
  References: D4/D5；useSSE.ts:86-96；Agent.tsx:692-1240（handler 假设）；loop.py:1398/1411/2286/2881（负载对齐源）
  Acceptance criteria: 五类 golden 全绿；translator 单测覆盖差分/tail/preview 截断/裸名映射/静默计时器
  QA scenarios: happy: 多工具 trace 回放产出配对完整的 tool_call/tool_result；failure: continuation trace 中 idle 后新 part → 不发 terminal（计时器重置）
  Commit: Y | feat(engine-bridge): event translator with golden trace tests

- [x] 5. OpencodeSessionService：完整契约实现 + 移植契约测试 — **完成 2026-09-12**（commit `6530a7f3`：D6 全契约（7 方法/store/event_bus 原样复用/同型 SessionBusyError/metadata 枚举与 native 一致/tool_trail 委托 native recorder/last_attempt_id/FTS 锚点/D8① 注入）；结构化 Protocol+stub 与 T4 并行零耦合；T7 补全 D8② 上传解析（tool-agnostic 措辞，FINDINGS.md #2））
  What to do: `service.py`：实现 D6 全契约——7 方法 + `.store`（复用 SessionStore 原样写 session.json/messages.jsonl/attempts/partial_response）+ `.event_bus`（复用 EventBus）+ 抛同类 `SessionBusyError` + `send_message(session_id, content, role="user", *, include_shell_tools=False)` 兼容位置参数调用 + 回复 Message 带 `linked_attempt_id`/`metadata{status,elapsed_ms,started_at,provider,model}`/`tool_trail`（从 translator 工具事件累积）+ FTS `index_message/index_session` + `message.received`/`attempt.created/started` 生命周期事件 + goal prompt 注入块（D8 的注入点在此预留）。契约测试：移植三个现有 fake-service 套件形状 + OpenBB adapter 消费形状（subscribe/clear/append_message）跑在本实现上。
  Must NOT do: 不改 SessionStore/models/events 本体（保护区）；不自造消息格式。
  Parallelization: Wave 2 | Blocked by: T3 | Blocks: T6,T7 | 可并行: T4
  References: D3/D6/D8；service.py:99-154,227,284,419 对齐点；runtime.py:340-348；scheduled_routes.py:88,109-114；openbb_bridge/adapter.py
  Acceptance criteria: 契约测试全绿；busy→409 语义、metadata.status、tool_trail 形状断言通过
  QA scenarios: happy: 并发第二次 send_message 抛 SessionBusyError；failure: opencode prompt 受理失败 → attempt.failed 事件 + 错误落 metadata，不悬挂
  Commit: Y | feat(engine-bridge): session service with full seam contract

- [x] 6. 启动恢复 + opencode 存活对账 + 级联生命周期 — **完成 2026-09-12**（commit `8fc1fe75`：三分支恢复（重挂含 watch timer TOCTOU 补写/补写终态/interrupted 对齐 native）+ replay=active 不重播死回合断言 + D3 所有权 docstring + `mymain-wiki/features/f8-engine-bridge.md` 卡；子类组合零冻结文件编辑；T7 契约 = construct 后 `await service.reconcile()`）
  What to do: `recovery.py`：网关启动时对每个 pending/running attempt——查 opencode 会话状态：仍在跑→重挂 SSE 保持 running；已完成→补写终态消息+事件；opencode 无此会话→按现有 interrupted 语义落盘（对齐 `service.py:101-154` 行为）。级联：`delete_session`→DELETE opencode session；IM `/new`（runtime.reset_session 丢映射后新建）→新 opencode session。所有权规则（D3）写入模块 docstring + wiki 卡。测试：杀网关-恢复三分支各一例（用 T1 rig 可重放）。
  Must NOT do: 不在恢复中重发 prompt（永不重复执行）；不改 runtime.reset_session 本体。
  Parallelization: Wave 2 | Blocked by: T5 | Blocks: T7 | 可并行: T4
  References: D3；F2 恢复分叉场景（opencode 独立进程存活）；checkpoint.py 行为对齐
  Acceptance criteria: 三分支恢复测试绿；恢复后 replay=active 不再重播死回合
  QA scenarios: happy: 网关重启后进行中回合重挂继续流式；failure: opencode 也死了 → attempt 落 interrupted + IM 轮询在超时前拿到终态消息
  Commit: Y | feat(engine-bridge): crash recovery with engine liveness reconciliation

- [x] 7. 工厂开关 + 单租户 Web E2E（Phase 1 汇合门） — **完成 2026-09-13**（commit `225ba2f3`：state.py 恰好 15 行分支 + wiring.py（真实翻译器注入/preflight/优雅停机）+ api_server 5 行 preflight 钩子；活体 rig（serve 14096 + gateway 18080 + 前端构建 + playwright 无头）八组断言 67 检查全 PASS（含 >90s 静默回合破 watchdog、上传→分析绝对路径生效）；全局门：失败集与基线 9 既有全等（4 个基线污染显式列名排除）、三零 diff、black/ruff 干净；证据 `.omo/evidence/.../t7-e2e/`（截图×9 + FINDINGS.md + gate 输出 + 成本）；rig 工具入库 `agent/tests/e2e_engine_bridge/`（env-gated，可复跑）；桥套件 192 绿）
  What to do: `state.py::_get_session_service()` 按 `VIBE_TRADING_ENGINE` 分支（≤15 行）；auto-title 路由保持 ChatLLM（D11，零改动）。E2E 脚本：起 gateway(ENGINE=opencode)+opencode serve+vt MCP → 浏览器脚本化回合：发消息→SSE 全词汇流式→回测 prompt→`attempt.completed.run_dir`→`/runs/{id}` 200 + run 卡片数据→cancel 中途→409 并发→刷新页面历史恢复（tool_trail 重建）→replay=active 中途重连→**长回合**（含 >90s 静默间隙，断言不触发前端 watchdog timeout 归档，`Agent.tsx:1247-1269`——B3 的 E2E 防线，快 fixture 会漏掉此 bug）→**上传→分析回合**（D8 注入块的上传路径解析生效，B6）。全局门：pytest 全绿 + black/ruff + **三个零 diff 断言**（保护区/前端/适配器）。
  Must NOT do: E2E 不跳过任何断言项；不放宽全局门。
  Parallelization: Wave 2 汇合 | Blocked by: T4,T5,T6 | Blocks: T8
  References: state.py:64；D1；AGENTS.md 测试门槛
  Acceptance criteria: E2E 全断言通过 + 全局门绿 + 零 diff 断言绿；证据落 .omo/evidence/
  QA scenarios: happy: 完整回合含 run 卡片；failure: 任一 SSE 形状错 → 前端 handler 静默丢弃即视为 FAIL（以断言而非肉眼为准）
  Commit: Y | feat(engine-bridge): engine factory switch and web e2e gate

### Wave 3: Phase 2 — IM 通道

- [x] 8. IM 零改动验证 + 恢复时序 + 级联 — **完成 2026-09-13**（套件 commit `84293175`：s0/s1/s3/s4 全 PASS——193s 长回合 D4 实证 94 次轮询全空、57 heartbeat 中位 3.0s、/new 级联 e2≠e1 旧会话按 D3 保留、简报 send_with_receipt 逐字送达；适配器/runtime 零 diff 绿；真平台冒烟 fail-closed 交付 `real_platform_smoke.py` 待用户测试 bot 凭据；成本 ~$0.19 披露。s2 曾 xfail = 桥 bug T8-1（杀 serve 后 attempt 永不落终态，IM 590s 才失败）→ **修复 commit `90a4378a`：driver 内 stream_liveness 双有界信号（4 零帧周期/15s 无帧窗，heartbeat 10s 为存活基准），实测 8.03s 落 failed + IM 同刻失败回复，引擎回归免网关重启自愈（_ensure_pumps），零服务侧改动，scenario g 真实数据无误杀守卫；s2 翻真断言官方套件重跑绿；桥套件 227 绿**）
  What to do: mock channel 脚本往返（3 分钟长回合，轮询契约满足）；真平台冒烟（钉钉或飞书其一，用租户自备测试 bot）；杀 opencode 中途 → attempt <30s 落 failed（非 600s 挂起）且 IM 收到失败回复；`/new` `/reset` `/pairing` 命令回归；定时研究简报投递路径（send_with_receipt）冒烟。
  Must NOT do: 不改 16 适配器与 ChannelRuntime 一行。
  Parallelization: Wave 3 | Blocked by: T7 | Blocks: T10(软) | 可并行: T9
  References: runtime.py:189,327,336-349；scheduled_routes.py:129-143
  Acceptance criteria: 四类场景证据落盘；适配器/runtime 零 diff 断言绿
  QA scenarios: happy: 钉钉群消息→最终回复含 markdown 卡片；failure: opencode 死 → 600s 内明确失败文案而非静默
  Commit: Y | test(engine-bridge): IM channel parity suite under opencode engine

- [x] 9. IM 流式彩蛋：_stream_delta 生产者 — **完成 2026-09-13**（commit `361e1a41`：`im_stream.py` 391 行 + service_persistence 最小 tap（+24/−3，异常隔离）+ wiring 挂接；grinev 阶梯 1s→2s→5s→10s 叠加 manager coalescing；mock 平台 3/3 PASS（流式开=渐进编辑成型/关=终态单条回退/乱序 _stream_id 隔离）；**架构发现：全仓库含上游 main 从不存在 _stream_delta 生产者——消费端基建 101306e9 休眠落地，T9 是首产者，契约自冻结消费端镜像**；排雷×2：_stream_end 不得携带 _stream_delta（coalescer 吸收致文本丢失，测试钉死）、双重回答用一次性 _streamed tagger 于 publish_outbound 缝解决（channels 零改动）；29 新测试；真平台 Telegram 冒烟 fail-closed 交付 `telegram_smoke.py` 待测试凭据；桥套件 222 绿亲验）
  What to do: translator 的 text_delta 旁路 → 按 session.config 的 {channel, channel_chat_id}（`runtime.py:329`）publish `OutboundMessage(metadata={"_stream_delta":True,"_stream_id":attempt_id})`，终态发 `_stream_end`；受每通道 `streaming` 配置开关门控。断言：manager 合并节流生效（Telegram 编辑而非刷屏、无重复消息）。节流策略 = grinev 渐进式（按会话年龄 1s→2s→5s→10s 封顶）叠加 manager coalescing；flush 顺序纪律：pending tool 行必须先于 permission/question 类内容发出（grinev flush ordering）。
  Must NOT do: 不改 manager/适配器；不绕过既有去重指纹。
  Parallelization: Wave 3 | Blocked by: T7 | Blocks: — | 可并行: T8
  References: manager.py:323-367（coalescing）；base.py send_delta 契约
  Acceptance criteria: 双平台（mock+真）流式编辑证据；关闭开关回退终态单条
  QA scenarios: happy: Telegram 单条消息持续编辑成型；failure: 乱序 delta → _stream_id 隔离不串话
  Commit: Y | feat(engine-bridge): IM streaming via stream_delta producer

### Wave 4: Phase 3 — 多租户

- [x] 10. 租户容器：OpencodeAgent 镜像扩展 gateway 进程 — **完成 2026-09-13**（commit `06507813`：钉版四处转换（`opencode-ai@1.18.30` 用户已确认 + `oh-my-openagent@4.19.4` tmpl/entrypoint 双处 + base `:v3.0.0-tenant` config digest 记录——字面 manifest digest 待 registry push 用户门控）；supervisord 双进程（serve 容器内部 127.0.0.1:4096 不暴露 / gateway 0.0.0.0:8080 单一公网口、纯 HTTP 客户端）；B5 volume 实证（named volume 挂 /home/opencode 全状态入卷、down&&up Settings 持久、VT_MEMORY_BASE_DIR 并入卷、VIBE_TRADING_HOME 分叉 fail-closed）；B6 external_directory **对象形状**（kimaki deep-merge 陷阱）+ uploads ALLOW 置 findLast 广域 ask 规则之后 + MCP env parity entrypoint 断言；前端 dist 入镜像（build.sh 构建 SPA 落 _FRONTEND_DIST 位）；vendoring=mymain-engine-bridge（OCI label 记档 commit）；**Rosetta 跑通 amd64 compose E2E 全绿**：无 key 拒启 exit 1 / Web 聊天往返 9/9 全链 / **serve 崩溃→supervisord 重启→gateway pid 不变→桥自愈后回合 9/9（T8-1 修复的生产形态实证）** / B2 受信 Host 200 非受信 403 / SPA 容器供给 / auth 200/401；config render 测试 24→47 绿（亲跑）；IM 往返以 T8 mock parity 于同一桥代码满足（diff 空验证）+ 容器 channels-status，真 bot 冒烟维持用户门控；**已知降级诚实成文**：nano-search-mcp 用 mcp v1 API 与 VT 拉入的 mcp 2.2.0 不兼容（核心 VT MCP 82 工具连通、全链工作；修复=独立的 fastmcp-4.x 迁移，KNOWN_DEGRADATION 文档）；证据 `.omo/evidence/.../t10-container/`（E2E_REPORT/PIN_RECORD/构建与 E2E 全日志））
  What to do: 镜像增层：vt gateway（uvicorn api_server，ENGINE=opencode，含 SPA dist + channels extras）与 opencode serve 双进程——**进程模型裁决（消除自相矛盾，Oracle 二轮注 1）：supervisord 管理、opencode 绑固定 127.0.0.1:4096、gateway 为纯 HTTP 客户端**（T6 存活对账 = health probe；opencode-runtime `process.py` 进程卫生与 openwork spawn 参数**仅当未来启用 gateway 自 spawn 模式时适用**，默认架构不用）。**版本钉死执行（B1 前置）**：Dockerfile `opencode-ai@latest` 与 tmpl `oh-my-openagent@latest` 改为 T2 记录的精确版本、base image 钉 digest（此编辑 = 冻结的执行，不算违反 Must-NOT）。**volume 与状态对齐（B5）**：per-tenant volume 挂载点 = `$HOME/.vibe-trading`，`VIBE_TRADING_HOME` 要么不设要么等于它（`helpers.py:31` ENV_PATH 硬编码 `Path.home()`，两者分叉则 Settings 写入落容器临时层、重建即丢）；`VT_MEMORY_BASE_DIR` 改指 volume 内路径。启动强制校验 `API_AUTH_KEY` 存在否则退出（fail-closed，D9）；`API_ALLOWED_HOSTS` 注入（B2）。**上传可达性（B6）**：server 生成配置的 `external_directory` ALLOW 对象覆盖 UPLOADS_DIR（永不进 session scope，D9）；断言 MCP 子进程 env 与 gateway 一致（HOME/VIBE_TRADING_HOME）。vt 源码改从 mymain 血统分支构建（含 bridge 模块）；校验 opencode.json.tmpl 的 MCP 条目为 `{type:"local",command:[...]}` 形状（GolemBot #42：Claude 式 `{command:"str"}` 条目令 opencode 拒绝启动）。单租户 compose 本地 E2E（Web+IM 全通 + 上传→分析 + Settings 写入经容器重启存续）。
  Must NOT do: 不改 render_config 工具治理与冻结钉版；不在镜像里存任何真实密钥。
  Parallelization: Wave 4 | Blocked by: T2,T8 | Blocks: T11,T12
  References: D2/D9；OpencodeAgent/Dockerfile、entrypoint.sh、docker-compose.yml；F7 卡
  Acceptance criteria: 单容器 compose 起来后 Web 聊天+IM 往返全通；无 key 启动被拒
  QA scenarios: happy: 容器重启后恢复对账生效（T6 路径）；failure: gateway 崩而 opencode 活 → 监督拉起 + 恢复不重复执行
  Commit: Y | feat(opencode-agent): tenant container with vt gateway process

- [x] 11. Router + 租户开通脚本 — **完成 2026-09-13**（commit `05c8058b`，37 文件 6994 行：`OpencodeAgent/deploy/router/` 薄反代（token sha256 registry 防泄漏 + 原子写 + mtime 热加载；解析优先级穷尽 Resolved/UnknownHost/UnknownToken/TenantMismatch——403 不猜、404 不误路由；Host 保留转发含 B2 论证；SSE portal 配方全语义（禁缓冲/retry 归代理/断连传播/合成 router.error 帧）；wake-on-inbound 含 body 重放 + /health 轮询（T10 preflight 后才应答）+ 503/Retry-After/具名兜底页——**E2E 抓到 Docker stop 后 accepts-then-resets 的 ReadError 形态并回归钉死**；空闲回收照 opencode-router 问引擎取 time.updated；openwork directory-fence 串行化 dispose/prompt；ECS wake 路径成文不实现）+ `provision_tenant.py`（幂等重跑复用 key 不静默轮换、LANGCHAIN_*/SSE_TIMEOUT 种子、0600 env 文件）；**双租户 E2E 59/59 全过**（含未注册 Host 404 不误路由、冷启动唤醒链路）；OpencodeAgent 测试 226 绿亲跑）
  What to do: 薄反代容器（uvicorn+httpx 或 Caddy，二选一，默认 Python 薄代理 ≤300 行）：tenant_registry.json（token→tenant→upstream、Host→tenant 映射）；**Host 保留**转发；SSE 透传（禁缓冲）；wake-on-inbound（容器停止时 docker/ECS API 拉起，超时页兜底）。开通脚本 `provision_tenant.py`：生成 API_AUTH_KEY、建 home volume 骨架、渲染 agent.json channels 段（租户 bot 凭据占位）、opencode.json 走既有 tmpl、注册进 registry。渲染清单补充（Oracle 二轮注 8）：种子 `LANGCHAIN_*`（D11 auto-title 按租户依赖）+ `VIBE_TRADING_SSE_TIMEOUT`（前端看门狗输入，`settings_routes.py:397`）。SSE 代理配方照抄 portal `events.ts`：`text/event-stream` + `X-Accel-Buffering:no`、retry 归代理所有（客户端 `sseMaxRetryAttempts:0` 语义）、客户端断连 cancel 传播上游、合成错误事件注入而非静默死亡。空闲回收真值检查照抄 opencode-router：代理侧活动记录不充分（WS/SSE 不打访问日志路径）→ 删除前问引擎 `GET /session?limit=1&roots=true` 取 `time.updated`。租户删除与在途消息竞争：openwork directory-fence 模式（per-tenant promise/lock 链串行化 dispose 与 prompt admission）。若未来引入 permission 中继：应答前校验 session→租户目录所有权（CodeNomad ownsDirectory 模式）。
  Must NOT do: router 不长业务逻辑（鉴权只做 token→tenant 解析，业务鉴权在租户 gateway）；不做共享 bot 分流。
  Parallelization: Wave 4 | Blocked by: T10 | Blocks: T12
  References: D2/D9；F6 配方；issue #20067 社区模式（opencode-router/opencode-runtime 参照——细节以 §7 先例调查为准）
  Acceptance criteria: 开通两租户后 registry/路由/唤醒全链路脚本通过
  QA scenarios: happy: 新租户开通→首条 IM 消息冷启动唤醒→回复；failure: 未注册 Host → 404 而非误路由
  Commit: Y | feat(opencode-agent): tenant router and provisioning

- [x] 12. 隔离矩阵 + 资源实测 + 回收策略（Phase 3 门） — **完成 2026-09-13**（commit `dc64dffc`：**93/93 矩阵全绿，零跨租户可达 → Phase 3 门 PASS，F1 架构主张存活**（证伪仪器未触发：QA failure 场景「任一跨租户可达→回炉」全程未出现）；六项矩阵逐项实测并标注证明层级（M1 直连 upstream 401×双向+正对照 200；M2 浏览器 POST 经 router 201 非 403（B2 配方端到端）+跨站 Origin 403 负对照；M3 ticket 单网关进程/单次使用/跨租户双向 401 + B 真回合期间 A 流 0 帧 0 字节；M4 REST+store 双层不相交（foreign grep 0 命中）；M5 三层：并发容器内 MockChannel 探针（T8 imlib 模式、生产 IM 布线、窗口重叠实证）零串话 + config/env 零外来物 + 真双 bot 冒烟 USER-GATED 交付（无凭据拒绝已演示、镜像内 SDK 实证 dingtalk-stream/lark-oapi）；M6 分离 compose 网络、4096 未发布、容器内 A 视角 B 经 DNS/容器IP/宿主发布端口全部不可达、PID 命名空间仅自身栈、文件系统零命中）；**实测**（2.5s 窗口采样非快照）：空闲 RSS 1039-1048 MiB、网关回合活跃 1290-1298 MiB、IM 探针形态至 1455 MiB（探针第二栈为测量伪影已成文）；冷启动唤醒 n=4：18.503/18.599/19.696s（T11 基线 19.7-40.9）；IM 首响 2.07-6.06s（首个出站=T9 流式 delta）/终态 10.1-14.2s；**与 0.7-1.3GB 估算偏差诚实呈报**：空闲在带内（1.09-1.10GB），活跃峰值十进制 GB 超上限 5-18%（R4 缓解梯未触发，容量规划建议 ~1.4GB/租户活跃）；**回收策略加速 N 端到端**（TTL 15s+运维侧调度器；生产默认 10800s）：空闲租户被无人值守停止（engine 真值裁决、栅栏 drain）、入站唤醒（4 样本中 2 个为回收后唤醒）、**容器内活回合仅凭 engine 真值检查保住**（代理账本按设计陈旧=IM/cron 形态；回合中 0 stop 裁决、keep 引用 truth_source=engine idle_ms=1245、time.updated 7.4s 内跟踪回合）；**发现（记录不修，冻结面）**：VT_ROUTER_RECLAIM_INTERVAL_S 已解析但未接线（router 内无周期循环，生产=cron/systemd 或 ~10 行 router 特性提交）、wake-vs-drain 交互注记；成本：权威轮 4 次极简回合（+归档验证轮 4 次，其唯一 FAIL 为 harness 检查窗 bug——已修复且 run1-debug 原始裁决时间戳佐证窗内行为正确），113k input tokens ≈ ¥0.272 ≈ $0.038 真实 DashScope 支出（<$0.20 预算 ~5x 余量；serve 价格库口径 $0.28 并列记录）；套件：OpencodeAgent/tests 169p+1s（158 既有=T11 基线 + 12 新 docker-free）、桥套件 233p+7s 与 T13 基线逐位一致、全局门 12070 passed 零新增失败（9 失败=T7 记录环境基线严格子集）；black+ruff 净、新文件全部 ≤400 行；agent/**、frontend/、保护区、翻译器/golden、T13 mcp_server/README 零 diff（未修改任何已跟踪文件、无需 OpencodeAgent 配置修复）；rig 已拆除（容器/卷/网络/scratch/端口全清）；证据 `.omo/evidence/opencode-engine-bridge-v2/t12-tenancy/`（未跟踪）+ 报告 `OpencodeAgent/docs/tenancy_report.md`）
  What to do: 双租户同机验收：A 的 key 打 B upstream→401；浏览器经 router POST→200（非 403，验证 Host+`API_ALLOWED_HOSTS` 配方，B2）；SSE ticket 各自独立；A 的会话列表不含 B；IM 双 bot 互不串话；跨租户 opencode/MCP 进程不可互访。实测：每容器空闲/活跃 RSS、冷启动唤醒延迟、IM 首响延迟。回收：空闲 N 小时停容器策略 + 入站唤醒验证。产出 `tenancy_report.md`（含与 0.7-1.3GB/租户估算的偏差）。
  Must NOT do: 不跳过任何矩阵项；估算值不得冒充实测值。
  Parallelization: Wave 4 汇合 | Blocked by: T10,T11 | Blocks: — | 可并行: T13-T15
  References: D2/D9；Oracle Phase 3 证伪表
  Acceptance criteria: 矩阵全绿 + tenancy_report.md 落盘
  QA scenarios: happy: 全矩阵通过；failure: 任一跨租户可达 → Phase 3 FAIL，回炉 router/容器网络
  Commit: Y | test(opencode-agent): tenant isolation matrix and resource report

### Wave 5: Phase 4 — 保真回填（冻结门控，可并行）

- [x] 13. scheduled_research MCP wrapper + IM confirm 流复活 — **完成 2026-09-13**（commit `0c4ca311`：**冻结预检裁决 LAPSED→PROCEED**（8 条证据引用：评测窗口 08-30 关闭、目录 INDEX 归档、harness 不在树、计划冻结为窗口限定、Track B 线索明示不阻塞、iter3 09-06 终结、队列⑦定义门即此预检、T10 已行使窗口后治理）；wrapper 手写 @mcp.tool goal-tools 同风格（77/82→78/83），转发未改的 ScheduledResearchTool，双表面 docstring 同步测试钉死，session 绑定走冻结 _resolve_session_id 零 diff；**proposal_id 前 200 字符 golden 断言（精确消费正则 + 活体引擎线二次断言）**；service.py 注入块①经 T5 自声明扩展点增名（T5/T14 断言全保持）；IM confirm 流 E2E 复活（mock channel，runtime.py:198-217 只读）；6 README 计数同步 test_readme_counts 绿；**eval before/after：全局地板逐位持平 0.4367（且与 P0 §8.1 历史基线逐位一致=复跑保真旁证）、19 个非 T13 域 top1/top3 零变化（最强地板形式）、定向组 0/8→6/8**；协议偏差诚实声明（归档 harness 复跑/queries 工作副本两处/打分器容差补丁/中文词法盲区）；桥套件 241 绿亲跑）
  What to do: 前置检查 harness-evolution 冻结状态（冻结中→本 todo 顺延并在 wiki 记账）。`agent/mcp_server.py` 增 `scheduled_research` wrapper（双表面：工具类 docstring 与 wrapper 同步，AGENTS.md 约定；**wrapper 输出须将 proposal_id 置于前 200 字符内**——D5 preview 截断 + 中继正则约束，golden 断言，Oracle 二轮 F3 行）；提案 confirm 流 E2E（IM "confirm/确认" 拦截路径 `runtime.py:198-217` 复活）；6 份 README 工具计数同步（test_readme_counts 绿）；tool_selection eval before/after（全局地板不降 + 定向组提升）。
  Must NOT do: 不动 mandate/下单类；冻结窗口内不提交。
  Parallelization: Wave 5 | Blocked by: 冻结门 | Blocks: — | 可并行: T14,T15
  References: D7；F3；AGENTS.md README 计数锚定 + eval 约定
  Acceptance criteria: confirm 流 E2E 证据；README 计数门绿；eval 对比报告落盘
  QA scenarios: happy: IM 内 agent 提案→用户"确认"→任务创建；failure: 冻结中触发 → 拒绝执行并提示顺延
  Commit: Y | feat(tools): scheduled_research reaches the MCP surface

- [x] 14. Goal 绑定：prompt 注入 + 面板 E2E — **完成 2026-09-13**（commit `02731637`，验证波次零代码改动：注入块原样即正确（治理面预检——四个 goal 工具全在 MCP 面零 deny，排除 D8② 同款死信陷阱）；面板 E2E 全链绿（REST 建 goal→SSE goal.created→UI kickoff 无提示→引擎 prompt 注入块逐字捕获→add_goal_evidence 携带正确 session_id（raw tool-part 断言）→REST 重取返回证据→reload 渲染）；**模型遵从率 12/12 = 100%**（升级门 ≥80%，别名映射升级不需要）；降级项 12 诚实佐证（MCP 侧写入无 goal.* SSE 帧，面板 reload 前陈旧——与 F8 卡一致）；mcp_server 冻结 diff=0；桥套件基线全等）
  What to do: 桥的 prompt 前置注入块（`[gateway context] vt_session_id=<id>，research-goal 工具调用须传 session_id='<id>'`）；Web UI goal 面板 E2E（REST 创建→kickoff→agent 侧 add_goal_evidence 透传→面板可见——**"面板可见"定义为 REST 重取后可见**：MCP 侧 goal 写入跨进程边界、不发 gateway EventBus 事件，`goal.*` SSE 实时性丢失记降级清单第 12 项，Oracle 二轮 F4 行）；模型遵从性抽样（≥10 回合统计透传成功率，<80% 则升级别名映射方案并记 wiki）。
  Must NOT do: 不改 `_resolve_session_id` 回退链（mcp_server 冻结面）。
  Parallelization: Wave 5 | Blocked by: T7 | Blocks: — | 可并行: T13,T15
  References: D8；F4；mcp_server.py:350-389
  Acceptance criteria: goal 面板 E2E 通过 + 遵从率报告落盘
  QA scenarios: happy: agent 建 goal 出现在对应会话面板；failure: 遵从率低 → 触发升级路径而非静默
  Commit: Y | feat(engine-bridge): goal session binding via gateway context injection

- [x] 15. Settings 重映射 + 降级清单成文 + wiki/divergence 记账（收尾） — **完成 2026-09-13**（commit `64ee7992`：三裁决全落计划许可零代码路径——sse_timeout_seconds 双引擎保持 90（桥 3s heartbeat + 8s 静默期使健康回合间隙有界，仅挂死引擎触发）、Settings→LLM 文档注记（前端无只读承载，零前端改动）、data-sources B5 = DOCUMENTATION-ONLY（网关无法重 spawn MCP/自重启丢在飞回合/host-direct 下属 systemd 事务）；F8 卡补全：14 项降级清单逐项吸收 T7/T8/T9 实证普查 + 治理现实注记（run_dir bash 收割/D8② tool-agnostic）+ F6 认证配方（API_ALLOWED_HOSTS/fail-closed API_AUTH_KEY）+ 回滚程序（ENGINE=native 一键）+ pending integrations（T13/T14/llm_usage run_dir artifact）；divergence 账 + 上游候选（SessionService Protocol 抽取等）记录；settings_routes.py 最终零 diff；280 行能力测试）
  What to do: ENGINE=opencode 下 Settings→LLM 区重映射为只读展示（指向 opencode 配置）或隐藏（前端零改动约束下用后端 capability 字段驱动既有 UI 状态；若必须前端改动则降级为文档说明，不改前端）——**重映射必须保持 `sse_timeout_seconds` 字段继续服务**（前端 watchdog 输入，`Agent.tsx:1241-1243`，Oracle 二轮注 4）；**Settings→data-sources 语义裁决（B5）**：MCP 子进程 env 在 spawn 时固定、写 .env 不热加载——要么文档化"容器重启后生效"+降级清单第 13 项，要么 gateway 在写成功后触发受控重启（二选一，执行时定夺并成文）；`llm_usage` 合成事件接入 Run Detail（best-effort，缺则面板优雅缺席）；14 项降级清单 + 所有权规则 + 认证配方成文为 mymain-wiki 特性卡（F8 engine-bridge）；`MYMAIN_DIVERGENCE.md` 记账（含上游候选：SessionService Protocol 抽取、scheduled_research wrapper）；回滚程序（ENGINE=native 一键回退验证）。
  Must NOT do: 不改前端；不把估算写成事实；不遗漏 divergence 记账。
  Parallelization: Wave 5 | Blocked by: T7 | Blocks: — | 可并行: T13,T14
  References: D3/D7；mymain-wiki 卡片体例（f2/f7 为模板）；F3 降级全清单
  Acceptance criteria: F8 卡 + divergence 条目落盘；ENGINE=native 回退冒烟通过；全量 pytest 门最终绿
  QA scenarios: happy: 回退后原 Python 引擎全功能恢复；failure: 回退残留 bridge 状态 → 清理脚本兜底
  Commit: Y | docs(mymain-wiki): F8 engine-bridge card, divergence ledger, rollback procedure

## §7 先例参照（opencode↔harness 融合开源项目调查）

> 来源：librarian 源码级调查（2026-09-12，12 个仓库全部 clone 阅读，12 条 permalink 抽查全部 200 解析）。完整带 permalink 报告归档：`.omo/evidence/opencode-engine-bridge-v2/prior-art-report.md`。

### 7.1 三个改变设计假设的生态事实
1. **v1.18.30 两代 API 并存**：legacy `/session/...`（含 `/session/{id}/permissions/{permissionID}`、`/abort`、`/children`、`/event`、`/global/event`）与新 `/api/*` Effect 表面；新官方客户端 `@opencode-ai/client`（Service.discover/ensure）。**所有 Python/桥接先例都走 legacy REST**——本计划同（D10），但必须做能力探测（CodeNomad 用 `--help` 解析探测版本能力）。
2. **token 级增量事件存在**：`message.part.delta`（`properties.field=="text"`、`properties.delta`=增量）——text 翻译**直接透传，无需自算差分**；`message.part.updated` 为累积快照、`part.time.end` 标 part 完成；`session.status` 带 `busy|idle|retry(attempt,message)`——attempt 生命周期直接挂钩。
3. **单服务器多目录原语**：`x-opencode-directory` header（kimaki）——比每租户容器便宜但边界弱（共享 auth/会话库），**否决用于租户隔离**（D2 不变），仅留作单用户多工作区优化选项。

### 7.2 对比表（浓缩；★/license/最近推送 截至 2026-09-12）
| 项目 | 机制 | 会话映射 | 事件翻译 | 权限 | 租户 |
|---|---|---|---|---|---|
| packages/slack（官方，**反面教材**） | SDK 内嵌 server | 内存 Map（重启即丢） | 阻塞 prompt 一次性回复 | 无 | 单用户 PoC |
| kimaki 1.4k/MIT | 共享 serve + x-opencode-directory，SDK v2 | SQLite thread_sessions + 每会话 1000 条事件缓冲 | part 完成即 flush（无 token 流）；子代理扁平+标签、文本抑制 | server-config 分层 + 按钮中继 + continue_loop_on_deny | 单 bot；worktree/thread |
| opencode-router 2/无license | k8s 反代 + pod-per-session | SHA256(email,repo,branch)→pod+PVC（PVC 比 pod 长寿） | bootstrap 进度经 pod 内插件→router SSE | 委托 oauth2-proxy | per-user pod、scale-to-zero |
| opencode-runtime 8/Apache-2.0 | **Python** spawn serve per (workspace,user,project) | 调用方自持久化 session_id | /global/event 透传、提取 part.delta | permission.asked→调用方 POST 回 | HOME/TMPDIR/config 隔离+随机密码 |
| GolemBot 322/MIT | **opencode run NDJSON**（无 server）+ 7 IM 适配器 | .golem/sessions.json + engineType 防串染 | NDJSON→StreamEvent{text,tool_call,tool_result,done}→自家 SSE | opencode.json `{"*":"allow"}` | KeyedMutex/sessionKey |
| portal 800/MIT | SDK + BFF SSE 再发射 | per-port 实例注册表 | 事件=缓存失效信号；16ms 批处理；**claude/codex/opencode 三引擎归一到 opencode 形状** | pending store + UI 应答 | Tailscale LAN |
| OpenChamber 9.8k/MIT | 自家后端聚合 + relay tunnel | 后端 store | heartbeat+定时重连；capability 查询参数 | 委托 workspace 配置 | 多 runtime resolver |
| CodeNomad 2.6k/MIT | @opencode-ai/client Service.discover/ensure | workspace→directory；worktree 会话疏散 | 自家 server 再广播 WorkspaceEventPayload | **YOLO 自动接受 + ownsDirectory 所有权校验** | 桌面多 workspace |
| @daytona/opencode Apache-2.0 | 插件工具覆写，ctx.sessionID→sandbox | sessionID→sandbox map | — | — | 每会话沙箱 |
| grinev telegram-bot 1.2k/MIT | SDK v2 + 本地 serve 自动重启 | settings store + 会话选择菜单 | **渐进编辑节流 1s→2s→5s→10s**；子代理卡片 | inline keyboard once/always/reject + 代数计数器 | 个人单用户 |
| openwork 23.5k/MIT | SDK + 受管 serve 池 + 策略插件 | 共享 SQLite（仅 live run 绑进程） | — | effective-permissions/authorized-folders | **蓝绿引擎轮换 + 4 条件 fail-closed reaper + 目录 fence** |
| opencode-manager 875/MIT | 单共享 server + proxy | per-user git identity env | — | AUTH_SECRET/JWT | 插件隔离区 + 配置恢复 |

### 7.3 借鉴清单（映射到本计划 todo）
| 我们的需求 | 最佳先例 | 借鉴点 |
|---|---|---|
| text_delta | opencode-runtime / portal | `message.part.delta` 直接透传（Web SSE 面）；part.updated 差分仅作 fallback |
| IM 流式投递 | grinev | 渐进节流（1s→10s 按会话年龄）叠加 manager 既有 coalescing；flush 顺序：pending tool 行先于 permission/question UI |
| tool preview | kimaki | preview 取 `part.state.title`/输出前段，**绝不重新解析 output**；大输出改发 "returned Nk tokens (P%)" 提示 |
| attempt/turn 生命周期 | kimaki + runtime | **natural completion** = `message.time.completed && finish≠"tool-calls"`（footer 在此发、不等 idle——OmO continuation 正解）；仅 `session.idle` 排空队列；`session.status retry`→attempt 计数 |
| OmO 子代理 | kimaki | 子会话正典 id = `task` tool part 的 `state.metadata.sessionId`（勿解析 state.output）；子文本抑制、工具行 `subtask:<label>` 前缀扁平化；token 核算走会话树；**现成回归 fixtures** `cli/src/session-handler/event-stream-fixtures/*.jsonl` |
| session.error 死锁 | kimaki #74 | error 后无 idle → 桥必须注入合成 idle，否则队列永卡 |
| 事件稀疏兜底 | kimaki | prompt_async 路径 part 事件可能稀疏/缺失 → 从 `message.updated` 的 msg.parts 种子化 part buffer |
| Python 进程卫生 | opencode-runtime process.py | **逐字借用**：`start_new_session=True` 组杀、PID+create_time 代数检查防 PID 复用、SIGTERM→5s→SIGKILL、注册表文件锁 `claim_starting`/`write_if_instance` CAS 防双 spawn |
| SSE 客户端健壮性 | kimaki global-listener + portal proxy | 单一持久 SSE（每租户实例）广播到 per-session runtime；backoff 500ms→30s、收到事件清零；portal 代理配方：`X-Accel-Buffering:no`、retry 归代理（`sseMaxRetryAttempts:0`）、客户端断连 cancel 传播上游、合成错误事件注入而非静默死亡 |
| prompt/订阅时序 | opencode-runtime session.py | 先 POST prompt_async 再订阅（受理即返、delta 待模型输出才开始）——或先订阅+缓冲去重；二选一并成文理由 |
| 空闲回收真值 | opencode-router | 代理侧活动不够（WS 不打 annotation）→ 删前问引擎 `GET /session?limit=1&roots=true` 取 `time.updated` |
| 租户删除 vs 在途消息 | openwork directory-fence | per-directory promise 链串行化 dispose 与 prompt admission |
| 权限转发安全 | CodeNomad | 任何自动/中继应答前校验 session→directory 所有权（防跨租户串扰） |
| 权限配置分层 | kimaki opencode.ts 头注释 | findLast 分层：内置默认▼合并配置▼`agent.<name>.permission`▼`session.permission`（最后者胜）；目录 ALLOW 规则进 server 生成配置**永不进 session 规则**；`external_directory` 必须对象非字符串（deep-merge 整体覆盖陷阱）；`OPENCODE_CONFIG=<file>` 非 `OPENCODE_CONFIG_CONTENT`（#90）；TTL 自动 reject 配 `experimental.continue_loop_on_deny:true` |
| 引擎热更（可选增强） | openwork engine-pool | 蓝绿轮换：spawn 备机→新请求路由→旧实例 drain（永不>2 台）——会话在共享 SQLite、仅 live run 绑进程 |
| 会话映射防护 | GolemBot + kimaki | 映射条目带 engineType 防跨引擎 id 串染；反查按 updated_at 排序（resume 后一个 session 可绑多会话） |
| IM 卡片逃生口 | packages/slack | `session.share()` URL 贴进会话 = 廉价"查看完整转录"（钉钉/飞书卡片可选增强） |

### 7.4 避坑清单（全部有失败先例）
1. 内存态会话映射重启即孤儿（packages/slack、opencode-runtime）→ 本计划映射走 SessionStore 持久化 ✓
2. 把"消息完成"当"回合完成"→ continuation 下假 terminal（kimaki natural-completion 为正解，已入 D4）
3. 空闲判定只信代理侧活动 → 误删活跃实例（opencode-router 双阶段真值检查，已入 T11）
4. permission allow 写 session scope → 用户项目 deny 失效（kimaki findLast 分层，已入 D9）
5. Claude 式 `{command:"str"}` MCP 条目令 opencode **拒绝启动**（GolemBot #42）→ T2 盘点 OpencodeAgent opencode.json.tmpl 的 MCP 条目须为 `{type:"local",command:[...]}` 形状
6. `shell:true` spawn 孤儿进程 / SIGTERM 杀错进程（kimaki：解析 binary 路径 + 组杀）→ T10 借用 process.py
7. SSE 单 generator 无 backoff 断连即死（opencode-runtime 局限）→ T3 必须补
8. 按精确 sessionID 过滤令子代理不可见（opencode-runtime 局限）→ D4 显式子会话策略
9. 多行 prompt 走 argv 在 PowerShell 损坏（GolemBot #43，容器 Linux 无此问题，记录备查）
10. opencode API 两代并存且移动中 → 钉版（冻结面）+ 能力探测 + T1 golden trace 语料即漂移报警器

### 7.5 三种已验证的文本流式契约（每表面显式选一，不混用）
- **token-delta 直通**（opencode-runtime/portal）→ **Web SSE 面选此**：vt `text_delta{delta}` 与 `message.part.delta` 同构，零阻抗。
- **part-completion flush**（kimaki，无 token 流）→ 不采用（vt 前端期待 token 流）。
- **渐进编辑节流**（grinev）→ **IM 面选此**：叠加 ChannelManager 既有 coalescing。
无任何被调查项目混用超过两种契约——本计划两面各取一种，符合业界实践。

### 7.6 对抗自检清单（供 Momus 逐项核对）
会话↔session 映射持久化？✓（SessionStore）· idle 检测等 natural completion？✓（D4 修订）· reaper 问引擎而非代理？✓（T11）· 权限规则对象深合并且 allow 不进 session scope？✓（D9 修订）· hung spawn 不可 SIGTERM 孤儿？✓（T10 借 process.py）· 租户删除与 prompt admission 竞争？✓（T11 fence）· permission 转发前校验 session→directory 所有权？✓（T11）· 客户端能力探测？✓（D10 修订）

## 风险登记册

| # | 风险 | 概率/影响 | 应急预案 |
|---|---|---|---|
| R1 | OmO headless 下 continuation/subagent 的 idle 语义不可驯服 | 中/高 | 静默计时器调参 → serve 模式禁用 OmO continuation → 降级 vanilla opencode agents → 最终应急：hybrid（IM/定时留 Python 引擎，Web 走 opencode）——仅在 Phase 0 证伪且 HIL 批准时启用 |
| R2 | opencode legacy `/event` 表面在钉版上不稳/形状漂移 | 低/高 | 钉版冻结（已是冻结面）+ trace 语料 golden 测试即漂移报警器；备选 ACP stdio 通道（fallback，不首选） |
| R3 | 事件翻译长尾（前端 handler 的隐藏假设） | 中/中 | 契约以 useSSE.ts+Agent.tsx 为准逐 handler 核对；E2E 断言以"前端是否消费"为判据而非肉眼 |
| R4 | 每租户资源超预算（vt gateway 全依赖 + opencode + MCP） | 中/中 | T12 实测裁决：瘦身 gateway 依赖面（channels extras 按需）/ 提高回收激进度 / 大租户独立主机 |
| R5 | 上游漂移（vt main 每周期 500+ commits，mymain 定期 rebase） | 高/中 | bridge 为新增模块+单点开关，rebase 冲突面极小；契约测试即回归报警；跟随 mymain 既有 rebase 纪律 |
| R6 | OmO SUL-1.0 许可（多租户商用托管情形） | 低/高 | 法务评估前置；备选 oh-my-openagent-slim 或 vanilla agents |
| R7 | 冻结窗口冲突（Phase 4 撞评测期） | 中/低 | Phase 4 全部冻结门控 + 可与 Phase 3 并行错排 |
| R8 | opencode API 两代并存且快速移动（legacy `/session` 与 `/api/*`、`@opencode-ai/client` 迁移中） | 中/中 | 钉版（冻结面）+ 启动能力探测（CodeNomad `--help` 解析模式）+ T1 golden trace 语料作漂移报警器；先例 portal 已证明三引擎归一到单一协议形状可行（EngineDriver D12 同构论证） |

## 降级清单（成文承诺，T15 落 wiki）

1. 聊天内 swarm 实时卡片（OMO 编排为 swarm 主通道，vt /swarm 面板仅覆盖 MCP run_swarm 路径）
2. `llm_usage` Run Detail 面板（best-effort 合成，缺则优雅缺席）
3. `compact` 事件渲染（opencode 内部压缩对 vt 转录不可见，D3）
4. mandate 提案卡 + 实盘授权流（research-only 姿态，D7 显式接受）
5. 下单类工具（同上，安全收益）
6. Settings→LLM 页对对话引擎失效（T15 重映射/只读化）
7. goal 面板 agent 侧创建依赖模型遵从 prompt 注入（D8，T14 监控遵从率）
8. `session_search` 跨会话搜索工具（wrapper 可选，未补前 agent 不可用但 FTS 索引仍写、REST 搜索仍work）
9. 自动标题走 Python provider 栈（D11，依赖 LANGCHAIN_* 存续）
10. OpenBB bridge 仅契约形状覆盖，不做真连接验证（out of scope）
11. `tool_progress` 阶段进度条：MCP progress 若无法透过 opencode 事件面则 best-effort 缺席（注意：`tool_heartbeat` 是**必须合成**项而非降级项——B3，前端 90s 看门狗硬依赖，D5 已锁定）
12. `goal.*` SSE 实时性：MCP 跨进程边界不发 gateway EventBus 事件，面板以 REST 重取兜底（T14）
13. Settings→data-sources 热更新：MCP 子进程 env 在 spawn 时固定，需容器重启或 gateway 受控重启（T15 裁决）
14. `stream_reset` provider 重试提示：opencode 无同构事件时 best-effort 映射（D5）

## Out of scope（显式排除）
- 上游 PR（SessionService Protocol 抽取等候选 → divergence 账本待裁决）
- 共享 bot 的 channel-ingress 路由服务（仅当成为硬产品需求再立项）
- desktop/ Electron 壳适配
- harness-evolution 评测轨道本身的任何工作
- 前端任何改动（包括"看起来更好"的顺手改）

## 全局验证门（每 Wave 收尾必跑）
```bash
pytest --ignore=agent/tests/e2e_backtest --tb=short -q   # AGENTS.md 门槛
black --check agent/src/opencode_bridge agent/tests && ruff check agent/src/opencode_bridge
git diff --name-only mymain...HEAD | grep -E "^agent/src/(agent|session|providers)/|^frontend/|^agent/src/channels/" && echo "PROTECTED-ZONE VIOLATION" && exit 1 || true
# channels 整目录零 diff（Oracle 二轮注 2：原 16 适配器枚举漏掉 runtime/manager/base/bus/pairing——T8/T9 的 Must-NOT 同样禁改它们；bridge 的流式 producer 位于 opencode_bridge/，不写 channels 目录）
```
提交纪律：DCO `git commit -s`、Conventional Commits、禁 Co-Authored-By/AI 追溯行、关联本地工作流（不写 Part of #1218 除非该 todo 确属 harness 演进上游轨道）。
