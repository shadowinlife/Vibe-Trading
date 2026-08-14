# 02 · 回测运行 / 产物 / 指标语义

> **文档族**: Vibe-Trading 前端集成知识库（`.omo/memory/frontend-integration/`）
> **日期**: 2026-08-14 · **事实来源**: 直接引自代码，路径见各节表格
> **本篇职责**: `/runs` REST 族（运行列表 / 运行详情 / 源码 / Pine 导出）+ run_dir 下全部产物文件的字段级 Schema + 回测指标语义。
> 认证、错误包体、Content-Type 校验、路径参数正则（`^[A-Za-z0-9_-]{1,128}$`）等全局约定见 [00-architecture-and-conventions.md](./00-architecture-and-conventions.md)，本篇不再重复。

**核心源码文件**：

| 文件 | 职责 |
|---|---|
| `agent/src/api/runs_routes.py` | `/runs*` 四个端点 + run_dir → 响应组装（`_build_response_from_run_dir`） |
| `agent/src/api/models.py` | `RunResponse` / `RunInfo` / `BacktestMetrics` / `Artifact` / `RAGSelection` Pydantic 模型 |
| `agent/src/ui_services.py` | 图表数据重建：`build_run_analysis` / `load_price_series` / `build_trade_markers` / `infer_run_stage` / `collect_run_logs` |
| `agent/backtest/engines/base.py` | 引擎产物写入：`_write_artifacts`（equity/trades/positions/metrics/ohlcv/fills）+ risk x-ray / rebalance notes / validation / run card 触发点 |
| `agent/backtest/metrics.py` | `calc_metrics` —— 全部指标的定义与单位 |
| `agent/backtest/validation.py` | Monte Carlo / Bootstrap / Walk-Forward + 严格 JSON 写盘（`write_validation_json`） |
| `agent/backtest/risk_xray.py` · `rebalance_notes.py` · `run_card.py` | 三个 JSON 产物的计算与写盘 |
| `agent/src/core/state.py` | run_dir 创建 + `state.json` / `req.json` 生命周期 |
| `frontend/src/lib/api.ts` | 参考实现 TypeScript 数据契约（`RunData` 等，~L451–671） |
| `frontend/src/pages/RunDetail.tsx` | Run Detail 页面（8 个 tab + 渐进式图表加载） |
| `frontend/src/lib/formatters.ts` · `runReports.ts` | 指标展示规则 / "报告级运行"判定 |

---

## 1. 能力概览

Run Detail 页面（路由 `/runs/{runId}`，参考实现 `RunDetail.tsx`）消费的全部数据来自 **一个详情端点 + 两个辅助端点**，无 SSE。页面能力与后端字段的映射如下（tab id 引自 `RunDetail.tsx` L51）：

| UI 能力 | tab id | 数据来源（`RunData` 字段） | 显示条件 |
|---|---|---|---|
| 研究仪表盘（`?view=dashboard` 直达） | `dashboard` | `StrategyResearchDashboard` 组件聚合 metrics / equity / validation 等 | 始终显示 |
| K 线 + 交易标记 + MA 叠加 + 权益小图 | `chart` | `chart_symbols`、`price_series`、`indicator_series`、`trade_markers`、`equity_curve` | 始终显示（无数据时空态） |
| Tearsheet（月度热力 / 年度收益 / Top 回撤） | `tearsheet` | `artifacts_equity_csv`（优先）或 `equity_curve` | 两者之一非空 |
| 交易明细表（筛选 / 分页 / CSV 下载） | `trades` | `trade_log`（`artifacts_trades_csv` 的前 500 行预览） | 始终显示 |
| Risk X-Ray + 调仓笔记（Portfolio Studio） | `studio` | `risk_xray`、`rebalance_notes` | 任一存在 |
| 统计验证面板（MC 扇形图 / Bootstrap CI / Walk-Forward） | `validation` | `validation` | 存在 |
| Run Card（Trust Layer：复现哈希 / 产物校验和 / 警告） | `runCard` | `run_card` | 存在 |
| 策略源码查看 | `code` | `GET /runs/{id}/code`（独立请求） | 始终显示 |

页面头部另有：状态图标（`status === "success"` ✓ / `"cancelled"` ⊘ / 其余 ✗，`RunDetail.tsx` L220–221、L323–338）、耗时（`elapsed_seconds`）、原始 prompt、指标卡（`metrics`，渲染规则见 §5）、trades/metrics 的 CSV 下载按钮（前端用 `trade_log` 与 `metrics` 本地重建 CSV，`RunDetail.tsx` L74–86）。

**运行目录布局**：运行落盘于 `~/.vibe-trading/runs/<run_id>/`（可用 `VIBE_TRADING_HOME` 迁移根目录；`RUNS_DIR` 经 `get_runs_dir()` 解析，`agent/src/api/helpers.py` L27）。`run_id` 形如 `20260814_030000_123456_a1b2c3`（时间戳 + 6 位 hex 后缀，`state.py` `create_run_dir`），目录名倒序即时间倒序（`GET /runs` 依此排序）。子结构：`code/`（signal_engine.py）、`logs/`（runner_stdout.txt / runner_stderr.txt / compile_error.txt）、`artifacts/`（全部回测产物）、根级 `state.json` / `req.json` / `config.json` / `planner_output.json` / `design_spec.json` / `run_card.json` / `llm_usage.json`（均按需存在）。

---

## 2. 端点清单

四个端点全部挂载 `require_auth`（Bearer 级别，见 00 篇 §3.3），路径参数经 `_validate_path_param` 校验。

