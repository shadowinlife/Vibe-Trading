# 08 · 设置 / IM 通道 / 上传 / 系统（Settings / Channels / Uploads / System）

> **文档族**: Vibe-Trading 前端集成知识库（`.omo/memory/frontend-integration/`）
> **读者**: 在 IM 插件视图层重建同类能力的前端团队
> **校对日期**: 2026-08-14 · **事实来源**: 直接引自代码，路径见各节
> **本篇职责**: `/settings/*`（LLM 与数据源）、`/channels/*`（16 个 IM 适配器运行时）、`/upload`、`/shadow-reports/*`、系统端点（`/health` `/system/shutdown` `/skills` `/api`）与 `/qveris/*` 概览。认证模型、CSRF 守卫、错误包体约定见 [00-architecture-and-conventions.md](./00-architecture-and-conventions.md)（本篇以 §3.3/§3.4/§4 为准，不再重复）。

---

## 1. 端点总览

事实来源：`agent/src/api/settings_routes.py`、`agent/src/api/channels_routes.py`、`agent/src/api/uploads_routes.py`、`agent/src/api/system_routes.py`、`agent/src/api/qveris_routes.py`。

| 方法 | 路径 | 鉴权级别 |
|---|---|---|
| `GET` | `/settings/llm` | `require_local_or_auth` |
| `PUT` | `/settings/llm` | `require_settings_write_auth` |
| `POST` | `/settings/llm/models` | `require_settings_write_auth` |
| `GET` | `/settings/data-sources` | `require_local_or_auth` |
| `PUT` | `/settings/data-sources` | `require_settings_write_auth` |
| `GET` | `/channels/status` | `require_auth` |
| `POST` | `/channels/start` · `/channels/stop` · `/channels/pairing/command` | `require_auth` |
| `POST` | `/upload` | `require_auth` |
| `GET` | `/shadow-reports/{shadow_id}` | `require_auth` |
| `GET` | `/health` · `/live` · `/ready` · `/api` | 无鉴权 |
| `POST` | `/system/shutdown` | 专用 shutdown 授权 + 仅 loopback |
| `GET` | `/skills` | `require_auth` |
| `GET` | `/qveris/config` · `/qveris/status` | `require_auth` |
| `PUT` | `/qveris/config` | `require_settings_write_auth` |

两级设置鉴权的精确语义（`agent/src/api/security.py`，对照 00 篇 §3.3）：

- `require_local_or_auth`：配置了 `API_AUTH_KEY` → 走 Bearer；未配置 → **仅 loopback 客户端**，否则 `403 Settings access requires API_AUTH_KEY or a local loopback client`。
- `require_settings_write_auth`：配置了 key → **仅接受头部 Bearer**（`allow_query=False`，查询串一律拒绝）；未配置 → 仅 loopback，否则 `403 Settings writes require API_AUTH_KEY or a local loopback client`。

## 2. LLM 设置（/settings/llm）

### 2.1 GET /settings/llm → LLMSettingsResponse

后端模型 `LLMSettingsResponse`（`settings_routes.py`）与前端 `LLMSettings`（`frontend/src/lib/api.ts`）逐字段对应：

| 字段 | 类型 | 说明 |
|---|---|---|
| `provider` | string | 当前 provider 名（`LANGCHAIN_PROVIDER` 小写化；未识别时回退 `openai` 元数据） |
| `model_name` | string | `LANGCHAIN_MODEL_NAME`，缺省取 provider 的 `default_model` |
| `base_url` | string | 取 provider 的 `base_url_env` 对应值，缺省 `default_base_url` |
| `api_key_env` | string \| null | 该 provider 的 key 环境变量名（Ollama 等免 key provider 为 null） |
| `api_key_configured` | boolean | key 是否已配置（占位符如 `sk-xxx` 视为未配置）。OAuth provider（openai-codex）改为反映**本地登录 token 是否存在** |
| `api_key_hint` | string \| null | 当前构建逻辑恒为 `null`（保留字段） |
| `api_key_required` | boolean | provider 元数据 |
| `temperature` | number | `LANGCHAIN_TEMPERATURE`，默认 0.0 |
| `timeout_seconds` | int | `TIMEOUT_SECONDS`，默认 120 |
| `max_retries` | int | `MAX_RETRIES`，默认 2 |
| `reasoning_effort` | string | `LANGCHAIN_REASONING_EFFORT` 小写；合法集合 `{"", "none", "low", "medium", "high", "max"}`（空串 = 不下发该字段） |
| `sse_timeout_seconds` | int | `VIBE_TRADING_SSE_TIMEOUT`，默认 90 |
| `env_path` | string | **项目相对路径**形式的配置文件位置（读取永不创建文件） |
| `providers` | `LLMProviderOption[]` | 全量 provider 目录，见下 |

