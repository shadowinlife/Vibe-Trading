# Vibe-Trading 用户认证系统（多租户 Phase 1）

> 分支：`mymain-engine-bridge` ｜ 目标：拆除 nginx 串码 + 去掉用户侧 API KEY + 隐藏配置页
> 隔离粒度：**仅认证，共享工作区**（用户裁决 2026-09-19）
> 注册策略：**邀请码**（用户裁决）
> 配置页：**前端隐藏 + 后端锁 admin**（用户裁决）

---

## 0. 背景与根因

### 0.1 用户看到的报错

```
远程 API 访问需要 API 密钥。请在设置中添加密钥，或在 localhost 上运行后端以仅限本地使用。
```

- 前端文案：`frontend/src/i18n/locales/zh-CN.json:581`（key `agent.authRequired`）
- 触发条件：`frontend/src/lib/api.ts:295-307` `errorFromResponse()` 把**任何 401/403** 的 detail 覆盖成这条 i18n 文案
- 后端来源：`agent/src/api/security.py:501-504`，403 `"API_AUTH_KEY is required for non-local API access"`

### 0.2 真实机制（已于 2026-09-19 18:41 在服务器上实证，含一次自我更正）

> ⚠️ **本节曾写错并已更正。** 初版断言「uvicorn 不解析 XFF，文档 §11#8 是空操作」——**错误**。
> `uvicorn` 的 `proxy_headers` 默认值即 `True`，`forwarded_allow_ips` 默认 `"127.0.0.1"`。
> 仓库里「零命中」只说明**未显式配置 ⇒ 走默认值 ⇒ 功能是开启的**，不能反推为关闭。

**实证**（从公网 Mac，出口 IP `<OBSERVER_CLIENT_IP>`，带串码访问 `http://<ECS_PUBLIC_IP>:4096/`）：

```
18:41:38 server1 gateway-start.sh[2445935]: INFO: 127.0.0.1:0 - "GET /settings/llm HTTP/1.1" 200 OK
18:41:38 server1 gateway-start.sh[2445935]: INFO: 127.0.0.1:0 - "GET / HTTP/1.1" 200 OK
```

**取证标记**：日志中 `:0` 端口 ⇒ 地址来自 `X-Forwarded-For` 头（无端口信息）；真实 TCP 对端会带端口（对照 `127.0.0.1:56370`）。这是区分「XFF 派生」与「真实 peer」的硬证据。

**当前生效配置**（`nginx -T` 实测，非文件推断）：
```nginx
auth_basic "opencode-web gate";
auth_basic_user_file /etc/nginx/opencode-web.htpasswd;
location / {
    proxy_pass http://127.0.0.1:8081;
    proxy_set_header Host $host;                            # 保留公网 Host
    proxy_set_header X-Forwarded-For 127.0.0.1;             # ← 字面量，对 gateway 谎报客户端 IP
    ...
}
```
`API_ALLOWED_HOSTS=<ECS_PUBLIC_IP>,localhost,127.0.0.1` 设在 **systemd unit**（`/etc/systemd/system/vt-gateway.service:18`），不在 `.env` —— 故 `_is_allowed_loopback_host()`（`security.py:133-136`）的 Host 校验通过。

**⇒ 真实机制**：nginx 把**每一个**互联网客户端的 IP 都改写成 `127.0.0.1`，uvicorn 默认信任来自 `127.0.0.1` 的 XFF，于是 `_is_local_client()`（`security.py:507-518`）恒为 True ⇒ `security.py:499-500` 发 `Principal(LOOPBACK_TRUST)` ⇒ **零凭证全权放行**。

**挡在公网与完整 API 之间的，只有 nginx 那一个共享串码。**

### 0.3 用户报错的真实根因（已定位并修复，2026-09-19 19:00）

> ⚠️ **本节经历过两次错误假设，最终由对照实验定论。记录全过程以免重蹈。**

**已排除的假设**（均有实测数据，勿再回头验证）：

| 假设 | 证伪证据 |
|---|---|
| ❌ `.env` 设了 `API_AUTH_KEY` 触发 key-first | `grep -c '^API_AUTH_KEY=' /opt/my-vibe-trading/.env` → **0**；`VIBE_TRADING_API_KEY` → **0** |
| ❌ 宿主机 `gateway-start.sh` 传了 `--proxy-headers` | 实测 `serve_main(['--host','127.0.0.1','--port','8081'])`，**未传**（走 uvicorn 默认，默认即开启） |
| ❌ `API_ALLOWED_HOSTS` 缺失导致 Host 校验 403 | 实际设在 systemd unit 第 18 行：`<ECS_PUBLIC_IP>,localhost,127.0.0.1` |
| ❌ 「17:01 的 XFF 修复已解决问题」 | 日志证明 17:01 **之后**仍有 `17:05:51 127.0.0.1:0 - "POST /sessions" 403`、`17:18:41` 同样 403 —— IP 已是 loopback 却仍被拒 |

**真实根因：nginx `$host` 剥掉端口，导致 `_origin_matches_request_host()` 端口比较必然失败。**

`security.py:400-420`：
```python
origin_port = parsed.port                       # Origin: http://<ECS_PUBLIC_IP>:4096 → 4096
request_host = _host_without_port(request.headers.get("host", ""))
request_port = request.url.port                 # Host: <ECS_PUBLIC_IP>（无端口）→ None → 按 http 默认 80
return origin_port == request_port              # 4096 != 80 → False → 403
```

nginx 第 18 行原为 `proxy_set_header Host $host;`。**nginx `$host` = 小写主机名，不含端口**；`$http_host` = 客户端原始 Host 头，含端口。

**三组对照实验（直连 gateway :8081，绕过 nginx，决定性证据）**：

| # | Host 头 | Origin 头 | 结果 |
|---|---|---|---|
| 1 | `<ECS_PUBLIC_IP>`（无端口） | `http://<ECS_PUBLIC_IP>:4096` | **403** `Cross-site request denied` |
| 2 | `<ECS_PUBLIC_IP>:4096`（带端口） | `http://<ECS_PUBLIC_IP>:4096` | **201** ✅ |
| 3 | `<ECS_PUBLIC_IP>`（无端口） | `http://<ECS_PUBLIC_IP>`（隐含 80） | **201** ✅ ← 完美对照：80==80 故通过 |

**为什么 GET 全部正常、只有交互失败**：`security.py:489-490` 只对非安全方法做跨站检查（`_SAFE_BROWSER_METHODS = {GET, HEAD, OPTIONS}`）。所以页面能打开、设置能读，但**一发消息（`POST /sessions`）就 403** —— 与用户描述的「登录后进行交互时报错」完全吻合。

**为什么诊断这么难**：`frontend/src/lib/api.ts:295-307` `errorFromResponse()` 把**任何** 401/403 的 `detail` 无条件覆盖成 `agent.authRequired` 文案。真实的 `Cross-site request denied` 被替换成「远程 API 访问需要 API 密钥」，把调查引向完全错误的方向（API KEY / loopback / XFF）。**这是 D18 必须修掉的可用性缺陷**，本事故是它的实证案例。

**已实施的修复**（19:00:58，生产）：
```diff
- proxy_set_header Host $host;
+ proxy_set_header Host $http_host;
```
- 备份：`/etc/nginx/conf.d/opencode-web.conf.pre-hostfix-20260919-190058`
- `nginx -t` 通过 → `systemctl reload nginx`
- Host 白名单不受影响：`_is_allowed_loopback_host()` 经 `_host_without_port()`（`security.py:118-130`）先剥端口再比对 `API_ALLOWED_HOSTS`，故 `<ECS_PUBLIC_IP>:4096` → `<ECS_PUBLIC_IP>` ✓ 仍放行

**外网端到端验证**（本机 Mac，出口 `<OBSERVER_CLIENT_IP>`，全部带浏览器 `Origin` + `Sec-Fetch-Site: same-origin`）：

| 请求 | 修复前 | 修复后 |
|---|---|---|
| `POST /sessions` | **403** | **201** ✅ |
| `POST /options/payoff` | 403 | **200** ✅ |
| `POST /auth/sse-ticket`（EventSource 路径） | 403 | **200** ✅ |
| `GET /` `/settings/llm` `/api/portfolio` `/alpha/list` `/live/status` | 200 | **200** ✅ |
| 无串码 `POST /sessions` | 401 | **401** ✅（鉴权未被削弱） |

测试产生的 4 个空会话已 `DELETE` 清理（200），7 个真实会话完好。

### 0.3.1 上游候选缺陷（独立 PR 价值）

1. **`_origin_matches_request_host()` 对反代不健壮**（`security.py:400-420`）：`proxy_set_header Host $host;` 是 nginx 社区最常见的写法（大量官方文档与教程都用它），而它会剥端口 ⇒ **任何部署在非标准端口 + 反代后的实例，所有 POST/PUT/DELETE 都会 403**。建议：端口缺失时回退比对 `X-Forwarded-Port` / `X-Forwarded-Host`，或在 detail 中明确写出 `origin_port` 与 `request_port` 的实际值（当前只回一句 `Cross-site request denied`，无任何可诊断信息）。
2. **前端错误文案覆盖**（`api.ts:295-307`）：把所有 401/403 一律渲染成「需要 API 密钥」，会系统性地误导运维。建议保留后端 `detail`，仅在 detail 缺失时才回退到通用文案。

### 0.4 时间线汇总

| 时刻（2026-09-19） | 事件 | 效果 |
|---|---|---|
| 16:56–16:58 | 日志 `<CLIENT_IP>:0` → 403（GET 也拒） | nginx 当时转发真实客户端 IP ⇒ `_is_local_client()`=False |
| 17:01:22 | conf mtime 变更 + nginx Reloaded（XFF 改字面量 `127.0.0.1`） | GET 恢复 200，**POST 仍 403** |
| 17:05 / 17:18 | `127.0.0.1:0 - "POST /sessions" 403` | 证明还有第二个独立故障 |
| 18:58–19:00 | 对照实验定位到 Host 端口比较 | 根因确认 |
| **19:00:58** | `$host` → `$http_host` + reload | **全部恢复**，外网实测 201/200 |

### 0.5 由此推出的硬约束（拆 nginx Basic Auth 的前置条件）

由 §0.2：**当前「能用」完全依赖 nginx 谎报客户端 IP**。这是一个安全反模式 —— gateway 无从得知真实客户端是谁，审计日志里的 `127.0.0.1` 全是假的，且一旦串码泄露即等于完整 API 权限。

