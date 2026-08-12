-- ClickHouse DDL snapshot: ashare.stk_company (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_company
(
    `ts_code` String,
    `com_name` String,
    `com_id` String,
    `chairman` String,
    `manager` String,
    `secretary` String,
    `reg_capital` Float64,
    `setup_date` String,
    `province` String,
    `city` String,
    `introduction` String,
    `website` String,
    `email` String,
    `office` String,
    `business_scope` String,
    `employees` Float64,
    `main_business` String,
    `exchange` String
)
ENGINE = MergeTree
ORDER BY ts_code
SETTINGS index_granularity = 8192
