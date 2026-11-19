# stk_factor_pro

个股日行情 + 估值（pe/pe_ttm/pb/ps/dv_ratio）+ 复权价 + 200+ 技术指标的首选表

| 项 | 值 |
|---|---|
| 领域 | `factor` |
| 资产类别 | `stock` |
| 粒度 | 标的-交易日 |
| 主键 | `ts_code`, `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-10-28（落后 1 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 是 |
| 可估值 | 是 |
| 状态 | ok |
| 行数 | 18,628,537 |
| 列数 | 261 |

## 单位

| 列 | 单位 |
|---|---|
| `vol` | 手 |
| `amount` | 千元 |
| `total_mv` | 万元 |

## 注意事项

- 涨跌幅列名是 pct_chg；指数表用 pct_change，不要跨表照抄
- close 不复权，close_hfq 后复权；回测必须显式声明用哪一列
- 生产库实际 261 列，仓库旧文档称 199 列，以本表全列清单为准

## 示例

```sql
SELECT trade_date, close, pe_ttm, pb FROM stk_factor_pro WHERE ts_code='600519.SH' AND trade_date >= today()-30 ORDER BY trade_date
```


## 相关表

[`stk_info`](stk_info.md), [`trade_calendar`](trade_calendar.md)

## 列清单（261 列，有注释 199 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | source=tushare stk_factor_pro; desc=股票代码(ts_code格式, 如000001.SZ) |
| 2 | `trade_date` | `Date` | source=tushare stk_factor_pro; desc=交易日期 |
| 3 | `open` | `Float64` | unit=yuan (CNY); adjust=raw; source=tushare stk_factor_pro; desc=开盘价(不复权); ambiguous_with=open_hfq |
| 4 | `open_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; caliber=不复权价×adj_factor; source=tushare stk_factor_pro; desc=开盘价(后复权); ambiguous_with=open |
| 5 | `high` | `Float64` | unit=yuan (CNY); adjust=raw; source=tushare stk_factor_pro; desc=最高价(不复权); ambiguous_with=high_hfq |
| 6 | `high_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; caliber=不复权价×adj_factor; source=tushare stk_factor_pro; desc=最高价(后复权); ambiguous_with=high |
| 7 | `low` | `Float64` | unit=yuan (CNY); adjust=raw; source=tushare stk_factor_pro; desc=最低价(不复权); ambiguous_with=low_hfq |
| 8 | `low_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; caliber=不复权价×adj_factor; source=tushare stk_factor_pro; desc=最低价(后复权); ambiguous_with=low |
| 9 | `close` | `Float64` | unit=yuan (CNY); adjust=raw; source=tushare stk_factor_pro; desc=收盘价(不复权); ambiguous_with=close_hfq |
| 10 | `close_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; caliber=不复权价×adj_factor; source=tushare stk_factor_pro; desc=收盘价(后复权); ambiguous_with=close |
| 11 | `change` | `Float64` | unit=yuan (CNY); adjust=raw; source=tushare stk_factor_pro; desc=涨跌额(收盘价-昨收价) |
| 12 | `pct_chg` | `Float64` | unit=percent (%); source=tushare stk_factor_pro; desc=涨跌幅; caliber=除权后的涨跌幅 |
| 13 | `vol` | `Float64` | unit=lots; caliber=tushare口径(1手=100股); source=tushare stk_factor_pro; desc=成交量; ambiguous_with=amount |
| 14 | `amount` | `Float64` | unit=千元 (thousand CNY); caliber=已交叉验证amount×10/vol≈close; source=tushare stk_factor_pro; desc=成交额; ambiguous_with=vol |
| 15 | `turnover_rate` | `Float64` | unit=percent (%); source=tushare stk_factor_pro; desc=换手率; ambiguous_with=turnover_rate_f |
| 16 | `turnover_rate_f` | `Float64` | unit=percent (%); source=tushare stk_factor_pro; desc=换手率(自由流通股本口径); ambiguous_with=turnover_rate |
| 17 | `volume_ratio` | `Float64` | unit=ratio; source=tushare stk_factor_pro; desc=量比 |
| 18 | `pe` | `Float64` | unit=ratio; caliber=总市值/净利润, 亏损的PE为空; source=tushare stk_factor_pro; desc=市盈率; ambiguous_with=pe_ttm |
| 19 | `pe_ttm` | `Float64` | unit=ratio; caliber=TTM(滚动12个月), 亏损的PE为空; source=tushare stk_factor_pro; desc=市盈率(TTM); ambiguous_with=pe |
| 20 | `pb` | `Float64` | unit=ratio; caliber=总市值/净资产; source=tushare stk_factor_pro; desc=市净率 |
| 21 | `ps` | `Float64` | unit=ratio; source=tushare stk_factor_pro; desc=市销率; ambiguous_with=ps_ttm |
| 22 | `ps_ttm` | `Float64` | unit=ratio; source=tushare stk_factor_pro; desc=市销率(TTM); ambiguous_with=ps |
| 23 | `dv_ratio` | `Float64` | unit=percent (%); source=tushare stk_factor_pro; desc=股息率; ambiguous_with=dv_ttm |
| 24 | `dv_ttm` | `Float64` | unit=percent (%); source=tushare stk_factor_pro; desc=股息率(TTM); ambiguous_with=dv_ratio |
| 25 | `total_share` | `Float64` | unit=万股; source=tushare stk_factor_pro; desc=总股本 |
| 26 | `float_share` | `Float64` | unit=万股; source=tushare stk_factor_pro; desc=流通股本 |
| 27 | `free_share` | `Float64` | unit=万股; source=tushare stk_factor_pro; desc=自由流通股本 |
| 28 | `total_mv` | `Float64` | unit=万元 (ten-thousand CNY); caliber=tushare daily_basic口径; source=tushare stk_factor_pro; desc=总市值; ambiguous_with=circ_mv |
| 29 | `circ_mv` | `Float64` | unit=万元 (ten-thousand CNY); caliber=tushare daily_basic口径; source=tushare stk_factor_pro; desc=流通市值; ambiguous_with=total_mv |
| 30 | `adj_factor` | `Float64` | unit=dimensionless; caliber=累计后复权因子, 后复权价=不复权价×因子; source=tushare stk_factor_pro; desc=复权因子 |
| 31 | `asi_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=振动升降指标ASI(M1=26,M2=10), 基于不复权价格计算 |
| 32 | `asi_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=振动升降指标ASI(M1=26,M2=10), 基于后复权价格计算 |
| 33 | `asit_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=振动升降指标ASIT(ASI的移动平均)(M1=26,M2=10), 基于不复权价格计算 |
| 34 | `asit_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=振动升降指标ASIT(ASI的移动平均)(M1=26,M2=10), 基于后复权价格计算 |
| 35 | `atr_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=真实波动N日平均值ATR(N=20), 基于不复权价格计算 |
| 36 | `atr_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=真实波动N日平均值ATR(N=20), 基于后复权价格计算 |
| 37 | `bbi_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=BBI多空指标(M=3,6,12,20), 基于不复权价格计算 |
| 38 | `bbi_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=BBI多空指标(M=3,6,12,20), 基于后复权价格计算 |
| 39 | `bias1_bfq` | `Float64` | unit=percent (%); adjust=bfq; source=tushare stk_factor_pro; desc=BIAS乖离率1(L=6,12,24), 基于不复权价格计算 |
| 40 | `bias1_hfq` | `Float64` | unit=percent (%); adjust=hfq; source=tushare stk_factor_pro; desc=BIAS乖离率1(L=6,12,24), 基于后复权价格计算 |
| 41 | `bias2_bfq` | `Float64` | unit=percent (%); adjust=bfq; source=tushare stk_factor_pro; desc=BIAS乖离率2(L=6,12,24), 基于不复权价格计算 |
| 42 | `bias2_hfq` | `Float64` | unit=percent (%); adjust=hfq; source=tushare stk_factor_pro; desc=BIAS乖离率2(L=6,12,24), 基于后复权价格计算 |
| 43 | `bias3_bfq` | `Float64` | unit=percent (%); adjust=bfq; source=tushare stk_factor_pro; desc=BIAS乖离率3(L=6,12,24), 基于不复权价格计算 |
| 44 | `bias3_hfq` | `Float64` | unit=percent (%); adjust=hfq; source=tushare stk_factor_pro; desc=BIAS乖离率3(L=6,12,24), 基于后复权价格计算 |
| 45 | `boll_lower_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=BOLL布林带下轨(N=20,P=2), 基于不复权价格计算 |
| 46 | `boll_lower_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=BOLL布林带下轨(N=20,P=2), 基于后复权价格计算 |
| 47 | `boll_mid_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=BOLL布林带中轨(N=20,P=2), 基于不复权价格计算 |
| 48 | `boll_mid_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=BOLL布林带中轨(N=20,P=2), 基于后复权价格计算 |
| 49 | `boll_upper_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=BOLL布林带上轨(N=20,P=2), 基于不复权价格计算 |
| 50 | `boll_upper_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=BOLL布林带上轨(N=20,P=2), 基于后复权价格计算 |
| 51 | `brar_ar_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=BRAR情绪指标AR(M1=26), 基于不复权价格计算 |
| 52 | `brar_ar_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=BRAR情绪指标AR(M1=26), 基于后复权价格计算 |
| 53 | `brar_br_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=BRAR情绪指标BR(M1=26), 基于不复权价格计算 |
| 54 | `brar_br_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=BRAR情绪指标BR(M1=26), 基于后复权价格计算 |
| 55 | `cci_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=CCI顺势指标(N=14), 基于不复权价格计算 |
| 56 | `cci_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=CCI顺势指标(N=14), 基于后复权价格计算 |
| 57 | `cr_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=CR价格动量指标(N=20), 基于不复权价格计算 |
| 58 | `cr_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=CR价格动量指标(N=20), 基于后复权价格计算 |
| 59 | `dfma_dif_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=平行线差指标DIF(N1=10,N2=50,M=10), 基于不复权价格计算 |
| 60 | `dfma_dif_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=平行线差指标DIF(N1=10,N2=50,M=10), 基于后复权价格计算 |
| 61 | `dfma_difma_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=平行线差指标DIFMA(DIF的移动平均)(N1=10,N2=50,M=10), 基于不复权价格计算 |
| 62 | `dfma_difma_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=平行线差指标DIFMA(DIF的移动平均)(N1=10,N2=50,M=10), 基于后复权价格计算 |
| 63 | `dmi_adx_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=DMI动向指标ADX(M1=14,M2=6), 基于不复权价格计算 |
| 64 | `dmi_adx_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=DMI动向指标ADX(M1=14,M2=6), 基于后复权价格计算 |
| 65 | `dmi_adxr_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=DMI动向指标ADXR(M1=14,M2=6), 基于不复权价格计算 |
| 66 | `dmi_adxr_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=DMI动向指标ADXR(M1=14,M2=6), 基于后复权价格计算 |
| 67 | `dmi_mdi_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=DMI动向指标MDI(空方)(M1=14,M2=6), 基于不复权价格计算 |
| 68 | `dmi_mdi_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=DMI动向指标MDI(空方)(M1=14,M2=6), 基于后复权价格计算 |
| 69 | `dmi_pdi_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=DMI动向指标PDI(多方)(M1=14,M2=6), 基于不复权价格计算 |
| 70 | `dmi_pdi_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=DMI动向指标PDI(多方)(M1=14,M2=6), 基于后复权价格计算 |
| 71 | `downdays` | `Float64` | unit=days; source=tushare stk_factor_pro; desc=连跌天数 |
| 72 | `updays` | `Float64` | unit=days; source=tushare stk_factor_pro; desc=连涨天数 |
| 73 | `dpo_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=DPO区间震荡线(M1=20,M2=10,M3=6), 基于不复权价格计算 |
| 74 | `dpo_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=DPO区间震荡线(M1=20,M2=10,M3=6), 基于后复权价格计算 |
| 75 | `madpo_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=区间震荡线MADPO(DPO的移动平均)(M1=20,M2=10,M3=6), 基于不复权价格计算 |
| 76 | `madpo_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=区间震荡线MADPO(DPO的移动平均)(M1=20,M2=10,M3=6), 基于后复权价格计算 |
| 77 | `ema_bfq_10` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=10), 基于不复权价格计算 |
| 78 | `ema_bfq_20` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=20), 基于不复权价格计算 |
| 79 | `ema_bfq_250` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=250), 基于不复权价格计算 |
| 80 | `ema_bfq_30` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=30), 基于不复权价格计算 |
| 81 | `ema_bfq_5` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=5), 基于不复权价格计算 |
| 82 | `ema_bfq_60` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=60), 基于不复权价格计算 |
| 83 | `ema_bfq_90` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=90), 基于不复权价格计算 |
| 84 | `ema_hfq_10` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=10), 基于后复权价格计算 |
| 85 | `ema_hfq_20` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=20), 基于后复权价格计算 |
| 86 | `ema_hfq_250` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=250), 基于后复权价格计算 |
| 87 | `ema_hfq_30` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=30), 基于后复权价格计算 |
| 88 | `ema_hfq_5` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=5), 基于后复权价格计算 |
| 89 | `ema_hfq_60` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=60), 基于后复权价格计算 |
| 90 | `ema_hfq_90` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=90), 基于后复权价格计算 |
| 91 | `ema_qfq_10` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=10), 基于前复权价格计算 |
| 92 | `ema_qfq_20` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=20), 基于前复权价格计算 |
| 93 | `ema_qfq_250` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=250), 基于前复权价格计算 |
| 94 | `ema_qfq_30` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=30), 基于前复权价格计算 |
| 95 | `ema_qfq_5` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=5), 基于前复权价格计算 |
| 96 | `ema_qfq_60` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=60), 基于前复权价格计算 |
| 97 | `ema_qfq_90` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=EMA指数移动平均(N=90), 基于前复权价格计算 |
| 98 | `emv_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=EMV简易波动指标(N=14,M=9), 基于不复权价格计算 |
| 99 | `emv_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=EMV简易波动指标(N=14,M=9), 基于后复权价格计算 |
| 100 | `maemv_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=简易波动指标MAEMV(EMV的移动平均)(N=14,M=9), 基于不复权价格计算 |
| 101 | `maemv_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=简易波动指标MAEMV(EMV的移动平均)(N=14,M=9), 基于后复权价格计算 |
| 102 | `expma_12_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=EXPMA指数平均数指标(N1=12,N2=50), 基于不复权价格计算 |
| 103 | `expma_12_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=EXPMA指数平均数指标(N1=12,N2=50), 基于后复权价格计算 |
| 104 | `expma_50_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=EXPMA指数平均数指标(N1=12,N2=50), 基于不复权价格计算 |
| 105 | `expma_50_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=EXPMA指数平均数指标(N1=12,N2=50), 基于后复权价格计算 |
| 106 | `kdj_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=KDJ随机指标J值(N=9,M1=3,M2=3), 基于不复权价格计算 |
| 107 | `kdj_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=KDJ随机指标J值(N=9,M1=3,M2=3), 基于后复权价格计算 |
| 108 | `kdj_d_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=KDJ随机指标D值(N=9,M1=3,M2=3), 基于不复权价格计算 |
| 109 | `kdj_d_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=KDJ随机指标D值(N=9,M1=3,M2=3), 基于后复权价格计算 |
| 110 | `kdj_k_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=KDJ随机指标K值(N=9,M1=3,M2=3), 基于不复权价格计算 |
| 111 | `kdj_k_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=KDJ随机指标K值(N=9,M1=3,M2=3), 基于后复权价格计算 |
| 112 | `ktn_down_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=肯特纳交易通道下轨(N=20,ATR M=10), 基于不复权价格计算 |
| 113 | `ktn_down_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=肯特纳交易通道下轨(N=20,ATR M=10), 基于后复权价格计算 |
| 114 | `ktn_mid_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=肯特纳交易通道中轨(N=20,ATR M=10), 基于不复权价格计算 |
| 115 | `ktn_mid_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=肯特纳交易通道中轨(N=20,ATR M=10), 基于后复权价格计算 |
| 116 | `ktn_upper_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=肯特纳交易通道上轨(N=20,ATR M=10), 基于不复权价格计算 |
| 117 | `ktn_upper_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=肯特纳交易通道上轨(N=20,ATR M=10), 基于后复权价格计算 |
| 118 | `lowdays` | `Float64` | unit=days; source=tushare stk_factor_pro; desc=LOWRANGE(LOW), 当前最低价是近多少周期内最低价的最小值 |
| 119 | `topdays` | `Float64` | unit=days; source=tushare stk_factor_pro; desc=TOPRANGE(HIGH), 当前最高价是近多少周期内最高价的最大值 |
| 120 | `ma_bfq_10` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=10), 基于不复权价格计算 |
| 121 | `ma_bfq_20` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=20), 基于不复权价格计算 |
| 122 | `ma_bfq_250` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=250), 基于不复权价格计算 |
| 123 | `ma_bfq_30` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=30), 基于不复权价格计算 |
| 124 | `ma_bfq_5` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=5), 基于不复权价格计算 |
| 125 | `ma_bfq_60` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=60), 基于不复权价格计算 |
| 126 | `ma_bfq_90` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=90), 基于不复权价格计算 |
| 127 | `ma_hfq_10` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=10), 基于后复权价格计算 |
| 128 | `ma_hfq_20` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=20), 基于后复权价格计算 |
| 129 | `ma_hfq_250` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=250), 基于后复权价格计算 |
| 130 | `ma_hfq_30` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=30), 基于后复权价格计算 |
| 131 | `ma_hfq_5` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=5), 基于后复权价格计算 |
| 132 | `ma_hfq_60` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=60), 基于后复权价格计算 |
| 133 | `ma_hfq_90` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=90), 基于后复权价格计算 |
| 134 | `ma_qfq_10` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=10), 基于前复权价格计算 |
| 135 | `ma_qfq_20` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=20), 基于前复权价格计算 |
| 136 | `ma_qfq_250` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=250), 基于前复权价格计算 |
| 137 | `ma_qfq_30` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=30), 基于前复权价格计算 |
| 138 | `ma_qfq_5` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=5), 基于前复权价格计算 |
| 139 | `ma_qfq_60` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=60), 基于前复权价格计算 |
| 140 | `ma_qfq_90` | `Float64` | unit=yuan (CNY); adjust=qfq; source=tushare stk_factor_pro; desc=MA简单移动平均(N=90), 基于前复权价格计算 |
| 141 | `macd_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=MACD指标柱(2×(DIF-DEA))(SHORT=12,LONG=26,M=9), 基于不复权价格计算 |
| 142 | `macd_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=MACD指标柱(2×(DIF-DEA))(SHORT=12,LONG=26,M=9), 基于后复权价格计算 |
| 143 | `macd_dea_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=MACD指标DEA(信号线)(SHORT=12,LONG=26,M=9), 基于不复权价格计算 |
| 144 | `macd_dea_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=MACD指标DEA(信号线)(SHORT=12,LONG=26,M=9), 基于后复权价格计算 |
| 145 | `macd_dif_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=MACD指标DIF(快慢线差)(SHORT=12,LONG=26,M=9), 基于不复权价格计算 |
| 146 | `macd_dif_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=MACD指标DIF(快慢线差)(SHORT=12,LONG=26,M=9), 基于后复权价格计算 |
| 147 | `mass_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=梅斯线MASS(N1=9,N2=25,M=6), 基于不复权价格计算 |
| 148 | `mass_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=梅斯线MASS(N1=9,N2=25,M=6), 基于后复权价格计算 |
| 149 | `ma_mass_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=梅斯线MA_MASS(MASS的移动平均)(N1=9,N2=25,M=6), 基于不复权价格计算 |
| 150 | `ma_mass_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=梅斯线MA_MASS(MASS的移动平均)(N1=9,N2=25,M=6), 基于后复权价格计算 |
| 151 | `mfi_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=MFI资金流量指标(成交量的RSI)(N=14), 基于不复权价格计算 |
| 152 | `mfi_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=MFI资金流量指标(成交量的RSI)(N=14), 基于后复权价格计算 |
| 153 | `mtm_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=MTM动量指标(N=12,M=6), 基于不复权价格计算 |
| 154 | `mtm_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=MTM动量指标(N=12,M=6), 基于后复权价格计算 |
| 155 | `mtmma_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=动量指标MTMMA(MTM的移动平均)(N=12,M=6), 基于不复权价格计算 |
| 156 | `mtmma_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=动量指标MTMMA(MTM的移动平均)(N=12,M=6), 基于后复权价格计算 |
| 157 | `obv_bfq` | `Float64` | unit=lots (cumulative volume); adjust=bfq; source=tushare stk_factor_pro; desc=OBV能量潮指标(带符号累计成交量), 基于不复权价格计算 |
| 158 | `obv_hfq` | `Float64` | unit=lots (cumulative volume); adjust=hfq; source=tushare stk_factor_pro; desc=OBV能量潮指标(带符号累计成交量), 基于后复权价格计算 |
| 159 | `psy_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=PSY心理线(N=12,M=6), 基于不复权价格计算 |
| 160 | `psy_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=PSY心理线(N=12,M=6), 基于后复权价格计算 |
| 161 | `psyma_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=心理线PSYMA(PSY的移动平均)(N=12,M=6), 基于不复权价格计算 |
| 162 | `psyma_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=心理线PSYMA(PSY的移动平均)(N=12,M=6), 基于后复权价格计算 |
| 163 | `roc_bfq` | `Float64` | unit=percent (%); adjust=bfq; source=tushare stk_factor_pro; desc=ROC变动率指标(N=12,M=6), 基于不复权价格计算 |
| 164 | `roc_hfq` | `Float64` | unit=percent (%); adjust=hfq; source=tushare stk_factor_pro; desc=ROC变动率指标(N=12,M=6), 基于后复权价格计算 |
| 165 | `maroc_bfq` | `Float64` | unit=percent (%); adjust=bfq; source=tushare stk_factor_pro; desc=变动率指标MAROC(ROC的移动平均)(N=12,M=6), 基于不复权价格计算 |
| 166 | `maroc_hfq` | `Float64` | unit=percent (%); adjust=hfq; source=tushare stk_factor_pro; desc=变动率指标MAROC(ROC的移动平均)(N=12,M=6), 基于后复权价格计算 |
| 167 | `rsi_bfq_12` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=RSI相对强弱指标(N=12), 基于不复权价格计算 |
| 168 | `rsi_bfq_24` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=RSI相对强弱指标(N=24), 基于不复权价格计算 |
| 169 | `rsi_bfq_6` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=RSI相对强弱指标(N=6), 基于不复权价格计算 |
| 170 | `rsi_hfq_12` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=RSI相对强弱指标(N=12), 基于后复权价格计算 |
| 171 | `rsi_hfq_24` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=RSI相对强弱指标(N=24), 基于后复权价格计算 |
| 172 | `rsi_hfq_6` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=RSI相对强弱指标(N=6), 基于后复权价格计算 |
| 173 | `rsi_qfq_12` | `Float64` | unit=dimensionless (indicator value); adjust=qfq; source=tushare stk_factor_pro; desc=RSI相对强弱指标(N=12), 基于前复权价格计算 |
| 174 | `rsi_qfq_24` | `Float64` | unit=dimensionless (indicator value); adjust=qfq; source=tushare stk_factor_pro; desc=RSI相对强弱指标(N=24), 基于前复权价格计算 |
| 175 | `rsi_qfq_6` | `Float64` | unit=dimensionless (indicator value); adjust=qfq; source=tushare stk_factor_pro; desc=RSI相对强弱指标(N=6), 基于前复权价格计算 |
| 176 | `taq_down_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=唐安奇通道(海龟)下轨(N=20), 基于不复权价格计算 |
| 177 | `taq_down_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=唐安奇通道(海龟)下轨(N=20), 基于后复权价格计算 |
| 178 | `taq_mid_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=唐安奇通道(海龟)中轨(N=20), 基于不复权价格计算 |
| 179 | `taq_mid_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=唐安奇通道(海龟)中轨(N=20), 基于后复权价格计算 |
| 180 | `taq_up_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=唐安奇通道(海龟)上轨(N=20), 基于不复权价格计算 |
| 181 | `taq_up_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=唐安奇通道(海龟)上轨(N=20), 基于后复权价格计算 |
| 182 | `trix_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=TRIX三重指数平滑平均线(M1=12,M2=20), 基于不复权价格计算 |
| 183 | `trix_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=TRIX三重指数平滑平均线(M1=12,M2=20), 基于后复权价格计算 |
| 184 | `trma_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=三重指数平滑平均线TRMA(TRIX的移动平均)(M1=12,M2=20), 基于不复权价格计算 |
| 185 | `trma_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=三重指数平滑平均线TRMA(TRIX的移动平均)(M1=12,M2=20), 基于后复权价格计算 |
| 186 | `vr_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=VR容量比率(M1=26), 基于不复权价格计算 |
| 187 | `vr_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=VR容量比率(M1=26), 基于后复权价格计算 |
| 188 | `wr_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=W&R威廉指标(N=10,N1=6), 基于不复权价格计算 |
| 189 | `wr_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=W&R威廉指标(N=10,N1=6), 基于后复权价格计算 |
| 190 | `wr1_bfq` | `Float64` | unit=dimensionless (indicator value); adjust=bfq; source=tushare stk_factor_pro; desc=W&R威廉指标WR1(N=10,N1=6), 基于不复权价格计算 |
| 191 | `wr1_hfq` | `Float64` | unit=dimensionless (indicator value); adjust=hfq; source=tushare stk_factor_pro; desc=W&R威廉指标WR1(N=10,N1=6), 基于后复权价格计算 |
| 192 | `xsii_td1_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=薛斯通道II TD1(N=102,M=7), 基于不复权价格计算 |
| 193 | `xsii_td1_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=薛斯通道II TD1(N=102,M=7), 基于后复权价格计算 |
| 194 | `xsii_td2_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=薛斯通道II TD2(N=102,M=7), 基于不复权价格计算 |
| 195 | `xsii_td2_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=薛斯通道II TD2(N=102,M=7), 基于后复权价格计算 |
| 196 | `xsii_td3_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=薛斯通道II TD3(N=102,M=7), 基于不复权价格计算 |
| 197 | `xsii_td3_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=薛斯通道II TD3(N=102,M=7), 基于后复权价格计算 |
| 198 | `xsii_td4_bfq` | `Float64` | unit=yuan (CNY); adjust=bfq; source=tushare stk_factor_pro; desc=薛斯通道II TD4(N=102,M=7), 基于不复权价格计算 |
| 199 | `xsii_td4_hfq` | `Float64` | unit=yuan (CNY); adjust=hfq; source=tushare stk_factor_pro; desc=薛斯通道II TD4(N=102,M=7), 基于后复权价格计算 |
| 200 | `asi_qfq` | `Nullable(Float64)` | — |
| 201 | `asit_qfq` | `Nullable(Float64)` | — |
| 202 | `atr_qfq` | `Nullable(Float64)` | — |
| 203 | `bbi_qfq` | `Nullable(Float64)` | — |
| 204 | `bias1_qfq` | `Nullable(Float64)` | — |
| 205 | `bias2_qfq` | `Nullable(Float64)` | — |
| 206 | `bias3_qfq` | `Nullable(Float64)` | — |
| 207 | `boll_lower_qfq` | `Nullable(Float64)` | — |
| 208 | `boll_mid_qfq` | `Nullable(Float64)` | — |
| 209 | `boll_upper_qfq` | `Nullable(Float64)` | — |
| 210 | `brar_ar_qfq` | `Nullable(Float64)` | — |
| 211 | `brar_br_qfq` | `Nullable(Float64)` | — |
| 212 | `cci_qfq` | `Nullable(Float64)` | — |
| 213 | `close_qfq` | `Nullable(Float64)` | — |
| 214 | `cr_qfq` | `Nullable(Float64)` | — |
| 215 | `dfma_dif_qfq` | `Nullable(Float64)` | — |
| 216 | `dfma_difma_qfq` | `Nullable(Float64)` | — |
| 217 | `dmi_adx_qfq` | `Nullable(Float64)` | — |
| 218 | `dmi_adxr_qfq` | `Nullable(Float64)` | — |
| 219 | `dmi_mdi_qfq` | `Nullable(Float64)` | — |
| 220 | `dmi_pdi_qfq` | `Nullable(Float64)` | — |
| 221 | `dpo_qfq` | `Nullable(Float64)` | — |
| 222 | `emv_qfq` | `Nullable(Float64)` | — |
| 223 | `expma_12_qfq` | `Nullable(Float64)` | — |
| 224 | `expma_50_qfq` | `Nullable(Float64)` | — |
| 225 | `high_qfq` | `Nullable(Float64)` | — |
| 226 | `kdj_d_qfq` | `Nullable(Float64)` | — |
| 227 | `kdj_k_qfq` | `Nullable(Float64)` | — |
| 228 | `kdj_qfq` | `Nullable(Float64)` | — |
| 229 | `ktn_down_qfq` | `Nullable(Float64)` | — |
| 230 | `ktn_mid_qfq` | `Nullable(Float64)` | — |
| 231 | `ktn_upper_qfq` | `Nullable(Float64)` | — |
| 232 | `low_qfq` | `Nullable(Float64)` | — |
| 233 | `ma_mass_qfq` | `Nullable(Float64)` | — |
| 234 | `macd_dea_qfq` | `Nullable(Float64)` | — |
| 235 | `macd_dif_qfq` | `Nullable(Float64)` | — |
| 236 | `macd_qfq` | `Nullable(Float64)` | — |
| 237 | `madpo_qfq` | `Nullable(Float64)` | — |
| 238 | `maemv_qfq` | `Nullable(Float64)` | — |
| 239 | `maroc_qfq` | `Nullable(Float64)` | — |
| 240 | `mass_qfq` | `Nullable(Float64)` | — |
| 241 | `mfi_qfq` | `Nullable(Float64)` | — |
| 242 | `mtm_qfq` | `Nullable(Float64)` | — |
| 243 | `mtmma_qfq` | `Nullable(Float64)` | — |
| 244 | `obv_qfq` | `Nullable(Float64)` | — |
| 245 | `open_qfq` | `Nullable(Float64)` | — |
| 246 | `pre_close` | `Nullable(Float64)` | — |
| 247 | `psy_qfq` | `Nullable(Float64)` | — |
| 248 | `psyma_qfq` | `Nullable(Float64)` | — |
| 249 | `roc_qfq` | `Nullable(Float64)` | — |
| 250 | `taq_down_qfq` | `Nullable(Float64)` | — |
| 251 | `taq_mid_qfq` | `Nullable(Float64)` | — |
| 252 | `taq_up_qfq` | `Nullable(Float64)` | — |
| 253 | `trix_qfq` | `Nullable(Float64)` | — |
| 254 | `trma_qfq` | `Nullable(Float64)` | — |
| 255 | `vr_qfq` | `Nullable(Float64)` | — |
| 256 | `wr1_qfq` | `Nullable(Float64)` | — |
| 257 | `wr_qfq` | `Nullable(Float64)` | — |
| 258 | `xsii_td1_qfq` | `Nullable(Float64)` | — |
| 259 | `xsii_td2_qfq` | `Nullable(Float64)` | — |
| 260 | `xsii_td3_qfq` | `Nullable(Float64)` | — |
| 261 | `xsii_td4_qfq` | `Nullable(Float64)` | — |
