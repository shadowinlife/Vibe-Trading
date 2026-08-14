# 01 · 会话 / 消息 / 研究目标 / 聊天 SSE 事件协议

> **文档族**: Vibe-Trading 前端集成知识库（`.omo/memory/frontend-integration/`）
> **读者**: 在 IM 插件视图层重建同类能力、只能消费 REST/SSE 面的前端团队
> **校对日期**: 2026-08-14 · **事实来源**: 逐行核对后端路由/服务/发射端与前端 TypeScript 契约
> **本篇职责**: `/sessions*` REST 端点族 + 聊天 SSE 事件流的字段级协议。
> 认证模型（API Key / SSE 票据）、错误包体、SSE 重连/退避机制等通用约定见 [./00-architecture-and-conventions.md](./00-architecture-and-conventions.md)，本篇不重复。

**事实来源文件**（本篇所有契约均逐条核对自以下源码）：

| 层 | 文件 |
|---|---|
| 路由（权威） | `agent/src/api/sessions_routes.py`（由 `agent/api_server.py` 经 `register_sessions_routes(app)` 挂载） |
| 会话服务/attempt 生命周期 | `agent/src/session/service.py`、`agent/src/session/models.py`、`agent/src/session/store.py` |
| SSE 事件总线 | `agent/src/session/events.py` |
| Agent 事件发射端 | `agent/src/agent/loop.py`、`agent/src/agent/progress.py` |
| Swarm 桥接发射端 | `agent/src/tools/swarm_tool.py`、`agent/src/swarm/models.py` |
| Goal 存储/枚举 | `agent/src/goal/models.py`、`agent/src/goal/store.py`、`agent/src/goal/context.py` |
| Live/Mandate 桥接 | `agent/src/api/live_routes.py`（`_emit_live_event`） |
| 前端 TypeScript 契约 | `frontend/src/lib/api.ts`、`frontend/src/types/agent.ts` |
| 前端消费端 | `frontend/src/hooks/useSSE.ts`、`frontend/src/stores/agent.ts`、`frontend/src/pages/Agent.tsx`、`frontend/src/lib/swarmStatus.ts` |

---

## 1. 能力概览

| 能力 | 后端承载 | 说明 |
|---|---|---|
| 会话管理（建/列/查/删/改名） | `POST/GET/DELETE/PATCH /sessions*` | 会话是多轮对话容器；删除会话同时清空其研究目标账本与 SSE 缓冲 |
| 聊天流式回复 | `POST /sessions/{id}/messages` + `GET /sessions/{id}/events`（SSE） | 发消息立即返回 `message_id`/`attempt_id`，模型输出经 SSE 流式推送 |
| 中断运行 | `POST /sessions/{id}/cancel` | 协作式取消；终态记为 `cancelled` 而非 `failed` |
| 自动标题 | `POST /sessions/{id}/title/auto` | LLM 摘要首轮对话为标题；绝不覆盖手动改名 |
| 研究目标（Goal） | `POST/GET/PATCH /sessions/{id}/goal`、`POST .../goal/evidence`、`PATCH .../goal/status` | 会话作用域的研究目标账本：目标/判据/证据/状态机 |
| 工具执行可视化 | SSE `tool_call`/`tool_result`/`tool_progress`/`tool_heartbeat` | 聊天内工具的调用、结果、进度、心跳 |
| 多智能体团队（Swarm）状态 | SSE `swarm.started`/`swarm.event` | 聊天内发起的 swarm 运行实时桥接到会话 SSE 流 |
| 实盘授权/动作通知 | SSE `mandate.*`/`live.*` | 由工具结果或 live 路由桥接；payload 细节见 07 篇 |

一个会话同一时刻只允许一个运行中的 attempt：重复 `POST /messages` 返回 **HTTP 409**（`agent/src/session/service.py` `SessionBusyError`）。

---

## 2. 端点清单

鉴权级别引用 [doc 00 §3.3](./00-architecture-and-conventions.md)：`require_auth` = Bearer 强制（配置 key 时）；`require_event_stream_auth` = Bearer 或一次性 SSE 票据。所有路径参数经 `_validate_path_param` 校验（畸形字符直接拒绝）。时间戳一律为时区感知 UTC ISO 字符串。

| 方法 | 路径 | 鉴权 | 请求 | 响应概要 |
|---|---|---|---|---|
| POST | `/sessions` | require_auth | `{title?: string, config?: object}` | `201` SessionItem |
| GET | `/sessions?limit=` | require_auth | `limit` 1–200，默认 50 | SessionItem[] |
| GET | `/sessions/{session_id}` | require_auth | — | SessionItem；不存在 `404` |
| DELETE | `/sessions/{session_id}` | require_auth | — | `{status:"deleted", session_id}`；连带删除目标账本 |
| PATCH | `/sessions/{session_id}` | require_auth | `{title?: string}` | `{status:"updated", session_id}` |
| POST | `/sessions/{session_id}/title/auto` | require_auth | — | `{status:"updated"\|"kept", session_id, title}` |
| POST | `/sessions/{session_id}/messages` | require_auth | `{content: string(1–5000)}` | `{message_id, attempt_id}`；忙 `409`；会话不存在 `404` |
| GET | `/sessions/{session_id}/messages?limit=` | require_auth | `limit` 1–1000，默认 100 | MessageItem[] |
| POST | `/sessions/{session_id}/cancel` | require_auth | — | `{status:"cancelled"\|"no_active_loop"}` |
| POST | `/sessions/{session_id}/goal` | require_auth | CreateGoalRequest | `201` GoalSnapshot |
| GET | `/sessions/{session_id}/goal` | require_auth | — | GoalSnapshot；无目标 `404` |
| PATCH | `/sessions/{session_id}/goal` | require_auth | UpdateGoalRequest | `{goal, snapshot}`；陈旧 `409` |
| POST | `/sessions/{session_id}/goal/evidence` | require_auth | AddGoalEvidenceRequest | `201` `{evidence, snapshot}`；陈旧 `409` |
| PATCH | `/sessions/{session_id}/goal/status` | require_auth | UpdateGoalStatusRequest | `{goal, snapshot}`；非法状态 `400`；陈旧 `409` |
| GET | `/sessions/{session_id}/events` | require_event_stream_auth | 查询参数见 §4.1 | SSE 流（`text/event-stream`） |