两种拆 Basic Auth 的错误做法及其后果：

| 做法 | 后果 |
|---|---|
| 只删 `auth_basic`，保留 `X-Forwarded-For 127.0.0.1` | **整个 API 裸露公网**：任何人可读写会话、改 LLM 凭证、触发回测烧配额、调 `/system/shutdown`。零凭证。 |
| 只删 `auth_basic`，同时把 XFF 改成 `$proxy_add_x_forwarded_for` | 回到 16:56 的状态：`_is_local_client()`=False 且无 `API_AUTH_KEY` ⇒ **所有人 403**，服务不可用 |

⇒ **必须引入显式开关 `VIBE_TRADING_USER_AUTH=1`**：开启后禁用 loopback 信任（D5 步骤 4 条件化），改由 user session 认证；同时 nginx 改为转发真实 IP（D14）。三者必须同批上线，顺序见 Phase 5。

---

## 1. 架构落点（已定，无需再议）

| 决策 | 结论 | 依据 |
|---|---|---|
| 认证放哪一层 | **vt-gateway（`agent/src/api/`）** | `OpencodeAgent/deploy/router/README.md:313-319` 明确划界：*"business logic / auth verdicts — the tenant gateway owns them"*；且 router 生产未接线（dormant） |
| 是否复用 T11 router | **本轮不复用** | router 身份模型是「每租户一把共享 Bearer Key」，无用户账号体系；用户已裁决共享工作区，不需要 per-tenant 容器 |
| 身份模型扩展点 | `Principal` + `AuthMethod` | `agent/src/session/models.py:33` 已预留 `FEDERATED_IDENTITY`（注释："Not reachable today"）；`:64` 已有 `tenant: Optional[str]`（注释："for a future multi-tenant runtime root"） |
| 新增依赖 | **零** | `pyproject.toml` 无 passlib/bcrypt/argon2/pyjwt/sqlalchemy；`requirements-lock.txt` 是 hash 锁定。用 stdlib `hashlib.scrypt` + `secrets` + `sqlite3` |
| 上游回流友好性 | **默认关闭，opt-in** | `VIBE_TRADING_USER_AUTH` 默认 `0` 时行为 100% 不变 ⇒ 可作为独立 PR 提给 HKUDS/Vibe-Trading |

---

## 2. 设计决策（D1–D19）

### D1. Session 机制：不透明 Bearer Token（存 sha256）

- 生成：`secrets.token_urlsafe(32)`
- 存储：SQLite `sessions.token_sha256`（**只存 sha256，不存明文**）——与 router registry 的 `token_sha256` 姿势一致（`registry.py:93-95`），DB 泄露 ≠ 凭据泄露
- 前端：localStorage key `vibe_trading_session_token`，经 `Authorization: Bearer <token>` 发送
- **复用现有管线**：`frontend/src/lib/apiAuth.ts:18-21` 的 `authHeaders()` 已经是 `Bearer` 语义，只需换 localStorage key 名；SSE 的 `POST /auth/sse-ticket` → `?ticket=` 流程（`apiAuth.ts:36-53` + `auth_routes.py:46`）**完全不用改**，因为该端点由 `require_auth` 守卫，而 `require_auth` 会自动接受新的 session token
- TTL：7 天滑动过期（每次校验刷新 `last_seen_at`，`expires_at` 顺延）
- **权衡记录**：localStorage 可被 XSS 读取。缓解 = 现有严格 CSP（`security.py:195-205`，`script-src 'self'`，无 `unsafe-inline`）。**未选 HttpOnly cookie**，因为需要全 api 层加 `credentials:'include'` + 重做 CSRF，而 `security.py:423-431` 的 `_reject_cross_site_browser_request` 已覆盖同源校验。Oracle 复核确认此推理成立：跨站 JS 读不到他源 localStorage，也无法在不触发 CORS preflight 的情况下设置 `Authorization` 头 ⇒ header-based token 天然免疫 CSRF。

### D1.1 会话校验 SQL 必须 JOIN users（Oracle B5，阻断项）

`ON DELETE CASCADE` 只覆盖**删行**；而 `deactivate` 是 `is_active=0`，**行还在** ⇒ 不 JOIN 则被停用用户的存量 session 最长再活 7 天。D10 的手动 `revoke-sessions` CLI 不够（依赖运维记得执行）。

**校验查询固定为**：
```sql
SELECT u.username, u.role, u.display_name, s.expires_at
FROM sessions s JOIN users u ON u.id = s.user_id
WHERE s.token_sha256 = ?
  AND s.expires_at > ?            -- now, UTC ISO8601
  AND u.is_active = 1
```

- `role` 从**同一条查询**取出 ⇒ D7.1 的 `Principal.role` 每请求新鲜、零额外查询、无 stale 窗口；admin 降级 / 用户停用都在下一个请求立即生效
- `deactivate` 因此**自动**失效该用户全部 session，无需额外动作
- 校验通过后才做滑动续期

**滑动续期的写放大（Oracle NB#2）**：每请求刷 `last_seen_at`/`expires_at` = 每个 API call 一次 SQLite 写事务；SSE + 前端轮询下每秒可达数十次。**必须节流**：仅当距上次刷新 > 60s 才写。读路径（JOIN SELECT）不节流。

### D2. 密码哈希：`hashlib.scrypt`（stdlib）

- 参数：`n=2**14, r=8, p=1, dklen=32`，每用户 `secrets.token_bytes(16)` 盐
- 存储格式：`scrypt$16384$8$1$<salt_b64>$<hash_b64>`（带算法标识，便于将来迁移 argon2）
- 校验：`hmac.compare_digest`（恒定时间）
- 选 scrypt 而非 pbkdf2：内存困难（memory-hard），抗 GPU/ASIC；两者都是 stdlib，项目要求 Py3.11+
- **import smoke guard（Oracle NB7）**：`hashlib.scrypt` 依赖 OpenSSL 后端，个别平台/构建可能缺失。上游 CI 含 Windows ⇒ 模块导入时探测一次，缺失则 **fail loud**（明确报错并提示改用 pbkdf2 回退），绝不静默降级到弱哈希
- ⚠️ `n=2**14` 每次约 16MB 内存 ⇒ 登录端点是未认证的 CPU/内存放大器。本轮不做应用层限流（D11，用户裁决），改由 **Phase 5 的 nginx `limit_req_zone`** 覆盖（零应用代码）

### D3. DB 位置

- `get_runtime_root() / "users.db"`（`agent/src/config/paths.py:13-34`）
  - 生产：`/opt/my-vibe-trading/.vibe-trading/users.db`
  - 本地：`~/.vibe-trading/users.db`
- 可覆盖：新增 `VIBE_TRADING_USERS_DB_PATH`
- 先例：`agent/src/strategy_store/sqlite_store.py:37` `_DEFAULT_DB_PATH = Path.home()/".vibe-trading"/"strategy_store.db"`
- 连接姿势照抄 `sqlite_store.py:146-152`：`sqlite3.connect(check_same_thread=False)` + `row_factory=sqlite3.Row` + `PRAGMA foreign_keys=ON / busy_timeout=5000 / journal_mode=WAL / synchronous=NORMAL` + `threading.RLock()` + `PRAGMA user_version` 迁移
- 文件权限 0600

### D4. Schema（3 表，`user_version=1`）

```sql
CREATE TABLE users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT    NOT NULL UNIQUE,          -- 3-32, ^[A-Za-z0-9_-]+$，入库前 lowercase 归一
  password_hash TEXT    NOT NULL,
  role          TEXT    NOT NULL DEFAULT 'user',  -- 'user' | 'admin'
  is_active     INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT    NOT NULL,                 -- ISO8601 UTC
  last_login_at TEXT
);

CREATE TABLE sessions (
  token_sha256 TEXT    PRIMARY KEY,
  user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at   TEXT    NOT NULL,
  expires_at   TEXT    NOT NULL,
  last_seen_at TEXT,
  user_agent   TEXT
);
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_expiry ON sessions(expires_at);

CREATE TABLE invites (
  code_sha256 TEXT    PRIMARY KEY,
  created_by  INTEGER REFERENCES users(id),
  created_at  TEXT    NOT NULL,
  expires_at  TEXT,                               -- NULL = 永不过期
  max_uses    INTEGER NOT NULL DEFAULT 1,
  used_count  INTEGER NOT NULL DEFAULT 0,
  note        TEXT
);
```

> **username 归一化（Oracle NB7）**：SQLite `UNIQUE` 是**字节精确**比较，`Alice` 与 `alice` 会是两个不同账号 ⇒ 视觉冒充风险。注册与登录**都必须**先 `username.strip().lower()` 再入库/查询；`display_name` 保留用户提交的原始大小写用于 UI 展示。

### D5. 鉴权改造 —— **采用「启动期不变量」而非「5 处 loopback 条件化」**（Oracle 复审后重构）

> **设计变更（Revision 2）**：初版计划在 5 个函数里分别条件化 loopback 信任。Oracle 指出现有 **key-first 优先级（GHSA-7wgj，`security.py:492-497`）在 `API_AUTH_KEY` 已设置时，已经让 loopback 信任在全部 5 个调用点自动失效** —— 这是现成的、被 `test_auth_precedence.py` 9 个回归测试钉死的机制。生产反正必须设 key（D15：CLI / T11 router 需要）。故改为用启动期不变量承重，`security.py` 的 diff 从「+45 行 / 5 函数」收缩到 **~15 行 / 1-2 函数**。

**核心不变量（新增，启动期强制）**：

```
VIBE_TRADING_USER_AUTH=1 且 API_AUTH_KEY 为空  ⇒  拒绝启动（fail fast，非仅告警）
```

理由：这个组合正是 §0.5 表格里「删了 basic auth 却仍走 loopback 信任 = 公网裸奔」的危险象限。用启动期不变量把它**从设计上排除**，而不是靠部署顺序和运维纪律去规避。

**误配置矩阵（改造后全面 fail-closed）**：

| flag | API_AUTH_KEY | nginx basic auth | 结果 |
|---|---|---|---|
| 0 | 空 | 在 | 现状（loopback 信任 + 串码），不变 |
| 0 | 空 | **已删** | 🔴 公网裸奔 —— **但 D14 要求 XFF 改真实 IP，此时 client=公网 IP ⇒ loopback 不命中 ⇒ 全员 403，fail-closed** |
| 0 | 已设 | 任意 | key-first，浏览器需 key（今天的 GHSA-7wgj 行为） |
| **1** | **空** | 任意 | **拒绝启动** ✅ 危险象限被排除 |
| 1 | 已设 | 已删 | 🟢 目标态：浏览器走 user session，机器走 shared key |

