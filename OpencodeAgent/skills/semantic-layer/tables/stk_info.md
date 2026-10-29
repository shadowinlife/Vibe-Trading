# stk_info

A 股股票基础信息（名称、行业、上市状态、实控人），代码↔名称解析的第一站

| 项 | 值 |
|---|---|
| 领域 | `basic` |
| 资产类别 | `stock` |
| 粒度 | 全量快照 |
| 主键 | `ts_code` |
| 日期列 | —（快照/无日期列） |
| 数据水位 | — |
| 新鲜度 SLA | 7 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | 快照 |
| 行数 | 5,576 |
| 列数 | 10 |

## 注意事项

- 快照表，无交易日维度；历史名称变化查 stk_name_history

## 示例

```sql
SELECT ts_code, name, industry FROM stk_info WHERE name LIKE '%宁德%'
```


## 相关表

[`stk_name_history`](stk_name_history.md), [`trade_calendar`](trade_calendar.md)

## 列清单（10 列，有注释 10 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `ts_code` | `String` | source=tushare stock_basic; desc=股票代码(ts_code格式, 如000001.SZ) |
| 2 | `symbol` | `String` | source=tushare stock_basic; desc=股票代码(6位数字) |
| 3 | `name` | `String` | source=tushare stock_basic; desc=股票名称 |
| 4 | `area` | `String` | source=tushare stock_basic; desc=地域 |
| 5 | `industry` | `String` | source=tushare stock_basic; desc=所属行业 |
| 6 | `cnspell` | `String` | source=tushare stock_basic; desc=股票名称拼音缩写 |
| 7 | `market` | `String` | source=tushare stock_basic; desc=市场类型(主板/创业板/科创板/CDR) |
| 8 | `list_date` | `String` | source=tushare stock_basic; desc=上市日期(YYYYMMDD格式) |
| 9 | `act_name` | `String` | source=tushare stock_basic; desc=实控人名称 |
| 10 | `act_ent_type` | `String` | source=tushare stock_basic; desc=实控人企业性质 |
