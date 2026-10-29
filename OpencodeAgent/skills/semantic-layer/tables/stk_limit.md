# stk_limit

每日涨停价/跌停价，用于判断封板、连板与可成交性

| 项 | 值 |
|---|---|
| 领域 | `quote` |
| 资产类别 | `stock` |
| 粒度 | 标的-交易日 |
| 主键 | `ts_code`, `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-10-28（落后 1 自然日） |
| 新鲜度 SLA | 2 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 18,268,444 |
| 列数 | 4 |

## 注意事项

- 1826 万行，按 ts_code + 日期区间过滤，不要全表扫

## 相关表

[`stk_factor_pro`](stk_factor_pro.md), [`stk_st_daily`](stk_st_daily.md)

## 列清单（4 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `trade_date` | `Date` | — |
| 2 | `ts_code` | `String` | — |
| 3 | `up_limit` | `Float64` | — |
| 4 | `down_limit` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
