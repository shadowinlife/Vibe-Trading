---
title: mymain-wiki agent 路由入口（AGENTS）
description: mymain 分支持久知识库的路由层与使用协议：任务/触发词到正确文档的映射、权威级别与冲突裁决、harness-evolution 冻结区、wiki 外权威（生产部署/用户认证/engine-bridge 计划）边界。在本分支上工作先读此文件，未命中再去 INDEX 全文检索。触发词：分支知识库、mymain-wiki、路由、权威、台账、DIVERGENCE、功能差异、开发历史、验证证据、研究裁决、未落地、F8 编号、engine-bridge、用户认证、生产部署、证据、evidence、多租户、tenancy。
type: index
status: active
created: 2026-09-20
updated: 2026-09-20
tags: [index, routing, mymain]
related: [INDEX.md, README.md, branch/MYMAIN_DIVERGENCE.md]
---

# AGENTS — mymain-wiki 路由入口

> 这里是 root `AGENTS.md` §8 所称的本分支持久记忆路由入口。本文件只做两件事：把任务路由到正确文档（下表），规定读取与裁决协议（其后三节）。不复制内容；路由未命中时到 [INDEX.md](INDEX.md) 逐条全文检索。
>
> 人类入口：[README.md](README.md)。全量文档索引：[INDEX.md](INDEX.md)。权威台账：[branch/MYMAIN_DIVERGENCE.md](branch/MYMAIN_DIVERGENCE.md)。

## 路由表（五大知识域）

| 知识域 | 典型问题 | 先读 | 随后按需深入 |
|---|---|---|---|
| **功能差异** | 与上游差在哪 / F1-F8 能力与核心文件 / 开关 | [branch/MYMAIN_DIVERGENCE.md](branch/MYMAIN_DIVERGENCE.md) §2（唯一权威台账） | 逐项能力卡 [features/README.md](features/README.md)（导航/浓缩，冲突时让位台账） |
| **开发历史** | 什么时候发生了什么 / rebase 冲突面 / 发布了什么 | [history/timeline.md](history/timeline.md)（倒序大事记） | 逐轮细节 DIVERGENCE §5 迭代笔记；发布记录 [branch/MYMAIN_README.md](branch/MYMAIN_README.md) |
| **验证证据** | 当前测试基线是多少 / 门禁怎么跑 / 计数 pin 义务 | [branch/MYMAIN_DIVERGENCE.md](branch/MYMAIN_DIVERGENCE.md) §3（最新基线唯一出处，当前 2026-09-19 轮）与 §4.3 | 研究周期的实验证据 `harness-evolution/evals/`（已冻结，只读） |
| **研究裁决** | 为什么这么设计 / 哪些本地能力可被上游替换 | [branch/UPSTREAM_REPLACEMENT_REVIEW_2026-09-18.md](branch/UPSTREAM_REPLACEMENT_REVIEW_2026-09-18.md)（REPLACE/PARTIAL/KEEP 判定） | ClickHouse 语义层 [clickhouse/README.md](clickhouse/README.md)（R1 裁决）；harness 四批实验 [harness-evolution/README.md](harness-evolution/README.md)（已冻结） |
| **未落地资产** | 接下来做什么 / 挂起的 PR 前置 / 已知债务 / 残余 todo | [branch/MYMAIN_DIVERGENCE.md](branch/MYMAIN_DIVERGENCE.md) §2.3 贡献队列、§4.5 债务 D1-D4 | 各能力卡末节「状态与上游关系 / 待接入」（如 [features/f8-engine-bridge.md](features/f8-engine-bridge.md)） |
| **engine-bridge 全域** | engine-bridge 计划/证据/部署/研究文档在哪 | [engine-bridge/README.md](engine-bridge/README.md)（集中索引） | 功能卡 [features/f8-engine-bridge.md](features/f8-engine-bridge.md)；用户认证 [features/user-auth-system.md](features/user-auth-system.md) |

任务型路由：

