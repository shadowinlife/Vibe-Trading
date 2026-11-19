# ClickHouse A 股数据目录（ashare）

> 自动生成，请勿手改。来源：`schema/clickhouse/metadata.json` + `catalog/catalog.yaml`。
> 快照日期 2026-10-29 · 57 张表 · 1351 列（有注释 444）
> 其中 16 张表落后于各自的 SLA，已在表中标「⚠ 停更」。

## 选表速查

| 你要查什么 | 用哪张表 |
|---|---|
| 个股日行情 / 估值 / 复权价 / 技术指标 | `stk_factor_pro` |
| ETF / LOF 日行情 | `fund_daily` |
| 指数日行情 / 技术指标 | `idx_factor_pro` |
| 指数估值（PE/PB） | `idx_dailybasic`（仅 12 个代码且已停更，先读 RULES R3） |
| 指数成分与权重 | `idx_weight`（月度快照，非全成分） |
| 板块行情（BK 代码） | `idx_daily_dc` / `idx_quote_dc` |
| 个股基础信息 / 代码解析 | `stk_info`；指数用 `idx_info` |
| 交易日历 / 应有最新交易日 | `trade_calendar` |
| 同步失败 / 是否脱管 | `table_sync_state` |

> 取数前若涉及 ETF↔指数、估值降级、空结果、范围过大，**必须先读 [RULES.md](RULES.md)**。

## basic — 标的基础信息（股票/指数/板块/交易日历）

| 表 | 粒度 | 水位 | 状态 | 用途 | 行数 |
|---|---|---|---|---|---:|
| [`idx_info`](tables/idx_info.md) | 全量快照 | — | 快照 | 指数基础信息（名称、发布机构、基期、成分数），指数代码↔名称解析 | 8,000 |
| [`idx_sw_classify`](tables/idx_sw_classify.md) | 行业层级字典 | — | 快照 | 申万行业分类字典（L1/L2/L3 层级与代码） | 359 |
| [`idx_sw_member_all`](tables/idx_sw_member_all.md) | 行业-成分股快照 | — | 快照 | 申万行业成分股全量快照，行业→个股展开的正确入口 | 5,922 |
| [`stk_bse_mapping`](tables/stk_bse_mapping.md) | 全量快照 | — | 快照 | 北交所旧代码与新代码映射；查询北交所历史代码前先过这张表 | 248 |
| [`stk_company`](tables/stk_company.md) | 全量快照 | — | 快照 | 公司概况（注册地、上市日期、主营业务、法人） | 6,359 |
| [`stk_hm_list`](tables/stk_hm_list.md) | 全量快照 | — | 快照 | 沪深港通标的名单 | 110 |
| [`stk_info`](tables/stk_info.md) | 全量快照 | — | 快照 | A 股股票基础信息（名称、行业、上市状态、实控人），代码↔名称解析的第一站 | 5,576 |
| [`stk_name_history`](tables/stk_name_history.md) | 标的-报告期 | — | 水位未知 | 股票曾用名与变更原因，做名称模糊匹配前先确认不是旧名 | 10,000 |
| [`trade_calendar`](tables/trade_calendar.md) | 全量快照 | — | 快照 | 交易日历（2000-2030），判断「最新应有交易日」「是否休市」的唯一依据 | 13,527 |

## quote — 行情与量价

| 表 | 粒度 | 水位 | 状态 | 用途 | 行数 |
|---|---|---|---|---|---:|
| [`idx_daily_dc`](tables/idx_daily_dc.md) | 标的-交易日 | 2026-10-28 | ok | 东方财富板块指数日行情（概念/行业/地域板块，BK 代码） | 1,728,301 |
| [`idx_quote_dc`](tables/idx_quote_dc.md) | 标的-交易日 | 2026-10-28 | ok | 板块行情概览（领涨股、涨跌家数、总市值） | 314,042 |
| [`stk_ah_comparison`](tables/stk_ah_comparison.md) | 标的-交易日 | 2026-10-28 | ok | A/H 股比价（同一公司的 A 股与 H 股价格、溢价率） | 50,851 |
| [`stk_limit`](tables/stk_limit.md) | 标的-交易日 | 2026-10-28 | ok | 每日涨停价/跌停价，用于判断封板、连板与可成交性 | 18,268,444 |
| [`stk_limit_list_d`](tables/stk_limit_list_d.md) | 标的-交易日 | 2024-05-30 | ⚠ 停更 | 涨跌停名单（涨停/跌停/炸板），做情绪与连板统计 | 25,191 |
| [`stk_st_daily`](tables/stk_st_daily.md) | 标的-交易日 | 2026-10-28 | ok | ST / *ST / 退市风险状态日历，做可交易池过滤 | 345,156 |
| [`stk_suspend`](tables/stk_suspend.md) | 标的-交易日 | 2026-10-28 | ok | 停复牌日历；回测遇停牌应跳过而不是按前收盘成交 | 641,789 |