路由定义均在 `agent/src/api/sessions_routes.py`。补充细节：

- **POST /sessions**：认证 principal 记为会话 owner（共享 key / loopback 模式下 `attributable=False`，不代表真实身份，见 `agent/src/session/models.py` `Principal`）。`config` 可携带 `mcpServers` 等会话级覆盖。
- **POST /sessions/{id}/messages**：`content` 为自然语言策略/研究描述。成功时同步返回 `{message_id, attempt_id}`（`agent/src/session/service.py` `send_message`）；agent 循环在后台任务执行，输出全部走 SSE。会话已有运行中 attempt 时抛 `SessionBusyError` → `409`。
- **POST /sessions/{id}/cancel**：无活动循环时返回 `{status:"no_active_loop"}`（非错误）。
- **POST /sessions/{id}/title/auto**：仅当当前标题为空或仍等于创建时的首条用户消息前缀（前 50 字符）时才改写；否则返回 `{status:"kept"}`。标题截断至 40 字符；LLM 失败或空标题返回 `502`。
- **goal 子组**：`POST /goal` 会**替换**当前目标（`replace_goal`）；`PATCH /goal` 仅编辑 `objective`/`ui_summary`（须至少给一个，否则 `400`）。所有 goal 写操作要求 `goal_id` 与 `expected_goal_id` 一致，不一致 → `409 StaleGoalError`（乐观并发控制）。
- **GET /sessions/{id}/events**：详见 §4。

### 2.1 请求体字段明细

权威定义为 `agent/src/api/sessions_routes.py` 顶部的 Pydantic 模型；前端对应类型在 `frontend/src/lib/api.ts`。

**CreateSessionRequest**（`POST /sessions`）：

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `title` | string | 默认 `""` | 会话标题 |
| `config` | object \| null | 默认 `null` | 会话级配置；可含 `mcpServers` 等覆盖项 |

**UpdateSessionRequest**（`PATCH /sessions/{id}`）：仅 `title?: string`。写入时同步刷新 `updated_at`。

**SendMessageRequest**（`POST /sessions/{id}/messages`）：仅 `content: string`，`min_length=1`、`max_length=5000`。

**CreateGoalRequest**（`POST /sessions/{id}/goal`）：

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `objective` | string | 必填，1–5000 | 研究目标描述 |
| `criteria` | string[] | 默认 `[]` | 验收判据；逐条 `strip()` 去空后若为空，落回 `default_goal_criteria()`（3 条默认判据，`agent/src/goal/context.py`） |
| `ui_summary` | string | 默认 `""` | UI 简述 |
| `protocol` | string | 默认 `"thesis_review"` | 协议名 |
| `risk_tier` | string | 默认 `"research_general"` | 见 §5.4；非法值或 `live_trading_or_execution` → `400` |
| `token_budget` / `turn_budget` / `time_budget_seconds` | number \| null | 默认 `null`，`ge=1` | 预算上限 |

**UpdateGoalRequest**（`PATCH /sessions/{id}/goal`）：`goal_id`（必填）、`expected_goal_id`（必填）、`objective?`（1–5000）、`ui_summary?`（≤500）。`objective` 与 `ui_summary` 至少给一个，否则 `400`。

**AddGoalEvidenceRequest**（`POST /sessions/{id}/goal/evidence`）：必填 `goal_id`、`expected_goal_id`、`text`（1–10000）；可选 `criterion_id`、`claim_id`、`evidence_type`（默认 `"evidence"`）、`tool_call_id`、`run_id`、`source_provider`、`source_type`、`source_uri`、`symbol_universe[]`、`benchmark[]`、`timeframe`、`method`、`assumptions{}`、`artifact_path`、`artifact_hash`、`data_as_of`、`confidence`、`caveat`、`contradicts_claim_ids[]`。引用未知 `criterion_id`/`claim_id` → `400`（`agent/src/goal/store.py` `append_evidence`）。

**UpdateGoalStatusRequest**（`PATCH /sessions/{id}/goal/status`）：必填 `goal_id`、`expected_goal_id`、`status`（§5.3 的 12 值之一，非法 → `400`）；可选 `audit: GoalAuditRowRequest[]`、`recap?: string \| null`。

