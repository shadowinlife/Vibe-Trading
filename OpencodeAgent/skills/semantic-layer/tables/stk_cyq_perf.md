# stk_cyq_perf

筹码分布派生指标（获利比例、平均成本、90/70 成本区间与集中度）

| 项 | 值 |
|---|---|
| 领域 | `flow` |
| 资产类别 | `stock` |
| 粒度 | 标的-交易日 |
| 主键 | `ts_code`, `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-07-27（落后 94 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 9,368,526 |
| 列数 | 11 |

## 注意事项

- 无官方原始源，属派生量；2026-07-27 后停更，先看水位

## 相关表

[`stk_cyq_chips`](stk_cyq_chips.md), [`stk_factor_pro`](stk_factor_pro.md)

## 列清单（11 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `trade_date` | `Date` | — |
| 3 | `his_low` | `Float64` | — |
| 4 | `his_high` | `Float64` | — |
| 5 | `cost_5pct` | `Float64` | — |
| 6 | `cost_15pct` | `Float64` | — |
| 7 | `cost_50pct` | `Float64` | — |
| 8 | `cost_85pct` | `Float64` | — |
| 9 | `cost_95pct` | `Float64` | — |
| 10 | `weight_avg` | `Float64` | — |
| 11 | `winner_rate` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
