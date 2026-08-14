# 09 · 枚举参考 / 数值校验 / 集成陷阱

> **文档族**: Vibe-Trading 前端集成知识库（`.omo/memory/frontend-integration/`）
> **读者**: 在 IM 插件视图层消费同一套 REST/SSE 面的外部前端团队
> **校对日期**: 2026-08-14 · **事实来源**: 全部取值经源码 grep 核对（后端发射端为权威，`frontend/src/lib/api.ts` 为契约镜像），路径见各表
> **本篇职责**: 横切参考——枚举/状态全集、数值与类型校验指南、集成陷阱清单、客户端验收清单。端点字段级契约见 01–08 篇。

---

## §1 枚举总表

### 1.1 回测运行（Run）状态

| 枚举名 | 取值 | 含义 | 定义位置 |
|---|---|---|---|
| run status | `success` | 运行成功（state.json 写入） | `agent/src/core/state.py:55` |
| | `failed` | 运行失败，附 `reason` | `agent/src/core/state.py:64` |
| | `cancelled` | 用户取消，附 `reason` | `agent/src/core/state.py:77` |
| | `unknown` | 无 state.json 且无产物兜底 | `agent/src/api/runs_routes.py:73,90,377` |

- 列表端点 `GET /runs` 的兜底规则：无 state.json 但存在 `artifacts/equity.csv` 或 `review_report.json` → 读作 `success`（`runs_routes.py:380-384`）。
- ⚠️ 契约漂移：`RunResponse.status` 的 Field 描述写的是 `"success, failed, aborted"`（`agent/src/api/models.py:59`），但实际写入端是 `cancelled`。**以 state.py 写入值为准**，`aborted` 不会出现。
- 前端 `RunData.status` / `RunListItem.status` 均为宽 `string`，无 union 约束（`api.ts:595,453`）——客户端必须对未知值兜底。
- 进行中运行没有独立的 `running` 持久态；运行是否进行中由 session attempt 状态表达（见 1.2）。

### 1.2 会话与 Attempt 生命周期

| 枚举名 | 取值 | 含义 | 定义位置 |
|---|---|---|---|
| SessionStatus | `active` / `completed` / `archived` | 会话生命周期 | `agent/src/session/models.py:121-126` |
| AttemptStatus | `pending` | 已创建未开始 | `agent/src/session/models.py:129-137` |
| | `running` | 执行中 | 同上 |
| | `waiting_user` | 等待用户输入 | 同上 |
| | `completed` | 成功终态 | 同上 |
| | `failed` | 失败终态 | 同上 |
| | `cancelled` | 用户取消终态（与 failed 区分，见 `mark_cancelled` 注释 `models.py:327-341`） | 同上 |
| Message role | `user` / `assistant` | 实际出现的取值 | `agent/src/session/models.py:216`、`agent/src/session/service.py`（`system` 为模型默认值，REST 消息流中未见发射）「源码未明确禁止其它值」 |

- Attempt SSE 事件与状态对应：`attempt.created` / `attempt.started` / `attempt.completed` / `attempt.failed` / `attempt.cancelled`（发射端 `agent/src/session/service.py:210` 等；订阅清单 `frontend/src/hooks/useSSE.ts:86-95`）。
- **一个会话同一时刻只允许一个 running attempt**：冲突时 `POST /sessions/{id}/messages`、`/cancel` 等返回 **HTTP 409**（`agent/src/api/sessions_routes.py:489,542,591,714`）。

### 1.3 工具轨迹（Tool Trail）状态

| 枚举名 | 取值 | 含义 | 定义位置 |
|---|---|---|---|
| ToolTrailItem.status | `running` / `ok` / `error` | 进行中 / 成功 / 失败 | `frontend/src/lib/api.ts:1182`（union 权威） |

- `ToolTrailItem.timestamp` 为 **epoch 毫秒**（`agent/src/session/service.py:471,503` `int(time.time()*1000)`）。
- SSE 侧对应事件：`tool_call`（开始）、`tool_result`（结束）、`tool_heartbeat` / `tool_progress`（过程中）。

### 1.4 研究目标（Goal）

