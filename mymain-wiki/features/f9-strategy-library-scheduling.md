---
title: F9 策略库与调度（Strategy Library + Scheduling）
description: 面向人工评审与调度的策略库：code/workflow 双门就绪、版本快照与 CAS 评审、复用 scheduled research 的排程，以及 /strategies 与 /strategy-schedules 两个 Web 面。RM 侧的运行入口见 F10、状态镜像见 F11、六个调度工具见 F12。改策略库、评审门、排程或查 UI 边界时必读。触发词：strategy library、策略库、promote_strategy、evaluate_strategy、schedule_strategy、readiness、workflow rubric、strategy-schedules、strategy-run-entry、vt-state-v1、RM scheduling。
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
- agent 面：`promote_strategy`（登记草稿/回测目录）、`evaluate_strategy`（重跑评审）、`list_strategy_library`（读库）仍管理本地库；`schedule_strategy` 及另外五个调度工具已改为 RM 控制面转发（F12），不再写本地 `scheduled_research`
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

- **箱内助手不再使用本地排程面**：六个调度工具全部转发 RM 控制面（F12），RM 拥有调度表、due-time loop 与 run history；本地 `/strategy-schedules` 路由保留给全量 Web 面，箱内入口不注册，且默认关闭。
- 本地 F9 路由仍复用既有 scheduled research 作业内核：attach 时锁定策略版本，版本在提案后变化则拒绝提交并要求重新 attach；`workflow` 提案的一次性确认 token 只由人类 REST 面消费。
- 每个策略只允许一个 primary schedule；支持 run-now、skipped/missed 语义与保留条数（retention）上限。
- 异步执行：`start` 立即返回 in-flight handle，`sweep` 对账，进程重启后收敛；超过 `run_timeout_s` 的作业记录为 failed 并带原因。
- 报告保持有界：每个策略的 run 报告最多保留最近 20 条。

## P1 服务入口

- 目标：为 RM 控制面提供按版本运行和状态查询的 service entry，复用本卡 runner，不重造执行器。
- 交付拆分：入口 API 见 [F10](f10-strategy-run-entry.md)；状态镜像见 [F11](f11-state-export-import.md)；六个 RM 调度工具见 [F12](f12-rm-schedule-tools.md)。
- 状态：delivered（VT 本地交付；RM E2E 是最终验收 gate，验收结论由 RM 填写）。2026-10-28 已合并 `mymain`，镜像 `v3.12.0-mymain`（manifest `sha256:15dad042dfec59109be3cb66d1c1fb1a2cdd9603d6531bf4f781fc688fc692a3`）。
- 跨仓需求：RM 仓 `docs/vt-change-requests/0001` / `0007` / `0008`（仓外路径）；字段、鉴权、幂等与 per-box 契约以需求件为准。
- 证据：`.omo/evidence/final-release/delivery.md`（VT 本地）；RM 侧交接见 `aliyun-acs-agent-service/docs/iterations/strategy-schedule-vt-handoff.md`。

## UI 面

> 以下为全量 Web 面；箱内 entry 不挂载 `/strategy-schedules`，箱内助手的调度走 F12。

- `/strategies`：KPI bar、搜索、kind/readiness 过滤、demo 标记；空库提供一键 demo seed
- 480 px inspector 六个只读页签：overview、contract、review、evidence、schedule、runs；inspector 不写任何状态
- `/strategy-schedules`：最近 5 次内联历史、20 条 run 抽屉、三步创建流（就绪策略 → cadence → 输出/边界）；workflow 策略出现确认卡，code 策略直接创建
- 90 秒六帧 storyboard 尊重 `prefers-reduced-motion`，最后一帧只跳转 demo，不触发网络请求

## API 面（全量 Web，箱内不挂载）

