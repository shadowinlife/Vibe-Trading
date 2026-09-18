---
title: 2026-09-18 上游可替换性审查（engine-bridge 分支）
description: rebase 到 d3c29488 后，对分支特有能力及其所用 utils/函数/tools/skills 逐项核查能否被上游社区能力替换；含人工复核修正、收缩优先级与上游 PR 候选清单。
type: review
status: active
created: 2026-09-18
updated: 2026-09-18
tags: [divergence, rebase, replacement-review, f8, engine-bridge]
related: [MYMAIN_DIVERGENCE.md, ../features/f8-engine-bridge.md, ../history/timeline.md]
---

# 上游可替换性审查 — 2026-09-18

## 0. 背景与方法

- **触发**：`mymain-engine-bridge` rebase 到上游 `d3c29488`（自上一基线 `899d3c75` 前进 417 commit）之后，按用户指令做两轮替换核查：
  1. 分支**特有能力**能否用上游社区能力替换；
  2. 这些能力所用的 **utils / 函数 / tools / skills** 能否用上游能力替换。
- **方法**：两个并行 explore 代理分别对 engine-bridge 面（opencode_bridge / OpencodeAgent / api_server / mcp_server 缝合点）与数据面（memory / clickhouse / valuation / flow tools / skills）做双树 grep+diff 对比（分支 worktree vs `/Users/mgong/LegoNanoBot/Vibe-Trading` 上游 main 检出）；**所有关键结论均经主线人工复核**（见 §3）。
- **判定语义**：`REPLACE` = 上游已有等价物，分支代码可删；`PARTIAL` = 上游覆盖一部分，剩余仍是分支胶水；`KEEP` = 上游无等价物，分支特有。

## 1. Engine-bridge 特有能力（11 项）

结构性事实（已复核）：分支对 `agent/src/session/`、`src/channels/`、`src/agent/`、`src/goal/`、`src/scheduled_research/`、`frontend/` **零 diff**——bridge 是纯增量，未触碰上游保护区。上游检出中出现的 `OpencodeAgent/` 为**未跟踪本地残留**（`git ls-files` 为空），不是上游代码。

