# idx_daily_dc

东方财富板块指数日行情（概念/行业/地域板块，BK 代码）

| 项 | 值 |
|---|---|
| 领域 | `quote` |
| 资产类别 | `board` |
| 粒度 | 标的-交易日 |
| 主键 | `ts_code`, `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-10-28（落后 1 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 1,728,301 |
| 列数 | 13 |

## 注意事项

- 只含 BK* 板块代码，不含 000300.SH 等标准指数；标准指数查 idx_factor_pro
- 涨跌幅列名是 pct_change

## 相关表

[`idx_quote_dc`](idx_quote_dc.md)

## 列清单（13 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `trade_date` | `Date` | — |
| 3 | `close` | `Float64` | — |
| 4 | `open` | `Float64` | — |
| 5 | `high` | `Float64` | — |
| 6 | `low` | `Float64` | — |
| 7 | `change` | `Float64` | — |
| 8 | `pct_change` | `Float64` | — |
| 9 | `vol` | `Float64` | — |
| 10 | `amount` | `Float64` | — |
| 11 | `swing` | `Float64` | — |
| 12 | `turnover_rate` | `Float64` | — |
| 13 | `category` | `Nullable(String)` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
