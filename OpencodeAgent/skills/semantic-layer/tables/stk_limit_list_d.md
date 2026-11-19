# stk_limit_list_d

涨跌停名单（涨停/跌停/炸板），做情绪与连板统计

| 项 | 值 |
|---|---|
| 领域 | `quote` |
| 资产类别 | `stock` |
| 粒度 | 标的-交易日 |
| 主键 | `ts_code`, `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2024-05-30（落后 882 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 25,191 |
| 列数 | 18 |

## 注意事项

- 2024-05-30 后停更；新数据请用 stk_limit 自行判定

## 相关表

[`stk_limit`](stk_limit.md)

## 列清单（18 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `trade_date` | `Date` | — |
| 2 | `ts_code` | `String` | — |
| 3 | `industry` | `String` | — |
| 4 | `name` | `String` | — |
| 5 | `close` | `Float64` | — |
| 6 | `pct_chg` | `Float64` | — |
| 7 | `amount` | `Float64` | — |
| 8 | `limit_amount` | `Float64` | — |
| 9 | `float_mv` | `Float64` | — |
| 10 | `total_mv` | `Float64` | — |
| 11 | `turnover_ratio` | `Float64` | — |
| 12 | `fd_amount` | `Float64` | — |
| 13 | `first_time` | `String` | — |
| 14 | `last_time` | `String` | — |
| 15 | `open_times` | `Int64` | — |
| 16 | `up_stat` | `String` | — |
| 17 | `limit_times` | `Float64` | — |
| 18 | `limit` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
