# MCP 使用说明（Resource Manager `/mcp`）

> 状态：living ｜ 最后更新：2026-11-11 ｜ 依赖：控制面开关 `RM_MCP_ENABLED=true` 时挂载 ｜ 正典：`src/resource_manager/mcp/` 与根 `README.md` API 表（不一致时以代码为准）
> 公共镜像说明：本文档同步自 JetWind 控制面仓库的
> [`docs/reference/mcp-usage.md`](https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/reference/mcp-usage.md)（正典）。
> 本页供外部阅读，可能与正典存在版本滞后；以源仓库为准。
>
> 读者：想从 MCP 客户端（QoderWork、Qoder CLI、Codex、VS Code Copilot、自写客户端或 curl）调用云端金融研究任务的用户与开发者。
>
> 背景与追踪：父需求 [Issue #8 金融分析 MCP：工具规划、长程任务监控、断点续跑与统一结果交付](https://github.com/shadowinlife/aliyun-acs-agent-service/issues/8)（含子工单 #9–#23）；可行性 Gate 证据 → [`https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/workstreams/mcp/`](https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/workstreams/mcp/README.md)；Stage 2 实施状态 → [`https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/iterations/mcp-financial-delivery-stage-2/README.md`](https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/iterations/mcp-financial-delivery-stage-2/README.md)；外部客户端支持矩阵 → [`https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/workstreams/mcp/stage2-client-compatibility.md`](https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/workstreams/mcp/stage2-client-compatibility.md)。

## 0. 这是什么

`/mcp` 是控制面（Resource Manager，简称 RM）对外暴露的 **Remote MCP 门面**：六个任务型工具，通过 MCP Streamable HTTP 提供。它只做**任务编排**——提交研究请求、跟踪进度、取回结果——真正干活的是云端沙箱里的 Agent 与金融工具（见 §8）。

- 异步执行：`research.ask` 提交后立即返回 `task_id`，任务在后台排队、派发到沙箱执行；客户端断线、关窗口都不影响任务继续跑。
- 稳定性边界：单任务墙钟预算、每用户并发/提交配额、任务行保留期全部有上限（见 §5）；开关关闭时 `/mcp` 与未知路径返回完全相同的 404，不暴露自身。
- 面向客户端：QoderWork、Codex、VS Code Copilot 等均可接入，但**没有任何一行被真实客户端证据验证过**（当前矩阵全部为 `degraded`/`unverified`，见 `stage2-client-compatibility.md`）；协议层由 Qoder CLI 0.2.12 实测通过（Gate 0/1/2，见 `https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/workstreams/mcp/`）。

## 1. 端点与鉴权

| 环境 | 端点 |
|---|---|
| 生产 | `https://www.jetwind.net/mcp` |
| 本地开发 | `http://127.0.0.1:8900/mcp`（`RM_MCP_ENABLED=true` 启动 `uv run python -m resource_manager`） |

**鉴权**：每个请求带 `Authorization: Bearer <凭据>`，两种凭据等价：

1. **会话 token**：登录接口返回的 Bearer token。
2. **API key**：`acs_live_…`（通过 `POST /api/v1/auth/api-keys` 创建；明文只显示一次，丢了自己新建）。适合脚本与 MCP 客户端长期配置。

- 凭据错误、缺失、过期、被撤销 → **统一 401** `{"detail":"invalid credentials"}`，与账号是否存在无关（反枚举），会话 token 与 API key 的失败响应逐字节相同。
- **Origin 白名单**：请求带 `Origin` 头时必须命中 `RM_CORS_ORIGINS`，否则 403 `{"detail":"origin not allowed"}`。服务端**永不反射**调用方 origin（配置含 `*` 时任何带 Origin 的请求也一律 403）。
- 日志与审计绝不记录 `Authorization` 头。

### 1.1 如何从前端网页获取凭据

**方式一（推荐）：网页设置页创建 API key**

1. 浏览器登录前端 `https://www.jetwind.net/sandbox`（需先注册账号）。
2. 进入「设置」，找到 **API Keys** 管理区（设置页的一个 tab，即 `ApiKeyManager` 组件）。
3. 创建一把 key：明文 `acs_live_…` **只在创建响应里显示一次**，立即复制保存；关闭弹窗后无法再次查看（只能撤销重建）。
4. 默认有效期 90 天（创建时可设 1–365 天）。这把 key 适合长期写入 MCP 客户端配置（§6.7 的 `Bearer ${ACS_TOKEN}`）。

**方式二（临时调试）：取当前会话的 Bearer token**

- token 持久化在浏览器的 localStorage，键为 `invest-assistant-auth`（JSON 结构 `{token, expiresAt, user}`）。取法：DevTools → Application → Local Storage → 站点 → 展开该键 → 复制 `token` 字段值。
- 或者 DevTools → Network 面板，刷新页面后点开任意 `/api/v1/…` 请求，从请求头的 `Authorization: Bearer <token>` 复制。
- 注意会话 token 有效期 7 天（`RM_TOKEN_TTL_DAYS`），登出、改密码或到期即失效——只适合临时调试，不适合长期配置客户端。

安全提醒：`acs_live_…` key 与会话 token 都等同账号权限（可提交任务、读取你的报告与结果），不要粘贴到公开渠道、日志或截图里。

## 2. 协议与传输

- JSON-RPC 2.0，MCP 协议版本协商到 **`2025-11-25`**。
- 单端点 `POST /mcp`（Streamable HTTP）：JSON body，或 `Accept: text/event-stream` 时返回**单帧** `event: message` 后关闭。
- 无 `id` 的请求（通知，如 `notifications/initialized`）→ `202` 空 body。
- 请求体上限 **256 KiB**：带 `Content-Length` 的在读取前拒绝（413 `{"detail":"request body too large"}`）；chunked 流式请求边读边计数，超限立即中止。恰好 256 KiB 可正常解析。
- 请求日志只记录方法、路径、RPC 方法、RPC id、状态码。

**标准调用顺序**（MCP 客户端自动完成；手工测试可按此序列）：

```
1. initialize                 → {"protocolVersion":"2025-11-25","serverInfo":{"name":"resource-manager-mcp",...}}
2. notifications/initialized  → 202
3. tools/list                 → 工具清单与输入输出 schema（严格模式：additionalProperties:false，多余参数报错）
4. tools/call                 → 工具结果（可重复）
```

JSON-RPC 错误：`-32700` 解析错误（HTTP 400）、`-32601` 方法未知、`-32602` 参数无效。工具业务失败不表现为协议错误，而是在 `tools/call` 结果里返回 `isError: true` + 结构化 `code`（见 §5.2）。

## 3. 工具清单

`tools/list` 按固定顺序宣告 6 个工具。门控附加工具（`task.resume`、`task.rerun`、`backtest.run`）只有在对应部署开关打开时才会出现在清单里，`backtest.run` 在外部能力验收前一律回答 `CAPABILITY_PENDING`。

| 工具 | 读写 | 作用 |
|---|---|---|
| `research.ask` | 写 | 提交一个研究任务，返回 `task_id`（异步，立即返回） |
| `task.get` | 读 | 查询任务：状态、进度、事件页、结果、错误、下次轮询间隔 |
| `task.wait` | 读 | 阻塞等待至多 `timeout_ms`（1–25 秒）后返回任务视图；**只观察**，超时/断连绝不取消底层任务 |
| `task.cancel` | 写 | 取消任务（幂等），返回更新后的视图 |
| `artifact.get` | 读 | 读取已产出的报告/结果（文件归档面，任务过期后仍可读） |
| `service.info` | 读 | 无参数能力文档：工具列表、可选值、配额、执行门状态 |

### 3.1 `research.ask` 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `question` | ✅ | 自然语言研究问题，≤ 2000 字符 |
| `universe` | — | 标的范围，如 `300750.SZ` 或市场标签；空 = 由问题自指 |
| `horizon` | — | 回看窗口：`1M`/`3M`/`6M`/`1Y`/`3Y`，默认 `6M` |
| `depth` | — | `quick`/`standard`，默认 `quick`（决定收集证据的多少） |
| `as_of` | — | `YYYY-MM-DD` 数据截止日；省略 = 最新可用 |
| `thread_id` | — | 会话归属；省略 = 新线程 |
| `idempotency_key` | — | 重试键：同键再次提交返回**第一次创建的任务**（不重复计算）；键相同但问题不同时返回原任务并带 `idempotency_key_reused` 提示。long 模式必填 |
| `execution_mode` | — | `short`（v1 旧契约，默认）/ `long`（v2 长任务） |
| `compute_budget_s` | — | long 模式**硬墙钟预算**（有绝对上下界，具体以 `service.info` 为准），不是 ETA；short 模式拒绝此字段 |
| `workflow` | — | long 模式工作流：`research`（默认）/ `factor_batch`（因子批量原型，需 `factor_spec`） |
| `factor_spec` | — | long + `factor_batch` 的因子批规格：`registry_digest`、`candidate_ids`、`symbols`（≤500 只）、`start_date`/`end_date`/`split_date`、`source`、`as_of`、`forward_return_horizon_days`、`n_groups` |

返回信封：`task_id`、`status`（`queued`）、五步计划 `plan`（确认问题与标的 → 准备数据 → 分析与建模 → 交叉验证 → 结论与报告）、`progress`、`eta_s`（quick ≈120s / standard ≈300s 的**典型值，不是 SLA**）、`thread_id`、`task_url`（相对路径，对应前端任务页）。long 模式另带 `compute_budget_s`、`accepted_at`、`deadline_at`。

提交路径不碰沙箱（只写一行排队记录，实测 p50 ≈5ms / p95 ≈8ms），沙箱工作在后台循环完成。

### 3.2 `task.get` 参数

- `task_id` ✅
- `after_seq`（可选，≥0）：事件游标。传上次收到的最后事件序号，只返回更新的；省略 = 最近一页；`0` = 从最早保留的事件开始。
- `event_limit`（可选）：事件页大小，默认 20，超过 100 钳到 100。

返回：`status`、`plan` 或 v2 `progress`（含 `phase`、`wait_reason`、`wait_detail`、`counters`、`heartbeat_at`、`last_real_progress_at` 等**真实进展证据**，不是心跳）、`events` + `cursor`（事件补读）、`task_url`、`poll_after_ms`（建议下次轮询间隔，默认 3000）、`result`（completed 后）、`error`（failed 后）、`delivery`（交付状态，见 §4）。结果过大的截断规则见 §4。

### 3.3 `task.wait` 参数

- `task_id` ✅、`timeout_ms` ✅（1000–25000，越界报错）。

返回同 `task.get`。适合能阻塞几秒的调用方；在等待期间 HTTP 请求中断**不会**取消底层任务，之后继续 `task.get` 即可。

### 3.4 `task.cancel` 参数

- `task_id` ✅。幂等：已 completed/failed/cancelled 的任务原样返回；已产出的结果不删除。

注意：取消控制面任务 ≠ 远端沙箱已停止的证据（远端停止确认属于未验证能力，见 §7）。

### 3.5 `artifact.get` 参数

二选一指定目标，不能同时给：

- `artifact_id`：交付清单里的不可变产物 id（来自 `task.get` 的 `delivery`/`artifact` 段或截断标记）。
- `task_id` + `kind`：kind ∈ `report`（Markdown 报告）/ `result`（结构化结果 JSON，含回测块）；`equity_csv`/`trades_csv`/`chart` 是沙箱指针，请求会得到 `ARTIFACT_NOT_RETRIEVABLE`。

分页：`offset`（字符偏移）或 `cursor`（上一页返回的续读游标），二选一；大产物按页返回，带 `sha256`、`size_bytes`、`mime`、`resource_link`、`next_offset`/`cursor`。归档产物优先于任务行读取：即使任务行已过期（默认保留 24 小时），已归档的报告/结果仍然可读。

### 3.6 `service.info`

无参数。返回能力文档：6 个工具名、`horizons`/`depths` 可选值、`eta_s`、`question_max_chars`、`wait_timeout_ms`、内联结果上限 `result_inline_chars`、配额（`max_active_tasks_per_user`/`submits_per_hour`）、`artifact_kinds`、执行能力（long 准入与预算范围、回测试点开关、外部门状态 `G-control`/`G-backtest`/`G-factor`，均 `pending`）。正式客户端应在提交前读它确认能力边界。

### 3.7 它可以解答哪些问题

能力边界由官方意图评测集（36 条冻结意图题，`https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/tests/fixtures/mcp_intent_baseline.json`）锚定。按意图分类如下，每类给典型提问示例：

**研究类**（基本面 / 估值 / 行业 / 宏观 / 事件）

- 「分析某只 A 股公司最近三年的营收质量，以及经营现金流和净利润是否匹配。」
- 「某港股公司现在的估值合理吗？请给出判断依据。」
- 「比较新能源车产业链上游和中游的景气度，并说明主要风险。」
- 「如果利率继续上行，高股息板块会受什么影响？」
- 「复盘最近一次行业政策事件对相关板块的影响。」
- 「请对我关于这家公司的投资逻辑做反证，找出最可能推翻它的证据。」

**回测类**（策略 / 组合 / 成本 / 复现）

- 「用双均线策略回测沪深 300 成分股，区间为 2024 年全年，考虑交易成本。」
- 「把这三只股票做成组合，回测再平衡后的表现。」
- 「回测时加入滑点和手续费，看看结果还成不成立。」
- 「这个策略在样本外还有效吗？请给出训练/验证切分。」
- 「复现我上次那个策略版本的回测结果，策略版本号我会提供。」

**因子类**

- 「对某因子做 IC 和 IR 分析，并看不同持有期的衰减。」
- 「筛一批多因子候选，并检查它们之间的相关性。」
- 「对因子做行业和市值中性化后再分层，看看结果是否稳定。」

**机器学习类**

- 「用监督学习做选股，给我训练/验证切分、随机种子和样本外评价。」
- 「解释这个模型的特征重要性，并说明它可能过拟合在哪里。」

**任务生命周期类**（交给 Agent 处理，或客户端直接调工具）

- 「刚才提交的任务现在到哪一步了？」→ `task.get`
- 「我在这里等 20 秒，有结果就告诉我。」→ `task.wait`
- 「停止这个任务。」→ `task.cancel`
- 「把任务报告/结构化结果给我。」→ `artifact.get`

**能力查询类**（→ `service.info`）

- 「你们现在支持哪些市场、哪些任务类型？」
- 「我的预算、配额和服务版本是什么？」

**会要求澄清的问题**（信息不足，先补齐再提交，不会盲目执行）

- 「帮我看看这个股票。」（缺标的与问题）
- 「回测一下。」（缺策略、标的、区间）
- 「用最好的模型帮我分析。」（缺目标与假设）

**明确拒绝的请求**（`must_not_submit`，返回拒绝/转交而非执行）

- 「帮我实盘下单买这只股票。」「策略跑完后自动发布到实盘账户。」——无下单通道。
- 「把全部原始行情和逐笔数据下载给我。」——数据面在沙箱内按研究用途提供，不支持全量导出。
- 「保证这个策略年化 20%。」——不承诺收益。
- 「用强化学习帮我挖一个稳定盈利的策略。」——该工作类型未立项，不承诺能力。
- 「直接修改 Vibe-Trading 仓库里的引擎代码。」——跨仓库改动转交人类处理。

用法要点：把**目标、标的、区间、假设**写进 `question`（必要时配 `universe`/`horizon`/`as_of`/`depth`）；模糊请求会先澄清而不是猜。实际可达的执行能力以 `service.info` 与交付状态为准——回测、因子、监督学习等属于试点范围，外部能力未全部验收（见 §8），不代表每题都能产出（36 题基线中部分条目仍标记 unverified/unsupported。

## 4. 任务生命周期与异步模式

```
research.ask ──► queued ──► running ──► completed ──(delivery pending)──► delivered（可读报告）
                    │            │
                    │            ├──► failed（error 带代码，见 §5.2）
                    │            └──► cancelled
                    └── expired（超过保留期，不再认领/被 GC 删除）
```

- **每用户同时在飞任务默认 1 个**：提交第二条排队任务时若已有未终态任务 → `QUOTA_EXCEEDED` + `retry_after_s`。
- **轮询节奏**：按 `task.get` 返回的 `poll_after_ms`（默认 3000ms）轮询；或调 `task.wait` 阻塞。实测自然提示的客户端会自发达成约每 2 秒一次轮询，提交/查询面按此节奏设计过，无需客户端做指数退避。
- **结果内联上限 50,000 字符**：超过时 `result` 字段被替换为截断标记
  `{"truncated":true,"artifact_ref":"<task_id>/<kind>","proof_evidence":false,"resource_link":{...}}`，
  用 `artifact.get`（传 `artifact_ref` 或 `task_id`+`kind=result`）读全量。
- **交付与计算分离**：`status=completed` 只是计算完成；`delivery.status`（`not_applicable`/`pending`/`delivered`/`failed`）表示报告归档/交付。`delivered` 后 `task.get` 视图附加 `report` 字段与不可变 `artifact` 清单；**completed 但 delivery 仍是 `pending` 时要继续轮询**。
- **事件**：v2 长任务的 `events` 按 `seq` 递增保留（默认上限 200 条/任务），`cursor` 支持断线补读（`after_seq`），不重复、不倒退。
- **重启语义**：服务重启会把遗留 `running` 行收敛为 `failed`（`TASK_STALE`），**不自动重跑**——需要显式重试或续跑（`task.rerun`/`task.resume` 门控工具）。

## 5. 配额与错误码

### 5.1 相关配置（`RM_MCP_*`，默认值）

| 配置 | 默认 | 含义 |
|---|---|---|
| `RM_MCP_ENABLED` | `false` | 总开关；关闭时 `/mcp` 与未知路径字节相同 404 |
| `RM_MCP_TASK_BUDGET_S` | `900` | 单任务墙钟预算（30–3600） |
| `RM_MCP_TASK_POLL_S` | `3.0` | `result.json` 产物轮询间隔 |
| `RM_MCP_TASK_EVENT_MAX` | `200` | 每任务事件保留数（10–5000，最新一条恒保留） |
| `RM_MCP_TASK_HEARTBEAT_INTERVAL_S` / `_STALE_S` | `5` / `60` | 执行器心跳周期/失联判定 |
| `RM_MCP_MAX_ACTIVE_PER_USER` | `1` | 每用户同时在飞任务（1–100） |
| `RM_MCP_TASK_RETENTION_S` | `86400` | 任务行保留期（60–2592000 秒） |
| `RM_MCP_SUBMIT_PER_HOUR` | `12` | 每人每小时 `research.ask` 提交数；`0` 关闭限流 |
| `RM_MCP_SANDBOX_CNY_PER_MIN` | `0` | 沙箱分钟单价；`0` = 未定价 |
| `RM_MCP_DELIVERY_MAX_ATTEMPTS` / `_RETRY_DELAY_S` | `3` / `60` | 报告交付重试次数/指数退避基数 |

### 5.2 工具错误码（`tools/call` 结果 `isError:true` + 结构化 `{code, message, suggested_action, retryable}`）

| code | 含义 |
|---|---|
| `QUOTA_EXCEEDED` | 超过并发或每小时提交配额；带 `retry_after_s`（指向下一个 UTC 小时边界） |
| `TASK_NOT_FOUND` | 任务不存在或不属于你（跨租户不可探测） |
| `TASK_STALE` | 服务重启前遗留的 running 行，已收敛为失败 |
| `TASK_INTERNAL` | 执行器内部故障 |
| `SANDBOX_UNAVAILABLE` | 沙箱不可用（未创建/已失败） |
| `RESULT_TIMEOUT` | 任务超过墙钟预算 |
| `ARTIFACT_NOT_FOUND` | 产物不存在（用 `task.get` 交付清单里的 id 重试） |
| `ARTIFACT_NOT_RETRIEVABLE` | 该产物是沙箱指针（equity_csv/trades_csv/chart），不提供下载 |
| `ARTIFACT_PATH_REJECTED` | 产物路径越界（防御性拒绝） |
| `CAPABILITY_PENDING` | 回测试点：外部能力尚未验收，未开始任何运行 |

## 6. 经典使用示例

以下示例全部针对生产端点 `https://www.jetwind.net/mcp`，`$TOKEN` 为 API key 或会话 token。手工验证也可以用 `http://127.0.0.1:8900/mcp` 的本地服务。

### 6.1 完整流程：提交 → 轮询 → 读结果（curl）

```bash
BASE=https://www.jetwind.net/mcp
AUTH="Authorization: Bearer $TOKEN"
CT='content-type: application/json'

# 1) 握手（MCP 客户端自动做，手工只需一次）
curl -sS "$BASE" -H "$AUTH" -H "$CT" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
curl -sS "$BASE" -H "$AUTH" -H "$CT" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 2) 提交研究任务（立即返回 task_id，不阻塞）
curl -sS "$BASE" -H "$AUTH" -H "$CT" -d '{
  "jsonrpc":"2.0","id":2,"method":"tools/call",
  "params":{"name":"research.ask","arguments":{
    "question":"分析 300750.SZ 最近的基本面变化并给出投资结论",
    "universe":"300750.SZ","horizon":"6M","depth":"standard",
    "idempotency_key":"demo-20261111-1"
  }}
}'
# → {"task_id":"<32位hex>","status":"queued","plan":{...5步...},"progress":{...},
#    "eta_s":300,"thread_id":"","task_url":"/tasks/<task_id>"}

# 3) 轮询直到终态（间隔用返回的 poll_after_ms，这里示意 3 秒）
TASK_ID="<上一步的 task_id>"
for i in $(seq 1 60); do
  R=$(curl -sS "$BASE" -H "$AUTH" -H "$CT" -d "{
    \"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",
    \"params\":{\"name\":\"task.get\",\"arguments\":{\"task_id\":\"$TASK_ID\"}}
  }")
  echo "$R"
  echo "$R" | grep -q '"completed"\|"failed"\|"cancelled"' && break
  sleep 3
done

# 4) completed 后读结构化结果（默认内联，>50k 字符时给截断标记）
curl -sS "$BASE" -H "$AUTH" -H "$CT" -d "{
  \"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",
  \"params\":{\"name\":\"artifact.get\",\"arguments\":{\"task_id\":\"$TASK_ID\",\"kind\":\"result\"}}
}"
```

### 6.2 阻塞等待（适合脚本按步骤推进，不写轮询循环）

```bash
curl -sS "$BASE" -H "$AUTH" -H "$CT" -d "{
  \"jsonrpc\":\"2.0\",\"id\":5,\"method\":\"tools/call\",
  \"params\":{\"name\":\"task.wait\",\"arguments\":{\"task_id\":\"$TASK_ID\",\"timeout_ms\":20000}}
}"
# 最多阻塞 20 秒；返回 pending/终态视图。注意：断开连接不会取消任务。
```

### 6.3 读完整的 Markdown 报告（含分页续读）

```bash
# 第一页
curl -sS "$BASE" -H "$AUTH" -H "$CT" -d "{
  \"jsonrpc\":\"2.0\",\"id\":6,\"method\":\"tools/call\",
  \"params\":{\"name\":\"artifact.get\",\"arguments\":{\"task_id\":\"$TASK_ID\",\"kind\":\"report\"}}
}"
# 若返回 next_offset / cursor，用 cursor 读下一页：
curl -sS "$BASE" -H "$AUTH" -H "$CT" -d "{
  \"jsonrpc\":\"2.0\",\"id\":7,\"method\":\"tools/call\",
  \"params\":{\"name\":\"artifact.get\",\"arguments\":{\"cursor\":\"<上一页 cursor>\"}}
}"
```

### 6.4 取消一个正在跑的任务

```bash
curl -sS "$BASE" -H "$AUTH" -H "$CT" -d "{
  \"jsonrpc\":\"2.0\",\"id\":8,\"method\":\"tools/call\",
  \"params\":{\"name\":\"task.cancel\",\"arguments\":{\"task_id\":\"$TASK_ID\"}}
}"
# 幂等：已完成/已取消的任务原样返回；已产出的结果不删除。
```

### 6.5 幂等重放（网络重试不产生重复计算）

```bash
# 同一个 idempotency_key 再提交 → 返回第一次的 task_id，不新建任务
curl -sS "$BASE" -H "$AUTH" -H "$CT" -d '{
  "jsonrpc":"2.0","id":9,"method":"tools/call",
  "params":{"name":"research.ask","arguments":{
    "question":"分析 300750.SZ 最近的基本面变化并给出投资结论",
    "universe":"300750.SZ","horizon":"6M","depth":"standard",
    "idempotency_key":"demo-20261111-1"
  }}
}'
```

### 6.6 查询服务能力（提交前先读边界）

```bash
curl -sS "$BASE" -H "$AUTH" -H "$CT" -d '{
  "jsonrpc":"2.0","id":10,"method":"tools/call",
  "params":{"name":"service.info","arguments":{}}
}'
```

### 6.7 MCP 客户端配置（Qoder 系示意）

在客户端的 MCP server 配置里注册（不同客户端字段名略有差异；`Bearer ${VARIABLE}` 环境变量展开已验证可用）：

```json
{
  "mcpServers": {
    "acs-remote": {
      "url": "https://www.jetwind.net/mcp",
      "headers": { "Authorization": "Bearer ${ACS_TOKEN}" },
      "timeout": 30000
    }
  }
}
```

会话思想：**让 Agent 在自然语言里自己决定调工具**。实测（Qoder CLI 0.2.12）在只暴露 `research.ask`/`task.get`/`artifact.get` 三工具、工具名与描述为真实投研语义时，一句「帮我研究 300750.SZ 最近的基本面变化并给出结论」即可自主完成 提交 1 次 → 轮询 53 次 → 读产物 2 次 的闭环。教训：工具名与描述必须说人话（曾因探针命名导致模型拒绝调用并反问用户）。

## 7. 边界与注意事项

- **不知道的不能说**：QoderWorker/Codex/VS Code Copilot 的进度展示、重连、取消、下载均**未经验证**（矩阵无 `available`）。可靠路径是查询工具 + 私有任务页（`task_url`）；不要在客户端原生能力上做承诺。
- **取消远端停止未验证**：`task.cancel` 只保证控制面任务终态；沙箱远端是否确实停止无回退证据，高成本任务取消后按需人工确认。
- **任务行保留 24 小时**（`RM_MCP_TASK_RETENTION_S`），过期任务不认领、被 GC 删除；已归档产物（`delivery=delivered`）不受影响，仍可通过 `artifact.get` 读取。
- **费用**：沙箱单价未定价（0 = 未定价），`service.info` 会如实报告 `pricing: unpriced`；不要对外承诺费用估算。
- **每次提交都会消耗配额**：每小时 12 次、每用户同时 1 个在飞；`QUOTA_EXCEEDED` 携带 `retry_after_s`。
- **不要期望实时推送**：服务端不主动推送进度；进度靠客户端轮询/等待，或私有任务页查看。
- **这不是回测/交易入口**：`/mcp` 只有研究任务面；固定输入回测试点（`backtest.run`）在外部能力验收前恒为 `CAPABILITY_PENDING`；真实下单、自动发布策略不在范围内。

## 8. 与沙箱 / 外部仓库的关系

- `/mcp` 只暴露任务编排工具；**行情、财务、回测等能力在沙箱内部**（沙箱镜像内注册 101 个金融研究 MCP 工具 + 技能，见 [`https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/opencode-serve/README.md`](https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/opencode-serve/README.md)），由执行任务时的 Agent 按需调用。研究需求（标的、区间、假设）应写进 `question`，而不是期望 `/mcp` 直接给数据工具。
- 沙箱内的**策略调度工具**（`schedule_strategy` 等六个）转发到 RM 控制面（`/api/v1/agent/strategy-schedules`），与 `/mcp` 是两条不同的认证/路由面，不要在 `/mcp` 上找它们。
- 回测、停止确认、因子注册等三项能力依赖外部仓库交付（State 跟踪在 [`https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/vt-change-requests/INDEX.md`](https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/vt-change-requests/INDEX.md)，三门均 `pending`）；验收通过前对应工具不承诺可用。

## 9. 相关材料

- 协议与客户端契约实测：Gate 0（[`mcp-gate0-client-contract.md`](https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/workstreams/mcp/mcp-gate0-client-contract.md)）、Gate 1 异步闭环（[`mcp-gate1-async-contract.md`](https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/workstreams/mcp/mcp-gate1-async-contract.md)）、Gate 2 真实执行链（[`mcp-gate2-execution.md`](https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/workstreams/mcp/mcp-gate2-execution.md)）
- 客户端兼容矩阵与证据等级：[`stage2-client-compatibility.md`](https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/workstreams/mcp/stage2-client-compatibility.md)
- 工程与发布状态、后置门：[`https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/iterations/mcp-financial-delivery-stage-2/README.md`](https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/iterations/mcp-financial-delivery-stage-2/README.md)；最终人工验收入口：`https://github.com/shadowinlife/aliyun-acs-agent-service/blob/main/docs/operations/runbooks/mcp-stage2-final-acceptance.md`
- 需求全文：[Issue #8](https://github.com/shadowinlife/aliyun-acs-agent-service/issues/8)（36 条用户故事、阶段计划、测试接缝）
- 根 README API 表 `/mcp` 行与 `RM_MCP_*` 配置节（本文件的单一事实源之一）