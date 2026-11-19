# stk_share_float

限售股解禁（解禁数量、占比、股东类型），判断供给冲击

| 项 | 值 |
|---|---|
| 领域 | `event` |
| 资产类别 | `stock` |
| 粒度 | 标的-解禁日 |
| 主键 | `ts_code`, `ann_date` |
| 日期列 | `ann_date`（String） ⚠ String，过滤需 `toDate()` |
| 数据水位 | 2026-10-28（落后 1 自然日） |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 426,202 |
| 列数 | 7 |

## 注意事项

- ann_date 为 String（YYYYMMDD）

## 相关表

[`stk_info`](stk_info.md)

## 列清单（7 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `String` | — |
| 3 | `float_date` | `String` | — |
| 4 | `float_share` | `Float64` | — |
| 5 | `float_ratio` | `Float64` | — |
| 6 | `holder_name` | `String` | — |
| 7 | `share_type` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
