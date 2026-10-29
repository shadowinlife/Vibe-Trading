# stk_top_list

龙虎榜上榜个股与上榜原因、买卖额

| 项 | 值 |
|---|---|
| 领域 | `flow` |
| 资产类别 | `stock` |
| 粒度 | 标的-交易日 |
| 主键 | `ts_code`, `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-07-22（落后 99 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 79,663 |
| 列数 | 15 |

## 注意事项

- 列名 pct_change；2026-07-22 后停更

## 相关表

[`stk_top_inst`](stk_top_inst.md)

## 列清单（15 列，有注释 15 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `trade_date` | `Date` | source=tushare top_list; desc=交易日期 |
| 2 | `ts_code` | `String` | source=tushare top_list; desc=股票代码(ts_code格式, 如000001.SZ) |
| 3 | `name` | `String` | source=tushare top_list; desc=股票名称 |
| 4 | `close` | `Float64` | unit=yuan (CNY); adjust=raw; source=tushare top_list; desc=收盘价 |
| 5 | `pct_change` | `Float64` | unit=percent (%); source=tushare top_list; desc=涨跌幅 |
| 6 | `turnover_rate` | `Float64` | unit=percent (%); source=tushare top_list; desc=换手率 |
| 7 | `amount` | `Float64` | unit=yuan (CNY); caliber=文档样例验证为元; source=tushare top_list; desc=总成交额; ambiguous_with=l_amount |
| 8 | `l_sell` | `Float64` | unit=yuan (CNY); source=tushare top_list; desc=龙虎榜卖出额 |
| 9 | `l_buy` | `Float64` | unit=yuan (CNY); source=tushare top_list; desc=龙虎榜买入额 |
| 10 | `l_amount` | `Float64` | unit=yuan (CNY); source=tushare top_list; desc=龙虎榜成交额; ambiguous_with=amount |
| 11 | `net_amount` | `Float64` | unit=yuan (CNY); source=tushare top_list; desc=龙虎榜净买入额; ambiguous_with=net_rate |
| 12 | `net_rate` | `Float64` | unit=percent (%); source=tushare top_list; desc=龙虎榜净买额占比; ambiguous_with=net_amount |
| 13 | `amount_rate` | `Float64` | unit=percent (%); source=tushare top_list; desc=龙虎榜成交额占比 |
| 14 | `float_values` | `Float64` | unit=yuan (CNY); source=tushare top_list; desc=当日流通市值 |
| 15 | `reason` | `String` | source=tushare top_list; desc=上榜理由 |