**GoalAuditRowRequest**：`criterion_id`（必填）、`result`（必填）、`evidence_ids: string[]`（默认 `[]`）、`notes: string`（默认 `""`）。完成类状态变更应携带按判据的审计行。

---

## 3. 数据契约

TypeScript 权威定义在 `frontend/src/lib/api.ts`（`SessionItem` 673–680、Goal 族 684–858、`MessageItem`/`ToolTrailItem` 1169–1188）；后端 Pydantic 模型在 `agent/src/api/sessions_routes.py`（`SessionResponse`/`MessageResponse`/`GoalSnapshotResponse` 等）与 `agent/src/goal/models.py`。二者已双向核对一致。

### 3.1 SessionItem

来源：`sessions_routes.py` `SessionResponse` ↔ `api.ts` `SessionItem`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | 会话唯一 ID（uuid4 前 12 位十六进制） |
| `title` | string | 会话标题；可为空串 |
| `status` | string | 会话状态，见 §5.2 |
| `created_at` | string | 创建时间（UTC ISO） |
| `updated_at` | string | 最后更新时间（UTC ISO） |
| `last_attempt_id` | string \| null | 最近一次 attempt 的 ID |

### 3.2 MessageItem

来源：`sessions_routes.py` `MessageResponse` ↔ `api.ts` `MessageItem`；持久化模型 `agent/src/session/models.py` `Message`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `message_id` | string | 消息唯一 ID |
| `session_id` | string | 所属会话 |
| `role` | string | `"user"` / `"assistant"`（REST 可见的两种） |
| `content` | string | 消息文本 |
| `created_at` | string | UTC ISO |
| `linked_attempt_id` | string \| null | assistant 回复关联的 attempt ID |
| `metadata` | object \| null | assistant 回复的运行元数据，见下 |
| `tool_trail` | ToolTrailItem[] | 已完成工具调用的紧凑轨迹（仅 `completed` attempt 有值，否则空数组） |

assistant 回复的 `metadata` 字段（`agent/src/session/service.py` `_run_attempt` 构造的 `reply_metadata`）：

| 键 | 类型 | 出现条件 |
|---|---|---|
| `run_id` | string | 产生了 run_dir 时（回测/运行目录名） |
| `status` | string | attempt 终态（`completed`/`failed`/`cancelled`） |
| `metrics` | object | 加载到回测指标时 |
| `elapsed_ms` | number | 恒有（运行耗时毫秒） |
| `provider` / `configured_model` / `model` / `model_source` / `reasoning_effort` | string | 运行时身份，存在时才输出 |

### 3.3 ToolTrailItem

来源：`api.ts` `ToolTrailItem` ↔ `agent/src/session/service.py` `_record_tool_trail_event`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `tool` | string | 工具名 |
| `status` | `"running"` \| `"ok"` \| `"error"` | `tool_call` 写入 `running`，对应 `tool_result` 更新为 `ok`/`error` |
| `arguments` | Record<string,string> \| undefined | 已脱敏、每值截断 200 字符的参数 |
| `elapsed_ms` | number \| undefined | 结果到达时填入 |
| `preview` | string \| undefined | 结果预览（脱敏后前 200 字符） |
| `call_id` | string \| undefined | 工具调用 ID，用于 `tool_call`↔`tool_result` 配对 |
| `timestamp` | number \| undefined | epoch 毫秒 |

### 3.4 sendMessage 响应

`POST /sessions/{id}/messages` 成功返回（`api.ts` `sendMessage` 类型）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `message_id` | string | 用户消息 ID |
| `attempt_id` | string | 本次执行 attempt ID（后续 SSE 事件均携带） |

### 3.5 GoalSnapshot 及子结构

`GoalSnapshot`（`api.ts` 779–785 ↔ `sessions_routes.py` `GoalSnapshotResponse` ↔ `agent/src/goal/store.py` `get_goal_snapshot`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `goal` | GoalRecord | 目标主记录 |
| `claims` | GoalClaim[] | 研究主张列表 |
| `criteria` | GoalCriterion[] | 验收判据列表 |
| `evidence` | GoalEvidence[] | 证据行（快照默认上限 50 条） |
| `evidence_count` | number | 证据总数（不受 50 上限影响） |

**GoalRecord**（`api.ts` 703–723 ↔ `agent/src/goal/models.py` `GoalRecord`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `goal_id` | string | 目标 ID（同时作为后续写操作的 `expected_goal_id`） |
| `session_id` | string | 所属会话 |
| `status` | GoalStatus | 见 §5.3 |
| `objective` | string | 研究目标描述 |
| `ui_summary` | string | UI 简述 |
| `source` | string | 创建来源（REST 为 `"api"`） |
| `protocol` | string | 协议名，默认 `"thesis_review"` |
| `risk_tier` | GoalRiskTier | 见 §5.4 |
| `token_budget` / `turn_budget` / `time_budget_seconds` | number \| null | 预算（可选） |
| `tokens_used` / `turns_used` / `time_used_seconds` | number | 已用量 |
| `budget_wrapup_sent` | boolean | 是否已发预算收尾提示 |
| `created_at` / `updated_at` | string | UTC ISO |
| `completed_at` | string \| null | 完成时间 |
| `recap` | string \| null | 状态收尾摘要 |

**GoalClaim**（`api.ts` 725–734）：`claim_id` / `goal_id` / `session_id` / `claim_type` / `text` / `status` / `created_at` / `updated_at`。

