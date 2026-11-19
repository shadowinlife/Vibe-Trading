# fund_daily

ETF/LOF 日行情（2026-10-29 实测 2829 个基金代码），ETF 价格的唯一权威表

| 项 | 值 |
|---|---|
| 领域 | `fund` |
| 资产类别 | `fund` |
| 粒度 | 标的-交易日 |
| 主键 | `ts_code`, `trade_date` |
| 日期列 | `trade_date`（String） ⚠ String，过滤需 `toDate()` |
| 数据水位 | 2026-10-28（落后 1 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 是 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 3,246,308 |
| 列数 | 11 |

## 单位

| 列 | 单位 |
|---|---|
| `vol` | 手 |
| `amount` | 千元 |

## 注意事项

- trade_date 是 String（YYYYMMDD），日期过滤必须 toDate(trade_date)
- 只有价格，没有净值/IOPV/份额/持仓/估值；净值查 get_fund_nav，持仓查 get_etf_holdings
- 列 COMMENT 未登记，单位按 Tushare fund_daily 口径

## 示例

```sql
SELECT trade_date, close, pct_chg FROM fund_daily WHERE ts_code='588000.SH' ORDER BY trade_date DESC LIMIT 20
```


## 相关表

[`idx_factor_pro`](idx_factor_pro.md), [`idx_info`](idx_info.md)

## 列清单（11 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `trade_date` | `String` | — |
| 3 | `pre_close` | `Float64` | — |
| 4 | `open` | `Float64` | — |
| 5 | `high` | `Float64` | — |
| 6 | `low` | `Float64` | — |
| 7 | `close` | `Float64` | — |
| 8 | `change` | `Float64` | — |
| 9 | `pct_chg` | `Float64` | — |
| 10 | `vol` | `Float64` | — |
| 11 | `amount` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
