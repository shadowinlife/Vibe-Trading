# stk_moneyflow_ths

同花顺口径资金流向，与 stk_moneyflow（东财口径）互为交叉验证

| 项 | 值 |
|---|---|
| 领域 | `flow` |
| 资产类别 | `stock` |
| 粒度 | 标的-交易日 |
| 主键 | `ts_code`, `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-10-28（落后 1 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 2,284,193 |
| 列数 | 13 |

## 注意事项

- 涨跌幅列名是 pct_change，与 stk_moneyflow 不同口径

## 相关表

[`stk_moneyflow`](stk_moneyflow.md)

## 列清单（13 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `trade_date` | `Date` | — |
| 2 | `ts_code` | `String` | — |
| 3 | `name` | `String` | — |
| 4 | `pct_change` | `Float64` | — |
| 5 | `latest` | `Float64` | — |
| 6 | `net_amount` | `Float64` | — |
| 7 | `net_d5_amount` | `Float64` | — |
| 8 | `buy_lg_amount` | `Float64` | — |
| 9 | `buy_lg_amount_rate` | `Float64` | — |
| 10 | `buy_md_amount` | `Float64` | — |
| 11 | `buy_md_amount_rate` | `Float64` | — |
| 12 | `buy_sm_amount` | `Float64` | — |
| 13 | `buy_sm_amount_rate` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
