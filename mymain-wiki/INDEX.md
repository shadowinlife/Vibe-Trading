---
title: mymain-wiki 全量文档索引
description: wiki 内每份文档的一行式索引（路径 + 一句话 + 状态）。路由表未命中时在这里全文检索。触发词：索引、INDEX、全部文档、目录。
type: reference
status: active
created: 2026-08-30
updated: 2026-08-31
tags: [index]
---

# INDEX — 全量文档索引

> 每条一行：路径 — 注释 — 状态。状态语义：`active` 活文档（随分支演进）；
> `archived` 归档冻结（只读证据，内容不改）。

## 入口

- [AGENTS.md](AGENTS.md) — agent 路由与使用协议，先读 — active
- [README.md](README.md) — 人类入口（GitHub 渲染） — active

## branch/ — 分支治理

- [branch/MYMAIN_DIVERGENCE.md](branch/MYMAIN_DIVERGENCE.md) — 与上游差异权威台账：F1-F7、贡献队列、验证门禁、债务 D1-D4 — active
- [branch/MYMAIN_README.md](branch/MYMAIN_README.md) — 发布 changelog：基线/迭代/验证基线/ tag 约定 — active

## features/ — 迭代功能卡

- [features/README.md](features/README.md) — 功能索引与状态表 — active
- [features/f1-reflection-lessons.md](features/f1-reflection-lessons.md) — 反思课程存储（memory） — active
- [features/f2-mcp-memory-tools.md](features/f2-mcp-memory-tools.md) — MCP 记忆工具 ×5 — active
- [features/f3-backtest-reflect-hook.md](features/f3-backtest-reflect-hook.md) — 回测自动反思钩子 — active
- [features/f4-memory-guard.md](features/f4-memory-guard.md) — MemoryGuard 中间件 + 项目目录存储 — active
- [features/f5-clickhouse-data-source.md](features/f5-clickhouse-data-source.md) — ClickHouse A 股数据源 + 语义层 Phase 0-2 — active
- [features/f7-opencode-agent.md](features/f7-opencode-agent.md) — OpencodeAgent harness 层（含 12 领域子代理） — active

## harness-evolution/ — harness 演进研究档案（2026-08-21~30）

- [harness-evolution/README.md](harness-evolution/README.md) — 导航：裁决总表 / 阅读顺序 / 未闭合线索 — active
- [harness-evolution/HARNESS_EVOLUTION_SUMMARY.md](harness-evolution/HARNESS_EVOLUTION_SUMMARY.md) — 总收口（zh；en/zh 变体同目录） — archived
- [harness-evolution/HARNESS_EVOLUTION_ROADMAP.md](harness-evolution/HARNESS_EVOLUTION_ROADMAP.md) — 28 PLAN 总表 + A/B/C/D 批执行结果 — archived
- [harness-evolution/HARNESS_EVOLUTION_CAPABILITY_AUDIT.md](harness-evolution/HARNESS_EVOLUTION_CAPABILITY_AUDIT.md) — 能力审计 v2（K/G/Q 编号、路由决策表、子代理草案） — archived
- [harness-evolution/HARNESS_EVOLUTION_RESEARCH.md](harness-evolution/HARNESS_EVOLUTION_RESEARCH.md) — 架构调研（32 框架、缓存纪律） — archived
- [harness-evolution/HARNESS_EVOLUTION_PAPERS.md](harness-evolution/HARNESS_EVOLUTION_PAPERS.md) — 论文证据索引（A-I 九类） — archived
- [harness-evolution/HARNESS_EVOLUTION_BENCHMARKS.md](harness-evolution/HARNESS_EVOLUTION_BENCHMARKS.md) — 评测基准调研 — archived
- [harness-evolution/HARNESS_EVOLUTION_TOOL_MAPPING.md](harness-evolution/HARNESS_EVOLUTION_TOOL_MAPPING.md) — 内部名 ↔ MCP 名权威映射 — archived
- [harness-evolution/HARNESS_EVOLUTION_MCP_GAP_REVIEW.md](harness-evolution/HARNESS_EVOLUTION_MCP_GAP_REVIEW.md) — MCP 面缺口评审（D3 裁决依据） — archived
- [harness-evolution/HARNESS_EVOLUTION_P0_PLAN.md](harness-evolution/HARNESS_EVOLUTION_P0_PLAN.md) — Wave 1 执行 + A 批终局 + 上游 PR 拆分（暂缓） — archived
- [harness-evolution/HARNESS_EVOLUTION_A5_A8_TEST_PLAN.md](harness-evolution/HARNESS_EVOLUTION_A5_A8_TEST_PLAN.md) — A5-A8 量化测试计划 — archived
- [harness-evolution/HARNESS_EVOLUTION_B_TEST_PLAN.md](harness-evolution/HARNESS_EVOLUTION_B_TEST_PLAN.md) — B 批预注册判据 — archived
- [harness-evolution/HARNESS_EVOLUTION_C_PLAN.md](harness-evolution/HARNESS_EVOLUTION_C_PLAN.md) — C 批计划（已回滚思路） — archived
- [harness-evolution/HARNESS_EVOLUTION_D_PLAN.md](harness-evolution/HARNESS_EVOLUTION_D_PLAN.md) — D 批试点计划 — archived
- [harness-evolution/HARNESS_EVOLUTION_D2_PLAN.md](harness-evolution/HARNESS_EVOLUTION_D2_PLAN.md) — D2 推进计划（状态机 + revision log） — archived
- [harness-evolution/HARNESS_EVOLUTION_D2_RESUME.md](harness-evolution/HARNESS_EVOLUTION_D2_RESUME.md) — D2 恢复上下文 — archived
- [harness-evolution/evals/](harness-evolution/evals/) — 研究代码与证据：tool_selection（评测基建+裁决 artifacts）、agent_eval、harness_bench、tests — archived

## clickhouse/ — ClickHouse 研究档案

- [clickhouse/README.md](clickhouse/README.md) — 导航与 R1 关键裁决 — active
- [clickhouse/CLICKHOUSE_SEMANTIC_LAYER_REPORT.md](clickhouse/CLICKHOUSE_SEMANTIC_LAYER_REPORT.md) — R1 中文决策报告 — active
- [clickhouse/CLICKHOUSE_SEMANTIC_LAYER_RESEARCH.md](clickhouse/CLICKHOUSE_SEMANTIC_LAYER_RESEARCH.md) — R1 英文调研原文 — active
- [clickhouse/CLICKHOUSE_ITERATION_PLAN.md](clickhouse/CLICKHOUSE_ITERATION_PLAN.md) — Phase 0-2 已落地 / Phase 3 未启动 — active
- [clickhouse/CLICKHOUSE_SYNC_DIAGNOSIS.md](clickhouse/CLICKHOUSE_SYNC_DIAGNOSIS.md) — 同步停更诊断（postmortem） — active

## history/

- [history/timeline.md](history/timeline.md) — 分支编年史（发布/rebase/大事件，倒序） — active