| # | 能力 | 分支位置 | 上游等价物 | 判定 | 依据 |
|---|---|---|---|---|---|
| 1 | 引擎工厂开关 | `src/api/state.py`(+15)、`api_server.py`(+5)、`env_schema.py::OpencodeBridgeConfig`、`opencode_bridge/wiring.py` | 无——上游 `state.py:29-71` 直接构造 `SessionService`，无 `VIBE_TRADING_ENGINE`，仅有测试用 `_host_attr` 注入口 | **KEEP** | 上游完全没有可插拔 agent-engine 概念；F8 卡的「SessionService Protocol 抽取」PR 候选未合入 |
| 2 | 会话服务缝合层（D6 契约） | `opencode_bridge/service.py::OpencodeSessionService` + `service_persistence.py` | `src/session/service.py::SessionService`（具体类，无 Protocol）+ `SessionStore` + `EventBus` 被分支零 diff 复用 | **PARTIAL** | 上游拥有桥所镜像的稳定表面与全部存储；但无正式 Protocol，鸭子类型实现仍是分支代码。收缩路径 = 上游化 Protocol 抽取 |
| 3 | 事件翻译器 | `opencode_bridge/translator/{events,lifecycle,routing,text,tools}.py` | 无入向等价。`src/openbb_bridge/event_mapper.py::SSEEventMapper` 是镜像问题（vt→OpenBB 出向），仅为先例；目标词汇本身上游原生（`loop.py:2672 tool_progress`、`:2678 tool_heartbeat`） | **KEEP** | 外部引擎事件→vt SSE 词汇的入向翻译上游不存在 |
| 4 | IM 流式（`_stream_delta` 生产者） | `opencode_bridge/im_stream.py`（全仓首个生产者） | 消费侧原生：`channels/manager.py::_coalesce_stream_deltas`（101306e9, 06-30）、`base.py::send_delta` 契约、`:226 _wants_stream` 戳；上游**零生产者**、无处写 `_streamed` | **PARTIAL** | 传输/合并基础设施全部上游原生并被原样消费；生产者 tap 分支独有。F8 候选「ChannelRuntime 置 `_streamed`」上游仍未落地 |
| 5 | SSE 客户端通道 | `opencode_bridge/sse.py`（手写帧解析, D10）、`driver.py::OpencodeDriver.events` | 无——上游只**服务** SSE（`sessions_routes`、`_mint_sse_ticket` 4e3c91c7）；`httpx-sse` 非依赖 | **KEEP** | 上游无「引擎即外部进程」概念，故无客户端 SSE 管道可替换 |
| 6 | 崩溃恢复 / 活性检测 | `recovery.py`、`recovery_branches.py`（三分支）、`engine_state.py`、`stream_liveness.py` | 分支 3 的 oracle = 上游 `session/service.py::_recover_interrupted_attempts`（884c08e1, 08-22，分支 import 复用零 diff）；`sessions_routes.py:806-812` replay 门不变；`system_routes.py:243 liveness_probe` 仅容器级 | **PARTIAL** | 重启中断语义上游已覆盖（桥直接委托）；re-attach / backfill / 引擎死亡检测是外部引擎专属，无上游对应 |
| 7 | Goal 会话绑定（D8①） | `service.py::_build_prompt_injection`（vt_session_id 前缀注入） | 原语全在上游：`mcp_server.py::_resolve_session_id` 显式 `session_id=` 优先 + per-connection ctx（#885, 6a859902 07-28）；`goal/store.py` 全量会话作用域；goal REST `sessions_routes.py:448-629` | **PARTIAL** | 绑定原语上游齐备且正是注入的目标；注入本身是不可约的桥胶水（外部模型无从得知 vt 会话 id）。降级 #12（MCP 子进程 goal 写入无 `goal.*` SSE，`goal_tool.py _emit` 仅进程内）上游依旧成立 |
| 8 | scheduled_research MCP 工具（T13） | `agent/mcp_server.py::scheduled_research`（~120 行 wrapper → registry execute） | **整个子系统上游原生**：`src/scheduled_research/`（65190774, 08-23 + outbox 租约 34d080c9 + verdict 持久化 dc9ec417）、`src/tools/scheduled_research_tool.py::ScheduledResearchTool`（同 actions / srp_ 提案）、REST `scheduled_routes.py` | **PARTIAL（强）** | 零调度研究逻辑属分支——上游只缺 MCP 表面（74 工具清单无 scheduled_research）。**最佳上游 PR 候选**：wrapper 直接委托上游工具类 |
| 9 | 租户路由 / 供给 | `OpencodeAgent/deploy/router/`、`provision_tenant.py`、`tenancy_lib.py`、fleet compose/supervisord | 无——上游单租户（compose 单服务）；桥消费的鉴权原语（`API_AUTH_KEY`/`API_ALLOWED_HOSTS`/`security.py`）均上游原生未改 | **KEEP** | 无 fleet 层可采纳；鉴权配方本就骑上游安全代码，无收缩空间 |
| 10 | 子代理名册 + 工具治理 | `OpencodeAgent/config/`（render_config deny 编译器、subagents.json、12 prompts、工具清单） | 不同 harness：上游 30 个 swarm presets + `run_swarm`；PR #1286 `src/specialists/` 未合入 | **KEEP** | 上游 swarm 服务原生引擎，非 opencode 配置；deny 编译器上游无对应 |
| 11 | 引擎驱动管道 | `driver.py`（4 原语, legacy `/session` REST, D10）、`client.py`、`errors.py`、`tool_names.py`、`_types.py` | 无 | **KEEP** | 整层因上游无外部引擎传输而存在 |

**时间窗核查**：F8 卡成文（09-13）→ 今（09-18）之间的上游 commit 全部是 grounding/factors/backtest/portfolio/robinhood 面，**无一触碰 agent-engine/session/channels 架构**——没有任何能力在此窗口从 KEEP 翻转为 REPLACE。

## 2. Utils / 函数 / tools / skills

### 2.1 MEMORY

