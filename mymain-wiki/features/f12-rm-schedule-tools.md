---
title: F12 RM 调度工具（六个 MCP 工具）
description: 箱内助手通过六个 MCP 工具把策略调度意图转发给 RM 控制面：create / list / pause / resume / delete / retarget；RM 拥有调度表与运行历史，VT 只做本地策略/版本解析与错误投影。改工具契约、排查调度 409/版本漂移、对接 RM 时必读。触发词：schedule_strategy、list_strategy_schedules、pause_strategy_schedule、resume_strategy_schedule、delete_strategy_schedule、retarget_strategy_schedule、last_run、drift、schedule_limit_reached。
type: delta
status: active
tags: [rm-scheduling, mcp-tools, control-plane, strategy-schedule]
related: [../branch/MYMAIN_DIVERGENCE.md, f9-strategy-library-scheduling.md, f10-strategy-run-entry.md]
---

# F12 RM 调度工具

> 一句话定位：把「什么时候跑」的所有权交给 RM；箱内只保留六个薄工具，负责本地策略/版本检查、cadence 校验和错误脱敏，不再写 `scheduled_research`，也不启动第二套调度器。

## 能力

| 工具 | 作用 | 成功返回 |
|---|---|---|
| `schedule_strategy` | 为 ready 策略创建一条 RM 调度；默认钉当前版本 | `{"ok":true,"schedule_id","next_run_at","pinned_version"}` |
| `list_strategy_schedules` | 列 RM 调度；补本地 `current_version` / `drift` / `last_run` | `{"ok":true,"schedules":[...],"legacy_jobs_ignored":N}` |
| `pause_strategy_schedule` | 暂停一条调度，保留运行历史 | `{"ok":true,"schedule_id","enabled":false,"next_run_at"}` |
| `resume_strategy_schedule` | 恢复一条暂停的调度 | `{"ok":true,"schedule_id","enabled":true,"next_run_at"}` |
| `delete_strategy_schedule` | 删除一条调度；历史留在 RM | `{"ok":true,"schedule_id","deleted":true}` |
| `retarget_strategy_schedule` | 把调度重钉到指定/当前版本，保留 schedule id 与历史 | `{"ok":true,"schedule_id","pinned_version"}` |

- 六个工具都只在调用时读 `RM_AGENT_API_URL` 与 per-box token；没有 token / 地址时返回 `scheduling_unavailable`，不静默降级。
- 不再写本地 `scheduled_research`；旧目录只读，`legacy_jobs_ignored` 统计其条目数（至少 1，M6）。
- 箱内入口不注册 `/strategy-schedules`（F10 的 app 只挂 run/state/health），即使全量 API 的 F9 路由存在也默认关闭。

## 工具契约

### `schedule_strategy`

参数：

```text
strategy_id: str
kind: "cron" | "interval" | "daily"
strategy_version: str | None = None
name: str | None = None
cron_spec: str | None = None       # kind=cron 必填，5 字段
interval_s: int | None = None      # kind=interval 必填，正整数
time_of_day: str | None = None     # kind=daily 必填，HH:MM
tz: str | None = None              # 默认 Asia/Shanghai
```

- 本地策略必须 `readiness == ready`；目标版本必须已物化（code 需要 `config.json` + `code/signal_engine.py`，workflow 需要 `strategy.json`）。
- 任一检查失败在调用 RM 前返回 `strategy_not_found` / `strategy_not_ready` / `version_not_found` / `invalid_request`。
- RM 返回 2xx 但没有 `schedule_id` 时按 `rm_unreachable` 处理，绝不假报创建成功。

### `list_strategy_schedules`

- RM row 原样透传：`schedule_id`、`strategy_id`、`name`、`kind`、`pinned_version`、`cron_spec`、`interval_s`、`time_of_day`、`tz`、`enabled`、`next_run_at`、`created_at`、`updated_at`、`last_run`。
- 本地附加：`current_version`（缺失为 `null`）、`drift`（两者都已知且不等）、`last_run`（0006；RM 不返回则为 `null`）。
- `legacy_jobs_ignored`：旧调度目录条目数；用于提示用户“旧作业从未触发”。

#### `list_strategy_schedules` 响应示例

```json
{
  "ok": true,
  "schedules": [
    {
      "schedule_id": "ss_9b2271f153f769d3",
      "strategy_id": "demo_momentum",
      "name": "demo momentum",
      "kind": "cron",
      "pinned_version": "v3",
      "cron_spec": "30 9 * * 1-5",
      "interval_s": null,
      "time_of_day": null,
      "tz": "Asia/Shanghai",
      "enabled": true,
      "next_run_at": "2026-10-29T01:30:00Z",
      "created_at": "2026-10-28T09:00:00Z",
      "updated_at": "2026-10-28T09:00:00Z",
      "last_run": {
        "run_id": "sr_4b5ec3f7284eb6bd0798a822",
        "status": "completed",
        "trigger": "scheduled",
        "scheduled_for": "2026-10-28T01:30:00Z",
        "started_at": "2026-10-28T01:30:02Z",
        "finished_at": "2026-10-28T01:31:12Z",
        "error_kind": null,
        "error": null,
        "report_run_id": "sr_4b5ec3f7284eb6bd0798a822"
      },
      "current_version": "v4",
      "drift": true
    }
  ],
  "legacy_jobs_ignored": 2
}
```

