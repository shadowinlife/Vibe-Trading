---
title: 用户认证系统（User Auth / 多租户 Phase 1）
description: 本地用户名/密码 + 邀请码注册 + 不透明 session token 的应用层认证，取代原 nginx 共享串码。scrypt 哈希、7 天滑动过期、role admin 锁写端点、前端 capability 门控。改认证逻辑、排查登录/鉴权、准备上游 PR ⑨ 时必读。触发词：用户认证、user-auth、login、邀请码、session token、VIBE_TRADING_USER_AUTH、users.db、admin、require_admin、AuthMethod.USER_SESSION、capability。
type: delta
status: active
created: 2026-09-20
updated: 2026-09-20
tags: [auth, user-auth, multi-tenant, security, session]
related: [../branch/MYMAIN_DIVERGENCE.md, README.md, f8-engine-bridge.md]
---

# 用户认证系统

> 一句话定位：vt 网关的应用层用户认证——每用户账密 + 邀请码注册取代 nginx 共享串码闸门，是 mymain 多租户路线的 Phase 1（仅认证，共享工作区）。
> ⚠️ 本卡不带 F 编号：台账 [../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md) §2.1 的 F8 即本系统；功能卡 `f8-engine-bridge.md` 的 F8 是 opencode 引擎桥。引用一律用名字（用户认证 / engine-bridge），编号双口径见 [README.md](README.md) 专节。

## 能力

- **注册**：邀请码闸门（`VIBE_TRADING_ALLOW_SELF_REGISTER=0` 时仅 admin 可建用户；=1 时开放注册但仍需邀请码）
- **登录**：本地用户名/密码，`hashlib.scrypt` 哈希（禁第三方依赖），返回不透明 session token（DB 只存 sha256）
- **会话**：7 天滑动过期（续期写节流 60s），校验时 JOIN `users.is_active`；`AuthMethod.USER_SESSION` 填充 `Principal.attributable`
- **授权**：role admin 锁 settings/connection/portfolio/qveris/live/channels/scheduled **写端点**（读端点保持 `require_auth`）；非 admin 前端看不到配置页
- **脱敏**：`GET /settings/runtime` 脱敏输出 + capability 端点 `GET /auth/mode`（SPA catch-all 使状态码探测不可能）
- **管理 CLI**：`python -m src.api.user_admin`（创建用户 / 重置密码 / 列出用户 / 停用启用）
- **前端**：登录门禁 / 路由守卫 / NAV 全部按后端 flag 门控，8 locale i18n 齐备

## 关键文件与开关

| 文件 / 开关 | 作用 |
|---|---|
| `agent/src/api/user_store.py` | 用户 CRUD + session token 签发/校验（SQLite `users.db`） |
| `agent/src/api/user_store_schema.py` | DDL + 迁移 |
| `agent/src/api/password_hashing.py` | scrypt 哈希封装 |
| `agent/src/api/user_auth_routes.py` | 认证 REST 端点（login/logout/register/me） |
| `agent/src/api/admin_auth.py` | admin 角色守卫 |
| `agent/src/api/runtime_settings_routes.py` | 脱敏 runtime settings + capability |
| `agent/src/api/user_admin.py` | 管理 CLI |
| `agent/src/api/security.py` | session 分支 + 启动期不变量（~15 行） |
| `agent/src/config/env_schema.py` | 4 个 env 声明 |
| `agent/src/session/models.py` | `AuthMethod.USER_SESSION` |
| `frontend/` | Login 页 / RequireAuth / auth store / 8 locale |
| `VIBE_TRADING_USER_AUTH` | **总开关**（默认 `0` ⇒ 行为逐字节不变、零 SQLite 查询） |
| `VIBE_TRADING_USERS_DB_PATH` | users.db 路径覆盖 |
| `VIBE_TRADING_SESSION_TTL_DAYS` | session 过期天数（默认 7） |
| `VIBE_TRADING_ALLOW_SELF_REGISTER` | 开放注册开关（默认 0） |

## 启动期不变量

flag=1 且 `API_AUTH_KEY` 为空 ⇒ **拒绝启动**（设计排除「公网裸奔」误配置象限）。key-first 优先级（GHSA-7wgj）使已设 `API_AUTH_KEY` 时 loopback 信任在全部调用点自动失效。

## 开发历史

- 2026-09-19 设计 + 实现（计划正典 `.omo/plans/vibe-trading-user-auth.md`；commit `7a7abfd8` 后端 / `43e297a6` / `ba86f453` 前端 / `27e32932` docs）
- 2026-09-20 **已部署生产并端到端验证**（`9cc51e9a`；实录 `OpencodeAgent/docs/DEPLOYMENT-PROD-ENGINE-BRIDGE.md` §15 与 §15.11）
- 2026-09-20 功能卡创建（本卡），补 features/ 下的长期缺口

## 验证

- F8 新套件 **35 passed**（`test_user_auth.py` + `test_user_auth_api.py` + `test_auth_mode_endpoint.py`）
- 认证优先级 **9 passed**（`test_auth_precedence.py`，GHSA-7wgj pin，flag=0 默认路径逐字节不变）
- API 基础设施 **42 passed**（`test_api_infrastructure.py`）
- 全量门禁 **14117 passed / 119 skipped / 0 failed**（2026-09-19 基线，DIVERGENCE §3.1）
- 前端 **70 files / 671 passed** + build 干净
- 生产端到端验证：登录 → 邀请码注册 → session 续期 → admin 写端点锁 → XFF 伪造防护（DEPLOYMENT §15.11）

## 已知遗留（勿误当已完成）

| 项 | 状态 | 说明 |
|---|---|---|
| `/auth/login` 限流 | **未实现** | 用户裁决延后（计划 D11） |
| TLS | **未实现** | 明文 HTTP，延后（计划 §4.3） |
| 多租户 per-tenant 数据隔离 | **未实现** | `Principal.tenant` 已填充但零过滤，属 Phase 2 |
| 前端 401/403 文案覆盖 | 分支已修（D18） | 上游候选缺陷 ⑪，PR 需剥离为独立修复 |

## 状态与上游关系

- **可上游**（贡献队列 ⑨，DIVERGENCE §2.3）；默认关零行为变更是卖点
- 上游提交前置：`session/models.py` 的 `ATTRIBUTABLE_AUTH_METHODS` 自重绑定须改写为普通字面成员（DIVERGENCE §3.1 末尾维护注记）
- 2026-09-20 已部署生产（server1）；上游 PR 待提交
