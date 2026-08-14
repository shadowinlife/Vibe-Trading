# 04 · Swarm 多智能体团队 API 族

> **文档族**: Vibe-Trading 前端集成知识库（`.omo/memory/frontend-integration/`）
> **读者**: 在 IM 插件视图层重建 Swarm 仪表盘的前端团队
> **校对日期**: 2026-08-14 · **事实来源**: 直接引自代码，路径见各节
> **本篇职责**: `/swarm/*` 端点族 —— 团队预设清单、运行的创建/列表/详情/取消/重试、SSE 事件流，以及聊天时间线里的 `swarm.started` / `swarm.event` 桥接事件。认证、错误包体、SSE 票据等通用约定见 [./00-architecture-and-conventions.md](./00-architecture-and-conventions.md)，本篇不再重复。

**事实来源文件**：

| 文件 | 职责 |
|---|---|
| `agent/src/api/swarm_routes.py` | 全部 7 个端点 + SSE 发射循环 |
| `agent/src/swarm/models.py` | `RunStatus` / `TaskStatus` / `SwarmEvent` / `SwarmRun` / `SwarmTask` / `SwarmAgentSpec` 数据模型 |
| `agent/src/swarm/store.py` | 持久化、`reconcile_run`（孤儿 running 收敛）、`is_run_stale`、`read_events` |
| `agent/src/swarm/runtime.py` | DAG 执行器 + 运行级事件发射（`run_started`/`layer_started`/…） |
| `agent/src/swarm/worker.py` | worker 级事件发射（`worker_*`/`tool_call`/`task_heartbeat`/…） |
| `agent/src/swarm/serialization.py` | `serialize_task`（任务公开投影） |
| `agent/src/swarm/presets.py` | 预设加载/列表（含用户预设目录） |
| `agent/src/tools/swarm_tool.py` | 聊天内 `run_swarm` 工具 → `swarm.started` / `swarm.event` 桥接 |
| `frontend/src/lib/api.ts` | TypeScript 契约（`SwarmPreset`/`SwarmRunSummary`，约 323–340 行）+ REST 客户端（约 191–203 行） |
| `frontend/src/lib/swarmStatus.ts`、`frontend/src/components/chat/SwarmStatusCard.tsx`、`frontend/src/components/chat/SwarmDashboard.tsx` | 聊天内 swarm 状态卡（事件 → 显示状态映射） |

---

## 1. 能力概览

Swarm 是多智能体团队协作引擎：一个**预设（preset）**定义一组 agent 角色与一张任务 DAG，运行时按拓扑分层并行执行，每个任务由绑定 agent 的 worker（独立 ReAct 循环 + 工具白名单）完成，上游任务的 summary 经 `input_from` 注入下游 prompt。

REST 面提供：

1. **预设清单** —— `GET /swarm/presets`，列出内置 30 个预设 + 用户自定义预设（`~/.vibe-trading/swarm/presets/`），含每个预设的变量声明。
2. **运行管理** —— `POST /swarm/runs` 创建并立即后台执行；`GET /swarm/runs` 列表；`GET /swarm/runs/{id}` 详情（含逐任务状态与最终报告）；`POST .../cancel` 取消；`POST .../retry` 以相同 preset+变量**新建**一次运行。
3. **SSE 事件流** —— `GET /swarm/runs/{id}/events`，增量回放持久化事件日志（`events.jsonl`），支持 `Last-Event-ID` 续传。
4. **聊天桥接** —— 当 swarm 由聊天内的 `run_swarm` 工具发起时，运行事件会被桥接进会话 SSE 流（`swarm.started` / `swarm.event`），聊天时间线据此渲染内联状态卡。**IM 插件若只走 REST/SSE，可直接订阅 `/swarm/runs/{id}/events`，无需依赖桥接事件**；桥接事件仅在你复用会话流（01 篇）时需要。

运行状态持久化在 `~/.vibe-trading/swarm/runs/{run_id}/`（`run.json` + `events.jsonl` + `tasks/` + `artifacts/`，`store.py` / `config/paths.py` `get_swarm_runs_dir`），服务器重启后历史仍在，且读路径会把孤儿 `running` 运行收敛为真实终态（§5.2）。

