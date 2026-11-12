---
title: F14 MCP 长执行控制面（find/start/status/stop、冻结回测与因子原型）
description: VT 为 RM 长执行提供的原实例控制、冻结回测物化与因子原型合同：按 dispatch_key 找回、幂等启动、五态状态、带证据停止、D-3/D-6 摘要、固定提示词与冻结 run_dir、结果回读。改控制面路由、排查 409/unknown、对接 RM 时必读。触发词：mcp-execution-control-v1、dispatch_key、remote_id、execution-instances、backtest/prepare、backtest/execute、factor/prototype、input_digest、registry_digest、stop_state。
type: delta
status: delivered-candidate
gates: pending
verified_by: pending
verified_at: pending
tags: [mcp-execution-control, rm-execution, control-plane, frozen-backtest, factor-prototype]
related: [../branch/MYMAIN_DIVERGENCE.md, f10-strategy-run-entry.md, f12-rm-schedule-tools.md]
---

# F14 MCP 长执行控制面

> 一句话定位：RM 丢启动响应时先 find 再决定重试；VT 用持久派发行、冻结目录和停止证据证明「这是同一个实例、跑的是钉住的输入、停的是原进程」。本文是交付候选记录，不是 gate receipt；三门外部门仍为 `pending`，需要人类确认。

完整的外部合同以 [OpencodeAgent/docs/MCP-EXECUTION-CONTROL.md](../../OpencodeAgent/docs/MCP-EXECUTION-CONTROL.md) 为准。本卡只做导航与范围浓缩。

## 能力

| 路由 | 作用 | 关键成功形状 |
|---|---|---|
| `GET /execution-instances?dispatch_key=...` | 按派发键找回原实例 | `not_dispatched` / `dispatched` / `absent`，三态 body 均带 `contract_version` |
| `GET /execution-instances/{remote_id}` | 读五态状态 | `running|completed|failed|cancelled|unknown` |
| `GET /execution-instances/{remote_id}/result` | 读有界结果 | `result_status=available|unavailable`，`has_more=false` |
| `POST /execution-instances` | 通用启动/重放 | 202，`replayed` 区分新启动与原声明重放 |
| `POST /execution-instances/{remote_id}/stop` | 幂等停止 | `requested|confirmed|unknown` 与证据种类 |
| `POST /backtest/prepare` | 原子物化冻结回测目录 | `config_sha256`、`source_sha256`、`input_digest` |
| `POST /backtest/execute` | 只执行已 prepare 的回测 | 202，固定提示词与 `build_fingerprint` 绑定 |
| `POST /factor/prototype` | 冻结候选库因子批次 | 202，确定性 `factor-` remote id |
| `POST /strategy-runs/{vt_run_id}/cancel` | 0002 旧 cancel | `{vt_run_id,status,stop_state}` |

所有新路由使用 `RM_VT_STRATEGY_RUN_TOKEN_FILE` 指向的 service token。缺头/非 Bearer/空值 `401`，token 文件不可用或不匹配 `403`。错误体固定，不泄露路径、SQL 或 token。

## 关键合同

- **合同版本**：每个新 execution-control 响应（find 三态、status、result、start、stop、prepare、execute、factor-prototype）与每个新 error body（400/404/409/503）都带 `contract_version="mcp-execution-control-v1"`。字段是可加性的，消费方忽略未知字段即可。legacy 0001/0002 body 逐字不变、按设计不带该字段（0001 兼容）。
- **canonical JSON**：`sort_keys=True`、紧凑分隔符、ASCII 转义、禁止 NaN。所有摘要都是 UTF-8 canonical bytes 的 64 位小写 sha256。
- **D-3**：`sha256(canonical_json({schema:"backtest-input-v1",config_sha256,source_sha256,data_source,data_as_of,snapshot_basis}))`。**不包含** RM 的 `declared_digest`/`build_fingerprint`。
- **D-6**：`factor-registry-v1` 文档，candidate 按 id 排序，`tool_schema_version="alpha-zoo-v1+alpha-bench-v1+factor-analysis-v1"`。客户端 `registry_digest` 只用于校验，服务端必须重算，不符即 `409 input_digest_mismatch`。
- **回测 15 字段**：`strategy_id,strategy_version,market,symbols,start_date,end_date,bar_interval,data_source,data_as_of,snapshot_basis,initial_cash,currency,commission_bps,tax_bps,slippage_bps`；`start_date < end_date <= data_as_of`；顶层 `data_source/data_as_of/snapshot_basis` 必须与内嵌 config 完全相等。
- **成本语义**：commission 开平仓都收 `notional*commission_bps/10000`；tax 只在平仓收；slippage 买方加、卖方减。neutral 块存在时没有最低佣金、过户费、默认印花税、引擎费率乘数或 maker/taker/funding 默认。
- **启动绑定**：prepare 行 + 精确 `run_dir`/三个 digest + 固定提示词 + 调用方 64-hex `build_fingerprint`；`begin_start` 先持久化完整 start 声明及摘要，再 spawn。相同声明只 spawn 一次，异声明 409 并回原 remote id。
- **停止**：请求必带 `{dispatch_key,remote_id,stop_key,deadline_at,contract_version}`；先落 `requested` 再发信号；`confirmed` 必须有 `terminal_record/process_exit/process_absent/ttl_expired/explicit_absence` 之一；`unknown` 无证据且不可升级；`confirmed` 不可降级；重启后无旧句柄，非终态读 `unknown`，不按裸 pid 杀进程。
- **find 的 absent**：终态派发行 + 非 `process_exit` 的 confirmed stop 才构成 absent。仅凭 remote_id 404 不是缺席证据。
- **结果回读**：回测返回完整 summary 1.0 + run_card artifacts + prepare provenance；因子返回 `result.json`（registry/tool schema/contract 版本、`quality_bounds`、`artifacts{ic_series,ic_summary,group_equity}`、`artifact_paths`）。缺失/非法路径为 `unavailable`，不截断成部分成功。
- **horizon**：标签按持仓 bar 定位；IS 要求 `label_end < split`；OOS 要求 `factor_date >= split` 且 `label_end <= end` 且 `label_end <= as_of`；跨边界标签置 NaN 并按 insufficient 处理，不补数。
- **0003 边界**：prepare 只做策略/版本文件存在性预检，不新增版本查询路由，不校验 market/交易假设。