`LLMProviderOption`：`name, label, api_key_env?, base_url_env, default_model, default_base_url, base_url_options[], api_key_required, auth_type（默认 "api_key"，OAuth provider 为 "oauth"）, login_command?`。元数据来自数据文件 `agent/src/providers/llm_providers.json`（启动时加载，重名校验，空文件/重名直接启动失败）。

当前目录共 **23 个 provider**（`name` → key 环境变量 / 默认模型，节选关键项）：

| name | auth | api_key_env | default_model |
|---|---|---|---|
| `openrouter` | api_key | `OPENROUTER_API_KEY` | `deepseek/deepseek-v4-pro` |
| `requesty` | api_key | `REQUESTY_API_KEY` | `openai/gpt-4o-mini` |
| `openai` | api_key | `OPENAI_API_KEY` | `gpt-5.5` |
| `anthropic` | api_key | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| `openai-codex` | **oauth** | 无（登录 token） | `openai-codex/gpt-5.4` |
| `deepseek` | api_key | `DEEPSEEK_API_KEY` | `deepseek-v4-pro` |
| `siliconflow-cn` / `siliconflow-global` | api_key | `SILICONFLOW_API_KEY` / `SILICONFLOW_GLOBAL_API_KEY` | `deepseek-ai/DeepSeek-V3.1-Terminus` |
| `nvidia` | api_key | `NVIDIA_API_KEY` | `nvidia/nemotron-3-ultra-550b-a55b` |
| `gemini` | api_key | `GEMINI_API_KEY` | `gemini-3.5-flash` |
| `groq` | api_key | `GROQ_API_KEY` | `meta-llama/llama-4-maverick-17b-128e-instruct` |
| `dashscope` / `qwen`（别名同 key） | api_key | `DASHSCOPE_API_KEY` | `qwen-plus-latest` |
| `zhipu` / `glm`（别名同 key） | api_key | `ZHIPU_API_KEY` | `glm-5.1` |
| `moonshot` | api_key | `MOONSHOT_API_KEY` | `kimi-k2.6` |
| `kimi-coding` | api_key | `KIMI_CODING_API_KEY` | `kimi-for-coding` |
| `minimax` | api_key | `MINIMAX_API_KEY` | `MiniMax-M3` |
| `mimo` | api_key | `MIMO_API_KEY` | `MiMo-72B-A27B` |
| `spark` | api_key | `SPARK_API_KEY` | `4.0Ultra` |
| `zai` | api_key | `ZAI_API_KEY` | `glm-5.1` |
| `modelscope` | api_key | `MODELSCOPE_API_KEY` | `Qwen/Qwen3.5-27B` |
| `ollama` | **免 key**（`api_key_env=null`，`api_key_required=false`） | — | `qwen2.5:32b` |

UI 不应硬编码此表——以 `GET /settings/llm` 返回的 `providers[]` 为准（数据驱动，随版本增长）。

> **安全契约**：响应**绝不回显原始 key**——只有 `api_key_configured` 布尔与（保留的）hint。任何要求"读回 key"的 UI 设计都不可实现。

### 2.2 PUT /settings/llm（UpdateLLMSettingsRequest）

