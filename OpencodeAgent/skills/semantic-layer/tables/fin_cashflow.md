# fin_cashflow

现金流量表（经营/投资/筹资）

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
| 行数 | 276,735 |
| 列数 | 97 |

## 相关表

[`fin_balance`](fin_balance.md), [`fin_income`](fin_income.md)

## 列清单（97 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `ann_date` | `Date` | — |
| 3 | `f_ann_date` | `Date` | — |
| 4 | `end_date` | `Date` | — |
| 5 | `comp_type` | `String` | — |
| 6 | `report_type` | `String` | — |
| 7 | `end_type` | `String` | — |
| 8 | `net_profit` | `Float64` | — |
| 9 | `finan_exp` | `Float64` | — |
| 10 | `c_fr_sale_sg` | `Float64` | — |
| 11 | `recp_tax_rends` | `Float64` | — |
| 12 | `n_depos_incr_fi` | `Float64` | — |
| 13 | `n_incr_loans_cb` | `Float64` | — |
| 14 | `n_inc_borr_oth_fi` | `Float64` | — |
| 15 | `prem_fr_orig_contr` | `Float64` | — |
| 16 | `n_incr_insured_dep` | `Float64` | — |
| 17 | `n_reinsur_prem` | `Float64` | — |
| 18 | `n_incr_disp_tfa` | `Float64` | — |
| 19 | `ifc_cash_incr` | `Float64` | — |
| 20 | `n_incr_disp_faas` | `Float64` | — |
| 21 | `n_incr_loans_oth_bank` | `Float64` | — |
| 22 | `n_cap_incr_repur` | `Float64` | — |
| 23 | `c_fr_oth_operate_a` | `Float64` | — |
| 24 | `c_inf_fr_operate_a` | `Float64` | — |
| 25 | `c_paid_goods_s` | `Float64` | — |
| 26 | `c_paid_to_for_empl` | `Float64` | — |
| 27 | `c_paid_for_taxes` | `Float64` | — |
| 28 | `n_incr_clt_loan_adv` | `Float64` | — |
| 29 | `n_incr_dep_cbob` | `Float64` | — |
| 30 | `c_pay_claims_orig_inco` | `Float64` | — |
| 31 | `pay_handling_chrg` | `Float64` | — |
| 32 | `pay_comm_insur_plcy` | `Float64` | — |
| 33 | `oth_cash_pay_oper_act` | `Float64` | — |
| 34 | `st_cash_out_act` | `Float64` | — |
| 35 | `n_cashflow_act` | `Float64` | — |
| 36 | `oth_recp_ral_inv_act` | `Float64` | — |
| 37 | `c_disp_withdrwl_invest` | `Float64` | — |
| 38 | `c_recp_return_invest` | `Float64` | — |
| 39 | `n_recp_disp_fiolta` | `Float64` | — |
| 40 | `n_recp_disp_sobu` | `Float64` | — |
| 41 | `stot_inflows_inv_act` | `Float64` | — |
| 42 | `c_pay_acq_const_fiolta` | `Float64` | — |
| 43 | `c_paid_invest` | `Float64` | — |
| 44 | `n_disp_subs_oth_biz` | `Float64` | — |
| 45 | `oth_pay_ral_inv_act` | `Float64` | — |
| 46 | `n_incr_pledge_loan` | `Float64` | — |
| 47 | `stot_out_inv_act` | `Float64` | — |
| 48 | `n_cashflow_inv_act` | `Float64` | — |
| 49 | `c_recp_borrow` | `Float64` | — |
| 50 | `proc_issue_bonds` | `Float64` | — |
| 51 | `oth_cash_recp_ral_fnc_act` | `Float64` | — |
| 52 | `stot_cash_in_fnc_act` | `Float64` | — |
| 53 | `free_cashflow` | `Float64` | — |
| 54 | `c_prepay_amt_borr` | `Float64` | — |
| 55 | `c_pay_dist_dpcp_int_exp` | `Float64` | — |
| 56 | `incl_dvd_profit_paid_sc_ms` | `Float64` | — |
| 57 | `oth_cashpay_ral_fnc_act` | `Float64` | — |
| 58 | `stot_cashout_fnc_act` | `Float64` | — |
| 59 | `n_cash_flows_fnc_act` | `Float64` | — |
| 60 | `eff_fx_flu_cash` | `Float64` | — |
| 61 | `n_incr_cash_cash_equ` | `Float64` | — |
| 62 | `c_cash_equ_beg_period` | `Float64` | — |
| 63 | `c_cash_equ_end_period` | `Float64` | — |
| 64 | `c_recp_cap_contrib` | `Float64` | — |
| 65 | `incl_cash_rec_saims` | `Float64` | — |
| 66 | `uncon_invest_loss` | `Float64` | — |
| 67 | `prov_depr_assets` | `Float64` | — |
| 68 | `depr_fa_coga_dpba` | `Float64` | — |
| 69 | `amort_intang_assets` | `Float64` | — |
| 70 | `lt_amort_deferred_exp` | `Float64` | — |
| 71 | `decr_deferred_exp` | `Float64` | — |
| 72 | `incr_acc_exp` | `Float64` | — |
| 73 | `loss_disp_fiolta` | `Float64` | — |
| 74 | `loss_scr_fa` | `Float64` | — |
| 75 | `loss_fv_chg` | `Float64` | — |
| 76 | `invest_loss` | `Float64` | — |
| 77 | `decr_def_inc_tax_assets` | `Float64` | — |
| 78 | `incr_def_inc_tax_liab` | `Float64` | — |
| 79 | `decr_inventories` | `Float64` | — |
| 80 | `decr_oper_payable` | `Float64` | — |
| 81 | `incr_oper_payable` | `Float64` | — |
| 82 | `others` | `Float64` | — |
| 83 | `im_net_cashflow_oper_act` | `Float64` | — |
| 84 | `conv_debt_into_cap` | `Float64` | — |
| 85 | `conv_copbonds_due_within_1y` | `Float64` | — |
| 86 | `fa_fnc_leases` | `Float64` | — |
| 87 | `im_n_incr_cash_equ` | `Float64` | — |
| 88 | `net_dism_capital_add` | `Float64` | — |
| 89 | `net_cash_rece_sec` | `Float64` | — |
| 90 | `credit_impa_loss` | `Float64` | — |
| 91 | `use_right_asset_dep` | `Float64` | — |
| 92 | `oth_loss_asset` | `Float64` | — |
| 93 | `end_bal_cash` | `Float64` | — |
| 94 | `beg_bal_cash` | `Float64` | — |
| 95 | `end_bal_cash_equ` | `Float64` | — |
| 96 | `beg_bal_cash_equ` | `Float64` | — |
| 97 | `update_flag` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
