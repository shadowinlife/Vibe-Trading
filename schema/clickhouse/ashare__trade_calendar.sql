-- ClickHouse DDL snapshot: ashare.trade_calendar (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.trade_calendar
(
    `exchange` String,
    `cal_date` String,
    `is_open` Int64,
    `pretrade_date` String
)
ENGINE = MergeTree
ORDER BY cal_date
SETTINGS index_granularity = 8192
