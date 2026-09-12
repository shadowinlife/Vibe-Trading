---
title: 多租户 opencode+OMO+VT-MCP 能力差距分析
description: 多租户服务化的权威差距台账——前端/IM Channel/基本能力三域差异表 + 运维/安全缺口 + 优先级路线。触发词：多租户、multi-tenant、租户隔离、invest-assistant、Daytona、litellm、沙箱、IM 网关、opencode-serve。
type: research
status: active
created: 2026-09-09
updated: 2026-09-09
tags: [multitenant, gap-analysis, serving, security, ops]
---

# 多租户 `opencode + oh-my-openagent + vibe-trading MCP` 能力差距分析

> 时点分析：2026-09-09。基于四路代码勘察（mymain-wiki 文档 / mymain 服务化代码 /
> invest-assistant 前后端 / oh-my-openagent 源码）+ llm-proxy 仓库核实。
> 差距条目有稳定编号（FE-/IM-/BASE-/SEC-/OPS-），后续会话按编号引用与销项。

## 0. 结论摘要

1. **架构方向已对**：mymain 裁决生产形态 = opencode harness + VT MCP（PR #1286 终局：
   built-in loop 子代理移植放弃）；OMO 源码结论：单进程/单密钥/loopback 设计，
   **租户边界只能是进程/容器边界**。invest-assistant 的 "1 用户 = 1 Daytona 沙箱"
   正是这个模型的落地，计算层路由隔离结构性成立。
2. **核心矛盾：三块拼图没接线**。litellm 多租户网关（per-tenant 虚拟 key + 预算 +
   审计）与 ClickHouse `llm_role` 隔离均已建成，但 invest-assistant 给**所有**沙箱
   注入同一个 `SANDBOX_ENV_DASHSCOPE_API_KEY` 和同一套 CH 凭据——租户在沙箱内
   `env` 即可拿到真实共享 key，**已建成的隔离被整体旁路**（SEC-1，P0）。
3. 差距不是 "0→1 造多租户"，而是 "接通已有隔离基建 + 补管理面/计量/IM 网关"。

## 1. 现状基线

### 1.1 目标链路（invest-assistant 已实现的骨架）

```
Next.js 前端(assistant-ui + react-opencode)
  → FastAPI JWT 网关(access+refresh/Redis 黑名单/轮换/弱密钥防护)
  → Daytona 每用户沙箱(1:1，DB 唯一索引，按 user_id 路由 opencode_url)
  → 沙箱内 opencode serve :4096(镜像来自外部 gitee 仓库 opencode-serve 构建的
    Daytona 快照 opencode-server:1.0.0；opencode-serve 理解为 opencode 中转层)
```

- SSE 字节透传（`backend/app/api/opencode_proxy.py`）；SSRF guard 仅护后端出站
  （`backend/app/proxy.py`）。
- 关键历史事实：后端**曾有** Session 模型（含 total_tokens/total_cost）与
  EventPipeline（SSE 解析），后被刻意删除改为透明代理——孤儿测试
  `backend/tests/test_session_service.py`、`test_event_pipeline.py` import
  不存在的模块，CI 收集即红（OPS-6）。

### 1.2 mymain 侧现状（单租户生产）

- OpencodeAgent：nginx 共享口令门(:4096) → loopback `opencode web`(:4097) →
  VT MCP(stdio 子进程) → ClickHouse `llm_role`(VPC)。单机 ECS，
  `OpencodeAgent/docs/DEPLOY-GUIDE.md` 是唯一部署架构文档。
- VT 原生 server（上游能力，mymain 未用于生产）：`vibe-trading serve` FastAPI +
  React 前端 12 页 + 16 IM adapter channel runtime + MCP HTTP。
- 多租户钩子已预留但未接线：`agent/src/session/models.py` 的
  `Principal.tenant`（注释明言 "for future multi-tenant runtime root"）、
  `AuthMethod.FEDERATED_IDENTITY`（标注 unreachable）、`attributable` 恒 False。

