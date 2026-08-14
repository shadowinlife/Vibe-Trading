# 07 · 实盘交易运行时（Live Trading Runtime）

> **文档族**: Vibe-Trading 前端集成知识库（`.omo/memory/frontend-integration/`）
> **日期**: 2026-08-14 · **读者**: 在 IM 插件视图层重建 Runtime 页面（broker 卡片、授权、mandate 提交对话框、halt/resume 开关、runner 状态）的前端团队
> **事实来源**: `agent/src/api/live_routes.py`（端点权威）、`agent/src/api/sessions_routes.py`（SSE 桥接）、`agent/src/live/`（halt/mandate/audit/liveness 状态机）、`agent/src/trading/{profiles,service,types}.py`（connector 注册表）、`frontend/src/lib/api.ts`（TypeScript 契约）、`frontend/src/pages/Runtime.tsx`（参考渲染）
> **通用约定**: 认证、错误包体、SSE 传输机制（重连/去重/票据）见 [./00-architecture-and-conventions.md](./00-architecture-and-conventions.md)，本篇不再重复。

> ⚠️ **安全关键面**：本篇覆盖的端点是系统中唯一能激活真实资金交易的 HTTP 面。语义精确性优先于简洁——每个字段、枚举、触发条件均逐行核对源码，勿凭猜测实现。

---

## 1. 能力概览

### 1.1 功能 → 端点映射

| 功能 | 端点 | 方法 | 说明 |
|---|---|---|---|
| Mandate 提交（唯一写路径） | `/mandate/commit` | POST | 用户在提案卡片上明确确认后激活实盘授权 |
| Kill switch 触发 | `/live/halt` | POST | 全局或单 broker 立即停机 |
| Kill switch 解除 | `/live/resume` | POST | 清除停机哨兵文件 |
| 运行时状态（可轮询） | `/live/status` | GET | 全部 broker 的 auth/mandate/runner/halted 快照 |
| Connector 连通性校验 | `/live/connectors/{profile_id}/verify` | POST | 仅 `broker_sdk` 型 live profile；幂等只读 |
| OAuth 引导（发现型） | `/live/authorize` | POST | 返回操作指引，不做服务端重定向 |
| Runner 启动 / 停止 | `/live/runner/start` · `/live/runner/stop` | POST | 持久自主交易 runner（当前仅 robinhood） |
| live SSE 事件 | `/sessions/{id}/events` | GET(SSE) | 与聊天共用同一条流，**没有独立流**（见 §4） |

所有端点均挂 `require_auth`（00 篇 §3.3）；IM 插件以服务端身份调用时直接带 Bearer 头即可。

### 1.2 同意模型（Consent Model）

实盘通道是一条单向状态机，**每一步都结构性地把"模型"排除在写路径之外**（`live_routes.py` 模块 docstring、`src/live/mandate/commit.py` 模块 docstring）：

1. **PROPOSE（agent 发起）**：用户在聊天中表达实盘意图后，agent 调用只读工具 `propose_mandate_profiles`（`agent/src/tools/propose_mandate_tool.py`），合成 2–4 个编号候选 mandate profile。每个 profile 都被账户 ceilings **向下钳制**（只能收紧、不能放大），并落盘为 proposal 记录。proposal 不授予任何交易权限。该工具结果经 SSE 桥接以 `mandate.proposal` 事件到达前端（§4.2）。
2. **COMMIT（用户显式确认）**：前端渲染提案卡片；用户点选某个 profile 并经二次确认对话框后，前端调用 `POST /mandate/commit`，请求体必须携带 `consent_ack: true`。这是**唯一**的 mandate 写路径——它不是 agent 工具，agent 循环没有任何对 `commit_mandate()` 的引用；`consent_ack` 只能由用户界面在显式点击/按键时置位。
3. **有界执行**：mandate 生效后，agent 的下单工具被 order guard 包裹（`src/live/order_guard.py`、`src/live/sdk_order_gate.py`），每笔写操作在触达 broker 前依次 fail-closed 检查：mandate 有效性 → 过期 → kill switch → 意图可解析 → 持仓/资金快照 → 限额校验。每个决定（放行/拒绝/暂停重授权）都写一条审计记录并以 `live.action` 事件广播。
4. **Kill switch（用户随时）**：`POST /live/halt` 在文件系统写入哨兵文件（`<runtime_root>/live/HALT` 全局，或 `<runtime_root>/live/<broker>/HALT` 单 broker）。停机判定是**纯文件系统检查**，不依赖 LLM 配合、不依赖进程内状态、不依赖 SSE 总线——即使 agent 循环卡死也生效（`src/live/halt.py`）。
5. **自动过期**：mandate 默认 30 天（`lifetime_days` 1–365 可选）后主动过期；过期后下单被拒并路由到重新授权（`order_guard.py` 检查链第 2 步）。

