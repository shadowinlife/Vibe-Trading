# stk_holdernumber

股东户数与户均持股，判断筹码集中度变化

| 项 | 值 |
|---|---|
| 领域 | `event` |
| 资产类别 | `stock` |
| 粒度 | 标的-公告期 |
| 主键 | `ts_code`, `end_date` |
| 日期列 | `end_date`（String） ⚠ String，过滤需 `toDate()` |
| 数据水位 | 2026-07-22（落后 99 自然日） |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 8,382,000 |
| 列数 | 4 |

## 注意事项

- ann_date/end_date 为 String；历史数据含非法日期哨兵值，过滤时先 toDateOrNull

## 相关表

[`stk_holdertrade`](stk_holdertrade.md)

## 列清单（4 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `String` | — |
| 3 | `end_date` | `String` | — |
| 4 | `holder_num` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
