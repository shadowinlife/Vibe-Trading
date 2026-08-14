# 03 · Alpha Zoo 因子库 API 族

> **文档族**: Vibe-Trading 前端集成知识库（`.omo/memory/frontend-integration/`）
> **读者**: 在 IM 插件视图层重建 Alpha Zoo 仪表盘的前端团队
> **校对日期**: 2026-08-14 · **事实来源**: 直接引自代码，路径见各节
> **本篇职责**: `/alpha/*` 端点族 —— 因子目录浏览/检索、因子详情（公式+源码）、Bench 任务（SSE 进度流+结果卡片）、多因子 Compare 排名。认证、错误包体、SSE 票据等通用约定见 [./00-architecture-and-conventions.md](./00-architecture-and-conventions.md)，本篇不再重复。

**事实来源文件**：

| 文件 | 职责 |
|---|---|
| `agent/src/api/alpha_routes.py` | 全部 6 个端点 + SSE 发射循环 + 请求校验（本篇字段级权威来源） |
| `agent/src/factors/bench_runner.py` | bench 结果包络（`run_bench`）、alive/reversed/dead 分类阈值 |
| `agent/src/factors/compare_runner.py` | compare 结果包络（`compare_alphas`）、`SORT_KEYS` |
| `agent/src/tools/alpha_bench_tool.py` | `_parse_period`（period 格式权威定义）、bench universe 白名单 |
| `frontend/src/pages/AlphaZoo.tsx` | 参考实现页面（browse/detail/bench/compare 四视图） |
| `frontend/src/lib/api.ts` | TypeScript 数据契约（`Alpha*` 类型，约 860–962 行）+ REST 客户端（约 229–254 行） |

---

## 1. 能力概览

Alpha Zoo 是 462 个预置横截面因子（cross-sectional alpha）的目录，分 5 个 zoo 家族（见 §5）。REST 面提供四类能力：

1. **目录浏览/检索** —— `GET /alpha/list`，按 zoo / theme / universe 过滤，返回因子摘要列表（含衰减周期、预热 bar 数等元数据）。
2. **因子详情** —— `GET /alpha/{alpha_id}`，返回完整元数据（含 LaTeX 公式）+ 该因子所在 zoo `.py` 文件的源码。
3. **Bench（整 zoo 打分）** —— `POST /alpha/bench` 异步起任务返回 `job_id`，再经 `GET /alpha/bench/{job_id}/stream` SSE 流拿进度与结果。一次 bench 对指定 universe × period 上的整个 zoo 逐因子计算 IC 序列，产出 alive/reversed/dead 三分类统计、Top 因子表、按 theme 聚合。耗时通常 5–10 分钟（`alpha_routes.py` 模块注释）。
4. **Compare（点名对决）** —— `POST /alpha/compare` 只对调用者点名的 ≥2 个因子做 bench 并排名（`compare_runner.py` 通过 `run_bench(only=…)` 子集过滤，不会把整个 zoo 跑一遍），SSE 流形态与 bench 相同。

Bench/compare 任务**纯计算、无 LLM 参与**（pandas 密集），任务状态只存进程内存，不落盘。

## 2. 端点清单

| 方法 | 路径 | 鉴权 | 说明 | 来源 |
|---|---|---|---|---|
| `GET` | `/alpha/list` | `require_auth` | 因子目录（可过滤） | `alpha_routes.py` `list_alphas` |
| `GET` | `/alpha/{alpha_id}` | `require_auth` | 单因子元数据 + 源码 | `alpha_routes.py` `get_alpha` |
| `POST` | `/alpha/bench` | `require_auth` | 起 bench 任务，**202**，返回 `job_id` | `alpha_routes.py` `kick_off_bench` |
| `GET` | `/alpha/bench/{job_id}/stream` | `require_event_stream_auth`（票据） | bench SSE 进度流 | `alpha_routes.py` `stream_bench` |
| `POST` | `/alpha/compare` | `require_auth` | 起 compare 任务，**202**，返回 `job_id` | `alpha_routes.py` `kick_off_compare` |
| `GET` | `/alpha/compare/{job_id}/stream` | `require_event_stream_auth`（票据） | compare SSE 进度流 | `alpha_routes.py` `stream_compare` |

