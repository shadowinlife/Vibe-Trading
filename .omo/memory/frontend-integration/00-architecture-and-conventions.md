# 00 · 架构与通用约定（Architecture & Conventions）

> **文档族**: Vibe-Trading 前端集成知识库（`.omo/memory/frontend-integration/`）
> **读者**: 在 IM 插件视图层重建同类能力的前端团队
> **校对日期**: 2026-08-14 · **事实来源**: 直接引自代码，路径见各节
> **本篇职责**: 全局架构、传输协议、认证、错误约定、SSE 通用机制。各 API 族的字段级细节见索引表中的分篇。

---

## 1. 系统架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│ 视图层（可替换）                                                    │
│  ├─ React 19 SPA（参考实现，frontend/）                            │
│  └─ IM 插件视图（贵团队系统）─── 消费同一套 HTTP/SSE 面               │
└───────────────┬─────────────────────────────────────────────────┘
                │ HTTP REST（JSON） + SSE（命名事件流）
┌───────────────▼─────────────────────────────────────────────────┐
│ FastAPI 后端（agent/api_server.py，薄装配器 ~400 行）               │
│  中间件链: CORS → DNS-rebinding Host 守卫 → SPA 深链回退 → 安全响应头 │
│  路由模块: agent/src/api/*.py（runs/sessions/system/settings/     │
│            uploads/channels/qveris/swarm/live/alpha/auth/        │
│            scheduled）                                            │
├──────────────────────────────────────────────────────────────────┤
│ 服务层                                                            │
│  ├─ Session Runtime（多轮对话 + attempt 生命周期 + 工具执行）        │
│  ├─ Backtest Engines（10 引擎，产物写 run_dir/artifacts/）         │
│  ├─ Swarm DAG 执行器（多智能体团队）                                │
│  ├─ Alpha Zoo（462 因子目录 + bench/compare 任务）                 │
│  ├─ Scheduled Research Executor（默认关闭，env 开关）               │
│  ├─ Channel Runtime（16 个 IM 适配器 —— 与贵系统平行的既有通道）     │
│  └─ Live Trading Runtime（mandate/kill-switch/runner）            │
└──────────────────────────────────────────────────────────────────┘
状态存储: ~/.vibe-trading/（可用 VIBE_TRADING_HOME 迁移）
  sessions/ runs/ swarm/ uploads/ memory/ shadow_accounts/ sessions.db(FTS5)
```

**关键事实**：

- 后端暴露面分三层：**REST API**（前端/IM 可直接调用，本知识库覆盖）、**MCP Server**（70 工具，仅 Agent 客户端）、**Agent 工具注册表**（~94 工具，仅聊天内）。IM 视图层只能消费 REST 层；聊天内工具结果通过 SSE 事件间接可见（见 01 篇）。
- React SPA 是**参考实现**：`frontend/src/lib/api.ts`（1188 行）是全部 REST 数据契约的权威 TypeScript 定义，本篇与后续各篇的字段表均以其 + 后端路由源码双向核对。
- 后端已内置 16 个 IM 通道适配器（Telegram/Slack/Feishu/DingTalk/WeCom 等，`channels` 路由族），它们与贵团队系统消费同一个 session runtime——可将其行为作为协议参照。

## 2. 部署形态与 Base URL

| 形态 | 说明 | 对客户端的影响 |
|---|---|---|
| **生产单服务** | `vibe-trading serve --port 8899`（CLI 约定端口；`serve_main` 代码默认 8000、host 默认 `127.0.0.1`）。FastAPI 用 `SPAStaticFiles` 直接托管 `frontend/dist`，浏览器路由 404 回退 `index.html` | API 与 UI **同源**，参考实现用相对路径 `BASE = ""`。IM 插件请用 `http://<host>:8899` 作为 base |
| **开发双服务** | Vite dev server `:5899`，`vite.config.ts` 将 API 路径代理到 `VITE_API_URL || http://127.0.0.1:8899` | 代理路径白名单见下；新增 API 族必须进白名单否则 dev 下 404 |
| **Docker** | 容器内 8899，默认 `127.0.0.1` 发布；宿主网关回环需 `VIBE_TRADING_TRUST_DOCKER_LOOPBACK=1` | 远程访问必须配置 `API_AUTH_KEY` |

**Vite 代理白名单**（`frontend/vite.config.ts` `PROXY_PATHS`）：
`/auth` `/sessions` `/swarm/presets` `/swarm/runs` `/qveris` `/settings/llm` `/settings/data-sources` `/channels` `/mandate` `/live` `/upload` `/shadow-reports` `/scheduled-runs`，加正则特例：`^/runs/[^/]+/?$`（HTML Accept 回退 index.html）、`/runs`、`/correlation`（同回退）、`^/alpha(?:/|$)`。
注意 `/runs/{id}/code` 与 `/runs/{id}/pine` **必须**保持代理（API-only），不回退 HTML。

**OpenAPI**：`/docs` `/redoc` 在配置 `API_AUTH_KEY` 后禁用；带 Bearer 可取 `/openapi.json`（无 key 的 loopback 开发模式可直接打开 `/docs`）。

## 3. 认证模型（必须完整实现）

事实来源：`agent/src/api/security.py`、`agent/src/api/auth_routes.py`、`frontend/src/lib/apiAuth.ts`。

### 3.1 API Key（Bearer）

- 环境变量 `API_AUTH_KEY`（别名 `VIBE_TRADING_API_KEY`），请求头 `Authorization: Bearer <key>`。
- **Key-first 优先级**：一旦配置了 key，**所有**对端（含 loopback）都必须携带有效凭证；错误/缺失 → `401 {"detail": "Invalid or missing API key"}`。
- **未配置 key**：loopback 开发信任——本地客户端直接放行；非本地客户端 → `403 {"detail": "API_AUTH_KEY is required for non-local API access"}`。
- loopback 判定：客户端 IP 为回环地址，或显式信任的 Docker 宿主网关 IP（`VIBE_TRADING_TRUST_DOCKER_LOOPBACK=1`）。
- **DNS-rebinding 守卫**：本地客户端还必须携带可信 `Host` 头（`localhost` / `127.0.0.1` / `::1` / `testserver`，可用 `VIBE_TRADING_API_ALLOWED_HOSTS` 追加），否则 `403 {"detail": "Untrusted local API host"}`。
- 参考实现把 key 存在 `localStorage["vibe_trading_api_auth_key"]`（`apiAuth.ts`），每次请求经 `authHeaders()` 注入 Bearer 头。

### 3.2 SSE 一次性票据（EventSource 专用）

浏览器 `EventSource` 无法发送 `Authorization` 头，因此：

1. `POST /auth/sse-ticket`（**头部**携带 Bearer key）→ `{"ticket": "<token>"}`。
2. 打开流时以查询参数携带：`GET /sessions/{id}/events?ticket=<token>`。

票据规则（`security.py` `_mint_sse_ticket` / `_consume_sse_ticket`）：

| 规则 | 值 |
|---|---|
| TTL | **60 秒** |
| 使用次数 | **一次性**——首次查验即销毁（无论是否过期），重放必失败 |
| 长期 key | **绝不**接受查询串传入（防浏览器历史/日志/Referer 泄漏）；访问日志对 `api_key=` / `ticket=` 值做 `***REDACTED***` 脱敏 |
| 无 key 模式 | loopback 开发模式下无需票据，直连即可（参考实现 `withAuthTicket()` 无 key 时原样返回 URL） |

**IM 插件实现要点**：每次**连接/重连**都要重新 mint 一张票据（参考实现明确不缓存 URL）；非浏览器客户端（服务器侧 SSE 消费）可以直接用 Bearer 头，无需票据。

### 3.3 各端点鉴权级别

| 依赖 | 语义 | 典型端点 |
|---|---|---|
| `require_auth` | Bearer 强制（配置 key 时）| runs / sessions / swarm / alpha / scheduled / live / channels / upload |
| `require_event_stream_auth` | Bearer **或** 一次性 ticket | 所有 `*/events` SSE 流 |
| `require_local_or_auth` | 配置 key → Bearer；否则仅 loopback | `GET /settings/*` |
| `require_settings_write_auth` | 配置 key → 仅头部 Bearer（不接受查询串）；否则仅 loopback | `PUT /settings/*`、`POST /channels/*` 等写操作 |

### 3.4 CSRF / 跨站守卫

对**非安全方法**（除 GET/HEAD/OPTIONS 外）：

- `Sec-Fetch-Site: cross-site` → `403 {"detail": "Cross-site request denied"}`；
- 携带 `Origin` 头且既非 loopback origin、又与请求 host:port 不同源 → 同样 403。

IM 插件是服务端调用（无 Origin/Sec-Fetch-Site 头），不受此守卫影响；但**内嵌 WebView** 场景要注意保持同源或走 API key 路径。

### 3.5 CORS 与安全响应头

- 默认 CORS origins：`http://(localhost|127.0.0.1):(3000|5173|8000)`；`CORS_ORIGINS` 整体替换、`VIBE_TRADING_EXTRA_CORS_ORIGINS` 追加（两者都禁止 `*`，因为 `allow_credentials=True`）。
- 每个响应携带：严格 CSP（`default-src 'self'`，`style-src 'unsafe-inline'` 因 ECharts/React 内联样式，`connect-src 'self'`）、`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Permissions-Policy`、`Referrer-Policy`。不设 HSTS（预期由 TLS 终结代理负责）。`VIBE_TRADING_CSP_REPORT_ONLY=1` 可回退为 Report-Only。
- **对 IM 插件的含义**：若以 iframe 嵌入页面会被 `X-Frame-Options: DENY` 拦截——请走纯 API 渲染或让运维调整部署层。

## 4. 错误与响应约定

| 约定 | 细节 |
|---|---|
| 错误包体 | FastAPI `HTTPException` → `{"detail": "..."}`；个别路由用 `{"message": "..."}`。参考实现读取顺序：`body.detail \|\| body.message \|\| "HTTP <status>"`（`api.ts` `errorFromResponse`） |
| 401/403 | 参考实现统一映射为本地化"需要 API key"提示（`isAuthRequiredError` 判 `status ∈ {401, 403}`） |
| Content-Type 校验 | 参考实现强制校验响应 `content-type` 含 `application/json`，否则抛错并附 80 字符预览——防反向代理返回 HTML 错误页被静默 `JSON.parse` |
| 空响应 | 空 body → `{}`；`DELETE /scheduled-runs/{id}` 返回真正的空 204 |
| 路径参数校验 | run/session/swarm ID 经 `_SAFE_PATH_PARAM_RE` 校验，含换行等畸形字符直接拒绝（安全加固 #80）。客户端应对 ID 做 `encodeURIComponent` |
| 时间戳 | 会话/API/目标/通道各路径统一输出**时区感知的 UTC ISO** 字符串（如 `2026-08-14T03:00:00+00:00`）；scheduled jobs 的 `next_run_at`/`created_at` 是 **epoch 数值** |
| 数值 JSON | 严格 JSON：后端在写 `validation.json`、swarm 工具结果等位置把 `NaN`/`Infinity` 归一为 `null`。客户端仍应对数值字段做 `Number.isFinite` 防御（见 09 篇） |

## 5. SSE 传输通用机制

事实来源：`frontend/src/hooks/useSSE.ts`（参考实现的完整消费端）+ 各路由模块的事件发射端。

### 5.1 事件类型全集（参考实现订阅清单）

```
聊天流:     text_delta, reasoning_delta, stream_reset, thinking_done,
           tool_call, tool_result, compact, tool_heartbeat, tool_progress, llm_usage
Swarm 桥接: swarm.started, swarm.event
Attempt:   attempt.created, attempt.started, attempt.completed,
           attempt.failed, attempt.cancelled
会话:       message.received, session.created
研究目标:   goal.created, goal.evidence, goal.updated
实盘:       mandate.proposal, mandate.committed, live.halted, live.resumed, live.action
传输控制:   heartbeat, done
```

> 各事件的 payload 字段级定义见 **01 篇（聊天/attempt/goal/tool）** 与 **07 篇（live/mandate）**。未列入的自定义事件名会被参考实现忽略——后端新增事件类型时，前端必须同步扩展订阅清单（`useSSE.ts` `knownTypes`），这是 IM 插件同样要做的维护点。

### 5.2 连接管理契约

| 机制 | 参考实现行为 | IM 插件要求 |
|---|---|---|
| 重连 | 指数退避 1s → 30s（×2），`onerror` 即关闭重连 | 必须实现；后端不保证长连接永不断 |
| 断点续传 | 记录每个事件的 `lastEventId`，重连时以**查询参数** `?Last-Event-ID=<id>` 请求回放（注意：不是 HTTP 头） | 需要实现才能真正"不丢事件"；`?replay=active` 另见 01 篇 |
| 去重 | 按事件 ID 维护 500 容量 LRU 集合，重复 ID 丢弃 | 回放+重连场景下事件可能重复投递，**消费端必须幂等/去重** |
| 心跳 | `heartbeat` 事件保活；心跳不打断回放 | 不要把 heartbeat 当业务事件渲染 |
| 票据 | 每次连接现 mint（一次性） | 见 §3.2 |
| 状态机 | `disconnected → connected → reconnecting` | 建议 UI 暴露连接状态 |

### 5.3 事件 payload 通用形态

- 所有事件 `data:` 为 **JSON 对象**；解析失败时参考实现降级为 `{ raw: <原文> }`（防御后端非标输出）。
- 事件 ID 由后端分配（单调），是去重与续传的基础。

## 6. 其它全局约定

| 主题 | 约定 |
|---|---|
| Shell 工具 | HTTP/SSE 面**默认不暴露** `bash`/`background_run` 等 shell 工具（需服务端 `VIBE_TRADING_ENABLE_SHELL_TOOLS=1` 显式开启）。IM 插件不应期待聊天中出现 shell 类工具事件 |
| 上传根 | 文档/日志类读取受 `~/.vibe-trading/uploads`、`~/.vibe-trading/runs` 等白名单根约束（`VIBE_TRADING_ALLOWED_FILE_ROOTS` 可扩展），详见 08 篇 |
| 生成代码沙箱 | 回测生成代码在受限 env 下端子进程运行（不含 LLM key/经纪商密钥）；其产物经 run_dir 落盘后经 `/runs` 族暴露 |
| 调度执行器 | 定时研究**默认不执行**：仅 `VIBE_TRADING_ENABLE_SCHEDULER=1` 启动的服务器才会触发 job；API 始终可以创建/列出/删除 job（06 篇） |
| 前端图表约束 | 参考实现 `frontend/src/lib/echarts.ts` 仅注册 Candlestick/Line/Bar/Heatmap 四种 series——这是**参考实现的限制**，不是协议限制；IM 视图层可自由选择渲染技术 |
| 语言 | 后端消息/错误为英文或模型输出语言；参考实现 UI 文案走 i18next（6 语言） |

## 7. 本知识库索引

| 文档 | 覆盖 API 族 | 主要端点 |
|---|---|---|
| [01-sessions-chat-sse.md](./01-sessions-chat-sse.md) | 会话 / 消息 / 研究目标 / **聊天 SSE 事件协议** | `/sessions*`、`/sessions/{id}/events` |
| [02-runs-backtest-artifacts.md](./02-runs-backtest-artifacts.md) | 回测运行 / 产物 / 指标语义 | `/runs*` |
| [03-alpha-zoo.md](./03-alpha-zoo.md) | 因子目录 / Bench / Compare（含 SSE 进度流） | `/alpha/*` |
| [04-swarm.md](./04-swarm.md) | 多智能体团队预设 / 运行 / SSE 流 | `/swarm/*` |
| [05-correlation-regime.md](./05-correlation-regime.md) | 滚动相关矩阵 / 机制时间线 | `/correlation`、`/correlation/regime` |
| [06-scheduled-research.md](./06-scheduled-research.md) | 定时研究任务 / Playbook 模板 | `/scheduled-runs*` |
| [07-live-trading-runtime.md](./07-live-trading-runtime.md) | 实盘授权 / Mandate / Kill-switch / Runner / live SSE 事件 | `/live/*`、`/mandate/*` |
| [08-settings-channels-uploads.md](./08-settings-channels-uploads.md) | 设置 / IM 通道 / 上传 / Shadow 报告 / 系统 | `/settings/*`、`/channels/*`、`/upload`、`/shadow-reports/*`、`/health` |
| [09-enums-validation-pitfalls.md](./09-enums-validation-pitfalls.md) | 全量枚举参考 + 数值校验 + 常见陷阱 | （横切） |

**参考实现关键文件**（协议消费侧的权威参照）：

| 文件 | 职责 |
|---|---|
| `frontend/src/lib/api.ts` | 全部 REST 客户端 + TypeScript 数据契约 |
| `frontend/src/lib/apiAuth.ts` | key 存储 / Bearer 头 / SSE 票据交换 |
| `frontend/src/hooks/useSSE.ts` | SSE 连接管理（重连/去重/续传/事件订阅清单） |
| `frontend/src/lib/formatters.ts` | 15 个回测指标的展示标签与阈值 |
| `frontend/src/lib/runReports.ts` | "报告级运行"判定逻辑 |
| `frontend/src/lib/echarts.ts` | 图表库 tree-shaken 注册（仅 4 种 series） |