**Paper/live 是结构性守卫，不是配置开关**：每个 broker 的 paper/live 区分由运行时可验证的结构性判据实现——账户 ID 格式、主机分离、demo 标志、交易环境等（`auth.environment_identity` 字段即该判据的标签，§5.2）。**任何没有运行时 paper/live 判据的 broker 一律封顶为 paper + read-only**（如 Longbridge/Dhan/Shoonya，其 live 下单在代码第一行即硬拒绝）；Trading 212 连 paper 下单都拒绝（完全只读）。前端不应提供任何"切换到 live"的开关——不存在这样的 API。

---

## 2. 端点清单

### 2.1 POST /mandate/commit

请求体 `CommitMandateRequest`（`live_routes.py` L41-56，字段约束为服务端强校验）：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `broker` | string | 必填，长度 1–64 | broker 键，如 `robinhood` |
| `proposal_id` | string | 必填，正则 `^mp_[0-9a-f]{32}$` | 来自 `mandate.proposal` 事件 |
| `selected_ordinal` | int | 必填，1–10 | 用户选中的 profile 序号（1 基） |
| `adjustments` | object \| null | 可空 | 仅 adjust 路径使用；只能**收紧**渲染过的限额，放宽 → 400 |
| `consent_ack` | bool | **必填且必须为 true** | 显式肯定同意；由 UI 在用户确认点击时置位；false/缺失 → 400 |
| `session_id` | string \| null | 可选 | 发起会话 id；记入 consent 记录，并作为 SSE 事件目标 |
| `account_ref` | string | 默认 `""`，≤128 | 不透明 broker 账户标识（绝非凭证） |
| `lifetime_days` | int | 默认 30，1–365 | mandate 有效期（天） |

响应 `CommitMandateResponse`（`commit_mandate()` 返回值，`commit.py` L448-454）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `mandate_id` | string | `mandate_<32hex>` |
| `consent_record_id` | string | `cr_<32hex>`，不可变同意记录 id |
| `broker` | string | 回显 |
| `expires_at` | string | UTC ISO（`Z` 后缀）= created_at + lifetime_days |
| `resolved_profile` | object | 应用 adjustments 后的完整 profile（§3.5 字段集） |

> 参考实现的 TS 类型（`api.ts` `CommitMandateResponse`）额外声明了可选的 `selected_ordinal` / `max_order_usd` / `daily_trade_cap`——这些字段用于消费 `mandate.committed` SSE 事件（§4.3）；HTTP 响应本体以上表为准，其余限额请从 `resolved_profile` 读取。

错误（均为 400，包体 `{"detail": "..."}`）：`consent_ack` 非 true；proposal 不存活（已提交/已失效/未知——proposal 是**一次性**的，提交后立即作废）；ordinal 不在 proposal 内；adjustment 放宽了某限额；解析后的 profile 超出 ceilings。提交前后端会 best-effort 重新拉取 broker 侧账户 ceilings（经 broker 的 account 工具）做二次校验，拉取失败则回退用 proposal 自带的 ceilings 快照——**不会**因 broker 读失败而阻塞提交。

副作用：成功后向 `session_id` 的会话总线 best-effort 发 `mandate.committed`（完整响应体）与 `live.action`（`{"kind":"mandate_committed","broker":...,"mandate_id":...}`）两个事件。

### 2.2 POST /live/halt

请求体 `LiveHaltRequest`：`broker`（可选，≤64；**省略 = 全局停机**）、`reason`（默认 `"user requested halt"`，≤500）、`session_id`（可选）。

响应：`{"halted": true, "broker": <string|null>, "reason": <string>, "sentinel": "<哨兵文件绝对路径>"}`。哨兵写入是原子的、幂等的（重复触发覆盖旧哨兵，记录最新元数据）；哨兵内容为 `{"tripped_at","by":"frontend","reason"}`，但**文件存在本身即停机**，内容只是归因元数据（用户/看门狗也可以直接 touch 文件触发停机）。

副作用：发 `live.halted`（响应体）与 `live.action`（`{"kind":"halt_tripped","broker":...,"reason":...}`）。

### 2.3 POST /live/resume

请求体同 `LiveHaltRequest`（`reason` 无实际用途）。响应：`{"halted": false, "broker": <string|null>, "cleared": <bool>}`——`cleared=true` 表示哨兵原本存在并被删除，`false` 表示本来就没停机。

**注意**：清除全局哨兵**不会**清除各 broker 哨兵，反之亦然——两者独立（`halt.py` `clear_halt` docstring）。若 UI 提供"全局恢复"，恢复后仍需检查各 broker 的 `halted` 字段。

副作用：发 `live.resumed`（响应体）与 `live.action`（`{"kind":"halt_cleared","broker":...,"cleared":...}`）。

