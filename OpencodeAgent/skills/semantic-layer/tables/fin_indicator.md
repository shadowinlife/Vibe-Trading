# fin_indicator

财务指标（ROE/ROA/毛利率/资产负债率/每股指标），做筛选最常用

| 项 | 值 |
|---|---|
| 领域 | `finance` |
| 资产类别 | `stock` |
| 粒度 | 标的-报告期 |
| 主键 | `ts_code`, `end_date` |
| 日期列 | `end_date`（Date） |
| 数据水位 | 2026-10-29（落后 0 自然日） |
| 新鲜度 SLA | 120 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 308,946 |
| 列数 | 168 |

## 单位

| 列 | 单位 |
|---|---|
| `roe` | 百分比 |
| `grossprofit_margin` | 百分比 |

## 注意事项

- 同名指标有多个口径（roe / roe_waa / roe_dt），先看列注释再选

## 相关表

[`fin_income`](fin_income.md), [`fin_balance`](fin_balance.md)

## 列清单（168 列，有注释 168 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | source=tushare fina_indicator; desc=TS代码 |
| 2 | `ann_date` | `Date` | source=tushare fina_indicator; desc=公告日期 |
| 3 | `end_date` | `Date` | source=tushare fina_indicator; desc=报告期 |
| 4 | `ann_date_key` | `Date` | source=tushare fina_indicator; desc=待补充; needs_review=1 |
| 5 | `eps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=基本每股收益; ambiguous_with=dt_eps |
| 6 | `dt_eps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=稀释每股收益; ambiguous_with=eps |
| 7 | `total_revenue_ps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=每股营业总收入 |
| 8 | `revenue_ps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=每股营业收入 |
| 9 | `capital_rese_ps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=每股资本公积 |
| 10 | `surplus_rese_ps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=每股盈余公积 |
| 11 | `undist_profit_ps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=每股未分配利润 |
| 12 | `extra_item` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=非经常性损益 |
| 13 | `profit_dedt` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=扣除非经常性损益后的净利润(扣非净利润) |
| 14 | `gross_margin` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=毛利; ambiguous_with=grossprofit_margin |
| 15 | `current_ratio` | `Float64` | unit=times; source=tushare fina_indicator; desc=流动比率 |
| 16 | `quick_ratio` | `Float64` | unit=times; source=tushare fina_indicator; desc=速动比率 |
| 17 | `cash_ratio` | `Float64` | unit=times; source=tushare fina_indicator; desc=保守速动比率 |
| 18 | `invturn_days` | `Float64` | unit=days; source=tushare fina_indicator; desc=存货周转天数 |
| 19 | `arturn_days` | `Float64` | unit=days; source=tushare fina_indicator; desc=应收账款周转天数 |
| 20 | `inv_turn` | `Float64` | unit=times; source=tushare fina_indicator; desc=存货周转率 |
| 21 | `ar_turn` | `Float64` | unit=times; source=tushare fina_indicator; desc=应收账款周转率 |
| 22 | `ca_turn` | `Float64` | unit=times; source=tushare fina_indicator; desc=流动资产周转率 |
| 23 | `fa_turn` | `Float64` | unit=times; source=tushare fina_indicator; desc=固定资产周转率 |
| 24 | `assets_turn` | `Float64` | unit=times; source=tushare fina_indicator; desc=总资产周转率 |
| 25 | `op_income` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=经营活动净收益 |
| 26 | `valuechange_income` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=价值变动净收益 |
| 27 | `interst_income` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=利息费用 |
| 28 | `daa` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=折旧与摊销 |
| 29 | `ebit` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=息税前利润 |
| 30 | `ebitda` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=息税折旧摊销前利润 |
| 31 | `fcff` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=企业自由现金流量 |
| 32 | `fcfe` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=股权自由现金流量 |
| 33 | `current_exint` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=无息流动负债 |
| 34 | `noncurrent_exint` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=无息非流动负债 |
| 35 | `interestdebt` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=带息债务 |
| 36 | `netdebt` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=净债务 |
| 37 | `tangible_asset` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=有形资产 |
| 38 | `working_capital` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=营运资金 |
| 39 | `networking_capital` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=营运流动资本 |
| 40 | `invest_capital` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=全部投入资本 |
| 41 | `retained_earnings` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=留存收益 |
| 42 | `diluted2_eps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=期末摊薄每股收益 |
| 43 | `bps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=每股净资产 |
| 44 | `ocfps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=每股经营活动产生的现金流量净额 |
| 45 | `retainedps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=每股留存收益 |
| 46 | `cfps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=每股现金流量净额 |
| 47 | `ebit_ps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=每股息税前利润 |
| 48 | `fcff_ps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=每股企业自由现金流量 |
| 49 | `fcfe_ps` | `Float64` | unit=yuan/share (元/股); caliber=报告期累计口径; source=tushare fina_indicator; desc=每股股东自由现金流量 |
| 50 | `netprofit_margin` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=销售净利率 |
| 51 | `grossprofit_margin` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=销售毛利率; ambiguous_with=gross_margin |
| 52 | `cogs_of_sales` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=销售成本率 |
| 53 | `expense_of_sales` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=销售期间费用率 |
| 54 | `profit_to_gr` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=净利润/营业总收入 |
| 55 | `saleexp_to_gr` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=销售费用/营业总收入 |
| 56 | `adminexp_of_gr` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=管理费用/营业总收入 |
| 57 | `finaexp_of_gr` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=财务费用/营业总收入 |
| 58 | `impai_ttm` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=资产减值损失/营业总收入 |
| 59 | `gc_of_gr` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=营业总成本/营业总收入 |
| 60 | `op_of_gr` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=营业利润/营业总收入 |
| 61 | `ebit_of_gr` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=息税前利润/营业总收入 |
| 62 | `roe` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=净资产收益率; ambiguous_with=roe_dt,roe_waa |
| 63 | `roe_waa` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=加权平均净资产收益率 |
| 64 | `roe_dt` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=净资产收益率(扣除非经常损益) |
| 65 | `roa` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=总资产报酬率 |
| 66 | `npta` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=总资产净利润 |
| 67 | `roic` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=投入资本回报率 |
| 68 | `roe_yearly` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=年化净资产收益率 |
| 69 | `roa2_yearly` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=年化总资产报酬率 |
| 70 | `roe_avg` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=平均净资产收益率(增发条件) |
| 71 | `opincome_of_ebt` | `Float64` | source=tushare fina_indicator; desc=经营活动净收益/利润总额 |
| 72 | `investincome_of_ebt` | `Float64` | source=tushare fina_indicator; desc=价值变动净收益/利润总额 |
| 73 | `n_op_profit_of_ebt` | `Float64` | source=tushare fina_indicator; desc=营业外收支净额/利润总额 |
| 74 | `tax_to_ebt` | `Float64` | source=tushare fina_indicator; desc=所得税/利润总额 |
| 75 | `dtprofit_to_profit` | `Float64` | source=tushare fina_indicator; desc=扣除非经常损益后的净利润/净利润 |
| 76 | `salescash_to_or` | `Float64` | source=tushare fina_indicator; desc=销售商品提供劳务收到的现金/营业收入 |
| 77 | `ocf_to_or` | `Float64` | source=tushare fina_indicator; desc=经营活动产生的现金流量净额/营业收入 |
| 78 | `ocf_to_opincome` | `Float64` | source=tushare fina_indicator; desc=经营活动产生的现金流量净额/经营活动净收益 |
| 79 | `capitalized_to_da` | `Float64` | source=tushare fina_indicator; desc=资本支出/折旧和摊销 |
| 80 | `debt_to_assets` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=资产负债率 |
| 81 | `assets_to_eqt` | `Float64` | unit=times; source=tushare fina_indicator; desc=权益乘数 |
| 82 | `dp_assets_to_eqt` | `Float64` | unit=times; source=tushare fina_indicator; desc=权益乘数(杜邦分析) |
| 83 | `ca_to_assets` | `Float64` | source=tushare fina_indicator; desc=流动资产/总资产 |
| 84 | `nca_to_assets` | `Float64` | source=tushare fina_indicator; desc=非流动资产/总资产 |
| 85 | `tbassets_to_totalassets` | `Float64` | source=tushare fina_indicator; desc=有形资产/总资产 |
| 86 | `int_to_talcap` | `Float64` | source=tushare fina_indicator; desc=带息债务/全部投入资本 |
| 87 | `eqt_to_talcapital` | `Float64` | source=tushare fina_indicator; desc=归属于母公司的股东权益/全部投入资本 |
| 88 | `currentdebt_to_debt` | `Float64` | source=tushare fina_indicator; desc=流动负债/负债合计 |
| 89 | `longdeb_to_debt` | `Float64` | source=tushare fina_indicator; desc=非流动负债/负债合计 |
| 90 | `ocf_to_shortdebt` | `Float64` | source=tushare fina_indicator; desc=经营活动产生的现金流量净额/流动负债 |
| 91 | `debt_to_eqt` | `Float64` | unit=times; source=tushare fina_indicator; desc=产权比率 |
| 92 | `eqt_to_debt` | `Float64` | source=tushare fina_indicator; desc=归属于母公司的股东权益/负债合计 |
| 93 | `eqt_to_interestdebt` | `Float64` | source=tushare fina_indicator; desc=归属于母公司的股东权益/带息债务 |
| 94 | `tangibleasset_to_debt` | `Float64` | source=tushare fina_indicator; desc=有形资产/负债合计 |
| 95 | `tangasset_to_intdebt` | `Float64` | source=tushare fina_indicator; desc=有形资产/带息债务 |
| 96 | `tangibleasset_to_netdebt` | `Float64` | source=tushare fina_indicator; desc=有形资产/净债务 |
| 97 | `ocf_to_debt` | `Float64` | source=tushare fina_indicator; desc=经营活动产生的现金流量净额/负债合计 |
| 98 | `ocf_to_interestdebt` | `Float64` | source=tushare fina_indicator; desc=经营活动产生的现金流量净额/带息债务 |
| 99 | `ocf_to_netdebt` | `Float64` | source=tushare fina_indicator; desc=经营活动产生的现金流量净额/净债务 |
| 100 | `ebit_to_interest` | `Float64` | unit=times; source=tushare fina_indicator; desc=已获利息倍数(EBIT/利息费用) |
| 101 | `longdebt_to_workingcapital` | `Float64` | source=tushare fina_indicator; desc=长期债务与营运资金比率 |
| 102 | `ebitda_to_debt` | `Float64` | source=tushare fina_indicator; desc=息税折旧摊销前利润/负债合计 |
| 103 | `turn_days` | `Float64` | unit=days; source=tushare fina_indicator; desc=营业周期 |
| 104 | `roa_yearly` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=年化总资产净利率 |
| 105 | `roa_dp` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=总资产净利率(杜邦分析) |
| 106 | `fixed_assets` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=固定资产合计 |
| 107 | `profit_prefin_exp` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=扣除财务费用前营业利润 |
| 108 | `non_op_profit` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=非营业利润 |
| 109 | `op_to_ebt` | `Float64` | source=tushare fina_indicator; desc=营业利润/利润总额 |
| 110 | `nop_to_ebt` | `Float64` | source=tushare fina_indicator; desc=非营业利润/利润总额 |
| 111 | `ocf_to_profit` | `Float64` | source=tushare fina_indicator; desc=经营活动产生的现金流量净额/营业利润 |
| 112 | `cash_to_liqdebt` | `Float64` | source=tushare fina_indicator; desc=货币资金/流动负债 |
| 113 | `cash_to_liqdebt_withinterest` | `Float64` | source=tushare fina_indicator; desc=货币资金/带息流动负债 |
| 114 | `op_to_liqdebt` | `Float64` | source=tushare fina_indicator; desc=营业利润/流动负债 |
| 115 | `op_to_debt` | `Float64` | source=tushare fina_indicator; desc=营业利润/负债合计 |
| 116 | `roic_yearly` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=年化投入资本回报率 |
| 117 | `total_fa_trun` | `Float64` | unit=times; source=tushare fina_indicator; desc=固定资产合计周转率 |
| 118 | `profit_to_op` | `Float64` | source=tushare fina_indicator; desc=利润总额/营业收入 |
| 119 | `q_opincome` | `Float64` | unit=yuan (CNY); caliber=单季度口径(元); source=tushare fina_indicator; desc=经营活动单季度净收益 |
| 120 | `q_investincome` | `Float64` | unit=yuan (CNY); caliber=单季度口径(元); source=tushare fina_indicator; desc=价值变动单季度净收益 |
| 121 | `q_dtprofit` | `Float64` | unit=yuan (CNY); caliber=单季度口径(元); source=tushare fina_indicator; desc=扣除非经常损益后的单季度净利润 |
| 122 | `q_eps` | `Float64` | unit=yuan/share (元/股); caliber=单季度口径; source=tushare fina_indicator; desc=每股收益(单季度) |
| 123 | `q_netprofit_margin` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=销售净利率(单季度) |
| 124 | `q_gsprofit_margin` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=销售毛利率(单季度) |
| 125 | `q_exp_to_sales` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=销售期间费用率(单季度) |
| 126 | `q_profit_to_gr` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=净利润/营业总收入(单季度) |
| 127 | `q_saleexp_to_gr` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=销售费用/营业总收入(单季度) |
| 128 | `q_adminexp_to_gr` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=管理费用/营业总收入(单季度) |
| 129 | `q_finaexp_to_gr` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=财务费用/营业总收入(单季度) |
| 130 | `q_impair_to_gr_ttm` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=资产减值损失/营业总收入(单季度) |
| 131 | `q_gc_to_gr` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=营业总成本/营业总收入(单季度) |
| 132 | `q_op_to_gr` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=营业利润/营业总收入(单季度) |
| 133 | `q_roe` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=净资产收益率(单季度) |
| 134 | `q_dt_roe` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=净资产单季度收益率(扣除非经常损益) |
| 135 | `q_npta` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=总资产净利润(单季度) |
| 136 | `q_opincome_to_ebt` | `Float64` | source=tushare fina_indicator; desc=经营活动净收益/利润总额(单季度) |
| 137 | `q_investincome_to_ebt` | `Float64` | source=tushare fina_indicator; desc=价值变动净收益/利润总额(单季度) |
| 138 | `q_dtprofit_to_profit` | `Float64` | source=tushare fina_indicator; desc=扣除非经常损益后的净利润/净利润(单季度) |
| 139 | `q_salescash_to_or` | `Float64` | source=tushare fina_indicator; desc=销售商品提供劳务收到的现金/营业收入(单季度) |
| 140 | `q_ocf_to_sales` | `Float64` | source=tushare fina_indicator; desc=经营活动产生的现金流量净额/营业收入(单季度) |
| 141 | `q_ocf_to_or` | `Float64` | source=tushare fina_indicator; desc=经营活动产生的现金流量净额/经营活动净收益(单季度) |
| 142 | `basic_eps_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=基本每股收益同比增长率(%) |
| 143 | `dt_eps_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=稀释每股收益同比增长率(%) |
| 144 | `cfps_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=每股经营活动产生的现金流量净额同比增长率(%) |
| 145 | `op_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=营业利润同比增长率(%) |
| 146 | `ebt_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=利润总额同比增长率(%) |
| 147 | `netprofit_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=归属母公司股东的净利润同比增长率(%) |
| 148 | `dt_netprofit_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=归属母公司股东的净利润-扣除非经常损益同比增长率(%) |
| 149 | `ocf_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=经营活动产生的现金流量净额同比增长率(%) |
| 150 | `roe_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=净资产收益率(摊薄)同比增长率(%) |
| 151 | `bps_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=每股净资产相对年初增长率(%) |
| 152 | `assets_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=资产总计相对年初增长率(%) |
| 153 | `eqt_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=归属母公司的股东权益相对年初增长率(%) |
| 154 | `tr_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=营业总收入同比增长率(%) |
| 155 | `or_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=营业收入同比增长率(%) |
| 156 | `q_gr_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=营业总收入同比增长率(%)(单季度) |
| 157 | `q_gr_qoq` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=营业总收入环比增长率(%)(单季度) |
| 158 | `q_sales_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=营业收入同比增长率(%)(单季度) |
| 159 | `q_sales_qoq` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=营业收入环比增长率(%)(单季度) |
| 160 | `q_op_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=营业利润同比增长率(%)(单季度) |
| 161 | `q_op_qoq` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=营业利润环比增长率(%)(单季度) |
| 162 | `q_profit_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=净利润同比增长率(%)(单季度) |
| 163 | `q_profit_qoq` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=净利润环比增长率(%)(单季度) |
| 164 | `q_netprofit_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=归属母公司股东的净利润同比增长率(%)(单季度) |
| 165 | `q_netprofit_qoq` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=归属母公司股东的净利润环比增长率(%)(单季度) |
| 166 | `equity_yoy` | `Float64` | unit=percent (%); caliber=tushare财报口径(百分比类为百分值, 12.5=12.5%); source=tushare fina_indicator; desc=净资产同比增长率 |
| 167 | `rd_exp` | `Float64` | unit=yuan (CNY); caliber=tushare财报口径(元); source=tushare fina_indicator; desc=研发费用 |
| 168 | `update_flag` | `String` | source=tushare fina_indicator; desc=更新标识 |
