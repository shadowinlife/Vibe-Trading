---
title: F9 策略库与调度（Strategy Library + Scheduling）
description: 面向人工评审与调度的策略库：code/workflow 双门就绪、版本快照与 CAS 评审、复用 scheduled research 的排程，以及 /strategies 与 /strategy-schedules 两个 Web 面。改策略库、评审门、排程或查 UI 边界时必读。触发词：strategy library、策略库、promote_strategy、evaluate_strategy、schedule_strategy、readiness、workflow rubric、strategy-schedules。
type: delta
status: active
tags: [strategy-library, scheduling, readiness-gate, strategy-review]
related: [../branch/MYMAIN_DIVERGENCE.md]
---

# F9 策略库与调度

> 一句话定位：把策略从草稿、评审、就绪到定时执行串成一条可审计链路；四只 agent 工具与两个 Web 页面读写同一份带版本的策略快照，人工评审只做否决、调度只走已就绪版本。

## 能力

- 策略库同时容纳 `code` 与 `workflow` 两类 spec；每次编辑生成不可变版本快照，库表与磁盘 `strategy.json` 由同一语句推进版本
- 评审结论与就绪状态在一次 compare-and-swap UPDATE 中写入，守卫 `(artifact_version, spec_sha256)`：并发编辑只会让旧结论变成 stale no-op，陈旧 UI 无法覆盖新评审
- agent 面四只工具：`promote_strategy`（登记草稿/回测目录）、`evaluate_strategy`（重跑评审）、`list_strategy_library`（读库）、`schedule_strategy`（创建排程提案）；其中三只经 MCP 暴露，且位于显式 mutating gate 之后
- 会话级工具白名单持久化，并在 MCP 工具追加后仍然生效；策略运行只能调用 `ALLOWED_STRATEGY_TOOLS` 的精确白名单，`trading_*` / `etoro_*` / `connector_*` 前缀作为运行期兜底永远拒绝

## 两道门

### code 门（确定性回测门）

- 只认一个真实 run 目录：`state.json` 成功、metrics 可解析、权益曲线有限、交易笔数大于零，缺一项即按稳定 reason code fail closed
- 通过后把该 run 的证据写进 SDM 的 per-regime evidence cache 与 bench history；所有数字来自 run 自身 `artifacts/`，不调用模型、不手工填指标
- run 目录只读，策略自身目录不得冒充 run 目录（它只有 `strategy.json`，没有 `state.json`）

### workflow 门（独立 rubric 评审）

- 固定 8 项 must-pass：目标可判定、输入完整、前置条件可检查、步骤可执行、工具在白名单内、输出契约可解析、失败处理显式、上下文自包含
- 任一检查失败即整体 fail，不做平均；90 秒内无法判定的检查记为 `inconclusive`（基础设施失败，永不视为 pass）
- 人工评审只能 reject，不能 approve；任何编辑都要求重新评估，旧版本结论不会跟随新字节

## 调度模型

- 复用既有 scheduled research 作业内核：attach 时锁定策略版本，版本在提案后变化则拒绝提交并要求重新 attach
- `workflow` 策略返回带一次性确认 token 的提案，token 只由人类 REST 面消费，不进入 agent 响应；`code` 策略直接创建
- 每个策略只允许一个 primary schedule；支持 run-now、skipped/missed 语义与保留条数（retention）上限
- 异步执行：`start` 立即返回 in-flight handle，`sweep` 对账，进程重启后收敛；超过 `run_timeout_s` 的作业记录为 failed 并带原因
- 报告保持有界：每个策略的 run 报告最多保留最近 20 条

## P1 服务入口

- 目标：为 RM 控制面提供按版本运行和状态查询的 service entry，复用本卡 runner，不重造执行器。
- 基线：本地 `mymain`（已含 F9）；开发分支 `feat/strategy-run-entry`，基于 `mymain` 新建 worktree。
- 跨仓需求：RM 仓 `docs/vt-change-requests/0001-strategy-run-entry.md`（仓外路径）；字段、鉴权、幂等与 per-box 契约以该需求为准。
- 状态：delivered（VT 本地交付；RM E2E 是最终验收 gate，验收结论由 RM 填写）。交付 commit：e373076e；证据：.omo/evidence/strategy-run-entry/delivery.md。

