# 05 · 相关性与机制时间线（Correlation & Regime Timeline）

> **文档族**: Vibe-Trading 前端集成知识库（`.omo/memory/frontend-integration/`）
> **读者**: 在 IM 插件视图层重建同类能力的前端团队
> **校对日期**: 2026-08-14 · **事实来源**: 直接引自代码，路径见各节
> **本篇职责**: `/correlation`（滚动相关矩阵）与 `/correlation/regime`（机制时间线）两个只读分析端点。认证/错误/限流的通用约定见 [00-architecture-and-conventions.md](./00-architecture-and-conventions.md)。

---

## 1. 端点总览

| 方法 | 路径 | 鉴权 | 用途 |
|---|---|---|---|
| `GET` | `/correlation` | `require_auth` | 跨资产日收益相关系数矩阵（Pearson / Spearman） |
| `GET` | `/correlation/regime` | `require_auth` | 边缘密度 + 滞回状态机的 FUSED 机制时间线 |

事实来源：`agent/src/api/system_routes.py`（路由注册、参数约束、限流器）、`agent/backtest/correlation.py`、`agent/backtest/regime.py`（计算实现）。

两个端点**共享同一个限流预算**：进程内滑动窗口限流器，**每客户端 IP 30 次 / 60 秒**（`system_routes.py` `_SlidingWindowRateLimiter(max_requests=30, window_seconds=60.0)`，`_correlation_rate_limiter` 为两路由共用）。超限返回 `429 {"detail": "Rate limit exceeded, try again later"}`。限流按 `request.client.host` 分桶——IM 插件若从单一服务端 IP 高频调用，会与其它消费者共享这一配额，请在客户端做请求合并/节流。

两路由均挂 `Depends(require_auth)`（00 篇 §3.3）；`/correlation` 还在 Vite dev 代理白名单内（00 篇 §2）。

## 2. GET /correlation — 相关矩阵

### 2.1 查询参数

| 参数 | 类型 | 约束（源码值） | 默认 |
|---|---|---|---|
| `codes` | string | 必填，逗号分隔；去空白后 **≥2 且 ≤20** 个代码，否则 `400` | — |
| `days` | int | `ge=7, le=365`（FastAPI Query 约束，越界 422） | `90` |
| `method` | string | 仅 `pearson` 或 `spearman`，其它值 `400` | `pearson` |

参数校验错误（`system_routes.py`）：

- `400 At least 2 asset codes required`
- `400 Maximum 20 assets per request`
- `400 method must be 'pearson' or 'spearman'`

### 2.2 代码书写形式与 loader 回退链

`codes` 接受**裸 ticker**（如 `AAPL,SPY,600000,0700,BTC-USDT`），后端按 `backtest/correlation.py` 的规则归一化：

1. `infer_market()` 判定市场：加密对（以 USDT/BTC/ETH/… 结尾或含 `/`）→ `crypto`；显式后缀（`.HK` `.SH`/`.SZ`/`.BJ` `.KS`/`.KQ` `.TO`/`.V` `.US`）权威优先；纯数字按位数区分 A 股（恰 6 位）与港股（≤5 位）；其余字母代码按美股处理。
2. `_normalize_symbol()` 补后缀：`AAPL`→`AAPL.US`；`600000`→`600000.SH`（6 开头沪市、4/8 开头北交所、其余深市）；`0700`→`0700.HK`；加密对与已带 `.` 的代码原样透传。
3. 按市场的 `FALLBACK_CHAINS`（`agent/backtest/loaders/registry.py`）**逐个 loader 尝试直到真正取到数据**——不是停在第一个"可用"的 loader。例如 A 股链：`tencent → mootdx → eastmoney → baostock → akshare → tushare → local`。

**标签回显规则**：响应里的 `labels` 使用**用户原始书写**（非归一化符号），并按字典序排序（`_rolling_correlation_matrix` 中 `codes = sorted(price_series.keys())`）。矩阵行列顺序与 `labels` 一致。

任何 loader 都取不到数据的代码会被**静默丢弃**（仅服务端日志 warning）。若成功取数的资产 <2，返回 `400`，detail 形如 `Could not fetch price data for at least 2 assets. Fetched: [...]`。

各市场回退链（`registry.py` `FALLBACK_CHAINS`，按 IP 封禁风险排序，与回测共用）：

| 市场键 | 链（从左到右依次尝试） |
|---|---|
| `a_share` | tencent → mootdx → eastmoney → baostock → akshare → tushare → local |
| `us_equity` | yahoo → stooq → sina → eastmoney → yfinance → tiingo → fmp → finnhub → alphavantage → longbridge → akshare → local |
| `hk_equity` | tencent → eastmoney → yahoo → futu → akshare → yfinance → tushare → longbridge → local |
| `crypto` | okx → binance → ccxt → yfinance → local |
| `kr_equity` | pykrx → yahoo → yfinance → local |
| `ca_equity` | yahoo → yfinance → local |