| 方法 | 路径 | 查询参数 | 响应 | 来源 |
|---|---|---|---|---|
| GET | `/runs` | `limit`（默认 20，钳制到 1..100） | `RunInfo[]` | `runs_routes.py` L356–455 |
| GET | `/runs/{run_id}` | `chart_payload`、`chart_symbol`（见下） | `RunResponse`（§3） | `runs_routes.py` L313–354 |
| GET | `/runs/{run_id}/code` | 无 | `Record<filename, string>` | `runs_routes.py` L273–292 |
| GET | `/runs/{run_id}/pine` | 无 | `PineScriptResult` | `runs_routes.py` L294–311 |

### 2.1 GET /runs

按目录名倒序取前 `limit` 个 run_dir，每行 `RunInfo`（`models.py` L42–53）：

| 字段 | 类型 | 语义与推导规则（`runs_routes.py` L374–453） |
|---|---|---|
| `run_id` | string | 目录名 |
| `status` | string | `state.json.status` 小写；无 state.json 时：存在 `artifacts/equity.csv` 或 `review_report.json` → `"success"`，否则 `"unknown"` |
| `created_at` | string | 从 run_id 解析 `YYYY-MM-DD HH:MM:SS`（支持 `run_` 前缀格式）；解析失败回退目录 mtime |
| `prompt` | string \| null | 依次取 `req.json.prompt` → `planner_output.json.user_goal`/`.goal` → `user_prompt.txt`；全缺省时列表侧填 `"Manual Analysis"` |
| `total_return` / `sharpe` | number \| null | `artifacts/metrics.csv` 首行同名列（float） |
| `codes` | string[] | `load_run_context()` 的 codes（`ui_services.py` L100–146） |
| `start_date` / `end_date` | string \| null | 同上，归一为 `YYYY-MM-DD` |

### 2.2 GET /runs/{run_id} —— 渐进式图表加载

`chart_payload` 只接受缺省或 `"summary"`，其余值 → `400 {"detail": "invalid chart_payload"}`；run_dir 不存在 → `404`。三种调用形态（`runs_routes.py` L313–354 + `ui_services.py` `build_run_analysis` L489–535）：

| 调用 | 响应形态差异 |
|---|---|
| `GET /runs/{id}`（无参数，默认） | **全量图表**：`price_series` / `indicator_series` 含全部 symbol，`trade_markers` 不过滤；**不含** `chart_symbols` 字段；以 `response_model=RunResponse` 序列化 |
| `GET /runs/{id}?chart_payload=summary` | **摘要模式**：`price_series={}`、`indicator_series={}`、`trade_markers=[]`（不读 K 线，省带宽）；`chart_symbols`（排序后的 symbol 清单）、`run_stage`、`run_context`、`run_logs` 照常返回；以 `JSONResponse(model_dump + chart_symbols)` 返回 |
| `GET /runs/{id}?chart_symbol=X` | **单 symbol 模式**：`price_series` 仅含 X 的 K 线，`indicator_series` 仅 X，`trade_markers` 过滤到 `code == X`；同时返回 `chart_symbols`；同样经 `JSONResponse` 返回 |

参考实现的加载时序（`RunDetail.tsx` L169–188、L224–254）：先 `chart_payload=summary` 拿 run 摘要 + symbol 清单渲染首屏与指标卡，再对选中 symbol 逐个 `chart_symbol=X` 拉图表数据并缓存（每 symbol 独立 loading 态，支持"加载全部 + 进度条 + 取消"）。两种带参模式响应里还会顺带刷新 `equity_curve` / `trade_log`（后端每次都重建）。

三种模式的响应形态差异（仅列图表相关键，其余键与默认模式一致）：

```jsonc
// GET /runs/{id}  —— 默认全量，无 chart_symbols 键
{ "status": "success", "metrics": { ... },
  "price_series": { "AAPL.US": [ {"time":"2024-01-02","open":187.1,"high":188.4,"low":186.8,"close":188.0,"volume":82488700}, ... ], "MSFT.US": [ ... ] },
  "indicator_series": { "AAPL.US": { "ma5": [ {"time":"2024-01-02","value":null}, {"time":"2024-01-08","value":187.42}, ... ] } },
  "trade_markers": [ {"time":"2024-01-15","timestamp":"2024-01-15","code":"AAPL.US","side":"BUY","price":185.2,"qty":54.0,"reason":"signal","text":"BUY AAPL.US"}, ... ] }
```

```jsonc
// GET /runs/{id}?chart_payload=summary —— 图表三键置空，新增 chart_symbols
{ "status": "success", "metrics": { ... }, "run_stage": "done", "run_context": { ... },
  "price_series": {}, "indicator_series": {}, "trade_markers": [],
  "chart_symbols": ["AAPL.US", "MSFT.US"], "run_logs": [ ... ] }
```

```jsonc
// GET /runs/{id}?chart_symbol=AAPL.US —— 只含该 symbol，附带 chart_symbols
{ "status": "success",
  "price_series": { "AAPL.US": [ ... ] },
  "indicator_series": { "AAPL.US": { "ma5": [ ... ], "ma20": [ ... ] } },
  "trade_markers": [ /* 仅 code == "AAPL.US" 的行 */ ],
  "chart_symbols": ["AAPL.US", "MSFT.US"] }
```

序列化差异：默认模式经 `response_model=RunResponse` 序列化；两种带参模式经 `JSONResponse(model_dump(mode="json") + chart_symbols)` 返回（`runs_routes.py` L349–352），字段集合一致，仅 `chart_symbols` 有无的差别。

`chart_symbols` 的推导（`load_chart_symbols`，`ui_services.py` L408–436）：优先扫 `artifacts/price_series.csv` 的 code 列 → 次选 `artifacts/ohlcv_*.csv` 文件名 → 最后回退 `run_context.codes`。