**GoalCriterion**（`api.ts` 736–747）：`criterion_id` / `goal_id` / `session_id` / `text` / `required`(bool) / `status`（默认 `"pending"`）/ `freshness_requirement`(string\|null) / `protocol_step`(string\|null) / `created_at` / `updated_at`。

**GoalEvidence**（`api.ts` 749–777 ↔ `agent/src/goal/models.py` `EvidenceRecord`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `evidence_id` | string | 证据 ID |
| `goal_id` / `session_id` | string | 归属 |
| `text` | string | 证据正文 |
| `criterion_id` / `claim_id` | string \| null | 关联判据/主张 |
| `evidence_type` | string | 默认 `"evidence"` |
| `tool_call_id` / `run_id` | string \| null | 溯源到工具调用/回测运行 |
| `source_provider` / `source_type` / `source_uri` | string \| null | 数据源 |
| `symbol_universe` / `benchmark` | string[] | 标的池/基准 |
| `timeframe` / `method` | string \| null | 时间范围/方法 |
| `assumptions` | object | 假设 |
| `artifact_path` / `artifact_hash` | string \| null | 产物路径/sha256 |
| `retrieved_at` | string | 检索时间 |
| `data_as_of` | string \| null | 数据时点 |
| `freshness_status` | string | 有 `data_as_of` 为 `"fresh"`，否则 `"unknown"` |
| `verification_status` | string | 默认 `"unverified"` |
| `confidence` / `caveat` | string \| null | 置信度/注意事项 |
| `contradicts_claim_ids` | string[] | 与之矛盾的 claim |
| `created_at` | string | UTC ISO |

goal 写操作请求体（`CreateGoalRequest`/`UpdateGoalRequest`/`AddGoalEvidenceRequest`/`UpdateGoalStatusRequest`/`GoalAuditRowRequest`）字段与上表一一对应，权威定义在 `api.ts` 787–858 与 `sessions_routes.py` 同名 Pydantic 模型；`objective` 上限 5000 字符、`evidence.text` 上限 10000 字符、`ui_summary` 编辑上限 500 字符。

---

## 4. SSE 事件协议（核心）

### 4.1 流 URL、续传与回放

流地址：`GET /sessions/{session_id}/events`（`agent/src/api/sessions_routes.py` `session_events`）。响应头 `Cache-Control: no-cache`、`Connection: keep-alive`、`X-Accel-Buffering: no`。

- **续传游标**：`Last-Event-ID`。后端**同时**接受 HTTP 头 `Last-Event-ID` 与查询参数 `Last-Event-ID`（`Query(None, alias="Last-Event-ID")`，头部优先）。参考实现重连时以**查询参数**形式携带（`frontend/src/hooks/useSSE.ts` `buildUrl`）。
- **`?replay=active` 语义**：当未携带 `Last-Event-ID` 且会话最近一次 attempt 仍为 `running` 时，置 `replay_all=True`，从事件缓冲**头部**整体回放，用于页面刷新/切换后对进行中运行的"补水"（hydration）。已完成的历史不走回放，走 `GET /messages` REST。
- **事件缓冲**：`EventBus` 每会话保留最近 **500** 条事件（`agent/src/session/events.py` `max_buffer_size=500`）。若携带的 `Last-Event-ID` 已不在缓冲内且未 `replay_all`，则不回放任何事件（其间事件丢失，消费端应以 REST 兜底）。
- **帧格式**：`id: <event_id>` / `event: <event_type>` / `data: <JSON>`。`event_id` 为 uuid4 前 16 位十六进制，单调由后端分配，是去重与续传的基础。心跳帧无 `id`（见 §4.8）。
- **票据**：浏览器 EventSource 无法带 Authorization 头，须先 `POST /auth/sse-ticket` 换取一次性票据再以 `?ticket=` 连接；每次连接/重连都要重新 mint。详见 [doc 00 §3.2](./00-architecture-and-conventions.md)。

### 4.2 载荷通用约定

所有业务事件 `data` 为 JSON 对象；解析失败参考实现降级为 `{raw: <原文>}`。凡由 agent 循环发出的事件，`agent/src/session/service.py` 的 `event_callback` 都会**注入 `attempt_id` 字段**（`data["attempt_id"] = attempt_id`），下表不再逐条重复该字段。

一次典型用户回合的事件顺序（`agent/src/session/service.py` `_run_attempt` + `agent/src/agent/loop.py`）：

```
POST /messages → HTTP {message_id, attempt_id}
SSE: message.received → attempt.created → attempt.started
     → [reasoning_delta* / text_delta* / tool_call → (tool_heartbeat|tool_progress)* → tool_result]*
     → thinking_done* / llm_usage* / compact? / goal.updated?
     → attempt.completed | attempt.failed | attempt.cancelled
```

**断线重连 / 页面切换回来的补水序列**（参考实现 `frontend/src/pages/Agent.tsx`）：

1. 以 `GET /sessions/{id}/messages` 重建已完成历史（含 `tool_trail`）。
2. 若会话最近 attempt 仍在运行，用 `GET /sessions/{id}/events?replay=active`（无 `Last-Event-ID`）连接：后端检测到末次 attempt 为 `running` 时整体回放缓冲，前端据此重建活动对象与工具进度。
3. 正常断线重连则携带 `Last-Event-ID` 增量续传；回放与实时流可能重叠投递，按事件 `id` 去重（§6 第 1 条）。