**`_validate_api_auth`（`security.py:463-504`）新顺序**：

1. 非安全方法先过 CSRF（`:489-490`，**不变**）
2. **【新，且 gate 在 flag=1】** Bearer token 命中有效 user session ⇒ `Principal(subject=username, auth_method=USER_SESSION, tenant=username, display_name=username, role=<from JOIN>)`
3. `API_AUTH_KEY` 已配置且 token 匹配 ⇒ `Principal(SHARED_KEY)`（**不变**）
4. loopback 信任 ⇒ `Principal(LOOPBACK_TRUST)`（**完全不改** —— key-first 已在步骤 3 之前拦截；生产设了 key 故此分支永不命中）
5. 否则 403

**为什么步骤 2 必须 gate 在 flag=1**（Oracle B5，修正初版）：初版写「flag=0 时步骤 2 仍生效」，这与 §1「默认 0 时行为 100% 不变」**自相矛盾** —— flag=0 会给每个上游部署的每个请求加一次 SQLite 查询；遗留 `users.db` 里的旧 token 在 flag=0 时仍能认证；GHSA 测试环境没有 `users.db`，需要额外容错。gate 在 flag=1 后，默认路径**零改动、零查询**。回滚场景下浏览器根本不需要 token（回到 loopback 信任），无损失。

**为什么步骤 2 必须在步骤 3 之前**：需求 1 要求登录用户不需要 API KEY。若 key 已设置（key-first），session token 会先被当成错误的 shared key 而 401。

**`require_settings_write_auth`（`security.py:640-656`）改为 flag-aware**（Oracle NB#1，替代初版的「注入 3 个模块」）：

```
flag=0 ⇒ 行为逐字节不变（key-first + loopback）
flag=1 ⇒ 接受 (USER_SESSION 且 role=admin) 或 SHARED_KEY（break-glass）；拒绝 loopback / 非 admin
```

**为什么改本体而不是注入**：实测 `register_connection_routes`（`connection_routes.py:44`）与 `register_portfolio_routes`（`portfolio_routes.py:121`）签名都是 `(app: FastAPI)`，**不支持依赖注入** —— 初版 D7 的注入方案对这两个文件根本行不通。改 `require_settings_write_auth` 本体则自动覆盖 connection / portfolio / qveris（qveris 本就经 `_host()` 委托，`qveris_routes.py:81-86`）三处写端点，零文件改动。

**`require_local_or_auth`（`security.py:625-637`）**：全仓库仅 2 个消费点（`settings_routes.py:601,739`，已实测确认），且 `register_settings_routes` 支持注入 ⇒ 这两个 GET 用 D7 的注入方案，**不改本体**。

⚠️ **新发现的测试约束**：`agent/tests/test_api_infrastructure.py:20` 有身份断言
```python
assert api_server.require_local_or_auth is security.require_local_or_auth
```
用 `is` 比较对象同一性 ⇒ **任何对 `require_local_or_auth` 的包装/替换都会让它失败**。注入方案（在 `api_server.py:220` 传参）不触碰模块属性，故安全；但若改为在 `security.py` 里重新绑定该名字，此测试必挂。实现时须保持 `security.require_local_or_auth` 对象不变。

**`require_event_stream_auth`（`security.py:591-622`）**：浏览器走 ticket 已通（ticket 由 `POST /auth/sse-ticket` 换发，该端点由 `require_auth` 守卫 ⇒ 自动接受 session token）。仅为非浏览器 SSE 客户端（直接带 Bearer session token）加一个 flag-gated session 分支，可选。

**`_require_shutdown_authorization`（`security.py:434-451`）**：**不改**。flag=1 且 key 已设 ⇒ key-first 已生效，`/system/shutdown` 需要 shared key，浏览器用户（session token）无法关停服务。这是期望行为。

**`_reject_untrusted_loopback_host`（`security.py:166-173`）**：**不改**。flag=1 + 真实 XFF ⇒ `_is_local_client()`=False ⇒ 该 DNS-rebinding 守卫对代理请求跳过。此时 loopback 信任已关，守卫本无意义，可接受。**需在代码注释中写明这一推理**，否则后人会误以为是漏洞。

**兼容性保证**：`VIBE_TRADING_USER_AUTH=0`（默认）时步骤 2 不执行、步骤 3/4/5 逐字节不变 ⇒ `agent/tests/test_auth_precedence.py` 全部 9 个 GHSA-7wgj 回归测试（4 个 loopback 用例 + 5 个 event-stream 用例）**必须保持绿**，且 `test_api_infrastructure.py:20` 的身份断言必须保持绿。

### D6. `AuthMethod` 扩展（`agent/src/session/models.py:17-38`）

```python
class AuthMethod(str, Enum):
    SHARED_KEY = "shared_key"
    LOOPBACK_TRUST = "loopback_trust"
    FEDERATED_IDENTITY = "federated_identity"
    USER_SESSION = "user_session"        # 【新】本地用户名/密码换发的会话

ATTRIBUTABLE_AUTH_METHODS = frozenset({
    AuthMethod.FEDERATED_IDENTITY,
    AuthMethod.USER_SESSION,             # 【新】能指名到人
})
```

- **不复用 `FEDERATED_IDENTITY`**：该值语义是「外部 IdP」，本地密码认证不是 federated
- 加入 `ATTRIBUTABLE_AUTH_METHODS` ⇒ `Principal.attributable` 自动为 True（`models.py:74-79` 由 `__post_init__` 派生，调用方不可设置）——这正是 `models.py:29-33` 注释里等待的那个 True case
- `tenant=username`：本轮**不做任何过滤**（用户裁决：共享工作区），仅把字段填上，为 Phase 2 逻辑隔离留接口

### D7. Admin 门禁：**必须 flag-aware**，否则注入即破坏 flag-off 世界（Oracle B2）

> **阻断项修正**：初版的 `require_admin` 拒绝一切非 `USER_SESSION` principal。但注入是**静态**的（`api_server.py:220`，app 组装期），而 flag 是**运行期**读的。若照初版实现，flag=0 时：
> - 本地 dev 的 loopback principal → 403 ⇒ **上游默认 UX 全灭**
> - **桌面端 Electron 的 per-launch `API_AUTH_KEY` bearer**（已实测：`desktop/electron/src/main.ts:94` `details.requestHeaders.Authorization = Bearer ${apiAuthKey}`）→ SHARED_KEY principal → 403 ⇒ **桌面设置页全灭**
>
> 直接违反 §1「默认 0 时行为 100% 不变」的立约。

**正确实现**（新文件 `agent/src/api/admin_auth.py`）：`require_admin` 在**请求时**读 flag：

```
flag=0 ⇒ 委托被包裹的原依赖（require_local_or_auth / require_settings_write_auth），行为逐字节不变
flag=1 ⇒ 要求 USER_SESSION 且 role=="admin"；同时放行 SHARED_KEY 作 break-glass
```

**为什么 flag=1 时仍放行 SHARED_KEY**：它本来就是 god-mode 秘密（能改 LLM 凭证），拒绝它不会提升安全性，反而在 `users.db` 损坏时切断机器通道、造成锁死。D10 的 CLI 是服务端 break-glass（直接操作 DB，不经 HTTP），两者互补。

**注入适用范围（已实测各 register 签名）**：

| 模块 | 签名 | 能否注入 | 处置 |
|---|---|---|---|
| `settings_routes.py:577` | `(app, require_local_or_auth=None, require_settings_write_auth=None)` | ✅ | 注入 flag-aware `require_admin`（仅 2 个 GET 需要；写端点由 D5 的 `require_settings_write_auth` 本体改造覆盖） |
| `connection_routes.py:44` | `(app: FastAPI)` | ❌ **无注入参数** | 靠 D5 改 `require_settings_write_auth` 本体自动覆盖 |
| `portfolio_routes.py:121` | `(app: FastAPI)` | ❌ **无注入参数** | 同上 |
| `qveris_routes.py:81-86` | 自有 `_require_auth` 经 `_host()` 委托 | — | 同上（委托链自动跟随） |
| `live_routes.py:742` | `(app, require_auth=None, ...)` | ✅ | 见下方「权限面决策」 |
| `channels_routes.py:57` | `(app, require_auth=None, ...)` | ✅ | 同上 |
| `scheduled_routes.py:361` | `(app, require_auth=None, ...)` | ✅ | 同上 |

**`settings_routes.py` 仍保持零新增代码**（797/800 行）—— 注入从 `api_server.py:220` 传入。

**D7.1 `Principal.role`**：给 frozen dataclass 加 `role: str = "user"`（有默认值 ⇒ 加法式变更，不破坏现有构造点）。**role 必须来自 session 校验时的同一条 JOIN 查询**（见 D1.1），因此每请求新鲜、零额外查询、无 stale 窗口 —— admin 降级或用户停用都在下一个请求立即生效。`to_dict()` **不**序列化 role（避免把角色固化进持久化记录）。

**⚠️ 权限面不一致（需用户决策，Oracle NB#3）**：settings 锁 admin，但下列端点对**任何**登录用户开放：

| 端点 | 文件:行 | 风险 |
|---|---|---|
| `POST /mandate/commit`、`/live/*`（下单/撤单/halt） | `live_routes.py:760-1169` | **实盘交易** —— 比改设置危险得多 |
| `/channels/start\|stop\|pairing` | `channels_routes.py:94-107` | 启停 IM bot、绑定发送者 |
| `/scheduled/*`（周期任务增删） | `scheduled_routes.py:391-705` | 定时烧 LLM 配额 |

这与「共享工作区」裁决一致（所有用户都是半可信的邀请码持有者），但三者的 register 签名**都已支持注入** ⇒ Phase 2 锁 admin 是**零文件改动**。**✅ 用户裁决（2026-09-19）：一并锁 admin。**

⇒ Phase 2 范围扩展：`live_routes`（`/mandate/commit`、`/live/*`）、`channels_routes`（`/channels/start|stop|pairing`）、`scheduled_routes`（`/scheduled/*`）全部注入 flag-aware `require_admin`。三者签名已实测支持注入（`live_routes.py:742`、`channels_routes.py:57`、`scheduled_routes.py:361`）⇒ **零文件改动**，只在 `api_server.py` 的注册调用处传参。

