# stk_repurchase

股份回购进度（预案/实施/完成）与回购数量金额

| 项 | 值 |
|---|---|
| 领域 | `event` |
| 资产类别 | `stock` |
| 粒度 | 标的-公告 |
| 主键 | `ts_code`, `ann_date` |
| 日期列 | `ann_date`（Date） |
| 数据水位 | 2025-07-18（落后 468 自然日） |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 28,073 |
| 列数 | 9 |

## 注意事项

- 2025-07 后停更，先看水位

## 相关表

[`stk_info`](stk_info.md)

## 列清单（9 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `Date` | — |
| 3 | `end_date` | `Date` | — |
| 4 | `proc` | `String` | — |
| 5 | `exp_date` | `String` | — |
| 6 | `vol` | `Float64` | — |
| 7 | `amount` | `Float64` | — |
| 8 | `high_limit` | `Float64` | — |
| 9 | `low_limit` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