## factor — 因子与估值

| 表 | 粒度 | 水位 | 状态 | 用途 | 行数 |
|---|---|---|---|---|---:|
| [`idx_dailybasic`](tables/idx_dailybasic.md) | 标的-交易日 | 2025-07-04 | ⚠ 停更 | 指数估值（pe / pe_ttm / pb / 换手率），唯一提供指数市盈率的表 | 11,976 |
| [`idx_factor_pro`](tables/idx_factor_pro.md) | 标的-交易日 | 2026-10-28 | ok | 指数日行情 + 80+ 技术指标（含 000300.SH / 000688.SH 等 4103 个指数代码） | 10,485,706 |
| [`stk_factor_pro`](tables/stk_factor_pro.md) | 标的-交易日 | 2026-10-28 | ok | 个股日行情 + 估值（pe/pe_ttm/pb/ps/dv_ratio）+ 复权价 + 200+ 技术指标的首选表 | 18,628,537 |

## flow — 资金、筹码、两融、北向

| 表 | 粒度 | 水位 | 状态 | 用途 | 行数 |
|---|---|---|---|---|---:|
| [`stk_block_trade`](tables/stk_block_trade.md) | 成交笔-交易日 | 2026-07-22 | ⚠ 停更 | 大宗交易逐笔（成交价、成交量、溢价率、买卖营业部） | 269,165 |
| [`stk_ccass_hold`](tables/stk_ccass_hold.md) | 标的-交易日 | 2026-06-10 | ⚠ 停更 | 香港中央结算持股（陆股通托管口径） | 1,370,097 |
| [`stk_cyq_chips`](tables/stk_cyq_chips.md) | 标的-交易日-价格 | 1970-01-01 | ⛔ 已排除 | 逐价位筹码占比（当前为空表，不要依赖） | 0 |
| [`stk_cyq_perf`](tables/stk_cyq_perf.md) | 标的-交易日 | 2026-07-27 | ⚠ 停更 | 筹码分布派生指标（获利比例、平均成本、90/70 成本区间与集中度） | 9,368,526 |
| [`stk_hk_hold`](tables/stk_hk_hold.md) | 标的-交易日 | 2026-07-22 | ⚠ 停更 | 沪深股通持股（持股数量、占比） | 3,952,840 |
| [`stk_margin`](tables/stk_margin.md) | 标的-交易日 | 2026-10-28 | ok | 融资融券明细（融资余额、融券余量、偿还量），判断杠杆资金方向 | 6,941,404 |
| [`stk_moneyflow`](tables/stk_moneyflow.md) | 标的-交易日 | 2026-10-28 | ok | 个股大中小单资金净流向（主力/超大单/大单/中单/小单） | 14,760,896 |
| [`stk_moneyflow_hsgt`](tables/stk_moneyflow_hsgt.md) | 交易日 | 2026-07-22 | ⚠ 停更 | 沪深港通资金（北向/南向） | 1,064 |
| [`stk_moneyflow_ths`](tables/stk_moneyflow_ths.md) | 标的-交易日 | 2026-10-28 | ok | 同花顺口径资金流向，与 stk_moneyflow（东财口径）互为交叉验证 | 2,284,193 |
| [`stk_top_inst`](tables/stk_top_inst.md) | 标的-交易日-席位 | 2026-07-22 | ⚠ 停更 | 龙虎榜买卖席位（营业部/机构专用），每边仅披露前 5 名 | 1,446,542 |
| [`stk_top_list`](tables/stk_top_list.md) | 标的-交易日 | 2026-07-22 | ⚠ 停更 | 龙虎榜上榜个股与上榜原因、买卖额 | 79,663 |

## event — 公司行为与事件（分红/解禁/回购/质押/增减持/调研）

| 表 | 粒度 | 水位 | 状态 | 用途 | 行数 |
|---|---|---|---|---|---:|
| [`stk_dividend`](tables/stk_dividend.md) | 标的-除权除息日 | 2026-10-22 | ok | 分红送股方案与除权除息日，复权因子校验的辅助表 | 118,626 |
| [`stk_holdernumber`](tables/stk_holdernumber.md) | 标的-公告期 | 2026-07-22 | ⚠ 停更 | 股东户数与户均持股，判断筹码集中度变化 | 8,382,000 |
| [`stk_holdertrade`](tables/stk_holdertrade.md) | 标的-公告-股东 | 2026-07-22 | ⚠ 停更 | 股东/高管增减持（方向、股数、均价、占比） | 4,761,000 |
| [`stk_managers`](tables/stk_managers.md) | 标的-任职期 | 2025-12-30 | ⚠ 停更 | 董监高人员与任职、薪酬 | 434,151 |
| [`stk_new_share`](tables/stk_new_share.md) | 标的-发行 | — | 快照 | 新股发行信息（发行价、发行量、上市日） | 2,000 |
| [`stk_pledge_detail`](tables/stk_pledge_detail.md) | 标的-公告-质押笔 | 2026-04-22 | ⚠ 停更 | 股权质押逐笔明细（质押方、质权方、数量） | 271,768 |
| [`stk_pledge_stat`](tables/stk_pledge_stat.md) | 标的-统计期 | 2026-04-17 | ⚠ 停更 | 股权质押比例与质押次数（中登周频） | 2,407,677 |
| [`stk_repurchase`](tables/stk_repurchase.md) | 标的-公告 | 2025-07-18 | ⚠ 停更 | 股份回购进度（预案/实施/完成）与回购数量金额 | 28,073 |
| [`stk_rewards`](tables/stk_rewards.md) | 标的-公告-激励对象 | — | 水位未知 | 股权激励与员工持股计划明细 | 3,704,315 |
| [`stk_share_float`](tables/stk_share_float.md) | 标的-解禁日 | 2026-10-28 | ok | 限售股解禁（解禁数量、占比、股东类型），判断供给冲击 | 426,202 |
| [`stk_surv`](tables/stk_surv.md) | 标的-调研活动 | — | 水位未知 | 机构调研记录（参与机构、调研日期、接待人员） | 199,200 |