| 枚举名 | 取值 | 定义位置 |
|---|---|---|
| GoalStatus（12 值） | `active` `paused` `waiting_user` `needs_refresh` `insufficient_evidence` `compliance_blocked` `blocked` `budget_limited` `usage_limited` `complete` `cancelled` `superseded` | `agent/src/goal/models.py:10-24`；前端镜像 `api.ts:684-696`（逐值一致） |
| GoalRiskTier（前端契约 3 值） | `research_general` `market_specific_short_term` `personalized_advice_or_position_sizing` | `api.ts:698-701` |
| RiskTier（后端枚举 4 值） | 上述 3 值 + `live_trading_or_execution` | `agent/src/goal/models.py:27-33` |
| criterion status | 初始 `pending`；证据覆盖后经 `pending`/`open`/`unsatisfied` → `covered`；完成审计时写入审计结果 | `agent/src/goal/store.py:187,688-690,755-762` |
| 审计结果（criterion 终值） | `satisfied` `satisfied_with_caveat` `not_applicable_user_accepted` | `agent/src/goal/store.py:48-52`（`_COMPLETION_RESULTS`） |
| evidence freshness_status | `fresh`（有 `data_as_of`）/ `unknown` | `agent/src/goal/store.py:639` |
| evidence verification_status | `verified` / `unverified`（默认） | `agent/src/goal/store.py:640,846`、`models.py:147` |
| protocol | 默认 `thesis_review` | `agent/src/tools/goal_tool.py:204`；其它取值「源码未明确」枚举化 |
| claim_type | 自由字符串，无封闭枚举 | `agent/src/goal/models.py:72` |

- ⚠️ 第 4 个风险层 `live_trading_or_execution` 不在前端 union 中：客户端创建/更新 goal 不应提交该值（agent 工具侧会被拦截，见 `goal_tool.py` 风险层校验）。
- SSE 事件：`goal.created` / `goal.evidence` / `goal.updated`。

### 1.5 Swarm（多智能体团队）

| 枚举名 | 取值 | 定义位置 |
|---|---|---|
| RunStatus | `pending` `running` `completed` `failed` `cancelled` | `agent/src/swarm/models.py:29-40` |
| TaskStatus | `pending` `blocked` `in_progress` `completed` `failed` `cancelled` | `agent/src/swarm/models.py:14-26` |
| WorkerStatus（worker 终态） | `completed` `failed` `timeout` `token_limit` `incomplete` | `agent/src/swarm/models.py:43-57`（`incomplete` ≠ `failed`：跑完但无实质交付物） |
| 前端展示态映射 | `in_progress`→`running`、`completed`→`done`、`failed`→`failed`、`blocked`→`blocked`、`cancelled`→`cancelled`、`pending`/未知→`waiting`；另有事件驱动的 `retry` | `frontend/src/lib/swarmStatus.ts:58-74,277-284` |

**终态调和规则**（每次读取时执行，`agent/src/swarm/store.py:364-460`）：

1. run 已是 `completed`/`failed`/`cancelled` → 原样返回；
2. 所有 task 均终态但 run 仍 `running`（宿主进程死亡）→ 从 task 推导：全 `completed`→`completed`；含 `failed`→`failed`；仅 `cancelled`+`completed`→`cancelled`；其余→`failed`（`_recover_terminal`）；
3. run 非终态且超过心跳阈值无事件 → 非终态 task 标 `failed` 后再推导（`_reap_stale`）。

**Swarm SSE 事件类型全集**（`/swarm/runs/{id}/events` 流，`evt.type` 即 SSE event 名）：
`run_started` `layer_started` `task_started` `worker_started` `tool_call` `tool_result` `task_heartbeat` `worker_text` `task_completed` `worker_completed` `task_failed` `worker_failed` `worker_timeout` `worker_incomplete` `task_blocked` `task_retry` `run_completed` `run_error`（发射端 `agent/src/swarm/runtime.py`、`worker.py`、`src/tools/swarm_tool.py`；消费端映射 `swarmStatus.ts:161-284`）。流结束事件为 `done`，其 data 为 `{"status":"missing"}`（run 不存在）或 `{"status": <RunStatus>}`（`agent/src/api/swarm_routes.py:200,207`）。

### 1.6 定时研究（Scheduled Runs）

| 枚举名 | 取值 | 定义位置 |
|---|---|---|
| JobStatus | `pending` `running` `completed` `failed` `cancelled` | `agent/src/scheduled_research/models.py:182-189` |
| failure_kind | `dispatch`（provider/session 失败）/ `schedule`（调度自身失败）/ `null` | `models.py:214,295-296`（其它值在反序列化时直接抛错） |

- 响应模型 `ScheduledRunResponse`：`id, prompt, schedule, next_run_at, status, created_at, last_run_at?, consecutive_failures, last_error?, failure_kind?, config, timezone?`（`agent/src/api/scheduled_routes.py:209-224`）。
- `schedule` 字段本身是**二选一格式**：纯整数 = 毫秒间隔；5 段 cron 表达式（`min hour dom mon dow`）。

### 1.7 Alpha Zoo

