---
name: semantic-layer
description: |
  ClickHouse `ashare` 库的语义目录与取数判定规则。Use when: 查 A 股/ETF/指数数据、写 ClickHouse SQL、
  不确定该查哪张表、列名报 Code 47、结果为空、怀疑数据过期、问估值/PE/PB/行业排名、
  需要在 ETF 与其跟踪指数之间换算。Contains per-table schema docs and hard decision rules.
argument-hint: "表名 或 问题描述（如：科创50ETF 的 PE）"
user-invocable: true
---

# Semantic Layer — 先查目录，再写 SQL

本目录回答三件事：**该查哪张表、这张表怎么用、查不到怎么办。**

## 阅读顺序（不要跳步）

1. **`INDEX.md`** — 按领域定位表；先看「选表速查」，确认资产类别对应的权威表。
2. **`tables/<table>.md`** — 打开命中表的文档：粒度、主键、日期列类型、单位、坑、示例、全列清单。
3. **`RULES.md`** — 只要出现以下任一情况，**必须**先读规则再回答：
   - 结果为空 / 行数异常少；
   - 用户问 ETF、指数、板块，或 ETF 与指数的关系；
   - 用户问估值（PE/PB）、行业排名、"所有指数"、"哪些便宜"；
   - 最新日期看起来不对。

## 三条硬规则（违反即错）

1. **先判资产类别再选表**：个股 `stk_factor_pro`、ETF/LOF `fund_daily`、指数 `idx_factor_pro`、板块 `idx_daily_dc`。
   后缀决定一切：`000001.SZ` 是股票，`000001.SH` 是指数。
2. **空结果不等于没数据**：按 `RULES.md` R5 做两次探测（表内水位 + `table_sync_state`），区分
   「查错表 / 区间无数据 / 停更 / 未同步」。
3. **过期必须标注，估值必须给来源**：落后超过 SLA 要在结论第一行写数据日期；
   指数估值优先 `idx_dailybasic`，缺失时降级到成分股加权并披露覆盖率，**不得编造**。

## 与工具的分工

| 工具 | 用途 |
|---|---|
| `ch_list_tables` / `ch_describe_table` | 实时确认表是否存在、列名与类型（以库为准） |
| `ch_query` | 受约束只读 SQL（≤500 行、无写操作） |
| 本目录 | 库结构里读不出来的语义：粒度、权威性、单位、坑、降级规则 |
| `data-warehouse` skill | 仅当查询超出 `ch_query` 限制（大聚合/复杂 JOIN）时使用 |

> 本目录由 `tools/gen_semantic_layer.py` 从 `schema/clickhouse/metadata.json` + `catalog/catalog.yaml`
> 生成。**不要手改 `tables/` 与 `INDEX.md`**；要改语义请改 `catalog/catalog.yaml` 后重新生成。