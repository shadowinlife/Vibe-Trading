---
title: 功能卡索引（mymain 独有特性 F1-F5、F7）
description: mymain 分支相对上游的独有功能索引。改代码前确认能力归属、查上游回流计划、定位验证基线时先读这里。触发词：F1、F2、F3、F4、F5、F7、功能卡、feature、reflections、memory MCP、MemoryGuard、ClickHouse、OpencodeAgent。
type: index
status: active
created: 2026-08-30
updated: 2026-08-30
tags: [index, features, mymain]
related: [../branch/MYMAIN_DIVERGENCE.md]
---

# 功能卡索引

> 每张卡回答四个问题：它是什么（能力）、改动落在哪（关键文件与开关）、怎么走到今天（开发历史）、怎么验证与何时回流上游（验证 / 状态与上游关系）。
> 权威的差异总表、贡献队列与债务清单在 [../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md)；卡片只做导航与浓缩，不复制大表。

## 状态总表

| # | 名称 | 一句话 | 上游关系 | 卡片 |
|---|------|--------|----------|------|
| F1 | 反思课程存储 | 按策略类型的 append-only JSONL 课程库，记忆系统 T4 迭代的存储底座 | 上游无对应；贡献队列 ② | [f1-reflection-lessons.md](f1-reflection-lessons.md) |
| F2 | MCP 记忆工具 | 五个 memory_* 工具经 MCP 面暴露（默认 OFF） | 上游无对应；贡献队列 ③ | [f2-mcp-memory-tools.md](f2-mcp-memory-tools.md) |
| F3 | 回测反思钩子 | run_backtest 成功后 fire-and-forget 自动沉淀课程 | 上游为 prompt 驱动，机制不同；队列 ④ | [f3-backtest-reflect-hook.md](f3-backtest-reflect-hook.md) |
| F4 | MemoryGuard + 项目目录存储 | FastMCP middleware 自动记忆（零 LLM）+ VT_MEMORY_BASE_DIR | 路径部分队列 ①；中间件部分队列 ⑤（先解决债务 D1/D2） | [f4-memory-guard.md](f4-memory-guard.md) |
| F5 | ClickHouse A 股数据源 + 语义层 | CH 为 A 股首选数据源；语义层 Phase 0-2 含 ch_* 受约束查询通道 | 个人部署独有，不回流 | [f5-clickhouse-data-source.md](f5-clickhouse-data-source.md) |
| F7 | OpencodeAgent harness 层 | opencode + omo + 本仓库 MCP 的独立部署 harness（含 12 子代理花名册） | 个人部署独有，不回流 | [f7-opencode-agent.md](f7-opencode-agent.md) |

## 为什么没有 F6

F6 不曾是独立特性：ClickHouse 语义层（Phase 0-2）在 2026-08-12 落地时即归入 F5 的演进（fork PR #1）。发布记录 2026-08-17 表格里的「F6」行是历史行文，规范引用一律用「F5 语义层」。查语义层资料请读 F5 卡片与 [../clickhouse/README.md](../clickhouse/README.md)。

## 阅读建议

- 改某个功能前：先读对应卡片的「关键文件与开关」与「状态与上游关系」两节，再回 DIVERGENCE 查最新计数基线与贡献队列细节。
- 验证基线数字以 DIVERGENCE §3 与各发布记录为准，卡片只引用当前最新一轮（2026-08-28）。
- 分支级大事顺序见 [../history/timeline.md](../history/timeline.md)。