| 枚举名 | 取值 | 定义位置 |
|---|---|---|
| category | `alive`（ic_mean>0.02 且 ic_positive_ratio≥0.55 且 \|t\|>2）/ `reversed`（ic_mean<-0.02 且 \|t\|>2）/ `dead`（其余） | `agent/src/factors/bench_runner.py:11-13,111-119`；前端 union `api.ts` `AlphaBenchTopRow.category` |
| compare sort | `ir`（默认）/ `ic_mean` / `ic_positive_ratio` / `ic_count` | `agent/src/api/alpha_routes.py:115`（`_VALID_SORTS`） |
| zoo id | `alpha101` `gtja191` `qlib158` `academic` `fundamental` | `alpha_routes.py:104`（`_VALID_ZOOS`） |
| theme | `momentum` `reversal` `volume` `volatility` `quality` `value` `liquidity` `microstructure` `sentiment` `growth` `leverage` | `alpha_routes.py:105-108`（`_VALID_THEMES`） |
| universe（`/alpha/list` 过滤） | `equity_us` `equity_cn` `equity_hk` `equity_in` `equity_kr` `crypto` `futures` | `alpha_routes.py:109-112`（`_VALID_UNIVERSES`） |
| universe（bench/compare 执行） | `csi300` `sp500` `btc-usdt` | `alpha_routes.py:116`（`_BENCH_UNIVERSES`） |
| bench/compare job status | `queued` → `running` → `done` \| `error` | `alpha_routes.py:240-241,253,268,278,289,528,612` |

- ⚠️ 两个 universe 集合**不可互换**：list 过滤用市场分类名，bench/compare 用具体成分集名；传错返回 400/422。
- bench/compare SSE 流（`/alpha/bench/{job_id}/stream`）事件：`progress` / `result` / `error` / `done`（`alpha_routes.py:754` `_sse` 及各调用点）。

### 1.8 相关性 / 机制

| 枚举名 | 取值 | 定义位置 |
|---|---|---|
| correlation method | `pearson`（默认）/ `spearman` | `agent/src/api/system_routes.py:279,299-300`（其它值 400） |
| regime fused 数组 | 每 bar `0`/`1` 整数 | `agent/backtest/regime.py:84-93` |
| regime episode `end` | 日期字符串；进行中 episode 为 `null` | `regime.py:126-129`；前端 `RegimeEpisode.end: string \| null`（`api.ts:38-41`） |

### 1.9 LLM 设置（模型发现）

| 枚举名 | 取值 | 定义位置 |
|---|---|---|
| warning_code | `oauth_discovery_unsupported` / `api_key_required` / `model_list_unavailable` / `null`（发现成功） | `agent/src/api/settings_routes.py:88-90,100,292-318` |

### 1.10 IM 通道适配器

| 字段 | 类型 | 含义 | 定义位置 |
|---|---|---|---|
| `configured` | boolean | 配置文件中存在该通道配置 | `frontend/src/lib/api.ts:415-425`（契约）；服务端状态装配 `agent/src/channels/manager.py` |
| `enabled` | boolean | 配置中 `enabled: true` | `manager.py:70,467` |
| `available` | boolean | 依赖 SDK 可导入 | `manager.py:79-92,118`、`agent/src/channels/registry.py`（`_INSTALL_HINTS`/`_AVAILABILITY_FLAGS`） |
| `loaded` | boolean | 适配器实例已加载 | `manager.py:93,119,130` |
| `running` | boolean | 适配器轮询/监听中 | `manager.py:94,120,229` |

**状态读法**：`available=false` → 看 `install_hint`（缺 extra 包）；`configured && enabled && available && !loaded` → runtime 未启动；`loaded && running` 才是真正在收发消息。runtime 级还有 `running` / `inbound_queue` / `outbound_queue` / `session_count`（`api.ts:427-433`）。

**16 个适配器名**（`agent/src/channels/registry.py:33-50` `_INSTALL_HINTS` 键集）：
`websocket` `telegram` `slack` `discord` `matrix` `whatsapp` `signal` `qq` `napcat` `weixin` `wecom` `feishu` `dingtalk` `msteams` `email` `mochat`。

### 1.11 实盘交易（Live Trading）

| 枚举名 | 取值 | 定义位置 |
|---|---|---|
| connection_state | `connected` `error` `not_configured` `ready` | `agent/src/api/live_routes.py:236`（`_CONNECTION_STATES`） |
| environment_identity | `config_declared` `config_declared_live` `config-declared` `header_flag+uid_pin` `host_separated` `read_only_no_runtime_discriminator` `simulated_locally` `path_separated_key_bound` `trd_env_acc_list` | `live_routes.py:223-235`（`_ENVIRONMENT_IDENTITIES`） |
| error_code | `authentication_failed` `broker_error` `credentials_conflict` `credentials_missing` `credentials_partial` `network_unreachable` `sdk_missing` | `live_routes.py:237-248`（`_ERROR_CODES`） |
| capabilities | 读能力五元组 `account.read` `positions.read` `orders.read` `quotes.read` `history.read`；写能力 `orders.place`、`orders.place.requires_mandate`；探测能力 `mcp.read.discovery` | `agent/src/trading/types.py:11-17`（`READ_CAPABILITIES`）；`connectors/*/profiles.py`（组合）；`ibkr/profiles.py:36` |
| Environment | `paper` / `live` | `agent/src/trading/types.py:8` |
| Transport | `local_tws` / `remote_mcp` / `broker_sdk` | `agent/src/trading/types.py:9` |
| TradeMarker.side | `BUY` / `SELL` | `api.ts:483`（回测图表交易标记） |