**发射端可调参数**（`agent/src/config/env_schema.py`，服务端环境变量，供理解事件节奏）：

| 参数 | 默认 | 影响的事件 |
|---|---|---|
| `VT_HEARTBEAT_INTERVAL_S` | 3.0 | `tool_heartbeat` 节奏 |
| `VT_REASONING_DELTA_MIN_INTERVAL_S` | 1.0 | `reasoning_delta` 节流间隔 |
| `VIBE_TRADING_TOOL_TIMEOUT_SECONDS` | 1800.0 | 只读工具超时 → `tool_progress(stage="timeout")` |
| `TOKEN_THRESHOLD` | 40000 | 触发 `compact` 的上下文压缩阈值 |

### 4.3 聊天流事件

| 事件 | payload 字段 | 触发时机 | 发射端 |
|---|---|---|---|
| `text_delta` | `delta`: string（增量文本）；`iter`: number | 模型正文流式增量；grounding 缓冲模式下改为一次性整段输出 | `loop.py:917/1130` |
| `reasoning_delta` | `iter`: number；`chars`: number（累计推理字符）；`tail`: string（滚动尾窗，服务端限长，末 600 字符；缓冲模式下省略） | 推理/思考增量，按 `VT_REASONING_DELTA_MIN_INTERVAL_S`（默认 1s）节流 | `loop.py:938–944` |
| `stream_reset` | `iter`；`reason: "provider_stream_retry"`；`provider`；`model` | 提供商流式中断、即将重试一次前；此前增量作废 | `loop.py:973–981` |
| `thinking_done` | `iter`；`content`: string（截断 500 字符） | 某一迭代思考文本收集完成 | `loop.py:1051–1054` |
| `compact` | `tokens_before`: number；`summary`: string（截断 200 字符） | 上下文自动压缩（token 超阈值）后 | `loop.py:2040` |
| `llm_usage` | `input_tokens` / `output_tokens` / `total_tokens`: number；`iter` | 每次 LLM 调用后上报提供商口径用量 | `loop.py:1008–1015` |

### 4.4 工具事件

| 事件 | payload 字段 | 触发时机 | 发射端 |
|---|---|---|---|
| `tool_call` | `tool`: string；`arguments`: Record<string,string>（脱敏、每值截 200）；`iter`；`call_id`；`blocked`?: true（身份门拦截时） | 工具开始执行 | `loop.py:1497/1603/1658` |
| `tool_result` | `tool`；`status`: `"ok"`\|`"error"`；`elapsed_ms`: number；`preview`: string（脱敏后前 200）；`call_id` | 工具执行结束 | `loop.py:1924–1933` |
| `tool_progress` | `tool`；`call_id`；`stage`: string；`current`?: number；`total`?: number；`message`: string；`elapsed_s`: number；`ts`: number | 长任务结构化工具进度；超时变体 `stage` 为 `"timeout"`/`"timeout_warning"`（附 `readonly`?） | `loop.py:1701–1707/1740–1761`、`progress.py` `ProgressEvent` |
| `tool_heartbeat` | `tool`；`elapsed_s`: number；`call_id` | 工具运行期间每 `VT_HEARTBEAT_INTERVAL_S`（默认 3s）心跳 | `loop.py:1709–1713`、`progress.py` `HeartbeatTimer` |

`tool_progress` 的 `current`/`total` 同时存在且 `total>0` 表示确定性进度（可渲染进度条）；`tool_call`/`tool_result` 用 `call_id` 配对。只读工具可并行执行，多个工具的进度/心跳事件会交错到达，消费端须按 `call_id` 归组。

### 4.5 Attempt / 会话生命周期事件

| 事件 | payload 字段 | 触发时机 | 发射端 |
|---|---|---|---|
| `session.created` | `session_id`；`title` | 创建会话 | `service.py:142` |
| `message.received` | `message_id`；`role`；`content` | 用户消息落库 | `service.py:199` |
| `attempt.created` | `attempt_id`；`prompt` | attempt 创建 | `service.py:210` |
| `attempt.started` | `attempt_id` | attempt 进入运行 | `service.py:260` |
| `attempt.completed` | `attempt_id`；`status`；`summary`；`error`；`run_dir`；`elapsed_ms`；`provider`?/`configured_model`?/`model`?/`model_source`?/`reasoning_effort`? | 成功终态 | `service.py:317–323` |
| `attempt.failed` | 同上（异常路径仅 `attempt_id`+`error`） | 失败终态 | `service.py:317–323/340` |
| `attempt.cancelled` | 同上（`CancelledError` 路径仅 `attempt_id`+`status`） | 用户取消终态 | `service.py:317–323/331–335` |

终态三事件由 `_TERMINAL_EVENTS` 映射（`service.py`），`summary` 为最终回复文本，`run_dir` 为运行目录路径（前端取末段作 `run_id`）。**用户取消是独立终态**，不复用 `attempt.failed`。

### 4.6 研究目标事件