| 项 | 分支路径 | 上游等价物 | 判定 | 依据 |
|---|---|---|---|---|
| 记忆核心栈 | `agent/src/memory/{persistent,lifecycle,hierarchy,compression,search_index,semantic_links,__init__}.py` | **上游已跟踪同套文件**（`git ls-files` 复核确认；`fix(memory)` 系列持续维护至 23a6293e）；6/7 文件与分支逐字节一致 | **REPLACE（已吸收）** | 早期上游化成果；分支在此无增量（除下行） |
| `VT_MEMORY_BASE_DIR` 路径覆盖 | `persistent.py::_default_memory_base()` + `env_schema.py::MemoryConfig.base_dir` | 无——上游硬编码 `MEMORY_BASE = ~/.vibe-trading/memory` | **KEEP**（小 PR 候选） | ~25 行容器化路径覆盖，可干净回移上游 |
| 反思课程存储 | `memory/reflections.py`(304L) + `VT_MEMORY_REFLECTIONS` + `backtest_tool.py:106-115` auto-reflect 钩子 | 无——上游 backtest_tool 零 reflect；全仓无 lessons store | **KEEP** | F1/F3 纯分支能力 |
| 5 个 opt-in memory MCP 工具 | `memory/mcp_adapter.py`(197L) + `mcp_server.py:836-957` + `VT_MEMORY_MCP_TOOLS` | 部分：上游 `remember_tool.py::RememberTool._save/_recall/_forget/_reinforce`（两树逐字节一致）原生覆盖同生命周期，但上游 MCP 面 **0 个** memory 工具 | **PARTIAL** | adapter 是上游 `PersistentMemory`/`MemoryLifecycle` 的薄信封。若不需要对外 MCP 暴露 → 整体可退役（REPLACE）；`memory_reflect` 无原生等价 |
| MemoryGuard 中间件 | `memory/memory_guard.py`(158L) | 无——上游无 FastMCP middleware | **KEEP** | 依附 MCP 面，随其存亡 |
| memory-lifecycle skill | `agent/src/skills/memory-lifecycle/SKILL.md` | 无——上游 90 技能无 memory 类 | **KEEP**（耦合） | 纯文档化分支 MCP 面；MCP 面退役则同退；上游化则触发 6-README 计数锚 |
| 分支 memory 测试 | `tests/memory/{test_mcp_adapter,test_reflections,test_concurrent_mcp,test_latency_bench}.py` | 无 | **KEEP** | 其余 6 个 memory 测试文件与上游一致 |

### 2.2 CLICKHOUSE

| 项 | 分支路径 | 上游等价物 | 判定 |
|---|---|---|---|
| 连接器 + 单位注册表 | `src/clickhouse_connector.py`(568L)、`src/clickhouse_units.py`(370L) | 无——上游 `git ls-files \| grep clickhouse` 为空；duckdb 仅本地文件访问（`local_loader._read_duckdb`、`base.py` parquet 缓存） | **KEEP** |
| 回测 loader + 链首位 | `backtest/loaders/clickhouse.py`(321L)+`clickhouse_columns.py`；registry 模块表 + a_share 链首（**本轮修复注册 bug**：模块表原写 `clickhouse_loader`，实际文件为 `clickhouse.py`，loader 从未注册、链首被静默跳过） | 角色部分重叠：`local_loader.py`（`type: duckdb` SQL、每链尾位、`~/.vibe-trading/data-bridge/config.yaml`） | **PARTIAL→KEEP**：`local` 占据「自有数据」链位但无法承担 CH 角色（无服务器连接、无全表镜像/同步态、无 `stk_factor_pro` 复权口径、无 `extra_provenance`）；若日后导出 DuckDB 镜像，`local` 仅可吸收价格 bar 用例 |
| MCP 探查/查询工具 | `tools/clickhouse_{explore_tools,query_tool,query_guard}.py`(857L) + `ch_list_tables/ch_describe_table/ch_query` | 无——上游 74 工具面无 SQL/仓库通道 | **KEEP**（只读 `llm_role` AST 守卫上游独有） |
| 配置管道 | `env_schema.py` CLICKHOUSE_*（8 字段，含默认 IP `172.24.165.51`） | 无 | **KEEP**（随 CH 栈存亡） |
| 4 个 flow 工具的 CH 钩子 | `dragon_tiger_tool.py:209-219`、`fund_flow_tool.py:100-108`、`margin_trading_tool.py:182-197`、`northbound_tool.py:217-232` | 上游版本与分支**除纯增量 CH 钩子外逐字节一致**；上游 5fe512dc 的 4 个 fallback 测试直接约束钩子行为 | **REPLACE（条件）**：放弃 CH 栈即自动回归上游零分歧。**本轮已修**：`fetch_*_ch` 原内置 tushare 回退导致钩子把 tushare 数据错标 `source="clickhouse"`（上游测试正确拒绝），现改抛 `ClickHouseUnavailableError`，钩子回落上游 eastmoney→tushare 链，provenance 真实 |
| schema/DDL/脚本/测试 | `schema/clickhouse/`、`tools/clickhouse_*`、8 个 CH 测试文件 | 无 | **KEEP** |
| market_data provenance | `_clickhouse_provenance()` + a_share→clickhouse 路由 | 上游 a_share→tencent，无 provenance 钩子 | **KEEP**（CH 耦合；rebase 时已按上游 per-symbol 链重构为逐符号解析） |
| PIT 基本面 provider | `tushare_fundamentals.py::ClickHouseFundamentalProvider`(+~200L) | 无——上游仅 tushare/akshare/eastmoney provider；基类 `TushareFundamentalProvider` 分支未改 | **KEEP**（CH 面唯一真正的新*能力*） |

