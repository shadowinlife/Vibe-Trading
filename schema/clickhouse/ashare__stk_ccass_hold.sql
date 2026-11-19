-- ClickHouse DDL snapshot: ashare.stk_ccass_hold (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_ccass_hold
(
    `trade_date` Date,
    `ts_code` String,
    `name` String,
    `shareholding` String,
    `hold_nums` String,
    `hold_ratio` String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(trade_date)
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