### 2.4 GET /live/status

查询参数：`broker`（可选；空白 → 400；不在名册 → 404 `unknown broker: <x>`；服务端做 strip+lowercase）。

响应 `LiveStatus`（`LiveStatusResponse`）：

```json
{
  "global_halted": false,
  "brokers": [
    {
      "auth": { "...LiveBrokerAuthStatus，见 §3.1" },
      "mandate": { "...LiveMandateStatus，见 §3.3；无 mandate 时为 null" },
      "runner": { "...LiveRunnerLiveness，见 §3.4" },
      "halted": false
    }
  ]
}
```

- **名册**：`brokers[]` 覆盖两个来源的并集（排序后）——OAuth/mandate 型 live broker（`LIVE_BROKER_SERVER_KEYS = {"robinhood","ibkr"}`，`src/config/schema.py` L15）+ 从 profile 注册表动态发现的 `broker_sdk` 型 live connector（`_live_broker_sdk_connectors()`：当前为 alpaca、binance、dhan、etoro、futu、longbridge、mt5、okx、shoonya、tiger、trading212）。**名册随注册表增长，前端必须容忍新 broker 出现**，共 13 个（2026-08-14）。
- `halted`（broker 级）= 全局哨兵 **或** 该 broker 哨兵存在（`halt_flag_set(broker=key)`）；`global_halted` 仅看全局哨兵。
- 对 SDK 型 broker，响应中的权限元数据（`capabilities`/`readonly`）**只信任注册表声明**，绝不回显 connector 自报；且当 `readonly=true` 但 capabilities 含非 `.read` 项时，`readonly` 会被降级为 `null`（fail-closed，`live_routes.py` L806-824）。
- 这是纯 GET、幂等、可随时轮询——参考实现即以此作为 Runtime 页数据源（§6.4）。

### 2.5 POST /live/connectors/{profile_id}/verify?force=true

路径参数为 connector profile id（如 `longbridge-live-sdk-readonly`）。查询参数 `force`（默认 false；true 绕过缓存）。

前置校验：profile 不存在 → 404；`environment != "live"` → 400；`transport != "broker_sdk"` → 400（local_tws/remote_mcp 不支持）。

响应 `ConnectorVerifyResponse`（§3.6）：`check_connection()` 的状态级信封，**永远剥离 `config` 子字典**（可能含密钥），异常被归一化为 `status:"error"` 信封、绝不外抛原始异常。结果缓存 **15 秒**（TTL，按 profile id）；参考实现固定传 `force=true`（`api.ts` `verifyConnector`）。该端点幂等只读、不要求 mandate、不变更 broker 状态。

### 2.6 POST /live/authorize

请求体：`{"broker": "<key>"}`。仅接受 OAuth/mandate 型 broker（当前 `robinhood` / `ibkr`）：空白 → 400，未知 → 400 `unknown live broker: <x>`。

响应 `LiveAuthorizeResponse`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `broker` | string | 规范化后的 broker 键 |
| `connector_profile` | string | 推荐 profile id（优先 remote_mcp live profile：robinhood → `robinhood-live-mcp`，ibkr → `ibkr-live-official-mcp-readonly`；`service.py` `connector_profile_id_for_broker`） |
| `oauth_token_present` | bool | `live/<broker>/oauth/` 目录存在且非空 |
| `instruction` | string | 指引文案：在持有 broker 会话的设备上运行 `vibe-trading connector authorize <profile>` |
| `note` | string | 固定提示：OAuth token 就位 **且** mandate 已提交 **且** 下单工具显式启用之前，live 通道保持只读 |

**语义**：这是"发现型"引导端点——Vibe-Trading 不托管资金、不做服务端 OAuth 重定向；OAuth 走 broker 自己的设备端同意流程。IM 插件应把 `instruction` 展示给运营者，而不是尝试在插件内完成授权。

### 2.7 POST /live/runner/start · /live/runner/stop

请求体 `LiveRunnerControlRequest`：`broker`（必填）、`session_id`（可选，仅作事件目标）。

支持性门槛：`broker_supports_live_runner()` 要求存在 `environment="live"` + `transport="remote_mcp"` + capabilities 含 `runner.manage.requires_mandate` 的 profile（`service.py` L824-846）——**当前只有 robinhood 满足**；其余 broker → 400 `live runner is not supported for <x>`。

`start` 的额外门槛（按序）：已有存活任务 → 200 `{"broker","started":false,"already_running":true}`；无已提交 mandate → **409**；mandate 已过期 → **409**（提示重新授权）；kill switch 已触发（broker 级或全局）→ **409**；broker 通道未配置 → **503**（`LiveRunnerUnavailable`）；其他构造失败 → 500。成功 → `{"broker","started":true,"already_running":false}` 并发 `live.action`（`{"kind":"runner_started","broker"}`）。