### 2.3 VALUATION / DATA TOOLS

| 项 | 分支路径 | 上游等价物 | 判定 | 依据 |
|---|---|---|---|---|
| `get_valuation` | `tools/valuation_tool.py::GetValuationTool`(270L) | 无同名工具。邻近：`get_fundamentals_tool.py`（报告基本面 PIT，两树一致）、`tushare.py:284-327 _merge_fundamental` daily_basic extra_fields（仅回测侧）、`quantlib/valuation/`（DCF/comps 引擎）、`valuation-model` skill（方法论） | **KEEP + 缺陷标记** | A 股**逐日估值倍数**（pe_ttm/pb/ps/total_mv + 单位口径）上游无工具表面。但已复核确认**孤儿**：native `tools/__init__.py`（与上游一致）与 `_MIRRORED_TOOL_SOURCES` 均未注册，仅 `test_valuation_tool.py` 引用——需接线或随 CH 裁决退役 |
| northbound ×100 修复 | `tools/tushare_fallbacks.py`（分支删除 `* 100`） | 文件上游已有（5fe512dc），但 **上游 192-194 行 `* 100` 仍在**，从未独立修复（`git log -S` 仅引入 commit） | **KEEP（修复）+ 最高价值上游 PR 候选** | 分支 docstring 记录为已确认 100× 数据缺陷（tushare `moneyflow_hsgt` 本已万元，2026-08-12 对 CH 镜像实证） |
| moneyflow ×10⁴「移除」 | —— | `* 10_000` **两树同在**（上游 86/124 行 = 分支 88/126 行） | **无分歧（声称不成立）** | 万元→元归一是有意保留（对齐东财口径，上游 84 行注释），分支从未移除 |
| 4 个 flow 工具「单位修复」 | —— | —— | **无单位增量** | 四文件分支 diff 仅为 CH 钩子块；单位面已与上游一致 |
| 装饰性 black 重排 | `tushare_fallbacks.py`(3 hunks)、`market_data.py`(4 hunks) | 上游同内容不同折行 | **REPLACE（回退）** | 零行为差异、白白膨胀 diff；回退即收缩分歧 |
| env_schema 增量 | CLICKHOUSE_*、memory `reflections_enabled`/`mcp_tools_enabled`/`base_dir`、`OpencodeBridgeConfig` | 上游 `MemoryConfig` 已有 VT_MEMORY 预设框架（7 旗标）；CH/engine 字段无 | **KEEP** | 各随所属栈存亡 |

### 2.4 Skills

- **memory-lifecycle**：上游 90 技能零重叠，唯一分支新增技能，与 memory MCP 面共生（见 2.1）。
- 复核修正：explore 代理曾报「分支缺上游 `sec-edgar-fetch` 技能，疑似 rebase 丢失」——**误报**。`sec-edgar-fetch` 在上游检出中为**未跟踪残留目录**（仅含 `scripts/`，`git ls-files` 为空，历史无添加记录）；分支备份（rebase 前）同样没有它。rebase 未丢失任何上游技能。

