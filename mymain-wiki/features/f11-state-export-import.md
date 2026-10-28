---
title: F11 状态导出/导入（vt-state-v1）
description: 跨沙箱搬运策略库的版本化状态包：manifest + 逐文件 sha256、SQLite 一致性快照、原子替换与失败回滚、真实 reopened、每次导入轮换 generation marker；含 full / run 两种 scope。改状态包、排查换箱后策略丢失、对接 RM restore 时必读。触发词：vt-state-v1、state export、state import、content_sha256、reopened、generation marker、换箱、状态镜像。
type: delta
status: active
tags: [state-export, state-import, snapshot, rm-restore]
related: [../branch/MYMAIN_DIVERGENCE.md, f10-strategy-run-entry.md]
---

# F11 状态导出/导入（vt-state-v1）

> 一句话定位：把一个箱的策略库（策略 spec、版本快照、库表）打成一个可校验的 gzip 包，换箱后原子替换并在失败时回滚；不搬运 run 账本、run artifacts、sessions、uploads 或 RM token。

## 能力

- `POST /state/export`：
  - `{"scope":"full"}` 返回 `vt-state-v1` gzip 包（`manifest.json` + `data/`），带 `X-VT-State-Layout` 与 `X-VT-State-Content-Sha256`。
  - `{"scope":"run","vt_run_id":...}` 返回单次运行的有界 JSON（metrics / equity / trades / report）。
- `POST /state/import`：校验 layout + 每个文件 digest，先在 runtime root 下解包，再备份并原子替换策略目录与数据库，最后写 generation marker；任一步失败回滚，rollback 本身失败则保留备份目录并告警。
- `reopened` 是 `_shared.reopen_store()` 的真实结果：导入时本进程已打开 store 才为 `true`。
- generation marker 为 `<content_sha256>:<每次导入的 nonce>`；同一 blob 重复导入也会轮换，第二个进程会重开旧 inode。
- 导入是进程内单飞（`threading.Lock`）；跨进程并发导入尚未用文件锁隔离（见「后续迭代」）。

## 包格式

```text
vt-state-v1.tar.gz
├── manifest.json
└── data/
    └── ...（策略库与库表；路径相对 data/）
```

`manifest.json` 六个键：

| 键 | 含义 |
|---|---|
| `layout_version` | 固定 `vt-state-v1` |
| `created_at` | ISO8601 UTC |
| `content_sha256` | 排序后的 `(path, sha256)` 折叠值；RM 用它比较两次导出 |
| `files` | `[{path, sha256, size}]`，逐文件校验 |
| `databases` | 同结构的 SQLite 文件条目 |
| `excluded` | 被排除的路径（调用方顺序） |

排除项：`strategy_runs.sqlite3`、`runs`、`sessions`、`uploads`、`scheduled_research`、`.config/rm/agent-token`。full 包是**策略库状态**，不是整箱备份；run 历史/产物与凭据不随包迁移。

上限：blob ≤ 64 MiB、文件数 ≤ 2000；单 run payload ≤ 2 MiB。

## API 契约

### 鉴权

与 F10 相同：`Authorization: Bearer <RM_VT_STRATEGY_RUN_TOKEN_FILE 内容>`；401/403 语义一致。

### `POST /state/export`（full）

```http
POST /state/export
Authorization: Bearer <token>
Content-Type: application/json

{"scope":"full"}
```

```http
HTTP/1.1 200 OK
Content-Type: application/gzip
X-VT-State-Layout: vt-state-v1
X-VT-State-Content-Sha256: <64 hex>
```

### `POST /state/export`（run）

```json
{"scope":"run","vt_run_id":"3b3190cbc39a487699e366539dcf7c71"}
```

```json
{
  "vt_run_id":"...",
  "strategy_id":"...",
  "strategy_version":"...",
  "status":"completed",
  "started_at":"2026-10-28T09:30:00Z",
  "finished_at":"2026-10-28T09:31:12Z",
  "title":"...",
  "strategy":"...",
  "metrics":{},
  "equity_curve":[],
  "trades":[],
  "report_markdown":"...",
  "truncated":false
}
```

`truncated` 在 equity / trades / report / run_card 任一触顶时为 `true`；被切断的最后一行会丢弃。payload 超过 2 MiB 时会二分砍半 equity/trades。

