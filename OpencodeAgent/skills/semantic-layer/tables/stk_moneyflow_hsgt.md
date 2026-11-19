# stk_moneyflow_hsgt

沪深港通资金（北向/南向）

| 项 | 值 |
|---|---|
| 领域 | `flow` |
| 资产类别 | `market` |
| 粒度 | 交易日 |
| 主键 | `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-07-22（落后 99 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 1,064 |
| 列数 | 7 |

## 单位

| 列 | 单位 |
|---|---|
| `north_money` | 百万元 |
| `south_money` | 百万元 |

## 注意事项

- 2024-08-19 起北向不再披露日度净买入，hgt/sgt/north_money 已是成交额（百万元），不是净流入
- 2026-10-29 实测停在 2026-07-22；引用前先看本表水位行

## 相关表

[`trade_calendar`](trade_calendar.md)

## 列清单（7 列，有注释 7 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `trade_date` | `Date` | source=tushare moneyflow_hsgt; desc=交易日期 |
| 2 | `ggt_ss` | `String` | unit=万元 (10k CNY); caliber=2026-08-12实测与tushare活数据逐位一致; 官方文档"百万元"标注与实测数量级不符, 以实测为准; source=tushare moneyflow_hsgt; desc=港股通(上海)资金流向 |
| 3 | `ggt_sz` | `String` | unit=万元 (10k CNY); caliber=2026-08-12实测与tushare活数据逐位一致; 官方文档"百万元"标注与实测数量级不符, 以实测为准; source=tushare moneyflow_hsgt; desc=港股通(深圳)资金流向 |
| 4 | `hgt` | `String` | unit=万元 (10k CNY); caliber=2026-08-12实测CH与tushare活数据一致(375048.34万元≈37.5亿, 符合北向数量级); 注意工具层clickhouse_fallbacks×100为缺陷(P1.4修复); source=tushare moneyflow_hsgt; desc=沪股通(北向上海)资金流; ambiguous_with=north_money |
| 5 | `sgt` | `String` | unit=万元 (10k CNY); caliber=2026-08-12实测CH与tushare活数据一致; 注意工具层clickhouse_fallbacks×100为缺陷(P1.4修复); source=tushare moneyflow_hsgt; desc=深股通(北向深圳)资金流 |
| 6 | `north_money` | `String` | unit=万元 (10k CNY); caliber=north_money=hgt+sgt, 2026-08-12实测与tushare活数据逐位一致; 注意工具层clickhouse_fallbacks×100为缺陷(P1.4修复); source=tushare moneyflow_hsgt; desc=北向资金; ambiguous_with=south_money |
| 7 | `south_money` | `String` | unit=万元 (10k CNY); caliber=south_money=ggt_ss+ggt_sz, 2026-08-12实测与tushare活数据一致; 注意工具层clickhouse_fallbacks×100为缺陷(P1.4修复); source=tushare moneyflow_hsgt; desc=南向资金; ambiguous_with=north_money |