注意：`infer_market` 只产出 `crypto / hk_equity / a_share / kr_equity / ca_equity / us_equity` 六个市场键——`futures / fund / macro / forex` 链在本端点**不可达**（无对应后缀识别规则），外汇/期货类代码会按字母代码落入美股链。

请求示例（鉴权头细节见 00 篇 §3）：

```
GET /correlation?codes=AAPL,SPY,BTC-USDT,600519&days=90&method=pearson
Authorization: Bearer <key>
```

### 2.3 响应体

```json
{
  "labels": ["000001.SZ", "600519.SH"],
  "matrix": [[1.0, 0.4321], [0.4321, 1.0]],
  "window": 90,
  "method": "pearson"
}
```

| 字段 | 说明 |
|---|---|
| `labels` | 资产标签数组（用户原始书写，字典序） |
| `matrix` | N×N 对称矩阵；对角线恒为 `1.0`；非对角保留 4 位小数；计算得 NaN 时落为 `0.0` |
| `window` / `method` | 回显实际使用的窗口与方法 |

> **契约差异提示**：参考实现 `frontend/src/lib/api.ts` 的 `CorrelationResponse` 只声明了 `labels` 与 `matrix` 两个字段（`window`/`method` 被前端忽略）。IM 视图层可按需消费后两个字段，但应对缺失保持容忍。

### 2.4 计算语义与数据陷阱（必须向用户传达）

- **不做前向填充**：收益率用 `pct_change(fill_method=None)` 计算（PR #873 语义）。停牌交易日**不会产生伪造的 0% 收益**——该会话直接从收益序列中剔除。
- **对齐方式**：所有序列先归一化到"日期 0 点"（跨市场时区差异被抹平），再按**内连接**对齐、`dropna()`。即矩阵只基于所有资产**共同有数据**的交易日；窗口内重叠天数不足 2 → `400 Not enough data points to compute correlation`。
- **窗口语义**：`days` 是"对齐后收益序列的最后 N 行"，不是日历天数。后端实际取数范围是 `days + 60` 个日历日（为周末/假日与对齐损耗预留缓冲）。
- 矩阵是**整窗口的单一静态值**（非滚动时间序列）；时间维度见 §3 的 regime 端点。

## 3. GET /correlation/regime — 机制时间线

### 3.1 查询参数（全部有默认值，`codes` 除外）

| 参数 | 默认 | 约束（源码值） | 含义 |
|---|---|---|---|
| `codes` | — | 必填；2–20 个（同 §2.1） | 资产代码 |
| `days` | `90` | `ge=30, le=365` | 返回的时间线条数（日 bar） |
| `corr_window` | `60` | `ge=5, le=250` | 滚动两两相关的窗口（bar 数） |
| `edge_threshold` | `0.5` | `gt=0.0, lt=1.0` | 一对资产计为"边"的 \|ρ\| 阈值 |
| `smooth_window` | `5` | `ge=1, le=60` | 密度序列的**尾随**平滑窗口（bar） |
| `enter_threshold` | `0.65` | `gt=0.0, lt=1.0` | 平滑密度进入 FUSED 的阈值 |
| `exit_threshold` | `0.45` | `gt=0.0, lt=1.0`，且必须 `< enter_threshold`（否则 `400 exit_threshold must be below enter_threshold`） | 退出 FUSED 的阈值 |

代码形式、回退链、标签规则与 `/correlation` 完全相同（共用 `_fetch_price_series`）。取数缓冲为 `days + corr_window + 90` 个日历日，保证返回窗口内每个 bar 背后都有完整的 `corr_window` 历史。

### 3.2 响应体（CorrelationRegimeResponse，逐字段）

参考实现类型：`frontend/src/lib/api.ts` `CorrelationRegimeResponse` / `RegimeEpisode`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `labels` | `string[]` | 参与计算的资产标签（对齐后收益帧的列名，字典序） |
| `dates` | `string[]` | 每个 bar 的日期，格式 `YYYY-MM-DD` |
| `density` | `(number \| null)[]` | 每 bar 的边缘密度 ∈ [0,1]：滚动窗口内 \|ρ\| ≥ `edge_threshold` 的资产对占比。warmup 期为 `null`；数值保留 4 位小数 |
| `smoothed` | `(number \| null)[]` | 密度的**尾随均值**平滑（因果，绝不居中——不读未来）；同样可为 `null`，4 位小数 |
| `fused` | `number[]` | 每 bar 0/1 状态：平滑密度 ≥ `enter_threshold` 进入 FUSED，回落至 ≤ `exit_threshold` 退出（施密特触发器式滞回，死区抑制抖动） |
| `episodes` | `RegimeEpisode[]` | 连续 FUSED 区间：`{start: string, end: string \| null}`；`end` 为最后观测到 FUSED 的日期；**若最后一根 bar 仍 FUSED，`end` 为 `null`**（区间进行中） |
| `params` | `object` | 回显 `{days, corr_window, edge_threshold, smooth_window, enter_threshold, exit_threshold}` |