### 2.3 GET /runs/{run_id}/code

返回 `{ "signal_engine.py": "<源码>" }`（当前仅枚举这一个文件，`runs_routes.py` L288）；`code/` 目录不存在 → `404`。

### 2.4 GET /runs/{run_id}/pine

读取 `artifacts/strategy.pine`（由 Agent 按 `pine-script` skill 用 `write_file` 生成，非引擎产物）。不存在 → `{"exists": false, "content": null}`；存在 → `{"exists": true, "content": "..."}`。

---

## 3. RunData 数据契约

权威定义：后端 `RunResponse`（`models.py` L56–103）+ 参考实现 `RunData`（`api.ts` L594–621）。下表以 `RunData` 为准，并标注后端多出的字段。

| 字段 | 类型 | 语义 | 来源 |
|---|---|---|---|
| `status` | string | 运行状态，枚举见 §6 | `state.json` |
| `run_id` | string | 运行 ID（= 目录名） | 路径 |
| `prompt` | string? | 原始自然语言请求 | `req.json.prompt` |
| `elapsed_seconds` | number? | 执行耗时（秒）。**注意**：历史运行详情端点固定回填 `0.0`（`runs_routes.py` L342），仅实时运行路径才有真实值 | — |
| `run_directory` | string | run_dir 绝对路径 | — |
| `run_stage` | string? | UI 阶段徽标，枚举见 §6 | `infer_run_stage` |
| `run_context` | object? | `{prompt, codes[], start_date, end_date, raw_context}`（`ui_services.py` L140–146） | `req.json` + planner 回退 |
| `metrics` | BacktestMetrics? | 指标对象，见 §5 | `artifacts/metrics.csv` 首行 |
| `artifacts` | ArtifactInfo[] | artifacts/ 目录下**每个文件**的元数据（非递归） | 目录扫描 |
| `run_card` | RunCard? | Trust Layer 运行卡，见 §4.10 | `run_card.json` |
| `risk_xray` | RiskXRayPayload? | 风险 X-Ray，见 §4.8 | `artifacts/risk_xray.json` |
| `rebalance_notes` | RebalanceNotesPayload? | 调仓笔记，见 §4.9 | `artifacts/rebalance_notes.json` |
| `validation` | ValidationData? | 统计验证，见 §4.7 | `artifacts/validation.json` |
| `chart_symbols` | string[]? | 可绘制 symbol 清单（仅带参模式返回，§2.2） | `load_chart_symbols` |
| `price_series` | Record<symbol, PriceBar[]>? | 按 symbol 分组的 K 线 | `load_price_series` |
| `indicator_series` | Record<symbol, Record<label, IndicatorPoint[]>>? | MA 叠加线，label 形如 `ma5`/`ma20`（周期从 planner/design 的 `*ma*` 参数推断，缺省 `[5, 20]`，`ui_services.py` L149–189） | 后端现算 |
| `trade_markers` | TradeMarker[]? | 图表买卖标记 | `trades.csv` 归一化 |
| `equity_curve` | EquityPoint[]? | **截断预览**：`artifacts_equity_csv` 前 **1000** 行映射为 `{time, equity, drawdown}`（`runs_routes.py` L193–204） | equity.csv |
| `trade_log` | Record<string,string>[]? | **截断预览**：`artifacts_trades_csv` 前 **500** 行（L206–207） | trades.csv |
| `artifacts_equity_csv` | Record<string,string>[]? | equity.csv **全量**行，值为字符串 | equity.csv |
| `artifacts_metrics_csv` | Record<string,string>[]? | metrics.csv 全量行 | metrics.csv |
| `artifacts_trades_csv` | Record<string,string>[]? | trades.csv 全量行 | trades.csv |
| `run_logs` | {source?, line_number?, message?}[]? | runner 日志：stdout/stderr/compile 三源，每文件**末尾 200 行**（`collect_run_logs` L222–254） | `logs/` |

**后端 `RunResponse` 额外携带、但参考实现 `RunData` 未声明的字段**（IM 视图层可选消费）：`reason`（失败/取消原因）、`planner_output`、`strategy_spec`（`design_spec.json`）、`rag_selection`（`RAGSelection`：selected_api/selected_name/selected_score）、`llm_usage`（`llm_usage.json`，provider/model + token 用量）、`artifacts_positions_csv`、`artifacts_target_positions_csv`（`models.py` L59–103）。

### 3.1 支撑类型

| 类型 | 字段 | 备注（来源 `api.ts`） |
|---|---|---|
| `RunListItem` | run_id, status, created_at, prompt?, total_return?, sharpe?, codes?, start_date?, end_date? | L451–461，对应 §2.1 |
| `PriceBar` | time, timestamp?, code?, open, high, low, close, volume | L468–477；open/high/low/close/volume 为 number（后端 `_normalize_price_rows` 已 float 化，缺失补 0.0） |
| `TradeMarker` | time（`YYYY-MM-DD`，截取前 10 字符）, timestamp?, code?, **side: "BUY" \| "SELL"**, price, qty?, reason?, text?（`"{side} {code}"`） | L479–488；后端 `build_trade_markers`（`ui_services.py` L257–288）将 trades.csv 的 side 大写化；price/qty 经 `_safe_float`，非法值 → `null` |
| `EquityPoint` | time, equity: string \| number, drawdown: string \| number | L490–494；来自 CSV 的行是字符串，消费端需 `Number()`（参考实现 `tearsheet.ts` `normalizeEquitySeries` 丢弃 equity 非有限的行，drawdown 非有限置 0） |
| `IndicatorPoint` | time, value（窗口不足处为 `null`） | L655–658 |
| `ArtifactInfo` | name, path（绝对路径）, type（扩展名去点，如 csv/json/pine，无扩展名 `"unknown"`）, size（字节）, exists（列表场景恒 true） | L660–666；`models.py` `Artifact` |
| `PineScriptResult` | exists: boolean, content: string \| null | L668–671 |
| `BacktestMetrics` | 7 个必填键 + `[key: string]: number` 索引签名 | L643–652，见 §5 |
| `RunCard` / `RunCardArtifact` | 见 §4.10 | L623–641 |