### 1.3 已有隔离资产

| 资产 | 位置 | 能力 |
|---|---|---|
| litellm 多租户网关 | `/Users/mgong/myrepo/llm-proxy/litellm/` | per-tenant 虚拟 `sk-` key、预算/RPM/TPM、`tenant_id` metadata、全量 Q&A 审计落库 `audit.usage_record`（含 reasoning/cached_tokens）、Admin UI :4000/ui；`provision_tenants.sh` 为手动脚本 |
| ClickHouse 隔离 | mymain F5 + `clickhouse/CLICKHOUSE_ITERATION_PLAN.md` | `llm_role` SELECT-only + sqlglot AST 守卫 + 资源配额(30s/2GB/1M rows/50MB)，隔离 tushare key |
| VT channel runtime | `agent/src/channels/`（16 adapter + pairing + deliveryTargets） | 可独立部署为 IM 网关的完整件 |
| VT 金融前端 | mymain `frontend/`（React 19，RunDetail/Compare/Portfolio/Correlation/AlphaZoo/OptionsLab/Reports 等 12 页） | 金融可视化组件移植源 |
| 治理原语 | `agent/src/governance/ledger.py`（hash-chained 审计账本）、OpencodeAgent tool-deny manifest（`config/vibe-trading-tools.json` → permission deny） | 可参数化为 per-tenant 策略 |

## 2. 能力差异表

### 2.1 前端差距（FE-）

| # | 能力项 | 现状 | 差距 | 可复用资产 |
|---|---|---|---|---|
| FE-1 | 金融可视化 | invest-assistant 前端为纯通用 coding-agent 聊天，grep backtest/chart/report/portfolio = 0 命中 | 缺回测视图、K线/图表、报告渲染、组合视图、Run 对比 | VT mymain `frontend/` 12 页组件移植，或聊天流内渲染 VT artifact |
| FE-2 | Artifact 浏览 | 回测报告/HTML 产物只在沙箱文件系统内，前端无触达通道 | 缺 artifact 画廊/预览/下载（代理沙箱内 run 产物） | VT `/runs/{id}` API 模式、OpenBB bridge 先例 |
| FE-3 | 会话连续性 | 会话/历史只活在临时沙箱内；沙箱重建=全丢；无服务端注册表 | 缺服务端会话登记、跨设备历史、跨会话搜索 | 被删的旧 Session 模型可从 git 历史恢复改造 |
| FE-4 | 租户/管理界面 | 无 admin UI；`role` 字段全代码库零处 enforce | 缺用户管理、用量/成本仪表盘、租户切换、封禁/配额操作面 | litellm Admin UI 可作 LLM 侧管理面 |
| FE-5 | 登录体系 | 自助 email+password；Dex OIDC 只护 Daytona 面板，与后端 JWT 两套断开 | 缺统一 SSO（后端接 OIDC）、企业身份（钉钉扫码与 IM 场景契合） | Dex 已在 compose 内 |
| FE-6 | 前端安全姿态 | JWT/refresh 存 localStorage（XSS 面）；react-opencode 靠 vendored patch（`frontend/patches/@assistant-ui+react-opencode+0.2.20.patch`，升级脆弱） | httpOnly cookie 化；patch 上游化或锁定策略 | — |

### 2.2 IM Channel 差距（IM-）