- 上述三个诊断字段经 `_closed_vocabulary()` 白名单过滤：**不在集合内的值会被归一为 `null`**（`live_routes.py:250-252`），客户端可安全按封闭枚举渲染。
- live SSE 事件：`mandate.proposal` `mandate.committed` `live.halted` `live.resumed` `live.action`（payload 见 07 篇）。

### 1.12 SSE 事件名总目录（session 流 `/sessions/{id}/events`）

| 分组 | 事件名 | 发射端（示例） |
|---|---|---|
| 聊天流 | `text_delta` `reasoning_delta` `stream_reset` `thinking_done` `tool_call` `tool_result` `compact` `tool_heartbeat` `tool_progress` `llm_usage` | `agent/src/agent/loop.py:917,1131` 等 |
| Swarm 桥接 | `swarm.started` `swarm.event` | `agent/src/tools/swarm_tool.py:817` |
| Attempt | `attempt.created` `attempt.started` `attempt.completed` `attempt.failed` `attempt.cancelled` | `agent/src/session/service.py:210` 等 |
| 会话 | `message.received` `session.created` | `service.py` / `sessions_routes.py` |
| 研究目标 | `goal.created` `goal.evidence` `goal.updated` | `agent/src/tools/goal_tool.py:212`、`sessions_routes.py:448` |
| 实盘 | `mandate.proposal` `mandate.committed` `live.halted` `live.resumed` `live.action` | `sessions_routes.py:227`、`live_routes.py` |
| 传输控制 | `heartbeat` `done` | 各 SSE 生成器 |
| 后端发射但参考实现未订阅 | `mcp.warning` `session_cleared` | `loop.py` / `service.py`（未知事件名会被 `useSSE.ts` 忽略——新客户端同样需要"未知事件跳过"策略） |

订阅清单权威：`frontend/src/hooks/useSSE.ts:86-95`（`knownTypes`）。各事件 payload 字段级定义见 **01 篇**（聊天/attempt/goal/tool）与 **07 篇**（live/mandate）。

### 1.13 回测配置：数据源 / 间隔 / 引擎

| 枚举名 | 取值 | 定义位置 |
|---|---|---|
| `source`（24 值） | `tushare` `okx` `binance` `yfinance` `akshare` `baostock` `tencent` `mootdx` `ccxt` `futu` `eastmoney` `sina` `stooq` `yahoo` `finnhub` `alphavantage` `tiingo` `fmp` `qveris` `india_broker` `pykrx` `longbridge` `mt5` `local` `auto` | `agent/backtest/loaders/registry.py:33-58`（`VALID_SOURCES`，含 `auto`；回归测试强制与注册器同步） |
| `interval`（7 值，**大小写敏感**） | `1m` `5m` `15m` `30m` `1H` `4H` `1D` | `agent/backtest/runner.py:51`（`_VALID_INTERVALS`，`field_validator` 精确匹配 `runner.py:105-110`） |
| `engine` | `daily` / `options` | `runner.py:52`（`_VALID_ENGINES`） |

- ⚠️ 小写别名（`1h`/`4h`/`1d`）只在**各 loader 层**被归一化（如 `agent/src/trading/connectors/okx/sdk.py:321` 的映射表；历史修复 PR #812–#838、#1003）；**回测 config schema 不做归一化**，`"4h"` 会被 `BacktestConfigSchema` 直接拒绝。IM 插件生成/转发 config 时必须用规范大小写。

---

## §2 数值与类型校验指南

### 2.1 可空/可选数值字段（必须 null 防御）

| 场景 | 说明 | 依据 |
|---|---|---|
| `RunListItem.total_return` / `sharpe` | `Optional[float]`——metrics.csv 缺失或解析失败即为 null | `agent/src/api/models.py:49-50` |
| `RunData.metrics` 整体 | 可选；非回测类运行（纯研究问答）没有 metrics | `models.py:69` |
| `BacktestMetrics` 扩展键 | 后端 `extra="allow"`、前端 `[key: string]: number`——键集合**开放**（如 `turnover`、`calmar`、`benchmark_return` 视运行而出现或缺席） | `models.py:20-31`、`api.ts:666-674` |
| regime 数组 | `density` / `smoothed` 是 `(number \| null)[]`——warm-up 窗口内无值 | `api.ts:43-49` |
| `RegimeEpisode.end` | 进行中 episode 为 `null` | `api.ts:38-41` |
| live 诊断三件套 | `connection_state` / `environment_identity` / `error_code` 均 `string \| null`（含白名单归一） | `live_routes.py:250-252`、`api.ts:1110-1127` |
| `LiveRunnerLiveness.last_tick` | `number \| string \| null`——runner 从未启动为 null | `api.ts:1101-1107` |
| scheduled `last_run_at` / `failure_kind` | 从未运行 / 未失败为 null | `scheduled_routes.py:217-222` |
| goal 预算字段 | `token_budget` / `turn_budget` / `time_budget_seconds` 可 null（未设预算） | `api.ts:712-716` |