## 2. 端点清单

| 方法 | 路径 | 鉴权 | 说明 | 来源 |
|---|---|---|---|---|
| `GET` | `/swarm/presets` | `require_auth` | 预设清单 | `list_swarm_presets` |
| `POST` | `/swarm/runs` | `require_auth` | 创建并启动运行 | `create_swarm_run` |
| `GET` | `/swarm/runs` | `require_auth` | 运行列表（新→旧，逐行 reconcile） | `list_swarm_runs` |
| `GET` | `/swarm/runs/{run_id}` | `require_auth` | 运行详情（reconcile 后） | `get_swarm_run` |
| `GET` | `/swarm/runs/{run_id}/events` | `require_event_stream_auth`（票据） | SSE 事件流 | `swarm_run_events` |
| `POST` | `/swarm/runs/{run_id}/cancel` | `require_auth` | 取消活跃运行 | `cancel_swarm_run` |
| `POST` | `/swarm/runs/{run_id}/retry` | `require_auth` | 重试 → **返回新 run id** | `retry_swarm_run` |

### 2.1 GET /swarm/presets

返回数组（按 `name` 升序），每项（`list_presets()`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 预设名（= 创建运行时传的 `preset_name`） |
| `title` | string | 展示标题 |
| `description` | string | 描述 |
| `agent_count` | int | agent 数量 |
| `variables` | `{name, description, required}[]` | 变量声明（YAML 原样透传；`api.ts` `SwarmPreset` 即此形状） |
| `source` | string | `"bundled"` 或 `"user"`（用户目录优先，同名覆盖内置） |

### 2.2 POST /swarm/runs

请求体：`{preset_name: string, user_vars: Record<string,string>}`。成功 → `{id, status, preset_name}`（`status` 通常为 `"pending"`）。错误：preset 不存在 → **404**（detail 含可用预设清单）；DAG/变量非法 → **400**。run id 形如 `swarm-YYYYMMDD-HHMMSS-<8位hex>`（`presets.build_run_from_preset`）。

### 2.3 GET /swarm/runs

查询参数 `limit`（默认 20，1–100）。返回数组（新→旧），每项（`SwarmRunSummary` 超集）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` / `preset_name` / `status` | string | `status` 为 reconcile 后的值 |
| `is_stale` | bool | 仍 `running` 但事件静默超阈值（§5.2）——**后端返回，`api.ts` 类型未声明**，IM 插件可自行消费 |
| `created_at` / `completed_at` | string \| null | UTC ISO |
| `task_count` / `completed_count` | int | 任务总数 / `completed` 任务数 |

### 2.4 GET /swarm/runs/{run_id}

未找到 → 404 `Run {id} not found`；id 经路径参数安全校验（00 篇 §4）。响应（reconcile + hydrate 后）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` / `preset_name` / `status` / `is_stale` | — | 同列表行 |
| `user_vars` | object | 创建时的变量 |
| `agents` | `SwarmAgentSpec[]` | 完整 model_dump：`id, role, system_prompt, tools, skills, max_iterations, timeout_seconds, model_name, max_retries` |
| `tasks` | object[] | 每项 = `serialize_task` 投影 + `worker_iterations`：`id, agent_id, status, summary, iterations, error, started_at, completed_at, depends_on, blocked_by, worker_iterations`（`error` 经内部路径脱敏） |
| `created_at` / `completed_at` | string \| null | UTC ISO |
| `final_report` | string \| null | 末层任务 summary 作为最终报告 |

> `api.ts` 把该响应声明为 `Record<string, unknown>`（无强类型）——上表即实际形状。

### 2.5 POST /swarm/runs/{run_id}/cancel

活跃运行 → `{status:"cancelled"}`；无此活跃运行 → **404** `No active run {id}`。取消在 DAG 层边界生效：未完成任务标记 `cancelled`（`runtime._cancel_remaining_tasks`）。

### 2.6 POST /swarm/runs/{run_id}/retry