⚠️ 连带影响需实现时确认：
- `channels_routes.py:88,94,100,107` 有 4 个 `require_auth` 消费点，其中**只读**的 status 类端点是否也要锁 admin？建议：**只锁写操作**（start/stop/pairing），status 保持 `require_auth`（普通用户需要看到渠道状态才能理解为什么不能改）。实现时按端点语义逐个判断，不要一刀切替换整个模块的依赖。
- `scheduled_routes.py` 有 10 个 `require_auth` 消费点（`:391,496,510,524,536,549,572,592,615,705`）。同理：**列表/详情读取保持 `require_auth`，增删改锁 admin**。
- `live_routes.py` 有 9 个消费点（`:760,820,841,860,879,1050,1078,1118,1169`）。`/live/status` 等只读端点保持 `require_auth`；`/mandate/commit` 与下单/撤单/halt 锁 admin。
- **flag=0 时全部委托原依赖**（D7 的 flag-aware 契约），故上游默认行为与桌面端不受影响。

### D8. 新增脱敏只读端点 `GET /settings/runtime`

**问题**：`frontend/src/pages/Agent.tsx:1365` 聊天页自己调 `api.getLLMSettings()` 取 `sse_timeout_seconds`（SSE 看门狗）与 provider/model 显示名。若 `GET /settings/llm` 锁 admin，普通用户聊天页会退化（`sseTimeoutMsRef` 回落默认 90s，`llmSettings=null` 传给 `:1766` 的组件）。

好消息：`Agent.tsx:1368` 已有 `.catch(() => {})`，不会崩，但会静默降级。

**方案**：新文件 `agent/src/api/runtime_settings_routes.py` 提供
```
GET /settings/runtime  →  { provider, model_name, sse_timeout_seconds }
```
- 守卫：`require_auth`（任意已登录用户）
- **绝不返回** `api_key_hint` / `api_key_configured` / `env_path`（文件系统路径泄露）/ `providers` 目录（含 `api_key_env` 名）——这些是 `LLMSettingsResponse`（`settings_routes.py:44-62`）里的敏感字段
- `Agent.tsx:1365` 改调此端点

### D9. 邀请码

- 生成：`secrets.token_urlsafe(16)`，只存 sha256，**生成时明文只显示一次**
- 注册必须携带有效码；`used_count += 1` 与 `INSERT users` 在**同一事务**内（防止一码多用竞态）
- 校验：`max_uses` 未耗尽 + `expires_at` 未过
- 管理：CLI（见 D10）

### D10. 管理员 CLI（新文件 `agent/src/api/user_admin.py`）

首个 admin 无法由 admin 创建 ⇒ 必须有 CLI bootstrap。

```bash
python -m src.api.user_admin create-admin <username>     # getpass 交互输密码
python -m src.api.user_admin create-user  <username> [--role user|admin]
python -m src.api.user_admin invite [--uses N] [--days N] [--note TEXT]   # 打印明文码一次
python -m src.api.user_admin list
python -m src.api.user_admin deactivate <username>
python -m src.api.user_admin reset-password <username>
python -m src.api.user_admin revoke-sessions <username|--all>
```

- 密码经 `getpass` 交互输入，**绝不接受命令行参数**（会进 shell history / `ps`）
- 输出不含任何 hash 或 token 明文
- 后续可选：接入 `vibe-trading users ...` 子命令（`agent/cli/` 体量巨大，本轮不做）

### D11. 限流 —— **本轮不做**（用户裁决 2026-09-19：DDoS 当前不考虑）

原设计（复用 `_SlidingWindowRateLimiter`、抽取 `rate_limit.py`、修正 `_client_key` 的反代取址）**全部移出本轮范围**。

连带简化：
- 不再新增 `agent/src/api/rate_limit.py`
- 不再重构 `agent/src/api/system_routes.py`（保持 468 行原样，`_SlidingWindowRateLimiter` 留在原处）
- 不再需要处理「nginx 同机 ⇒ 所有用户共享限流桶 `127.0.0.1`」这个坑

**保留的替代防线**（不依赖限流）：
1. **scrypt 本身就是撞库防线** —— `n=2**14` 使单次校验约需 ~16MB 内存 + 可感知 CPU 时间，这正是 scrypt 的设计目的。攻击者每秒可尝试的密码数被哈希成本天然压制，无需额外限流。
2. **邀请码闸门** —— 注册端点需有效邀请码（128 bit `token_urlsafe(16)`），攻击面收敛到「已存在用户的密码」，而非无限注册。
3. **统一错误文案**（D17）—— 防用户名枚举，与限流无关，保留。

**已知残留风险（显式接受，不在本轮处理）**：
- `POST /auth/login` 是**未认证的 CPU/内存放大器**：无凭证即可触发 scrypt。若将来遭遇针对性 DoS，最低成本的补救是在 nginx 层加 `limit_req_zone`（无需改应用代码），其次才是恢复本节的 D11 原设计。
- 若届时需应用层限流，务必先修 `_client_key(request)`：它基于 `request.client.host`，nginx 同机会让全体用户共享 `127.0.0.1` 这一个桶 —— 不修则限流要么形同虚设、要么把全站锁死。

### D12. 前端路由与守卫

`frontend/src/router.tsx`（76 行，`createBrowserRouter`，**当前无任何 guard**）：

```tsx
const router = createBrowserRouter([
  { path: "/login", element: <Login /> },          // 【新】Layout 之外，无侧边栏
  { path: "/", element: <RequireAuth><Layout /></RequireAuth>, children: [ /* 现有 */ ] },
]);
```

新增 `frontend/src/components/auth/RequireAuth.tsx`：
- 无 token ⇒ `<Navigate to="/login" replace />`
- 有 token ⇒ mount 时 `GET /auth/me` 校验并取 `role`；401 ⇒ 清 token + 跳 `/login`
- 校验期间渲染 loading（避免闪一下主界面）
- role 存入新的 zustand store `frontend/src/stores/auth.ts`（项目已用 zustand 5）

`/login` 刷新不会 404：`agent/src/api/spa.py:14-20` `SPAStaticFiles` 捕获 404 回落 `index.html`。

### D13. 前端隐藏配置页（按 role）

- `frontend/src/components/layout/Layout.tsx:30`：`{ to: "/settings", ... }` 改为**仅 admin 可见**（不是全删——否则你自己也无法从 UI 配置）
- `frontend/src/router.tsx:65`：`/settings` 路由加 role 守卫，非 admin ⇒ `<Navigate to="/" replace />`
- `frontend/src/pages/Settings.tsx:289-320` `localApiAccessSection`：**整段删除**（用户不再需要填 API KEY）。连带删 `:49` `useState(() => getApiAuthKey())`、`:211-216` `submitLocalApiKey()`、`:329` 渲染点
  - `Settings.tsx` 当前 782/800 行 ⇒ 删除约 32 行后**净释放空间**，符合行数纪律
- `Layout.tsx` 新增用户菜单：显示 username + 登出按钮

### D14. nginx 改造（生产，`/etc/nginx/conf.d/opencode-web.conf`）

> 现状已由 `nginx -T` 实测确认（见 §0.2）。**关键认知更正**：`X-Forwarded-For 127.0.0.1` **不是空操作，而是当前唯一的承重墙** —— 删掉它而不启用 `USER_AUTH` 会让所有人 403（回到 16:56 状态）；保留它而删掉 `auth_basic` 会让 API 裸露公网。两者必须与 D5/D16 同批切换。

**删除**：
- `auth_basic` / `auth_basic_user_file`（需求 2）—— 仅在 `VIBE_TRADING_USER_AUTH=1` 已生效且登录验证通过之后
- `proxy_set_header X-Forwarded-For 127.0.0.1;` —— 它使 gateway 的审计日志与 `_client_key` 全部记录为假的 `127.0.0.1`，且是「零凭证放行」的直接成因

**改为**：
- `proxy_set_header X-Forwarded-For $remote_addr;` —— **覆盖，不是追加**（Oracle B1.3）
  - ⚠️ **不要用 `$proxy_add_x_forwarded_for`**：它是「客户端自带的 XFF + `, ` + `$remote_addr`」的**追加**语义。其不可欺骗性**完全依赖** uvicorn 从右往左遍历 + 默认信任名单这两个前提。一旦有人设了 `FORWARDED_ALLOW_IPS=*`（大量部署教程这么写），uvicorn 就变成取**最左**值 ⇒ 攻击者自带 `X-Forwarded-For: 127.0.0.1` 即可**复活 loopback 信任**，绕过全部鉴权。
  - `$remote_addr` 覆盖形态在**任何** uvicorn 配置下都不可欺骗。
- 保留 `proxy_set_header X-Forwarded-Proto $scheme;`
- **保留 `proxy_set_header Host $http_host;`** —— 这是 2026-09-19 19:00 刚修好的（§0.3），**不可回退成 `$host`**，否则所有 POST/PUT/DELETE 重新 403
- 保留 SSE 相关：`proxy_buffering off` / `proxy_read_timeout 3600s` / `proxy_send_timeout 3600s`
- 保留 `client_max_body_size 50m`、`Upgrade`/`Connection` map

**可选加固**：显式设置 uvicorn 的 `forwarded_allow_ips`。已实测生产**未设置** `FORWARDED_ALLOW_IPS`（走默认 `127.0.0.1`，恰好正确，因 nginx 同机）。若将来 gateway 与 nginx 分离，默认值会静默失效 ⇒ 建议在 `serve_main` 或 `gateway-start.sh` 显式传 `--forwarded-allow-ips=127.0.0.1`，把隐式依赖变成显式契约。

**版本漂移记录**：`requirements-lock.txt:3845` 钉 `uvicorn==0.52.4`，但生产 conda 环境实测为 **0.48.0**。两者 `proxy_headers` 默认值均为 `True`（生产环境已实测 `Config.__init__` 签名），故本计划结论不受影响；但漂移本身应记入 `MYMAIN_DIVERGENCE.md`。

**新增 TLS（强烈建议，见 §4.1）**

**gateway 环境新增**：`VIBE_TRADING_USER_AUTH=1` + **必须同时设置 `API_AUTH_KEY`**（D5 启动期不变量，否则拒绝启动）。写入 systemd unit `Environment=`，与既有 `API_ALLOWED_HOSTS` 同处。

### D15. 不能被破坏的既有消费方