| # | 能力项 | 现状 | 差距 | 可复用资产 |
|---|---|---|---|---|
| IM-1 | 双向 IM ↔ 租户沙箱 | 生产 harness 只有单向 DingTalk webhook cron 通知；opencode/OMO 侧无 IM 概念 | 缺 "IM 消息→识别用户→路由到其沙箱→流式回推" 完整双向链路 | VT 16 adapter channel runtime 独立部署为 IM 网关服务，前置到多租户调度器 |
| IM-2 | IM 身份 ↔ 租户映射 | VT session key = `channel:chat_id`（按聊天不按用户）；群聊共享会话；`pairing.json` 全局单文件；invest-assistant User 表有 `dingtalk_staff_id` 字段但无消费者 | 缺 sender→tenant→sandbox 映射存储；群聊需 per-user 拆分或 "群=租户" 策略 | `dingtalk_staff_id` 已埋；VT pairing approve/deny/revoke 可扩展为租户开通审批流 |
| IM-3 | IM 触发沙箱生命周期 | 沙箱靠前端 polling 创建；IM 首消息时沙箱可能不存在/已停 | 缺冷启动编排（首消息触发 create/start + 排队提示 + 超时兜底） | `sandbox_service.py` 创建状态机 |
| IM-4 | IM 侧防滥用 | 无 | per-sender 限流、配对码防爆破、消息长度/频率限制 | VT `allow_from` + pairing code；litellm RPM/TPM 兜底成本 |
| IM-5 | 定时研报推送（多租户版） | OpencodeAgent cron+钉钉 webhook 是运营者单例 | 缺 per-tenant 订阅/推送目标管理 | VT `scheduled_research` + `deliveryTargets`（opaque ref 不泄露原始 chat id，天然适合多租户） |

### 2.3 基本能力差距（BASE-）

| # | 能力项 | 现状 | 差距 | 可复用资产 |
|---|---|---|---|---|
| BASE-1 | 租户模型 | invest-assistant 只有 `User`，无 org/tenant 层级、无 membership；VT `Principal.tenant` 占位未接线 | 缺租户实体、租户级配置、RBAC | 两侧数据模型钩子均已预留（接线非重写） |
| BASE-2 | Per-tenant 凭据注入 | 所有沙箱共享同一 DASHSCOPE key + 同一 CH 凭据（静态 env，`backend/app/config.py:118-134`） | 缺 provisioning 流水线：建租户→litellm 发虚拟 key→CH 建 per-tenant user/quota→注入沙箱 env | `litellm/provision_tenants.sh`（手动版）；CH `llm_role` resource profile 模式 |
| BASE-3 | 计量/计费 | 后端 token/cost accounting 曾存在后被删；SSE 字节透传网关无法观测事件 | 缺 per-tenant 用量聚合、账单、超额动作 | litellm `audit.usage_record` 原始数据 + `tenant_id` metadata，聚合层纯增量开发 |
| BASE-4 | 配额/限流 | `/api/opencode/*` 代理路径零限流（slowapi 只护 auth 端点）；VT 全局 4-worker 线程池 | 网关级 per-tenant 并发/请求限流；沙箱级资源 quota 策略 | litellm 预算/RPM/TPM（需接 per-tenant key 才生效） |
| BASE-5 | 沙箱生命周期 | `auto_stop/archive/delete = 0` → 沙箱永存；`last_active_at` 有列无消费者 | 缺 idle reaper、冷/热分层、快照恢复、成本上限联动 | Daytona auto_* 参数已预留 |
| BASE-6 | 租户状态持久化 | 沙箱无持久卷：`.vt-memory`、研究状态、会话历史随沙箱销毁 | per-tenant volume/对象存储 + 备份恢复；VT 记忆系统需按租户分区 | `VT_MEMORY_BASE_DIR`/`VIBE_TRADING_HOME` 可重定位设计 |
| BASE-7 | OMO 网络模型适配 | OMO 硬编码 `127.0.0.1` spawn；非 loopback attach 不注入 Basic auth（`omo-opencode/src/cli/run/server-connection.ts:95-97`）；多项目模式 session 列表故意去过滤（全部可见，`tools/session-manager/storage.ts:11-17`） | 容器网络需 sidecar 或 patch；网关必须屏蔽 opencode list-all session 端点 | per-container `OPENCODE_SERVER_PASSWORD` 天然成为 per-tenant bearer，OMO 无需改码即支持一容器一租户 |
| BASE-8 | 工具治理 per-tenant | OpencodeAgent tool-deny manifest 全局单份 | 缺租户级工具策略（如某租户禁交易类工具/开放 shell） | render_config 管线参数化 tenant profile |
| BASE-9 | 已知债务多租户外爆 | D1: MemoryGuard 无 env 开关强制注册；D2: dedup 坏/GC 关→无界磁盘增长（DIVERGENCE §4.5）；CH Phase 3 sync 管道不在 git | 每沙箱一份无界增长 guard store = 多租户磁盘炸弹 | 债务已记录，修复方案明确 |