| 正在做的事 | 入口 |
|---|---|
| 改某个功能（F1-F8） | 对应功能卡「关键文件与开关」→ 回 DIVERGENCE §3 核最新基线 |
| 发社区 PR | root `AGENTS.md` §6 约束文件清单逐条过 + DIVERGENCE §4.4（DCO、禁 AI trailer）；上游 pin 测试义务见 §4.3 |
| rebase/merge 对齐后 | DIVERGENCE §4.1 五步流程，第 5 步更新台账本身 |
| 线上排障 / 部署变更 / 生产配置 | **wiki 外**：`OpencodeAgent/docs/DEPLOYMENT-PROD-ENGINE-BRIDGE.md`；接入方式背景 DIVERGENCE §3.3 |
| ClickHouse 取数与语义层（F5） | [features/f5-clickhouse-data-source.md](features/f5-clickhouse-data-source.md) → [clickhouse/README.md](clickhouse/README.md) 及其四份研究文档；同步管道事故 [clickhouse/CLICKHOUSE_SYNC_DIAGNOSIS.md](clickhouse/CLICKHOUSE_SYNC_DIAGNOSIS.md) |
| 记忆系统（F1-F4） | 对应功能卡 → DIVERGENCE §2.3 队列 ①-⑤ 前置 + §4.5 债务 D1-D4 |
| engine-bridge（会话恢复/重挂/降级/回滚） | [engine-bridge/README.md](engine-bridge/README.md)（全域索引）→ [features/f8-engine-bridge.md](features/f8-engine-bridge.md)（运维圣经） |
| 用户认证（登录/注册/鉴权/admin） | [features/user-auth-system.md](features/user-auth-system.md)（功能卡）→ 部署实录 `OpencodeAgent/docs/DEPLOYMENT-PROD-ENGINE-BRIDGE.md` §15 |

常见速查（答案所在，不在此复制）：

| 问题 | 单一答案位置 |
|---|---|
| 全量测试基线现在是多少 | DIVERGENCE §3.1（2026-09-19 轮：14117 passed / 119 skipped / 0 failed） |
| MCP 工具计数被什么钉着 | DIVERGENCE §4.3 上游 pin 测试（本分支 OFF=77 / ON=82，六 README + SKILL.md 同步义务） |
| 本地哪些文件已被上游吸收、该删 | [branch/UPSTREAM_REPLACEMENT_REVIEW_2026-09-18.md](branch/UPSTREAM_REPLACEMENT_REVIEW_2026-09-18.md) 的 REPLACE/PARTIAL/KEEP 判定 |
| 用户认证怎么开、默认行为 | `VIBE_TRADING_USER_AUTH`（默认 0，行为逐字节不变），DIVERGENCE §2.1 F8 行开关列 |
| opencode 引擎怎么回滚 | f8 卡片「回滚程序」节（`VIBE_TRADING_ENGINE=native` 一键回退） |
| 本分支跟踪的上游 Issue/PR | DIVERGENCE §2.4（#1481/#1483，PR #1484/#1486）与 §2.3 ⑥ #1286 |

## 目录地图

| 目录 | 内容 | 状态 |
|---|---|---|
| `branch/` | `MYMAIN_DIVERGENCE.md`（权威台账：F1-F8、贡献队列、债务 D1-D4、验证门禁）、`MYMAIN_README.md`（发布 changelog 与 tag 约定）、`UPSTREAM_REPLACEMENT_REVIEW_2026-09-18.md`（上游可替换性审查） | active |
| `features/` | `README.md` + 能力卡 f1-f5、f7、f8、user-auth-system（F6 不存在，见 features/README「为什么没有 F6」） | active |
| `engine-bridge/` | `README.md` 集中索引：计划正典 / 19 份证据 / 研究文档 / 部署实录 / 功能卡 | active |
| `clickhouse/` | `README.md` + 语义层 RESEARCH / REPORT / ITERATION_PLAN / SYNC_DIAGNOSIS | active |
| `harness-evolution/` | 17 份 `HARNESS_EVOLUTION_*` 裁决文档 + `evals/`（研究代码与 jsonl 证据，9.3MB、占 wiki 97%） | **archived（冻结）** |
| `history/` | `timeline.md` 分支编年史（倒序） | active |
| `INDEX.md` / `README.md` | 全量文档索引 / 人类入口 | active |

## 权威级别与冲突裁决（按序，不投票）