`stop`：无任务/已结束 → `{"broker","stopped":false,"was_running":false}`；否则取消任务 → `{"broker","stopped":true,"was_running":true}` 并发 `live.action`（`{"kind":"runner_stopped","broker"}`）。runner 任务存活于 API 进程内（module-level task dict），进程重启即消失；其存活状态以 §3.4 的心跳契约为准。

---

## 3. 数据契约

### 3.1 LiveBrokerAuthStatus（`/live/status` 的 `auth` 块）

后端模型 `BrokerAuthState`（`live_routes.py` L95-111）；TS 类型 `LiveBrokerAuthStatus`（`api.ts` L1110-1127）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `broker` | string | broker 键（名册键，非 profile id） |
| `oauth_token_present` | bool | OAuth token 缓存目录存在且非空 |
| `is_live_broker` | bool | 是否属于 OAuth/mandate 型名册（当前仅 robinhood/ibkr 为 true） |
| `profile_id` | string \| null | 状态页选中的 live profile id（SDK 型为 `-readonly` 后缀者优先；robinhood/ibkr 无 SDK profile → null） |
| `transport` | string \| null | `broker_sdk` \| `remote_mcp` \| `local_tws`（取该 broker 第一个 live profile 的 transport） |
| `connection_state` | string \| null | 闭集，见 §5.1 |
| `configured` | bool \| null | 选中的 SDK profile 是否已配置凭证 |
| `credential_source` | string \| null | `environment` \| `runtime_file`（闭集；只是来源标签，绝无凭证内容） |
| `sdk_installed` | bool \| null | connector SDK 是否已安装 |
| `last_checked_at` | string \| null | 最近一次 verify 的 UTC ISO（`Z` 后缀，服务端规范化） |
| `environment_identity` | string \| null | paper/live 结构性判据标签，闭集见 §5.2 |
| `readonly` | bool \| null | 注册表声明的只读性（可能被降级为 null，见 §2.4） |
| `capabilities` | string[] \| null | 注册表声明的能力串（§5.4） |
| `error_code` | string \| null | 稳定、脱敏的诊断码，闭集见 §5.3 |

> TS 类型里还有 `error?: string | null`，但后端 `BrokerAuthState` **不输出** `error` 字段——人类可读错误文本只出现在 verify 信封（§3.6）。前端在状态卡片上应基于 `error_code` 做本地化诊断文案（参考 `Runtime.tsx` `connectorDiagnostic`）。
> robinhood/ibkr 没有 SDK live profile，其 `sdk_metadata` 全为 null，只有 `broker`/`oauth_token_present`/`is_live_broker`/`transport` 四个字段有值。

### 3.2 LiveMandateLimits

| 字段 | 类型 | 说明 |
|---|---|---|
| `max_order_notional_usd` | number | 单笔订单名义上限（USD） |
| `max_total_exposure_usd` | number | 总敞口上限（USD） |
| `max_leverage` | number | 杠杆上限；现金账户为 1.0 |
| `max_trades_per_day` | int | 每日下单笔数上限（UTC 日切） |
| `allowed_instruments` | string[] | 工具白名单（如 `["equity"]`） |
| `account_funding_usd` | number | 提交时快照的账户资金（USD） |

### 3.3 LiveMandateStatus（`/live/status` 的 `mandate` 块，可为 null）

后端模型 `ActiveMandateState`（`live_routes.py` L125-136）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `broker` | string | 回显 |
| `account_ref` | string | 提交时记录的不透明账户标识（可能为 `""`） |
| `created_at` | string | UTC ISO |
| `expires_at` | string | UTC ISO；**主动过期**时刻（SPEC 决策：到期即拒单并路由重授权） |
| `expires_in_seconds` | int \| null | 距过期秒数；**已过期为负数**；`expires_at` 解析失败为 null。UI 必须容忍 null |
| `expired` | bool | `expires_in_seconds <= 0` |
| `limits` | LiveMandateLimits | 见 §3.2 |

> TS 类型另有 `mandate_id?`，但当前后端模型**不输出**该字段——不要依赖它。

### 3.4 LiveRunnerLiveness（`/live/status` 的 `runner` 块）

后端模型 `RunnerLivenessState`（`live_routes.py` L139-145），数据来自心跳契约（`src/live/runtime/liveness.py`）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `broker` | string | 回显 |
| `alive` | bool | 心跳存在且距上次 tick ≤ **90 秒**（`DEFAULT_STALENESS_MS = 90_000`） |
| `last_tick` | number \| null | 上次心跳时间戳，**epoch 毫秒**（心跳文件存 ms；runner 每 tick 写一次）。从未启动为 null |
| `last_tick_age_seconds` | number \| null | 服务端计算为 `max(0, time.time() - last_tick)`；因 last_tick 是毫秒，有 tick 时该值恒为 0.0——**参考实现用客户端时钟基于 `last_tick` 自行计算 age**（`Runtime.tsx` `formatLastTick`/`normalizeEpochMs`，同时容忍秒/毫秒两种量级），建议照做 |

