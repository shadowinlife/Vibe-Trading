-- ClickHouse DDL snapshot: ashare.stk_dividend (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_dividend
(
    `ts_code` String,
    `end_date` String,
    `ann_date` String,
    `div_proc` String,
    `stk_div` Float64,
    `stk_bo_rate` Int32,
    `stk_co_rate` Float64,
    `cash_div` Float64,
    `cash_div_tax` Float64,
    `record_date` String,
    `ex_date` String,
    `pay_date` String,
    `div_listdate` String,
    `imp_ann_date` String
)
ENGINE = MergeTree
ORDER BY (ts_code, ann_date, record_date, ex_date, pay_date)
SETTINGS index_granularity = 8192