## 关键文件

| 文件 | 职责 |
|---|---|
| `agent/src/strategy_runs/control_contract.py` | wire 合同、canonical JSON、D-3、固定提示词 |
| `agent/src/strategy_runs/control_store.py` | dispatch/start/stop 的 CAS 持久化 |
| `agent/src/strategy_runs/control_service.py` | find/status/result 只读投影与 legacy 合并 |
| `agent/src/strategy_runs/execution_start_service.py` | prepare 绑定校验、begin_start、spawn 顺序 |
| `agent/src/strategy_runs/execution_stop_service.py` | 幂等 stop 投影 |
| `agent/src/strategy_runs/native_supervisor.py` | 进程组、终态证据、重启不重放 |
| `agent/src/strategy_runs/backtest_materialize.py` | 原子物化、replay 校验、本地数据摘要 |
| `agent/src/strategy_runs/backtest_config.py` | 15 字段、时序、重复 provenance、成本适配 |
| `agent/src/api/backtest_prepare_routes.py` | `/backtest/prepare` HTTP 面 |
| `agent/src/api/execution_control_routes.py` | find/status/result/start/stop HTTP 面 |
| `agent/src/api/factor_prototype_routes.py` | `/factor/prototype` HTTP 面 |
| `agent/src/api/strategy_run_routes.py` | 0001/0002 legacy 路由 |
| `agent/strategy_run_server.py` | 专用入口与路由装配 |

## 验证

本地验证命令与逐项覆盖见合同文档 §17。核心命令：

```bash
python -m pytest agent/tests/test_execution_control_contract.py \
  agent/tests/test_factor_registry_freeze.py -q
python -m pytest agent/tests/test_execution_control_store.py \
  agent/tests/test_execution_control_start_stop.py \
  agent/tests/test_execution_start_store.py \
  agent/tests/test_execution_supervisor.py -q
python -m pytest agent/tests/test_execution_control_routes.py \
  agent/tests/test_backtest_http_contract.py -q
python -m pytest agent/tests/test_backtest_prepare.py \
  agent/tests/test_backtest_execute.py \
  agent/tests/test_backtest_execution_config.py -q
python -m pytest agent/tests/test_factor_prototype.py \
  agent/tests/test_factor_prototype_local_integration.py -q
python -m pytest tools/test_mcp_execution_control_docs.py -q
python tools/ci_private_files_gate.py
```

这些是本地单元/路由/集成证据，不构成 A1-A11 的验收入口。G-control、G-backtest、G-factor 仍 `pending`；没有 E2E，没有 receipt。

固定镜像交付验证（2026-11-10，v3.18.0 本地构建、未推送；历史记录）：

| 项 | 结果 |
|---|---|
| 镜像 | `opencode-serve:v3.18.0-mcp-execution-control-20261109`，ID `sha256:a6a6bbc74f6ad64cf6a7642573e761c12af0b655547b91984132e3fe3a5a8b60`（短 `a6a6bbc74f6a`），`linux/amd64`，无 RepoDigest |
| 就绪 | listening 5.6s，首次 `/config` 200 24.3s，VT MCP 28.7s，entry listening 32.0s，entry `/health` 200 34.9s（预算 300s） |
| A1-A11 真实 HTTP | PASS，365 个响应，378 次 attempt，无 MCP/worker mock，30 个负向响应 |
| 全量离线回归 | `42 failed, 23060 passed, 172 skipped in 39:47`；42 个 FAILED id 与 `fork/mymain` 基线逐行相同，新增失败 0 |
| 静态门 | Ruff clean；Black 仅剩 `agent/backtest/runner.py` 在基线同样存在的整文件漂移；私有文件门与 `git diff --check` clean |

