# 06 · 定时研究（Scheduled Research）

> **文档族**: Vibe-Trading 前端集成知识库（`.omo/memory/frontend-integration/`）
> **读者**: 在 IM 插件视图层重建同类能力的前端团队
> **校对日期**: 2026-08-14 · **事实来源**: 直接引自代码，路径见各节
> **本篇职责**: `/scheduled-runs` 任务 CRUD 与 `/scheduled-runs/playbooks` 模板族。认证/错误通用约定见 [00-architecture-and-conventions.md](./00-architecture-and-conventions.md)。

---

## 1. 端点总览

事实来源：`agent/src/api/scheduled_routes.py`（路由）、`agent/src/scheduled_research/models.py`（模型/校验）、`agent/src/scheduled_research/executor.py`（执行器）、`agent/src/scheduled_research/store.py`（持久化）、`agent/src/scheduled_research/playbooks.py`（模板）。

| 方法 | 路径 | 成功码 | 鉴权 |
|---|---|---|---|
| `POST` | `/scheduled-runs` | `201` | `require_auth` |
| `GET` | `/scheduled-runs` | `200` | `require_auth` |
| `DELETE` | `/scheduled-runs/{job_id}` | `204`（**真正空响应体**） | `require_auth` |
| `GET` | `/scheduled-runs/playbooks` | `200` | `require_auth` |
| `GET` | `/scheduled-runs/playbooks/{slug}` | `200` | `require_auth` |
| `POST` | `/scheduled-runs/playbooks/{slug}` | `201` | `require_auth` |

路由顺序无歧义：`{job_id}` 不含 `/`，playbook 子路径不会与 CRUD 路由冲突（源码注释明确说明）。

### 1.1 执行器闸门（最重要的部署事实）

后台执行器**默认关闭**。只有以环境变量 `VIBE_TRADING_ENABLE_SCHEDULER=1`（真值集合 `1/true/yes/on`，`scheduled_routes.py` `_SCHEDULED_RESEARCH_TRUE_VALUES`）启动的服务器才会触发 job；**未开启时 API 仍然可以创建/列出/删除 job，但没有任何 job 会执行**（`executor.py` `scheduler_enabled_from_env`、`env_schema.py` 默认 `False`）。IM 视图层应把这一点明示给用户（参考实现 Scheduled 页有一条常驻 `executorHint` 文案）。

## 2. ScheduledRun 数据模型（逐字段）

后端 `ScheduledRunResponse`（`scheduled_routes.py`）与前端 `ScheduledRun`（`frontend/src/lib/api.ts`）一致：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `string` | 创建时可指定（规则 `^[A-Za-z0-9_-]{1,128}$`，违反 → `422`）；缺省自动生成 UUID |
| `prompt` | `string` | 每次触发时原样重放的研究提示词（必填，min_length=1） |
| `schedule` | `string` | 间隔毫秒整数串或 5 字段 cron（§3） |
| `next_run_at` | `number` | **epoch 毫秒**（不是秒！`models.py`：`int(time.time() * 1000)`，`from_dict` 强制 int 且注释 "epoch ms"） |
| `status` | `string` | 生命周期枚举，§4 |
| `created_at` | `number` | **epoch 毫秒**；创建时戳一次，同 id 替换创建会重新戳（执行器据此区分新旧记录） |
| `last_run_at` | `number \| null` | 最近一次执行器尝试的时刻（epoch 毫秒）；从未触发为 `null` |
| `consecutive_failures` | `number` | 连续 dispatch 失败计数；成功一次清零 |
| `last_error` | `string \| null` | 最近失败的脱敏诊断文本，**上限 1000 字符**（超出截断加 `...`，`executor.py` `_MAX_PERSISTED_ERROR_CHARS`） |
| `failure_kind` | `string \| null` | 仅两个值：`"dispatch"`（provider/会话失败）或 `"schedule"`（调度表达式无法推进/非法）；成功或无失败为 `null`（`models.py` `from_dict` 强校验该集合） |
| `config` | `object` | 透传给 agent 会话的可选配置；playbook 创建会自动注入 `config.playbook = <slug>` |
| `timezone` | `string \| null` | IANA 时区键（如 `Pacific/Auckland`）；`null` = UTC（该字段出现前的既有语义）。间隔调度忽略它 |

> **时间戳单位是本 API 族的特例**：00 篇 §4 约定会话/API 路径输出 UTC ISO 字符串，但 scheduled jobs 的 `next_run_at`/`created_at`/`last_run_at` 是 **epoch 毫秒数值**。前端渲染前不要按 ISO 字符串解析。

