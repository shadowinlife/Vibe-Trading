# stk_st_daily

ST / *ST / 退市风险状态日历，做可交易池过滤

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
| 行数 | 345,156 |
| 列数 | 5 |

## 相关表

[`stk_info`](stk_info.md), [`stk_limit`](stk_limit.md)

## 列清单（5 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `name` | `String` | — |
| 3 | `trade_date` | `Date` | — |
| 4 | `type` | `String` | — |
| 5 | `type_name` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