数组长度关系：`dates.length == density.length == smoothed.length == fused.length`，且等于请求的 `days`（数据不足时更短）。

### 3.3 语义定性（文案必须准确）

- FUSED episodes 是**描述性风险上下文**（"市场何时拧成一股"），**不是交易信号**——源码 docstring 与 README 均如此定性（`backtest/regime.py` 首注、`system_routes.py` 路由 docstring）。IM 视图层的文案不得暗示买卖方向。
- 状态机在**裁剪窗口之前**对全历史运行，因此返回窗口第一根 bar 的 FUSED 状态反映的是完整历史（不是窗口内冷启动）。
- 数据对齐与停牌处理与 `/correlation` 相同（§2.4）：`fill_method=None` + 内连接，停牌会话被排除而非填 0。

## 4. 错误响应汇总

| 状态 | 触发条件 | detail 示例 |
|---|---|---|
| `400` | 代码数 <2 / >20、method 非法、取数 <2 资产、无重叠数据、数据点不足、阈值关系非法 | `At least 2 asset codes required`；`No overlapping return data between assets. Date ranges: ...` |
| `401/403` | 鉴权失败（00 篇 §3） | `Invalid or missing API key` |
| `422` | `days` 等整数参数越界（FastAPI Query 约束） | FastAPI 校验包体 |
| `429` | 超出 30 次/分钟/IP 共享预算 | `Rate limit exceeded, try again later` |
| `500` | 计算内部异常 | `Correlation computation failed` / `Regime timeline computation failed` |

错误包体读取顺序遵循 00 篇 §4：`body.detail || body.message`。

## 5. 参考实现消费方式（`frontend/src/pages/Correlation.tsx`）

| 行为 | 参考实现做法 | 对 IM 视图层的启示 |
|---|---|---|
| 窗口选择 | 固定档位 `WINDOWS = [30, 60, 90, 180, 365]`，默认 90 | 档位均在后端 `7–365` 合法区间内 |
| 方法选择 | `pearson` / `spearman` 二选一，默认 `pearson` | — |
| regime 开关 | 复选框 opt-in；勾选后才并行请求 `getCorrelation` + `getCorrelationRegime`（`Promise.all`） | regime 端点参数更贵（取数缓冲更大），不要默认无条件调用 |
| regime 调用参数 | 只传 `codes` 与 `days`，其余 6 个参数用后端默认值 | 如需暴露高级参数，注意 §3.1 的约束与阈值关系 |
| 竞态防护 | 请求代际计数（`requestGeneration`），过期响应丢弃 | 两端点延迟较高（现取现算 + 网络取数），必须做 |
| 渲染 | `RegimeTimeline` 组件（height=260）置于 `CorrelationMatrix` 热图（height=520）上方；均经 ECharts 出图（参考实现仅注册 Candlestick/Line/Bar/Heatmap 四种 series，00 篇 §6） | IM 视图层可自由选型 |

**性能提示**：两端点均为**请求时现算**且要真实拉取行情，无服务端缓存；20 资产 × 365 天的 regime 请求是重操作。建议 IM 视图层对相同参数做客户端缓存，并把 429 视为"稍后重试"而非错误。

错误响应示例（取数不足）：

```json
{
  "detail": "Could not fetch price data for at least 2 assets. Fetched: ['AAPL']"
}
```

## 6. 集成核对清单

- [ ] 两个端点都带 Bearer 头（配置 key 时）；401/403 按 00 篇 §4 映射提示。
- [ ] `codes` 逗号分隔、URL 编码（参考实现对整个 codes 串 `encodeURIComponent`）。
- [ ] 消费 `labels` 时记住它是**用户原始书写 + 字典序**，与请求顺序无关——矩阵下标必须以响应 `labels` 为准。
- [ ] `density`/`smoothed` 数组允许 `null`（warmup），渲染前过滤；`episodes[].end` 允许 `null`（进行中区间）。
- [ ] 对数值字段做 `Number.isFinite` 防御（00 篇 §4 的严格 JSON 约定）。
- [ ] 文案不暗示交易信号：FUSED 是描述性风险上下文。
- [ ] 客户端限流/缓存：共享预算 30 次/分钟/IP，429 时退避重试。
- [ ] 停牌语义向用户说明：矩阵只基于共同交易日，停牌会话被排除而非计 0%。