## 3. 安全缺口（SEC-，按严重度排序）

| # | 级别 | 缺口 | 证据 | 修复方向 |
|---|---|---|---|---|
| SEC-1 | **P0** | 共享真实 key 注入所有沙箱，旁路 litellm/CH 全部隔离 | `config.py:118-134` + compose `SANDBOX_ENV_*` | 沙箱只注入 per-tenant litellm 虚拟 key + per-tenant CH 凭据；真实 key 只存在于 litellm/同步管道侧 |
| SEC-2 | **P0** | 沙箱可被直连绕过 JWT 网关：`public=True` + preview URL，沙箱内 opencode 无 auth（后端转发剥离 Authorization） | `sandbox_service.py:168`、`opencode_proxy.py:26,80` | 每沙箱启用 `OPENCODE_SERVER_PASSWORD`（网关持映射）+ 关 public preview / 网络策略只允许 daytona-proxy 入站 |
| SEC-3 | **P0** | 沙箱无 egress 管控：SSRF guard 只护后端出站；VT injection scanner 仅 advisory 不阻断 → prompt injection 后数据外传通道 | `proxy.py`、`agent/src/security/scanner.py` | 沙箱出口白名单（只放行 litellm/CH/必要数据源）；scanner 结果升级为策略输入 |
| SEC-4 | P1 | 无 RBAC/admin 权限分离；`role` 零 enforce；无审计日志（谁访问谁的沙箱、admin 操作无痕） | `backend/app/api/auth.py:128` | 后端接 Dex OIDC + role 中间件 + 审计表；VT hash-chained ledger 可移植为租户操作审计 |
| SEC-5 | P1 | 静态弱秘密：Dex 提交 dev 账号 `dev@daytona.io/password`；README 列默认 secret；无轮换机制 | `docker/dex/config.yaml:23-29` | secret 入 vault/环境注入 + 轮换 runbook；litellm 虚拟 key 轮换流程化 |
| SEC-6 | P1 | 代理路径无限流 → per-tenant DoS/成本攻击面 | `opencode_proxy.py` | slowapi 扩展到代理路径 + litellm 预算硬顶联动 |
| SEC-7 | P2 | JWT 存 localStorage；db:5432/redis:6379/registry:6000 发布到 host；无 TLS 终止（README 假设 nginx 但 compose 无） | compose、`lib/auth-store.ts:146` | httpOnly cookie、端口收敛、nginx/TLS 边缘 |
| SEC-8 | P2 | 供应链无 provenance：opencode-serve 外部 gitee 仓库手工 clone 构建快照 + vendored react-opencode patch | invest-assistant README:76-88 | 快照镜像 digest 锁定 + 构建入自有 CI + 签名 |

## 4. 运维缺口（OPS-）