## UI 面

- `/strategies`：KPI bar、搜索、kind/readiness 过滤、demo 标记；空库提供一键 demo seed
- 480 px inspector 六个只读页签：overview、contract、review、evidence、schedule、runs；inspector 不写任何状态
- `/strategy-schedules`：最近 5 次内联历史、20 条 run 抽屉、三步创建流（就绪策略 → cadence → 输出/边界）；workflow 策略出现确认卡，code 策略直接创建
- 90 秒六帧 storyboard 尊重 `prefers-reduced-motion`，最后一帧只跳转 demo，不触发网络请求

## 边界

- 不自动批准任何策略；人工评审只有否决权
- 不编造指标或历史；库内只保存当前与上一次评审结论，不是完整审计账本
- 请求路径内不执行策略；执行只发生在异步 runner
- 策略运行不触达 broker/order 路径，调度也不下单
- 已合入本地 `mymain`（2026-10-25，合并提交 81df3a992）；未回上游 HKUDS。F9 源分支 `feat/strategy-library-scheduling` 已合并，不再单独演进。

## 关键文件与开关

| 文件 / 开关 | 作用 |
|---|---|
| `agent/src/strategy_library/spec.py` | spec 契约、确定性 gap 报告、`ALLOWED_STRATEGY_TOOLS` 与禁用前缀 |
| `agent/src/strategy_library/store.py` / `store_io.py` | 版本发布、CAS 评审写入、快照与列表 |
| `agent/src/strategy_library/gate_code.py` / `review.py` | code 回测门 / workflow rubric 门 |
| `agent/src/strategy_library/promote.py` / `demo.py` | 草稿登记与带标记的 demo seed |
| `agent/src/strategy_library/scheduling.py` / `scheduling_io.py` | attach/commit/detach、一次性确认 token、primary 约束 |
| `agent/src/strategy_library/execute_code.py` / `execute_workflow.py` / `runner.py` | 异步 start/poll/sweep、超时与重启收敛 |
| `agent/src/strategy_library/history.py` / `history_io.py` | run 历史、保留条数与 20 条报告上界 |
| `agent/src/tools/strategy_library_tool.py` / `agent/src/tools/__init__.py` | 四只 agent 工具与会话级白名单执行点 |
| `agent/src/api/strategy_library_routes.py` / `strategy_schedule_routes.py` | `/strategies`、`/strategy-schedules` REST 面 |
| `frontend/src/pages/StrategyLibrary.tsx` / `StrategySchedules.tsx` | 两个 Web 页面 |
| `frontend/src/components/strategy/` | 表格、inspector、排程步骤、run 抽屉与 storyboard |

## 验证

- `agent/tests/test_strategy_spec.py`、`test_strategy_promote.py`、`test_strategy_review.py`、`test_strategy_gate_code.py` 覆盖 spec 预检、两道门与 CAS 拒绝
- `agent/tests/test_strategy_scheduling.py`、`test_scheduled_strategy_execution.py`、`test_scheduled_strategy_job.py`、`test_strategy_store_adaptation.py` 覆盖 attach/commit、异步执行、超时与重启收敛
- `agent/tests/test_strategy_library_tools.py`、`test_strategy_library_routes.py`、`test_strategy_schedule_routes.py` 覆盖工具与 REST 面
- `frontend/src/pages/__tests__/StrategyLibrary.test.tsx`、`StrategySchedules.test.tsx`、`frontend/src/components/strategy/__tests__/` 覆盖两个页面、inspector 与 storyboard

## 状态与上游关系

- 本地 `mymain` 独有，**未回上游**：依赖本分支的 scheduled-research 存储内核与会话级工具白名单，上游没有对应的策略库/排程面。2026-10-25 已合入本地 `mymain`（81df3a992）。
- 与 SDM 策略发现互补：本卡管理人工评审与调度，SDM 负责证据发现与适配；两者共用同一份 strategy store，不产生第二套登记表
- 差异台账与贡献队列见 [../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md)