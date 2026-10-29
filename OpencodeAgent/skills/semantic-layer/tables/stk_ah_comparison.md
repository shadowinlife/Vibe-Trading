# stk_ah_comparison

A/H 股比价（同一公司的 A 股与 H 股价格、溢价率）

| 项 | 值 |
|---|---|
| 领域 | `quote` |
| 资产类别 | `stock` |
| 粒度 | 标的-交易日 |
| 主键 | `ts_code`, `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-10-28（落后 1 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 50,851 |
| 列数 | 11 |

## 注意事项

- pct 列名为 pct_chg

## 相关表

[`stk_info`](stk_info.md)

## 列清单（11 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `hk_code` | `String` | — |
| 2 | `ts_code` | `String` | — |
| 3 | `trade_date` | `Date` | — |
| 4 | `hk_name` | `String` | — |
| 5 | `hk_pct_chg` | `Float64` | — |
| 6 | `hk_close` | `Float64` | — |
| 7 | `name` | `String` | — |
| 8 | `close` | `Float64` | — |
| 9 | `pct_chg` | `Float64` | — |
| 10 | `ah_comparison` | `Float64` | — |
| 11 | `ah_premium` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
