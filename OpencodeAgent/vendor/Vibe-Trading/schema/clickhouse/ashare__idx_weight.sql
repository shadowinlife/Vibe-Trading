-- ClickHouse DDL snapshot: ashare.idx_weight (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.idx_weight
(
    `index_code` String,
    `con_code` String,
    `trade_date` String,
    `weight` Float64
)
ENGINE = MergeTree
ORDER BY (index_code, con_code, trade_date)
SETTINGS index_granularity = 8192