### 3.5 MandateProposal / MandateProfile（`mandate.proposal` 事件载荷）

由 `propose_mandate_profiles` 工具生成（`propose_mandate_tool.py` L138-160），并经 §4.2 桥接以**完整持久化记录**形式下发：

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | string | 恒为 `"mandate.proposal"` |
| `proposal_id` | string | `mp_<32hex>` |
| `session_id` | string \| null | 发起会话 |
| `intent_normalized` | string | 规范化用户意图（可为 `""`） |
| `account` | object | `{broker, type: "cash"|"margin", funded_by: "user"}`——资金由用户在 broker 侧自行设置，agent 无法移动资金 |
| `ceilings_ref` | string | ceilings 快照引用 id |
| `ceilings` | object | 完整 ceilings 快照（commit 时用它复验；前端一般不必渲染） |
| `profiles` | MandateProfile[] | 2–4 个候选（当前模板固定 3 个） |
| `funding_note` | string | 资金说明文案 |
| `halt_note` | string | kill switch 说明文案 |
| `reauth_for` | object \| 缺省 | **仅当**本提案由 mandate 违约（breach）触发时存在：`{breach_id?, limit?, attempted_value?}`；UI 应据此切换到"重新授权"视觉（参考 `MandateProposalCard.tsx` `isReauth`） |

