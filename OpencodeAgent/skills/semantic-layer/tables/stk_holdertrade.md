# stk_holdertrade

股东/高管增减持（方向、股数、均价、占比）

| 项 | 值 |
|---|---|
| 领域 | `event` |
| 资产类别 | `stock` |
| 粒度 | 标的-公告-股东 |
| 主键 | `ts_code`, `ann_date`, `holder_name` |
| 日期列 | `ann_date`（String） ⚠ String，过滤需 `toDate()` |
| 数据水位 | 2026-07-22（落后 99 自然日） |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 4,761,000 |
| 列数 | 11 |

## 注意事项

- ann_date 为 String；2026-07-22 后停更

## 相关表

[`stk_holdernumber`](stk_holdernumber.md)

## 列清单（11 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `String` | — |
| 3 | `holder_name` | `String` | — |
| 4 | `holder_type` | `String` | — |
| 5 | `in_de` | `String` | — |
| 6 | `change_vol` | `Float64` | — |
| 7 | `change_ratio` | `Float64` | — |
| 8 | `after_share` | `Float64` | — |
| 9 | `after_ratio` | `Float64` | — |
| 10 | `avg_price` | `Float64` | — |
| 11 | `total_share` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