| 字段 | 约束 |
|---|---|
| `provider` | 必填；不在目录中 → `400 Unsupported LLM provider` |
| `model_name` | 必填非空（`400 Model name is required`） |
| `base_url` | 可选；缺省用 provider 默认；OAuth provider 走专用 URL 校验 |
| `api_key` | 可选；提供则写入（占位符值会被清空为 ""）；未提供时保留现有已配置 key |
| `clear_api_key` | bool，默认 false；true → 清空该 provider 的 key |
| `temperature` | `0–2`，越界 `400 Temperature must be between 0 and 2` |
| `timeout_seconds` | `1–3600`（Pydantic 边界，违反 422） |
| `max_retries` | `0–20` |
| `reasoning_effort` | 可选；必须在 §2.1 合法集合内，否则 `400` |

副作用：写入**规范用户配置** `~/.vibe-trading/.env`（遗留 `agent/.env` 存在时一次性迁移合并），并**同步当前进程运行时 env**（`_sync_runtime_env`：同步 `OPENAI_API_KEY`/`OPENAI_BASE_URL` 等兼容变量 + 重置 env 配置缓存）。写文件失败（权限等）→ `503 Unable to save settings; check ownership and permissions for ~/.vibe-trading/.env`。成功返回更新后的完整 `LLMSettings`。

桌面壳特殊形态：`VIBE_TRADING_DESKTOP_SECURE_CREDENTIALS=1` 时密钥由 Electron safeStorage 托管，dotenv 中对应键被写空——IM 视图层不涉及，但看到"已配置却读不到 dotenv 值"时不要误判。

### 2.3 POST /settings/llm/models（在线模型发现）

请求 `ListLLMModelsRequest`：`provider`（必填）、`base_url?`、`api_key?`。**不持久化**任何凭证。

响应 `LLMModelsResponse`：

| 字段 | 说明 |
|---|---|
| `provider` | 回显 |
| `models` | 模型 id 列表：case-insensitive 排序，**上限 1000**；provider 的 `default_model` 保证插在首位（即使发现列表里没有） |
| `source` | `"provider"`（真实发现成功且非空）或 `"default"`（回退，仅含 default_model） |
| `warning_code` | `oauth_discovery_unsupported`（OAuth provider 不支持在线发现）/ `api_key_required`（需要 key 但未提供）/ `model_list_unavailable`（HTTP/解析失败）/ `null`（成功） |

行为细节（`_list_provider_models`）：发现请求打 `<base_url>/models`（自动剥离 `/chat/completions`、`/responses` 后缀），12 秒超时、**不跟随重定向**；Ollama 自动补 `/v1`。**已保存 key 的复用受信任 URL 白名单约束**（保存的/默认的/`base_url_options` 里的 base_url 才允许带上存量 key）——向任意自填 URL 探测时不会泄漏存量凭证。

## 3. 数据源设置（/settings/data-sources）

### 3.1 GET → DataSourceSettingsResponse

| 字段 | 说明 |
|---|---|
| `tushare_token_configured` | bool；占位符（空串、`your-tushare-token`）视为未配置 |
| `tushare_token_hint` | 当前构建逻辑恒为 `null`（保留字段） |
| `baostock_supported` | 项目内是否存在 baostock loader 实现 |
| `baostock_installed` | `baostock` 包是否可导入 |
| `baostock_message` | 三态文案：loader 可用 / 包装了但无 loader / 均未注册 |
| `env_path` | 项目相对路径 |

### 3.2 PUT（UpdateDataSourceSettingsRequest）

字段：`tushare_token?`、`clear_tushare_token?`（true → 清空）。优先级：clear > 新值 > 保留现值。成功后同步进程 env 的 `TUSHARE_TOKEN` 并重置配置缓存；返回更新后的完整响应。**原始 token 同样从不回显。**

## 4. IM 通道（/channels/*）

后端已内置 **16 个 IM 适配器**——它们与贵团队系统平行、消费同一个 session runtime，可作协议参照（00 篇 §1）。

### 4.1 16 个适配器名与安装提示

事实来源：`agent/src/channels/registry.py` `_INSTALL_HINTS`（键即全部 16 个适配器名）：