## 3. schedule 格式（两种，语法以源码为准）

### 3.1 间隔形式

裸正整数字符串 = **毫秒**间隔（`_INTERVAL_MS_RE = ^[1-9][0-9]*$`），如 `"60000"` = 每分钟。上限 15 位数字（约 31,000 年），超出 → `422 interval is too large`。间隔调度**忽略 timezone**，且即使存储的时区键在当前主机无法解析也照常推进（`executor.py` `next_due` 先判间隔）。

### 3.2 5 字段 cron

`分 时 日 月 周`，空白分隔，必须恰好 5 段。每段接受：`*`、`*/n`、单个数字、逗号列表、`low-high` 范围（可混写，如 `1,3-5`）。字段边界（`models.py` `CRON_BOUNDS`）：

| 字段 | 范围 | 备注 |
|---|---|---|
| 分钟 | 0–59 | — |
| 小时 | 0–23 | — |
| 日（月内） | 1–31 | 字段校验只看边界；不可能的组合（如 2 月 31 日）在约 6 年搜索窗口内无匹配，`next_due` 抛 `ValueError`（带时区 cron 创建时即 422；无时区 job 首次执行时转 `failed`/`schedule`） |
| 月 | 1–12 | — |
| 周 | 0–6 | **cron 惯例周日=0；不接受 7 作为周日别名** |

范围倒写（`5-1`）→ 422。日和周**同时受限**时按标准 cron 语义取**或**（其一匹配即触发）；任一为 `*` 时另一字段为准。

### 3.3 timezone 与 DST 语义

- `timezone` 为 IANA 键或 `null`。cron 在**该时区的墙钟**上求值（周几判断也是）；`null` = 纯 UTC 语义。
- 创建时校验分两档：**cron** 调度必须能真正解析时区（`validate_timezone`，不可解析 → `422`）；**间隔**调度只做形状校验（非空字符串即可，`validate_timezone_shape`）——保证时区数据库缺失的主机上间隔 job 仍可运行。
- DST 策略（`executor.py` `_local_wall_time_to_epoch_ms`，issue #953）：
  - **春季前移缺口**（墙钟不存在）→ 该次触发**跳过**；
  - **秋季回退重叠**（墙钟二义）→ 取 `fold=0` **第一次出现**，只运行一次。
- 首次触发规则：带 timezone 的 cron job 首次触发是**第一个作者墙钟时刻**（不是创建时刻）；间隔 job 与无时区 job 保持"立即可触发"默认（`next_run_at = now`）。可用请求体 `next_run_at` 显式覆盖。

## 4. status 枚举与生命周期

`models.py` `JobStatus`（`str` 枚举，响应中为字符串值）：

| 值 | 语义 |
|---|---|
| `pending` | 等待下次触发（新创建 / 失败后重试中） |
| `running` | 执行器已拾取、dispatch 进行中 |
| `completed` | 最近一次 dispatch 成功。**注意：不是终态**——`next_run_at` 已推进，到期会再次触发 |
| `failed` | 终态。连续失败达上限，或调度表达式无法推进/非法（`failure_kind=schedule`）。**不会再被触发** |
| `cancelled` | 合法枚举值且执行器永不触发该状态；但 REST 的 DELETE 是**物理删除记录**而非改写状态——通过本篇 API 不会出现 cancelled 记录 |

执行器语义（`executor.py`）：

- 轮询周期默认 **60 秒**（`DEFAULT_TICK_INTERVAL_MS`）。
- dispatch = 新建一个标题为 `scheduled-research:{job.id}` 的 agent 会话并发送 `prompt`（`scheduled_routes.py` `_dispatch_scheduled_research_job`）。**`completed` 的含义是"成功入队"，不是 agent 跑完**——`send_message` 被接受即返回。
- dispatch 失败 → `consecutive_failures++`，指数退避重试（基数 60s、上限 1h、最多 3 次，分别可用 `VIBE_TRADING_SCHEDULER_MAX_CONSECUTIVE_FAILURES` / `..._RETRY_BASE_DELAY_MS` / `..._RETRY_MAX_DELAY_MS` 调整，默认值见 `env_schema.py`），超限 → `failed`。
- 进程重启后，残留的 `running` 记录在启动时被恢复为 `pending`（`recover_stale_running`）。
- 持久化：`~/.vibe-trading/scheduled_research/scheduled_research_jobs.json`（原子写 + fsync；文件损坏会被**隔离**——坏文件改名移开并抛 `CorruptStoreError`，而不是静默返回空列表，`store.py`）。