1. **`branch/MYMAIN_DIVERGENCE.md` 是唯一权威台账**：功能差异、贡献队列、债务、验证计数以它为准。
2. `features/` 卡片与 `INDEX.md` 只做导航与浓缩，与台账不一致时**以台账为准**；卡片负责被修正，不负责重裁事实。
3. `status: archived` 内容是冻结证据：可读可引用，**不可改写内容**。`harness-evolution/` 整目录如此——它含可执行 Python 与测试，误改会污染已定版实验的可复现性。
4. 两个编号陷阱是历史事实，**禁止重编号"修复"**：
   - **F6 不存在**：ClickHouse 语义层落地即归入 F5 演进，规范引用写「F5 语义层」（[features/README.md](features/README.md) 有专节说明）。
   - **F8 有两个口径**：台账 DIVERGENCE §2.1 的 F8 = **用户认证系统**（多租户 Phase 1，可上游、队列 ⑨、2026-09-20 已部署）；能力卡 [features/f8-engine-bridge.md](features/f8-engine-bridge.md) 的 F8 = opencode 引擎桥（个人部署独有，不回流）。引用能力一律以名字（用户认证 / engine-bridge），引用编号必须声明所用口径。

## 使用协议

新任务读取顺序：① root `AGENTS.md` §8（分支定位、rebase 义务、社区约束）→ ② 本文件路由表 → ③ 未命中再到 [INDEX.md](INDEX.md) 检索。先读权威文档再读浓缩卡片，反之会携带过期计数。

维护义务：

- 新能力落地 / rebase 对齐 / 发布后：DIVERGENCE（台账）、`branch/MYMAIN_README.md`（发布记录）、`history/timeline.md`（编年）三处按事件同步；wiki 新增文件必须当日登记进 `INDEX.md`，新增知识域或外边界变化同步本文件路由表。
- 本 wiki 是**分支本地个人部署知识**（root `AGENTS.md` §8.3）：F5、F7 与 engine-bridge 类能力个人部署独有不回流，本 wiki 及其索引的文档一律不随社区 PR 上行；仅 DIVERGENCE §2.3 队列 ①-⑨ 对应的回流 patch 走上游（⑩/⑪ 是上游缺陷候选）。
- ⚠️ 安全红线：本仓库是公开 fork。wiki 内外任何文件不得写入真实 token、公网/内网 IP、主机名，基础设施标识一律用占位符（`<ECS_PUBLIC_IP>`、`<CH_VPC_IP>` 等；2026-09-18 清理与暴露面警示见 DIVERGENCE §5 该轮条目）。

## Wiki 外权威（本目录不维护的现行记录）

生产部署与运维文档不在本 wiki（此处无 runbook）：

| 主题 | 权威位置 |
|---|---|
| 线上 ECS 部署全貌（systemd 双进程 + nginx、配置、运维命令、事故实录）——随生产变动更新，2026-09-20 最近全量修订 | `OpencodeAgent/docs/DEPLOYMENT-PROD-ENGINE-BRIDGE.md` |
| 用户认证系统（台账 F8 / 队列 ⑨）：每用户登录 + 邀请码注册替代 nginx 共享串码，**2026-09-20 已部署生产并端到端验证** | 设计 `.omo/plans/vibe-trading-user-auth.md`；部署实录 DEPLOYMENT §15 与 §15.11；台账 DIVERGENCE §2.1 F8 行 |
| engine-bridge 计划正典（D3-D11 决策、T1-T15 任务、降级清单 14 项来源） | `.omo/plans/opencode-engine-bridge-v2.md` |
| OpencodeAgent harness 操作规范（F7） | `OpencodeAgent/AGENTS.md` 与 `OpencodeAgent/docs/` |

## 文档约定

- 每份文档带 YAML frontmatter：`title` / `description`（内嵌**触发词**，检索靠它）/ `type`（delta=差异记录、reference=参考、index=索引）/ `status` / `created` / `updated` / `tags` / `related`。
- 状态语义与 [INDEX.md](INDEX.md) 一致：`active` 活文档（随分支演进）；`archived` 归档冻结（只读证据，内容不改）。
- 正文内链一律相对路径；新增文档当日登记进 INDEX.md 并检查本文件路由表是否需要加行。
- 引用上游仓库对象（Issue/PR/commit）保留完整链接与 SHA，不改写结论；与台账相关的裁决变化只改 DIVERGENCE，本文件与其他索引随之改指针。
