---
title: F3 回测反思钩子（Backtest Auto-Reflect Hook）
description: run_backtest 成功后自动从 run_card 提取课程的 fire-and-forget 钩子，附延迟基准与并发测试。改回测工具、排查反思未落盘、准备上游 PR ④ 时必读。触发词：auto_reflect、run_card、回测反思、latency bench、并发测试。
type: delta
status: active
created: 2026-07-27
updated: 2026-08-30
tags: [memory, backtest, hook, automation]
related: [../branch/MYMAIN_DIVERGENCE.md]
---

# F3 回测反思钩子

> 一句话定位：让每次成功回测自动沉淀一条课程，不依赖 agent 记得调用记忆工具；上游的 post-backtest attribution 靠 prompt 约定，机制不同，不构成等价物。

## 能力

- `run_backtest` 成功后以 daemon 线程 fire-and-forget 调用 F1 的 `auto_reflect_from_run_dir`，非致命（反思失败不影响回测结果）
- MCP 与 in-process 两个入口均覆盖
- 与 F4 有明确分工：guard 的 `_TOOLS_THAT_PRODUCE_INSIGHTS` 不含 backtest，本钩子是回测反思的唯一入口，无双重写入
- 性能有硬基线：延迟基准 p50<200ms / p95<500ms（bench marker，默认跳过、显式运行）

## 关键文件与开关

| 文件 / 开关 | 作用 |
|---|---|
| `agent/src/tools/backtest_tool.py` | 钩子段（daemon 线程） |
| `agent/tests/memory/test_latency_bench.py` | 延迟基准（`-m bench` 显式运行） |
| `agent/tests/memory/test_concurrent_mcp.py` + `conftest.py` | 5 会话并发测试与 bench marker 注册 |
| `pyproject.toml` | bench marker 声明 |
| `VT_MEMORY_REFLECTIONS` | 总开关（关闭时钩子静默跳过） |

## 开发历史

- 2026-07-27 首次落地（pre-carve `427251d0`）；五-agent 评审的关键修正：回测成功路径上的同步 I/O 改为 daemon 线程 fire-and-forget（[../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md) §5 2026-07-27 条）。
- 2026-08-04 rebase：回测双重反思去重——guard 移除 backtest，run_card 钩子成为唯一反思入口（§5 2026-08-04 条）。
- 2026-08-11 merge+carve 重整为 `0f0e53a1`（当前分支 SHA）。
- 2026-08-28 rebase 无冲突重放。

## 验证

- 延迟基准：`python -m pytest agent/tests/memory/test_latency_bench.py -m bench`，门槛 p50<200ms / p95<500ms（DIVERGENCE §3.1 末节）
- 5 会话并发测试随 memory 套件运行（当前基线 **309 passed / 3 skipped**）
- 冒烟：开 `VT_MEMORY_REFLECTIONS` 跑一次 `run_backtest`，成功后数秒内 `<runtime_root>/memory/reflections/<策略类型>.jsonl` 应新增一条课程（DIVERGENCE §3.2）

## 状态与上游关系

- 上游 post-backtest attribution 是 prompt 驱动，无机制等价物；历次对齐核查均确认未被取代。
- 回流：贡献队列 **④**（DIVERGENCE §2.3），依赖 ②（反思存储 API）先入。
- 与 F4 的边界（谁是回测反思入口）是评审重点，改动时不要破坏 2026-08-04 定下的去重约定。