鉴权为 VT 自己的 `require_auth`（session / API key），不是 RM 的 service token；箱内 entry 不注册这些路由。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/strategies` | 列表（kind / readiness / 搜索） |
| GET | `/strategies/summary` | KPI 汇总 |
| POST | `/strategies` | 登记 spec / run_dir；体 `{"spec": {...}, "run_dir": null, "demo": false}` |
| POST | `/strategies/demo` | 一键 demo seed |
| PATCH | `/strategies/{id}` | 只更新 spec，生成新版本 |
| GET | `/strategies/{id}` | 详情（含当前/上一次评审） |
| GET | `/strategies/{id}/runs` | 最近运行 |
| POST | `/strategies/{id}/evaluate` | 重跑就绪门 |
| GET | `/strategy-schedules` | 本地排程列表（每行带最近 5 次 run） |
| POST | `/strategies/{id}/schedule` | attach；体 `{"schedule": "0 9 * * 1-5", "timezone": "Asia/Shanghai"}` |
| POST | `/strategy-schedule-proposals/{pid}/commit` | workflow 提案提交；体 `{"confirm_token": "..."}` |
| DELETE | `/strategy-schedules/{job_id}` | 删除本地排程 |
| GET | `/strategy-schedules/{job_id}/runs` | 有界运行历史（5–100） |
| POST | `/strategy-schedules/{job_id}/run-now` | 立即运行一次 |

`POST /strategies` 返回库行（`id` / `version` / `readiness` / `spec` 等）；`POST /strategies/{id}/schedule` 对 `code` 直接创建、对 `workflow` 返回 proposal（`confirm_token` 只在此 REST 面出现）。RM 侧的运行入口与调度工具见 F10 / F12，不复用这些路由。

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
| `agent/src/tools/strategy_library_tool.py` / `agent/src/tools/__init__.py` | 本地策略库工具（promote/evaluate/list）与会话级白名单执行点；`schedule_strategy` 已由 F12 的 RM 工具承接 |
| `agent/src/api/strategy_library_routes.py` / `strategy_schedule_routes.py` | `/strategies`、`/strategy-schedules` REST 面 |
| `frontend/src/pages/StrategyLibrary.tsx` / `StrategySchedules.tsx` | 两个 Web 页面 |
| `frontend/src/components/strategy/` | 表格、inspector、排程步骤、run 抽屉与 storyboard |

## 验证

- `agent/tests/test_strategy_spec.py`、`test_strategy_promote.py`、`test_strategy_review.py`、`test_strategy_gate_code.py` 覆盖 spec 预检、两道门与 CAS 拒绝
- `agent/tests/test_strategy_scheduling.py`、`test_scheduled_strategy_execution.py`、`test_scheduled_strategy_job.py`、`test_strategy_store_adaptation.py` 覆盖 attach/commit、异步执行、超时与重启收敛
- `agent/tests/test_strategy_library_tools.py`、`test_strategy_library_routes.py`、`test_strategy_schedule_routes.py` 覆盖工具与 REST 面
- `frontend/src/pages/__tests__/StrategyLibrary.test.tsx`、`StrategySchedules.test.tsx`、`frontend/src/components/strategy/__tests__/` 覆盖两个页面、inspector 与 storyboard

## 相关提交

| commit | 内容 |
|---|---|
| `81df3a992` | F9 策略库 + 本地调度合入 `mymain` |
| `7f67ace8` | P1 入口初版（F10） |
| `585845ad` | PR #3 merge：入口 + 0007 修复 |
| `d90d5308` | PR #4 merge：状态镜像（F11）+ 六个 RM 调度工具（F12） |
| `bb27b8d4` | 发布 review 修复：sweeper 线程、import 限长、`trust_env=False`、health token |
| `f036c6b9` | lifecycle 拆分、FastAPI 路由断言、SKILL.md 94 tools |

## 后续迭代

- **B8**：箱内 MCP 工具 → 真实 RM 的活体调用未验证；`VT-STRAT-0005` 保持 `delivered`。
- **0002**：`POST /strategy-runs/{id}/cancel` 未实现；删除调度只停后续槽。
- **0003**：RM 创建调度前的 `strategy/version` 预检未实现。
- **生产模板**：仍指向 `strategy-run-entry-e373076`；切到 `v3.12.0-mymain` 是 HIL 步骤。
- **并发**：跨进程 import 锁与 store 关闭竞态见 F11。

## 状态与上游关系

- 本地 `mymain` 独有，**未回上游**：依赖本分支的 scheduled-research 存储内核与会话级工具白名单，上游没有对应的策略库/排程面。2026-10-25 已合入本地 `mymain`（81df3a992）；2026-10-28 完成 P1 入口、状态镜像与 RM 调度工具的合并（`585845ad` / `d90d5308` / `f036c6b9`）。
- 与 SDM 策略发现互补：本卡管理人工评审与调度，SDM 负责证据发现与适配；两者共用同一份 strategy store，不产生第二套登记表
- 差异台账与贡献队列见 [../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md)