---

## 4. 产物文件 Schema（核心）

除注明外，写入者均为 `agent/backtest/engines/base.py`（引擎主流程 L713–868 + `_write_artifacts` L1683–1786），暴露端点均为 `GET /runs/{id}`。所有 JSON 产物经 `_json_safe` 归一（非有限浮点 → `null`）并以 `allow_nan=False` 写盘（严格 RFC-8259）。

### 4.1 equity.csv —— 权益曲线

| 列 | 类型 | 单位 / 语义 |
|---|---|---|
| `timestamp`（索引列） | string | bar 时间戳（日级为 `YYYY-MM-DD`） |
| `ret` | float | 组合单 bar 收益率（小数，首行 0） |
| `equity` | float | 组合净值（报价币种绝对值） |
| `drawdown` | float | 相对历史峰值回撤（小数，≤0） |
| `benchmark_equity` | float | 基准等值曲线（initial_capital 复利基准收益） |
| `active_ret` | float | `ret − 基准 bar 收益` |

读取注意：独立验证 CLI 接受 `equity`/`nav`/`value` 三种列名别名（`validation.py` `_load_equity` L352–361）。API 侧映射：`equity_curve` 预览仅取 timestamp/equity/drawdown 三列（§3）。

### 4.2 trades.csv —— 交易流水

每笔往返交易写**两行**（进场 + 离场），列序固定（`base.py` L1759–1763）：

| 列 | 类型 | 语义 |
|---|---|---|
| `timestamp` | string | 事件日期（`YYYY-MM-DD`） |
| `code` | string | symbol |
| `side` | string | 小写 `buy` / `sell`。多头进场为 buy、离场为 sell；空头相反 |
| `price` | float | 成交价（round 4 位） |
| `qty` | float | 数量（round 6 位） |
| `reason` | string | 进场行固定 `"signal"`；离场行为引擎 exit_reason（如 signal/stop_loss 等，取值集合随引擎，源码未穷举） |
| `pnl` | float | 进场行恒 0；离场行为已实现盈亏（round 4 位，绝对金额） |
| `holding_days` | int | 离场行 = 自然日持仓天数；进场行 0 |
| `holding_bars` | float | bar 数持仓（加权） |
| `return_pct` | float | **百分数**（如 5.23 = 5.23%，非小数）；离场行 round 2 位，进场行 0 |

独立验证 CLI 用 `pnl != 0` 识别离场行（`validation.py` L371–373）。前端交易表即按此列渲染（`RunDetail.tsx` TradesTab）。

### 4.3 positions.csv vs target_positions.csv —— 成交仓位 vs 优化器目标

**PR #1082 之后的关键区分**（`base.py` L1717–1723 注释）：

| 文件 | 内容 | 索引 / 列 |
|---|---|---|
| `positions.csv` | **真实成交后的持仓权重**（lot 取整、费用、拒单之后的执行真相，`_actual_positions_frame` L1546–1557） | 索引 `timestamp`；列 = 各 symbol，值 = 权重（小数，可为 0） |
| `target_positions.csv` | **优化器/信号请求的目标权重**（调仓指令的起点） | 同上 |

两者差异即执行滑移审计面。Risk X-Ray 的篮子权重取自 `positions.csv`（实际成交均值，`base.py` L816 `average_invested_weights(actual_pos)`）；rebalance notes 取自 target 帧（`base.py` L797）。**陷阱**：旧版本 positions.csv 曾是目标权重，消费端不要混用（见 §7）。

### 4.4 metrics.csv —— 标量指标

单行宽表：`calc_metrics` 结果中**所有非标量（dict）被剔除**后落盘（`base.py` L1785–1786），即不含 `by_symbol` / `by_exit_reason` / `validation`。API 解析时除 `trade_count` / `max_consecutive_loss` 转 int 外一律 float，且要求 `final_value` 存在（`runs_routes.py` L110–128）。键集与单位见 §5。

### 4.5 ohlcv_{code}.csv / price_series.csv —— K 线

引擎对每个 symbol 写 `ohlcv_{code}.csv`（`df.to_csv`，含索引列 `trade_date` + open/high/low/close/volume）。图表数据读取优先级（`load_price_series` L390–405）：`artifacts/price_series.csv`（列含 code/timestamp/OHLCV）→ `ohlcv_*.csv` 合集 → 用 run 的 config + loader **在线重建**（`reconstruct_price_series`，按 `data_lookback_days` 前移抓取起点，再裁剪回 `start_date`）。price_series.csv 无引擎写入点（测试与外部路径可写），源码未明确其常规产生方。所有行归一为 `{time, timestamp, code, open, high, low, close, volume}`（float，缺失 0.0），按 (code, time) 排序（`_normalize_price_rows` L575–601）。

### 4.6 fills.jsonl —— 不可变成交证据

每行一个 JSON：`symbol, timestamp(ISO), bar_idx, action(open/increase/reduce/close), signed_quantity, notional, execution_price, fee, margin, reason, holding_bars`（`base.py` L1768–1782）。审计用，`/runs` 族不直接内联，经 `artifacts[]` 可见。

