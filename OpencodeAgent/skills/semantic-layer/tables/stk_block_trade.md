# stk_block_trade

大宗交易逐笔（成交价、成交量、溢价率、买卖营业部）

| 项 | 值 |
|---|---|
| 领域 | `flow` |
| 资产类别 | `stock` |
| 粒度 | 成交笔-交易日 |
| 主键 | `ts_code`, `trade_date`, `buyer`, `seller` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-07-22（落后 99 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 269,165 |
| 列数 | 7 |

## 注意事项

- 2026-07-22 后停更，先看水位

## 相关表

[`stk_factor_pro`](stk_factor_pro.md)

## 列清单（7 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `trade_date` | `Date` | — |
| 3 | `price` | `Float64` | — |
| 4 | `vol` | `Float64` | — |
| 5 | `amount` | `Float64` | — |
| 6 | `buyer` | `String` | — |
| 7 | `seller` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