**总则**：对所有数值字段先 `Number.isFinite()` 再渲染；参考实现对未知 metric 键也有兜底（`formatters.ts` 未识别键走 `v.toFixed(4)`，`formatters.ts:66`）。

### 2.2 字符串编码的数字（CSV 行记录）

`artifacts_equity_csv` / `artifacts_metrics_csv` / `artifacts_trades_csv` / `artifacts_positions_csv` / `artifacts_target_positions_csv` 全部是 `Array<Record<string, string>>`——**每个单元格都是字符串**（后端用 `csv.DictReader` 原样转 dict，`runs_routes.py:33-44`）。渲染前必须 `Number()` / `parseFloat()` 并做 NaN 防御。

`equity_curve` 是派生预览：`equity` / `drawdown` 类型为 `string | number`（同样来自 CSV 行映射，`runs_routes.py:193-206`、`api.ts:490-494`）。

### 2.3 非有限值（NaN/Infinity）→ null 归一点

后端在以下位置强制"严格 JSON"：

| 位置 | 机制 | 依据 |
|---|---|---|
| `artifacts/validation.json` 写入 | `_finite_or_none` 把非有限 float 归一为 None，再 `json.dumps(..., allow_nan=False)` | `agent/backtest/validation.py:431,443-453` |
| swarm worker `get_market_data` 工具 | 严格 JSON，非有限 float 序列化为 `null` | `agent/src/tools/market_data_tool.py`（PR #199 引入） |
| 各分析工具 | `json.dumps(..., allow_nan=False)` + `math.isfinite` 守卫：`cashflow_analytics_tool.py:300,331`、`factor_analysis_tool.py:75,105`、`get_fundamentals_tool.py:41,210`、`options_payoff_tool.py:225,266` | 同左 |

**客户端仍应假设数值字段可能出现 `null`**，不要假设一定是数字。

### 2.4 百分比 vs 小数约定（以参考实现 formatters 为准）

参考实现 `frontend/src/lib/formatters.ts:49-67` 揭示了后端 metrics 的量纲：

| 指标族 | 后端量纲 | UI 处理 | 键 |
|---|---|---|---|
| 收益率族 | **小数**（0.12 = 12%） | ×100 加 `%`，正数加 `+` | `total_return` `annual_return` `win_rate` `max_drawdown` `benchmark_return` `excess_return`（`PCT_KEYS`，`formatters.ts:49`） |
| 比率族 | 原值 | 直接两位小数 | `sharpe` `calmar` `sortino` `profit_loss_ratio` `information_ratio`（`RATIO_KEYS`） |
| 整数族 | 整数 | 取整 | `trade_count` `max_consecutive_loss`（`INT_KEYS`） |
| 特殊 | — | `final_value` 千分位整数；`avg_holding_days` 一位小数 | 同上文件 64-65 行 |

关键细节：
- `max_drawdown` 是**负小数**（情绪阈值：`> -0.05` 绿、`> -0.2` 中、否则红，`formatters.ts:71`）；
- `win_rate` 是 **0–1 小数**（阈值 0.5 / 0.35，`formatters.ts:73`）；
- IM 插件若要显示百分比，必须自己做 ×100——**后端不会发百分数**。

### 2.5 时间戳：epoch vs ISO 的分治

| 族 | 格式 | 依据 |
|---|---|---|
| session / message / attempt / goal / channel | **时区感知 UTC ISO 字符串**（`2026-08-14T03:00:00+00:00`） | `agent/src/session/models.py:12-13`（`_utc_now_iso`）、`agent/src/goal/store.py`（`_now_iso`） |
| scheduled jobs `next_run_at` / `created_at` / `last_run_at` | **epoch 毫秒整数**（反序列化时强制 int，否则 TypeError） | `agent/src/scheduled_research/models.py:205-208,225-227,279-283` |
| ToolTrailItem `timestamp` | **epoch 毫秒** | `agent/src/session/service.py:471` |
| live runner `last_tick` | **epoch 秒**（前端注释声明） | `api.ts:1101-1107` |
| live `last_checked_at` / mandate `expires_at` | ISO 字符串（`_canonical_utc_timestamp` 归一为 `...Z`） | `live_routes.py:255-266` |
| run 列表 `created_at` | 从 run_id 解析的本地墙钟字符串 `YYYY-MM-DD HH:MM:SS`（无时区），解析失败回退目录 mtime | `runs_routes.py:386-402` |

