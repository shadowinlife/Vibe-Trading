# idx_sw_member_all

申万行业成分股全量快照，行业→个股展开的正确入口

| 项 | 值 |
|---|---|
| 领域 | `basic` |
| 资产类别 | `board` |
| 粒度 | 行业-成分股快照 |
| 主键 | `l3_code`, `ts_code` |
| 日期列 | —（快照/无日期列） |
| 数据水位 | — |
| 新鲜度 SLA | 90 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | 快照 |
| 行数 | 5,922 |
| 列数 | 11 |

## 相关表

[`idx_sw_classify`](idx_sw_classify.md)

## 列清单（11 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `l1_code` | `String` | — |
| 2 | `l1_name` | `String` | — |
| 3 | `l2_code` | `String` | — |
| 4 | `l2_name` | `String` | — |
| 5 | `l3_code` | `String` | — |
| 6 | `l3_name` | `String` | — |
| 7 | `ts_code` | `String` | — |
| 8 | `name` | `String` | — |
| 9 | `in_date` | `String` | — |
| 10 | `out_date` | `Int32` | — |
| 11 | `is_new` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
