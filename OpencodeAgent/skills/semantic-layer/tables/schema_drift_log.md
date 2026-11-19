# schema_drift_log

上游字段漂移记录（列增删改），解释「昨天还能查的列今天没了」

| 项 | 值 |
|---|---|
| 领域 | `ops` |
| 资产类别 | `market` |
| 粒度 | 事件 |
| 主键 | `event_time`, `target_table` |
| 日期列 | —（快照/无日期列） |
| 数据水位 | — |
| 新鲜度 SLA | 7 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | 快照 |
| 行数 | 180 |
| 列数 | 5 |

## 相关表

[`table_sync_state`](table_sync_state.md)

## 列清单（5 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `event_time` | `DateTime` | — |
| 2 | `target_table` | `String` | — |
| 3 | `event_type` | `String` | — |
| 4 | `column_name` | `String` | — |
| 5 | `detail` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