| 消费方 | 走哪条鉴权 | 影响 |
|---|---|---|
| T11 router 转发 | 每租户 `Authorization: Bearer <API_AUTH_KEY>` | ✅ 走 D5 步骤 3，不变 |
| MCP server（`mcp_server.py`） | serve 派生子进程，直连 CH，不经 gateway HTTP | ✅ 无影响（实现时需复核） |
| cron 周期任务 | `opencode run --attach <serve:4098>`，直连 serve | ✅ 不经 gateway |
| `vibe-trading` CLI（本地） | loopback | ⚠️ `USER_AUTH=1` 时 loopback 信任关闭 ⇒ CLI 需带 `API_AUTH_KEY`。**默认 `USER_AUTH=0` 故本地开发不受影响**；生产 CLI 操作需文档说明 |
| 桌面端 Electron | `isDesktop` 分支（`Settings.tsx:329`） | ⚠️ 桌面端是单用户本地场景，应保持 `USER_AUTH=0`；`Settings.tsx` 的 desktop 条件分支需复核 |
| OpenBB Workspace bridge | `try_register_openbb_routes`（`api_server.py:311`），`/agents.json` `/v1/query` | ⚠️ `/v1/query` 走 host `require_auth`（`openbb_bridge/routes.py:118`）。flag=1 时若给它配的是 **session token，7 天后过期即静默失效** ⇒ 应给 OpenBB 配 **shared key**（`API_AUTH_KEY`），或文档化该降级。`/agents.json` 未认证，不受影响。另需确认 `_reject_cross_site_browser_request` 与 `VIBE_TRADING_EXTRA_CORS_ORIGINS` 仍放行 |

### D16. 新增 env（全部进 `agent/src/config/env_schema.py` `APIConfig`，**禁止 `os.getenv`**）

`README.md` 2026-07-10 条目记载：*"Environment variables now flow through a single Pydantic `EnvConfig` schema with an AST-based CI gate against future `os.getenv` sprawl"* ⇒ 有 CI 门禁，直接用 `os.getenv` 会挂 CI。

| 变量 | 默认 | 含义 |
|---|---|---|
| `VIBE_TRADING_USER_AUTH` | `0` | 总开关。`1` = 启用用户认证（session 分支 + admin 门禁 + 前端 capability）。**启动期不变量：`1` 且 `API_AUTH_KEY` 为空 ⇒ 拒绝启动**（D5） |
| `VIBE_TRADING_USERS_DB_PATH` | `<runtime_root>/users.db` | 用户库路径 |
| `VIBE_TRADING_SESSION_TTL_DAYS` | `7` | 会话有效期（滑动） |
| `VIBE_TRADING_ALLOW_SELF_REGISTER` | `1` | `0` = 关闭注册端点（仅 CLI 建号） |

`APIConfig` 位置：`env_schema.py:301-330+`。用 `EnvBool` 类型（同文件既有姿势，如 `:325` `vibe_trading_trust_docker_loopback`）。

### D17. 新增 HTTP 端点

新文件 `agent/src/api/user_auth_routes.py`（沿用 `auth_routes.py:22-38` 的 `register_*(app, deps...)` + `sys.modules` host 解析姿势）：

| 方法 + 路径 | 守卫 | 说明 |
|---|---|---|
| `POST /auth/register` | 公开（需邀请码） | `{username, password, invite_code}` → 201 + session token |
| `POST /auth/login` | 公开 | `{username, password}` → `{token, username, role, display_name}` |
| `GET /auth/mode` | **公开、恒注册** | `{user_auth: bool}` —— 前端 capability 门控（D19），两种模式下都必须存在 |
| `POST /auth/logout` | `require_auth` | 吊销当前 session |
| `GET /auth/me` | `require_auth` | `{username, role, display_name}` |
| `POST /auth/change-password` | `require_auth` | `{old_password, new_password}`，成功后吊销其他 session |

注册在 `agent/api_server.py:306`（现有 `register_auth_routes(app)` 旁）。

⚠️ `api_server.py` 当前 **399 行**，400 practical 线 ⇒ 新增注册代码会越线。可接受（硬上限 800），但需在 PR 描述中说明；或把 `--- Auth helpers ---` 块整体外提。

**错误响应纪律**：登录失败一律返回同一句 `"Invalid username or password"` + 401，**绝不区分「用户不存在」与「密码错误」**（防用户名枚举）。注册时用户名已存在可返回 409（邀请码本身已构成准入屏障）。

### D18. 前端 API 层改造

`frontend/src/lib/apiAuth.ts`（53 行）：
- `STORAGE_KEY`：`vibe_trading_api_auth_key` → `vibe_trading_session_token`
- 新增 `getSessionToken()` / `setSessionToken()` / `clearSessionToken()`
- `authHeaders()`（`:18-21`）逻辑不变（仍是 `Bearer`）
- `withAuthTicket()`（`:36-53`）逻辑不变
- **迁移**：首次加载时若发现旧 key `vibe_trading_api_auth_key`，直接清除（旧共享 key 对新系统无意义）

`frontend/src/lib/api.ts`：
- `:34-36` `isAuthRequiredError()` 保留，但语义改为「会话失效」
- `:295-307` `errorFromResponse()`：401/403 时**不再覆盖成 `agent.authRequired`**，改为触发全局登出 + 跳 `/login`（保留后端 detail 用于 toast）
- 新增 `:503` 附近 `auth` 命名空间：`login()` / `register()` / `logout()` / `me()` / `changePassword()` / `getRuntimeSettings()`
- `frontend/src/pages/Agent.tsx:1495,1584`：移除 `isAuthRequiredError ? AUTH_REQUIRED_MESSAGE : ...` 分支
- `frontend/src/pages/Settings.tsx:83,97`：同上
- `frontend/src/components/settings/QVerisSettings.tsx:5,51`：`authHeaders()` 自动跟随，无需改
- **`frontend/src/hooks/useSSE.ts:149-155`（Oracle NB4）**：ticket 换取返回 401 时当前只会**无限 reconnect**（静默死循环，用户看不到任何提示）。必须把 ticket 401 接入与 `api.ts request()` 同一个 re-auth 通道（清 token + 跳 `/login`），且仅在 flag=1 时如此（D19）

### D19. 前端必须由后端 capability 标志门控（Oracle B3，阻断项）

> **初版遗漏**：计划把前端改造写成无条件的。但 `frontend/dist` 是**运行中的进程按请求从磁盘读取**的（`SPAStaticFiles`，`api_server.py:371-374`），与后端代码/flag 不同步。三个已验证的破坏点：

1. **构建即上线的锁死窗口**：Phase 5 里 `npm run build` 完成的**那一瞬间**，登录门控 UI 就对**旧后端**生效（早于 flag 设置、早于 gateway restart）⇒ 用户被要求登录，而后端还没有 `/auth/login`。若 restart 失败需回滚后端代码，dist 已换 ⇒ 锁死持续。
2. **flag 回滚（dist 不回滚）⇒ 永久死锁**：`RequireAuth` 想探测 `GET /auth/me`，但 **SPA catch-all 会把一切未匹配的 GET 变成 200 + index.html**（`agent/src/api/spa.py:14-20`，已实测确认）⇒ **无法用状态码探测功能是否存在**；改探 `POST /auth/login` 则得到 405（同样歧义）。用户永远卡在登录页，尽管后端已回到 loopback 模式。
3. **无条件删除 `localApiAccessSection`**（`Settings.tsx:289-320,329`）会破坏 flag-off 的远程/LAN 填 key 流程 —— 那是 GHSA-7wgj 的配套 UX ⇒ **不可上游**。同理 Phase 3 里改写 `agent.authRequired` 文案也会改变 flag-off 的消息。

**修复：新增一个未认证、恒注册、返回 JSON 的能力端点。**

```
GET /auth/mode  →  { "user_auth": bool }
```

- 放在新文件 `user_auth_routes.py`，**两种模式下都注册**（flag=0 时返回 `{"user_auth": false}`）
- 无需认证（登录前就要能读）
- **必须是 JSON 端点，不能靠 404 探测**（原因见破坏点 2：SPA catch-all 让 404 不可达）
- 备选：给 `/health`（`system_routes.py:252`，本就未认证）加一个字段。但 `/health` 被 nginx healthcheck、systemd、router 的 wake 轮询（`wake.py:76-118`）消费，改它的响应形状风险更高 ⇒ **选独立的 `/auth/mode`**

**前端全部分支以此为准**：

| 前端行为 | flag=1 | flag=0 |
|---|---|---|
| `RequireAuth` 守卫 | 生效，未登录跳 `/login` | **完全旁路**，直接渲染 Layout |
| `/login` 路由 | 注册 | 可保留但不可达（守卫不跳转） |
| `Settings.tsx` `localApiAccessSection` | 隐藏 | **保留**（GHSA-7wgj 配套 UX） |
| `agent.authRequired` 文案 | 改为「会话已过期，请重新登录」 | **保持原文案不变** |
| 401/403 全局登出 | 触发 | **不触发**（保持今天的 toast 行为） |
| `/settings` NAV 条目 | 仅 admin 可见 | **保持可见** |

⇒ **flag=0 时前端行为与今天逐字节一致**，这是「可上游」的硬前提。

**实现要点**：`/auth/mode` 的结果必须在 app 启动最早期取得（`main.tsx` 里 `RouterProvider` 之前，或 `RequireAuth` 的首次 render 前 await），并缓存到 `stores/auth.ts`。取不到时的默认值必须是 **`false`**（fail-open 到旧行为，绝不把人锁在登录页）。

---

## 2.5 冻结的 API 契约（前后端并行开发的唯一依据）

> **规范性质**：后端（`agent/`）与前端（`frontend/`）按 Phase 1+2 / Phase 3 **并行**委派，零文件重叠。两边必须**逐字节**遵守本契约；任何一侧要改契约，必须先改本节再改代码。
>
> 错误响应统一沿用 FastAPI 既有形状 `{"detail": "<message>"}`（与 `security.py` 现有 403/401 一致）。

### 端点