## finance — 财务报表与披露

| 表 | 粒度 | 水位 | 状态 | 用途 | 行数 |
|---|---|---|---|---|---:|
| [`fin_audit`](tables/fin_audit.md) | 标的-报告期 | 2025-07-05 | ⚠ 停更 | 审计意见类型（标准无保留/保留/无法表示等） | 7,938 |
| [`fin_balance`](tables/fin_balance.md) | 标的-报告期 | 2026-10-29 | ok | 资产负债表（158 列） | 277,976 |
| [`fin_cashflow`](tables/fin_cashflow.md) | 标的-报告期 | 2026-10-29 | ok | 现金流量表（经营/投资/筹资） | 276,735 |
| [`fin_disclosure_date`](tables/fin_disclosure_date.md) | 标的-报告期 | 2026-07-24 | ok | 财报预约披露日期与实际披露日期 | 136,206 |
| [`fin_express`](tables/fin_express.md) | 标的-报告期 | 2026-10-21 | ok | 业绩快报（早于正式财报） | 33,478 |
| [`fin_forecast`](tables/fin_forecast.md) | 标的-报告期 | 2026-10-24 | ok | 业绩预告（预增/预减/扭亏类型与幅度） | 121,648 |
| [`fin_income`](tables/fin_income.md) | 标的-报告期 | 2026-10-29 | ok | 利润表（营收、净利润、EPS） | 286,225 |
| [`fin_indicator`](tables/fin_indicator.md) | 标的-报告期 | 2026-10-29 | ok | 财务指标（ROE/ROA/毛利率/资产负债率/每股指标），做筛选最常用 | 308,946 |
| [`fin_mainbz`](tables/fin_mainbz.md) | 标的-报告期-业务 | 2025-12-31 | ok | 主营业务构成（分产品/分地区收入与毛利率） | 1,492,850 |
| [`fin_top10_float_holders`](tables/fin_top10_float_holders.md) | 标的-报告期-股东 | 2026-10-29 | ok | 前十大流通股东持股 | 2,150,262 |
| [`fin_top10_holders`](tables/fin_top10_holders.md) | 标的-报告期-股东 | 2026-10-29 | ok | 前十大股东持股 | 1,553,080 |

## fund — 基金与 ETF

| 表 | 粒度 | 水位 | 状态 | 用途 | 行数 |
|---|---|---|---|---|---:|
| [`fund_daily`](tables/fund_daily.md) | 标的-交易日 | 2026-10-28 | ok | ETF/LOF 日行情（2026-10-29 实测 2829 个基金代码），ETF 价格的唯一权威表 | 3,246,308 |

## ops — 运维元数据（同步状态、漂移日志）

| 表 | 粒度 | 水位 | 状态 | 用途 | 行数 |
|---|---|---|---|---|---:|
| [`schema_drift_log`](tables/schema_drift_log.md) | 事件 | — | 快照 | 上游字段漂移记录（列增删改），解释「昨天还能查的列今天没了」 | 180 |
| [`table_sync_state`](tables/table_sync_state.md) | 表-维度-维度值 | — | 快照 | 同步状态（每表每维度的 is_sync / 行数 / 错误信息），判断「没数据」还是「没同步」 | 158,305 |

## index — index

| 表 | 粒度 | 水位 | 状态 | 用途 | 行数 |
|---|---|---|---|---|---:|
| [`idx_member_daily`](tables/idx_member_daily.md) | 交易日-成分股 | 2026-10-28 | ok | 指数成分股每日快照（3167 万行），回溯历史成分 | 31,676,536 |
| [`idx_weight`](tables/idx_weight.md) | 指数-成分股-交易日 | 2026-10-28 | ok | 指数成分与权重（月度快照），指数→成分股展开 | 108,297,082 |
