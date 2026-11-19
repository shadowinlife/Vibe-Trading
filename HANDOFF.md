# 数据层交接 · 2026-10-29 / 2026-10-30

上游 `ch-ashare-sync` 两批变更落到了 `ashare` 库：**5 个列改名、1 个列改类型、8 张新表、12 张表行数变化**。消费侧把 `metadata.json` → `catalog.yaml` → `tables/*.md` → `INDEX.md` 重新对齐，让 `tools/ci_semantic_layer_gate.py` 通过。那份 gate 逐表比对"列名 + 顺序"，缺一个、多一个、顺序不同都算失败，是本文唯一的完成判据。

---

## 步骤

### 1. 重新导出结构快照

`schema/clickhouse/metadata.json` 现在是 **65 张表**。这 8 个名字必须存在：

```
stk_daily  idx_daily_ts  idx_sw_daily  stk_kpl_list
stk_margin_sum  stk_moneyflow_mkt_dc  fund_info  dim_etf_index_map
```

**完成判据**：

```bash
python -c "
import json;d=json.load(open('schema/clickhouse/metadata.json'))
names={t['name'] for t in d['tables']}
need={'stk_daily','idx_daily_ts','idx_sw_daily','stk_kpl_list','stk_margin_sum',
      'stk_moneyflow_mkt_dc','fund_info','dim_etf_index_map'}
print('tables',len(names),'missing',need-names)"
```

输出 `tables 65 missing set()`。

### 2. 改代码里的字段引用

| 文件 | 要找的东西 | 改成 |
|---|---|---|
| `OpencodeAgent/workspace/scripts/microstructure/source_registry.py` | 三处 `SourceField("pct_change", "Float64", ...)` | `pct_chg` |
| `tools/test_semantic_layer_gate.py`、`OpencodeAgent/vendor/Vibe-Trading/tools/test_semantic_layer_gate.py` | 测试夹具里的 `"name": "pct_change"` | `pct_chg` |
| `OpencodeAgent/workspace/scripts/microstructure/rejection_report.py` | 注释 `"fund_daily.trade_date is VARCHAR (YYYYMMDD) — always CAST to DATE"` | 该说明已过期：现在是 `Date`，直接比较即可 |

`df["close"].pct_change(...)` 是 pandas 方法，与本次改名无关。

**完成判据**：`grep -rn "pct_change" --include="*.py" --include="*.json" .` 的结果里，只剩 pandas 方法调用与 `OpencodeAgent/tests/evals/**` 的无关用法。

### 3. 给 8 张新表补三件套

| 位置 | 要求 |
|---|---|
| `schema/clickhouse/catalog/catalog.yaml` | `tables.<name>` 一条 |
| `OpencodeAgent/skills/semantic-layer/tables/<name>.md` | 列清单与 metadata.json **完全一致且同序** |
| `OpencodeAgent/skills/semantic-layer/INDEX.md` | 链接 `tables/<name>.md` |

列清单从实库取，不要手写：

```sql
SELECT name, type FROM system.columns
WHERE database='ashare' AND table='stk_daily' ORDER BY position;
```

**完成判据**：`python tools/ci_semantic_layer_gate.py` 输出里 `column list mismatch` / `no catalog entry` / `missing tables/` 三类计数都是 `0`。

### 4. 把口径写进表文档

第 3 步只管列名对不对，管不了"这个数是什么"。下面 8 条是仓库里查不到、只能从上游带的：

