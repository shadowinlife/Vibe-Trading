---
title: Harness Evolution 研究档案导航
description: harness 演进研究（披露税治理，2026-08-21~30）的导航与裁决总表——A/B/C/D 四批结论、文档地图、evals 研究代码归档、未闭合线索。触发词：harness、披露税、子代理、search_tools、路由、评测、D 批、回滚。
type: reference
status: active
created: 2026-08-30
updated: 2026-08-30
tags: [harness, routing, evals, subagent, archive]
related: [../AGENTS.md, ../branch/MYMAIN_DIVERGENCE.md]
---

# Harness Evolution 研究档案（2026-08-21 ~ 2026-08-30）

> 本目录是 `mymain` 分支对 **harness 演进研究**的完整归档：问题审计 → 论文/开源调研 →
> 四个方案批次（A/B/C/D）的预注册实验 → 裁决与生产落地。
> 全部文档保持原始文件名与原始内容（仅归档所需的路径重写）；本 README 是唯一导航入口。

## 这是什么问题

生产部署形态 = opencode + vibe-trading MCP。核心矛盾是**披露税**：每个规划轮把全部
工具/技能描述注入模型上下文（VT 内置 agent ~106 工具 ~74k token；MCP 面 74 工具 + 90
技能 ~52k token）。论文证据：工具选择准确率在 25-30 个可见工具后退化、~100 个崩塌。

## 结论速览（裁决总表）

| 批次 | 假设 | 结果 | 裁决 |
|---|---|---|---|
| **A 描述治理**（A1-A8） | 措辞改善路由 | 池化 McNemar p=0.885，基线 0.88-0.94 已到顶 | ❌ 路由中性；A7 弱效应、A8 回归，**均已回滚，勿重测** |
| **B 暴露面工程**（B1-B5） | 裁掉不可用工具不损路由 | C1 非劣 PASS（Δ=+1.05pp，CI[−2.03,+3.75]）；MCP keyless 74→59，−5,100 tok/轮（−17.9%）；幻觉调用 0 | ✅ 成立；**暂缓上游**（用户裁决 2026-08-27） |
| **C 路由层**（search_tools + 披露层级） | 懒加载砍披露税且路由不降 | 检索本身达标（recall@7=0.937、披露税 −79%），但端到端 4 配置全部显著更差（Δ −11.5pp ~ −33.6pp） | ❌ **全部回滚，思路标记失败** |
| **D 领域子代理**（D1-D4） | 固定小白名单落在舒适区，无需搜索决策 | R1 召回 99.1%、R2 误委派 3.57%；D2-1 复测两域 CI 下界为正（quant +6.67pp、webdocs +33.44pp）；决策读入 −86%、延迟中位 −33% | ✅ **生产落地**：mymain 12 子代理，主循环 59→46（`552c7bfe`），trading-connector 准入（`b5a7265b`） |

**四条校准教训**（SUMMARY §4）：① 描述措辞不是杠杆；② "裁掉没用的"无损，"藏起来让
模型找"有损；③ 子代理路线成立但非银弹（委派需编排侧政策显式激活）；④ 评测纪律本身
是最大产出（预注册 + 噪声地板挡住了两次假阳性）。

## 阅读顺序

1. **`HARNESS_EVOLUTION_SUMMARY.md`**（zh/en 双语版同在）—— 总收口：问题 → 调研 → 方案 → 实测 → 裁决。**先读这份。**
2. **`HARNESS_EVOLUTION_ROADMAP.md`** —— 28 个 PLAN 的总表 + 状态 + 各批执行结果（§7-§10）。
3. 按需深入专项文档（见下表）。

## 文档地图

