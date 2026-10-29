# fin_express

业绩快报（早于正式财报）

| 项 | 值 |
|---|---|
| 领域 | `finance` |
| 资产类别 | `stock` |
| 粒度 | 标的-报告期 |
| 主键 | `ts_code`, `end_date` |
| 日期列 | `end_date`（Date） |
| 数据水位 | 2026-10-21（落后 8 自然日） |
| 新鲜度 SLA | 120 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 33,478 |
| 列数 | 33 |

## 相关表

[`fin_income`](fin_income.md)

## 列清单（33 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `Date` | — |
| 3 | `end_date` | `Date` | — |
| 4 | `revenue` | `Float64` | — |
| 5 | `operate_profit` | `Float64` | — |
| 6 | `total_profit` | `Float64` | — |
| 7 | `n_income` | `Float64` | — |
| 8 | `total_assets` | `Float64` | — |
| 9 | `total_hldr_eqy_exc_min_int` | `Float64` | — |
| 10 | `diluted_eps` | `Float64` | — |
| 11 | `diluted_roe` | `Float64` | — |
| 12 | `yoy_net_profit` | `Float64` | — |
| 13 | `bps` | `Float64` | — |
| 14 | `yoy_sales` | `Float64` | — |
| 15 | `yoy_op` | `Float64` | — |
| 16 | `yoy_tp` | `Float64` | — |
| 17 | `yoy_dedu_np` | `Float64` | — |
| 18 | `yoy_eps` | `Float64` | — |
| 19 | `yoy_roe` | `Float64` | — |
| 20 | `growth_assets` | `Float64` | — |
| 21 | `yoy_equity` | `Float64` | — |
| 22 | `growth_bps` | `Float64` | — |
| 23 | `or_last_year` | `Float64` | — |
| 24 | `op_last_year` | `Float64` | — |
| 25 | `tp_last_year` | `Float64` | — |
| 26 | `np_last_year` | `Float64` | — |
| 27 | `eps_last_year` | `Float64` | — |
| 28 | `open_net_assets` | `Float64` | — |
| 29 | `open_bps` | `Float64` | — |
| 30 | `perf_summary` | `String` | — |
| 31 | `is_audit` | `Int32` | — |
| 32 | `remark` | `String` | — |
| 33 | `update_flag` | `Nullable(String)` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