先 reconcile 原运行，再按状态闸门：仍 `running` → **409** `Cannot retry a running run. Cancel it first.`；否则用原 `preset_name` + `user_vars` **新建运行**，返回 `{id: <新id>, status, preset_name}` —— **原运行记录原封不动**（`swarm_routes.py` 注释与 README 2026-08-11 修复：retry 不得删除/改写原 run）。原运行不存在 → 404；preset 已被删除 → 404；变量非法 → 400。任何终态（completed/failed/cancelled）都可 retry。

## 3. 数据契约（模型层）

### 3.1 SwarmEvent（事件日志条目，`models.py`）

`{type: string, agent_id: string|null, task_id: string|null, data: object, timestamp: string(UTC ISO)}`。追加写 `events.jsonl`；SSE 的 `data:` 载荷即该对象的 `model_dump()`（§4）。

### 3.2 任务状态（`TaskStatus`）

`pending`（无依赖任务的初始态）· `blocked`（初始：有 `depends_on`；运行期：上游失败被拦截）· `in_progress` · `completed` · `failed` · `cancelled`。

### 3.3 运行状态（`RunStatus`）

`pending → running → completed | failed | cancelled`。另有内部 `WorkerStatus`（不上 REST 面）：`completed | failed | timeout | token_limit | incomplete`——`incomplete` 表示 worker 跑完但无实质交付物，会被记为任务 `failed` 并带 `error`。

### 3.4 显示状态映射（聊天状态卡）

参考实现把任务状态 + 事件映射为 7 种显示态（`types/agent.ts` `SwarmAgentDisplayStatus` + `swarmStatus.ts` `mapTaskStatus`/`applySwarmEvent`）：

| 显示态 | 来源 |
|---|---|
| `waiting` | 任务 `pending`（默认兜底） |
| `running` | 任务 `in_progress`；或 `task_started`/`worker_started`/`tool_call`/`task_heartbeat` 事件 |
| `done` | 任务 `completed`；或 `task_completed`/`worker_completed` 事件 |
| `failed` | 任务 `failed`；或 `task_failed`/`worker_failed`/`worker_timeout`/`worker_incomplete` 事件 |
| `blocked` | 任务 `blocked`；或 `task_blocked` 事件（渲染 `Blocked by <上游id列表>`） |
| `retry` | `task_retry` 事件（渲染 `retry <attempt>`） |
| `cancelled` | 任务 `cancelled` |

## 4. SSE 事件

### 4.1 GET /swarm/runs/{run_id}/events（REST 面）

发射循环（`swarm_routes.py` `swarm_run_events`）：每 2s 轮询 `events.jsonl`，增量回放；**每帧带单调递增 `id:`**（第 N 条事件的 id 就是 N，首条为 1）。续传：浏览器 EventSource 重连自动带 `Last-Event-ID` **头**；非浏览器客户端可用 `?last_index=N` **查询参数**（头优先于查询参数）。语义为"跳过前 N 条"：客户端把最后收到的 `id` 原样回传即可精确续传，默认 0 = 从头回放。

帧形态：

```
id: <序号>
event: <SwarmEvent.type>
data: {"type":..., "agent_id":..., "task_id":..., "data":{...}, "timestamp":...}
```

终止帧：运行 reconcile 后进入终态 → `event: done`，`data: {"status": "completed"|"failed"|"cancelled"}`；运行目录消失 → `event: done`，`data: {"status": "missing"}`。**`done` 是发射器合成事件，不在 events.jsonl 里**。注意：该流无周期心跳帧——静默期靠 `task_heartbeat`/`run_heartbeat` 业务事件体现（默认约 3s 一条），代理超时应放宽。

### 4.2 事件类型全集（`runtime.py` + `worker.py` + `store.py`）

**运行级**：

| type | data 载荷 | 发射点 |
|---|---|---|
| `run_started` | `{}` | 运行转 running |
| `layer_started` | `{layer: int(0起), tasks: string[]}` | 每个拓扑层开始 |
| `run_heartbeat` | `{tool, elapsed_s, phase:"grounding"}` | grounding 预取期间保活 |
| `run_error` | `{error}` | 编排循环异常 |
| `run_completed` | `{status: 终态值}` | 运行收尾（**无论成败都发**，`status` 才是真实终态） |
| `run_recovered_terminal` | `{reason}` | reconcile 收敛孤儿运行（store 合成，幂等只记一次） |
| `run_reaped` | `{reason, stale_seconds}` | stale 回收（store 合成） |