**期权引擎差异**（`options_portfolio.py` L489–504，`engine="options"`）：同名产物的列结构与日线引擎**不同**，消费端必须先判别：

| 文件 | 期权引擎列 |
|---|---|
| equity.csv | `timestamp, equity, cash, positions_value`（无 ret/drawdown/benchmark 列；`equity_curve` 预览因此只有 time+equity） |
| trades.csv | `timestamp, code, option_type, strike, expiry, side, price, qty, pnl, entry_date`（无 reason/holding_days/return_pct） |
| greeks.csv | `timestamp, delta, gamma, theta, vega, rho, num_positions`（每 bar 组合 Greeks，round 6 位） |
| metrics.csv | 期权专用指标路径 `_calc_options_metrics`（权益触零时跳过风险比率），键集与 §5 不完全相同 |

期权运行不产生 positions.csv / target_positions.csv / fills.jsonl / rebalance_notes / risk_xray（源码未含相应写入点）。

### 4.7 validation.json —— 统计验证

触发条件：`config.json` 含 `validation` 键（`base.py` L834–845）；写入者 `write_validation_json`（`validation.py` L439–456）。顶层三键均可选，失败子项携带 `error` 字符串而非抛错。前端类型 `ValidationData`（`api.ts` L509–556）：

- `monte_carlo`（`monte_carlo_test` L29–113）：`actual_sharpe`, `actual_max_dd`, `p_value_sharpe`, `p_value_max_dd`, `simulated_sharpe_mean/std/p5/p95`, `n_simulations`, `n_trades`, `sharpe_samples[]`（全部模拟值），`equity_paths?`（扇形图载荷：`steps[]`（交易序号，1 起）、`initial_capital`、`actual[]`、`band_p5/p25/p50/p75/p95[]`、`samples[]`（≤30 条抽样路径）；仅当 `n_simulations × n_trades ≤ 2,000,000` 时生成，steps 降采样至 ≤400 点）。交易数 <3 → 仅返回 `error` + `p_value_sharpe: 1.0`。
- `bootstrap`（`bootstrap_sharpe_ci` L136–197）：`observed_sharpe`, `ci_lower`, `ci_upper`, `median_sharpe`, `prob_positive`, `confidence`, `n_bootstrap`, `sharpe_samples?`（`n_bootstrap ≤ 20000` 时附带）。收益观测 <5 → `error`。
- `walk_forward`（`walk_forward_analysis` L208–285）：`n_windows`, `windows[]`（每窗 `window/start/end/return/sharpe/max_dd/trades/win_rate`，return 为窗口内小数收益）, `profitable_windows`, `consistency_rate`, `return_mean/std`, `sharpe_mean/std`。

### 4.8 risk_xray.json —— 组合风险 X-Ray

写入者 `write_risk_xray`（`risk_xray.py` L356–368），引擎在 `base.py` L815–831 触发（短历史 / 从未持仓的运行抛 ValueError → **不产生该产物**）。前端类型 `RiskXRayPayload`（`api.ts` L558–575），实际字段以写入端为准：

| 段 | 字段 | 语义 |
|---|---|---|
| `inputs` | `symbols[]`, `weights{sym: w}`, `aligned_days`, `return_observations`, `first_date`, `last_date` | 进入计算的篮子（权重已归一到 1，round 8 位） |
| `concentration` | `hhi`, `effective_n`(=1/HHI), `top1_weight`, `top3_weight` | 集中度。**注意**：前端 TS 声明为 `top_weight`，与写入端 `top1_weight`/`top3_weight` 不一致（参考实现未渲染该字段，故未暴露）；IM 端请以写入端为准 |
| `volatility` | `daily_vol`, `annualized_vol`, `downside_deviation_annualized` | 小数 |
| `drawdown` | `max_drawdown`, `max_drawdown_start`, `max_drawdown_trough` | 小数 + 日期字符串 |
| `tail_risk` | `var_95`, `expected_shortfall_95`, `var_99`, `expected_shortfall_99`, `method` | 历史模拟法（日频、正损失分位数） |
| `diversification` | `diversification_ratio`（资产 <2 时为 null + `note`） | 分散化比率 |
| `correlation` | `avg_pairwise_abs`, `max_pair{symbols, corr}`, `beta_to_equal_weight` | 资产 <2 时字段为 null + `note` |
| `skipped` | `[{symbol, reason}]` | 历史不足（<30 bar）被剔除者。**注意**：前端 TS 声明 `string[]`，写入端为对象数组（参考实现未渲染） |
| `warnings` | string[] | 如权重归一化提示 |

### 4.9 rebalance_notes.json —— 调仓笔记

写入者 `write_rebalance_notes`（`rebalance_notes.py` L144–158），计算自 **target** 权重帧：任一目标权重向量较前次变动（turnover > 1e-6）记一次调仓。结构（前端 `RebalanceNotesPayload`，`api.ts` L577–592）：

- `rebalances[]`：`date`, `turnover`（0.5×Σ|Δw|，小数）, `entries[]`, `exits[]`, `top_moves[]`（按 |Δ| 降序前 5：`{code, from, to, delta}`）。**注意**：写入端 entries/exits 元素为 `{code, weight}`（`rebalance_notes.py` L68–77），前端 TS 声明的 `to`/`from` 与线上不符；参考实现只消费 `code`。
- `summary`：`rebalance_count`, `turnover_total`, `turnover_mean`, `turnover_max`, `largest_rebalance_date`（可为 null）。

同时写 `rebalance_notes.md`（渲染版，仅 artifacts[] 可见）。summary 三个 turnover 值会注入 metrics（`rebalance_count` / `rebalance_turnover_mean` / `rebalance_turnover_max`，`base.py` L802–804）。

