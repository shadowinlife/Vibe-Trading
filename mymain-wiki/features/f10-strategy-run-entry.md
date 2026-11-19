---
title: F10 策略运行服务入口（RM Strategy Run Entry）
description: 在 F9 runner 之上暴露给 RM 的两条 service-token HTTP 路由：按版本启动一次运行、读终态；含 run_key 幂等、per-box 寻址、异步 sweeper 与重启收敛。改入口、排查 run 悬挂、对接 RM 时必读。触发词：strategy run entry、/strategy-runs、vt_run_id、run_key、service token、strategy_not_found、version_not_found、timeout、recovered_after_restart。
type: delta
status: active
tags: [strategy-run, rm-entry, service-token, http-api]
related: [../branch/MYMAIN_DIVERGENCE.md, f9-strategy-library-scheduling.md]
---

# F10 策略运行服务入口

> 一句话定位：RM 只负责「什么时候跑」，VT 只负责「按钉死版本跑一次并给出终态」；入口不维护第二套调度表，也不把 run 写进 `scheduled_research`。

## 能力

- 两条冻结路由：`POST /strategy-runs` 启动一次运行并返回不透明 `vt_run_id`；`GET /strategy-runs/{vt_run_id}` 只读三字段状态。
- 专用进程 `agent/strategy_run_server.py` 监听 `:8000`（`VT_STRATEGY_RUN_PORT`），与 opencode `:4096` 由 entrypoint 同时监督。
- 全量 API server 的 opt-in 入口（`VT_STRATEGY_RUN_ENABLED=1`）在 lifespan 中成对 start/stop 同一个进程级 service；未开启时全量 API 不注册 `/strategy-runs`。
- `run_key` 在箱内 SQLite ledger 中 `NOT NULL UNIQUE`，是幂等锚点；重复提交返回首次的 `vt_run_id`。
- 先校验目标箱再 claim：`sandbox_id` 与本箱身份不匹配时 `403 target_mismatch`，且不消耗 `run_key`。
- 异步执行：`submit` 立即返回；sweeper 轮询 handle，`timeout_s` 到点落 `failed/timeout`；进程重启时带 handle 的行 rehydrate + poll（不重跑），无 handle 的行超过 grace 落 `failed/recovered_after_restart`。
- 运行期不触达 broker/order；runner 只使用 F9 的精确工具白名单。

## API 契约

### 鉴权

```http
Authorization: Bearer <RM_VT_STRATEGY_RUN_TOKEN_FILE 内容>
```

缺失/非 Bearer/空 → `401 {"detail":"unauthorized"}`；token 文件不可用/不匹配 → `403 {"detail":"forbidden"}`。token 每次请求重读，支持热轮换；路径与内容不入日志。

### `POST /strategy-runs`

```json
{
  "strategy_id": "demo_momentum",
  "strategy_version": "v3",
  "run_key": "sched_9b2271f153f769d32f57e3fe:2026-10-28T09:30:00Z",
  "sandbox_id": "ea-abc123",
  "timeout_s": 3600
}
```

| 字段 | 约束 |
|---|---|
| `strategy_id` | F9 合法 id；本地库存在 |
| `strategy_version` | 精确版本；缺失时 run 终态 `failed/version_not_found` |
| `run_key` | 1–512 字符，逐字保存；重复 → 409 |
| `sandbox_id` | 可选；提供时必须等于本箱身份，否则 403 |
| `upstream` | 可选；`sandbox_id` 与 `upstream` 至少一个 |
| `timeout_s` | 60–86400，默认 3600 |

成功 `202 {"vt_run_id":"..."}`；重复 `409 {"error":"duplicate_run_key","vt_run_id":"..."}`；模型校验 `422`；service 构造失败 `503 {"detail":"entry unavailable"}`。

### `GET /strategy-runs/{vt_run_id}`

```json
{"status":"running","error":null,"error_kind":null}
```

`status` ∈ `running|completed|failed`；`error_kind` ∈ `strategy_not_found|version_not_found|timeout|runner_error|recovered_after_restart`。未知 id → `404 {"detail":"unknown vt_run_id"}`。路由只读，不启动、不轮询、不改状态。

## 关键文件与开关

| 文件 / 开关 | 作用 |
|---|---|
| `agent/strategy_run_server.py` | 专用 app：health + 两条 run 路由 + state 路由 |
| `agent/src/api/strategy_run_routes.py` | 请求模型、鉴权依赖、202/409/200 投影 |
| `agent/src/strategy_runs/service.py` | claim、version check、runner.start、sweep、recover |
| `agent/src/strategy_runs/store.py` | `strategy_runs` ledger（`run_key UNIQUE`、handle_json） |
| `agent/src/api/strategy_run_lifecycle.py` | 全量 API opt-in 的 start/stop（`f036c6b9` 拆出） |
| `agent/src/api/strategy_run_auth.py` | service token 读取与 401/403 |
| `VT_STRATEGY_RUN_PORT` | entry 端口，默认 `8000` |
| `VT_STRATEGY_RUN_ENABLED` | 全量 API 是否注册 `/strategy-runs` |
| `VT_SANDBOX_ID` / `RM_LLM_SANDBOX_ID_PREFIX` | 本箱身份；未显式设置时用 `default--${HOSTNAME}` |

## 验证

- `agent/tests/test_strategy_run_routes.py`：401/403/202/409/404、OpenAPI 路径、opt-in lifespan。
- `agent/tests/test_strategy_run_execution.py`：claim、runner、sweep、timeout、重启收敛、sweeper 不阻塞事件循环。
- `agent/tests/test_strategy_run_store.py` / `test_strategy_run_auth.py`：ledger 唯一性、token 边界。
- 镜像 smoke：合法提交 202 并收敛到 `failed/strategy_not_found`；无 token 401、错 token 403、未知 id 404（26/26）。

## 相关提交

| commit | 内容 |
|---|---|
| `7f67ace8` | 0001 入口初版（PR #3 合并前 head） |
| `585845ad` | PR #3 merge：入口 + 0007-1/2/3/4 |
| `d90d5308` | PR #4 merge：state + 调度工具，合并树保留入口 |
| `bb27b8d4` | sweeper/recover 移到 worker thread；import 流式限长；`/health` token_configured |
| `f036c6b9` | lifecycle 拆到 `strategy_run_lifecycle.py`；路由断言改 OpenAPI |

## 后续迭代

- **0002**：`POST /strategy-runs/{id}/cancel` 未实现；删除调度只停后续槽。
- **0003**：RM 创建调度前的 `strategy/version` 预检未实现；当前由 VT 在 submit 时判定。
- 全量 API 的 opt-in 入口与专用入口不应对同一箱同时启用；`build_service` 是进程级单例，跨进程仍各有一份 ledger。
- 跨进程 import 锁与 store 关闭竞态见 F11。