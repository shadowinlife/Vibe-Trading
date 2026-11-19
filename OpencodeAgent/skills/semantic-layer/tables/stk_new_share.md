# stk_new_share

新股发行信息（发行价、发行量、上市日）

| 项 | 值 |
|---|---|
| 领域 | `event` |
| 资产类别 | `stock` |
| 粒度 | 标的-发行 |
| 主键 | `ts_code` |
| 日期列 | —（快照/无日期列） |
| 数据水位 | — |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | 快照 |
| 行数 | 2,000 |
| 列数 | 12 |

## 相关表

[`stk_info`](stk_info.md)

## 列清单（12 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `sub_code` | `Int64` | — |
| 3 | `name` | `String` | — |
| 4 | `ipo_date` | `Date` | — |
| 5 | `issue_date` | `Date` | — |
| 6 | `amount` | `Float64` | — |
| 7 | `market_amount` | `Float64` | — |
| 8 | `price` | `Float64` | — |
| 9 | `pe` | `Float64` | — |
| 10 | `limit_amount` | `Float64` | — |
| 11 | `funds` | `Float64` | — |
| 12 | `ballot` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
