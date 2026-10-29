# stk_margin

融资融券明细（融资余额、融券余量、偿还量），判断杠杆资金方向

| 项 | 值 |
|---|---|
| 领域 | `flow` |
| 资产类别 | `stock` |
| 粒度 | 标的-交易日 |
| 主键 | `ts_code`, `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-10-28（落后 1 自然日） |
| 新鲜度 SLA | 2 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 6,941,404 |
| 列数 | 11 |

## 单位

| 列 | 单位 |
|---|---|
| `rzye` | 元 |
| `rzmre` | 元 |
| `rqyl` | 股 |

## 相关表

[`stk_factor_pro`](stk_factor_pro.md)

## 列清单（11 列，有注释 11 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | source=tushare margin_detail; desc=股票代码(ts_code格式, 如000001.SZ) |
| 2 | `trade_date` | `Date` | source=tushare margin_detail; desc=交易日期 |
| 3 | `name` | `String` | source=tushare margin_detail; desc=股票名称(20190910后有数据) |
| 4 | `rzye` | `Float64` | unit=yuan (CNY); caliber=本日融资余额=前日融资余额+本日融资买入-本日融资偿还额; source=tushare margin_detail; desc=融资余额; ambiguous_with=rzrqye |
| 5 | `rqye` | `Float64` | unit=yuan (CNY); caliber=本日融券余额=本日融券余量×本日收盘价; source=tushare margin_detail; desc=融券余额; ambiguous_with=rqyl |
| 6 | `rzmre` | `Float64` | unit=yuan (CNY); source=tushare margin_detail; desc=融资买入额 |
| 7 | `rqyl` | `Float64` | unit=股; caliber=本日融券余量=前日融券余量+本日融券卖出量-本日融券买入量-本日现券偿还量; source=tushare margin_detail; desc=融券余量; ambiguous_with=rqye |
| 8 | `rzche` | `Float64` | unit=yuan (CNY); source=tushare margin_detail; desc=融资偿还额 |
| 9 | `rqchl` | `Float64` | unit=股; source=tushare margin_detail; desc=融券偿还量 |
| 10 | `rqmcl` | `Float64` | unit=股/份/手; caliber=标的为股票计股、基金计份、债券计手; source=tushare margin_detail; desc=融券卖出量 |
| 11 | `rzrqye` | `Float64` | unit=yuan (CNY); caliber=融资融券余额=融资余额+融券余额; source=tushare margin_detail; desc=融资融券余额; ambiguous_with=rzye |
