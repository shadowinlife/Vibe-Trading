---
title: F5 ClickHouse A 股数据源与语义层（ClickHouse Data Source + Semantic Layer）
description: mymain 的 A 股数据底座：ClickHouse 首选数据源 + 语义层 Phase 0-2（DDL 快照 / 列注释 / 单位 registry / ch_* 受约束查询通道）。改数据链路、查语义层裁决、对齐上游数据机制前必读。触发词：clickhouse、语义层、ch_query、get_valuation、comments.yaml、数据源路由、F6。
type: delta
status: active
created: 2026-07-28
updated: 2026-10-29
tags: [clickhouse, data-source, semantic-layer, a-share]
related: [../branch/MYMAIN_DIVERGENCE.md]
---

# F5 ClickHouse A 股数据源与语义层

> 一句话定位：个人部署的 A 股数据主力——T-1 历史走本地 ClickHouse（199 列宽表），当日 OHLCV 走网络源联邦，语义层把单位/口径/注释下沉到数据库与仓库；个人部署独有，不回流上游。

## 能力

- CH HTTP connector + OHLCV loader（实现 DataLoaderProtocol）；A 股检测与回退链以 clickhouse 为链首，断 CH 静默回退 tencent/eastmoney 网络链（mootdx 已随上游 #1729 于 2026-10 退出默认链，八源排列）
- 基本面 Provider（回退 Tushare）+ 资金流/龙虎榜/融资融券/北向四只 flow 工具 CH 优先回退
- 语义层 Phase 0：`schema/clickhouse/` 57 表（55 业务 + 2 运维）DDL 快照 + 9 表 444 列 COMMENT + CI 门禁（生产库 COMMENT 444/444 已应用）
- 语义层 Phase 1：显式 199 列消除 `SELECT *`、`_provenance` 单位元数据、`get_valuation` 工具、`clickhouse_units.py` 单位 registry（移除 ×10⁴ 与北向 ×100 硬编码）
- 语义层 Phase 2：`ch_list_tables` / `ch_describe_table` / `ch_query` 受约束灵活性通道（llm_role SELECT-only，sqlglot AST 守卫，绝不回退 default 用户）

## 关键文件与开关

| 文件 / 开关 | 作用 |
|---|---|
| `agent/src/clickhouse_connector.py`、`agent/backtest/loaders/clickhouse.py` | 连接与加载主通道 |
| `agent/src/tools/clickhouse_fallbacks.py` / `clickhouse_query_tool.py` / `clickhouse_explore_tools.py` / `valuation_tool.py` | 工具层 |
| `schema/clickhouse/`、`tools/ci_clickhouse_comments_gate.py` | DDL 快照、comments.yaml、CI 门禁 |
| `schema/clickhouse/catalog/`、`schema/clickhouse/metadata.json` | 表级语义契约（领域/粒度/主键/单位/坑/示例）+ 库结构快照，语义层唯一真相源 |
| `OpencodeAgent/skills/semantic-layer/`、`tools/gen_semantic_layer.py`、`tools/ci_semantic_layer_gate.py` | agent 侧语义目录（57 表一表一档 + RULES 判定规则）、生成器与一致性门禁 |
| `CLICKHOUSE_*`（DataConfig） | 主通道连接配置（远端接入参数见 DIVERGENCE §3.3） |
| `CLICKHOUSE_LLM_USER` / `CLICKHOUSE_LLM_PASSWORD` | 灵活性通道专用只读凭据 |

## 开发历史

