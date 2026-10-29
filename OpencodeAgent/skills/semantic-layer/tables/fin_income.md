# fin_income

利润表（营收、净利润、EPS）

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
| 行数 | 286,225 |
| 列数 | 94 |

## 相关表

[`fin_balance`](fin_balance.md), [`fin_indicator`](fin_indicator.md)

## 列清单（94 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `Date` | — |
| 3 | `f_ann_date` | `Date` | — |
| 4 | `end_date` | `Date` | — |
| 5 | `report_type` | `String` | — |
| 6 | `comp_type` | `String` | — |
| 7 | `end_type` | `String` | — |
| 8 | `basic_eps` | `Float64` | — |
| 9 | `diluted_eps` | `Float64` | — |
| 10 | `total_revenue` | `Float64` | — |
| 11 | `revenue` | `Float64` | — |
| 12 | `int_income` | `Float64` | — |
| 13 | `prem_earned` | `Float64` | — |
| 14 | `comm_income` | `Float64` | — |
| 15 | `n_commis_income` | `Float64` | — |
| 16 | `n_oth_income` | `Float64` | — |
| 17 | `n_oth_b_income` | `Float64` | — |
| 18 | `prem_income` | `Float64` | — |
| 19 | `out_prem` | `Float64` | — |
| 20 | `une_prem_reser` | `Float64` | — |
| 21 | `reins_income` | `Float64` | — |
| 22 | `n_sec_tb_income` | `Float64` | — |
| 23 | `n_sec_uw_income` | `Float64` | — |
| 24 | `n_asset_mg_income` | `Float64` | — |
| 25 | `oth_b_income` | `Float64` | — |
| 26 | `fv_value_chg_gain` | `Float64` | — |
| 27 | `invest_income` | `Float64` | — |
| 28 | `ass_invest_income` | `Float64` | — |
| 29 | `forex_gain` | `Float64` | — |
| 30 | `total_cogs` | `Float64` | — |
| 31 | `oper_cost` | `Float64` | — |
| 32 | `int_exp` | `Float64` | — |
| 33 | `comm_exp` | `Float64` | — |
| 34 | `biz_tax_surchg` | `Float64` | — |
| 35 | `sell_exp` | `Float64` | — |
| 36 | `admin_exp` | `Float64` | — |
| 37 | `fin_exp` | `Float64` | — |
| 38 | `assets_impair_loss` | `Float64` | — |
| 39 | `prem_refund` | `Float64` | — |
| 40 | `compens_payout` | `Float64` | — |
| 41 | `reser_insur_liab` | `Float64` | — |
| 42 | `div_payt` | `Float64` | — |
| 43 | `reins_exp` | `Float64` | — |
| 44 | `oper_exp` | `Float64` | — |
| 45 | `compens_payout_refu` | `Float64` | — |
| 46 | `insur_reser_refu` | `Float64` | — |
| 47 | `reins_cost_refund` | `Float64` | — |
| 48 | `other_bus_cost` | `Float64` | — |
| 49 | `operate_profit` | `Float64` | — |
| 50 | `non_oper_income` | `Float64` | — |
| 51 | `non_oper_exp` | `Float64` | — |
| 52 | `nca_disploss` | `Float64` | — |
| 53 | `total_profit` | `Float64` | — |
| 54 | `income_tax` | `Float64` | — |
| 55 | `n_income` | `Float64` | — |
| 56 | `n_income_attr_p` | `Float64` | — |
| 57 | `minority_gain` | `Float64` | — |
| 58 | `oth_compr_income` | `Float64` | — |
| 59 | `t_compr_income` | `Float64` | — |
| 60 | `compr_inc_attr_p` | `Float64` | — |
| 61 | `compr_inc_attr_m_s` | `Float64` | — |
| 62 | `ebit` | `Float64` | — |
| 63 | `ebitda` | `Float64` | — |
| 64 | `insurance_exp` | `Float64` | — |
| 65 | `undist_profit` | `Float64` | — |
| 66 | `distable_profit` | `Float64` | — |
| 67 | `rd_exp` | `Float64` | — |
| 68 | `fin_exp_int_exp` | `Float64` | — |
| 69 | `fin_exp_int_inc` | `Float64` | — |
| 70 | `transfer_surplus_rese` | `Float64` | — |
| 71 | `transfer_housing_imprest` | `Float64` | — |
| 72 | `transfer_oth` | `Float64` | — |
| 73 | `adj_lossgain` | `Float64` | — |
| 74 | `withdra_legal_surplus` | `Float64` | — |
| 75 | `withdra_legal_pubfund` | `Float64` | — |
| 76 | `withdra_biz_devfund` | `Float64` | — |
| 77 | `withdra_rese_fund` | `Float64` | — |
| 78 | `withdra_oth_ersu` | `Float64` | — |
| 79 | `workers_welfare` | `Float64` | — |
| 80 | `distr_profit_shrhder` | `Float64` | — |
| 81 | `prfshare_payable_dvd` | `Float64` | — |
| 82 | `comshare_payable_dvd` | `Float64` | — |
| 83 | `capit_comstock_div` | `Float64` | — |
| 84 | `net_after_nr_lp_correct` | `Float64` | — |
| 85 | `credit_impa_loss` | `Float64` | — |
| 86 | `net_expo_hedging_benefits` | `Float64` | — |
| 87 | `oth_impair_loss_assets` | `Float64` | — |
| 88 | `total_opcost` | `Float64` | — |
| 89 | `amodcost_fin_assets` | `Float64` | — |
| 90 | `oth_income` | `Float64` | — |
| 91 | `asset_disp_income` | `Float64` | — |
| 92 | `continued_net_profit` | `Float64` | — |
| 93 | `end_net_profit` | `Float64` | — |
| 94 | `update_flag` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