| 事件 | payload 字段 | 触发时机 | 发射端 |
|---|---|---|---|
| `goal.created` | `goal`: GoalRecord | `POST /goal` 成功 | `sessions_routes.py:448` |
| `goal.evidence` | `evidence`: GoalEvidence；`goal_id` | `POST /goal/evidence` 成功 | `sessions_routes.py:549–553` |
| `goal.updated` | `goal`: GoalRecord；`snapshot`: GoalSnapshot | `PATCH /goal`、`PATCH /goal/status` 成功；或 agent 循环内目标用量记账后 | `sessions_routes.py:496/598`、`loop.py:1035–1038` |

参考实现收到 `goal.created`/`goal.evidence` 后重新 `GET /goal` 拉快照；`goal.updated` 直接消费携带的 `snapshot`，并在 `status` 进入终态时关闭目标面板（`Agent.tsx` `isTerminalGoalStatus`：`complete`/`cancelled`/`blocked`/`superseded`/`usage_limited`）。

### 4.7 Swarm 桥接事件

聊天内 agent 调用 `run_swarm` 工具时，`agent/src/tools/swarm_tool.py` 把 swarm 运行状态桥接进会话 SSE 流（不改变 `/swarm/runs` API）：

| 事件 | payload 字段 | 说明 |
|---|---|---|
| `swarm.started` | `run_id`；`preset`；`variables`；`status`；`agents`: SwarmAgentSpec[]；`tasks`: SwarmTask[] | 运行启动（`swarm_tool.py:816–826`） |
| `swarm.event` | `run_id`；`event`: SwarmEvent | 运行期间逐事件转发（`swarm_tool.py:786–789/828–831`） |

内嵌 `SwarmEvent`（`agent/src/swarm/models.py`）：`type`: string；`agent_id`?: string；`task_id`?: string；`data`: object；`timestamp`: string。前端 `applySwarmEvent`（`frontend/src/lib/swarmStatus.ts`）消费的 `type` 值：`run_started` / `layer_started` / `task_started` / `worker_started` / `tool_call` / `tool_result` / `task_heartbeat` / `worker_text` / `task_completed` / `worker_completed` / `task_failed` / `worker_failed` / `worker_timeout` / `worker_incomplete` / `task_blocked` / `task_retry` / `run_completed` / `run_error`。任务状态枚举 `pending`/`blocked`/`in_progress`/`completed`/`failed`/`cancelled`，运行状态 `pending`/`running`/`completed`/`failed`/`cancelled`。

### 4.8 Mandate / Live 桥接事件

实盘授权与动作事件也经本流送达。`sessions_routes.py` 在转发每个 `tool_result` 后额外尝试两个桥接器：

- `_mandate_proposal_frame_from_tool_result`：当 `tool_result` 的 `tool` 为 `propose_mandate_profiles` 且 `status=="ok"`，从 `preview` 提取 `proposal_id`（`mp_` + 32 位十六进制），从 `live_root()/*/proposals/{id}.json` 重新载入完整提案，发出 **`mandate.proposal`** 帧（`sessions_routes.py:210–231`）。
- `_live_action_frame_from_tool_result`：当 `tool_result.preview` 含 `"live_action"`，提取 `audit_id`（`la_` 前缀），从 `live_root()/audit.jsonl` 载入脱敏动作记录，发出 **`live.action`** 帧（`sessions_routes.py:259–281`）。

另一路：`agent/src/api/live_routes.py` 的 `_emit_live_event` 直接把以下事件写入会话事件总线（`live_routes.py:340–354/677–721`）：

| 事件 | payload（概要） | 触发 |
|---|---|---|
| `mandate.committed` | commit_mandate 结果对象 | `POST /mandate/commit` 成功 |
| `live.halted` | `{halted:true, broker, reason, sentinel}` | `POST /live/halt` |
| `live.resumed` | `{halted:false, broker, cleared}` | `POST /live/resume` |
| `live.action` | 动作记录（如 `{kind:"mandate_committed"\|"halt_tripped"\|"halt_cleared", broker, ...}`） | 上述各端点及 live runner 审计 |

**以上 mandate/live 事件的字段级定义见 [07-live-trading-runtime.md](./07-live-trading-runtime.md)**，本篇仅说明其在本流的桥接存在与触发条件。

### 4.9 传输控制事件

| 事件 | payload | 说明 |
|---|---|---|
| `heartbeat` | `{ts: number}` | 订阅循环空闲 30s 触发（`events.py:217–225`）；**无 `id`**，不推进 `lastEventId`，不打断回放；仅保活，勿当业务事件渲染 |
| `done` | — | 会话流**不发射**此事件；参考实现订阅清单（`useSSE.ts` `knownTypes`）含它仅为与其他 SSE 流（如 `/alpha` bench 流）保持一致。会话流的终态信号是 `attempt.*` |

另有内部哨兵 `session_cleared`（`events.py:236–264`）：删除会话时通知订阅者退出循环，客户端表现为连接关闭，非业务事件。`mcp.warning`（`service.py:394`，MCP 服务名冲突告警）可能被发射但不在参考实现订阅清单内，消费端可忽略。

---

## 5. 枚举与状态机

### 5.1 Attempt 生命周期

`AttemptStatus`（`agent/src/session/models.py:129–137`）：`pending` / `running` / `waiting_user` / `completed` / `failed` / `cancelled`。

```
pending ──mark_running──▶ running ──┬─ mark_completed ─▶ completed
                                    ├─ mark_failed ─────▶ failed
                                    └─ mark_cancelled ──▶ cancelled
```

