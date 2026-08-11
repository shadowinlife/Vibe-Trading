-- ClickHouse DDL snapshot: ashare.idx_sw_classify (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.idx_sw_classify
(
    `index_code` String,
    `industry_name` String,
    `level` String,
    `industry_code` String,
    `is_pub` Int32,
    `parent_code` String,
    `src` String
)
ENGINE = MergeTree
ORDER BY (src, level, index_code)
SETTINGS index_granularity = 8192