| 方法 + 路径 | 认证 | 请求体 | 成功响应 | 错误 |
|---|---|---|---|---|
| `GET /auth/mode` | **无** | — | `200 {"user_auth": <bool>}` | 不应失败 |
| `POST /auth/register` | **无** | `{"username","password","invite_code"}` | `201 {"token","username","role","display_name"}` | `400` 校验失败 / 邀请码无效或耗尽；`409` 用户名已存在；`403` 注册已关闭（`VIBE_TRADING_ALLOW_SELF_REGISTER=0`） |
| `POST /auth/login` | **无** | `{"username","password"}` | `200 {"token","username","role","display_name"}` | `401 {"detail":"Invalid username or password"}` —— **统一文案，绝不区分用户不存在/密码错误** |
| `POST /auth/logout` | Bearer | — | `204`（无 body） | `401` |
| `GET /auth/me` | Bearer | — | `200 {"username","role","display_name"}` | `401` |
| `POST /auth/change-password` | Bearer | `{"old_password","new_password"}` | `204` | `400`（旧密码不匹配 **或** 新密码不合规）；`401`（会话本身被拒，来自 `require_auth` 依赖）。**为何旧密码错是 400 不是 401**：前端持有「401 ⇒ 会话已失效，立即销毁并跳登录」的全局不变量（`api.ts errorFromResponse()` 仅对 401 触发 `expireSession()`），该不变量必须对我们自己的端点同样成立——调用方已通过认证、会话完全有效，请求体里的错误值是校验失败（400），不是认证失败（401）；返回 401 会把一个健康会话误杀 |
| `GET /settings/runtime` | Bearer（任意已登录用户） | — | `200 {"provider","model_name","sse_timeout_seconds"}` | `401`/`403` |

### 字段契约

- `token`：`secrets.token_urlsafe(32)` 的**明文**，仅在本次响应中出现一次（DB 只存 sha256）。前端存 localStorage `vibe_trading_session_token`，经 `Authorization: Bearer <token>` 发送。
- `role`：`"user"` | `"admin"`，字符串字面量，前端据此决定 `/settings` NAV 与路由可见性。
- `username`：**lowercase 归一后**的值（D4）。
- `display_name`：用户提交的原始大小写，仅用于 UI 展示；可能为 `null`（此时前端回退显示 `username`）。
- `sse_timeout_seconds`：整数秒。前端 `Agent.tsx:1366` 乘 1000 存入 `sseTimeoutMsRef`；取不到时保持默认 `90_000`。

### 校验规则（后端强制，前端仅做 UX 提示）

- `username`：`^[A-Za-z0-9_-]{3,32}$`，入库前 `.strip().lower()`
- `password`：最少 8 字符，无复杂度强制（邀请码已是准入屏障）；最长 128 字符（防 scrypt 输入放大）
- `invite_code`：非空字符串；校验用 sha256 比对

### `/auth/mode` 的特殊约束（D19）

- **两种 flag 模式下都必须注册**，flag=0 时返回 `{"user_auth": false}`
- **必须未认证可达**（登录前就要能读）
- **必须是 JSON 端点**：前端**不能**靠 404 探测能力存在与否，因为 SPA catch-all（`agent/src/api/spa.py:14-20`）会把一切未匹配 GET 变成 `200 + index.html`
- 前端取不到该端点时**默认按 `false` 处理**（fail-open 到旧行为，绝不把人锁在登录页）

### 并行委派切分

| Agent | 范围 | 目录 | 依赖 |
|---|---|---|---|
| **A（后端）** | Phase 1 + Phase 2 合并 | `agent/` | 无（本契约即输入） |
| **B（前端）** | Phase 3 | `frontend/` | 无（本契约即输入，用 mock 测试） |

**为什么不按 Phase 1/2 切**：两者都改 `agent/api_server.py`（注册 + 注入），且 Phase 2 依赖 Phase 1 产出的 `admin_auth.require_admin` ⇒ 会撞车。按目录切则零文件重叠。

**集成点（我负责，不委派）**：Phase 4 联调 + Phase 5 部署。

## 3. 行数纪律（`CONTRIBUTING.md:153`：400 practical / 800 hard cap）

| 文件 | 当前 | 策略 |
|---|---|---|
| `agent/src/api/settings_routes.py` | **797** | **零新增**，靠 D7 依赖注入 |
| `frontend/src/pages/Settings.tsx` | **782** | **净减少**（删 localApiAccessSection ~32 行） |
| `agent/src/api/security.py` | 669 | +~45（session 分支 + 条件化 loopback），仍在 cap 内 |
| `agent/src/api/system_routes.py` | 468 | **不改动**（限流已移出范围，`_SlidingWindowRateLimiter` 留在原处） |
| `agent/api_server.py` | 399 | +~10（越 400 practical 线，PR 中说明） |
| `agent/src/session/models.py` | 359 | +~8 |
| `frontend/src/components/layout/Layout.tsx` | 459 | +~25（role 过滤 + 用户菜单） |

**全部新文件**（每个 ≤400 行）：
- `agent/src/api/user_store.py` — SQLite DAO（users/sessions/invites）
- `agent/src/api/password_hashing.py` — scrypt 哈希/校验
- `agent/src/api/user_auth_routes.py` — register/login/logout/me/change-password
- `agent/src/api/admin_auth.py` — `require_admin`
- `agent/src/api/runtime_settings_routes.py` — `GET /settings/runtime`
- `agent/src/api/user_admin.py` — 管理 CLI
- `frontend/src/pages/Login.tsx`
- `frontend/src/components/auth/RequireAuth.tsx`
- `frontend/src/stores/auth.ts`

---

## 4. 安全事项（必须在 PR 描述中显式声明）

1. **🔴 TLS 缺失**：入口是 `http://<ECS_PUBLIC_IP>:4096` 明文 HTTP。改成真实用户名/密码后，密码在公网明文传输，而用户普遍复用密码。你们另一套栈已有 `<EXISTING_TLS_DOMAIN>`（`nginx -T` 实测 :443 ssl 已存在）⇒ 建议申请子域（如 `<VT_TLS_SUBDOMAIN>`）+ Let's Encrypt。**`security.py:238-241` 明确把 HSTS 责任推给 TLS 终止层**，上了 TLS 后应在 nginx 补 HSTS。
   - 若本轮不做 TLS，必须在登录页显示明文传输警告，且**不得开放公网注册**。
2. **🔴 拆 nginx Basic Auth 与 `USER_AUTH=1` 必须同批上线**（§0.5）。两种错误做法的后果见 §0.5 表格。顺序：先部署代码 + 设 `USER_AUTH=1` + 建 admin + 验证登录 → **再**删 `auth_basic` 与 XFF 谎报。反序会造成公网裸奔窗口或全员 403。
3. **scrypt 是未认证的 CPU/内存放大器**：本轮不做限流（用户裁决），故 `POST /auth/login` 可被无凭证触发 scrypt（`n=2**14`，~16MB/次）。**显式接受此风险**；若将来需要补救，最低成本是在 nginx 层加 `limit_req_zone`（无需改应用代码）。详见 D11。
4. **XFF 信任边界**：uvicorn `forwarded_allow_ips` 当前依赖默认值 `127.0.0.1`。Phase 0 遗留待办中的 `203.0.113.9`（TEST-NET-3 文档保留段）疑为伪造 XFF 的扫描器 ⇒ 建议按 D14 显式传 `--forwarded-allow-ips=127.0.0.1`，把隐式依赖变成显式契约。
5. **用户名枚举**：D17 的统一错误文案（与限流无关，保留）。
6. **邀请码爆破**：`token_urlsafe(16)` = 128 bit，熵足够；无限流下仍可高速尝试，但 128 bit 空间使暴力破解不可行。
7. **session 固定/重放**：登录成功后**必须新发 token**（不复用登录前任何状态）；改密码后吊销其他 session。
8. **审计**：登录成功/失败、注册、邀请码使用、admin 操作应写日志，且**绝不记录密码/token 明文**。注意 `security.py:264-296` 已有 `_AccessLogRedactionFilter` 会脱敏 `api_key=`/`ticket=` query 参数 —— 新增的 token 若走 query 需扩展该正则（本设计 token 只走 header，故不需要）。
9. **CSP**：`security.py:195-205` `connect-src 'self'` ⇒ 登录页所有请求必须同源，符合本设计（`api.ts:9` `BASE = ""`）。
10. **审计日志真实性**：改造前 gateway 日志里所有客户端 IP 都是 nginx 谎报的 `127.0.0.1`（§0.2）。D14 改为真实 XFF 后，日志才第一次具备取证价值 —— 这也是本轮的附带收益。

---

## 4.5 Oracle 复审采纳记录（Revision 2，2026-09-19）

Oracle 判定 **SOUND WITH REQUIRED CHANGES**，6 个阻断项。逐条核实与处置：

| # | Oracle 发现 | 我的核实 | 处置 |
|---|---|---|---|
| **B1** | §0.2 机制判断错误：uvicorn `proxy_headers` 默认 True，XFF hack 是承重墙不是空操作 | ✅ 生产环境实测 `proxy_headers default = True`；lock 钉 `uvicorn==0.52.4`（`requirements-lock.txt:3845`） | §0.2 已重写（我独立发现并先于 Oracle 更正）；D14 改用 `$remote_addr` **覆盖**而非 `$proxy_add_x_forwarded_for` **追加**；Phase 0 补 `ss -tlnp` / `FORWARDED_ALLOW_IPS` 实测 |
| **B2** | `require_admin` 必须 flag-aware，否则破坏桌面端（`desktop/electron/src/main.ts:94` 注入 Bearer）与本地 dev | ✅ 实测 `main.ts:94` 确有 `details.requestHeaders.Authorization = Bearer ${apiAuthKey}` | D7 重写为 flag-aware；flag=1 时额外放行 SHARED_KEY 作 break-glass |
| **B3** | 前端必须由后端 capability 标志门控；SPA catch-all 使 404 探测不可用 ⇒ flag 回滚会永久死锁 | ✅ 我已独立读过 `spa.py:14-20`（404 → index.html），但**未意识到它会让能力探测失效** —— 这是 Oracle 的独立贡献 | 新增 **D19**：`GET /auth/mode` 未认证恒注册 JSON 端点；前端 6 项行为全部按 flag 分支；取不到时默认 `false`（fail-open 到旧行为） |
| **B4** | 限流问题六处自相矛盾 | ✅ grep 确认 | 已全部清扫（§3 表 / 新文件清单 / Phase 1 / Phase 4 / D17 表 / §4.3-4.4 / D2）；nginx `limit_req_zone` 从 D11 备注**升格为 Phase 5 部署步骤** |
| **B5** | 会话校验必须 JOIN users（`deactivate` 是 `is_active=0`，行还在，CASCADE 不触发 ⇒ 停用用户 session 最长再活 7 天）；步骤 2 必须 gate 在 flag=1 | ✅ 逻辑成立，我的 D4 schema 确实只写了 CASCADE | 新增 **D1.1**：固定 JOIN 查询 + role 同源取出 + 滑动续期 60s 节流；D5 步骤 2 改为 gate 在 flag=1（修正初版「flag=0 时步骤 2 仍生效」的自相矛盾） |
| **B6** | Phase 5 缺 nginx 备份；§7 回滚引用了从未创建的 `.pre-userauth-bak`；且「flag=0 ⇒ 回到 loopback」只在 D14 之前成立 | ✅ 确认 §7 引用了不存在的备份 | Phase 5 补 3 份备份（应用 / nginx / dist）；§7 重写为**分段回滚表** |