⚠️ 同一页面可能混用三种时间表示（ISO / epoch ms / epoch s）。建议封装统一的 `toEpochMs()` 归一器，渲染时转用户时区。

### 2.6 单位与乘数陷阱（A 股成交量）

- 历史背景：A 股回退链上五个源报**手数**（board lots），BaoStock 报**股数**，回退切换曾导致 volume 静默缩放 100×（PR #1065 / #1067，issue #1062）。
- 现行契约：loader 声明每市场 volume 单位；**每个 symbol 的实际单位**暴露在 `_provenance.volume_unit`，取值 `"lots"`（1 手 = 100 股）/ `"shares"`；未声明的市场为 `null`（`agent/backtest/loaders/base.py:620-631`）。
- swarm/agent 的 `get_market_data` 工具描述明确要求消费方读 `_provenance.volume_unit` 而非假设单位（`agent/src/tools/market_data_tool.py:18-20`）。
- IM 插件若渲染成交量或基于 volume 的信号：先读 provenance 单位，跨源对比前归一。

---

## §3 集成陷阱清单

**P1 · SSE 票据一次性 + 60 秒 TTL**
现象：重连时复用旧 ticket → 401。原因：票据首次查验即销毁（无论是否过期），TTL 60 秒（`agent/src/api/security.py:306,319-324`）。对策：**每次连接/重连前**重新 `POST /auth/sse-ticket`（头部带 Bearer），参考实现明确不缓存 URL（`api.ts:182` 注释）。服务端到服务端消费可直接用 Bearer 头，无需票据。

**P2 · Last-Event-ID 是查询参数（session 流）**
现象：断线重连后丢事件。原因：浏览器 `EventSource` 重连虽会自动带 `Last-Event-ID` 头，但本系统参考实现走**查询参数** `?Last-Event-ID=<id>`（`useSSE.ts:66`）；后端 session 流同时接受查询参数与头，头优先（`sessions_routes.py:756-769`）。注意 **swarm 流只接受 HTTP 头**（`swarm_routes.py:177` `Header(alias="Last-Event-ID")`）。对策：session 流用查询参数传；swarm 流依赖 EventSource 原生头或自行实现 fetch-streaming。

**P3 · 事件回放会重复投递 → 消费端必须幂等**
现象：重连后同一消息渲染两次。原因：`?replay=active` + Last-Event-ID 回放与实时流可能重叠（`sessions_routes.py:770-781`）。对策：按事件 ID 去重——参考实现用 500 容量 LRU 集合（`useSSE.ts:41` `seenIdsRef`/`trackEventId`）。所有状态更新必须写成幂等赋值而非累加。

**P4 · 一个会话同时只跑一个 attempt → HTTP 409**
现象：连发消息报 409。原因：running attempt 存在时拒绝新 attempt（`sessions_routes.py:489,542,591,714`）。对策：UI 在 attempt 运行期间禁用发送，或对 409 给出"正在处理中"提示；轮询 attempt 状态（`attempt.*` 事件）后再允许下一轮。

**P5 · DELETE /scheduled-runs/{id} 返回真正的空 204**
现象：`res.json()` 抛错。原因：响应体为空（PR #1068；`scheduled_routes.py:354,365`）。对策：对 204 不做 body 解析；参考实现对空 body 返回 `{}`（`api.ts` `request()` 空 text 分支）。

**P6 · Content-Type 防御（代理 HTML 错误页）**
现象：反向代理在 502/504 时返回 HTML 错误页，`JSON.parse` 出乱码错误。对策：强制校验响应 `content-type` 含 `application/json`，否则抛错并附前 80 字符预览（参考实现 `api.ts:90-96`）。

**P7 · 路径参数编码 + 不安全 ID 拒绝**
现象：含特殊字符的 id 返回 422/400。原因：run/session/swarm ID 经 `_SAFE_PATH_PARAM_RE = ^[A-Za-z0-9_-]{1,128}$` 校验，换行等畸形字符直接拒绝（`agent/src/api/helpers.py:263`，安全加固 PR #80）。对策：客户端对 ID 做 `encodeURIComponent`（参考实现 `api.ts` 各 `encodeURIComponent` 调用点），且不要构造含 `.`/`/`/空白的 id。