`websocket` `telegram` `slack` `discord` `matrix` `whatsapp` `signal` `qq` `napcat` `weixin` `wecom` `feishu` `dingtalk` `msteams` `email` `mochat`

`install_hint` 按适配器给出（如 telegram → `pip install 'vibe-trading-ai[telegram]'`；weixin/email/signal 为"无需额外 Python 包，配置 channels.<name> 即可"；websocket → `[channels]` 全家桶）。未列出的外部插件名回退为 `pip install 'vibe-trading-ai[<name>]'`。

### 4.2 GET /channels/status → ChannelRuntimeStatus

运行时层（`agent/src/channels/runtime.py` `status()`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `running` | boolean | 运行时消费循环是否活跃 |
| `inbound_queue` | number | 入站消息队列深度 |
| `outbound_queue` | number | 出站消息队列深度 |
| `session_count` | number | 通道→会话映射条目数 |
| `channels` | `Record<string, ChannelAdapterStatus>` | 每适配器状态（管理器未初始化时为 `{}`） |

`ChannelAdapterStatus`（`registry.py` `inspect_channels` + `manager.py` `get_status` 合成；前端类型见 `api.ts`）：

| 字段 | 说明 |
|---|---|
| `name` | 适配器名（= map 键） |
| `display_name` | 展示名（如 `WeChat` `Napcat (QQ)` `Microsoft Teams`） |
| `configured` | 通道配置里存在该节 |
| `enabled` | 该节 `enabled: true` |
| `available` | 依赖可导入（可选 SDK 缺失 → false） |
| `loaded` | 已成功实例化 |
| `running` | 适配器当前运行中 |
| `error` | 失败原因（如 `missing optional dependency for telegram`；正常时为空串/省略） |
| `install_hint` | 恢复建议（见 §4.1） |

参考实现（`Settings.tsx` IM Channels 面板）把 `enabled/loaded/running` 渲染为状态列，`install_hint || error` 渲染为恢复提示列；不可用的适配器**不隐藏**，靠 hint 引导修复。

### 4.3 启停与配对

| 端点 | 行为 |
|---|---|
| `POST /channels/start` | 启动运行时（`start_manager=True`），返回 `{"status": "started", ...ChannelRuntimeStatus}` |
| `POST /channels/stop` | 停止全部适配器与消费循环，返回 `{"status": "stopped", ...ChannelRuntimeStatus}` |
| `POST /channels/pairing/command` | 请求体 `{channel: string, command: string}` → `{channel, reply: string}`；对共享配对存储执行 `list` / `approve <code>` / `deny <code>` / `revoke ...` 子命令（`agent/src/channels/pairing/store.py` `handle_pairing_command`） |

**operator 门控的准确边界**：REST 的 `/channels/pairing/command` 经 `require_auth` 鉴权后以**全局 operator 权限**执行（`is_global_operator=True` 默认值，跨通道可见/可批/可吊销）。而**聊天内**的 `/pairing` 命令走运行时门控：发送者必须在 `channels.operators`（全局）或该通道节的 `operators` 列表中，否则被拒并回复 `Not authorized: pairing management is restricted to configured operators.`；**未配置任何 operator 时聊天内 `/pairing` 失败关闭**，配对只能走 CLI 与本 REST 端点（`runtime.py` `_handle_inbound`、README channels 节）。

**聊天内命令全集**（16 个适配器通用，`runtime.py`）：

| 命令 | 行为 | 门控 |
|---|---|---|
| `/new` · `/reset` · `/newsession` | 重置当前会话——下一条消息开启新对话 | 无（任何允许的发件人） |
| `/pairing list` 等 | 配对管理子命令 | 仅 operator（见上） |

命令**大小写不敏感**且必须**独占整条消息**（`content.strip().lower()` 全等匹配；`hello /new` 是普通消息不是重置）。IM 视图层若复用同一运行时，注意这些命令会被运行时先行拦截，不会进入 agent。

