# table_sync_state

同步状态（每表每维度的 is_sync / 行数 / 错误信息），判断「没数据」还是「没同步」

| 项 | 值 |
|---|---|
| 领域 | `ops` |
| 资产类别 | `market` |
| 粒度 | 表-维度-维度值 |
| 主键 | `target_table`, `dimension_type`, `dimension_value` |
| 日期列 | —（快照/无日期列） |
| 数据水位 | — |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | 快照 |
| 行数 | 158,305 |
| 列数 | 8 |

## 注意事项

- 没有记录 ≠ 同步失败；很可能是该表没被注册进同步体系
- 与 check_freshness 的水位判断互为补充

## 示例

```sql
SELECT target_table, max(updated_at) FROM table_sync_state WHERE is_sync=0 GROUP BY target_table
```


## 相关表

[`trade_calendar`](trade_calendar.md)

## 列清单（8 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `source_table` | `String` | — |
| 2 | `target_table` | `String` | — |
| 3 | `dimension_type` | `String` | — |
| 4 | `dimension_value` | `String` | — |
| 5 | `is_sync` | `UInt8` | — |
| 6 | `rows_written` | `UInt64` | — |
| 7 | `error_message` | `String` | — |
| 8 | `updated_at` | `DateTime` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
