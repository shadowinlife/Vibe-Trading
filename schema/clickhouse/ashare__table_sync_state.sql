-- ClickHouse DDL snapshot: ashare.table_sync_state (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.table_sync_state
(
    `source_table` String,
    `target_table` String,
    `dimension_type` String,
    `dimension_value` String,
    `is_sync` UInt8,
    `rows_written` UInt64,
    `error_message` String,
    `updated_at` DateTime
)
ENGINE = MergeTree
ORDER BY (source_table, dimension_type, dimension_value)
SETTINGS index_granularity = 8192