状态迁移表（执行器视角）：

| 当前状态 | 事件 | 迁移结果 |
|---|---|---|
| `pending` | 到期被拾取 | `running` |
| `running` | dispatch 成功 | `completed`（`next_run_at` 推进；到期再次触发） |
| `running` | dispatch 失败，未达上限 | `pending`（指数退避后的 `next_run_at`） |
| `running` | dispatch 失败，达上限 | `failed`（终态，`failure_kind=dispatch`） |
| `running` / `pending` | schedule 推进失败或表达式非法 | `failed`（终态，`failure_kind=schedule`） |
| `running` | 进程崩溃后重启 | 恢复为 `pending` |
| 任意 | `DELETE` | 记录物理删除（204） |

请求/响应示例：

```
POST /scheduled-runs
{"prompt":"Scan CSI300 momentum breakouts","schedule":"0 */6 * * *",
 "timezone":"Asia/Shanghai"}
→ 201 {"id":"…","status":"pending","next_run_at":1786708800000,…}

DELETE /scheduled-runs/<id> → 204（空响应体）
```

## 5. CRUD 请求/响应细节

### 5.1 POST /scheduled-runs（创建或替换）

请求体（`CreateScheduledRunRequest`）：`id?`、`prompt`（必填）、`schedule`（必填）、`next_run_at?`（epoch 毫秒）、`config?`、`timezone?`。前端 TS 类型未声明 `next_run_at`，但后端接受。

- **同 id 重 POST = 替换**（store upsert），`created_at` 重新戳。
- 校验失败一律 `422`，detail 为可读英文（如 `job id must be 1-128 characters of letters, digits, '_' or '-'`、`timezone 'X' is not a recognized IANA timezone key`）。
- 成功返回 `201` + 完整 `ScheduledRun`。

### 5.2 GET /scheduled-runs（列表）

查询参数：`status`（可选，按状态过滤；注意参数名经 alias 为 `status`）、`limit`（默认 50，`ge=1, le=200`）。返回 `ScheduledRun[]`。

### 5.3 DELETE /scheduled-runs/{job_id}

- 成功：**HTTP 204，响应体真正为空**（PR #1068；参考实现 `request<void>` 对空 body 返回 `{}`）。
- 未找到：`404 {"detail": "scheduled run <id> not found"}`。
- `job_id` 经 `_SAFE_PATH_PARAM_RE` 校验（00 篇 §4 的路径参数加固）；客户端应对 id 做 `encodeURIComponent`。

## 6. Playbook 模板族

模板是带 YAML frontmatter 的 markdown；frontmatter 是目录记录，正文是**逐字成为 `prompt`** 的指令文本（不重写为工具调用，路由留给 agent）。模板**不指名工具**，只以自然语言描述数据能力——工具面变化时模板不失效。

### 6.1 内置 5 个 slug（`agent/src/scheduled_research/playbooks/`）

| slug | 建议 schedule | 建议 timezone | 声明变量 |
|---|---|---|---|
| `premarket-brief` | `30 8 * * 1-5` | `Asia/Shanghai` | `home_market`, `watchlist` |
| `earnings-season-tracker` | `0 7 * * 1-5` | `America/New_York` | `universe`, `horizon_days` |
| `portfolio-checkup` | `0 9 * * 6` | `Asia/Shanghai` | `holdings`, `benchmark`, `lookback_days` |
| `a-share-money-flow` | `0 19 * * 1-5` | `Asia/Shanghai` | `watchlist` |
| `institutional-holdings-diff` | `0 9 15 2,5,8,11 *` | `America/New_York` | `managers`, `symbols` |

运营者可用 `VIBE_TRADING_PLAYBOOK_DIR` 追加自定义目录，同 slug **用户文件覆盖内置**（与用户技能同规则）。自定义模板解析失败会使整个目录列表返回 `500 playbook catalogue is unreadable: ...`（坏模板被暴露而非静默消失）。

### 6.2 GET /scheduled-runs/playbooks

返回 `PlaybookResponse[]`：`slug, name, description, suggested_schedule, suggested_timezone (可 null), markets[], data_capabilities[], variables (name→默认值映射)`；**列表端点 `body` 恒为 null**（保持目录响应小）。

### 6.3 GET /scheduled-runs/playbooks/{slug}

同上但**含 `body`**（未解析占位符的原文，`{{placeholder}}` 形式）。未知 slug → `404`；文件畸形 → `422`。slug 经路径参数校验（`^[a-z0-9][a-z0-9-]*$` 之外直接 404）。

