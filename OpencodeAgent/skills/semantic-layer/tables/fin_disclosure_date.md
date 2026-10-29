# fin_disclosure_date

财报预约披露日期与实际披露日期

| 项 | 值 |
|---|---|
| 领域 | `finance` |
| 资产类别 | `stock` |
| 粒度 | 标的-报告期 |
| 主键 | `ts_code`, `end_date` |
| 日期列 | `end_date`（Date） |
| 数据水位 | 2026-07-24（落后 97 自然日） |
| 新鲜度 SLA | 120 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 136,206 |
| 列数 | 5 |

## 相关表

[`fin_income`](fin_income.md)

## 列清单（5 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `Date` | — |
| 3 | `end_date` | `Date` | — |
| 4 | `pre_date` | `String` | — |
| 5 | `actual_date` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
