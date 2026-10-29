# stk_company

公司概况（注册地、上市日期、主营业务、法人）

| 项 | 值 |
|---|---|
| 领域 | `basic` |
| 资产类别 | `stock` |
| 粒度 | 全量快照 |
| 主键 | `ts_code` |
| 日期列 | —（快照/无日期列） |
| 数据水位 | — |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | 快照 |
| 行数 | 6,359 |
| 列数 | 18 |

## 相关表

[`stk_info`](stk_info.md)

## 列清单（18 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `com_name` | `String` | — |
| 3 | `com_id` | `String` | — |
| 4 | `chairman` | `String` | — |
| 5 | `manager` | `String` | — |
| 6 | `secretary` | `String` | — |
| 7 | `reg_capital` | `Float64` | — |
| 8 | `setup_date` | `String` | — |
| 9 | `province` | `String` | — |
| 10 | `city` | `String` | — |
| 11 | `introduction` | `String` | — |
| 12 | `website` | `String` | — |
| 13 | `email` | `String` | — |
| 14 | `office` | `String` | — |
| 15 | `business_scope` | `String` | — |
| 16 | `employees` | `Float64` | — |
| 17 | `main_business` | `String` | — |
| 18 | `exchange` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
