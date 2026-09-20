---
title: Engine-Bridge 知识域导航
description: opencode 引擎桥（engine-bridge）全部知识的集中索引——计划正典、19 份证据报告、研究文档、部署实录、功能卡。查 engine-bridge 任何子主题时先读这里定位。触发词：engine-bridge、opencode_bridge、SessionService、多租户、tenancy、spike、baseline、T1-T15、证据、evidence、部署、deployment、router、tenant。
type: index
status: active
created: 2026-09-20
updated: 2026-09-20
tags: [engine-bridge, index, navigation, evidence, deployment]
related: [../AGENTS.md, ../features/f8-engine-bridge.md, ../features/user-auth-system.md, ../branch/MYMAIN_DIVERGENCE.md]
---

# Engine-Bridge 知识域

> engine-bridge 是 mymain 分支最大的独立项目（2026-09-12 ~ 2026-09-20）：把 opencode 变成 vt 网关的外置 agent 引擎（SessionService 置换层），加上用户认证系统（多租户 Phase 1）与多租户容器形态（T10 容器 + T11 薄路由，已通过隔离矩阵验证但未进生产）。
> 本页是导航入口，不复制内容。功能现状见 [../features/f8-engine-bridge.md](../features/f8-engine-bridge.md)（运维圣经）与 [../features/user-auth-system.md](../features/user-auth-system.md)。

## 文档地图

### 计划与设计（正典）

| 文档 | 位置 | 内容 |
|---|---|---|
| engine-bridge 工作计划 | `.omo/plans/opencode-engine-bridge-v2.md` | D3-D11 决策、T1-T15 任务分解、降级清单 14 项来源、五波执行计划 |
| 用户认证设计 | `.omo/plans/vibe-trading-user-auth.md` | 用户认证系统设计正典（D1-D18 决策、scrypt 参数、启动期不变量、前端 capability 门控） |

### 研究证据（一次性交付物，已完成）

| 文档 | 位置 | 内容 | 对应计划任务 |
|---|---|---|---|
| 基线盘点 | `OpencodeAgent/docs/baseline_memo.md` | 镜像族版本矩阵（B1 钉版虚构发现、B5 存储分裂）、组件实测、OmO 版本事实 | T2 |
| Spike 报告 | `OpencodeAgent/docs/spike_report.md` | opencode serve + OmO 事件面验证：8 份 golden traces、GO/NO-GO 门、三个强制条件（QUIESCENCE ≥8s、part.delta 不区分 text/reasoning、idle 按 sessionID 隔离） | T1 |
| 租户隔离矩阵 | `OpencodeAgent/docs/tenancy_report.md` | 93/93 检查全绿、零跨租户可达；六类隔离机制实证（key 认证、Host 保持、SSE 票据、会话存储、IM 双 bot、网络/进程/文件系统） | T12 |

### 执行证据（`.omo/evidence/opencode-engine-bridge-v2/`）

> 按任务分组，每组含 FINDINGS.md（结论）+ 原始数据（json/jsonl/log/截图）。

| 目录 | 任务 | 关键产物 |
|---|---|---|
| `t7-e2e/` | Web E2E 汇合门 | 八组 67 检查全绿；FINDINGS.md + assertion-results.json + 截图（g1-g8） |
| `t8-im/` | IM 零改动验证 | s0/s1/s3/s4 PASS、s2 xfail→T8-1 修复；FINDINGS.md + 各 scenario results |
| `t8-im-run1-debug/` | T8 第一轮调试 | 调试日志与中间状态 |
| `t8-im-run3-phasec-debug/` | T8 第三轮调试 | Phase C 重跑结果 |
| `t9-im-stream/` | IM 流式（_stream_delta 首产者） | mock 3/3 PASS；FINDINGS.md + sse-events.json |
| `t10-container/` | 租户容器构建与 E2E | 无 key 拒启 / Web 9/9 / 崩溃自愈 9/9 / B2 200/403 / SPA / 持久性 |
| `t11-router/` | 薄路由 + 双租户 E2E | 59/59 检查全绿 |
| `t12-tenancy/` | 隔离矩阵（Phase 3 出口门） | **93/93 PASS**；matrix_results.json + cost.json |
| `t13-wrapper/` | scheduled_research MCP wrapper | eval 地板持平（0.4367）；EVAL_COMPARISON.md |
| `t14-goal/` | goal 绑定 E2E | 遵从率 12/12=100%；FINDINGS.md + compliance.md |
| — | Oracle 对抗复审 | `oracle-rereview-report.md`（B1-B5 修正） |
| — | 先行技术调查 | `prior-art-report.md`（32 框架源码级调研） |