`last_run` 的键来自 RM：`run_id`、`status`、`trigger`、`scheduled_for`、`started_at`、`finished_at`、`error_kind`、`error`、`report_run_id`；没有运行时为 `null`。

### pause / resume / delete / retarget

- `schedule_id` 必须是安全的单路径段；非法 id 返回 `invalid_request`。
- `retarget` 先解析本地目标版本（参数或当前版本），未物化时在调用 RM 前返回 `version_not_found`。
- 删除只停止后续槽；已派发 run 在 VT 侧继续跑（0002 的 cancel 未实现）。

## 错误契约

失败统一为：

```json
{"ok":false,"error":"<code>","detail":"<≤300 字符，已脱敏>"}
```

| code | 触发 |
|---|---|
| `scheduling_unavailable` | `RM_AGENT_API_URL` 未配置 / token 不可读 |
| `unauthorized` | RM 拒绝 per-box token |
| `strategy_not_found` | 本地库没有该策略 |
| `strategy_not_ready` | 策略未过就绪门 |
| `schedule_limit_reached` | RM 每用户调度上限（409 `{"detail":"schedule_limit_reached"}`，M1） |
| `version_not_found` | 本地没有该版本的物化文件 |
| `not_found` | RM 没有该 schedule_id |
| `rm_unreachable` | 网络/协议失败，或 2xx 缺关键字段 |
| `invalid_request` | cadence / id / 字段校验失败 |

`detail` 中的 Bearer 会被替换为 `[redacted]`；token、Authorization、RM 请求体不进入返回或日志。

## 与 RM 的边界

- RM 拥有：调度表、due-time loop、run history、每用户限额、暂停/恢复语义、`last_run`。
- VT 拥有：本地策略库与版本、cadence 语法校验、当前版本/drift 计算、错误投影。
- 工具调用 RM 的 `/api/v1/agent/strategy-schedules`（create/list/detail/patch/delete/pause/resume/retarget/run-now/runs），使用 `Authorization: Bearer <per-box token>`。
- `AgentScheduleClient` 设置 `trust_env=False`，不把箱内 bearer 转发给环境代理。

## 关键文件与开关

| 文件 / 开关 | 作用 |
|---|---|
| `agent/src/agent_schedules/tools.py` | 六个工具、cadence、错误 envelope、legacy 计数 |
| `agent/src/agent_schedules/client.py` | RM HTTP client、409/401/404 映射、`trust_env=False` |
| `agent/src/tools/__init__.py` / `agent/mcp_server.py` | 工具注册与 MCP 面 |
| `RM_AGENT_API_URL` | 箱可达的 RM agent API 基址 |
| `RM_AGENT_TOKEN_FILE` | per-box token；默认 `~/.config/rm/agent-token` |
| `VT_STRATEGY_SCHEDULE_ROUTES_ENABLED` | 全量 API 的 F9 路由开关，箱内默认关闭 |

## 验证

- `agent/tests/test_agent_schedules.py`：六个工具的返回形状、cadence、错误映射、409 detail、legacy 计数、drift/last_run。
- `agent/tests/test_agent_schedules_client.py`：HTTP 状态映射、token 读取、代理隔离。
- 324 聚焦回归含 agent schedules；镜像内 `VT MCP server OK — 99 tools registered`。
- RM 侧七条路由/七个动作已用真实 per-box token 跑通；**箱内 MCP → RM 的 B8 仍未活体验证**。

## 相关提交

| commit | 内容 |
|---|---|
| `d90d5308` | PR #4 merge：六个工具、M1/M6、0006 `last_run` |
| `3eaff213` / `49b370c0` / `169bc891` | M1 409 detail、M6 legacy 计数等修复 |
| `bb27b8d4` | `trust_env=False`；AGENTS 工具数 94/99 |
| `f036c6b9` | `SKILL.md` 补齐 94 tools（含六个调度工具） |

## 后续迭代

- **B8**：箱内 MCP 工具 → 真实 RM 的活体调用未验证；`VT-STRAT-0005` 保持 `delivered`。
- **0003**：创建调度前的 RM 侧 `strategy/version` 预检未实现；当前只在 VT 本地校验。
- **0002**：删除/暂停不会取消已派发 run；如需「删除即停」，要新增 cancel 端点。
- 调度目录迁移：旧 `scheduled_research_jobs.json` 只读；未来可提供一次性迁移/清理命令。