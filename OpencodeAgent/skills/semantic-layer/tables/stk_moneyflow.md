# stk_moneyflow

个股大中小单资金净流向（主力/超大单/大单/中单/小单）

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
| 行数 | 14,760,896 |
| 列数 | 20 |

## 单位

| 列 | 单位 |
|---|---|
| `buy_elg_amount` | 万元 |
| `net_mf_amount` | 万元 |

## 相关表

[`stk_moneyflow_ths`](stk_moneyflow_ths.md), [`stk_factor_pro`](stk_factor_pro.md)

## 列清单（20 列，有注释 20 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | source=tushare moneyflow; desc=股票代码(ts_code格式, 如000001.SZ) |
| 2 | `trade_date` | `Date` | source=tushare moneyflow; desc=交易日期 |
| 3 | `buy_sm_vol` | `Int64` | unit=lots; caliber=小单(成交额<5万), 基于主动买卖单统计; source=tushare moneyflow; desc=小单买入量; ambiguous_with=buy_sm_amount |
| 4 | `buy_sm_amount` | `Float64` | unit=万元 (ten-thousand CNY); caliber=小单(成交额<5万), 基于主动买卖单统计, 工具层×10000换算为元输出; source=tushare moneyflow; desc=小单买入金额; ambiguous_with=buy_sm_vol |
| 5 | `sell_sm_vol` | `Int64` | unit=lots; caliber=小单(成交额<5万), 基于主动买卖单统计; source=tushare moneyflow; desc=小单卖出量; ambiguous_with=sell_sm_amount |
| 6 | `sell_sm_amount` | `Float64` | unit=万元 (ten-thousand CNY); caliber=小单(成交额<5万), 基于主动买卖单统计, 工具层×10000换算为元输出; source=tushare moneyflow; desc=小单卖出金额; ambiguous_with=sell_sm_vol |
| 7 | `buy_md_vol` | `Int64` | unit=lots; caliber=中单(成交额5万~20万), 基于主动买卖单统计; source=tushare moneyflow; desc=中单买入量; ambiguous_with=buy_md_amount |
| 8 | `buy_md_amount` | `Float64` | unit=万元 (ten-thousand CNY); caliber=中单(成交额5万~20万), 基于主动买卖单统计, 工具层×10000换算为元输出; source=tushare moneyflow; desc=中单买入金额; ambiguous_with=buy_md_vol |
| 9 | `sell_md_vol` | `Int64` | unit=lots; caliber=中单(成交额5万~20万), 基于主动买卖单统计; source=tushare moneyflow; desc=中单卖出量; ambiguous_with=sell_md_amount |
| 10 | `sell_md_amount` | `Float64` | unit=万元 (ten-thousand CNY); caliber=中单(成交额5万~20万), 基于主动买卖单统计, 工具层×10000换算为元输出; source=tushare moneyflow; desc=中单卖出金额; ambiguous_with=sell_md_vol |
| 11 | `buy_lg_vol` | `Int64` | unit=lots; caliber=大单(成交额20万~100万), 基于主动买卖单统计; source=tushare moneyflow; desc=大单买入量; ambiguous_with=buy_lg_amount |
| 12 | `buy_lg_amount` | `Float64` | unit=万元 (ten-thousand CNY); caliber=大单(成交额20万~100万), 基于主动买卖单统计, 工具层×10000换算为元输出; source=tushare moneyflow; desc=大单买入金额; ambiguous_with=buy_lg_vol |
| 13 | `sell_lg_vol` | `Int64` | unit=lots; caliber=大单(成交额20万~100万), 基于主动买卖单统计; source=tushare moneyflow; desc=大单卖出量; ambiguous_with=sell_lg_amount |
| 14 | `sell_lg_amount` | `Float64` | unit=万元 (ten-thousand CNY); caliber=大单(成交额20万~100万), 基于主动买卖单统计, 工具层×10000换算为元输出; source=tushare moneyflow; desc=大单卖出金额; ambiguous_with=sell_lg_vol |
| 15 | `buy_elg_vol` | `Int64` | unit=lots; caliber=特大单(成交额>=100万), 基于主动买卖单统计; source=tushare moneyflow; desc=特大单买入量; ambiguous_with=buy_elg_amount |
| 16 | `buy_elg_amount` | `Float64` | unit=万元 (ten-thousand CNY); caliber=特大单(成交额>=100万), 基于主动买卖单统计, 工具层×10000换算为元输出; source=tushare moneyflow; desc=特大单买入金额; ambiguous_with=buy_elg_vol |
| 17 | `sell_elg_vol` | `Int64` | unit=lots; caliber=特大单(成交额>=100万), 基于主动买卖单统计; source=tushare moneyflow; desc=特大单卖出量; ambiguous_with=sell_elg_amount |
| 18 | `sell_elg_amount` | `Float64` | unit=万元 (ten-thousand CNY); caliber=特大单(成交额>=100万), 基于主动买卖单统计, 工具层×10000换算为元输出; source=tushare moneyflow; desc=特大单卖出金额; ambiguous_with=sell_elg_vol |
| 19 | `net_mf_vol` | `Int64` | unit=lots; source=tushare moneyflow; desc=净流入量; ambiguous_with=net_mf_amount |
| 20 | `net_mf_amount` | `Float64` | unit=万元 (ten-thousand CNY); caliber=工具层×10000换算为元输出; source=tushare moneyflow; desc=净流入额; ambiguous_with=net_mf_vol |
