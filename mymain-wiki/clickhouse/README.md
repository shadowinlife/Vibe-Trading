---
title: ClickHouse 数据源与语义层研究档案
description: F5（ClickHouse A 股数据源 + 语义层 Phase 0-2）的研究与决策文档导航。触发词：ClickHouse、语义层、A 股数据源、ch_query、DDL、单位换算、同步管道。
type: reference
status: active
created: 2026-08-12
updated: 2026-10-29
tags: [clickhouse, data-source, semantic-layer]
related: [../features/f5-clickhouse-data-source.md, ../branch/MYMAIN_DIVERGENCE.md]
---

# ClickHouse 研究档案

> F5 的全部研究/决策文档。功能现状（能力、验证基线、上游关系）见
> [功能卡 F5](../features/f5-clickhouse-data-source.md)；本目录是研究与决策证据。

## 阅读顺序

| 序 | 文档 | 内容 | 何时读 |
|---|---|---|---|
| 1 | [CLICKHOUSE_SEMANTIC_LAYER_REPORT.md](CLICKHOUSE_SEMANTIC_LAYER_REPORT.md) | **中文决策报告**：分层混合 + 语义下沉数据库的结论与理由 | 先要结论 |
| 2 | [CLICKHOUSE_SEMANTIC_LAYER_RESEARCH.md](CLICKHOUSE_SEMANTIC_LAYER_RESEARCH.md) | 英文正式调研（出处链接/架构图/数据流/场景决策示例） | 要证据与出处 |
| 3 | [CLICKHOUSE_ITERATION_PLAN.md](CLICKHOUSE_ITERATION_PLAN.md) | 迭代计划：Phase 0 地基 → Phase 1 主通道 → Phase 2 灵活性通道 → **Phase 3 可选演进（未启动）** | 要落地路线/查 Phase 3 |
| 4 | [CLICKHOUSE_SYNC_DIAGNOSIS.md](CLICKHOUSE_SYNC_DIAGNOSIS.md) | 同步管道诊断与修复全程记录 | 同步管道出问题时 |

关键裁决（R1，2026-08-12）：不引入官方 mcp-clickhouse 作主接口（UInt64 损坏 #111 /
readonly 可击穿 #131 / 无结果上限）；语义下沉数据库（DDL 入仓库 + 列 COMMENT +
llm_role 只读用户）+ 领域工具层为主通道 + sqlglot 守卫的受约束 SQL 逃生舱。

## 代码与生成物（现行入口）

> 以下五处是 2026-10-29 起的**现行入口**；本目录的 REPORT / RESEARCH / ITERATION_PLAN 是决策历史，不再逐表维护。

| 路径 | 作用 | 何时读 |
|---|---|---|
| `schema/clickhouse/metadata.json` | 生产库结构快照（57 表 / 1351 列 / 注释 / 行数 / 水位），生成器输入 | 确认某表/列是否存在 |
| `schema/clickhouse/catalog/catalog.yaml` | 57 表语义契约（domain / grain / primary_key / date_column / asset_class / valuation / units / caveats / examples / related） | 写 SQL 前查表归属与口径 |
| `schema/clickhouse/catalog/rules.md` | R1–R8 判定规则：资产类别路由、ETF→指数、估值回退、空结果分诊、陈旧标注 | 遇到选表/口径歧义 |
| `tools/gen_semantic_layer.py` | 从 metadata + catalog 生成 agent 文档（幂等） | 改了契约或库结构后重生成 |
| `tools/ci_semantic_layer_gate.py` | 一致性门禁（表/列/链接漂移即失败） | CI 或改完契约后 |
| `OpencodeAgent/skills/semantic-layer/` | agent 消费入口：`INDEX.md` + 57 篇表文档 + `RULES.md` | agent 运行时检索 |

更新顺序：改库 → 重新导出 `metadata.json` → 改 `catalog.yaml` / `rules.md` → 跑生成器 → 跑门禁 → 提交生成物。