### 2.1 GET /alpha/list

查询参数（全部可选）：

| 参数 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `zoo` | string | ∈ §5 zoo 枚举，否则 400 | 按 zoo 过滤 |
| `theme` | string | ∈ §5 theme 枚举，否则 400 | 按主题过滤 |
| `universe` | string | ∈ §5 元数据 universe 枚举，否则 400 | 按适用市场过滤；**接受 bench 别名**：`csi300→equity_cn`、`sp500→equity_us`、`btc-usdt→crypto`（`list_alphas` 内 `_ALIAS`） |
| `limit` | int | 1–1000，默认 100 | 截断上限 |

响应（`AlphaListResponse`，`api.ts`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | string | 恒为 `"ok"` |
| `alphas` | `AlphaSummary[]` | 见 §3.1 |
| `total` | int | 过滤后**未截断**的总数 |
| `returned` | int | 本次实际返回条数 |
| `truncated` | bool | `total > returned` 时为 true —— 客户端必须读此标志（见 §6） |

### 2.2 GET /alpha/{alpha_id}

`alpha_id` 必须匹配正则 `^[a-z][a-z0-9]+_[a-z0-9_]{1,64}$`（`_ALPHA_ID_RE`），否则 400 `invalid alpha_id`；未注册 → **404，且 `detail` 是对象** `{"status":"error","error":"alpha_id not found"}`（不是普通字符串，见 §6 陷阱）。成功响应（`AlphaDetailResponse`）：`{status:"ok", alpha: AlphaDetail, source_code: string}`。源码读取失败时降级为占位文本 `# <source unavailable: ...>`（仍返回 200）。

### 2.3 POST /alpha/bench

请求体（`AlphaBenchRequest` / 后端 `BenchRequest`）：

| 字段 | 类型 | 约束 |
|---|---|---|
| `zoo` | string | 必填，∈ zoo 枚举（1–64 字符） |
| `universe` | string | 必填，∈ **bench universe** `{csi300, sp500, btc-usdt}` |
| `period` | string | 必填，`YYYY-YYYY` 或 `YYYY-MM-DD/YYYY-MM-DD`（§5.4）；POST 时同步预解析，非法 → 400 `invalid period: ...` |
| `top` | int | 可选，默认 20，范围 1–500 |

成功 → **HTTP 202** `{status:"ok", job_id:"<uuid4 hex>"}`。并发上限 `MAX_CONCURRENT_BENCHES=2`：满了 → **429** `too many running benches; wait for one to finish`。

### 2.4 POST /alpha/compare

请求体（`AlphaCompareRequest` / 后端 `CompareRequest`）：

| 字段 | 类型 | 约束 |
|---|---|---|
| `alpha_ids` | string[] | 必填，原始 2–50 个；逐项匹配 `_ALPHA_ID_RE`；**服务端去重保序**，去重后仍需 ≥2，否则 400 |
| `universe` | string | ∈ bench universe 三值 |
| `period` | string | 同 bench |
| `sort` | string | 默认 `"ir"`，∈ `{ir, ic_mean, ic_positive_ratio, ic_count}`（与 `compare_runner.SORT_KEYS` 同步） |

成功 → **202** `{status:"ok", job_id}`；并发上限 `MAX_CONCURRENT_COMPARES=2`，满了 → **429** `too many running comparisons; wait for one to finish`。未知 alpha id **不会**在 POST 阶段报错——由 worker 放进结果包的 `skipped` 列表（`compare_runner._zoo_of`）。

## 3. 数据契约

### 3.1 AlphaSummary（列表行）

来源：`list_alphas` 组装逻辑 + `api.ts` `AlphaSummary`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 如 `gtja191_171` |
| `zoo` | string | 所属 zoo |
| `theme` | string[] | 主题标签数组（可为空数组） |
| `universe` | string[] | 适用市场数组（元数据 universe 值） |
| `nickname` | string \| null | 可选昵称 |
| `decay_horizon` | number \| null | 预测半衰期（天），参考实现展示为 "decay days" 列 |
| `min_warmup_bars` | number \| null | 最少预热 bar 数 |
| `requires_sector` | boolean | 是否需要行业数据（后端强制 `bool(...)`） |

### 3.2 AlphaDetail（详情）