### `POST /state/import`

```http
POST /state/import
Authorization: Bearer <token>
X-VT-State-Layout: vt-state-v1
Content-Type: application/gzip

<gzip body>
```

```json
{
  "layout_version":"vt-state-v1",
  "content_sha256":"<与导出头一致>",
  "strategies":5,
  "versions":12,
  "reopened":true
}
```

### 错误表

| HTTP | `error` | 触发 |
|---|---|---|
| 400 | `unknown_scope` / `missing_vt_run_id` | export 参数不合法 |
| 404 | `unknown_vt_run_id` | run scope 找不到 ledger 行 |
| 409 | `export_unstable` / `import_in_progress` | 导出时状态在变 / 已有导入 |
| 413 | `blob_too_large` | 超 64 MiB（Content-Length 或流式累计） |
| 415 | `layout_version_unsupported` | 缺失 / 非 `vt-state-v1` |
| 422 | `manifest_invalid` / `path_escape` / `blob_corrupt` | 结构、路径或 digest 失败 |
| 500 | `export_failed` / `import_failed` | 导出/安装或自检失败（导入已尽力回滚） |

## 一致性语义

- full 导出用 SQLite backup API 取一致快照，再对策略树与库文件逐文件 hash；导出期间策略被改写会触发 `export_unstable`，不返回半新半旧的包。
- 导入先验证 layout 与全部 digest，再触碰 live state；安装顺序为策略目录 → 各数据库（去 stale sidecar）→ generation marker。
- 备份失败或安装失败会反向重放已记录的 rename；回滚失败时备份目录保留在磁盘并在 warning 中命名，调用方可人工恢复。
- 导入后 `_self_check` 校验策略数/版本数与 manifest 一致；不一致按 `import_failed` 处理。

## 关键文件与开关

| 文件 | 作用 |
|---|---|
| `agent/src/api/state_routes.py` | 两条路由、错误映射、流式限长 |
| `agent/src/state_export/manifest.py` | `vt-state-v1` 包格式、路径围栏、digest |
| `agent/src/state_export/_export.py` | SQLite 快照、策略树归档、稳定性检查 |
| `agent/src/state_export/_install.py` | 备份、原子安装、self-check、回滚 |
| `agent/src/state_export/_run_report.py` | run scope 有界 payload、`truncated` |
| `agent/src/strategy_store/_shared.py` | `state-generation` marker、store 重开 |
| `agent/src/state_export/service.py` | 进程内导入单飞与公共 facade |

## 验证

- `agent/tests/test_state_export.py` / `test_state_import.py` / `test_state_routes.py` / `test_state_live_handle.py`：包格式、digest、回滚、`reopened`、marker 轮换、路由错误表。
- 镜像 smoke：full 导出 200 / gzip / layout / 64-hex digest；导入 200，`reopened=true`（此前 F10 lookup 已打开 store）；同 blob 二次导入 marker 旋转。
- 0004 的 RM E2E：2026-10-27 换箱往返 sha `38897cc18d58dc0183f98e17e6957667d83ab0300990dd7fd595f6dc83f0c0c6`。

## 相关提交

| commit | 内容 |
|---|---|
| `f3475546` | 0004 初版（导出/导入、manifest、原子安装） |
| `3eaff213` / `49b370c0` / `169bc891` | PR #4 的 M2/M3/M4/A4 等修复 |
| `9042d270` | PR #4 最终 head |
| `d90d5308` | PR #4 merge 进 `mymain` |
| `bb27b8d4` | import body 流式限长；`run_card.json` 截断上报 |
| `f036c6b9` | state 路由测试改 OpenAPI 断言 |

## 后续迭代

- **跨进程 import 锁**：当前 `threading.Lock` 只保护单进程；两个 entry 进程并发导入同一 box 需要文件锁（`fcntl` / `msvcrt`）或外部单飞。
- **store 关闭竞态**：generation marker 变化时，`get_store()` 可能关闭另一线程仍在使用的 store；需要引用计数或读写锁。
- **full 包不含 run 历史**：`strategy_runs.sqlite3` / `runs` / `sessions` / `uploads` / `scheduled_research` 按设计排除；需要整箱备份时另开方案。
- **备份保留策略**：rollback 失败时备份目录只告警，没有自动清理/索引；长期运行需补 GC。