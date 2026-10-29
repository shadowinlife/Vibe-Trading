# fin_audit

审计意见类型（标准无保留/保留/无法表示等）

| 项 | 值 |
|---|---|
| 领域 | `finance` |
| 资产类别 | `stock` |
| 粒度 | 标的-报告期 |
| 主键 | `ts_code`, `end_date` |
| 日期列 | `end_date`（Date） |
| 数据水位 | 2025-07-05（落后 481 自然日） |
| 新鲜度 SLA | 365 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 7,938 |
| 列数 | 7 |

## 注意事项

- 2025-07 后停更，先看水位

## 相关表

[`fin_income`](fin_income.md)

## 列清单（7 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `Date` | — |
| 3 | `end_date` | `Date` | — |
| 4 | `audit_result` | `String` | — |
| 5 | `audit_fees` | `Float64` | — |
| 6 | `audit_agency` | `String` | — |
| 7 | `audit_sign` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
