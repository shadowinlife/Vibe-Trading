# stk_rewards

股权激励与员工持股计划明细

| 项 | 值 |
|---|---|
| 领域 | `event` |
| 资产类别 | `stock` |
| 粒度 | 标的-公告-激励对象 |
| 主键 | `ts_code`, `ann_date`, `name` |
| 日期列 | `ann_date`（String） ⚠ String，过滤需 `toDate()` |
| 数据水位 | — |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | 水位未知 |
| 行数 | 3,704,315 |
| 列数 | 7 |

## 注意事项

- ann_date 为 String；数据水位见本表首行

## 相关表

[`stk_managers`](stk_managers.md)

## 列清单（7 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `String` | — |
| 3 | `end_date` | `String` | — |
| 4 | `name` | `String` | — |
| 5 | `title` | `String` | — |
| 6 | `reward` | `Float64` | — |
| 7 | `hold_vol` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