通道配置位于 `~/.vibe-trading/agent.json` 的 `channels` 节（每适配器 `enabled` + 凭证；全局 `replyTimeoutS` 控制助手回复等待预算，默认 600 秒）。SDK 类适配器是可选 extra（§4.1 的 install_hint），缺 SDK 时运行时**不崩溃**，只在 status 里报 `available=false` + hint。

## 5. 上传（POST /upload）

事实来源：`agent/src/api/uploads_routes.py`。multipart 表单，字段名 **`file`**（参考实现 `uploadFile` 用 `FormData.append("file", file)`，不带 Content-Type 头，由浏览器生成 boundary）。

### 5.1 限制与黑名单（源码常量）

| 常量 | 值 |
|---|---|
| `MAX_UPLOAD_SIZE` | **50 MB**（`50 * 1024 * 1024`）；以 **1 MB** 块流式读取（`_UPLOAD_CHUNK_SIZE`），累计超限立即中止、删除临时文件并返回 `413 File too large (limit 50 MB)` |
| `_BLOCKED_UPLOAD_EXT` | 可执行/二进制：`.exe .msi .bat .cmd .com .scr .app .dmg .so .dll .dylib`；可执行邻近的源码/脚本/配置/模板：`.py .pyw .sh .bash .zsh .fish .ps1 .yaml .yml .j2 .jinja .jinja2 .template`；归档（不自动解包）：`.zip .rar .7z .tar .gz .tgz .bz2 .xz` |
| `_BLOCKED_UPLOAD_NAMES` | `dockerfile`、`containerfile`（按小写文件名匹配） |

命中黑名单 → `400 This file type is not allowed for upload.`；缺文件名 → `400 Missing filename`；落盘 IO 失败 → `500`（附重试提示）。

**白名单侧的意图**（路由 docstring）：接受 PDF、Word、Excel、PowerPoint、图片、CSV/TSV、纯文本、JSON、TOML 等常见文档/数据格式；可执行文件、可执行邻近的源码/配置/模板文件、归档一律拒绝。注意 `.yaml/.yml/.j2` 等被拒是**有意为之**（防模板注入面），不是遗漏——IM 视图层不要在前端"修复"这些限制。

扩展名判定取 `Path(filename).suffix.lower()`（最后一个点之后），文件名判定取**完整文件名小写**（`dockerfile`/`containerfile` 无论扩展名）。

### 5.2 成功响应（UploadResult）

```json
{ "status": "ok", "file_path": "uploads/<uuid4hex>.<ext>", "filename": "原始文件名.csv" }
```

- 落盘文件名为 **uuid 化安全名**（保留原扩展名），目录为上传根 `~/.vibe-trading/uploads`（`get_uploads_dir()` = 运行时根 `/uploads`，可经 `VIBE_TRADING_HOME` 迁移）。
- `file_path` 是**相对运行时根的相对路径**，后续聊天中以该路径引用文件。
- 文档/日志类**读取**另受白名单根约束（`~/.vibe-trading/uploads`、`~/.vibe-trading/runs`、`./uploads`、`./data` 及 `VIBE_TRADING_ALLOWED_FILE_ROOTS` 追加项；`agent/src/tools/path_utils.py`）——上传成功不等于任意路径可读。

## 6. Shadow 报告（GET /shadow-reports/{shadow_id}）

`uploads_routes.py` 中唯一存在的 shadow-reports 端点：

- 路径参数必须匹配 `^shadow_[0-9a-f]{8}$`，否则 `400 invalid shadow_id`。
- 查询参数 `format`：`html`（默认）或 `pdf`，其它 → `400 format must be html or pdf`。
- 文件位置固定：`~/.vibe-trading/shadow_reports/<shadow_id>.<format>`；不存在 → `404 Shadow report not found: <id>.<format>`。
- 成功以 `FileResponse` 内联返回（`Content-Disposition: inline`），media type 为 `text/html; charset=utf-8` 或 `application/pdf`——IM 视图层可直接内嵌预览或转存。

## 7. 系统端点（system_routes.py）