`{id, zoo, module_path?, meta}`。`meta` 是自由对象（`Record<string, unknown>`），参考实现读取的键（`AlphaZoo.tsx` DetailView）：`formula_latex`（公式展示）、`nickname`、`theme`、`universe`、`frequency`、`decay_horizon`、`min_warmup_bars`、`requires_sector`、`notes`。外层另有 `source_code`（整个 zoo 文件源码，参考实现按行数展示折叠面板）。

### 3.3 bench 任务内部状态（job 记录）

`ALPHA_BENCH_JOBS[job_id]`（进程内 dict，`alpha_routes.py`）：

```
{job_id, status, zoo, universe, period, top, created_at,
 progress: {n_done, n_total, current_alpha_id},
 result, error}
```

`status` 生命周期：`queued → running → done | error`。`created_at` 为 UTC ISO（秒精度）。compare job 记录同形，另含 `alpha_ids` 与 `sort`，且初始 `progress.n_total = len(alpha_ids)`（bench job 初始 `n_total=0`，由 worker 首次回调填充）。

### 3.4 AlphaBenchResult（`result` 事件载荷）

`_result_for_wire` 投影后的字段（bench_runner 原始包络的子集）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `alive` / `reversed` / `dead` | int | 三分类计数 |
| `skipped` 与 `n_skipped` | int | 跳过因子数——**两个键同值并发**（后端注释：兼容早期读 `n_skipped` 的客户端） |
| `n_alphas_tested` | int | 实际算出 IC 的因子数 |
| `top5_by_ir` | `AlphaBenchTopRow[]` | IR 降序前 min(5, top) 名 |
| `dead_examples` | `AlphaBenchTopRow[]` | ic_mean 升序前 5（最弱/最反转的样例） |
| `by_theme` | `Record<theme, {alive, reversed, dead, count}>` | 按 theme 聚合；`count` 为后端附带键，参考实现只读前三项 |
| `meta` | object | universe 面板元数据（如 sp500 的 `survivorship_bias` 披露），可能为空对象 |

`AlphaBenchTopRow`（`_slim` 投影）：`{id, ic_mean, ir, theme[], formula_latex, category}`，`category ∈ alive|reversed|dead`。

> 后端在存入 job 前已剥离大体积的 `rows`（全量逐因子明细）与 `skipped[]` 明细列表（`_run_bench_blocking`），SSE 载荷只有摘要——**REST 面拿不到逐因子全量明细**。bench_runner 包络里的 `multiple_testing`（多重检验校正）也不在 `keep` 白名单内，不会上线。

### 3.5 AlphaCompareResult（`result` 事件载荷）

`_compare_result_for_wire` = compare 包络去掉 `status`（事件类型本身已表达成功）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `universe` / `period` / `sort` | string | 回显请求参数（`sort` 非法值在 runner 内回落 `ir`） |
| `n_compared` | int | 参与排名的因子数 |
| `n_skipped` | int | 跳过数 |
| `winner` | string | `ranking[0].id` |
| `ranking` | `AlphaCompareRow[]` | 按 `sort` 降序，`rank` 从 1 起 |
| `skipped` | `{id, reason}[]` | 未知 id / bench 失败原因明细 |

`AlphaCompareRow`：`{rank, id, zoo, ic_mean, ic_std, ir, ic_positive_ratio, ic_count, delta_<sort>_vs_best}`。**`delta_<sort>_vs_best` 是动态键**：键名随 `sort` 变化（如 `delta_ir_vs_best`、`delta_ic_mean_vs_best`），值 = 该行指标 − 第一名指标（≤0，保留 6 位小数；第一名自身为 0）。参考实现用 `` `delta_${result.sort}_vs_best` `` 取列（`CompareResultPanel`），`api.ts` 以索引签名 `[deltaKey: string]` 声明。

## 4. SSE 进度事件

事实来源：`alpha_routes.py` `_job_event_stream`（bench/compare 共用同一发射循环）、`_sse` 帧格式。**这两个流没有事件 ID、没有回放/续传能力**——与 01/04 篇的会话/swarm 流不同；断线即丢，客户端需重新 POST 起任务。