**Oracle 提出的更简设计（§四）已整体采纳** —— 这是本次复审最大的价值：

> 用「启动期不变量」替代「5 处 loopback 条件化」作为承重机制。因为现有 key-first 优先级（GHSA-7wgj，`security.py:492-497`）在 key 已设时**已经**让 loopback 信任在全部 5 个调用点失效 —— 这是现成的、被 9 个回归测试钉死的机制。

采纳后：`security.py` diff 从「+45 行 / 5 函数」收缩到 **~15 行 / 1-2 函数**；危险象限（flag off + key 空 + basic auth 已删）被启动期不变量**从设计上排除**；`security.py` 是上游最热文件（GHSA 修复落点）且本分支每周 rebase ⇒ 改动面减半直接降低冲突概率。详见 D5。

**Non-blocking 采纳清单**：

| # | 发现 | 处置 |
|---|---|---|
| NB1 | `connection_routes.py:44` / `portfolio_routes.py:121` **无注入参数**，初版 D7 注入方案对这两个文件行不通 | ✅ 实测确认。改为让 `require_settings_write_auth` **本体** flag-aware，自动覆盖 connection/portfolio/qveris 三处写端点（零文件改动）。见 D5/D7 |
| NB2 | 滑动过期每请求一次写事务，SSE+轮询下每秒数十次 | ✅ 纳入 D1.1：>60s 才写 |
| NB3 | 权限面不一致：settings 锁 admin，但 `/mandate/commit`、`/live/*`、`/channels/start\|stop\|pairing`、`/scheduled/*` 对任何登录用户开放 | ⚠️ **待用户裁决**（见 D7 末尾）。三者 register 签名均支持注入（`live_routes.py:742`、`channels_routes.py:57`、`scheduled_routes.py:361`，已实测）⇒ 锁 admin 是零文件改动 |
| NB4 | SSE ticket 换取 401 时 `useSSE.ts:149-155` 只会无限 reconnect；D18 的全局登出只覆盖 `api.ts request()` | ✅ 纳入 D18：ticket 401 接入同一 re-auth 通道 |
| NB5 | OpenBB bridge `/v1/query` 走 host `require_auth`（`openbb_bridge/routes.py:118`），flag on 时静态配置的 session token 7 天过期 | ✅ 纳入 D15：给 OpenBB 配 shared key，或文档化降级。`/agents.json` 未认证不受影响 |
| NB6 | flag on 且 key 未设 ⇒ `/system/shutdown` 永久 403、CLI 控制面全灭 | ✅ 被 D5 启动期不变量直接排除（该组合拒绝启动） |
| NB7 | 运维细节：systemd `User=` 与 `create-admin` 执行者须一致；username 应 lowercase 归一（SQLite UNIQUE 字节精确，`Alice`/`alice` 视觉冒充）；`hashlib.scrypt` 加 import smoke guard（上游 CI 含 Windows）；`python -m src.api.user_admin` 需 `cwd=agent/` | ✅ `User=root` 已实测并写入 Phase 0/Phase 5；其余三项纳入 D2/D4/D10 |
| NB8 | MCP / cron / T11 结论复核无误（`mcp_server.py:148,3254` 自带 host allowlist 且默认 bind 127.0.0.1，生产为 serve 的 stdio 子进程不经 gateway；cron 直连 serve:4098；T11 dormant） | ✅ D15 表格成立，无需修改 |

**我独立发现、Oracle 未提的项**：
- `agent/tests/test_api_infrastructure.py:20` 有 `assert api_server.require_local_or_auth is security.require_local_or_auth` 的**对象身份断言** ⇒ 任何包装/重绑定都会挂。已写入 D5 与 Phase 4 必绿清单。
- 生产 uvicorn **0.48.0** vs lock **0.52.4** 的版本漂移（Oracle 只引用了 lock 值）。
- §0.3 的真实根因（nginx `$host` 剥端口 ⇒ `_origin_matches_request_host` 端口比较失败）与已实施修复 —— Oracle 复审时该行尚未写入计划。

## 5. 执行阶段

### Phase 0 — 生产诊断 ✅ **已完成（2026-09-19 18:41，SSH 直连 server1 实证）**

| 检查项 | 命令 | 结果 |
|---|---|---|
| `API_AUTH_KEY` 是否设置 | `grep -cE '^API_AUTH_KEY=' /opt/my-vibe-trading/.env` | **0**（未设置）⇒ key-first 路径不触发，排除假设 A |
| `VIBE_TRADING_API_KEY` | 同上 | **0** |
| 宿主机 `gateway-start.sh` | `cat` | `serve_main(['--host','127.0.0.1','--port','8081'])`，**未传** `--proxy-headers` ⇒ 走 uvicorn 默认（默认即开启） |
| nginx 生效配置 | `nginx -T` | `auth_basic` + `X-Forwarded-For 127.0.0.1`（字面量）+ `Host $host`，与文件一致 |
| `API_ALLOWED_HOSTS` | `systemctl cat vt-gateway` | 设在 **unit 第 18 行**：`<ECS_PUBLIC_IP>,localhost,127.0.0.1`（不在 `.env`） |
| 故障时间线 | `journalctl -u vt-gateway` | 16:56–16:58 真实公网 IP → 403；**17:01:22** conf mtime 变更 + nginx Reloaded；18:41 外网实测 → `127.0.0.1:0` → **200** |
| 外网端到端 | 本机 Mac（出口 `<OBSERVER_CLIENT_IP>`）curl | 无串码 → **401**（nginx 拦截）；带串码 → `/settings/llm` **200**、`/` **200 text/html**、`/health` **healthy** |

**结论**：
1. **用户报的错根因是 nginx `$host` 剥端口**，不是 API KEY、不是 loopback、不是 XFF。已于 19:00:58 修复（`$host` → `$http_host`），外网实测 `POST /sessions` 201。完整定位过程与对照实验见 **§0.3**。
2. ⚠️ **17:01 那次 XFF 修改并未解决问题** —— 它只修好了 GET，POST 仍 403（日志 17:05/17:18 为证）。初版计划曾误判为「已自愈」，此处更正。
3. **当前「可用」完全依赖 nginx 谎报客户端 IP**（§0.2/§0.5）—— 这正是需求 1/2 要根治的结构性问题，不是可以留下的权宜之计。
4. 四条已排除的假设见 §0.3 表格，实现时勿再回头验证。
5. **两个上游候选缺陷**见 §0.3.1：`_origin_matches_request_host()` 对反代不健壮（影响所有非标准端口部署）、前端 401/403 文案覆盖（系统性误导运维）。后者已纳入 D18。

**Phase 0 补充实测（Oracle B1.4 要求，已于 19:20 完成）**：

| 检查项 | 命令 | 结果 |
|---|---|---|
| vt-gateway 实际 bind | `ss -tlnp` | **`127.0.0.1:8081`**（与 `DEPLOYMENT:43` 一致；与仓库 `gateway-start.sh` 的 `0.0.0.0` **矛盾** —— 宿主机版脚本用 `GATEWAY_HOST=127.0.0.1`） |
| 公网能否绕过 nginx 直连 8081 | `curl http://<ECS_PUBLIC_IP>:8081/health` | **`http=000` 不可达** ✅ 无绕过风险 |
| `FORWARDED_ALLOW_IPS` 是否被设 | `/proc/<pid>/environ` + unit + `.env` | **未设置** ⇒ uvicorn 默认 `127.0.0.1` ⇒ 右到左遍历 ⇒ 当前 XFF 不可欺骗（但 D14 仍改用 `$remote_addr` 覆盖形态，以防将来有人设 `*`） |
| systemd `User=` | `systemctl show -p User` | **root** ⇒ `create-admin` 必须以 root 执行（users.db 0600 属主一致性；WAL 伴生 `-wal`/`-shm`） |
| 生产 uvicorn 版本 | `python -c "import uvicorn"` | **0.48.0**（lock 钉 0.52.4 ⇒ 漂移，记入 `MYMAIN_DIVERGENCE.md`）；实测 `Config.__init__` 的 `proxy_headers` 默认 **True** |
| opencode serve bind | `ss -tlnp` | `127.0.0.1:4098` ✅ 仅回环 |

**遗留待办（非阻塞）**：`203.0.113.9` 在 16:59 出现过一次 `POST /sessions` 403 —— 该 IP 属 TEST-NET-3（RFC 5737 文档保留段），正常互联网流量不应出现此源地址。疑为扫描器伪造 XFF（当时 nginx 尚未强制覆盖 XFF，故伪造值被 uvicorn 采信）。**这恰好证明「信任 XFF 而不显式限定 `forwarded_allow_ips`」的风险**，支持 D14 的可选加固项。

### Phase 1 — 后端认证核心（可独立验证，默认关闭故零风险）
- [ ] `password_hashing.py` + `user_store.py`（D2/D3/D4）
- [ ] `models.py`：`AuthMethod.USER_SESSION` + `ATTRIBUTABLE_AUTH_METHODS` + `Principal.role`（D6/D7.1）；`to_dict()` **不**序列化 role
- [ ] `user_store.py` 的会话校验实现 **D1.1 的 JOIN 查询**（含 `is_active=1` 与 60s 节流续期）—— 这是 B5 阻断项，不是可选优化
- [ ] **D5 启动期不变量**：flag=1 且 `API_AUTH_KEY` 空 ⇒ 拒绝启动（在 `_run_startup_preflight`，`api_server.py:128-144`）
- [ ] `GET /auth/mode` capability 端点（**D19，两种模式下都注册**）—— 前端 Phase 3 依赖它，必须先落地
- [ ] `env_schema.py`：4 个新 env（D16）
- [ ] `security.py`：D5 五步优先级 + 4 个依赖同步改造
- [ ] `admin_auth.py`（D7）
- [ ] `user_auth_routes.py`（D17）+ `api_server.py` 注册
- [ ] `user_admin.py` CLI（D10）
- [ ] **验证**：`pytest agent/tests/test_auth_precedence.py agent/tests/test_security_auth_api.py -q` 全绿（`USER_AUTH=0` 默认路径未变）

