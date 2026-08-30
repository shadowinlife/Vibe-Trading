---
title: F1 反思课程存储（Reflection Lessons Store）
description: 按策略类型沉淀回测/研究课程的 append-only JSONL 存储，mymain 记忆系统 T4 迭代的基座，F2/F3 都建在它之上。改记忆子系统、排查课程读写、准备上游 PR ② 时必读。触发词：reflections、课程、lesson、auto_reflect、VT_MEMORY_REFLECTIONS、记忆 T4。
type: delta
status: active
created: 2026-07-27
updated: 2026-08-30
tags: [memory, reflections, lessons, jsonl]
related: [../branch/MYMAIN_DIVERGENCE.md]
---

# F1 反思课程存储

> 一句话定位：把「这次回测/研究学到了什么」以结构化课程（lesson）形式按策略类型持久化，供后续会话检索与置信度更新；上游没有任何等价物，这是它存在于 mymain 的原因。

## 能力

- append-only JSONL 课程库，按策略类型分文件（`<memory_root>/reflections/<策略类型>.jsonl`）
- 标签 / 子串检索，课程置信度（confidence）可更新
- `auto_reflect_from_run_dir`：从回测产物目录自动提取课程（F3 钩子的调用入口）
- 特性开关语义：被 `VT_MEMORY=full` 预设隐含，也可单独用 `VT_MEMORY_REFLECTIONS` 控制

## 关键文件与开关

| 文件 / 开关 | 作用 |
|---|---|
| `agent/src/memory/reflections.py` | 全部实现（本分支新增文件） |
| `agent/src/config/env_schema.py` | `VT_MEMORY_REFLECTIONS` 与 `VT_MEMORY=full` 预设语义声明 |
| `agent/tests/memory/` | 测试套件（含 F3 的并发与延迟基准） |

## 开发历史

- 2026-07-27 首次落地（pre-carve `2b8240dc`，备份分支 `backup/mymain-pre-rebase-20260804` 可查）。同日五-agent 并行评审（Goal/QA/CodeQuality/Security/ContextMining）裁定：锁超时错误信息用 `None`（关闭）vs `""`（锁超时）区分；非阻塞建议（`_iter_lessons` 改名、大 JSONL 逐行读、自定义 encoder）留待社区 PR。见 [../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md) §5 2026-07-27 条。
- 2026-08-04 rebase（基线 `3a752d5`）：随全分支过 env-var AST 门禁合规修复。
- 2026-08-11 merge+carve：重整为单功能 commit `6a612498`（当前分支 SHA），carve 树与 merge 树逐字节一致。
- 2026-08-28 rebase 到 `80ffdda4`：本 commit 无冲突重放。

## 验证

- memory 套件基线 **309 passed / 3 skipped**（命令见 DIVERGENCE §3.1，reflections 专项在套件内）
- 冒烟：`VT_MEMORY_REFLECTIONS` 关闭时 `memory_reflect` 应返回 skipped 而非 error（DIVERGENCE §3.2）

## 状态与上游关系

- 上游无对应（上游 remember_tool / memory CLI 是不同暴露面，不构成取代；历次对齐核查均确认，最新 2026-08-28）。
- 回流：上游贡献队列 **②**（DIVERGENCE §2.3）；PR 时顺手实施评审留下的三个非阻塞建议以减少往返。
- 无直接关联债务（D1-D4 均挂在 F4 侧，见 [f4-memory-guard.md](f4-memory-guard.md)）。
