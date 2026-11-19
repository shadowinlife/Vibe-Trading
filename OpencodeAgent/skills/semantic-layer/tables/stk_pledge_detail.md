# stk_pledge_detail

股权质押逐笔明细（质押方、质权方、数量）

| 项 | 值 |
|---|---|
| 领域 | `event` |
| 资产类别 | `stock` |
| 粒度 | 标的-公告-质押笔 |
| 主键 | `ts_code`, `ann_date`, `pledgor` |
| 日期列 | `ann_date`（String） ⚠ String，过滤需 `toDate()` |
| 数据水位 | 2026-04-22（落后 190 自然日） |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 271,768 |
| 列数 | 14 |

## 注意事项

- 日期列为 String，含 99990101 哨兵值，必须过滤

## 相关表

[`stk_pledge_stat`](stk_pledge_stat.md)

## 列清单（14 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `String` | — |
| 3 | `holder_name` | `String` | — |
| 4 | `pledge_amount` | `Float64` | — |
| 5 | `start_date` | `String` | — |
| 6 | `end_date` | `String` | — |
| 7 | `is_release` | `String` | — |
| 8 | `release_date` | `String` | — |
| 9 | `pledgor` | `String` | — |
| 10 | `holding_amount` | `Float64` | — |
| 11 | `pledged_amount` | `Float64` | — |
| 12 | `p_total_ratio` | `Float64` | — |
| 13 | `h_total_ratio` | `Float64` | — |
| 14 | `is_buyback` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
