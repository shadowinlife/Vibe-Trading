---
title: F15 精确策略版本源码读取
description: 部署归属绑定、持久版本登记、单文件精确字节、血缘与状态迁移不变量。涉及 strategy-version-source、owner binding、version registry、tombstone、lineage、策略版本、源码摘要时阅读。
type: feature
status: delivered
created: 2026-11-14
updated: 2026-11-14
tags: [strategy, version, source, owner-binding, registry]
related: [../branch/MYMAIN_DIVERGENCE.md]
---

# F15 · 精确策略版本源码读取

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

本卡记录 VT 已交付与部署工程验收：RM 已为每个新沙箱写入归属绑定，生产镜像与 15 组场景及 A8/A17 均已通过。A11 具名人工验收与 USER 生产验收仍 pending，不构成最终 verified 回执。既有回测执行器、0001 策略运行入口和 0013 执行控制语义不因本能力改变。

## 状态与上游关系

该能力是个人部署与 RM 消费面的新增合同，暂不回流上游。发布分支为 `feat/strategy-version-source`；不可变镜像标签和部署证据以 `MYMAIN_DIVERGENCE` 的最新记录为准。