### 4.10 run_card.json —— Trust Layer 运行卡

写入者 `write_run_card`（`run_card.py`，引擎在 `base.py` L853–862 调用；`SCHEMA_VERSION = "0.1"`）。同时渲染 `run_card.md`。字段（前端 `RunCard`，`api.ts` L623–641）：

| 字段 | 语义 |
|---|---|
| `schema_version` | 当前 `"0.1"` |
| `generated_at` | UTC ISO（`...Z` 结尾） |
| `run_dir` | 绝对路径 |
| `backtest` | config 摘要，仅取存在的键：`codes, start_date, end_date, interval, engine, initial_cash, source`（`BACKTEST_SUMMARY_KEYS` L14–22） |
| `reproducibility` | `config_hash`（config.json 文件 sha256；无文件则 config 内容 JSON sha256）+ `strategy_hash`（signal_engine.py sha256，存在时） |
| `data_sources` | string[]（本次运行的数据源名） |
| `metrics` | 标量指标子集（剔除 `validation` 与非标量） |
| `validation` | 验证结果原样内嵌（当 metrics 含 validation 时） |
| `warnings` | string[]（含 `content_filter_warnings` 透传） |
| `artifacts` | `[{path（相对 run_dir 的 posix 路径）, size_bytes, sha256}]`，覆盖 config.json + code/signal_engine.py + artifacts/ 全部文件（递归，`_list_artifacts` L147–167） |
| `artifact_refs` | 可选（IRR-AGL 引用，常规回测无） |

### 4.11 config.json / code/signal_engine.py / strategy.pine

| 文件 | 写入者 | 说明 |
|---|---|---|
| `config.json` | Agent 代码生成步骤（`write_file`），回测工具执行前校验 | Schema 见 `BacktestConfigSchema`（`runner.py` L68–162）：必填 `codes[]`（非空）、`start_date`、`end_date`（YYYY-MM-DD，start ≤ end）；默认 `source="tushare"`（须属 `VALID_SOURCES`）、`interval="1D"`（枚举 `{1m,5m,15m,30m,1H,4H,1D}`）、`engine="daily"`（`daily`/`options`）、`position_adjustment="hold"`（`hold`/`rebalance`）、`initial_cash=1_000_000`（>0）；可选 `benchmark`（ticker 或 `"auto"`）、`validation`、`optimizer`、`fundamental_fields`、`event_feeds` 等（`extra="allow"`） |
| `code/signal_engine.py` | Agent 代码生成 | 策略源码；`GET /runs/{id}/code` 返回；run_card 哈希对象 |
| `artifacts/strategy.pine` | Agent 按 pine-script skill 生成 | `GET /runs/{id}/pine` 返回 |

### 4.12 运行级元数据文件（run_dir 根级）

| 文件 | 写入者 | 内容 |
|---|---|---|
| `state.json` | `RunStateStore`（`state.py`，write+fsync） | `{status: success}` / `{status: failed, reason}` / `{status: cancelled, reason}` |
| `req.json` | `RunStateStore.save_request` | `{prompt, context}` |
| `planner_output.json` / `design_spec.json` / `rag_metadata.json` | planner/design 流程（会话模式） | 暴露为 `planner_output` / `strategy_spec` / `rag_selection` |
| `llm_usage.json` | AgentLoop（`src/agent/loop.py`） | provider/model + token 用量汇总与逐迭代明细；暴露为 `llm_usage` |
| `logs/runner_stdout.txt` 等 | 回测子进程 | 暴露为 `run_logs`（§3） |

---

## 5. 指标语义

定义：`calc_metrics`（`agent/backtest/metrics.py` L458–623）；展示规则：`formatters.ts`。**单位判定依据**：`formatters.ts` L49–67 —— `PCT_KEYS` 乘 100 加 `%`（证明数据为小数），`RATIO_KEYS` 原值展示，`INT_KEYS` 取整。

| 键 | 语义 | 存储单位 | UI 渲染（formatters.ts） | 必选 |
|---|---|---|---|---|
| `final_value` | 期末组合净值 | 币种绝对值 | 千分位、0 位小数 | ✅（metrics.csv 解析的必需键） |
| `total_return` | 总收益率 | 小数（0.12 = 12%） | `+12.00%` | ✅ |
| `annual_return` | 年化收益率 | 小数 | 百分比 | ✅ |
| `max_drawdown` | 最大回撤 | 小数（≤0） | 百分比；情绪阈值 >−5% 正 / >−20% 中 / 否则负 | ✅ |
| `sharpe` | 夏普比率（年化） | 比率 | `+1.23`；阈值 ≥1 正 / ≥0.3 中 | ✅ |
| `win_rate` | 胜率 | 小数（0–1） | 百分比；阈值 ≥0.5 正 / ≥0.35 中 | ✅ |
| `trade_count` | 往返交易笔数 | 整数 | 整数 | ✅ |
| `calmar` | 卡尔马（年化/|回撤|） | 比率 | 2 位小数 | 可选（DISPLAY_ORDER 内） |
| `sortino` | 索提诺（下行波动年化） | 比率 | 2 位小数 | 可选 |
| `profit_loss_ratio` | 盈亏比（均盈/均亏） | 比率 | 2 位小数 | 可选 |
| `profit_factor` | 盈利因子（总盈/总亏） | 比率 | 默认 4 位小数（无标签映射，展示键名原文） | 可选 |
| `max_consecutive_loss` | 最大连续亏损次数 | 整数 | 整数；阈值 ≤3 正 / ≤6 中 | 可选 |
| `avg_holding_days` | 平均持仓（实为 bar 数，`avg_holding_bars`） | float | 1 位小数 | 可选 |
| `benchmark_return` | 基准总收益 | 小数 | 百分比 | 可选（有基准时） |
| `excess_return` | 超额收益 = total − benchmark | 小数 | 百分比 | 可选 |
| `information_ratio` | 信息比率（年化主动收益/跟踪误差） | 比率 | 2 位小数；阈值 ≥0.5 正 / ≥0 中 | 可选 |
| `tracking_error` | 跟踪误差（年化主动波动） | 小数 | 无标签映射（默认 4 位小数） | 可选 |
| `benchmark_beta` | 组合对基准 beta | 比率 | 同上 | 可选 |
| `avg_turnover` / `total_turnover` | 执行层每 bar 平均/累计换手 | 小数 | 同上 | 可选 |
| `rebalance_count` / `rebalance_turnover_mean` / `rebalance_turnover_max` | 调仓次数 / 目标权重平均/最大换手 | int / 小数 | 同上 | 可选（引擎注入） |
| `risk_xray_hhi` / `risk_xray_effective_n` / `risk_xray_annualized_vol` / `risk_xray_max_drawdown` / `risk_xray_avg_invested` | X-Ray 头线指标 | 小数 | 同上 | 可选（X-Ray 成功时） |
| `benchmark_ticker` | 基准代码（字符串，非数值） | string | 不进指标卡（非 number） | 可选 |

