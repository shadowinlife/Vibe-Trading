# stk_top_inst

龙虎榜买卖席位（营业部/机构专用），每边仅披露前 5 名

| 项 | 值 |
|---|---|
| 领域 | `flow` |
| 资产类别 | `stock` |
| 粒度 | 标的-交易日-席位 |
| 主键 | `ts_code`, `trade_date`, `exalter` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-07-22（落后 99 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ⚠ 停更 |
| 行数 | 1,446,542 |
| 列数 | 10 |

## 注意事项

- 交易所只披露每边前 5 名，不存在「全席位」数据；2026-07-22 后停更

## 相关表

[`stk_top_list`](stk_top_list.md)

## 列清单（10 列，有注释 10 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `trade_date` | `Date` | source=tushare top_inst; desc=交易日期 |
| 2 | `ts_code` | `String` | source=tushare top_inst; desc=股票代码(ts_code格式, 如000001.SZ) |
| 3 | `exalter` | `String` | source=tushare top_inst; desc=营业部名称(交易席位) |
| 4 | `buy` | `Float64` | unit=yuan (CNY); source=tushare top_inst; desc=买入额; ambiguous_with=buy_rate |
| 5 | `buy_rate` | `Float64` | unit=percent (%); source=tushare top_inst; desc=买入占总成交比例; ambiguous_with=buy |
| 6 | `sell` | `Float64` | unit=yuan (CNY); source=tushare top_inst; desc=卖出额; ambiguous_with=sell_rate |
| 7 | `sell_rate` | `Float64` | unit=percent (%); source=tushare top_inst; desc=卖出占总成交比例; ambiguous_with=sell |
| 8 | `net_buy` | `Float64` | unit=yuan (CNY); source=tushare top_inst; desc=净成交额 |
| 9 | `side` | `String` | source=tushare top_inst; desc=买卖类型(0=买入金额最大的前5名, 1=卖出金额最大的前5名) |
| 10 | `reason` | `String` | source=tushare top_inst; desc=上榜理由 |