`MandateProfile`（`propose_mandate_tool.py` L226-237；TS 类型 `api.ts` L967-978 是其子集）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ordinal` | int | 1 基序号（commit 时传 `selected_ordinal`） |
| `label` | string | 档位标签（当前模板：`稳健` / `均衡` / `激进`） |
| `universe` | string[] \| string | 具体标的列表，或结构性 universe 描述符 |
| `max_order_usd` | number | 单笔上限（USD） |
| `max_total_exposure_usd` | number | 总敞口上限（TS 类型未声明，但载荷携带） |
| `daily_trade_cap` | int | 每日笔数上限 |
| `leverage` | string \| number | 现金账户为 `"none"`，否则为倍数 |
| `instruments` | string[] | 工具白名单 |
| `flatten_on_halt` | bool | 停机时是否平仓（默认 false = 仅撤单；TS 类型未声明） |
| `notes` | string | 档位说明 |

### 3.6 ConnectorVerifyResponse（verify 端点信封）

TS 类型 `api.ts` L1129-1144 声明了 `[key: string]: unknown` 兜底——信封字段随 connector 演进，前端只应依赖下表稳定字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | string | `ok` / `error` 等（异常归一化后为 `error`） |
| `profile_id` | string | 回显 |
| `connector` / `environment` / `transport` | string | 注册表元数据 |
| `connection_state` / `configured` / `credential_source` / `sdk_installed` / `last_checked_at` / `environment_identity` / `error_code` | 同 §3.1 | verify 是这些字段的原始来源 |
| `error` | string \| 缺省 | 人类可读错误（仅 verify 信封有） |
| `config` | **永不出现** | 服务端强制剥离 |

---

## 4. SSE 事件

### 4.1 传输位置

全部 live/mandate 事件都到达**既有的会话流** `GET /sessions/{session_id}/events`——没有独立的 live 流（`live_routes.py` 模块 docstring："No new bus"；`_emit_live_event` 复用 `svc.event_bus.emit`）。连接管理、票据、去重、续传见 00 篇 §5；事件订阅清单见 `useSSE.ts` `knownTypes`（含 `mandate.proposal`、`mandate.committed`、`live.halted`、`live.resumed`、`live.action`）。

### 4.2 两条发射路径（务必都覆盖）

**路径 A：端点直接发射**。§2 各写端点在状态落盘后，向请求体的 `session_id` 对应会话 best-effort 发事件（会话不存在/发射失败被静默吞掉——通知永不阻塞状态变更）。**含义**：请求体不传 `session_id` 就不会有 SSE 通知（状态变更仍然生效）。

**路径 B：工具结果桥接**（`sessions_routes.py` L210-281、L786-791）。agent 在聊天内调用工具产生的实盘语义，由 SSE 生成器在逐条转发事件时旁路检测并补发：

- `_mandate_proposal_frame_from_tool_result`：当事件是 `tool_result`、`tool == "propose_mandate_profiles"`、`status == "ok"`，从 `preview` 文本用正则 `"proposal_id"\s*:\s*"(mp_[0-9a-f]{32})"` 提取 id，然后**从磁盘重新加载完整持久化 proposal**（`live/*/proposals/<id>.json`）作为 `mandate.proposal` 帧下发——所以下发的不是被截断的 preview，而是完整载荷。
- `_live_action_frame_from_tool_result`：当 `tool_result.preview` 含 `"live_action"` 标记键（order guard 把脱敏审计记录挂在工具结果的冻结键 `live_action` 下），用正则提取 `audit_id`（`la_<hex>`），**从 `live/audit.jsonl` 账本重新加载脱敏记录**作为 `live.action` 帧下发。

两条桥接都是"尽力而为"，任何失败只记 debug 日志、不断流。

### 4.3 事件字段表

| 事件 | 载荷 | 触发条件 |
|---|---|---|
| `mandate.proposal` | §3.5 完整 proposal | agent 工具合成提案成功（路径 B） |
| `mandate.committed` | §2.1 响应体（`mandate_id`/`consent_record_id`/`broker`/`expires_at`/`resolved_profile`） | `POST /mandate/commit` 成功（路径 A） |
| `live.halted` | §2.2 响应体（`halted`/`broker`/`reason`/`sentinel`） | `POST /live/halt` 成功 |
| `live.resumed` | §2.3 响应体（`halted`/`broker`/`cleared`） | `POST /live/resume` 成功 |
| `live.action` | 见下 | 三种来源，见下 |

`live.action` 有**两种载荷形态**：

1. **完整审计记录**（order guard 决定 / runner 自主动作，来自 `audit.py` `to_record()`）：`audit_id`（`la_<hex>`）、`ts`（UTC ISO 毫秒精度）、`session_id`、`kind`、`intent_normalized`（如 `"buy 3 NVDA @ market"`）、`mandate_snapshot_ref`、`consent_record_ref`（二者构成 mandate→consent 问责链）、`broker_request`/`broker_response`（**已脱敏**，敏感值为 `[redacted]`）、`outcome`、`gate_decision`（如 `{"allowed": true, "checked_limits": [...]}`）、`server`（broker 键）、`remote_tool`、`error`。
2. **端点简易载荷**（路径 A）：仅 `kind` + 少量字段，如 `{"kind":"halt_tripped","broker","reason"}`。

`kind` 审计枚举（`audit.py` `LiveActionKind`）：`order_placed` · `order_cancelled` · `order_rejected` · `mandate_committed` · `breach` · `halt_tripped` · `halt_cleared`；端点另发非审计 kind：`runner_started` · `runner_stopped`。`outcome` 枚举：`accepted` · `filled` · `rejected` · `error` · `blocked`。前端应按 `kind` 分派渲染，并对未知 kind 容错忽略。

> runner 的自主动作审计事件广播在 **runner 自己的会话**（`_build_live_runner` 创建 `live-runner:<broker>` 会话）的总线上——若 IM 插件只订阅用户聊天会话，runner 的逐笔动作不会出现在该流中；跨会话的权威动作记录在 `live/audit.jsonl`。

---

## 5. 枚举与状态

以下全部是源码中的**闭集**（`live_routes.py` L222-247 的 frozenset + 注册表常量），服务端对 verify 报告做闭集过滤——不在集内的值一律归一为 `null`，前端不会见到自由文本。

### 5.1 connection_state

`connected` · `error` · `not_configured` · `ready`。参考实现的渲染映射（`Runtime.tsx` `connectorState`）：`connected` → 已连接（若满足只读判据则标"只读"）；`not_configured` 或 `configured === false` → 未配置（展示缺少的环境变量清单）；`error` → 连接失败 + 重试按钮；`ready` → 待校验 + "校验连接"按钮；null → 状态不可用。

### 5.2 environment_identity

`config_declared` · `config_declared_live` · `config-declared`（历史连字符变体，与前者同义）· `header_flag+uid_pin` · `host_separated` · `read_only_no_runtime_discriminator` · `simulated_locally` · `path_separated_key_bound` · `trd_env_acc_list`。服务端先读 verify 报告的 `environment_identity`，回退读 `paper_guard` 字段，取第一个命中闭集的值。该字段回答"paper/live 是靠什么结构性判据区分的"。

### 5.3 error_code

`authentication_failed` · `broker_error` · `credentials_conflict` · `credentials_missing` · `credentials_partial` · `network_unreachable` · `sdk_missing`。参考实现把每个码映射为本地化诊断句（`Runtime.tsx` `connectorDiagnostic`）。

### 5.4 capabilities

注册表声明的能力串。只读五件套（`src/trading/types.py` `READ_CAPABILITIES`）：`account.read` · `positions.read` · `orders.read` · `quotes.read` · `history.read`。写能力：`orders.place`（paper 直下）· `orders.place.requires_mandate`（live 下单需 mandate）· `runner.manage.requires_mandate`（runner 启停，仅 robinhood-live-mcp）。eToro 扩展集另含：`orders.cancel` · `positions.close` · `orders.cancel_close` · `positions.edit` · `copy.precheck` · `copy.start` · `copy.poll` · `copy.close`（live 变体中 `orders.place` 同样替换为 `orders.place.requires_mandate`）。参考实现的"只读兼容"判定：`connection_state=="connected"` 且 `readonly===true` 且 `profile_id` 以 `-readonly` 结尾且 capabilities 全部以 `.read` 结尾（`Runtime.tsx` `isReadOnlyCompatible`）。

### 5.5 halted / global_halted 语义

- `global_halted`：全局哨兵 `<runtime_root>/live/HALT` 是否存在。
- broker 级 `halted`：全局哨兵存在 → 对**所有** broker 为 true（全局永远赢）；否则看 `<runtime_root>/live/<broker>/HALT`。无法解析的 broker 键按停机处理（fail-closed）。
- 哨兵存在即停机，与其内容无关；空文件/坏 JSON 同样有效（归因元数据不可读而已）。
- 停机对交易的实际影响见 §6.1。

### 5.6 transport

`local_tws`（本地 TWS/IB Gateway socket）· `remote_mcp`（远端 MCP，如 Robinhood Agentic Trading）· `broker_sdk`（直连 SDK）。

---

## 6. 注意事项与校验要求

### 6.1 Fail-closed 语义（安全红线）

- order guard 检查链（`order_guard.py` 模块 docstring）：无有效 mandate/schema 版本不匹配 → 拒；过期 → 拒（路由重授权）；**kill switch 触发 → 拒且不发生任何远端调用**；意图不可解析 → 拒；限额校验失败按性质分流——结构性违约（universe/instrument）→ DENY，数量性违约 → PAUSE_FOR_REAUTH（触发 `reauth_for` 新提案）。
- **停机期间撤单/降险仍可用**：`cancel_order` 被显式归类为 risk-reducing，不受 mandate 与 kill switch 阻塞，但照常写审计（`service.py` `cancel_order` docstring）；SDK 写路径经 `execute_live_action(risk_reducing=...)` 分流，risk-reducing 跳过 halt/mandate 检查、仍审计。反之，名义上像"撤单"但实际**增加**敞口的操作（如 eToro 撤销待成交的平仓单）在 live 侧 fail-closed 禁用。
- 无法定价的按数量下单（拿不到报价）→ 拒；无法解析的 broker 键 → 按停机；verify 报告与注册表 profile id 不匹配 → 不采信权限元数据。凡"不确定"一律向"不交易"方向失败。

### 6.2 consent_ack 与提交对话框

`consent_ack` 必须**且只能**由 UI 在用户显式确认动作时置 true——服务端对非 true 一律 400。参考实现的交互是两段式：profile 卡片上的"Commit"按钮只打开二次确认对话框（展示该档位的 universe/单笔/日笔数/杠杆），用户在对话框内确认才发请求（`MandateProposalCard.tsx` `ConfirmDialog` + `handleCommit`，请求体固定 `adjustments: null, consent_ack: true`）。**禁止**：自动提交、由聊天消息触发提交、记住/预填同意。"Adjust"路径是发自然语言消息回 agent 重新生成提案（新 proposal_id），不是在同一提案上放宽限额。

### 6.3 mandate 过期展示

`expires_at` 是硬过期点：过期后下单被拒、runner 无法启动。UI 应展示倒计时（参考实现以 1 秒时钟基于 `expires_at` 客户端计算，`Runtime.tsx` `formatCountdown`），并处理三种状态：`expired === true`（或倒计时 ≤ 0）→ 已过期；`expires_in_seconds` 为 null → 不显示精确倒计时、退回 `expires_at` 日期展示；负值 `expires_in_seconds` 与 `expired: true` 同义。

### 6.4 /live/status 轮询指引

Runtime 面板把 `/live/status` 当作**普通鉴权 GET 轮询**，绝不通过聊天消息获取（`api.ts` `getLiveStatus` 注释明示）。参考实现参数：轮询间隔 **15 秒**（`Runtime.tsx` `RUNTIME_POLL_INTERVAL_MS`、`LiveRuntimePanel.tsx` `LIVE_STATUS_POLL_INTERVAL_MS` 均为 15000）；展示用倒计时另以 1 秒时钟刷新；新请求发出前 abort 旧请求并做请求序号竞态保护；收到 `mandate.committed` / `live.halted` / `live.resumed` / 相关 `live.action` 事件时立即触发一次额外刷新（事件驱动 + 轮询兜底的混合模式）。404/501 视为"运行时不可用"降级展示而非报错重试。

### 6.5 Broker 名册与能力分层

与 README broker 表交叉核对（源码：`_known_live_brokers` + profile 注册表）：

| Broker | 名册来源 | 能力分层 |
|---|---|---|
| **robinhood** | OAuth/mandate（`is_live_broker=true`） | live-only（无 paper 账户）；read + 有界 live + **唯一支持 runner 启停** |
| **ibkr** | OAuth/mandate（`is_live_broker=true`） | 只读（本地 TWS/Gateway 只读 + 官方 MCP `mcp.read` 探针）；无下单工具 |
| **tiger · alpaca · okx · binance · futu · etoro · mt5** | SDK 发现 | read + paper + 有界 live（live 写经 mandate gate；eToro demo/real 按路径+密钥分离，MT5 demo⇔paper 按账户身份守卫） |
| **longbridge · dhan · shoonya** | SDK 发现 | read + paper only——无运行时 paper/live 判据，live 下单硬拒绝 |
| **trading212** | SDK 发现 | 完全只读——连 paper 下单都拒绝 |

前端含义：`is_live_broker=true` 的卡片走 OAuth/mandate 叙事（token 状态、authorize 指引、runner 开关）；SDK 型卡片走 connector 叙事（connection_state、verify 按钮、环境判据、能力清单）；参考实现正是按 `auth.transport === "broker_sdk"` 分叉成两种卡片（`Runtime.tsx`）。未配置凭证的 SDK broker 应展示缺失变量提示（参考实现内置 longbridge：`LONGBRIDGE_APP_KEY/APP_SECRET/ACCESS_TOKEN`；etoro：`ETORO_API_KEY/USER_KEY`）。

### 6.6 其他校验要点

- `proposal_id` 一次性：commit 成功即作废，重复提交 → 400 "not live"。断线重连后不要复用旧 proposal 重试，应让用户经聊天重新生成。
- verify 缓存 15 秒：密集点击无收益；需要即时结果传 `force=true`。
- `POST /live/authorize` 不执行授权，只返回指引；`oauth_token_present` 从 false 变 true 的唯一途径是运营者在宿主设备完成 CLI 授权——轮询 `/live/status` 观察该字段即可。
- 全部时间戳为 UTC ISO（`Z` 或 `+00:00`）；`last_tick` 例外为 epoch 毫秒数值。
- 本族端点的 4xx 包体均为 `{"detail": "..."}`（00 篇 §4）。

---

## 7. 参考实现映射

| 参考实现文件 | 职责 | IM 插件对应物 |
|---|---|---|
| `frontend/src/lib/api.ts` L256-296（客户端方法）+ L964-1167（类型） | `commitMandate` / `haltLive` / `resumeLive` / `getLiveStatus` / `verifyConnector`（固定 `force=true`）/ `authorizeLive` / `startLiveRunner` / `stopLiveRunner` | 逐一实现同名调用；注意 `haltLive`/`resumeLive` 透传 `session_id` 以收到 SSE 通知 |
| `frontend/src/pages/Runtime.tsx` | 独立 Runtime 页：15s 轮询 + 摘要瓷砖（global halt/broker 数/已授权/runner 数）+ OAuth 型卡片（风险态派生 `deriveRiskState`：halted → active → idle → dormant）+ SDK 型卡片（状态机、verify、缺配置提示、能力/环境/诊断三面板） | Runtime 视图层 |
| `frontend/src/components/chat/MandateProposalCard.tsx` | 提案卡片：profile 瓷砖（具体数字）、二次确认对话框、commit、committed 后折叠为活动 mandate 徽章、reauth 视觉、Adjust 走聊天消息 | mandate 提交对话框 |
| `frontend/src/components/chat/LiveRuntimePanel.tsx` | 聊天侧运行时面板：halt/resume 开关（调 `haltLive`/`resumeLive`）、15s 轮询、SSE 事件驱动的即时刷新（`handleMandateCommitted`/`handleHalted`/`handleResumed`/`handleLiveAction`） | halt/resume 开关 |
| `frontend/src/components/chat/RunnerStatus.tsx` | runner 行：`authorizeLive` 拉取 OAuth 指引、alive 状态切换 `startLiveRunner`/`stopLiveRunner` | runner 状态控件 |
| `frontend/src/pages/Agent.tsx` L1080-1131 | 五个 live/mandate 事件的 SSE 分派（提案入列、committed 匹配 proposal_id 折叠卡片、halted/resumed 通知面板、live.action 按 kind 渲染） | 事件消费分派 |
| `frontend/src/hooks/useSSE.ts` L86-96 | `knownTypes` 订阅清单（含全部五个 live 事件） | 订阅清单须同步 |

**实现顺序建议**：先做 `GET /live/status` 轮询卡片（只读、无副作用），再接 `mandate.proposal` 渲染 + `/mandate/commit` 对话框，最后接 halt/resume 与 runner 控件——前者是后者的状态前提，且与同意模型的状态机方向一致。