**任务级**（带 `agent_id`/`task_id`）：

| type | data 载荷 |
|---|---|
| `task_started` | `{}` |
| `task_completed` | `{status, iterations, input_tokens, output_tokens}` |
| `task_failed` | `{error, input_tokens, output_tokens}` |
| `task_blocked` | `{blocked_by: string[], reason}`（上游未完成 → 下游跳过） |
| `task_retry` | `{attempt, max_retries, previous_error}` |
| `task_heartbeat` | `{tool, elapsed_s, iteration, phase: "llm"|"tool"}` |

**worker 级**（细粒度，仪表盘可选消费）：`worker_started`、`worker_completed {iterations}`、`worker_failed {error}`、`worker_timeout {elapsed}`、`worker_token_limit {tokens}`、`worker_incomplete {iterations, reason}`、`worker_iteration_limit`、`worker_text {content(增量), iteration}`、`tool_call {tool, iteration, call_id, arguments(预览), ...}`、`tool_result {tool, call_id, elapsed_ms, status:"ok"|"error", iteration, result_preview, ...}`、`content_filter_skipped {iteration, content_filter_count}`、`content_filter_circuit_breaker {count}`。

参考实现的消费策略（`swarmStatus.ts`）：以 `task_*` 为主干驱动状态机，`tool_call`/`tool_result` 更新"当前工具/耗时"列，`worker_text` 取最后一行做实时输出预览（截 160 字符），`task_heartbeat` 刷新 `elapsed_s`。未知事件类型**直接忽略**（前向兼容）。

### 4.3 聊天桥接事件（会话 SSE 流，01 篇通道）

仅当 swarm 由聊天内 `run_swarm` 工具发起时出现（`swarm_tool.py` `_emit_session_event`）：

| 事件 | payload |
|---|---|
| `swarm.started` | `{run_id, preset, variables, status, agents: [SwarmAgentSpec...], tasks: [SwarmTask...]}` —— 状态卡初始化骨架（参考实现 `buildSwarmStatusFromStarted`：tasks 映射为行，agents 补 role） |
| `swarm.event` | `{run_id, event: SwarmEvent}` —— 内层 `event` 即 §4.2 的条目，经 `applySwarmEvent` 归约更新状态卡 |

历史回放/重连场景下，若桥接事件缺失，参考实现还能从 `run_swarm` 工具结果（`tool_result` 预览 JSON：`{status, wait_budget_exhausted, run_id, preset, auto_variables, final_report, error, tasks, token_usage}`）水合出终态卡片（`buildSwarmStatusFromToolResultPreview`）。IM 插件走独立 `/swarm/runs/{id}/events` 流时不需要这套水合。

## 5. 枚举与状态机

### 5.1 状态枚举汇总

- `RunStatus`: `pending | running | completed | failed | cancelled`
- `TaskStatus`: `pending | blocked | in_progress | completed | failed | cancelled`
- 显示态（前端约定）: `waiting | running | done | failed | blocked | retry | cancelled`
- 预设 `source`: `bundled | user`

### 5.2 孤儿 running 收敛（reconciliation，`store.reconcile_run`）

每个读路径（list/detail/events/retry）都会对运行执行三层变换，**列表与详情是 `write=True`**（收敛结果会落盘）：

1. **Hydrate**：用 `tasks/*.json` 实时文件覆盖 `run.json` 里的层边界快照（执行中 `run.json` 只在层边界同步，任务文件才是实时真相）。
2. **终态恢复**：所有任务已终态但 run 仍 `running`（宿主在收尾前崩溃）→ 由任务态推导：全 `completed` → `completed`；含 `failed` → `failed`；仅 `cancelled`+`completed` → `cancelled`；其余 → `failed`。`final_report` 缺失时用最后一个完成任务的 summary 回填。追加 `run_recovered_terminal` 事件。
3. **Stale 回收**：仍 `running` 且事件静默超过阈值 → 非终态任务标记 `failed`（error 注明静默时长），再按 2 推导 run 终态，追加 `run_reaped` 事件。阈值 = `max(60s, min(心跳间隔×10, 最大agent预算+60s))`（`compute_stale_threshold`；心跳默认 3s → 下限钳到 60s）。