| # | 级别 | 缺口 | 证据 | 修复方向 |
|---|---|---|---|---|
| OPS-1 | **P0** | 沙箱永不回收 → 成本泄漏 | `sandbox_service.py:171-173`（auto_* 全 0，last_active_at 无消费者） | idle reaper job + 冷热分层（活跃=运行/闲置=archive/深度闲置=delete+快照） |
| OPS-2 | **P0** | 无管理面：不能列租户/沙箱/会话、kill 沙箱、封禁用户、重置配额 | 全代码库无 admin 端点 | admin API + 最小运维 UI（可先 CLI） |
| OPS-3 | P1 | 可观测性缺位：OTEL disabled、无 per-tenant 用量 dashboard、沙箱内日志随销毁丢失、litellm 审计库无告警 | compose/`main.py` | 日志集中采集 + usage_record 按 tenant 聚合报表 + 预算告警动作 |
| OPS-4 | P1 | 发布工程断裂：opencode-serve 快照/VT MCP/OMO/skills 各自手工 pin；无灰度回滚（DEPLOY-GUIDE 回滚表是单机 nginx+systemd 版） | DEPLOY-GUIDE.md | 镜像版本矩阵 + 快照版本化 + 分批滚动（先 10% 租户） |
| OPS-5 | P1 | 备份/DR 空白：Postgres/Redis/CH 无备份策略；租户数据无导出 | — | 定时备份 + 租户数据导出 API（合规也需要） |
| OPS-6 | P2 | CI 红：两个孤儿测试 ImportError（session_service/event_pipeline 模块已删） | `backend/tests/` | 与 FE-3/BASE-3 联动决策：恢复服务端会话层则重写测试，否则删除 |
| OPS-7 | P2 | 容量规划：Daytona 单 runner，无沙箱池预热、无节点扩容策略 | compose 单 runner | 预热池（降冷启动）+ runner 水平扩展评估 |

## 5. 优先级路线（建议顺序）

1. **P0 安全接线**：BASE-2 provisioning 流水线（`provision_tenants.sh` 接进
   sandbox 创建流程）→ 消解 SEC-1；SEC-2 沙箱侧 auth + 关 public；SEC-3 egress 白名单。
2. **P0 运维止血**：OPS-1 idle reaper + auto_* 策略；OPS-2 最小 admin API。
3. **P1 多租户核心闭环**：FE-3/BASE-3 恢复服务端会话注册表 + 计量（顺带修 OPS-6）；
   BASE-4/SEC-6 代理限流；FE-5/SEC-4 后端接 Dex OIDC 统一身份 + RBAC。
4. **P1 前端补强**：FE-1/FE-2 移植 VT 金融可视化组件进聊天流 + artifact 代理通道。
5. **P2 IM 网关**：IM-1~IM-5，VT channel runtime 独立部署 + `dingtalk_staff_id→
   tenant→sandbox` 映射 + pairing 流做租户开通审批 + IM 首消息冷启动。
6. **持续**：BASE-9 偿还 D1/D2（MemoryGuard 开关 + GC）；BASE-7 OMO 非 loopback
   attach auth 缺口（sidecar 方案可规避，否则 patch）。

## 6. 证据来源

- 外部仓库（绝对路径）：
  - `/Users/mgong/myrepo/invest-assistant/invest-assistant/`（前端+JWT 网关+Daytona 沙箱）
  - `/Users/mgong/OpenSource/oh-my-openagent/`（OMO 源码：auth/网络/会话模型结论）
  - `/Users/mgong/myrepo/llm-proxy/litellm/`（多租户 litellm 网关，validate.sh 11 项断言全绿）
- 本仓库（mymain worktree 相对路径）：
  - `OpencodeAgent/docs/DEPLOY-GUIDE.md`（单租户生产拓扑权威文档）
  - `agent/src/session/models.py`（Principal.tenant 占位）、`agent/src/api/security.py`
    （共享 API_AUTH_KEY 模型）、`agent/src/channels/`（16 adapter + pairing + runtime）
  - `mymain-wiki/branch/MYMAIN_DIVERGENCE.md` §3.3/§4.5、
    `mymain-wiki/harness-evolution/README.md`（生产形态裁决）、
    `mymain-wiki/features/f7-opencode-agent.md`、`mymain-wiki/clickhouse/CLICKHOUSE_ITERATION_PLAN.md`
- 勘察方法：4 路并行 explore（wiki 文档 / mymain 服务化代码 / invest-assistant /
  OMO），2026-09-09 执行；opencode-serve gitee 仓库不可达（403），按用户指示
  理解为 opencode 中转层，未纳入勘察。