## 3. 人工复核清单（对代理结论的验证）

| 声称 | 复核方式 | 结果 |
|---|---|---|
| 上游已跟踪 memory 核心栈 | 上游 `git ls-files agent/src/memory/` + `git log` | ✅ 7 文件在跟踪，`fix(memory)` 维护至 23a6293e |
| `sec-edgar-fetch` 为 rebase 丢失 | 上游 `git ls-files` + 分支 `--diff-filter=D` 历史 + 备份树 | ✅ 误报：上游未跟踪残留；分支从未删除 |
| `get_valuation` 孤儿 | 分支 mcp_server / api_server / tools `__init__` 全文 grep | ✅ 零注册点，仅测试引用 |
| northbound ×100 上游仍在 | 上游 `tushare_fallbacks.py:192-194` 直读 | ✅ 仍在；分支已删 |
| 保护区零 diff | `git diff --stat main..HEAD -- session/ channels/ agent/ goal/ scheduled_research/ frontend/` | ✅ 空输出 |
| MCP 计数一致性 | 上游 docstring「Surfaces 74」vs 分支「78 (83 with memory)」 | ✅ 74+3ch+1scheduled=78，+5memory=83，口径自洽 |
| CH loader 注册 bug | `LOADER_REGISTRY` 实测（修复前 27 无 clickhouse / 修复后 28） | ✅ 潜伏 bug 实锤并修复：`_loader_modules` 条目 `clickhouse_loader` ≠ 实际模块 `clickhouse.py`；F5 链路面自落地起未生效，旧门禁（直接 import loader 的 CH 测试 + 不对 registry 断言的旧 README pin）双重掩盖 |
| flow 工具 provenance 错标 | 上游 4 个 fallback 测试失败现场（`assert 'clickhouse' == 'tushare'`）+ `fetch_*_ch` 源码 | ✅ 实锤并修复：内置 tushare 回退 + 钩子硬编码标签；旧分支门禁从未暴露（测试环境缺 `clickhouse-connect`，钩子 ImportError 自禁用） |

## 4. 收缩优先级与上游 PR 候选

按（价值 / 体量）排序：

1. **scheduled_research MCP wrapper**（§1 行 8）：~120 行、自包含、直接委托上游 `ScheduledResearchTool`；PR 面 = `mcp_server.py` + 测试 + 6-README 计数同步。
2. **northbound ×100 修复**（§2.3）：4 行 + 实证 docstring，修复上游在产数据缺陷。
3. **`VT_MEMORY_BASE_DIR` 路径覆盖**（§2.1）：~25 行，容器化刚需，上游无对应。
4. **SessionService Protocol 抽取**（§1 行 1-2）：上游化后工厂开关可薄化为配置项。
5. **ChannelRuntime `_streamed` 戳**（§1 行 4）：F8 卡既有候选，上游仍未落地。
6. **装饰性 black 重排回退**（§2.3）：零风险纯收缩（~8 hunks）。

## 5. 决策依赖（非替换问题，需单独裁决）

- **ClickHouse 全家**（~3.1k 行 src + schema/ + tools/）：上游零等价，去留是**数据架构决策**；放弃则 4 个 flow 工具钩子、market_data 路由、valuation_tool、8 个 env 字段全部自动回归上游。
- **memory MCP 面**（5 工具 + guard + skill ≈ 800L 含 reflections）：若外部 MCP 暴露非刚需，native `RememberTool` 已覆盖 save/recall/forget/reinforce，可整体退役；`reflect` 无原生等价。
- **`get_valuation`**：接线进 `_MIRRORED_TOOL_SOURCES`（触发 README 计数锚）或随 CH 裁决退役，二选一，不可维持孤儿态。

## 6. 验证基线（本轮 rebase 后）

- 全量门禁：`pytest --ignore=tests/e2e_backtest --tb=short -q`（uv 管理 venv，补装分支新增依赖 `clickhouse-connect>=1.0.0` + `sqlglot>=30.0.0` 后运行）——结果见 MYMAIN_DIVERGENCE §5 2026-09-18 条。
