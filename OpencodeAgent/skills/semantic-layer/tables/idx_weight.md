# idx_weight

指数成分与权重（月度快照），指数→成分股展开

| 项 | 值 |
|---|---|
| 领域 | `index` |
| 资产类别 | `index` |
| 粒度 | 指数-成分股-交易日 |
| 主键 | `index_code`, `con_code`, `trade_date` |
| 日期列 | `trade_date`（String） ⚠ String，过滤需 `toDate()` |
| 数据水位 | 2026-10-28（落后 1 自然日） |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 108,297,082 |
| 列数 | 4 |

## 注意事项

- trade_date 为 String（YYYYMMDD）
- 每个快照只含部分成分（2026-10-29 实测：000688.SH 最新快照 19 只、权重和 77.6%），不是全成分；加权计算必须披露覆盖率
- 1.08 亿行，必须带 index_code + trade_date 过滤

## 示例

```sql
SELECT con_code, weight FROM idx_weight WHERE index_code='000688.SH' AND trade_date=(SELECT max(trade_date) FROM idx_weight WHERE index_code='000688.SH')
```


## 相关表

[`idx_info`](idx_info.md), [`stk_factor_pro`](stk_factor_pro.md)

## 列清单（4 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `index_code` | `String` | — |
| 2 | `con_code` | `String` | — |
| 3 | `trade_date` | `String` | — |
| 4 | `weight` | `Float64` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
