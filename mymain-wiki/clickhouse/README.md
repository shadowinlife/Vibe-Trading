---
title: ClickHouse 数据源与语义层研究档案
description: F5（ClickHouse A 股数据源 + 语义层 Phase 0-2）的研究与决策文档导航。触发词：ClickHouse、语义层、A 股数据源、ch_query、DDL、单位换算、同步管道。
type: reference
status: active
created: 2026-08-12
updated: 2026-08-30
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
