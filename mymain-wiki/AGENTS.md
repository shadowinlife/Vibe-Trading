---
title: mymain-wiki agent 路由入口（AGENTS）
description: mymain 分支持久知识库的路由层与使用协议：任务/触发词到正确文档的映射、权威级别与冲突裁决、harness-evolution 冻结区、2026-09-27 路线裁决（纯 opencode+omo+VT MCP，引擎桥/多租户移出）。在本分支上工作先读此文件，未命中再去 INDEX 全文检索。触发词：分支知识库、mymain-wiki、路由、权威、台账、DIVERGENCE、功能差异、开发历史、验证证据、研究裁决、回退、子代理、nano-search、engine-bridge、多租户。
type: index
status: active
created: 2026-09-27
updated: 2026-10-29
tags: [index, routing, mymain]
related: [INDEX.md, README.md, branch/MYMAIN_DIVERGENCE.md]
---

# AGENTS — mymain-wiki 路由入口

> 这里是 root `AGENTS.md` §8 所称的本分支持久记忆路由入口。本文件只做两件事：把任务路由到正确文档（下表），规定读取与裁决协议（其后三节）。不复制内容；路由未命中时到 [INDEX.md](INDEX.md) 逐条全文检索。
>
> 人类入口：[README.md](README.md)。全量文档索引：[INDEX.md](INDEX.md)。权威台账：[branch/MYMAIN_DIVERGENCE.md](branch/MYMAIN_DIVERGENCE.md)。

## 分支路线（2026-09-27 裁决，先读这个）

mymain = **纯粹的 opencode + omo + VT MCP 路线**：opencode 作为 harness 直接消费本仓库 MCP 全工具面（F7），叠加 F1-F4 记忆、F5 ClickHouse 数据层与 F9 策略库/调度。三项能力已回退/移出（裁决与证据见 DIVERGENCE §5 2026-09-27 条）：

1. **12 领域子代理层**——验证不够有效，主循环直接集成 VT MCP；
2. **nano-search-mcp**——验证不够有效，通用检索由 VT `web_search` 承接，A 股结构化检索为已知缺口待未来引擎；
3. **F8 引擎桥 + 多租户基础设施**——前端兼容路线整体移出本分支，原独立演进于 `mymain-engine-bridge` 分支，**2026-10-10 该线废弃封存**（opencode 桥接方式验证不可行；本 wiki 中 f8 卡与 multitenant 台账为冻结墓碑）。

## 路由表（知识域）

| 知识域 | 典型问题 | 先读 | 随后按需深入 |
|---|---|---|---|
| **功能差异** | 与上游差在哪 / F1-F5、F7、F9-F12 能力与核心文件 / 开关 | [branch/MYMAIN_DIVERGENCE.md](branch/MYMAIN_DIVERGENCE.md) §2（唯一权威台账） | 逐项能力卡 [features/README.md](features/README.md)（导航/浓缩，冲突时让位台账） |
| **开发历史** | 什么时候发生了什么 / rebase 冲突面 / 回退裁决 | [history/timeline.md](history/timeline.md)（倒序大事记） | 逐轮细节 DIVERGENCE §5 迭代笔记 |
| **验证证据** | 当前测试基线是多少 / 门禁怎么跑 / 计数 pin 义务 | [branch/MYMAIN_DIVERGENCE.md](branch/MYMAIN_DIVERGENCE.md) §3（最新基线唯一出处）与 §4.3 | 研究周期的实验证据 `harness-evolution/evals/`（已冻结，只读） |
| **研究裁决** | 为什么这么设计 / 子代理与 nano-search 为什么回退 | DIVERGENCE §5（2026-09-27 回退裁决、2026-09-06 specialist-arch iter3 终局） | ClickHouse 语义层 [clickhouse/README.md](clickhouse/README.md)（R1 裁决）；harness 四批实验 [harness-evolution/README.md](harness-evolution/README.md)（已冻结） |
| **未落地资产** | 接下来做什么 / 挂起的 PR 前置 / 已知债务 | DIVERGENCE §2.3 贡献队列、§4.5 债务 D1-D4、§4.6 待办研究 | 各能力卡末节「状态与上游关系」 |

任务型路由：

| 正在做的事 | 入口 |
|---|---|
| 改某个功能（F1-F5、F7、F9-F12） | 对应功能卡「关键文件与开关」→ 回 DIVERGENCE §3 核最新基线 |
| 发社区 PR | root `AGENTS.md` §6/§8.3 约束文件清单逐条过 + DIVERGENCE §4.4（DCO、禁 AI trailer）；上游 pin 测试义务见 §4.3 |
| rebase/merge 对齐后 | DIVERGENCE §4.1 五步流程，第 5 步更新台账本身 |
| 部署 / 生产配置（纯 opencode 路线） | `OpencodeAgent/docs/DEPLOY-GUIDE.md`（host-direct）+ `IMAGE-MANUAL.md`；接入方式背景 DIVERGENCE §3.3 |
| ClickHouse 取数与语义层（F5） | [features/f5-clickhouse-data-source.md](features/f5-clickhouse-data-source.md) → [clickhouse/README.md](clickhouse/README.md)（现行代码入口 `schema/clickhouse/catalog/` + `OpencodeAgent/skills/semantic-layer/`）；同步管道事故 [clickhouse/CLICKHOUSE_SYNC_DIAGNOSIS.md](clickhouse/CLICKHOUSE_SYNC_DIAGNOSIS.md) |
| 记忆系统（F1-F4） | 对应功能卡 → DIVERGENCE §2.3 队列 ①-⑤ 前置 + §4.5 债务 D1-D4 |
| 策略库 / 调度（F9） | [features/f9-strategy-library-scheduling.md](features/f9-strategy-library-scheduling.md) → 该卡「调度模型」「P1 服务入口」；RM 入口 [F10](features/f10-strategy-run-entry.md)、状态镜像 [F11](features/f11-state-export-import.md)、六个调度工具 [F12](features/f12-rm-schedule-tools.md)；差异台账见 DIVERGENCE §2.1 F9 行 |
| 设计新手引导 / 意图澄清 / 能力发现 | [agent-ux/2026-10-27-novice-onboarding-guidance.md](agent-ux/2026-10-27-novice-onboarding-guidance.md)（7 个方案 + 本分支实验证据 + 外部产品/论文 + 代价） |
| RM 交付 / 验收 | 交接文档 `aliyun-acs-agent-service/docs/iterations/strategy-schedule-vt-handoff.md`（仓外路径）→ 镜像 tag/digest、API 示例、E2E 清单；VT 侧细节 F10-F12 |
| 引擎桥 / 多租户（**已移出本分支，2026-10-10 线废弃封存**） | 墓碑：[features/f8-engine-bridge.md](features/f8-engine-bridge.md)、[multitenant/MULTI_TENANT_GAP_ANALYSIS.md](multitenant/MULTI_TENANT_GAP_ANALYSIS.md)；`mymain-engine-bridge` 分支为只读封存档案（无活文档），本分支不索引其细节 |

