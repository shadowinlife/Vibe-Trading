---
title: mymain 分支编年史（Timeline）
description: mymain 分支从 2026-07 记忆工作起步到当前的完整时间线：每次 rebase/merge 对齐、发布、特性落地与研究周期。定位「什么时候发生了什么」时读这里。触发词：历史、timeline、编年、发布、对齐、rebase。
type: reference
status: active
created: 2026-08-30
updated: 2026-08-31
tags: [history, timeline, changelog, mymain]
related: [../branch/MYMAIN_DIVERGENCE.md, ../branch/MYMAIN_README.md]
---

# mymain 分支编年史

> 倒序表。发布细节见 [../branch/MYMAIN_README.md](../branch/MYMAIN_README.md)，差异与验证基线见 [../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md)（表中简称 DIVERGENCE），功能细节见 [../features/README.md](../features/README.md)。
> 注：2026-08-11（merge+carve）与 2026-08-28（rebase）两次历史重整改写了分支上的 commit SHA；表内 pre-carve SHA 可在备份分支 `backup/mymain-pre-rebase-20260804` 查到，当前分支 SHA 以 `git log main..mymain` 为准。

| 日期 | 事件 | 证据 |
|---|---|---|
| 2026-08-31 | 生产部署：ECS `120.26.181.156` 同步至 `273520d0`（含 D 批 12 领域子代理 + 主循环收敛）；宿主 `.opencode/` 补齐 `subagents.json`/`prompts/`/新 `render_config.py`/新版工具治理清单并重渲染；验证：MCP 82、网关 401/200、memory_status ok、ch_list_tables 57 表、task→market-data-agent 委派 e2e 通过；CH 数据追平至 20260828（stk_factor_pro/idx_weight/stk_margin 迟一日属上游发布延迟，fail-closed 次日重试） | 部署会话记录；[../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md) §3.3 |
| 2026-08-30 | rebase 对齐上游 `fb5013c2`（`80ffdda4` 后 79 commit，34 个本地 commit 重放，1 处真冲突）；历史卫生：F2 的 `.omo` 会话文件与 Phase 2 的冲突标记经 edit 停点出史；release/mymain 2026-08-30 发布 | [../branch/MYMAIN_README.md](../branch/MYMAIN_README.md)；DIVERGENCE §5 2026-08-30 条 |
| 2026-08-30 | D4 生产同步：9 个准入域子代理上岗（`07a08aab`）；trading-connector 经 DEC-5 mini-admission 成为第 12 席（`b5a7265b`） | [../harness-evolution/README.md](../harness-evolution/README.md) 裁决总表 |
| 2026-08-29 | 主循环收敛：13 个域工具移入子代理独占，主表面 59→46（`552c7bfe`）；D 批 D2-1/D2-2/D2-3 同日收官 | [../harness-evolution/HARNESS_EVOLUTION_SUMMARY.md](../harness-evolution/HARNESS_EVOLUTION_SUMMARY.md) §5 |
| 2026-08-28 | rebase 对齐上游 `80ffdda4`（v0.1.14 后 117 commit，22 个本地 commit 重放，4 处真冲突）；上轮 merge 解法经 reconciliation `8a05a7c1` 回收；release/mymain 2026-08-28 发布；D 批试点子代理落地（`43cf7624` + `6f61a2c5`） | [../branch/MYMAIN_README.md](../branch/MYMAIN_README.md)；DIVERGENCE §5 2026-08-28 条 |
| 2026-08-21 | merge 对齐上游 `1907e47d`（v0.1.14，+183 commit）；计数升至 MCP 77/82、skills 91、引擎 10；OpencodeAgent 接线优化 O1-O5（`6a8a6cef`） | DIVERGENCE §5 2026-08-21 两条 |
| 2026-08-21 ~ 08-30 | harness 演进研究周期：A/B/C/D 四批预注册实验与裁决（A 描述治理回滚、B 暴露面工程暂缓上游、C 路由层回滚、D 子代理生产落地） | [../harness-evolution/README.md](../harness-evolution/README.md) |
| 2026-08-18 ~ 08-19 | OpencodeAgent 部署加固组：跨架构构建 `26758f8b`、权限与健康检查 `81a324b1` / `5aded1e8` / `220cf754`、构建文件跟踪 `507f340f` | [../features/f7-opencode-agent.md](../features/f7-opencode-agent.md) |
| 2026-08-17 | merge 对齐 `0713336c`（上游 +144 commit）并发布（发布 commit `57bf9563`）；同日第二次发布引入 F7 OpencodeAgent（pre-rebase `35bb27a1` / `8b89d1b3`）；#1062 volume 单位缺陷闭环 | [../branch/MYMAIN_README.md](../branch/MYMAIN_README.md)；DIVERGENCE §5 2026-08-17 条 |
| 2026-08-12 | ClickHouse 语义层 Phase 0-2 全落地（`9d7c7f2c` / `5672d6cc` / `a4f19f25`）+ 研究文档（`80516eac`，fork PR #1）；宿主侧同步管道双根因修复与回填追平 | [../clickhouse/README.md](../clickhouse/README.md)；DIVERGENCE §5 2026-08-12 条 |
| 2026-08-11 | merge+carve 对齐 v0.1.13（`c33133f4`），历史重整为 6 个单功能 commit（F1→F5+docs，当前 SHA `6a612498`…`388d2e3a`）；MYMAIN_README 发布记录创立（`11706c67`）；同日增量对齐 `1bf1d8b4`；#1062 修复 PR #1065/#1067 上游合入；发布 commit `9217c701` | [../branch/MYMAIN_README.md](../branch/MYMAIN_README.md)；DIVERGENCE §5 2026-08-11 两条 |
| 2026-08-07 | rebase 对齐 `6c44732`（上游 +52 commit）；上游承接 #973/#972/#974，memory 分歧面收窄至 5 个纯增量文件 | DIVERGENCE §5 2026-08-07 条 |
| 2026-08-04 | rebase 对齐 `3a752d5`；env 门禁合规修复、guard 自触发排除、回测反思入口去重 | DIVERGENCE §5 2026-08-04 条 |
| 2026-07-28 | F4 MemoryGuard + F5 ClickHouse 基础数据源首批 commit（pre-carve `174d991b` / `781c27cf`） | [../features/f4-memory-guard.md](../features/f4-memory-guard.md)、[../features/f5-clickhouse-data-source.md](../features/f5-clickhouse-data-source.md) |
| 2026-07-27 | 分支记忆工作启动：F1/F2/F3 首批 commit（pre-carve `2b8240dc` / `90095124` / `427251d0`）；同日五-agent 并行评审；分支差异文档创立（`a78f60f9`） | DIVERGENCE §5 2026-07-27 条；[../features/README.md](../features/README.md) |
