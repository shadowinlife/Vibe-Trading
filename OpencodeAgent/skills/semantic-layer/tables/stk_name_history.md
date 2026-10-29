# stk_name_history

股票曾用名与变更原因，做名称模糊匹配前先确认不是旧名

| 项 | 值 |
|---|---|
| 领域 | `basic` |
| 资产类别 | `stock` |
| 粒度 | 标的-报告期 |
| 主键 | `ts_code`, `end_date` |
| 日期列 | `end_date`（String） ⚠ String，过滤需 `toDate()` |
| 数据水位 | — |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | 水位未知 |
| 行数 | 10,000 |
| 列数 | 6 |

## 注意事项

- end_date 为 String（YYYYMMDD），按日期过滤需 toDate()

## 相关表

[`stk_info`](stk_info.md)

## 列清单（6 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `name` | `String` | — |
| 3 | `start_date` | `String` | — |
| 4 | `end_date` | `String` | — |
| 5 | `ann_date` | `String` | — |
| 6 | `change_reason` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