| 端点 | 说明 |
|---|---|
| `GET /live` | 存活探针：无条件 200（只要进程能响应）。载荷 `{status: "healthy", service: "Vibe-Trading API", timestamp: <UTC ISO>}` |
| `GET /health` | `/live` 的向后兼容别名，载荷相同 |
| `GET /ready` | 就绪探针：检查 LLM provider/model/凭证**配置**（刻意不触网、零 LLM 成本）。就绪 → 200 `{status: "ready", ...}`；未配置 → `503` + 非敏感原因（如 `LLM provider not configured`、`provider OAuth login not found`、`LLM provider credential not configured`） |
| `POST /system/shutdown` | 关停 API 进程。双重门：①专用 shutdown 授权（配置 key → 仅头部 Bearer；未配置 → 仅 loopback，`security.py` `_require_shutdown_authorization`）；②客户端 IP 必须是 `127.0.0.1`/`::1`/`localhost`，否则 `403 Local access only`。成功返回 `{status: "shutting-down", ...}`，响应发出后约 0.25s 进程自杀（SIGTERM）。**远程 IM 插件永远不应调用此端点** |
| `GET /skills` | `require_auth`。返回 `[{name, description}]` 技能清单（技能清单属于能力侦察面，故鉴权） |
| `GET /api` | 无鉴权服务元数据：`{service, version, docs: "/docs", health: "/health"}` |
| `GET /openapi.json` | `require_auth`（schema 枚举全部路由含实盘控制面，未授权不得读取） |
| `GET /docs` · `/redoc` | 仅**未配置 key 的 loopback 开发模式**可用；配置 key 后一律 `404`（00 篇 §2 已述） |

## 8. QVeris 概览（qveris_routes.py）

QVeris 是可选付费数据市场（63+ provider 一个 key），免费路由保持默认；详细数据语义见 qveris skill，此处只给 REST 契约。`qveris_router` 无路径前缀挂载于 app（`api_server.py` `app.include_router(qveris_router)`）。

| 端点 | 鉴权 | 响应模型 |
|---|---|---|
| `GET /qveris/config` | `require_auth`（委托宿主） | `QVerisConfigResponse`：`enabled, base_url, api_key_masked（如 "abcd…xyz"，≤7 字符为 "***"）, mode（"free"\|"paid"）, budget_credits_per_session, configured（是否有凭证）, signup_url, invite_code` |
| `PUT /qveris/config` | `require_settings_write_auth` | 更新体 `QVerisConfigUpdate`：`enabled?, base_url?（必须 http(s)，否则 422）, api_key?, mode?（"free"\|"paid"）, budget_credits_per_session?（ge=0）`。**mode 与 enabled 联动**：改 `mode=paid` 自动 `enabled=true`；改 `enabled` 则 mode 随之 free/paid。返回更新后的 config 响应 |
| `GET /qveris/status` | `require_auth` | `QVerisStatusResponse`：`enabled, ok, error（可 null）, remaining_credits（可 null）, recent[]（最近 ≤10 条用量：ts/tool_id/cost/charge_outcome）, signup_url, invite_code` |

**付费模式门控**（`agent/src/tools/qveris_tool.py`）：`is_qveris_configured = 存在 api_key 且 mode == "paid"`。无凭证时 status 返回 `ok=false, error="QVeris is not configured"`；有凭证但 mode=free 时返回 `ok=false, error="QVeris paid mode is off; free public data routing is active."`。status 端点**永不抛错**——上游异常被捕获并以 `ok=false + error=<异常文本>` 返回。

key 脱敏规则（`mask_api_key`）：长度 ≤7 → `***`；否则 `前4位…后3位`（如 `abcd…xyz`），不可逆。`budget_credits_per_session` 是每会话 credit 预算（`ge=0`），供执行层限额——与 00 篇的付费数据路由说明配套。

## 9. 补充细节（探针与关停的边界行为）