基准语义（`base.py` L731–757）：未显式配置 benchmark 时，基准 = 各 symbol 等权 bar 收益均值；显式配置时经 `resolve_benchmark` 抓取，`benchmark_return` 用 #872 安全复利重推，且 `excess_return` 随之重算以保持两字段自洽（L773–786）。`metrics` 对象还含两个嵌套 dict（`by_symbol` / `by_exit_reason`，逐 symbol / 逐离场原因统计），**不进 metrics.csv**、不进指标卡，`BacktestMetrics` 的索引签名允许其存在。

指标卡展示顺序固定为 `DISPLAY_ORDER`（`formatters.ts` L79–83）：total_return → annual_return → sharpe → max_drawdown → win_rate → trade_count → calmar → sortino → profit_loss_ratio → max_consecutive_loss → benchmark_return → excess_return → information_ratio → final_value → avg_holding_days。

---

## 6. 枚举与状态

### 6.1 run `status`

写入端（`state.py`）只有三个值：`success` / `failed`（附 `reason`）/ `cancelled`（附 `reason`，用户主动停止，区别于失败）。读取端（`runs_routes.py`）的派生值：

| 值 | 出现条件 |
|---|---|
| `success` | state.json 记录 success；或列表模式下无 state.json 但存在 equity.csv / review_report.json |
| `failed` | state.json 记录（详情附 `reason`） |
| `cancelled` | state.json 记录（详情附 `reason`） |
| `unknown` | 无 state.json 且无上述产物（运行中 / 纯研究类运行 / 目录畸形） |
| 其他透传 | state.json 出现未预期字符串时原样小写透传（`runs_routes.py` L88；当前写入端无其他值） |

注意：`RunResponse.status` 的 docstring 写有 "aborted"（`models.py` L59），但现行写入端用的是 `cancelled` —— 以写入端为准。前端状态图标逻辑：仅 `success` 为绿，仅 `cancelled` 为灰，其余一律红色失败态（`RunDetail.tsx` L220–221）。

### 6.2 `run_stage`（UI 阶段徽标）

`infer_run_stage`（`ui_services.py` L192–219），按优先级：`done`（state=success）→ `failed`（state=failed）→ `backtest`（有 metrics.csv）→ `review`（有 review_report.json）→ `coding`（有 code/signal_engine.py）→ `design`（有 design_spec.json）→ `planning`（有 planner_output.json）→ `queued`（仅有 req.json）→ `unknown`。

### 6.3 其他受控取值

- `side`（TradeMarker）：`BUY` | `SELL`（后端大写化输出；trades.csv 原文为小写）。
- `interval`：`1m` `5m` `15m` `30m` `1H` `4H` `1D`（`runner.py` L51）。
- `engine`：`daily` | `options`（L52；daily 下再按市场路由到 10 个引擎）。
- `position_adjustment`：`hold` | `rebalance`（L79）。
- `fills.action`：`open` | `increase` | `reduce` | `close`。
- `run_logs.source`：`stdout` | `stderr` | `compile`。

---

## 7. 注意事项与校验要求

