# stk_ccass_hold

香港中央结算持股（陆股通托管口径）

| 项 | 值 |
|---|---|
| 领域 | `flow` |
| 资产类别 | `stock` |
| 粒度 | 标的-交易日 |
| 主键 | `ts_code`, `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-06-10（落后 141 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 1,370,097 |
| 列数 | 6 |

## 注意事项

- 2026-06-10 后停更

## 相关表

[`stk_hk_hold`](stk_hk_hold.md)

## 列清单（6 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `trade_date` | `Date` | — |
| 2 | `ts_code` | `String` | — |
| 3 | `name` | `String` | — |
| 4 | `shareholding` | `String` | — |
| 5 | `hold_nums` | `String` | — |
| 6 | `hold_ratio` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