### 部署与运维（活文档，随生产变动更新）

| 文档 | 位置 | 内容 |
|---|---|---|
| **生产部署全貌** | `OpencodeAgent/docs/DEPLOYMENT-PROD-ENGINE-BRIDGE.md` | 宿主机裸部署（systemd 双进程 + nginx）：拓扑、配置、运维命令、事故实录、§15 用户认证部署 |
| 多租户镜像 + 薄路由 | `OpencodeAgent/docs/TENANT-IMAGE-GUIDE.md` | T10 容器形态 + T11 路由规范（**未进生产**，但 T12 已验证） |
| 镜像族手册 | `OpencodeAgent/docs/IMAGE-MANUAL.md` | opencode-serve 镜像构建/运行/验证操作手册 |
| 旧部署指南（已归档） | `OpencodeAgent/docs/archive/DEPLOY-GUIDE-opencode-web-host-direct.md` | 宿主机直部署 + nginx 串码形态（已被取代两次） |

### 功能卡（wiki 内）

| 卡片 | 内容 |
|---|---|
| [../features/f8-engine-bridge.md](../features/f8-engine-bridge.md) | engine-bridge 运维圣经：能力边界（降级清单 14 项）、认证配方、Settings 面裁决、回滚程序、开发历史、验证基线 |
| [../features/user-auth-system.md](../features/user-auth-system.md) | 用户认证系统：能力、关键文件、开关、验证、已知遗留、上游关系 |

### 上游审查

| 文档 | 内容 |
|---|---|
| [../branch/UPSTREAM_REPLACEMENT_REVIEW_2026-09-18.md](../branch/UPSTREAM_REPLACEMENT_REVIEW_2026-09-18.md) | engine-bridge 11 能力 KEEP×7 / PARTIAL×4 判定；memory/CH/valuation/skills 逐项审查；上游 PR 候选队列 |

## 关键裁决速查

| 裁决 | 结论 | 出处 |
|---|---|---|
| 引擎恢复语义 | 活性以引擎为准，历史以桥存储为准；永不重发 prompt | f8 卡「启动恢复三分支」 |
| 多租户架构 | 全栈每租户容器 + 薄路由（D2）；共享进程多租户被否决 | 计划 D2 + tenancy_report |
| 生产部署形态 | 宿主机裸部署（systemd 双进程），非容器 | DEPLOYMENT-PROD §1 |
| 用户认证 | 应用层每用户认证取代 nginx 串码；2026-09-20 已部署 | DEPLOYMENT-PROD §15 |
| 钉版 | opencode-ai@1.18.30 + oh-my-openagent@4.19.4 + base v3.0.0-tenant | TENANT-IMAGE-GUIDE §T10.1 |
| 降级清单 | 14 项 opencode 引擎不复制的原生便利，全部成文 | f8 卡「降级清单」 |
| 回滚 | `VIBE_TRADING_ENGINE=native` 一键回退，零残留风险 | f8 卡「回滚程序」 |

## 当前状态

- **engine-bridge 本体**：已进生产（宿主机裸部署，`VIBE_TRADING_ENGINE=opencode`），运行中
- **用户认证**：已进生产（2026-09-20 部署，端到端验证通过）
- **多租户容器形态**：T12 验证通过（93/93），**未进生产**（容器构建太慢，裸部署先行验证架构可行性），列为后续迭代项
- **上游 PR**：候选队列见 [../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md) §2.3 ⑨⑩⑪