### 6.4 POST /scheduled-runs/playbooks/{slug}（从模板建任务）

请求体（`CreateRunFromPlaybookRequest`，**所有字段可选**）：

| 字段 | 缺省行为 |
|---|---|
| `id` | 自动生成 `playbook-<slug>-<8位hex>`；提供时先过路径参数校验 |
| `schedule` | 用模板 `suggested_schedule` |
| `timezone` | **省略字段** = 保留模板建议时区；**显式 `null`** = 强制 UTC（二者在 JSON 层必须区分，源码用 `model_fields_set` 判别） |
| `variables` | 覆盖模板声明的变量默认值 |
| `config` | 透传；自动补 `playbook: <slug>` |
| `next_run_at` | 走 §3.3 首次触发规则 |

**POST `{}` = 按模板建议节奏 + 声明默认值直接排程**（README 明确的契约）。

变量校验（`playbooks.py` `render`）：

- **未声明的变量名被拒绝**（`422`，detail 形如 `playbook 'x' has no variable 'y'; declared: [...]`），绝不静默忽略；
- 空白值回退到声明的默认值；
- 单变量上限 **4000 字符**，超出 → `422`；
- 渲染后的正文逐字成为 `job.prompt`。

成功返回 `201` + `ScheduledRun`（与普通创建同构）。

## 7. 参考实现消费方式（`frontend/src/pages/Scheduled.tsx`）

| 行为 | 参考实现做法 |
|---|---|
| 轮询 | `GET /scheduled-runs` 每 **15 秒**一次（`POLL_MS = 15_000`）；AbortController + 序号守卫，创建/删除后立即刷新并丢弃过期响应 |
| 节奏文案 | `frontend/src/lib/cadence.ts` `describeCadence` 把 `schedule` 无损解析为四类：`interval`（毫秒整数）、`daily`（固定时刻 + dom/month 均为 `*` + dow 为 `*`）、`weekly`（固定时刻 + 可解析的 dow 集合）、`cron`（兜底显示原表达式）。**存储的 cron 就是作者墙钟时间，描述时不做任何 UTC 换算**——这正是 DST 两侧文案都正确的原因（#953） |
| 创建表单 | "时间模式"（`HH:MM` + 工作日/每天 → 生成 `M H * * 1-5` 或 `M H * * *`）与"高级模式"（裸 cron 文本）；timezone 下拉来自 `Intl.supportedValuesOf("timeZone")`，默认浏览器时区 |
| 时间渲染 | `next_run_at`（epoch ms）用 `Intl.DateTimeFormat` 在 **job 自己的 timezone** 里格式化；`timezone=null` 显示为 UTC 且不做任何换算 |
| 状态徽章 | `completed`=成功色、`failed`=危险色、`running`=警告色、`cancelled`/其它=中性 |
| 删除 | 两步确认（Delete → Confirm，5 秒自动收回）；成功依赖 204 空响应 |
| 错误展示 | `last_error` 非空时以危险色小字展示 |

**IM 视图层注意**：参考实现没有消费 playbook 端点（Scheduled 页只用裸 CRUD）——模板族的 UI 需要贵团队自行设计，契约以 §6 为准。

## 8. 集成核对清单

- [ ] 所有时间戳按 **epoch 毫秒**处理（`new Date(ms)` 直接可构造）；展示用 job 的 `timezone` 格式化，`null` 显示 UTC。
- [ ] 向用户明示执行器闸门：未以 `VIBE_TRADING_ENABLE_SCHEDULER=1` 启动的服务器，job 只会"记录"不会"执行"。
- [ ] `completed` 不是终态：列表里 completed 的 job 仍会按期再跑；只有 `failed` 与 DELETE 才终止调度。
- [ ] `last_error` 可能长达 1000 字符且含异常类名（已脱敏），UI 需换行/截断处理。
- [ ] 创建表单的 cron 校验可与后端同规则（§3.2 边界表），减少 422 往返；但**以后端 422 detail 为最终裁决**。
- [ ] 间隔输入换算成毫秒整数串提交（如"每 6 小时" → `"21600000"`）。
- [ ] playbook 创建时区分"省略 timezone"与"显式 null"（§6.4）。
- [ ] DELETE 成功判定以状态码 204 为准，不要尝试解析响应体。
- [ ] 轮询频率自定，但注意列表端点无服务端分页游标，仅 `limit`（≤200）+ `status` 过滤。
