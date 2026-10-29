# stk_pledge_stat

股权质押比例与质押次数（中登周频）

| 项 | 值 |
|---|---|
| 领域 | `event` |
| 资产类别 | `stock` |
| 粒度 | 标的-统计期 |
| 主键 | `ts_code`, `end_date` |
| 日期列 | `end_date`（String） ⚠ String，过滤需 `toDate()` |
| 数据水位 | 2026-04-17（落后 195 自然日） |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 2,407,677 |
| 列数 | 7 |

## 注意事项

- end_date 为 String；中登按周披露，不能按日回补

## 相关表

[`stk_pledge_detail`](stk_pledge_detail.md)

## 列清单（7 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `end_date` | `String` | — |
| 3 | `pledge_count` | `Int64` | — |
| 4 | `unrest_pledge` | `Float64` | — |
| 5 | `rest_pledge` | `Float64` | — |
| 6 | `total_share` | `Float64` | — |
| 7 | `pledge_ratio` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
