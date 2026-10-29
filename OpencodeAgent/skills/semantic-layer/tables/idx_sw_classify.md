# idx_sw_classify

申万行业分类字典（L1/L2/L3 层级与代码）

| 项 | 值 |
|---|---|
| 领域 | `basic` |
| 资产类别 | `board` |
| 粒度 | 行业层级字典 |
| 主键 | `index_code` |
| 日期列 | —（快照/无日期列） |
| 数据水位 | — |
| 新鲜度 SLA | 90 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | 快照 |
| 行数 | 359 |
| 列数 | 7 |

## 注意事项

- 纯分类维表：没有日期列、没有涨跌幅列，不能用来算行业涨幅
- 行业行情请用成分股 + stk_factor_pro 聚合，或 idx_sw_member_all 展开

## 相关表

[`idx_sw_member_all`](idx_sw_member_all.md)

## 列清单（7 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `index_code` | `String` | — |
| 2 | `industry_name` | `String` | — |
| 3 | `level` | `String` | — |
| 4 | `industry_code` | `String` | — |
| 5 | `is_pub` | `Int32` | — |
| 6 | `parent_code` | `String` | — |
| 7 | `src` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
