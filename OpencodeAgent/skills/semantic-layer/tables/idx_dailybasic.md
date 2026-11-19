# idx_dailybasic

指数估值（pe / pe_ttm / pb / 换手率），唯一提供指数市盈率的表

| 项 | 值 |
|---|---|
| 领域 | `factor` |
| 资产类别 | `index` |
| 粒度 | 标的-交易日 |
| 主键 | `ts_code`, `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2025-07-04（落后 482 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 否 |
| 可估值 | 是 |
| 状态 | ⚠ 停更 |
| 行数 | 11,976 |
| 列数 | 12 |

## 注意事项

- 2026-10-29 实测：仅 12 个指数代码，不含 000688.SH（科创50）
- 2026-10-29 实测停在 2025-07-04；不要把它当全指数覆盖

## 示例

```sql
SELECT trade_date, pe_ttm, pb FROM idx_dailybasic WHERE ts_code='000300.SH' ORDER BY trade_date DESC LIMIT 5
```


## 相关表

[`idx_factor_pro`](idx_factor_pro.md), [`idx_info`](idx_info.md)

## 列清单（12 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `trade_date` | `Date` | — |
| 3 | `total_mv` | `Float64` | — |
| 4 | `float_mv` | `Float64` | — |
| 5 | `total_share` | `Float64` | — |
| 6 | `float_share` | `Float64` | — |
| 7 | `free_share` | `Float64` | — |
| 8 | `turnover_rate` | `Float64` | — |
| 9 | `turnover_rate_f` | `Float64` | — |
| 10 | `pe` | `Float64` | — |
| 11 | `pe_ttm` | `Float64` | — |
| 12 | `pb` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
