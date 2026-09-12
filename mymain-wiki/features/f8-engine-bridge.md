---
title: F8 opencode 引擎桥（Engine Bridge）
description: VIBE_TRADING_ENGINE=opencode 时的 SessionService 置换层——vt 网关把 opencode 当外置 agent 引擎（driver/translator/service/recovery 四层），含双存储所有权规则（D3）、启动存活对账三分支恢复与级联生命周期。改 agent/src/opencode_bridge/、接 T7 工厂开关、排查会话恢复/重挂/补写问题时必读。触发词：engine-bridge、opencode_bridge、RecoverableOpencodeSessionService、reconcile、VIBE_TRADING_ENGINE、re-attach、backfill、interrupted、级联删除。
type: delta
status: active
created: 2026-09-13
updated: 2026-09-13
tags: [engine-bridge, opencode, recovery, session-service]
related: [../branch/MYMAIN_DIVERGENCE.md, f7-opencode-agent.md]
---

# F8 opencode 引擎桥

> 一句话定位：把 F7 的 opencode harness 变成 vt 网关的外置 agent 引擎——`agent/src/opencode_bridge/` 在 SessionService 接缝下整体置换原生 Python agent loop，前端/16 个 IM 适配器/调度器零改动；opencode 是独立进程、比网关命长（T2 memo F2），恢复语义因此以「引擎活着」为常态设计。

## 能力

- 四层结构：`driver.py`（T3，EngineDriver 四原语 + legacy `/session` REST + 单条持久 SSE，subscribe-first）、`translator/`（T4，opencode 事件 → vt SSE 词汇，静默期 8.0s 定版）、`service.py`（T5，OpencodeSessionService 全 D6 契约）、`recovery.py`（T6，启动恢复 + 存活对账 + 级联生命周期）
- **双存储所有权规则（D3，计划原文）**：`messages.jsonl` = 用户可见转录（对引擎只写）；opencode store = 引擎上下文真相。会话删除/IM `/new` 必须级联到 opencode session；opencode compaction 对 vt 转录不可见（接受）
- 崩溃推论：网关崩溃后**活性以引擎为准**（回合还在不在跑只有 opencode 知道），**历史以桥存储为准**（转录只追加、不重写）
- 恢复**永不重发 prompt**（计划 T6 Must-NOT：永不重复执行）——对账只读引擎状态；重挂不是重启

## 启动恢复三分支（T6）

网关启动时对每个 pending/running attempt 查 `GET /session/:id/message`，恰好落入一支：

| 分支 | 引擎状态 | 动作 | 落盘 |
|---|---|---|---|
| 1 重挂 | 回合仍在跑（assistant 消息未完成 / finish=tool-calls / 完成但在 OmO continuation 窗口内） | 重挂 SSE（泵重连即自然重订阅）+ `note_attempt(engine_sid, attempt_id)` 重新广播 | attempt 保持 running；看门狗定时复查防挂死 |
| 2 补写 | 回合已完成（最后一次 natural completion 超出 continuation 窗口） | 从引擎消息列表补写终态：D4 定版文本 + D6 元数据枚举 + FTS 恢复锚点索引，走 T5 `_persist_terminal_attempt` 同一接缝 | attempt completed + 回复消息 + `attempt.completed` 事件 |
| 3 中断 | 引擎无此会话（404）/ 引擎不可达 / prompt 从未到达 | 按现有 interrupted 语义落盘（对齐原生 `session/service.py:101-154`，含 `recovery_reason: "service_restart"`） | attempt interrupted + 部分文本 surfaced；IM 轮询在预算内拿到终态 |

验收锚点：恢复后 `replay=active`（`sessions_routes.py:806`）不再重播死回合——分支 2/3 的 attempt 均为终态，`replay_all` 恒为 False；分支 1 保持 running（活回合本就该重播）。

## 级联生命周期（D3）

- `delete_session` → 引擎侧 `DELETE /session/:id`（spike §7h 实证：200 + `session.deleted` 事件 + 子会话级联删除）；fire-and-forget，vt 侧删除永不被引擎阻塞，404 视为成功（幂等）
- IM `/new`：`runtime.reset_session`（保护区，零改动）只丢 channel→vt 映射；下一条消息建新 vt 会话 → 懒建**新** opencode 会话。旧引擎会话**故意不删**——其 vt 转录仍可浏览、引擎上下文仍在（D3），`session.config` 持久化映射保证重启后续用

## 关键文件与开关

| 文件 / 开关 | 作用 |
|---|---|
| `agent/src/opencode_bridge/recovery.py` | `RecoverableOpencodeSessionService` + `reconcile()`（T7 工厂启动入口）+ 级联删除；模块 docstring 为 D3 规则正典 |
| `agent/src/opencode_bridge/recovery_branches.py` | 三分支落地机械（重挂 awaiter + 看门狗 / 补写 / 中断），复用 T5 持久化接缝 |
| `agent/src/opencode_bridge/engine_state.py` | 引擎消息列表解析：turn 分类、D4 定版文本、run_dir 收割（T4 冻结正则）、tool_trail 重建 |
| `session.config["opencode_engine_session_id"]` | vt→引擎会话映射的持久化键（T5 仅内存，T6 落盘——重启后恢复与上下文续用的前提） |
| `VIBE_TRADING_ENGINE` | 工厂开关（`env_schema.py`，T7 接线；当前默认 native，桥未启用） |

## 降级清单（T15 扩写占位）

> 本节由 T15 按 F3 降级全清单（10 项，计划 §风险）扩写——占位，勿在此前填充内容。

## 开发历史

- 2026-09-12 Phase 0：T2 基线盘点 memo（`14bf3fe3`）+ T1 spike GO 与三份强制条件（`aaad7546`，8 份 golden traces）。
- 2026-09-12 Wave 2：T3 driver（`03332696`）→ T5 service（`6530a7f3`）→ T4 translator（`7967210f`）。
- 2026-09-13 T6 恢复对账 + 级联生命周期落地（本卡创建）；T7 工厂开关与 E2E 汇合门待接。

## 验证

- 桥测试套件 **175 passed / 1 skipped**（T3 62 + T4/T5 88 + T6 25，`pytest agent/tests/test_opencode_bridge_*.py`）
- T6 关键测试：三分支各一例 + T1 golden trace 经真实 EventTranslator 重挂重放（`scenario_a` 全回合 → 终态 "DONE_A"）+ `replay=active` 死回合不重播 + IM 轮询预算内拿到 interrupted 终态 + 级联删除（含重启后凭持久化映射级联）+ reconcile 幂等
- 工件：计划 `.omo/plans/opencode-engine-bridge-v2.md`（D3/D4/D6/T6 正典）、`OpencodeAgent/docs/spike_report.md`（§7h DELETE 级联）、`OpencodeAgent/docs/baseline_memo.md`（F2 恢复分叉：opencode 独立进程存活）、traces `agent/tests/fixtures/opencode_bridge/traces/`（只读夹具）

## 状态与上游关系

- 本分支独有，**不回流**（计划 guardrail：NO 上游 PR，候选记入 [../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md) 待裁决）。
- 保护区零触碰：`src/agent/`、`src/session/`、`src/providers/`、`src/channels/runtime.py`、`frontend/` 均零 diff；对原生 oracle 只 import 复用（`_format_interrupted_message` 等静态方法），不修改。
- 已知残余边界（T8 范畴）：opencode 自身中途崩溃重启后，僵尸 in-flight 消息会让重挂回合像长静默工具一样等待——活体引擎死亡检测走 T8 的 <30s failed 路径。