常见速查（答案所在，不在此复制）：

| 问题 | 单一答案位置 |
|---|---|
| 全量测试基线现在是多少 | DIVERGENCE §3.1 / §5 最新一轮（2026-10-27 对齐：routing 184、memory 353/3；2026-10-29 F5 子集 92/3 + 语义层门禁 6） |
| MCP 工具计数被什么钉着 | DIVERGENCE §4.3 上游 pin 测试（本分支 OFF=94 / ON=99，**七份** README + SKILL.md 同步义务） |
| 子代理/nano-search 为什么回退、证据在哪 | DIVERGENCE §5 2026-09-27 条 + 2026-09-06 specialist-arch iter3 终局条 |
| A 股结构化检索缺口怎么补 | DIVERGENCE §4.6 待办研究（未来搜索替换引擎） |
| 本分支跟踪的上游缺陷 | DIVERGENCE §2.4 |

## 目录地图

| 目录 | 内容 | 状态 |
|---|---|---|
| `branch/` | `MYMAIN_DIVERGENCE.md`（权威台账：F1-F5/F7/F9、贡献队列、债务 D1-D4、验证门禁）、`MYMAIN_README.md`（发布 changelog 与 tag 约定） | active |
| `features/` | `README.md` + 能力卡 f1-f5、f7、f9-f12（F6 不存在，见 features/README「为什么没有 F6」）；f8 卡为冻结墓碑 | active（f8 archived） |
| `clickhouse/` | `README.md` + 语义层 RESEARCH / REPORT / ITERATION_PLAN / SYNC_DIAGNOSIS | active |
| `multitenant/` | 多租户服务化差距台账（engine-bridge 线动机文档） | **archived（墓碑，随引擎桥线移出）** |
| `harness-evolution/` | 17 份 `HARNESS_EVOLUTION_*` 裁决文档 + `evals/`（研究代码与 jsonl 证据） | **archived（冻结）** |
| `history/` | `timeline.md` 分支编年史（倒序） | active |
| `INDEX.md` / `README.md` | 全量文档索引 / 人类入口 | active |

## 代码 → 文档（按仓库目录）

| 代码区域 | 文档入口 |
|---|---|
| `agent/src/memory/` + `agent/src/skills/memory-lifecycle/` | F1-F4 卡片 → DIVERGENCE §2.1 |
| `agent/backtest/loaders/` + `agent/src/clickhouse_*` + `schema/clickhouse/` + `OpencodeAgent/skills/semantic-layer/` | F5 卡片 → [clickhouse/README.md](clickhouse/README.md) → `catalog/rules.md` |
| `agent/src/strategy_library/` + `strategy_runs/` + `state_export/` + `agent/src/api/strategy_*` + `frontend/src/pages/Strategy*` | F9 卡片 → DIVERGENCE §2.1 F9 行 |
| `OpencodeAgent/`（skills / config / tests / workspace） | F7 卡片 → `OpencodeAgent/docs/{DEPLOY-GUIDE,IMAGE-MANUAL}.md` |
| `mymain-wiki/` | 本文件 → [INDEX.md](INDEX.md) |

## 权威级别与冲突裁决（按序，不投票）

1. **`branch/MYMAIN_DIVERGENCE.md` 是唯一权威台账**：功能差异、贡献队列、债务、验证计数以它为准。
2. `features/` 卡片与 `INDEX.md` 只做导航与浓缩，与台账不一致时**以台账为准**；卡片负责被修正，不负责重裁事实。
3. `status: archived` 内容是冻结证据：可读可引用，**不可改写正文**。`harness-evolution/` 整目录与 f8/multitenant 墓碑如此——墓碑只允许追加「移出注记」，不允许复活运维细节。
4. 编号陷阱是历史事实，**禁止重编号"修复"**：
   - **F6 不存在**：ClickHouse 语义层落地即归入 F5 演进，规范引用写「F5 语义层」（[features/README.md](features/README.md) 有专节说明）。
   - **F8 已移出**：历史上 F8 = opencode 引擎桥（SessionService 置换）。2026-09-27 起 mymain 不承载 F8；引用 F8 一律指引擎桥线的历史编号。

## 使用协议

新任务读取顺序：① root `AGENTS.md` §8（分支定位、对齐义务、社区约束）→ ② 本文件路由表 → ③ 未命中再到 [INDEX.md](INDEX.md) 检索。先读权威文档再读浓缩卡片，反之会携带过期计数。