证据在未跟踪的 `.omo/evidence/vt-mcp-execution-control/20261109T151921Z/`，入口为 `vt-owner-handoff.md` 与 `rm-integration-notes.md`，`evidence-sha256.txt` 可复算。该证据是本地固定镜像验证，不是 RM E2E，也不构成 receipt。

生产池重交付 canary（2026-11-12）：

| 项 | 结果 |
|---|---|
| 分支/标签 | `mymain` HEAD `084207fb` / `v3.18.3-mcp-execution-control-20261111` |
| 镜像 | `registry-vpc.cn-hangzhou.aliyuncs.com/jiefengnewsv2/opencode:v3.18.3-mcp-execution-control-20261111@sha256:6b8aadec567d8e6d145bc93f160a679adaacae16c9b3401518d7142a98c49372`；呼和浩特镜像仓同 manifest digest |
| 镜像验证 | 相关回归 543 passed；A1–A11 真实 HTTP PASS（110 responses / 123 attempts，无 MCP/worker mock）；启动就绪 PASS |
| 生产池 | `opencode-poc` 与 `mcp-long-task` 模板已切该 digest，`replicas 0`，generation/observed `23/23` 与 `3/3` |
| ImageCache | `opencode-v3183-mcp-exec-20261111` = `imc-bp19rbqnrfyvvkdp4wl9`，命中 size 5 GiB |
| canary（opencode-poc） | `default--opencode-poc-7lssg`：claim 12.23s、route-wait 6.73s、pod 2/2 Running、0 restarts；`/health` 200；`find` → `not_dispatched` + `contract_version=mcp-execution-control-v1`；`prepare`/`factor` 空体 400 均带合同版本；`/config` 200；opencode 1.19.3；VT MCP 99 tools |
| canary（mcp-long-task） | 首次 23s 时 `route-wait` 已 ready 但 entry `:8000` 尚未监听（预热时序，不是镜像失败）；重跑等待 entry `/health`，45s 就绪，`find`/`prepare` 同上 PASS。**`route-wait` 只代表网关路由，不代表应用 warm** |
| 清理 | `kill` 覆盖三个 canary 箱后 Sandbox/Pod 均为 0，生产池保持 `replicas 0` |
| 边界 | 这是 VT 侧生产池可用性 canary，不是 RM live E2E，也不是 gate receipt；G-control/G-backtest/G-factor 仍 `pending` |

证据：`.omo/evidence/mcp-execution-control-v3.18.3-20261111/production-canary-20261112.md`；RM 侧记录见 `aliyun-acs-agent-service/deploy/prod/README.md` §7.18。

## RM 集成前置与已知差异

1. **D-3 摘要不一致**：RM `backtest_inputs._input_digest` 目前把 `declared_digest` 和 `build_fingerprint` 纳入 canonical 文档；VT D-3 明确排除两者。RM 必须对齐或先重算。
2. **prepare 不返回 `build_fingerprint`**：execute 要求 RM 自带 64-hex 指纹，VT 只做格式校验并绑定进 start 摘要。
3. **receipt 枚举缺 `backtest.execute`**：RM `ExecutionCapability` 当前没有该成员；VT 路由已存在，但 RM 在扩展枚举前无法为它签发 receipt。
4. **无 live adapter**：RM 只有 `BacktestBackend` / `StopBackend` / `ExecutionSignalClient` 等协议与测试 fake，没有具体 HTTP adapter；`BacktestBackend` 也不含 result-read 方法，需要单独适配。
5. **Carhart warmup 漂移**：RM fixture 仍是 `academic_carhart_mom.min_warmup_bars=252`，当前 VT 注册表是 `253`。RM 必须冻结 VT 新发布的 metadata 与 digest，否则 D-6 重算会 409。
6. **find 与所有新 body 都带 contract_version**：find 三态（`not_dispatched`/`dispatched`/`absent`）、status、result、start、stop、prepare、execute、factor-prototype 的成功体，以及这些路由的每个新 error body（400/404/409/503），都带 `contract_version="mcp-execution-control-v1"`。字段是可加性的，RM 的 find 解析器忽略未知字段即可，不得把字段存在解释成版本不符。legacy 0001 `GET /strategy-runs/{vt_run_id}` 与 0002 cancel body 按 0001 兼容要求逐字不变、按设计不带该字段；401/403 仍是共享 auth 的固定 `{"detail":...}` body。

## 状态与上游关系

- 状态：`delivered-candidate`；`verified_by=pending`、`verified_at=pending`；三门外部门 `pending`；人类确认前不得写入 verified receipt。
- 本地实现已通过对应单元/路由/集成测试；未做 RM 端到端联调，未声明 A10/A11 或任何 gate 已验证。
- 上游关系：个人部署与 RM 消费面独有，暂不回流上游；若未来回流，需按社区 `CONTRIBUTING.md`、`AGENT_CONTRIBUTOR_GUIDE.md` 与 PR 模板重新切割，并移除本分支的 RM 专属描述。