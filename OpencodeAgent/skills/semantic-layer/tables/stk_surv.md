# stk_surv

机构调研记录（参与机构、调研日期、接待人员）

| 项 | 值 |
|---|---|
| 领域 | `event` |
| 资产类别 | `stock` |
| 粒度 | 标的-调研活动 |
| 主键 | `ts_code`, `surv_date` |
| 日期列 | `surv_date`（Date） |
| 数据水位 | — |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | 水位未知 |
| 行数 | 199,200 |
| 列数 | 9 |

## 相关表

[`stk_info`](stk_info.md)

## 列清单（9 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `name` | `String` | — |
| 3 | `surv_date` | `Date` | — |
| 4 | `fund_visitors` | `String` | — |
| 5 | `rece_place` | `String` | — |
| 6 | `rece_mode` | `String` | — |
| 7 | `rece_org` | `String` | — |
| 8 | `org_type` | `String` | — |
| 9 | `comp_rece` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
