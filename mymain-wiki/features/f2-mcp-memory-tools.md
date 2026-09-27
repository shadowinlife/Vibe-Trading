---
title: F2 MCP 记忆工具（Memory Lifecycle MCP Tools）
description: 把记忆生命周期五个操作经 MCP 面暴露为 memory_save/recall/reinforce/reflect/status，默认关闭。改 mcp_server 注册、核对 MCP 工具计数、准备上游 PR ③ 时必读。触发词：memory_save、memory_recall、VT_MEMORY_MCP_TOOLS、mcp_adapter、memory-lifecycle。
type: delta
status: active
created: 2026-07-27
updated: 2026-08-30
tags: [memory, mcp, tools, lifecycle]
related: [../branch/MYMAIN_DIVERGENCE.md]
---

# F2 MCP 记忆工具

> 一句话定位：F1 存储的 MCP 暴露面——五个工具加 never-raise dict 包络适配层，附 memory-lifecycle SKILL 工作流；上游 MCP 面没有任何记忆工具，且受门控默认关闭，不影响上游形态。

## 能力

- 五个 MCP 工具：`memory_save` / `memory_recall` / `memory_reinforce` / `memory_reflect` / `memory_status`
- never-raise 适配层：所有失败包成 dict 包络返回，不向 MCP 客户端抛异常
- `memory-lifecycle` SKILL：教 agent 何时存、何时取、何时加固的工作流（bundled skills 89→90 的来源）
- 注册受 `VT_MEMORY_MCP_TOOLS` 门控，默认 OFF

## 关键文件与开关

| 文件 / 开关 | 作用 |
|---|---|
| `agent/src/memory/mcp_adapter.py` | 适配层实现（本分支新增） |
| `agent/mcp_server.py` | 五工具注册段（含门控判断） |
| `agent/src/skills/memory-lifecycle/SKILL.md` | 生命周期工作流文档 |
| `VT_MEMORY_MCP_TOOLS` | 注册开关，默认 OFF；当前分支计数 OFF=77 / ON=82（其中 3 个为不回流的 ch_* 语义层工具，见 F5 卡） |

## 开发历史

- 2026-07-27 首次落地（pre-carve `90095124`）；五-agent 评审移除从未被调用的死代码 `build_default_adapter()`（[../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md) §5 2026-07-27 条）。
- 2026-08-07 rebase：memory-lifecycle 使 skills 89→90、Tool 类 10→11，五份 README + SKILL.md 同步过上游 pin 测试（§5 2026-08-07 条）。
- 2026-08-11 merge+carve 重整为 `08c579bb`（当前分支 SHA）。
- 2026-08-17 起上游新增第六份 README（README_es.md），本分支自行保持六份同步（上游 pin 只锚定五份）。
- 2026-08-28 rebase：本 commit 与 Phase 2 同为冲突集中点（六 README + SKILL.md + mcp_server.py 计数区），按「上游内容 + 本 commit 增量」逐层解决（§5 2026-08-28 条）。

## 验证

- MCP 计数门控冒烟：OFF=77 / ON=82（命令见 DIVERGENCE §3.2）
- 工具往返：`memory_save → memory_recall → memory_status` 应返回 ok 包络（DIVERGENCE §3.2）
- memory 套件 **309 passed / 3 skipped**；README/SKILL 计数门禁 **70 passed**（DIVERGENCE §3.1）

## 状态与上游关系

- 上游无 MCP 面记忆工具（当前上游 MCP 基数 74，本分支 77/82 含 3 个 ch_*；PR 描述需按提交时的上游基数重述）。
- 回流：贡献队列 **③**（DIVERGENCE §2.3）；**README 计数更新必须随 PR 一并提交**（上游 pin 测试强制），涉及六份 README + `agent/SKILL.md`，持续义务见 §4.3。
- 依赖 F1（反思存储 API）；无直接关联债务。
