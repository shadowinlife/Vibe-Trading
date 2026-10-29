# trade_calendar

交易日历（2000-2030），判断「最新应有交易日」「是否休市」的唯一依据

| 项 | 值 |
|---|---|
| 领域 | `basic` |
| 资产类别 | `market` |
| 粒度 | 全量快照 |
| 主键 | `cal_date` |
| 日期列 | —（快照/无日期列） |
| 数据水位 | — |
| 新鲜度 SLA | 30 天 |
| 权威行情表 | 否 |
| 可估值 | 否 |
| 状态 | 快照 |
| 行数 | 13,527 |
| 列数 | 4 |

## 注意事项

- 不要用 max(trade_date) FROM 某行情表 推断今天是否该有数据；用本表 is_open=1

## 示例

```sql
SELECT max(cal_date) FROM trade_calendar WHERE is_open=1 AND cal_date < today()
```


## 相关表

[`table_sync_state`](table_sync_state.md)

## 列清单（4 列，有注释 4 列）

| # | 列 | 类型 | 注释 |
|---:|---|---|---|
| 1 | `exchange` | `String` | source=tushare trade_cal; desc=交易所代码(SSE=上交所, SZSE=深交所; 另有CFFEX/SHFE/CZCE/DCE/INE) |
| 2 | `cal_date` | `String` | source=tushare trade_cal; desc=日历日期(YYYYMMDD格式) |
| 3 | `is_open` | `Int64` | source=tushare trade_cal; desc=是否交易(0=休市, 1=交易) |
| 4 | `pretrade_date` | `String` | source=tushare trade_cal; desc=上一个交易日(YYYYMMDD格式) |
