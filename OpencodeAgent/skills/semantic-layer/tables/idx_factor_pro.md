# idx_factor_pro

指数日行情 + 80+ 技术指标（含 000300.SH / 000688.SH 等 4103 个指数代码）

| 项 | 值 |
|---|---|
| 领域 | `factor` |
| 资产类别 | `index` |
| 粒度 | 标的-交易日 |
| 主键 | `ts_code`, `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-10-28（落后 1 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 是 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 10,485,706 |
| 列数 | 89 |

## 注意事项

- 只含价格与技术因子，没有任何 pe/pb 列；指数估值查 idx_dailybasic
- 涨跌幅列名是 pct_change

## 示例

```sql
SELECT trade_date, close, pct_change FROM idx_factor_pro WHERE ts_code='000688.SH' ORDER BY trade_date DESC LIMIT 20
```


## 相关表

[`idx_info`](idx_info.md), [`idx_weight`](idx_weight.md), [`idx_dailybasic`](idx_dailybasic.md)

## 列清单（89 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `trade_date` | `Date` | — |
| 3 | `open` | `Float64` | — |
| 4 | `high` | `Float64` | — |
| 5 | `low` | `Float64` | — |
| 6 | `close` | `Float64` | — |
| 7 | `pre_close` | `Float64` | — |
| 8 | `change` | `Float64` | — |
| 9 | `pct_change` | `Float64` | — |
| 10 | `vol` | `Float64` | — |
| 11 | `amount` | `Float64` | — |
| 12 | `asi_bfq` | `Float64` | — |
| 13 | `asit_bfq` | `Float64` | — |
| 14 | `atr_bfq` | `Float64` | — |
| 15 | `bbi_bfq` | `Float64` | — |
| 16 | `bias1_bfq` | `Float64` | — |
| 17 | `bias2_bfq` | `Float64` | — |
| 18 | `bias3_bfq` | `Float64` | — |
| 19 | `boll_lower_bfq` | `Float64` | — |
| 20 | `boll_mid_bfq` | `Float64` | — |
| 21 | `boll_upper_bfq` | `Float64` | — |
| 22 | `brar_ar_bfq` | `Float64` | — |
| 23 | `brar_br_bfq` | `Float64` | — |
| 24 | `cci_bfq` | `Float64` | — |
| 25 | `cr_bfq` | `Float64` | — |
| 26 | `dfma_dif_bfq` | `Float64` | — |
| 27 | `dfma_difma_bfq` | `Float64` | — |
| 28 | `dmi_adx_bfq` | `Float64` | — |
| 29 | `dmi_adxr_bfq` | `Float64` | — |
| 30 | `dmi_mdi_bfq` | `Float64` | — |
| 31 | `dmi_pdi_bfq` | `Float64` | — |
| 32 | `downdays` | `Float64` | — |
| 33 | `updays` | `Float64` | — |
| 34 | `dpo_bfq` | `Float64` | — |
| 35 | `madpo_bfq` | `Float64` | — |
| 36 | `ema_bfq_10` | `Float64` | — |
| 37 | `ema_bfq_20` | `Float64` | — |
| 38 | `ema_bfq_250` | `Float64` | — |
| 39 | `ema_bfq_30` | `Float64` | — |
| 40 | `ema_bfq_5` | `Float64` | — |
| 41 | `ema_bfq_60` | `Float64` | — |
| 42 | `ema_bfq_90` | `Float64` | — |
| 43 | `emv_bfq` | `Float64` | — |
| 44 | `maemv_bfq` | `Float64` | — |
| 45 | `expma_12_bfq` | `Float64` | — |
| 46 | `expma_50_bfq` | `Float64` | — |
| 47 | `kdj_bfq` | `Float64` | — |
| 48 | `kdj_d_bfq` | `Float64` | — |
| 49 | `kdj_k_bfq` | `Float64` | — |
| 50 | `ktn_down_bfq` | `Float64` | — |
| 51 | `ktn_mid_bfq` | `Float64` | — |
| 52 | `ktn_upper_bfq` | `Float64` | — |
| 53 | `lowdays` | `Float64` | — |
| 54 | `topdays` | `Float64` | — |
| 55 | `ma_bfq_10` | `Float64` | — |
| 56 | `ma_bfq_20` | `Float64` | — |
| 57 | `ma_bfq_250` | `Float64` | — |
| 58 | `ma_bfq_30` | `Float64` | — |
| 59 | `ma_bfq_5` | `Float64` | — |
| 60 | `ma_bfq_60` | `Float64` | — |
| 61 | `ma_bfq_90` | `Float64` | — |
| 62 | `macd_bfq` | `Float64` | — |
| 63 | `macd_dea_bfq` | `Float64` | — |
| 64 | `macd_dif_bfq` | `Float64` | — |
| 65 | `mass_bfq` | `Float64` | — |
| 66 | `ma_mass_bfq` | `Float64` | — |
| 67 | `mfi_bfq` | `Float64` | — |
| 68 | `mtm_bfq` | `Float64` | — |
| 69 | `mtmma_bfq` | `Float64` | — |
| 70 | `obv_bfq` | `Float64` | — |
| 71 | `psy_bfq` | `Float64` | — |
| 72 | `psyma_bfq` | `Float64` | — |
| 73 | `roc_bfq` | `Float64` | — |
| 74 | `maroc_bfq` | `Float64` | — |
| 75 | `rsi_bfq_12` | `Float64` | — |
| 76 | `rsi_bfq_24` | `Float64` | — |
| 77 | `rsi_bfq_6` | `Float64` | — |
| 78 | `taq_down_bfq` | `Float64` | — |
| 79 | `taq_mid_bfq` | `Float64` | — |
| 80 | `taq_up_bfq` | `Float64` | — |
| 81 | `trix_bfq` | `Float64` | — |
| 82 | `trma_bfq` | `Float64` | — |
| 83 | `vr_bfq` | `Float64` | — |
| 84 | `wr_bfq` | `Float64` | — |
| 85 | `wr1_bfq` | `Float64` | — |
| 86 | `xsii_td1_bfq` | `Float64` | — |
| 87 | `xsii_td2_bfq` | `Float64` | — |
| 88 | `xsii_td3_bfq` | `Float64` | — |
| 89 | `xsii_td4_bfq` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
