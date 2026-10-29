---
title: 功能卡索引（mymain 独有特性 F1-F5、F7、F9-F12）
description: mymain 分支相对上游的独有功能索引。改代码前确认能力归属、查上游回流计划、定位验证基线时先读这里。触发词：F1、F2、F3、F4、F5、F7、F8、F9、F10、F11、F12、功能卡、feature、reflections、memory MCP、MemoryGuard、ClickHouse、OpencodeAgent、engine-bridge、opencode 引擎桥、策略库、strategy library、策略调度、strategy-run-entry、vt-state-v1、RM schedule tools。
type: index
status: active
created: 2026-08-30
updated: 2026-10-29
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
| F5 | ClickHouse A 股数据源 + 语义层 | CH 为 A 股首选数据源；语义层 Phase 0-2 含 ch_* 受约束查询通道；2026-10-29 起 agent 侧有 57 表目录 + RULES（F5 语义层 v2） | 个人部署独有，不回流 | [f5-clickhouse-data-source.md](f5-clickhouse-data-source.md) |
| F7 | OpencodeAgent harness 层 | opencode + omo + 本仓库 MCP 的独立部署 harness（2026-09-27 起主循环直连 VT MCP 全工具面；子代理层与 nano-search-mcp 已回退） | 个人部署独有，不回流 | [f7-opencode-agent.md](f7-opencode-agent.md) |
| F8 | ~~opencode 引擎桥~~ **已移出（2026-09-27）** | 前端兼容路线（SessionService 置换 + 多租户）整体移至 `mymain-engine-bridge` 分支独立演进；mymain = 纯粹 opencode + omo + VT MCP 路线 | 卡片为冻结墓碑；scheduled_research wrapper 作为纯 MCP 增量保留本分支（队列 ⑦②） | [f8-engine-bridge.md](f8-engine-bridge.md)（archived） |
| F9 | 策略库与调度 | 版本化策略快照 + code/workflow 双门就绪 + 复用定时研究的排程；Web 策略库与排程页 | 本地 mymain 已合入（81df3a992），未回上游；P1 入口/状态/工具已交付（F10-F12） | [f9-strategy-library-scheduling.md](f9-strategy-library-scheduling.md) |
| F10 | 策略运行服务入口 | RM 专用 `/strategy-runs`：run_key 幂等、per-box 寻址、异步 runner + sweeper | 本地 mymain 已合入（585845ad / d90d5308）；未回上游 | [f10-strategy-run-entry.md](f10-strategy-run-entry.md) |
| F11 | 状态导出/导入 | `vt-state-v1` 包：逐文件 digest、SQLite 一致快照、原子替换/回滚、generation marker | 本地 mymain 已合入（d90d5308）；未回上游 | [f11-state-export-import.md](f11-state-export-import.md) |
| F12 | RM 调度工具 | 六个 MCP 工具转发 RM 控制面：create/list/pause/resume/delete/retarget | 本地 mymain 已合入（d90d5308）；未回上游 | [f12-rm-schedule-tools.md](f12-rm-schedule-tools.md) |

## 为什么没有 F6

F6 不曾是独立特性：ClickHouse 语义层（Phase 0-2）在 2026-08-12 落地时即归入 F5 的演进（fork PR #1）。发布记录 2026-08-17 表格里的「F6」行是历史行文，规范引用一律用「F5 语义层」。查语义层资料请读 F5 卡片与 [../clickhouse/README.md](../clickhouse/README.md)。

## 阅读建议

- 改某个功能前：先读对应卡片的「关键文件与开关」与「状态与上游关系」两节，再回 DIVERGENCE 查最新计数基线与贡献队列细节。
- 验证基线数字以 DIVERGENCE §3 与各发布记录为准，卡片只引用当前最新一轮（2026-10-29 F5 子集 92/3 + 语义层门禁 6；2026-10-28 聚焦回归 324 passed、镜像 smoke 26/26）。
- 分支级大事顺序见 [../history/timeline.md](../history/timeline.md)。