- `/ready` 的就绪判定**刻意不触网**（不做 base-URL ping、零 LLM 成本），只镜像 preflight 的配置校验部分：provider 非空、model 非空、可推导出凭证；OAuth provider（openai-codex）检查本地登录 token。探针高频命中也安全。
- `/system/shutdown` 成功响应示例：`{"status":"shutting-down","service":"Vibe-Trading API","timestamp":"<UTC ISO>"}`；响应发出约 0.25 秒后进程收到 SIGTERM。客户端 IP 白名单是硬编码三元组 `{127.0.0.1, ::1, localhost}`——**Docker 宿主网关即便被信任为 loopback 也过不了这一关**，shutdown 只能来自容器/本机内部。
- `/skills` 返回的是 `{name, description}` 扁平数组（无分类、无正文）；技能正文不在 REST 面暴露。

## 10. 参考实现消费方式（`frontend/src/pages/Settings.tsx`）

- 首屏 `Promise.allSettled` 并行拉 `getLLMSettings` + `getDataSourceSettings` + `getChannelStatus`，**逐路降级**（单路失败只 toast 该路，不阻塞其余面板）。
- 401/403 统一映射为本地化"需要 API key"提示（`isAuthRequiredError`，00 篇 §4）。
- 模型下拉：编辑 provider/base_url 后调用 `POST /settings/llm/models` 拉取候选，`warning_code` 映射为三种提示文案；`source="default"` 时提示列表为回退值。
- key/token 输入框默认留空 + "已配置"徽标；显式勾选"清除"才发送 `clear_api_key`/`clear_tushare_token`——与后端"未提供则保留现值"的语义配套。
- IM 面板：手动 Refresh + Start/Stop 按钮；`ChannelRuntimeActionResponse`（= ChannelRuntimeStatus + `status` 字段）回写本地状态。
- QVeris 面板为独立组件（`QVerisSettings`），消费 §8 三端点。

**IM 视图层注意**：设置写操作会**即时改变后端进程行为**（provider 切换、凭证生效），且无二次确认 API——请在贵层自行实现确认与权限收敛。`POST /system/shutdown` 与 `PUT /qveris/config` 属高危面，建议仅对运营者角色开放。

典型调用示例：

```
PUT /settings/llm            （require_settings_write_auth，仅头部 Bearer）
{"provider":"deepseek","model_name":"deepseek-v4-pro",
 "base_url":"https://api.deepseek.com","api_key":"***",
 "temperature":0.0,"timeout_seconds":120,"max_retries":2}
→ 200 完整 LLMSettings（api_key 不回显，仅 api_key_configured=true）

POST /upload                 （multipart，字段名 file）
→ 200 {"status":"ok","file_path":"uploads/<uuid>.pdf","filename":"report.pdf"}

GET /health → 200 {"status":"healthy","service":"Vibe-Trading API",
                   "timestamp":"2026-08-14T03:00:00+00:00"}
```

## 11. 集成核对清单

- [ ] 读设置（GET）与写设置（PUT/POST models）使用**不同鉴权级别**（§1 表）；写操作绝不接受查询串 key。
- [ ] 任何设置响应中都**没有原始密钥**——只消费 `*_configured` 布尔；"修改 key"语义 = 未提供则保留、提供则覆盖、`clear_*=true` 则清空。
- [ ] `api_key_hint` 与 `tushare_token_hint` 当前恒为 `null`，不要依赖其内容。
- [ ] 模型发现是"尽力而为"：`source="default"` 或 `warning_code` 非空时，列表只是回退候选，UI 应允许手填。
- [ ] 通道状态里 `available=false` 的适配器携带 `install_hint`——渲染为可操作的修复指引而非错误。
- [ ] `/channels/start|stop` 的响应 = 运行时全量状态 + `status` 字段，可直接回写本地视图。
- [ ] 上传按 50 MB 与黑名单在客户端预检，避免大文件传输到一半被 413 中止；成功后用返回的 `file_path` 引用文件。
- [ ] shadow 报告 URL 可直接内嵌（inline  disposition）；`shadow_id` 形如 `shadow_1a2b3c4d`。
- [ ] 健康探针区分 `/live`（进程活着）与 `/ready`（LLM 可用，503 带原因）——编排层按 Kubernetes 语义使用。
- [ ] 不向终端用户暴露 `/system/shutdown`、`/openapi.json`、`/skills` 等控制/侦察面。