| 表 | 必须写进文档的口径 |
|---|---|
| `stk_margin_sum` | **交易所汇总**，每日 3 行（SSE / SZSE / BSE）。个股两融在 `stk_margin`。 |
| `idx_daily_ts` | tushare `index_daily` 口径，与 `idx_daily_dc`（东财）**不同源**，同一天同一指数数值有差，指定一个当基准。 |
| `idx_sw_daily` | 申万 2021 版行业指数行情，配合 `idx_sw_classify` / `idx_sw_member_all`。 |
| `stk_kpl_list` | 开盘啦榜单，稀疏约 77 行/日。没有上榜的日子该股票**没有行**，空值是"未上榜"而非缺失。 |
| `fin_mainbz` | 只采集 `type=P`（**产品**维度）；`_param_type` 823,220 行全为空串（接口不回填）。地区 `B`、行业 `D` 未采集。 |
| `stk_cyq_chips` | 只保留**最近 15 个自然日**（实测 11 个交易日）、4,248 只（有筹码数据的股票）、4,713,330 行；不是全市场全历史。 |
| `stk_moneyflow_hsgt` | 6 个数值列是 **`String`**，单位**百万元**；北向自 **2024-08-30** 起披露口径变更，跨期比较需谨慎。 |
| `dim_etf_index_map` | 838 行中只有 **9 条 `match_method='verified'`**（人工核验，confidence 1.0），其余 829 条是 benchmark 文本匹配。做 ETF 归因时用 verified，1,044 只匹配不上的**没有写入**。 |

**完成判据**：8 个 `tables/<name>.md` 分别能检索到上表的关键短语（`交易所汇总` / `不同源` / `申万 2021` / `未上榜` / `type=P` / `最近 15 个自然日` / `2024-08-30` / `verified`）。

### 5. 跑 gate

```bash
python tools/ci_semantic_layer_gate.py; echo "rc=$?"
```

**完成判据**：`rc=0`。

---

## 结构变更

### 列改名（5 张）

`pct_change` → `pct_chg`，类型 `Float64`、单位（百分数，`6.33` = +6.33%）不变，与 tushare 官方字段名及库内其余表一致。

`idx_daily_dc` · `idx_factor_pro` · `idx_quote_dc` · `stk_moneyflow_ths` · `stk_top_list`

采集侧有 `column_map` 兜底，上游返回的 `pct_change` 会自动改名入库。

### 列改类型（1 张）

`fund_daily.trade_date`：`String`（`'YYYYMMDD'`）→ **`Date`**。3,248,478 行未丢，区间 1998-04-07 ～ 2026-10-29。

参数绑定建议写 `{d:Date}`；写成 `{d:String}` 仍能隐式转换，但会失去分区裁剪。

### 新增 8 张

| 表 | 行数 | 粒度 | 主键 | 覆盖 |
|---|---:|---|---|---|
| `stk_daily` | 2,399,177 | 日 × 股票 | `ts_code,trade_date` | 2025-01-02 起 |
| `idx_daily_ts` | 4,532,881 | 日 × 指数 | `ts_code,trade_date` | 2025-01-02 起 |
| `idx_sw_daily` | 193,160 | 日 × 申万行业 | `ts_code,trade_date` | 2025-01-02 起 |
| `stk_kpl_list` | 33,973 | 日 × 股票 | `trade_date,ts_code` | 2025-01-02 起 |
| `stk_margin_sum` | 1,319 | 日 × 交易所 | `trade_date,exchange_id` | 2025-01-02 起 |
| `stk_moneyflow_mkt_dc` | 440 | 日 | `trade_date` | 2025-01-02 起 |
| `fund_info` | 3,008 | 基金 | `ts_code` | 全量 |
| `dim_etf_index_map` | 838 | ETF → 指数 | `ts_code` | 全量 |

`stk_daily` 是**未复权原始行情，11 列**。`stk_factor_pro` 里已有同样的 OHLC（261 列，含复权与技术因子）：取原始行情用 `stk_daily`，做因子/复权用 `stk_factor_pro`。

`idx_daily_ts` 与 `idx_sw_daily` 含 **>8 个 `Nullable(Float64)` 列**，`uniqExact(a,b,c...)` 会直接报 `Maximum number of arguments for aggregate function with Nullable types`；去重用 `uniqExact(tuple(...))`，聚合前 `assumeNotNull()`。

### 行数变化（12 张）