### Phase 2 — 后端配置页锁 admin
- [ ] `api_server.py:220` 注入 `require_admin`（D7）
- [ ] `runtime_settings_routes.py`（D8）
- [ ] qveris / connection / portfolio 写端点 admin 化 —— **靠 D5 改 `require_settings_write_auth` 本体自动覆盖**（已实测 `connection_routes.py:44` / `portfolio_routes.py:121` 无注入参数，注入方案对它们行不通）
- [ ] ⚠️ **不得重新绑定 `security.require_local_or_auth`**：`agent/tests/test_api_infrastructure.py:20` 有 `is` 身份断言
- [ ] **验证**：普通用户 token curl `PUT /settings/llm` → 403；admin token → 200

### Phase 3 — 前端
- [ ] **先取 `/auth/mode`**（D19）：在 `main.tsx` 的 `RouterProvider` 之前 await，结果存 `stores/auth.ts`；**取不到时默认 `false`**（fail-open 到旧行为，绝不把人锁在登录页）
- [ ] `apiAuth.ts` 改造（D18）
- [ ] `stores/auth.ts` + `RequireAuth.tsx` + `Login.tsx`（D12）
- [ ] `router.tsx`：`/login` + guard + `/settings` role 守卫（D12/D13）
- [ ] `Layout.tsx`：role 过滤 NAV + 用户菜单（D13）
- [ ] `Settings.tsx`：删 localApiAccessSection（D13）
- [ ] `Agent.tsx:1365` → `/settings/runtime`；`:1495,1584` 移除 authRequired 分支（D8/D18）
- [ ] `api.ts`：401/403 全局登出 + auth 命名空间（D18）
- [ ] **D19 的 6 项 flag 分支全部落实**：RequireAuth 旁路、`localApiAccessSection` 保留、`agent.authRequired` 原文案保留、401/403 不触发全局登出、`/settings` NAV 保持可见 —— **flag=0 时前端行为必须与今天逐字节一致**（可上游的硬前提）
- [ ] `useSSE.ts:149-155` ticket 401 接入 re-auth 通道（Oracle NB4，仅 flag=1）
- [ ] i18n：8 个 locale 新增 login/register/invite 键；删除死键 `settings.authRequired`/`authDesc`（8 locale，en:337-338 / zh:332-333 等）+ `settings.localApiAccess*` / `serverApiKey` / `storedInBrowser` / `localApiKeySaved` / `apiAuthKey`；`agent.authRequired` 改写为「会话已过期，请重新登录」
- [ ] **验证**：`cd frontend && npm run build && npm run test:run`

### Phase 4 — 测试
- [ ] 新增 `agent/tests/test_user_auth.py`：scrypt 往返、session 生命周期/过期/滑动续期节流（D1.1）、**JOIN users 使 deactivate 立即失效**（D1.1/B5）、邀请码单次使用与并发、D5 优先级矩阵（session > shared key > loopback）、**启动期不变量（flag=1 且无 key ⇒ 拒绝启动）**、admin 门禁 flag-aware 两态、用户名枚举防护（统一错误文案）
- [ ] 新增 `agent/tests/test_auth_mode_endpoint.py`：`GET /auth/mode` 未认证可达、两种 flag 下的 JSON 形状、**不被 SPA catch-all 吞成 index.html**（D19）
- [ ] **必须保持绿**：`agent/tests/test_auth_precedence.py`（9 个 GHSA-7wgj 用例）、`agent/tests/test_api_infrastructure.py:20`（`require_local_or_auth` 的 `is` 身份断言）、`agent/tests/test_security_auth_api.py`（734 行）、`agent/tests/test_settings_api.py`（793 行，admin 化后断言需按 flag 分支改写）
- [ ] 更新 `agent/tests/test_settings_api.py`（793 行，admin 化后断言需改）
- [ ] 更新前端测试：`lib/__tests__/api.test.ts:147,152`、`lib/__tests__/apiAuth.test.ts`、`pages/__tests__/SettingsChannels.test.tsx:20,25-26`、`pages/__tests__/SettingsQVeris.test.tsx:21,26-28`、`components/layout/__tests__/Layout.test.tsx:35`（断言 settings 导航存在 → 改为 admin 条件断言）
- [ ] **验证**：`pytest agent/tests/ -q` 全绿；`pytest OpencodeAgent/tests/ -q` 保持 157 passed / 1 skipped

### Phase 5 — 部署（严格按序，见 §4.2）
- [ ] 备份 1（应用）：`cp -a /opt/my-vibe-trading /opt/my-vibe-trading-backup-$(date +%Y%m%d-%H%M%S)`
- [ ] **备份 2（nginx，Oracle B6 —— §7 回滚依赖它，初版遗漏）**：`cp -a /etc/nginx/conf.d/opencode-web.conf{,.pre-userauth-bak}`（注意 nginx conf 在 `/etc/nginx`，**不在**备份 1 里）
- [ ] **备份 3（前端 dist）**：`cp -a /opt/my-vibe-trading/repo/frontend/dist{,.pre-userauth}`（D19 破坏点 1：dist 由运行中进程按请求读盘，构建即上线）
- [ ] 部署代码 + `pip install -e .` + `cd frontend && npm ci && npm run build`
- [ ] systemd unit 加 `Environment=VIBE_TRADING_USER_AUTH=1` **与** `Environment=API_AUTH_KEY=<新生成的长随机值>`（D5 启动期不变量：缺 key 会拒绝启动）。**先不删 nginx basic auth**
- [ ] `create-admin` 可在 restart **之前**执行（只依赖新代码已安装，不依赖服务在跑）⇒ 缩小「flag 已开但无 admin」窗口。⚠️ **必须以 root 执行**（已实测 vt-gateway `User=root`；users.db 0600 属主须一致，WAL 还有 `-wal`/`-shm` 伴生文件）。⚠️ `python -m src.api.user_admin` 需 `cwd=/opt/my-vibe-trading/repo/agent`
- [ ] `python -m src.api.user_admin create-admin <you>` + `invite --uses 10`
- [ ] 重启 `vt-gateway`，用串码 + 新账号双因子验证登录可用
- [ ] **确认登录可用后**，删 nginx `auth_basic` + 删 XFF hack + 改真实 XFF，`nginx -t && systemctl reload nginx`
- [ ] 端到端验证：无串码直接访问 → 跳登录页 → 注册/登录 → 聊天可用 → 看不到配置页 → curl `PUT /settings/llm` 403
- [ ] **nginx 层限流（Oracle B4：从 D11 备注升格为部署步骤 —— 零应用代码，恰好覆盖用户拒绝在应用层处理的 scrypt 放大器 / login / invite 三个面）**：
      `limit_req_zone $binary_remote_addr zone=vt_auth:10m rate=10r/m;` 应用于 `location` 内的 `/auth/login`、`/auth/register`（`limit_req zone=vt_auth burst=5 nodelay;`），其余路径不限。注意此时 `$remote_addr` 是真实客户端 IP（D14 已改）
- [ ] （建议）TLS：子域 + certbot + HSTS
- [ ] 更新 `OpencodeAgent/docs/DEPLOYMENT-PROD-ENGINE-BRIDGE.md`：**修正 §11#8 的错误诊断**，新增用户认证章节
- [ ] 更新 `mymain-wiki/branch/MYMAIN_DIVERGENCE.md`

### Phase 6 — 社区回流（可选，独立 PR）
- [ ] 逐条过 `CONTRIBUTING.md` / `AGENT_CONTRIBUTOR_GUIDE.md` / `SECURITY.md` / `.github/PULL_REQUEST_TEMPLATE.md`
- [ ] `git commit -s`（DCO），**禁止任何 AI trailer**（本仓库 AGENTS.md 第 7 条覆盖全局规则）
- [ ] 卖点：`VIBE_TRADING_USER_AUTH` 默认关闭 ⇒ 对上游零行为变更；填上了 `Principal.attributable` 一直等待的 True case

---

## 6. 明确不做（本轮范围外）

- ❌ 租户数据隔离（sessions/runs/memory/uploads 过滤）——用户裁决共享工作区；`Principal.tenant` 已填好接口，留 Phase 2
- ❌ 启用 T10 容器 / T11 router 生产接线——router 仍 dormant；ECS 唤醒后端是 `raise` 的 stub（`backend.py:116-145`），周期回收循环未消费（`config.py:45`）
- ❌ 邮箱验证 / 找回密码 / 2FA / OAuth 社交登录
- ❌ 用户管理 Web 界面（本轮只有 CLI）
- ❌ per-user LLM 配额 / 计费
- ❌ 修改 ClickHouse / liteLLM / opencode serve 配置

---

## 7. 回滚

| 场景 | 动作 |
|---|---|
**回滚分两段，因为 D14 的 XFF 改动会改变 loopback 信任的成立条件（Oracle B6）：**

| 阶段 | 场景 | 动作 |
|---|---|---|
| **nginx 改动之前** | 用户认证出问题 | unit 里设 `VIBE_TRADING_USER_AUTH=0` + 重启 gateway ⇒ 回到 loopback 信任。**若做了 D19 的 capability 门控，dist 无需回滚**（前端读到 `user_auth:false` 自动旁路登录） |
| **nginx 改动之后** | 同上 | ⚠️ 单设 flag=0 **不够**：D14 已把 XFF 改成真实 IP ⇒ client=公网 IP ⇒ loopback 分支不命中 ⇒ 结果是**全员 403 而不是恢复**。必须 **恢复 nginx 备份**（`cp -a .pre-userauth-bak` → `nginx -t` → reload，basic auth + XFF 谎报一起回来）**再**设 flag=0 + 重启 |
| 任意 | 前端登录页坏了 | `cp -a frontend/dist.pre-userauth frontend/dist`（无需重启，`SPAStaticFiles` 按请求读盘） |
| 全栈回滚 | `cp -a /opt/my-vibe-trading-backup-<ts>/. /opt/my-vibe-trading/` + 重启双服务 |
| users.db 损坏 | 删除后重新 `create-admin`（会话全部失效，用户需重新注册；邀请码需重新生成） |