帧格式：`event: <name>\ndata: <JSON>\n\n`（`json.dumps(..., ensure_ascii=False, default=str)`）。循环每 0.5s 轮询一次 job；空闲约 15s 发一帧 SSE 注释 `: ping` 保活（不是事件，客户端解析器自然忽略）。

| 事件 | 触发条件 | payload |
|---|---|---|
| `progress` | `n_done` 相对上次发射变化时（含首次 0 值帧） | job 的 `progress` 对象原样：`{n_done, n_total, current_alpha_id}`。`current_alpha_id` 初始为 `null` |
| `result` | job `status=done` 且 result 非空 | bench：§3.4 投影；compare：§3.5 投影 |
| `done` | 成功终态，`result` 之后紧跟 | `{job_id, wall_seconds}`。bench 的 `wall_seconds` 取自结果（秒，2 位小数）；**compare 结果无此键 → 值为 `null`** |
| `error` | job `status=error`，或 job 被清理丢失 | `{message}`。普通失败 message 为 worker 归一化错误串（universe 加载失败等）或固定短语 `internal error; see server logs`；job 丢失为 `job vanished` |

终态序列：**成功 = `result` → `done`；失败 = `error` → `done`**（`done` 恒为最后一帧，随后服务端关闭流）。进度百分比 = `n_done / n_total`（`n_total=0` 时按 0% 处理，参考实现 `ProgressPanel`）。`progress` 事件不含百分比/阶段字段，需要客户端自行换算；bench 的 `n_total` 在 worker 首次回调前是 0。

## 5. 枚举

### 5.1 因子分类 category（`bench_runner.categorise`）

| 值 | 判定（IC 序列 t 统计量 `t = ic_mean/(ic_std/√n)`） |
|---|---|
| `alive` | `ic_mean > 0.02` 且 `ic_positive_ratio ≥ 0.55` 且 `|t| > 2` |
| `reversed` | `ic_mean < -0.02` 且 `|t| > 2` |
| `dead` | 其余 |

### 5.2 zoo id（`_VALID_ZOOS`）

`qlib158`（154 因子）· `alpha101`（101）· `gtja191`（191）· `academic`（12）· `fundamental`（4）。参考实现 `ZOO_CARDS` 的 `approxCount` 仅用于卡片展示（academic 卡片写 10，是展示近似值，目录真实计数以 `GET /alpha/list` 的 `total` 为准）。

### 5.3 universe —— 两套枚举，勿混用

| 用途 | 枚举 | 来源 |
|---|---|---|
| **bench/compare 请求**（`_BENCH_UNIVERSES`） | `csi300` · `sp500` · `btc-usdt` | `alpha_routes.py`；仅这三个有面板加载器（`alpha_bench_tool._UNIVERSE_TAG`）。`btc-usdt` 是单资产 universe，bench 语义上会退化（参考实现对 "single-asset" 错误有内联提示） |
| **列表过滤/因子元数据**（`_VALID_UNIVERSES`） | `equity_us` `equity_cn` `equity_hk` `equity_in` `equity_kr` `crypto` `futures` | `alpha_routes.py`；`GET /alpha/list` 额外接受 bench 三别名并映射（§2.1）。韩国/印度只是元数据 universe，没有 bench 面板 |

theme 枚举（`_VALID_THEMES`）：`momentum` `reversal` `volume` `volatility` `quality` `value` `liquidity` `microstructure` `sentiment` `growth` `leverage`。

### 5.4 period 格式（`alpha_bench_tool._parse_period`）

- `YYYY-YYYY`（如 `2020-2025`）→ 展开为 `{Y1}-01-01` ~ `{Y2}-12-31`；
- `YYYY-MM-DD/YYYY-MM-DD`（斜杠分隔）→ 原样使用；
- 起 > 止 → 拒绝。POST 阶段同步校验（400），其余格式错误在 worker 内才暴露（经 `error` 事件）。

### 5.5 job 状态与并发

job `status`：`queued | running | done | error`（仅内部，SSE 不直接发射 status 事件——状态变化体现在 progress/result/error/done 的序列上）。并发：bench 与 compare 各自独立信号量，上限均 2。

## 6. 注意事项