对客户端的含义：**任何读接口都不会返回"永久 running"的僵尸行**；`is_stale=true` 表示该行虽仍 running 但已被判定疑似死亡（下一次读可能直接收敛）。SSE 循环同样依赖 reconcile 来发出 `done` 帧——宿主已死的运行，流也能正常关闭。

## 6. 注意事项

1. **retry 是新运行**：拿到的是新 `id`，UI 应跳转/订阅新流；原运行保持原样可供审计。对 `running` 运行 retry 会 409——先 cancel。
2. **stale 判定是被动的**：没有后台守护线程主动收割（REST 面）；收敛发生在读时。若 IM 插件长时间不读某运行，它会一直挂着 `running` + `is_stale=true`。
3. **用户预设**：`~/.vibe-trading/swarm/presets/*.yaml`，与内置同名时**用户优先**；`GET /swarm/presets` 的 `source` 字段可区分。聊天内关键词路由只对内置表生效，用户预设只能点名运行（`swarm_tool._normalize_preset_name`）——REST 面不受此限。
4. **事件序号 vs 事件内时间戳**：SSE `id:` 是文件行序号（续传用），`timestamp` 才是业务时间；两者都单调，但重连回放时按 `id` 去重。
5. **`run_completed` 不等于成功**：真实终态在其 `data.status`（可能是 `failed`/`cancelled`）；随后 REST 面的合成 `done` 帧会再给一次终态。
6. **载荷中的 `arguments`/`result_preview` 是截断预览**，不是完整工具 I/O；完整产物在 run 目录 `artifacts/`（REST 面不暴露文件内容，如需见 02/08 篇的文件读取约定）。
7. **shell 工具默认关闭**：preset 声明的 shell 类工具仅在服务端显式开启时对 REST 发起的 run 生效（00 篇 §6）；worker 事件里默认看不到 bash 类 `tool_call`。
8. **创建即执行**：`POST /swarm/runs` 没有"仅创建不执行"模式；返回后运行已在后台线程推进，取消窗口从创建那一刻就存在。

## 7. 参考实现映射

| 参考实现 | 行为 | IM 插件对应点 |
|---|---|---|
| `api.ts` Swarm 客户端 | `listSwarmPresets` / `createSwarmRun(preset_name, user_vars)` / `listSwarmRuns`（不传 limit，用默认 20）/ `getSwarmRun` / `cancelSwarmRun` / `retrySwarmRun` / `swarmSseUrl`（mint ticket） | REST 调用层 |
| 独立 Swarm 页（`pages/Agent.tsx` 内 swarm 面板，原生 EventSource） | 订阅 `/swarm/runs/{id}/events`，按 §4.2 事件驱动仪表盘；`done` 帧收尾 | 团队运行仪表盘 |
| 聊天状态卡（`SwarmStatusCard.tsx` + `SwarmDashboard.tsx`） | 消费桥接事件 `swarm.started`/`swarm.event`；宽屏表格列 = agent / 状态 pill / 当前工具 / 耗时(+迭代数) / 输出预览（`error` 优先于 `lastText`）；层进度条（`LayerStepper`，由 `layer_started` 推导 current/total）；agent 计数 = `done|failed|blocked|cancelled` 的任务数 | 聊天内嵌卡片 |
| `swarmStatus.ts` | 事件 → 显示态的完整归约逻辑（§3.4 表）；未知事件忽略 | 状态机实现参照 |
| `swarmI18n`（`localizePreset`/`localizeStatus`） | 预设名与状态的多语言展示 | 文案层 |

> 维护提示：事件类型是自由字符串（`SwarmEvent.type: str`），后端新增事件不破坏旧客户端；IM 插件应对未知 type 静默忽略，并在需要新语义时对照 `runtime.py` / `worker.py` 的 `_make_event` / `_emit` 调用点扩展订阅。
