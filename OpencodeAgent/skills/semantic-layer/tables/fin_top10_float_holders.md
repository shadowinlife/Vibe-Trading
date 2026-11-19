# fin_top10_float_holders

前十大流通股东持股

| 项 | 值 |
|---|---|
| 领域 | `finance` |
| 资产类别 | `stock` |
| 粒度 | 标的-报告期-股东 |
| 主键 | `ts_code`, `end_date`, `holder_name` |
| 日期列 | `end_date`（Date） |
| 数据水位 | 2026-10-29（落后 0 自然日） |
| 新鲜度 SLA | 120 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 2,150,262 |
| 列数 | 9 |

## 相关表

[`fin_top10_holders`](fin_top10_holders.md)

## 列清单（9 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `Date` | — |
| 3 | `end_date` | `Date` | — |
| 4 | `holder_name` | `String` | — |
| 5 | `hold_amount` | `Float64` | — |
| 6 | `hold_ratio` | `Float64` | — |
| 7 | `hold_float_ratio` | `Float64` | — |
| 8 | `hold_change` | `Float64` | — |
| 9 | `holder_type` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
