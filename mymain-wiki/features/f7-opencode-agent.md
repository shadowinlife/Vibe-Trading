---
title: F7 OpencodeAgent harness 层（OpencodeAgent Harness）
description: opencode + omo + 本仓库 MCP 的独立部署 harness（Docker 镜像 opencode-serve），含问题处理协议、防幻觉纪律与 12 子代理花名册。改部署/子代理/主循环工具面、查 harness 演进裁决时必读。触发词：OpencodeAgent、opencode、子代理、subagent、render_config、harness、钉钉。
type: delta
status: active
created: 2026-08-17
updated: 2026-08-30
tags: [opencode-agent, harness, deployment, subagents]
related: [../branch/MYMAIN_DIVERGENCE.md]
---

# F7 OpencodeAgent harness 层

> 一句话定位：把 mymain 的能力（F1-F4 记忆 + F5 数据层）包装成可独立部署的生产研究 harness；源自独立仓库 vibetrading-opencode-instruct，2026-08-17 引入 `OpencodeAgent/` 管理，个人部署独有、不回流。

## 能力

- 问题处理协议：明确/开放/待澄清/宏观四类分流，开放型走 Least-to-Most 收敛漏斗，待澄清型走槽位澄清，宏观型走 Step-Back 拆分；硬性轮次预算每意图 1 轮 ≤3 问
- 防幻觉与诚实拒答纪律：数字溯源三来源、LLM 禁做数学、弃权一等公民、五要素拒答模板
- 领域资产：escape-top 微观结构信号（CH 数据层 + 7 门验证框架）、三层选股、VT 联邦行情 scanner、cron + 钉钉通知、nano-search-mcp（12 工具）
- 工具治理：`render_config.py` 把工具清单编译为 opencode permission deny 项（启动时生效，被 deny 工具不进模型可见列表），并按 agent 裁剪工具面
- 12 子代理花名册（harness 演进 D 批落地）：主循环工具面 59→46，13 个域工具移入子代理独占

## 关键文件与开关

| 文件 / 开关 | 作用 |
|---|---|
| `OpencodeAgent/`（整目录） | harness 本体：构建脚本、配置模板、prompts/、部署文档 |
| `OpencodeAgent/config/render_config.py` | entrypoint 渲染逻辑的单一事实源（24 项测试） |
| `OpencodeAgent/opencode.json.tmpl` / `oh-my-openagent.json` | 工具面与模型配置（全部 agents 统一 qwen3.8-max） |
| `OpencodeAgent/.env.example` | 容器 env 清单（`CLICKHOUSE_*` / `CLICKHOUSE_LLM_*` / `DASHSCOPE_API_KEY` 等） |

## 开发历史

- 2026-08-17 引入 `OpencodeAgent/`（[../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md) §5 该日条；发布记录 2026-08-17 第二次，pre-rebase F7 commit `35bb27a1`）。引入前在原仓库完成迁移改造：scripts 瘦身（backtest/chanlun/memory 由 VT 能力替代，22 个信号构建器迁入 `vibe_bridge/`）、microstructure/screening/realtime 数据层从 DuckDB 迁至 VT clickhouse_connector 与 market_data 联邦、AGENTS.md 605 行重写（新增问题处理协议与防幻觉拒答纪律两个 CRITICAL 章）。
- 2026-08-18 当前分支 commit `82a9392f`（author date 08-18，发布记 08-17）；08-18~08-19 部署加固组：跨架构构建 `26758f8b`、运行用户与目录权限 `81a324b1` / `5aded1e8`、状态目录预建 `220cf754`、构建文件跟踪 `507f340f`。
- 2026-08-21 `6a8a6cef` 接线优化 O1-O5：工具治理清单落地、AGENTS.md 瘦身 605→388（场景 playbook 迁入 `skills/research-scenarios/`）、模型统一 qwen3.8-max、编排单通道规则（VT swarm 与 OMO 子代理两通道不嵌套、压缩后必须 re-grounding）。
- 2026-08-28 `43cf7624` D1/D2 试点子代理（quant-agent、web-docs-agent）+ `6f61a2c5` 子代理 prompt 与渲染配置同址。
- 2026-08-29 `552c7bfe` 主循环收敛 59→46（单 commit 可回滚）。
- 2026-08-30 `07a08aab` D4 生产同步 9 子代理 + `b5a7265b` trading-connector 第 12 席（DEC-5 mini-admission）。

## 验证

- `OpencodeAgent/tests/test_config_render.py` **24 passed**；nano-search-mcp 回归 **193 passed**；AGENTS.md 450 行护栏防回涨（§5 2026-08-21 条）
- 引入时冒烟：scripts compileall（零 duckdb 残留）、CLI --help、CH 不可达优雅降级 `{"available": false}` + exit 0、shell 语法（MYMAIN_README 2026-08-17 第二次发布）
- D 批量化证据（[../harness-evolution/README.md](../harness-evolution/README.md) 裁决总表 + [../harness-evolution/HARNESS_EVOLUTION_SUMMARY.md](../harness-evolution/HARNESS_EVOLUTION_SUMMARY.md) §3/§5）：R1 路由召回 99.1%、R2 误委派 3.57%、决策读入 −86%、延迟中位 −33%、D4 铺开 9/9 候选准入、trading-connector R1 0.974 / R2 0 / R3 1.000

## 状态与上游关系

- 个人部署独有，**不回流**；消费 F5 语义层 ch_* 工具与 F1-F4 记忆能力（DIVERGENCE §2.1 F7 行）。
- harness 演进研究的未闭合线索（Track B 生产遥测 2026-09-26 兜底读出、D3 运行时映射层按需触发等）见 [../harness-evolution/README.md](../harness-evolution/README.md) 末节。
- 原独立仓库 vibetrading-opencode-instruct 已存档，后续以 `OpencodeAgent/` 为准。