**P8 · positions.csv = 实际成交，target_positions.csv = 优化器目标**
现象：把 `artifacts_positions_csv` 当目标权重展示，或反之。原因：PR #1082 起两者分离——`positions.csv` 是**实际成交后**的持仓权重（受整手取整/费用/拒单影响），`target_positions.csv` 是**优化器请求**的目标权重（`models.py:80-85` 字段描述）。对策：展示"实际持仓"用前者，展示"调仓指令/目标"用后者；两者可能显著不同（如目标 80% 仓位 vs 实际 20%）。

**P9 · equity_curve 有截断，artifacts_equity_csv 全量**
现象：长回测的净值曲线只有前段。原因：`equity_curve` 只取 equity.csv **前 1000 行**（`runs_routes.py:193-206`）；`trade_log` 同样截断前 500 行（`runs_routes.py:207`）。对策：完整曲线/完整交易表请用 `artifacts_equity_csv` / `artifacts_trades_csv`（无截断，但注意 P-2.2 的字符串单元格）。

**P10 · 调度执行器默认关闭**
现象：job 创建成功、列表可见，但永不触发。原因：后台执行器仅在 `VIBE_TRADING_ENABLE_SCHEDULER=1` 启动的服务器上运行（`agent/src/scheduled_research/executor.py:33,53`）；API 始终可创建/列出/删除 job。对策：UI 上对"已创建但未触发"给出服务端开关提示，不要渲染为调度 bug。

**P11 · shell 工具默认不出现在 HTTP/SSE 面**
现象：期待聊天里出现 `bash`/`background_run` 工具事件却从未出现。原因：HTTP/SSE 与 MCP 面默认禁用 shell 工具，需服务端 `VIBE_TRADING_ENABLE_SHELL_TOOLS=1`（00 篇 §6）。对策：工具渲染器按开放集合做未知工具兜底，不要硬编码期待 shell 工具。

**P12 · 并非每个 run 都有完整产物（report-worthy 门控）**
现象：纯研究问答 run 没有 metrics/图表，UI 渲染空面板。原因：产物取决于运行类型；参考实现用 `isReportWorthyRun()` 门控"报告级运行"（有 metrics/run_card/equity/trade/validation/特定产物文件之一才展示报告视图，`frontend/src/lib/runReports.ts:11-25`）。对策：复用同等门控逻辑，非报告级 run 只渲染对话摘要。

**P13 · CORS/CSRF（内嵌 WebView 场景）**
现象：跨源 POST 被 403 拒绝。原因：非安全方法遇 `Sec-Fetch-Site: cross-site` 或不同源 `Origin` → 403（00 篇 §3.4）；CORS 默认只放行 loopback 三端口，追加用 `VIBE_TRADING_EXTRA_CORS_ORIGINS`（禁止 `*`）。对策：IM 插件走**服务端代理**（无 Origin 头，天然豁免）；WebView 内嵌则保持同源或由运维配置受信 origin。

**P14 · X-Frame-Options: DENY 阻断 iframe 嵌入**
现象：iframe 嵌入 Vibe-Trading 页面白屏/拒绝连接。原因：每个响应带 `X-Frame-Options: DENY`（00 篇 §3.5）。对策：IM 插件走**纯 API 自渲染**，不要 iframe 嵌入既有 SPA；确需嵌入须部署层改造。

**P15 · 配置 API key 后所有客户端（含 loopback）都必须带 key**
现象：本机直连也 401。原因：Key-first 策略——一旦配置 `API_AUTH_KEY`，loopback 信任失效，所有请求必须携带有效 Bearer（00 篇 §3.1）。对策：IM 插件把 key 作为部署配置项，每次请求注入 `Authorization: Bearer <key>`；SSE 走票据流程（P1）。

**P16 · 回测 config 的 interval 大小写敏感**
现象：传 `"4h"` 得到 422 `unsupported interval`。原因：config schema 精确匹配 `{1m,5m,15m,30m,1H,4H,1D}`（`runner.py:51,105-110`），小写归一化只在 loader 层。对策：转发/生成 config 时统一规范大小写（见 §1.13）。

**P17 · run status 可能是 `unknown`，且描述文档与实际值有漂移**
现象：列表里出现 `unknown` 状态。原因：无 state.json 且无产物兜底即 `unknown`（`runs_routes.py:377`）；另外 `RunResponse` 的字段描述写 `aborted` 但实际写入 `cancelled`（§1.1）。对策：状态渲染器对未知值兜底为中性样式，不要断言封闭集合。

**P18 · GoalRiskTier 第 4 层不接受客户端提交**
现象：提交 `live_trading_or_execution` 被拒。原因：该层仅存在于后端枚举（`goal/models.py:33`），前端契约只有 3 值（`api.ts:698-701`），agent 工具侧对高风险层有拦截。对策：创建 goal 只发 3 个契约值。

