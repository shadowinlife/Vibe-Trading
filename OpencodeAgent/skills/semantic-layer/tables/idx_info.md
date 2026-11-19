# idx_info

指数基础信息（名称、发布机构、基期、成分数），指数代码↔名称解析

| 项 | 值 |
|---|---|
| 领域 | `basic` |
| 资产类别 | `index` |
| 粒度 | 全量快照 |
| 主键 | `ts_code` |
| 日期列 | —（快照/无日期列） |
| 数据水位 | — |
| 新鲜度 SLA | 7 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | 快照 |
| 行数 | 8,000 |
| 列数 | 8 |

## 注意事项

- 8000 条里含大量 .CSI 外币/全收益变体（000688USD07.CSI 等），按名称匹配时要锚定 .SH/.SZ 后缀
- 名称是简称：000688.SH 叫「科创50」，不叫「上证科创板50成份指数」

## 示例

```sql
SELECT ts_code, name FROM idx_info WHERE name LIKE '%科创50%' AND ts_code LIKE '%.SH'
```


## 相关表

[`idx_factor_pro`](idx_factor_pro.md), [`idx_weight`](idx_weight.md)

## 列清单（8 列，有注释 0 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | — |
| 2 | `name` | `String` | — |
| 3 | `market` | `String` | — |
| 4 | `publisher` | `String` | — |
| 5 | `category` | `String` | — |
| 6 | `base_date` | `String` | — |
| 7 | `base_point` | `Float64` | — |
| 8 | `list_date` | `String` | — |

> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。
