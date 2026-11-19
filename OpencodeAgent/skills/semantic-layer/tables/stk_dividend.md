# stk_dividend

分红送股方案与除权除息日，复权因子校验的辅助表

| 项 | 值 |
|---|---|
| 领域 | `event` |
| 资产类别 | `stock` |
| 粒度 | 标的-除权除息日 |
| 主键 | `ts_code`, `end_date` |
| 日期列 | `end_date`（String） ⚠ String，过滤需 `toDate()` |
| 数据水位 | 2026-10-22（落后 7 自然日） |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 118,626 |
| 列数 | 14 |

## 注意事项

- end_date 为 String（YYYYMMDD）

## 相关表

[`stk_factor_pro`](stk_factor_pro.md)

## 列清单（14 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `end_date` | `String` | — |
| 3 | `ann_date` | `String` | — |
| 4 | `div_proc` | `String` | — |
| 5 | `stk_div` | `Float64` | — |
| 6 | `stk_bo_rate` | `Int32` | — |
| 7 | `stk_co_rate` | `Float64` | — |
| 8 | `cash_div` | `Float64` | — |
| 9 | `cash_div_tax` | `Float64` | — |
| 10 | `record_date` | `String` | — |
| 11 | `ex_date` | `String` | — |
| 12 | `pay_date` | `String` | — |
| 13 | `div_listdate` | `String` | — |
| 14 | `imp_ann_date` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
