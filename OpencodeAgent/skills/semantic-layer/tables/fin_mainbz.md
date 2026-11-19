# fin_mainbz

主营业务构成（分产品/分地区收入与毛利率）

| 项 | 值 |
|---|---|
| 领域 | `finance` |
| 资产类别 | `stock` |
| 粒度 | 标的-报告期-业务 |
| 主键 | `ts_code`, `end_date`, `bz_item` |
| 日期列 | `end_date`（String） ⚠ String，过滤需 `toDate()` |
| 数据水位 | 2025-12-31（落后 302 自然日） |
| 新鲜度 SLA | 365 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 1,492,850 |
| 列数 | 9 |

## 注意事项

- end_date 为 String（YYYYMMDD）；149 万行，按 ts_code 过滤

## 相关表

[`fin_income`](fin_income.md)

## 列清单（9 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `end_date` | `String` | — |
| 3 | `bz_item` | `String` | — |
| 4 | `bz_code` | `String` | — |
| 5 | `bz_sales` | `Float64` | — |
| 6 | `bz_profit` | `Float64` | — |
| 7 | `bz_cost` | `Float64` | — |
| 8 | `curr_type` | `String` | — |
| 9 | `_param_type` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