1. **CSV 行记录是字符串**：`artifacts_equity_csv` / `artifacts_metrics_csv` / `artifacts_trades_csv` / `trade_log` 的每个值都是 CSV 原文字符串（`csv.DictReader` 直出）。消费端必须自行 `Number()` 并做 `Number.isFinite` 防御（参考实现 `tearsheet.ts` L64–70、`RunDetail.tsx` `parseTradeNumber` L856–860）。唯一例外：`metrics`（顶层对象）与 `equity_curve` 预览里的数值——但 `equity_curve` 的 equity/drawdown 同样是字符串（直接复制 CSV 单元格），`EquityPoint` 类型因此声明为 `string | number`。
2. **非有限值归一**：后端在 validation.json / risk_xray.json / rebalance_notes.json / run_card.json 写盘时把 NaN/Infinity → `null`（`_json_safe` + `allow_nan=False`）；metrics.csv 侧则可能出现空值/异常值，API 解析失败即丢弃该键（`runs_routes.py` L118–124）。客户端仍应对所有数值字段做有限性防御。
3. **equity_curve 截断 vs artifacts_equity_csv 全量**：`equity_curve` 只有前 1000 行且仅 3 列（预览用）；完整曲线必须用 `artifacts_equity_csv`。`trade_log` 同理只有前 500 行，完整流水用 `artifacts_trades_csv`。Tearsheet 的月/年收益与回撤计算必须基于全量序列（参考实现 `RunDetail.tsx` L599–601 优先取 `artifacts_equity_csv`）。
4. **渐进式图表加载**：首屏请用 `chart_payload=summary`（避免一次性拉全部 K 线），再按需 `chart_symbol=X`。带参响应才含 `chart_symbols`；默认全量响应**没有**该字段。`chart_payload` 传 `"summary"` 以外的值会得到 400。批量加载需处理：每 symbol 独立 loading 态、竞态代际（run 切换时丢弃过期响应，参考实现 `runGenerationRef`）、可取消。
5. **报告级判定**：是否展示"完整报告"能力由 `isReportWorthyRun`（`runReports.ts`）决定——满足任一即可：`metrics` / `run_card` 非空对象，`equity_curve` / `trade_log` / `trade_markers` 非空数组，`validation` 非空对象，`price_series` 任一 symbol 有 bars，或 `artifacts` 含 `metrics|equity|trades|positions|ohlcv|validation|strategy` + `csv|json|pine|py` 命名的文件。纯研究类运行（无回测）不满足，应降级为纯文本展示。
6. **positions = 成交，不是目标**：PR #1082 起 `positions.csv` 是执行真相、`target_positions.csv` 是优化器请求。展示"实际持仓/敞口"必须用前者（或 `artifacts_positions_csv`）；展示"策略意图"用后者。把 target 当实际持仓会复现 #1082 之前的错误（报告 80% 敞口而实际只有 20%）。
7. **return_pct 是百分数**：trades.csv 的 `return_pct` 已是百分比数值（5.23 = 5.23%），不要再乘 100；而 metrics 的收益率族（total_return 等）是小数，必须乘 100。
8. **sha256 校验用途**：`run_card.artifacts[]` 的 sha256 覆盖 config/策略源码/全部产物，可用于 IM 端展示"产物未被篡改"徽标或与 CLI `--show` 结果对账；`reproducibility.config_hash` / `strategy_hash` 用于方法论复现核验（00 篇 Governance 节）。
9. **前后端类型漂移（参考实现遗留）**：`RiskXRayPayload.concentration.top_weight`（实际为 `top1_weight`/`top3_weight`）、`skipped: string[]`（实际为 `{symbol, reason}[]`）、`RebalanceNotesPayload.entries[].to` / `exits[].from`（实际为 `weight`）。参考实现因未渲染这些字段而未暴露问题；IM 端请按本篇 §4.8 / §4.9 的写入端字段实现。
10. **run_id 安全约束**：仅 `[A-Za-z0-9_-]{1,128}`（00 篇 §4），客户端拼接 URL 时仍需 `encodeURIComponent`。
11. **elapsed_seconds 恒 0**：历史运行详情固定回填 0.0，不要用它展示历史运行耗时（`runs_routes.py` L342）。
12. **strategy.pine 非必有**：由 Agent 按需生成；`exists: false` 属常态，Pine 导出按钮应据此置灰。
13. **同名产物跨引擎列结构不同**：期权引擎的 equity.csv 无 `drawdown` 列、trades.csv 无 `reason`/`return_pct` 列（§4.6）。`equity_curve` 预览按列名**条件复制**（`runs_routes.py` L196–203），因此期权运行的 equity_curve 行只有 `{time, equity}`；回撤图、交易表扩展列都必须容忍键缺失。判别依据：`run_card.backtest.engine` 或 `artifacts[]` 中是否含 greeks.csv。

---

## 8. 参考实现映射

| UI 构件 | 参考实现文件 | 关键位置 |
|---|---|---|
| Run Detail 页面骨架 / tab 路由 / 状态图标 | `frontend/src/pages/RunDetail.tsx` | L51（Tab 类型）、L111–421 |
| 渐进式图表加载（summary → per-symbol → load-all） | 同上 | L169–188、L224–305、L705–845（ChartTab） |
| K 线 + 交易标记 + MA 叠加 | `frontend/src/components/charts/CandlestickChart.tsx` | 由 ChartTab L831–835 装配 |
| 权益/回撤图 | `frontend/src/components/charts/EquityChart.tsx` | ChartTab L837–842、TearsheetTab |
| Tearsheet（月度热力/年度收益/Top 回撤） | `frontend/src/lib/tearsheet.ts` + `MonthlyReturnsHeatmap` / `AnnualReturnsChart` / `TopDrawdownsPanel` | TearsheetTab L598–637 |
| 交易表（BUY/SELL 过滤、pnl 汇总、分页 100/页） | `RunDetail.tsx` TradesTab | L847–1018 |
| 指标卡 | `frontend/src/components/chat/MetricsCard.tsx` + `formatters.ts` | 头部 L346 |
| 验证面板（MC 扇形图复用 equity_paths） | `frontend/src/components/charts/ValidationPanel.tsx` | L411、RunCardTab 内嵌 L465–477 |
| Risk X-Ray / 调仓笔记面板 | `RunDetail.tsx` StudioTab | L515–596 |
| Run Card 面板（schema/哈希/校验和/警告） | `RunDetail.tsx` RunCardTab | L423–508 |
| 源码查看（markdown 代码块渲染） | `RunDetail.tsx` CodeTab | L1020–1062 |
| 研究仪表盘视图（`?view=dashboard`） | `frontend/src/components/charts/StrategyResearchDashboard.tsx` | L391、L114（查询参数直达） |
| 报告级运行判定 | `frontend/src/lib/runReports.ts` | 全文 26 行 |
| REST 客户端 + 类型契约 | `frontend/src/lib/api.ts` | L133–142（端点）、L451–671（类型） |