**P19 · 两套 universe 枚举不可混用（alpha 域）**
现象：`/alpha/bench` 传 `equity_us` 得 422。原因：list 过滤用 `_VALID_UNIVERSES`（市场分类），bench/compare 用 `_BENCH_UNIVERSES`（`csi300`/`sp500`/`btc-usdt`）（§1.7）。对策：按端点查表，不要共用一个下拉选项集。

---

## §4 客户端验收清单

IM 插件视图层上线前逐项自查：

**认证路径**
- [ ] 所有 REST 请求注入 `Authorization: Bearer <key>`（配置 key 的部署，含 loopback，P15）
- [ ] SSE 连接走 `POST /auth/sse-ticket` → `?ticket=` 流程；每次连接/重连重新 mint（P1）
- [ ] 服务端 SSE 消费（非浏览器）可直接用 Bearer 头，不经过票据
- [ ] 401/403 统一映射为"需要 API key"类提示（参考 `api.ts:66-68`）

**SSE 重连与去重**
- [ ] 指数退避重连（参考 1s→30s ×2，`useSSE.ts`）
- [ ] 记录 lastEventId；session 流重连带 `?Last-Event-ID=`（P2）；swarm 流用头
- [ ] 按事件 ID 去重（LRU≥500），所有处理器幂等（P3）
- [ ] `heartbeat` 不当业务事件渲染；`done` 触发终态收敛
- [ ] 未知事件名安全跳过（后端会新增事件，如 `mcp.warning`/`session_cleared`）

**枚举渲染**
- [ ] §1 每个枚举域要么完整渲染所有取值，要么有明确的未知值兜底样式（P17）
- [ ] run status 覆盖 `success/failed/cancelled/unknown` + 未知兜底
- [ ] attempt 六态 + 409 冲突提示（P4）
- [ ] goal 12 态、criterion 全生命周期（pending→covered→satisfied 族）
- [ ] swarm run 五态 + task 展示态（含 `retry`/`blocked`）；终态以调和后读取为准（§1.5）
- [ ] scheduled job 五态 + `failure_kind` 双值；DELETE 按空 204 处理（P5、P10）
- [ ] alpha category 三色（alive/reversed/dead）、job 四态（queued/running/done/error）
- [ ] live 诊断三件套按封闭枚举渲染，`null` 表示"未知/不适用"（§1.11）
- [ ] 通道适配器五布尔状态 + 16 适配器名 + `install_hint` 展示

**数值防御**
- [ ] 所有数值字段经 `Number.isFinite` 过滤后渲染（§2.1）
- [ ] CSV 行记录（`artifacts_*_csv`）单元格按字符串解析为数字（§2.2）
- [ ] 收益率族 ×100 显示百分比；`max_drawdown` 负值、`win_rate` 0–1（§2.4）
- [ ] 完整净值曲线用 `artifacts_equity_csv`，不依赖截断的 `equity_curve`（P9）
- [ ] volume 渲染读 `_provenance.volume_unit`（§2.6）

**时间戳**
- [ ] 统一归一器处理 ISO-UTC / epoch-ms / epoch-s 三种表示（§2.5）
- [ ] scheduled 页按 epoch 毫秒解析 `next_run_at`；session/goal 按 ISO 解析
- [ ] 渲染转用户本地时区，跨时区部署注明 UTC 原值

**错误与响应解析**
- [ ] 错误包体按 `body.detail || body.message || "HTTP <status>"` 顺序取文案（`api.ts:60-69`）
- [ ] 强制校验 `content-type: application/json`（P6）
- [ ] 空 body → `{}`；204 不解析 body（P5）
- [ ] 路径参数 `encodeURIComponent`（P7）

**产物语义**
- [ ] positions.csv / target_positions.csv 区分展示（P8）
- [ ] report-worthy 门控（P12）；非报告级 run 不渲染空报告面板
- [ ] 不期待 shell 工具事件（P11）；不 iframe 嵌入 SPA（P14）

---

## 附：本篇未覆盖 / 源码未明确项

| 项 | 状态 |
|---|---|
| Message role 除 `user`/`assistant` 外的取值 | REST 消息流未见 `system` 发射；`models.py` 默认值为 `user`，未做封闭约束「源码未明确」 |
| goal `protocol` 除 `thesis_review` 外的取值 | 默认值见 `goal_tool.py:204`，未见枚举化清单「源码未明确」 |
| claim_type 取值集 | 自由字符串，无封闭枚举（`goal/models.py:72`） |
| run 进行中的实时状态 | 无 `running` 持久态；经 session attempt 状态表达（§1.1/§1.2） |

**关联文档**：00 篇（架构/认证/SSE 通用机制）· 01 篇（session/聊天 SSE payload）· 02 篇（run/产物字段）· 03 篇（alpha）· 04 篇（swarm）· 05 篇（correlation/regime）· 06 篇（scheduled）· 07 篇（live/mandate）· 08 篇（settings/channels/uploads）。