- 创建即 `pending`，`_run_attempt` 开头转 `running` 并发 `attempt.started`。
- `waiting_user` 在枚举中定义，但当前会话服务路径未写入（源码未明确其激活路径）；消费端应容忍收到该值。
- **一次一个 attempt**：运行中再发消息 → `SessionBusyError` → **HTTP 409**（`service.py` `_reserve_session`）。
- **用户取消是独立终态**：`mark_cancelled` 与 `failed` 区分，避免把协作取消误报为故障（`models.py:327–341`、`service.py:271–275`）。对应 SSE 为 `attempt.cancelled`。
- 终态后 `completed_at` 落盘，`error` 在 `failed`/`cancelled` 时携带原因。
- **cancel 端点的内部语义**（`service.py` `cancel_current`）：AgentLoop 已构建时对其发协作式取消信号（当前步骤结束后停止，中途流式增量被丢弃）；运行还停留在注册表构建阶段时直接取消后台任务。两种路径最终都发 `attempt.cancelled` 并释放会话占用，后续 `POST /messages` 不再 409。取消无活动循环时端点返回 `{status:"no_active_loop"}`。

### 5.2 会话状态

`SessionStatus`（`agent/src/session/models.py:121–126`）：`active` / `completed` / `archived`。REST 创建的会话恒为 `active`；服务层当前不主动迁移到 `completed`/`archived`（源码未明确迁移路径），消费端应按字符串容忍处理。

### 5.3 研究目标状态（12 值）

`GoalStatus`（`agent/src/goal/models.py:10–24`），与 `api.ts` `GoalStatus`（684–696）一致：

```
active  paused  waiting_user  needs_refresh  insufficient_evidence
compliance_blocked  blocked  budget_limited  usage_limited
complete  cancelled  superseded
```

可继续推进的子集（agent 自动续跑判定，`agent/src/goal/context.py` `CONTINUABLE_GOAL_STATUSES`）：`active` / `needs_refresh` / `insufficient_evidence`。参考实现视为终态（关闭目标面板）的子集：`complete` / `cancelled` / `blocked` / `superseded` / `usage_limited`（`Agent.tsx` `isTerminalGoalStatus`）。判据 `status` 默认 `pending`，视为"未满足"的取值集合见 `context.py` `OPEN_CRITERION_STATUSES`：`""` / `pending` / `open` / `unsatisfied` / `missing` / `stale` / `too_weak`。

状态机的三条后端规则（`agent/src/goal/store.py`）：

1. **替换即作废**：`POST /goal`（`replace_goal`）先把该会话所有处于"当前"状态集合（`active` / `paused` / `waiting_user` / `needs_refresh` / `insufficient_evidence` / `compliance_blocked` / `budget_limited`）的旧目标置为 `superseded`，再创建新的 `active` 目标（`store.py:320–341`）。
2. **完成必须审计**：`PATCH /goal/status` 提交 `status="complete"` 时强制校验审计行（`_validate_completion_audit`）——需覆盖必填判据且结果为完成类取值（`satisfied` / `satisfied_with_caveat` / `not_applicable_user_accepted`，`store.py` `_COMPLETION_RESULTS`），否则 `400`。审计行的 `result` 会回写对应判据的 `status`。
3. **终态盖戳**：进入 `complete` / `blocked` / `cancelled` / `superseded` / `usage_limited` 时写入 `completed_at`（`store.py:720–726`）。

此外 `objective` 与每条 `criteria` 都会被 `reject_live_execution_objective` 检查，含实盘执行意图的文本直接 `400`（`store.py:297–304`）。

### 5.4 GoalRiskTier

前端契约 `api.ts` `GoalRiskTier`（698–701）为 **3 值**：

```
research_general  market_specific_short_term  personalized_advice_or_position_sizing
```

后端 `RiskTier` 枚举另有第 4 值 `live_trading_or_execution`（`agent/src/goal/models.py:33`），但 `POST /goal` 显式拒绝它（`sessions_routes.py:426–427` → HTTP 400 `"live trading or execution goals are not supported"`）。非法 `risk_tier` 同样 400。消费端只需实现 3 值。

---

## 6. 注意事项与校验要求

1. **事件去重/幂等**：重连会回放（`Last-Event-ID` 续传或 `replay=active` 全量），同一事件可能重复投递。消费端必须按事件 `id` 去重并对业务处理保持幂等（参考实现用 500 容量 LRU 集合，`useSSE.ts` `trackEventId`）。
2. **`Last-Event-ID` 走查询参数**：重连时拼进 URL（`?Last-Event-ID=<id>`）。注意它不是 SSE 规范里的自动 HTTP 头续传——浏览器 EventSource 原生只发头，参考实现因此显式把游标拼到 URL 上；后端两种形式都接受，同名 HTTP 头优先于查询参数。
3. **JSON 解析降级**：`data` 解析失败时降级为 `{raw: <原文>}`，不要因单条畸形事件中断整条流。
4. **空内容处理**：
   - `attempt.completed` 的 `summary` 可能为空——此时服务层以既有兜底文案落 assistant 消息（`service.py` `_format_result_message`），前端应回退到已累积的流式文本。
   - grounding 缓冲模式下不出 `text_delta` 增量，正文在结尾一次性给出；`thinking_done.content` 截断 500、`compact.summary` 与 `tool_result.preview` 截断 200，均为展示用预览，非完整内容。
   - `reasoning_delta.tail` 是**滚动尾窗**（服务端限长），消费端应整体替换而非追加。
