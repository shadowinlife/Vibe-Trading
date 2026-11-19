-- ClickHouse DDL snapshot: ashare.fin_disclosure_date (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.fin_disclosure_date
(
    `ts_code` String,
    `ann_date` Date,
    `end_date` Date,
    `pre_date` String,
    `actual_date` String
)
ENGINE = MergeTree
ORDER BY (ts_code, end_date)
SETTINGS index_granularity = 8192