- 2026-07-28 基础数据源首次落地（pre-carve `781c27cf`）。
- 2026-08-11 merge+carve 重整为 `388d2e3a`（当前分支 SHA）；同日 #1062 volume 单位修复（上游 PR #1065/#1067）合入，本分支经后续对齐继承（DIVERGENCE §2.4 闭环记录）。
- 2026-08-12 语义层 Phase 0（`9d7c7f2c`）→ Phase 1（`5672d6cc`）→ Phase 2（`a4f19f25`）全落地 + 研究文档（`80516eac`），fork PR #1；宿主侧同步管道修复与回填追平全程见 [../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md) §5 2026-08-12 条与 [../clickhouse/CLICKHOUSE_SYNC_DIAGNOSIS.md](../clickhouse/CLICKHOUSE_SYNC_DIAGNOSIS.md)。**语义层即历史行文中的「F6」，规范引用一律归 F5。**
- 2026-08-17 merge 对齐：fork 语义层回合唯一真冲突在 `market_data.py` provenance 块，与上游 #1065 的 `volume_unit` 合并保留。
- 2026-08-21 对齐后修正 `test_detect_source` / `test_chains_ordered_by_ip_ban_risk` 两处 pin 为本地 clickhouse-first 口径，加 divergence 注释防 rebase 误回退。
- 2026-08-28 rebase：上游 `MARKET_DATA_ORDER_*` 覆盖机制与 F5 互补（override 校验基于本地默认链快照，A 股 clickhouse-first 自动成为被重排基准）。
- 2026-10-10 merge+carve 对齐（基线 `b1f6ce71`）：**北向单位契约修正**——本地 P1.4「万元」判定被上游 #1484 推翻（HKEX 官方统计逐位交叉验证 tushare 原生单位=百万元；P1.4 把 2024-08-19 换手时代值 375048.34 误读为净额数量级 37.5 亿，实为 3750 亿成交额）。修正面：`clickhouse_units.py`（`NORTHBOUND_RAW_MILLION`=10⁶ 断言 + `northbound_raw_to_million_factor()`）、`clickhouse_fallbacks.py`（`fetch_northbound_flow_ch` 输出对齐上游信封：unit=CNY million + note + 2024-08-19 边界 turnover_* 字段，boundary/note 自 tushare_fallbacks 导入防漂移）、comments.yaml `stk_moneyflow_hsgt` 六列标注、三个测试文件（含双时代覆盖）。同轮：A 股链随上游 mootdx 退役收敛为八源排列（三处测试 fixture 同步），northbound_tool 上游 dead-feed 短路（#1481）与本地 CH-first 块共存。
- 2026-10-29 取数链路核验与修复（详见 `.omo/plans/clickhouse-data-repair.md`）：实测确认 ① 21/55 张业务表不在同步注册表内、所有停更表都是孤儿表、`check_freshness` 因只遍历注册表而全绿；② loader 只查 `stk_factor_pro`，ETF/指数必然 0 行并静默降级到 tencent；③ `idx_factor_pro` 已含 000300/000688 的 OHLCV（4103 个代码），指数缺的不是行情而是估值（`idx_dailybasic` 仅 12 个代码且停在 2025-07-04）；④ 北向 2024-08-19 后六列为成交额而非净流入；⑤ `pct_chg`/`pct_change` 跨源漂移、COMMENT 覆盖 444/1351。落地：loader 按资产类别路由（stock→`stk_factor_pro`、ETF→`fund_daily`、index→`idx_factor_pro`，`fund_daily.trade_date` 走 `toDate()`）、provenance 记录实际服务表、`clickhouse=raw` 口径、修掉联邦分支 `resolve_loader` 自解析递归；新增 57 表语义目录（INDEX + 一表一档 + RULES R1-R8）与 CI 门禁。验证：loader/market_data **92 passed / 3 skipped**，语义层门禁 6 passed。

## 验证

- CH 套件（F5 + 语义层 10 文件）**137 passed / 11 skipped**（skip = 需真实 CH 连接；DIVERGENCE §3.1）
- 2026-10-29 子集回归：loader / market_data **92 passed / 3 skipped**；语义层一致性门禁 **6 passed / 0 violations**；schema 门禁 **53 passed / 1 skipped + exit 0**（全量 CH 10 文件套件本轮未重跑）
- schema 门禁 **54 passed** + comments gate exit 0（2026-10-10 基线）；loader 链首 pin **8 passed**
- Phase 2 实测：65 guard 测试 + 17 攻击向量全拒；golden set 经 llm_role 实测 **16/16（100%）**（§5 2026-08-12 条）
- 冒烟：CH 可达时 `get_market_data` 拉 000001.SZ 命中 clickhouse；断 CH 静默回退网络链（§3.2）

## 状态与上游关系

- 个人部署独有，**不回流**（贡献队列标记 ✗，DIVERGENCE §2.3）。
- 研究档案：[../clickhouse/README.md](../clickhouse/README.md)（调研结论、迭代计划、同步诊断）；Phase 3（dbt SL/Cube 可选演进）未启动，单消费者不满足价值判据。
- 与上游机制是互补不是取代：loader 注册沿用上游 `VALID_SOURCES` / `FALLBACK_CHAINS` 模式，`MARKET_DATA_ORDER_*` 覆盖机制在其上正常工作。