5. **SSE 票据每连接一张**：票据一次性、TTL 60s，每次连接/重连都要重新 mint，勿缓存带票据的 URL（见 [doc 00 §3.2](./00-architecture-and-conventions.md)）。
6. **缓冲窗口有限**：每会话仅缓冲最近 500 条事件；`Last-Event-ID` 越窗且未 `replay_all` 时不回放。长时间断线后应以 `GET /messages` 重建历史。
7. **`heartbeat` 无 id**：不推进续传游标，勿用于去重或业务渲染。
8. **409 的两种来源**：发消息时 `409` = 会话忙（已有运行中 attempt）；goal 写操作时 `409` = `goal_id` 与 `expected_goal_id` 不一致（陈旧目标）。两者语义不同，需分别处理。
9. **路径参数编码**：session/goal ID 应 `encodeURIComponent`；后端对含换行等畸形字符的路径参数直接拒绝。
10. **shell 工具**：HTTP/SSE 面默认不暴露 `bash`/`background_run` 等 shell 工具（需服务端显式开启），不应期待聊天中出现 shell 类工具事件（见 doc 00 §6）。

---

## 7. 参考实现映射

| 前端文件 | 消费的协议面 |
|---|---|
| `frontend/src/lib/api.ts` | 全部 `/sessions*` REST 客户端 + TypeScript 数据契约（SessionItem/MessageItem/ToolTrailItem/Goal 族）；`sseUrl()` 生成裸流 URL（票据在连接时现 mint） |
| `frontend/src/hooks/useSSE.ts` | SSE 连接管理：重连退避、LRU 去重、`Last-Event-ID` 续传、`knownTypes` 事件订阅清单 |
| `frontend/src/stores/agent.ts` | 聊天状态存储：消息、流式文本、`reasoningTail`、工具调用、活动对象、swarm 状态、会话缓存 |
| `frontend/src/pages/Agent.tsx` | 事件处理器接线（`connect(api.sseUrl(sid,{replay:"active"}), {...})`）：逐事件消费、RunCompleteCard 触发、首轮完成后调 `title/auto`、goal 面板开合 |
| `frontend/src/lib/swarmStatus.ts` | `buildSwarmStatusFromStarted` / `applySwarmEvent` / `buildSwarmStatusFromToolResultPreview`：把 `swarm.started`/`swarm.event` 折叠成每 agent 的展示状态 |
| `frontend/src/components/chat/` | 渲染端：`ToolProgressIndicator`（工具进度/心跳）、`RunCompleteCard`（运行完成卡）、`SwarmStatusCard`/`SwarmDashboard`（swarm 状态卡）、`MandateProposalCard`（mandate 提案卡）、`GoalPanel`（目标面板）、`LiveRuntimePanel`/`RunnerStatus`（实盘运行面板）、`ConversationTimeline`/`MessageBubble`/`ActivityLine`/`ThinkingTimeline`（时间线与气泡） |

**事件 → 参考实现 UI 效果映射**（供 IM 视图层对齐交互预期；渲染方式非协议约束）：

| SSE 事件 | 参考实现效果 |
|---|---|
| `text_delta` | 追加流式正文气泡 |
| `reasoning_delta` | 更新"Reasoning…"低语行（`tail` 整体替换） |
| `stream_reset` | 清空当前流式视图并等待重试输出 |
| `thinking_done` | 保活，不冲刷流式文本 |
| `tool_call` / `tool_result` | 活动对象中新增/收口工具步骤（`call_id` 配对） |
| `tool_progress` / `tool_heartbeat` | 更新工具步骤的进度条/已耗时（rAF 合并渲染） |
| `compact` | 仅保活（无可见 UI） |
| `llm_usage` | 记录用量（调试面板） |
| `swarm.started` / `swarm.event` | 时间线插入/更新 swarm 状态卡（每 agent 一行状态） |
| `attempt.completed` | 冻结活动对象、落最终回答；有 `run_dir` 时拉取 run 详情渲染 RunCompleteCard；首轮完成后触发 `title/auto` |
| `attempt.failed` | 落错误气泡，活动对象置 `failed` |
| `attempt.cancelled` | 活动对象置 `stopped`，无错误气泡 |
| `goal.created` / `goal.evidence` / `goal.updated` | 刷新/开合 GoalPanel |
| `mandate.proposal` / `mandate.committed` | 时间线插入 MandateProposalCard 并标记已提交 |
| `live.halted` / `live.resumed` / `live.action` | 更新实盘运行面板与提示横幅 |
| `heartbeat` | 无 UI |

**对 IM 插件的含义**：上述组件是参考实现的呈现方式，非协议约束。贵方只需忠实实现 §2 端点 + §4 事件协议 + §5 状态机，即可在 IM 视图层重建会话、流式聊天、工具进度、swarm 状态卡、研究目标与实盘通知等同能力；mandate/live 事件的完整字段见 [07 篇](./07-live-trading-runtime.md)，认证/错误/SSE 传输通用约定见 [00 篇](./00-architecture-and-conventions.md)。
