# fin_balance

资产负债表（158 列）

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
| 行数 | 277,976 |
| 列数 | 158 |

## 注意事项

- 报告期维度；ann_date 是披露日，end_date 是报告期末，别混用

## 相关表

[`fin_income`](fin_income.md), [`fin_cashflow`](fin_cashflow.md), [`fin_indicator`](fin_indicator.md)

## 列清单（158 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `Date` | — |
| 3 | `f_ann_date` | `Date` | — |
| 4 | `end_date` | `Date` | — |
| 5 | `report_type` | `String` | — |
| 6 | `comp_type` | `String` | — |
| 7 | `end_type` | `String` | — |
| 8 | `total_share` | `Float64` | — |
| 9 | `cap_rese` | `Float64` | — |
| 10 | `undistr_porfit` | `Float64` | — |
| 11 | `surplus_rese` | `Float64` | — |
| 12 | `special_rese` | `Float64` | — |
| 13 | `money_cap` | `Float64` | — |
| 14 | `trad_asset` | `Float64` | — |
| 15 | `notes_receiv` | `Float64` | — |
| 16 | `accounts_receiv` | `Float64` | — |
| 17 | `oth_receiv` | `Float64` | — |
| 18 | `prepayment` | `Float64` | — |
| 19 | `div_receiv` | `Float64` | — |
| 20 | `int_receiv` | `Float64` | — |
| 21 | `inventories` | `Float64` | — |
| 22 | `amor_exp` | `Float64` | — |
| 23 | `nca_within_1y` | `Float64` | — |
| 24 | `sett_rsrv` | `Float64` | — |
| 25 | `loanto_oth_bank_fi` | `Float64` | — |
| 26 | `premium_receiv` | `Float64` | — |
| 27 | `reinsur_receiv` | `Float64` | — |
| 28 | `reinsur_res_receiv` | `Float64` | — |
| 29 | `pur_resale_fa` | `Float64` | — |
| 30 | `oth_cur_assets` | `Float64` | — |
| 31 | `total_cur_assets` | `Float64` | — |
| 32 | `fa_avail_for_sale` | `Float64` | — |
| 33 | `htm_invest` | `Float64` | — |
| 34 | `lt_eqt_invest` | `Float64` | — |
| 35 | `invest_real_estate` | `Float64` | — |
| 36 | `time_deposits` | `Float64` | — |
| 37 | `oth_assets` | `Float64` | — |
| 38 | `lt_rec` | `Float64` | — |
| 39 | `fix_assets` | `Float64` | — |
| 40 | `cip` | `Float64` | — |
| 41 | `const_materials` | `Float64` | — |
| 42 | `fixed_assets_disp` | `Float64` | — |
| 43 | `produc_bio_assets` | `Float64` | — |
| 44 | `oil_and_gas_assets` | `Float64` | — |
| 45 | `intan_assets` | `Float64` | — |
| 46 | `r_and_d` | `Float64` | — |
| 47 | `goodwill` | `Float64` | — |
| 48 | `lt_amor_exp` | `Float64` | — |
| 49 | `defer_tax_assets` | `Float64` | — |
| 50 | `decr_in_disbur` | `Float64` | — |
| 51 | `oth_nca` | `Float64` | — |
| 52 | `total_nca` | `Float64` | — |
| 53 | `cash_reser_cb` | `Float64` | — |
| 54 | `depos_in_oth_bfi` | `Float64` | — |
| 55 | `prec_metals` | `Float64` | — |
| 56 | `deriv_assets` | `Float64` | — |
| 57 | `rr_reins_une_prem` | `Float64` | — |
| 58 | `rr_reins_outstd_cla` | `Float64` | — |
| 59 | `rr_reins_lins_liab` | `Float64` | — |
| 60 | `rr_reins_lthins_liab` | `Float64` | — |
| 61 | `refund_depos` | `Float64` | — |
| 62 | `ph_pledge_loans` | `Float64` | — |
| 63 | `refund_cap_depos` | `Float64` | — |
| 64 | `indep_acct_assets` | `Float64` | — |
| 65 | `client_depos` | `Float64` | — |
| 66 | `client_prov` | `Float64` | — |
| 67 | `transac_seat_fee` | `Float64` | — |
| 68 | `invest_as_receiv` | `Float64` | — |
| 69 | `total_assets` | `Float64` | — |
| 70 | `lt_borr` | `Float64` | — |
| 71 | `st_borr` | `Float64` | — |
| 72 | `cb_borr` | `Float64` | — |
| 73 | `depos_ib_deposits` | `Float64` | — |
| 74 | `loan_oth_bank` | `Float64` | — |
| 75 | `trading_fl` | `Float64` | — |
| 76 | `notes_payable` | `Float64` | — |
| 77 | `acct_payable` | `Float64` | — |
| 78 | `adv_receipts` | `Float64` | — |
| 79 | `sold_for_repur_fa` | `Float64` | — |
| 80 | `comm_payable` | `Float64` | — |
| 81 | `payroll_payable` | `Float64` | — |
| 82 | `taxes_payable` | `Float64` | — |
| 83 | `int_payable` | `Float64` | — |
| 84 | `div_payable` | `Float64` | — |
| 85 | `oth_payable` | `Float64` | — |
| 86 | `acc_exp` | `Float64` | — |
| 87 | `deferred_inc` | `Float64` | — |
| 88 | `st_bonds_payable` | `Float64` | — |
| 89 | `payable_to_reinsurer` | `Float64` | — |
| 90 | `rsrv_insur_cont` | `Float64` | — |
| 91 | `acting_trading_sec` | `Float64` | — |
| 92 | `acting_uw_sec` | `Float64` | — |
| 93 | `non_cur_liab_due_1y` | `Float64` | — |
| 94 | `oth_cur_liab` | `Float64` | — |
| 95 | `total_cur_liab` | `Float64` | — |
| 96 | `bond_payable` | `Float64` | — |
| 97 | `lt_payable` | `Float64` | — |
| 98 | `specific_payables` | `Float64` | — |
| 99 | `estimated_liab` | `Float64` | — |
| 100 | `defer_tax_liab` | `Float64` | — |
| 101 | `defer_inc_non_cur_liab` | `Float64` | — |
| 102 | `oth_ncl` | `Float64` | — |
| 103 | `total_ncl` | `Float64` | — |
| 104 | `depos_oth_bfi` | `Float64` | — |
| 105 | `deriv_liab` | `Float64` | — |
| 106 | `depos` | `Float64` | — |
| 107 | `agency_bus_liab` | `Float64` | — |
| 108 | `oth_liab` | `Float64` | — |
| 109 | `prem_receiv_adva` | `Float64` | — |
| 110 | `depos_received` | `Float64` | — |
| 111 | `ph_invest` | `Float64` | — |
| 112 | `reser_une_prem` | `Float64` | — |
| 113 | `reser_outstd_claims` | `Float64` | — |
| 114 | `reser_lins_liab` | `Float64` | — |
| 115 | `reser_lthins_liab` | `Float64` | — |
| 116 | `indept_acc_liab` | `Float64` | — |
| 117 | `pledge_borr` | `Float64` | — |
| 118 | `indem_payable` | `Float64` | — |
| 119 | `policy_div_payable` | `Float64` | — |
| 120 | `total_liab` | `Float64` | — |
| 121 | `treasury_share` | `Float64` | — |
| 122 | `ordin_risk_reser` | `Float64` | — |
| 123 | `forex_differ` | `Float64` | — |
| 124 | `invest_loss_unconf` | `Float64` | — |
| 125 | `minority_int` | `Float64` | — |
| 126 | `total_hldr_eqy_exc_min_int` | `Float64` | — |
| 127 | `total_hldr_eqy_inc_min_int` | `Float64` | — |
| 128 | `total_liab_hldr_eqy` | `Float64` | — |
| 129 | `lt_payroll_payable` | `Float64` | — |
| 130 | `oth_comp_income` | `Float64` | — |
| 131 | `oth_eqt_tools` | `Float64` | — |
| 132 | `oth_eqt_tools_p_shr` | `Float64` | — |
| 133 | `lending_funds` | `Float64` | — |
| 134 | `acc_receivable` | `Float64` | — |
| 135 | `st_fin_payable` | `Float64` | — |
| 136 | `payables` | `Float64` | — |
| 137 | `hfs_assets` | `Float64` | — |
| 138 | `hfs_sales` | `Float64` | — |
| 139 | `cost_fin_assets` | `Float64` | — |
| 140 | `fair_value_fin_assets` | `Float64` | — |
| 141 | `cip_total` | `Float64` | — |
| 142 | `oth_pay_total` | `Float64` | — |
| 143 | `long_pay_total` | `Float64` | — |
| 144 | `debt_invest` | `Float64` | — |
| 145 | `oth_debt_invest` | `Float64` | — |
| 146 | `oth_eq_invest` | `Float64` | — |
| 147 | `oth_illiq_fin_assets` | `Float64` | — |
| 148 | `oth_eq_ppbond` | `Float64` | — |
| 149 | `receiv_financing` | `Float64` | — |
| 150 | `use_right_assets` | `Float64` | — |
| 151 | `lease_liab` | `Float64` | — |
| 152 | `contract_assets` | `Float64` | — |
| 153 | `contract_liab` | `Float64` | — |
| 154 | `accounts_receiv_bill` | `Float64` | — |
| 155 | `accounts_pay` | `Float64` | — |
| 156 | `oth_rcv_total` | `Float64` | — |
| 157 | `fix_assets_total` | `Float64` | — |
| 158 | `update_flag` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
