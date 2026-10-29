# stk_managers

董监高人员与任职、薪酬

| 项 | 值 |
|---|---|
| 领域 | `event` |
| 资产类别 | `stock` |
| 粒度 | 标的-任职期 |
| 主键 | `ts_code`, `ann_date`, `name` |
| 日期列 | `ann_date`（String） ⚠ String，过滤需 `toDate()` |
| 数据水位 | 2025-12-30（落后 303 自然日） |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 434,151 |
| 列数 | 11 |

## 注意事项

- ann_date 为 String；2025-12 后停更

## 相关表

[`stk_company`](stk_company.md)

## 列清单（11 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `String` | — |
| 3 | `name` | `String` | — |
| 4 | `gender` | `String` | — |
| 5 | `lev` | `String` | — |
| 6 | `title` | `String` | — |
| 7 | `edu` | `String` | — |
| 8 | `national` | `String` | — |
| 9 | `birthday` | `String` | — |
| 10 | `begin_date` | `String` | — |
| 11 | `end_date` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
