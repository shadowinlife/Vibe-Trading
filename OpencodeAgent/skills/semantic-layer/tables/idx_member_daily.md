# idx_member_daily

指数成分股每日快照（3167 万行），回溯历史成分

| 项 | 值 |
|---|---|
| 领域 | `index` |
| 资产类别 | `index` |
| 粒度 | 交易日-成分股 |
| 主键 | `ts_code`, `con_code`, `trade_date` |
| 日期列 | `trade_date`（Date） |
| 数据水位 | 2026-10-28（落后 1 自然日） |
| 新鲜度 SLA | 1 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | ok |
| 行数 | 31,676,536 |
| 列数 | 4 |

## 注意事项

- 只记录成员关系，无权重；权重查 idx_weight

## 相关表

[`idx_weight`](idx_weight.md), [`idx_info`](idx_info.md)

## 列清单（4 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `trade_date` | `Date` | — |
| 2 | `ts_code` | `String` | — |
| 3 | `con_code` | `String` | — |
| 4 | `name` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
