---
title: F16 精确策略版本源码读取
description: 部署归属绑定、持久版本登记、单文件精确字节、血缘与状态迁移不变量。涉及 strategy-version-source、owner binding、version registry、tombstone、lineage、策略版本、源码摘要时阅读。
type: feature
status: local-release-candidate
created: 2026-11-14
updated: 2026-11-20
tags: [strategy, version, source, owner-binding, registry]
related: [../branch/MYMAIN_DIVERGENCE.md]
---

# F16 · 精确策略版本源码读取

## 能力

`POST /strategy-version-source` 按部署绑定用户、精确策略 ID 和版本标签返回唯一的 `code/signal_engine.py` 原始字节及 SHA-256。它解决 RM 在冻结回测输入前无法证明策略归属、不可变版本身份和源码血缘的问题，不提供 latest/current 推断、目录导出或 Git HEAD 回退。

## 关键文件与开关

- `agent/src/api/strategy_version_source_routes.py`：认证、归属绑定、读取顺序和错误映射。
- `agent/src/api/strategy_owner_binding.py`：部署绑定文档及 `RM_VT_STRATEGY_OWNER_BINDING_FILE` 校验。
- `agent/src/strategy_store/version_registry.py`：持久版本登记和状态。
- `agent/src/strategy_versions/publisher.py`：pending → active 发布、崩溃恢复和 tombstone。
- `agent/src/state_export/`：状态迁移中的身份保持和冲突拒绝。
- `RM_VT_STRATEGY_RUN_TOKEN_FILE`：既有 service token 文件，保留原鉴权方式。
- `RM_VT_STRATEGY_OWNER_BINDING_FILE`：每个沙箱独立的归属绑定文件路径。

绑定文件必须是 `opencode` 拥有、无符号链接、权限 `0600` 的 `strategy-owner-binding-v1` JSON。共享模板只能配置路径，不能给所有用户配置同一个 owner。

## 验证与边界

本地验收覆盖精确字节、跨用户同形拒绝、错误版本、摘要漂移、重启、tombstone、发布崩溃、state 往返和跨 owner 不可见；选定回归为 961 项。完整合同见 [STRATEGY-VERSION-SOURCE.md](../../OpencodeAgent/docs/STRATEGY-VERSION-SOURCE.md)。

本卡是本地发布候选记录，不是生产验收回执。生产部署仍需 RM 为每个新沙箱写入归属绑定、amd64 镜像 digest、部署后证据和具名人工验收。既有回测执行器、0001 策略运行入口和 0013 执行控制语义不因本能力改变。

## 状态与上游关系

该能力是个人部署与 RM 消费面的新增合同，暂不回流上游。发布分支为 `feat/strategy-version-source`；不可变镜像标签和部署证据以 `MYMAIN_DIVERGENCE` 的最新记录为准。

## 2026-11-19 前向修复注记（0014 回归恢复）

v3.19.0 对齐合并丢弃了从未合入 mymain 的 0003 交付面。按 RM 跨仓请求 0014，本能力已从 `fbd73a55`（tag `v3.18.4-strategy-version-source-20261114`）字节一致地前向恢复到 `a709b99b`（v3.19.0 线）之上的 `fix/strategy-version-source-restore` worktree；v3.19.0 的 L0/L1 source-integrity 工作完整保留，未回滚。路由、合同字段、错误码、鉴权与归属绑定语义均未改动。20 个恢复验收套件 280 passed，A0 修复后真实 loopback HTTP 探针 PASS；全量回归 23414 passed / 172 skipped / 27 failed，27 个失败在未打补丁的 base worktree（同 HEAD）逐条复现，均为预存失败，与恢复无关。证据见 `.omo/evidence/0014-version-source-restore/`（`DELIVERY.md`、`a0-postfix.txt`、`base-attribution.txt` 等；修复前探针 `a0-prefix.txt` 已由 lead 在 base worktree 采集（框架 404，回归确认））。状态 delivered/pending：人工确认与 RM 验收 pending，不是 verified。2026-11-20 已 rebase 到 v3.20.1 线（base `48bba44d`）并复验：策略/状态/执行控制/paper_trade 交互面 2673 passed / 0 failed，A0 修复前 404 / 修复后七项 PASS，50/50 非 wiki 文件与 `fbd73a55` 字节一致。

## 编号注记（2026-11-20）

原 0003 交付（2026-11-14）拟用编号 F15；v3.20.0 pt-v2.0 组合栈（2026-11-19）已在权威台账占用 F15。本能力尚未合入 mymain，合入时归位 **F16**，卡片文件同步更名 `f16-strategy-version-source.md`；原交付分支 `feat/strategy-version-source` 的历史编号不追溯改写。
