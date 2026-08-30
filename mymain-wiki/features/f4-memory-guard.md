---
title: F4 MemoryGuard 与项目目录存储（MemoryGuard + VT_MEMORY_BASE_DIR）
description: FastMCP middleware 在工具调用后自动记忆（零 LLM），并允许把记忆根目录锚定到项目内。改记忆持久化路径、处理债务 D1-D4、准备上游 PR ①/⑤ 时必读。触发词：MemoryGuard、middleware、VT_MEMORY_BASE_DIR、VIBE_TRADING_HOME、记忆路径。
type: delta
status: active
created: 2026-07-28
updated: 2026-08-30
tags: [memory, middleware, guard, storage-path]
related: [../branch/MYMAIN_DIVERGENCE.md]
---

# F4 MemoryGuard 与项目目录存储

> 一句话定位：不靠 agent 自觉、由中间件在每次工具调用后自动 save/reflect 的兜底记忆层，外加把记忆从 `~/.vibe-trading` 解绑到项目目录的路径能力；上游记忆仍锚定 home 目录，两者的 FTS 改动各管各的、并存无冲突。

## 能力

- FastMCP middleware：工具调用后自动 `memory_save` + `memory_reflect`，全程零 LLM 调用
- 自触发排除：`memory_*` 前缀工具不再触发 guard（消除自录制噪音，2026-08-04 修正）
- `VT_MEMORY_BASE_DIR`：记忆可存进项目目录，随项目迁移与清理
- 默认路径跟随上游共享基础设施 `get_runtime_root()`（`VIBE_TRADING_HOME` 感知），复用而非分叉

## 关键文件与开关

| 文件 / 开关 | 作用 |
|---|---|
| `agent/src/memory/memory_guard.py` | middleware 实现（本分支新增） |
| `agent/src/memory/persistent.py` | `_default_memory_base()` 路径解析 |
| `agent/src/config/env_schema.py` | `MemoryConfig.base_dir` 声明 |
| `VT_MEMORY_BASE_DIR` | 显式覆盖记忆根目录 |
| （无开关） | middleware **无条件注册**，即债务 D1 |

## 开发历史

- 2026-07-28 首次落地（pre-carve `174d991b`）。
- 2026-08-04 rebase：`VT_MEMORY_BASE_DIR` 收入 `MemoryConfig` 过 env-var AST 门禁；guard 排除 `memory_*` 自触发（[../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md) §5 2026-08-04 条）。
- 2026-08-11 merge+carve 重整为 `016237de`（当前分支 SHA）。
- 2026-08-28 rebase 无冲突重放；上游 memory 侧改动（FTS5 排序衰减、tokenizer 下限、FTS GC #1174）与本特性不同关注面，历次对齐均自动合并共存。

## 验证

- memory 套件 **309 passed / 3 skipped**（DIVERGENCE §3.1）
- env-var AST 门禁 `tools/ci_env_var_gate.py` exit 0（`VT_MEMORY_BASE_DIR` 走 `env_schema.py` 声明 + config accessor，禁止裸 `os.getenv`）
- 路径行为由 `agent/tests/test_env_schema.py` 等 pin

## 状态与上游关系

- 拆两半回流（DIVERGENCE §2.3）：**路径部分 = 队列 ①**（`_default_memory_base()` 改调用期求值 + 旧路径 `~/.vibe-trading/memory` 一次性迁移 + 启动告警）；**中间件部分 = 队列 ⑤**，且必须先解决债务 D1/D2 才过得了社区评审。
- 关联债务（DIVERGENCE §4.5）：D1 无条件注册无 env 开关；D2 按日命名 + 时间戳内容导致 dedup 失效、GC 默认关闭下无限增长；D3 `VIBE_TRADING_HOME` 迁移缺口不覆盖 memory；D4 `MEMORY_BASE` import 期求值。D3/D4 随队列 ① 一并处理。
