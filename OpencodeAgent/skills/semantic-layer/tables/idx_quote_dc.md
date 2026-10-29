# idx_quote_dc

板块行情概览（领涨股、涨跌家数、总市值）

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
| 行数 | 314,042 |
| 列数 | 13 |

## 注意事项

- 只含 BK* 板块代码；涨跌幅列名是 pct_change

## 相关表

[`idx_daily_dc`](idx_daily_dc.md)

## 列清单（13 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `trade_date` | `Date` | — |
| 3 | `name` | `String` | — |
| 4 | `leading` | `String` | — |
| 5 | `leading_code` | `String` | — |
| 6 | `pct_change` | `Float64` | — |
| 7 | `leading_pct` | `Float64` | — |
| 8 | `total_mv` | `Float64` | — |
| 9 | `turnover_rate` | `Float64` | — |
| 10 | `up_num` | `Int64` | — |
| 11 | `down_num` | `Int64` | — |
| 12 | `idx_type` | `Nullable(String)` | — |
| 13 | `level` | `Nullable(String)` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
