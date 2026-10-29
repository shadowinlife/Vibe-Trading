# fin_forecast

业绩预告（预增/预减/扭亏类型与幅度）

| 项 | 值 |
|---|---|
| 领域 | `finance` |
| 资产类别 | `stock` |
| 粒度 | 标的-报告期 |
| 主键 | `ts_code`, `end_date` |
| 日期列 | `end_date`（Date） |
| 数据水位 | 2026-10-24（落后 5 自然日） |
| 新鲜度 SLA | 120 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 121,648 |
| 列数 | 13 |

## 注意事项

- end_date 可能是未来报告期，做「已披露」筛选要加 ann_date <= today()

## 相关表

[`fin_express`](fin_express.md), [`fin_income`](fin_income.md)

## 列清单（13 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `Date` | — |
| 3 | `end_date` | `Date` | — |
| 4 | `type` | `String` | — |
| 5 | `p_change_min` | `Float64` | — |
| 6 | `p_change_max` | `Float64` | — |
| 7 | `net_profit_min` | `Float64` | — |
| 8 | `net_profit_max` | `Float64` | — |
| 9 | `last_parent_net` | `Float64` | — |
| 10 | `first_ann_date` | `Date` | — |
| 11 | `summary` | `String` | — |
| 12 | `change_reason` | `String` | — |
| 13 | `update_flag` | `Nullable(String)` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