1. **截断标志必须处理**：`GET /alpha/list` 默认 `limit=100` 而目录有 462 个因子——不传 `limit` 必然 `truncated=true`。参考实现直接传 `limit=1000` 一次取全量再做客户端分页（`PAGE_SIZE=50`）。
2. **job 生命周期短、无持久化**：job 存于进程内存，**服务器重启即全部丢失**；完成/失败 1 小时（`_JOB_TTL_SECONDS`）后在下一次 POST 时被清理，此后访问流 → `error: job vanished` 或直接 404。IM 插件若需历史结果，请自行落库 `result` 事件载荷。
3. **job_id 校验**：`^[A-Za-z0-9_-]{1,64}$`（uuid4 hex 天然满足）；畸形 → 400，未知 → 404 `job {id} not found`。
4. **SSE 鉴权走一次性票据**：浏览器 EventSource 用 `?ticket=`（参考实现 `alphaBenchStreamUrl` / `alphaCompareStreamUrl` 经 `withAuthTicket` 现 mint）；服务端消费者可直接带 Bearer 头。详见 00 篇 §3.2。
5. **404 的 detail 是对象**：`GET /alpha/{alpha_id}` 未命中时 `detail = {"status":"error","error":"alpha_id not found"}`；00 篇的 `body.detail || body.message` 读取顺序会把该对象当字符串用——参考实现此处直接落通用错误文案，IM 插件需同样防御。
6. **EventSource 合成 error 竞态**：正常 `done` 关流后浏览器 EventSource 会再抛一个合成 `error`。参考实现用同步 `doneRef` 区分"已 done 的关流"与真实错误（`AlphaZoo.tsx` `attachStream`），重连逻辑必须复制这一防御，否则会误报失败。
7. **429 语义**：并发满员时 POST 直接拒绝（不进队列）。客户端应提示用户等待，而非自动重试加剧拥塞。
8. **progress 不是单调时间线**：只在 `n_done` 变化时发射；并行 worker 下 `current_alpha_id` 是"最近完成"的因子而非"正在计算"的因子（回调在完成时触发）。
9. **compare 的 `delta_*` 键随 sort 变化**：渲染列时必须按响应里的 `sort` 字段动态拼键名，不要硬编码 `delta_ir_vs_best`。

## 7. 参考实现映射

| 参考实现（`frontend/src/pages/AlphaZoo.tsx`） | 行为 | IM 插件对应点 |
|---|---|---|
| `BrowseView` | `listAlphas({zoo, theme, universe, limit:1000})`；zoo 卡片（`ZOO_CARDS` 含近似计数）；theme 选项从返回数据动态聚合；搜索/分页纯客户端；勾选 ≥2 个因子后经 URL `?ids=a,b,c` 进 compare | 目录页 + 多选入口 |
| `DetailView` | `getAlpha(id)`；渲染 `formula_latex`、meta 表、源码折叠面板；"Run bench" 按钮把元数据 universe 经 `BENCH_UNIVERSE_FOR_METADATA` 映射成 bench universe 预填表单（无面板市场不预填） | 详情卡片 |
| `BenchView` | `createAlphaBench` → `alphaBenchStreamUrl`（mint ticket）→ **原生 EventSource**（不走共享 `useSSE` hook——该 hook 的 `knownTypes` 白名单会丢弃 progress/result/done/error）；进度条 `n_done/n_total`；表单默认 `period=2020-2025`、`top=20` | bench 表单 + 进度条 |
| `ResultPanel` | 四张统计卡（alive/reversed/dead/skipped）+ `top5_by_ir` 表 + `dead_examples.slice(0,3)` 表 + `by_theme` 堆叠柱状图（ECharts） | 结果卡片 |
| `CompareView` | `createAlphaCompare` → `alphaCompareStreamUrl` → 同款原生 EventSource 生命周期；id 支持逗号/空白分隔自由文本（`parseAlphaIds` 去重保序） | 对决表单 |
| `CompareResultPanel` | winner 横幅 + `n_compared`/`n_skipped` 摘要 + 排名表（动态 `delta_<sort>_vs_best` 列，第一名渲染 "—"）+ skipped 明细行 | 排名卡片 |

> 维护提示：后端 `_VALID_*` 枚举与 `compare_runner.SORT_KEYS` 是"本地复制、注释要求同步"的关系（`alpha_routes.py` 注释）；若后端新增 zoo/theme/sort，本篇与 IM 插件需同步更新。