| 文档 | 内容 | 何时读 |
|---|---|---|
| `HARNESS_EVOLUTION_CAPABILITY_AUDIT.md` | 能力审计 v2：K1-K25 / G1-G10 / Q1-Q19 问题编号、§7.2 路由决策表、§8.1 子代理草案与移植映射表 | 查某个具体路由问题的编号与证据 |
| `HARNESS_EVOLUTION_RESEARCH.md` | 架构调研（32 框架源码级，opencode+omo 保留裁决）、§4.2 prompt 缓存纪律 | 改路由/披露层前 |
| `HARNESS_EVOLUTION_PAPERS.md` | 论文证据索引（A-I 九类 + 复现台账）；§F = 工具规模与选择准确率 | 需要论文依据时 |
| `HARNESS_EVOLUTION_BENCHMARKS.md` | 评测基准调研 | 评测设计参考 |
| `HARNESS_EVOLUTION_TOOL_MAPPING.md` | 内部工具名 ↔ MCP 名权威映射（A6 产物） | 写子代理白名单/preset 移植时 |
| `HARNESS_EVOLUTION_MCP_GAP_REVIEW.md` | MCP 面缺口评审（D3 裁决依据：运行时映射层无消费者） | D3 相关 |
| `HARNESS_EVOLUTION_P0_PLAN.md` | Wave 1 执行 + A 批 E2 终局 + §8 上游 PR 拆分裁决（暂缓上游） | 上游化决策时 |
| `HARNESS_EVOLUTION_A5_A8_TEST_PLAN.md` | A5-A8 量化测试计划（预注册） | — |
| `HARNESS_EVOLUTION_B_TEST_PLAN.md` / `HARNESS_EVOLUTION_C_PLAN.md` / `HARNESS_EVOLUTION_D_PLAN.md` / `HARNESS_EVOLUTION_D2_PLAN.md` / `HARNESS_EVOLUTION_D2_RESUME.md` | 各批工作计划 + 预注册判据 + revision log | 复跑/审计某批实验时 |

## evals/ — 研究代码与证据（归档态）

原始位置：`agent/src/evals/`（`fix/trading-tool-routing-hints` 分支的 `tool_selection` +
`NewAgentMain` 分支的 `agent_eval` / `harness_bench`）。归档后为**只读证据**，不再随
分支演进；如需复跑，按各 verdict 文档记录的协议在原分支环境中执行。

| 子目录 | 内容 |
|---|---|
| `evals/tool_selection/` | 工具选择评测基建：E1 语料（`queries*.yaml`，158→353 条）、LLM 判官（`run_llm_judge.py` / `llm_judge_protocol.py` / `judge_config*.yaml`）、批次统计脚本（`a5/a6/a7a8/b_*`）、`SUBAGENT_ADMISSION_PROTOCOL.md`（子代理准入协议资产化） |
| `evals/tool_selection/artifacts/` | 全部裁决文档与黄金 trace：`a6_a8_verdict.md`、`b_batch_verdict.md`、`c_batch_verdict.md`、`d_batch_verdict.md`、`d2/`（track_a_verdict / d4_final_verdict / d4tc_verdict / mainloop_convergence_* / preset_audit / power_analysis / telemetry_validation …）、`llm_judge_design.md`（方法学与已知缺口）、`d_l2*/`（L2 真实环境轨迹） |
| `evals/agent_eval/` | 确定性策略/轨迹评测（golden_trace / scorer / runner；`REGRESSION_NOTES.md`） |
| `evals/harness_bench/` | 外部基准适配器：tau²-bench / SWE-bench / terminal-bench / FinanceBench / FinEval / BacktestBench + `canonical_tool_manifest.json`（82 工具 schema 指纹）+ parity spec |
| `evals/tests/` | harness_bench scaffold 回归测试（原 `agent/tests/evals/`） |

> 归档注记：① 早期文档中 "agent_eval / harness_bench 位于 NewAgentMain 分支" 的表述
> 反映撰写时状态，现已一并归档于此；② 归档文档内 `mymain-wiki/harness-evolution/evals/...`
> 路径均经重写对齐当前布局；裸文件名互指（`HARNESS_EVOLUTION_X.md`）在同目录内有效。

## 未闭合线索（接手前必读）

| 线索 | 状态 | 触发条件 |
|---|---|---|
| Track B 生产遥测（twin_choice 观察窗） | 观察中 | 2026-09-26 兜底读出；4 周 <30 事件则按"功效不足"关闭，不构成阻塞 |
| B 批上游贡献 | 暂缓（用户裁决） | 未来用户决策重启；PR 拆分见 P0_PLAN §8.3 |
| E3 路由遥测 | 暂缓 | 原依赖 C2（已回滚）；后继 = Track B |
| E4 描述变更回归 | 未启动 | A 批回滚后缺回归对象；职能由 D4 准入纪律部分承担 |
| D3 运行时映射层 | 按需（DEC-6） | 首个 preset 白名单移植需求出现时再写代码 |
| A1-A8 描述改动 | **DO-NOT-RE-TEST** | SOTA 模型下已证路由中性（`evals/tool_selection/artifacts/llm_judge_design.md`） |