| 表 | 变更前 | 变更后 | 原因 |
|---|---:|---:|---|
| `stk_holdernumber` | 8,400,771 | 292,797 | 去重 810 万 |
| `stk_holdertrade` | 4,766,765 | 106,079 | 去重 466 万 |
| `stk_ccass_hold` | 1,975,161 | 1,900,634 | 去重 7.5 万 |
| `stk_pledge_detail` | 278,739 | 207,606 | 去重 7.1 万 |
| `stk_block_trade` | 275,700 | 253,676 | 去重 2.2 万 |
| `stk_top_list` | 84,604 | 84,331 | 去重 273 |
| `stk_repurchase` | 36,536 | 36,482 | 去重 54 |
| `fin_audit` | 7,938 | **88,796** | 从稀疏变完整（5,514 只全历史） |
| `stk_rewards` | 3,704,315 | 1,510,588 | 去重 219 万 |
| `stk_pledge_stat` | 2,407,677 | 1,648,923 | 去重 76 万 |
| `fin_mainbz` | 1,492,850 | 823,220 | 去重 67 万 |
| `stk_cyq_chips` | 0 | 4,713,330 | 首次采集 |

行数变少的表都是**去掉整行副本**，不是内容减少：重建后逐表校验 `count() == uniqExact(全列)`，差异为 0。语义层里写了行数断言、覆盖率分母、`count()` 快照的地方按新值改。

---

## 刷新节奏

| 表 | 节奏 |
|---|---|
| 其余全部 `trade_date` / `period` 表 | 日更 18:30（`daily_sync`）+ 次日 07:00 补采 |
| `fin_audit` `fin_mainbz` `stk_pledge_stat` `stk_rewards` `stk_cyq_chips` | **周更，周日 08:00** |

那 5 张表的接口只认 `ts_code`，没有批量维度，一次全量 5,577 次外呼，所以按周跑影子表全量交换。判"数据是否过期"时它们最大可以到 7 天，且 `fin_audit` / `fin_mainbz` 的 SLA 是 **200 天**（最新期是半年报，8 月底才披露完）。

---

## 验证查询

```sql
-- 表数量
SELECT count() FROM system.tables WHERE database='ashare';          -- 65

-- 新表覆盖
SELECT 'stk_daily', count(), min(trade_date), max(trade_date) FROM ashare.stk_daily
UNION ALL SELECT 'idx_daily_ts', count(), min(trade_date), max(trade_date) FROM ashare.idx_daily_ts
UNION ALL SELECT 'idx_sw_daily', count(), min(trade_date), max(trade_date) FROM ashare.idx_sw_daily
UNION ALL SELECT 'stk_kpl_list', count(), min(trade_date), max(trade_date) FROM ashare.stk_kpl_list
UNION ALL SELECT 'stk_moneyflow_mkt_dc', count(), min(trade_date), max(trade_date) FROM ashare.stk_moneyflow_mkt_dc
UNION ALL SELECT 'stk_margin_sum', count(), min(trade_date), max(trade_date) FROM ashare.stk_margin_sum;

-- 去重后应为 0
SELECT count() - uniqExact(tuple(*)) FROM ashare.stk_rewards;
```

上游侧当前状态：66 条契约 / 65 张表通过 `preflight`，`audit_coverage` 0 脱管 0 悬空 0 超 SLA，`check_freshness` 0 失败。

---

## 已知未做（供排期，不影响本次更新）

1. `stk_moneyflow_hsgt` 的 6 个 `String` 数值列改 `Float64`。
2. `idx_weight.trade_date`、`stk_dividend.*_date`、`idx_info` 等的日期列仍是 `String`（本次只改了 `fund_daily`）。
3. `fin_mainbz` 的地区 `B`、行业 `D` 维度。
4. 新表的覆盖期从 2025-01-02 开始，需要更长历史要向上游提。
5. 分区改造 / ReplacingMergeTree：会改变读取语义，需两边同一窗口改。