# 生产部署全貌 — Vibe-Trading Engine-Bridge（宿主机裸部署）

> 部署日期：2026-09-19 ｜ 分支：`mymain-engine-bridge` @ `fc41781f` ｜ 形态：**宿主机裸部署**（非容器）
> 本文记录 engine-bridge 架构在阿里云 ECS 上的**事实生产部署全貌**，用于后续跟踪与 CICD 工程迭代。
> 取代了原 `opencode-web.service`（native opencode web UI，`mymain` 分支）。
> 凭证一律以 `<见服务器 .env>` 引用，本文不含任何密钥明文。
> **2026-09-20 更新**：§11.1 修正用户报错 403 的真实根因（nginx `$host` 剥端口，**已部署**）；§11.2 实测回环信任模型与暴露面；§13 补充迭代项；**新增 §15 用户认证系统章节——该系统已于 2026-09-20 部署上线并通过端到端验证（§15.11）**。

---

## 1. 部署概览

| 项 | 值 |
|----|----|
| 部署目标 | 阿里云 ECS `<ECS_PUBLIC_IP>`（hostname `server1`，VPC `<ECS_VPC_IP>`，Alibaba Cloud Linux，4C/7G） |
| 部署形态 | 宿主机裸部署（systemd 双进程），**非** T10 容器（容器构建太慢，改为裸部署验证架构可行性） |
| 代码分支 | `mymain-engine-bridge` @ `fc41781f`（fork: shadowinlife/Vibe-Trading） |
| 工作目录 | `/opt/my-vibe-trading`（= 原 opencode web 默认工作目录；HOME 对齐到此） |
| 引擎模式 | `VIBE_TRADING_ENGINE=opencode`（vt gateway 经 engine bridge 接 headless opencode serve） |
| 对外入口 | `http://<ECS_PUBLIC_IP>:4096`（nginx 固定串码网关，用户 `vibe`，**URL 与替换前一致**） |
| 与生产关系 | **直接替换**原 `opencode-web.service`（已 stop+disable，unit 保留作回滚） |
| 多租户 | 暂单实例；多租户（T10 容器 + T11 薄路由）为后续迭代项 |

---

## 2. 基础设施拓扑

| 主机 | 公网 IP | VPC 内网 IP | 角色 | 关键服务 |
|------|---------|-------------|------|----------|
| **server1** | `<ECS_PUBLIC_IP>` | `<ECS_VPC_IP>` | 部署目标（opencode web 生产） | nginx :4096 网关、vt-gateway、opencode-serve、**invest-assistant 栈（独立，勿动）** |
| **CH/LLM 主机** | `<CH_PUBLIC_IP>` | `<CH_VPC_IP>` | ClickHouse + liteLLM | liteLLM :4000（VPC 内）、ClickHouse :8123/:9000（docker 24.8） |

- **跨子网 VPC 可达性已验证**：server1(`<ECS_VPC_IP>`) → `<CH_VPC_IP>` 的 CH `/ping`→200、liteLLM `/health`→401（可达需鉴权）。
- 运维入口：`<CH_PUBLIC_IP>`（公网）用于 CH/liteLLM 运维；业务一律走 VPC 内网 `<CH_VPC_IP>`。
- ⚠️ server1 同机运行**独立的 invest-assistant 生产栈**（frontend:3001 / backend:8000 / daytona×3 / postgres / redis / registry:6000 / dex，域名 <EXISTING_TLS_DOMAIN>）——本部署**不触碰**它。

---

## 3. 服务架构（systemd 双进程 + nginx 网关）

| 单元 / 组件 | 监听 | 角色 | 状态 |
|------|------|------|------|
| `nginx`（`conf.d/opencode-web.conf`） | `0.0.0.0:4096` | 固定串码 Basic Auth 网关（用户 `vibe`）→ 反代 gateway；`proxy_set_header Host $http_host`（2026-09-19 19:00:58 修复——`$host` 剥端口会让全部非安全方法 403，§11.1）+ **字面量 `X-Forwarded-For 127.0.0.1`**——uvicorn 默认 `proxy_headers=True` 采信该头并重写 `scope["client"]` ⇒ **每个公网客户端都伪装成回环** ⇒ `Principal(LOOPBACK_TRUST)` 零凭证放行，**挡在公网与完整 API 之间的只有共享串码本身**（机制与取证标记见 §11.2，勿按「同机部署天然回环」理解）；SSE 长连接 | active/enabled |
| `vt-gateway.service` | `127.0.0.1:8081`（仅回环） | VT gateway（uvicorn `api_server.serve_main`）：React SPA + REST/SSE；`ENGINE=opencode`，作为 serve 的纯 HTTP 客户端 | active/enabled |
| `vt-opencode-serve.service` | `127.0.0.1:4098`（仅回环） | headless `opencode serve`：engine bridge 的后端引擎；装载 omo 插件 + 派生 VT MCP / search MCP | active/enabled |
| `opencode-web.service`（旧生产） | ~~`127.0.0.1:4097`~~ | 原 native opencode web UI | **inactive/disabled**（unit 保留回滚） |

进程关系：`浏览器/CLI → nginx:4096（串码）→ vt-gateway:8081 → engine bridge → opencode-serve:4098 → omo(qwen3.8-max) → liteLLM:4000 → DashScope MaaS`；MCP 工具由 serve 派生（`mcp_server.py` 经 VPC 内网读 CH `<CH_VPC_IP>:8123`）。

- `vt-gateway` 经 `/opt/my-vibe-trading/gateway-start.sh` 启动：先轮询 serve `/mcp` 就绪（最长 300s，覆盖首启 omo 插件安装），再 exec uvicorn。
- `vt-gateway` `Requires=`/`After=vt-opencode-serve`；serve 崩溃由 systemd `Restart=on-failure` 拉起，bridge 经存活对账 + `_ensure_pumps` 在下次发送时自愈（无需重启 gateway）。

---

## 4. 端口规划（避免与同机服务冲突）

| 端口 | 占用 | 说明 |
|------|------|------|
| `0.0.0.0:4096` | nginx | 对外唯一入口（串码网关） |
| `127.0.0.1:8081` | vt-gateway | 内部；nginx 反代目标（8080 被 nginx reports 占用，故用 8081） |
| `127.0.0.1:4098` | opencode-serve | 内部 headless 引擎（4096 被 nginx 占、4097 旧生产，故用 4098） |
| `<CH_VPC_IP>:4000` | liteLLM（47 主机） | LLM 网关，VPC 内网 |
| `<CH_VPC_IP>:8123/9000` | ClickHouse（47 主机） | 数据仓库 HTTP/native，VPC 内网 |

---

## 5. LLM 后端 — liteLLM（含本次关键修复）

| 项 | 值 |
|----|----|
| 服务 | `litellm.service`（47 主机，`/mnt/quantdata/litellm/`，venv312，postgres 后端） |
| 监听 | `<CH_VPC_IP>:4000`（VPC 内网 only） |
| 配置 | `/mnt/quantdata/litellm/config.yaml` + `.env`（`DASHSCOPE_API_KEY`/`LITELLM_MASTER_KEY`/`DATABASE_URL`） |
| 客户端鉴权 | 虚拟 key `<见服务器 .env>`（alias `<LITELLM_KEY_ALIAS>`），opencode 经 `auth.json` 持有 |
| opencode provider | `alibaba-cn` → `baseURL http://<CH_VPC_IP>:4000/v1`（OpenAI 兼容） |

### 5.1 ⚠️ 本次关键修复：liteLLM 后端工作区已失效

| 工作区 | endpoint | 部署前状态 | 处置 |
|--------|----------|-----------|------|
| `<DEFUNCT_LLM_WORKSPACE>`（原 liteLLM 后端） | `<DEFUNCT_LLM_WORKSPACE_ENDPOINT>` | ❌ 所有模型 `AccessDenied.Unpurchased`（403）——**生产 LLM 已挂**（journal 实证 server1 持续 403） | 弃用 |
| `<LITELLM_WORKSPACE>`（本地 omo 后端） | `<LITELLM_WORKSPACE_ENDPOINT>` | ✅ 可用，服务 qwen3.8-max + deepseek-v4.1-flash 等 | **重指 liteLLM 到此工作区** |

修复动作（47 主机，均已备份）：
1. `.env` 的 `DASHSCOPE_API_KEY` → 可用工作区 key（`<见服务器 .env>`）。
2. `config.yaml` 全部模型 `api_base` → `<LITELLM_WORKSPACE>`（4 处）。
3. `model_list` 新增 `deepseek-v4.1-flash`（→ `dashscope/deepseek-v4.1-flash`）。
4. 扩展虚拟 key `<见服务器 .env>` 的 models 范围（`/key/update`，含 deepseek-v4.1-flash）。
5. `systemctl restart litellm`（启动约 60s：Prisma toolchain + migrate）。

### 5.2 liteLLM 暴露模型（修复后全部验证 OK）

| model_name | 后端路由 | 用途（omo 档位） |
|-----------|---------|------------------|
| `qwen3.8-max` | dashscope/qwen3.8-max | 旗舰（主编排/顾问/规划） |
| `qwen3.8-flash` | dashscope/qwen3.8-flash | 轻量（writing） |
| `deepseek-v4.1-flash` | dashscope/deepseek-v4.1-flash | 平衡（**本次新增**，搜索/评审/中等复杂度） |
| `deepseek-v4-flash-0731` | dashscope/deepseek-v4-flash-0731 | 备用 deepseek |
| `glm-5.2` | dashscope/glm-5.2 | 备用 |

---

## 6. 数据后端 — ClickHouse

| 项 | 值 |
|----|----|
| 实例 | docker `clickhouse`（clickhouse-server:24.8）@ `<CH_VPC_IP>:8123`(HTTP)/`:9000`(native) |
| 库 | `ashare`（**57 张表**：fin_* 三表、stk_factor_pro ~1850万行、stk_moneyflow ~1464万行、idx_weight ~1.08亿行、两融/龙虎榜/股东户数等） |
| 读写账户 | `default`（loader/脚本）`<见服务器 .env>` |
| 只读账户 | `llm_role`（专供 `ch_*` 语义层工具，缺失绝不回退 default）`<见服务器 .env>` |
| 访问路径 | VT MCP 子进程经 VPC 内网 `<CH_VPC_IP>:8123` |
| 本次修复 | 重新应用 `clickhouse_connector.py::health_check()` 凭证探针修复（engine-bridge 分支缺此修复；CH 强制鉴权，匿名 `SELECT 1` 被拒→误判不可达，影响回测 CH loader） |

---

## 7. 配置全貌（`/opt/my-vibe-trading/`）

| 文件 | 内容 / 关键设置 |
|------|----------------|
| `.opencode/opencode.json` | 渲染产物：provider `alibaba-cn`→liteLLM；MCP `vibe-trading`(CH+记忆 env) + `search mcp`；permission 15 项治理 deny + external_directory(uploads)；**agent 仅 explore/multimodal-looker（12 领域子代理已关）**；plugin `oh-my-openagent@4.19.4` |
| `.opencode/opencode.json.tmpl.host` | 宿主机适配模板（host 路径 + provider 块 + HOME/VIBE_TRADING_HOME 对齐 + external_directory） |
| `.opencode/oh-my-openagent.json` | omo 各 agent/category 模型档位（见 §8） |
| `.opencode/render_config.py` `vibe-trading-tools.json` | 渲染器 + 工具治理清单（来自 repo `OpencodeAgent/config/`） |
| `.local/share/opencode/auth.json` | opencode 鉴权：`alibaba-cn` = liteLLM 虚拟 key `<见服务器 .env>`（HOME=/opt/my-vibe-trading） |
| `.env`（0600） | 唯一凭证点：CH(default+llm_role)、TUSHARE_TOKEN、OPENCODE_SERVER_PASSWORD、OPENCODE_WEB_GATE_CODE、VT_MEMORY*、DASHSCOPE→liteLLM、DingTalk/SMTP |
| `.vibe-trading/`（VIBE_TRADING_HOME） | 运行时根（sessions/runs/goals/uploads/FTS）；`.vibe-trading/.env` 软链 → `../.env`（ENV_PATH 对齐） |
| `.vt-memory/`（VT_MEMORY_BASE_DIR） | VT 记忆库（**48 条记忆**，替换前已存在，已保留） |
| `gateway-start.sh` | 宿主机版网关启动脚本（等 serve /mcp 就绪 → exec uvicorn api_server） |
| `repo/`（git checkout） | `mymain-engine-bridge` @ fc41781f；`repo/frontend/dist`（131 文件，本机构建） |

### 7.1 `.env` 关键项（本次仅改 LLM 指向 liteLLM，余复用）

| 变量 | 值 / 说明 |
|------|----------|
| `DASHSCOPE_BASE_URL` | `http://<CH_VPC_IP>:4000/v1`（**改**：原 dashscope 直连 → liteLLM） |
| `DASHSCOPE_API_KEY` | liteLLM 虚拟 key `<见服务器 .env>`（**改**） |
| `LANGCHAIN_PROVIDER` / `LANGCHAIN_MODEL_NAME` | `dashscope` / `qwen3.8-max`（驱动 auto-title + swarm worker，经 liteLLM） |
| `CLICKHOUSE_*` | host `<CH_VPC_IP>`、port 8123、db ashare、default+llm_role（复用） |
| `VT_MEMORY` / `VT_MEMORY_MCP_TOOLS` / `VT_MEMORY_BASE_DIR` | `full` / `1` / `/opt/my-vibe-trading/.vt-memory`（**记忆全开**） |
| `TUSHARE_TOKEN` | 复用服务器 .env（与本机 `agent/.env` 同源） |
| `OPENCODE_SERVER_PASSWORD` / `OPENCODE_WEB_GATE_CODE` | serve 内部 basic auth / nginx 串码（复用） |

---

## 8. 模型选型（omo 各 agent，与本机 omo 一致 → 映射到 liteLLM 模型名）

| 档位 | 模型 | reasoningEffort | agents / categories |
|------|------|-----------------|---------------------|
| 旗舰 | `alibaba-cn/qwen3.8-max` | high | build(主编排)、sisyphus-junior |
| 旗舰 | `alibaba-cn/qwen3.8-max` | max | oracle、prometheus、ultrabrain、deep |
| 旗舰 | `alibaba-cn/qwen3.8-max` | medium | multimodal-looker |
| 平衡 | `alibaba-cn/deepseek-v4.1-flash` | max | hephaestus、metis、momus、atlas、explore、librarian、visual-engineering、artistry、unspecified-high、unspecified-low、quick |
| 轻量 | `alibaba-cn/qwen3.8-flash` | medium | writing |

> 注：本机 omo 平衡档为 `deepseek-v4.1-flash`；该模型名原不在 liteLLM model_list，本次已新增（§5.1）。omo agent 名用 4.19.4 花名册（`build` 对应本机 `sisyphus`）。

---

## 9. 版本钉死（用户裁决：钉版，非 @latest）

| 面 | 版本 | 说明 |
|----|------|------|
| opencode CLI | `1.18.30`（`/usr/bin/opencode`，已装） | 与桥验证版本一致；无需升级 |
| oh-my-openagent | `4.19.4`（opencode 首启经 npm 装入 `~/.cache/opencode/packages/`） | tmpl plugin 钉版 |
| vibe-trading-ai | `0.1.15`（editable from repo） | engine-bridge 分支版本 |
| Python / Node | conda `legonanobot` py3.11.15 / node v20.20.2 | 复用既有环境 |

> 改任一钉版前须按 DEPLOY-GUIDE §T10.5 跑 drift-alarm 复演（record_traces → analyze → pytest opencode_bridge）。本次未改钉版。

---

## 10. 验证结果（全绿）

| # | 验证项 | 结果 |
|---|--------|------|
| 1 | VT React 前端（SPA）经 gateway 服务 | ✅ `GET /`→200 text/html，title「Vibe-Trading — …financial agent team」 |
| 2 | gateway `/health` | ✅ `{"status":"healthy","service":"Vibe-Trading API"}` |
| 3 | MCP 工具计数（记忆全开） | ✅ **83** |
| 4 | engine bridge 激活 | ✅ `VIBE_TRADING_ENGINE=opencode`、`OPENCODE_BASE_URL=127.0.0.1:4098` |
| 5 | ClickHouse `ch_list_tables` | ✅ ashare **57 张表**（经 VPC 内网 + llm_role） |
| 6 | 记忆 `memory_status` | ✅ **48 条记忆**，状态健康（替换前记忆已保留） |
| 7 | liteLLM 5 模型 roundtrip | ✅ qwen3.8-max/flash、glm-5.2、deepseek-v4-flash-0731、deepseek-v4.1-flash 全 OK |
| 8 | 端到端 bridge 会话（loopback） | ✅ tool_trail=`[memory_status, ch_list_tables]`，回复正确 |
| 9 | 端到端会话（**公网 :4096 串码网关**） | ✅ 创建会话+发消息+回复「1+1 等于 2。」 |
| 10 | 网关鉴权 | ✅ 无串码→401，带串码→200 |
| 11 | 外部访问（Mac→公网 IP:4096） | ✅ 200，VT SPA |
| 12 | 子代理关闭 | ✅ opencode.json agent 仅 explore/multimodal-looker |

gateway 预检 5/7 就绪：Tushare/akshare/ccxt/Content-Filter OK；**OKX**(connect timeout)、**yfinance**(rate limited) FAIL——ECS 外网受限所致，仅影响 crypto/美港 backtest 取数，非核心，gateway 正常启动。

---

## 11. 本次部署所做的环境变更（CICD 需复现项）

| # | 变更 | 位置 | 可逆性 / 备注 |
|---|------|------|--------------|
| 1 | liteLLM 重指工作区 + 新增 deepseek-v4.1-flash + 扩虚拟 key | 47:`/mnt/quantdata/litellm/{config.yaml,.env}` | 备份 `*.bak-20260919-140904`；**修复生产 LLM**（原工作区已失效） |
| 2 | 新增 4G swapfile（构建/运行 OOM 安全网，原 swap 512M 已满） | server1:`/swapfile2` | `swapoff /swapfile2 && rm` 可逆；建议保留（主机内存紧张） |
| 3 | npm registry → npmmirror（omo 插件安装提速；npmjs.org 经 Cloudflare 在 ECS 卡死） | server1:`/opt/my-vibe-trading/.npmrc`、`/root/.npmrc` | `registry=https://registry.npmmirror.com` |
| 4 | repo 切到 engine-bridge + 重应用 CH health_check 凭证探针修复 | server1:`/opt/my-vibe-trading/repo` | 修复存于备份 patch；**上游候选**（engine-bridge 分支缺此修复） |
| 5 | 新增 systemd 单元 vt-opencode-serve / vt-gateway + gateway-start.sh | server1:`/etc/systemd/system/`、`/opt/my-vibe-trading/` | 旧 opencode-web unit 保留 |
| 6 | nginx :4096 反代 4097→8081 + 移除 opencode basic-auth 注入 | server1:`/etc/nginx/conf.d/opencode-web.conf` | 备份 `*.pre-eb-bak` |
| 7 | `.env` DASHSCOPE → liteLLM | server1:`/opt/my-vibe-trading/.env` | 备份 `*.pre-engine-bridge-bak` |
| 8 | **nginx 强制字面量 `X-Forwarded-For 127.0.0.1`** | server1:`/etc/nginx/conf.d/opencode-web.conf` | ⚠️ **原诊断（"此改动解决了 403"）已被 §11.1 实证推翻**：该改动令 `_is_local_client()`=True、只修好了 **GET**，POST/PUT/DELETE 在其之后仍持续 403（真正根因是 `Host $host` 剥端口，即本表 #9）。但它仍是当前「无需在 Settings 填密钥」的**唯一承重墙**——机制是公网客户端全部伪装成回环（§11.2），防线只剩共享串码。**CICD 复现时必须与 §15 用户认证开关联动**：删 `auth_basic` 而不启用 `VIBE_TRADING_USER_AUTH=1` 等于完整 API 裸露公网 |
| 9 | **nginx `Host $host` → `Host $http_host`（2026-09-19 19:00:58 生产修复，用户报错 403 的真实解）** | server1:`/etc/nginx/conf.d/opencode-web.conf` | 备份 `/etc/nginx/conf.d/opencode-web.conf.pre-hostfix-20260919-190058`；`nginx -t` 通过后 reload。完整根因、对照实验与已排除假设见 §11.1。**禁止回退成 `$host`**（否则所有 POST/PUT/DELETE 重新 403）。**CICD 必复现** |
| 10 | **移除 nginx `auth_basic` 串码网关 + `X-Forwarded-For` 由字面量 `127.0.0.1` 改为 `$remote_addr`（2026-09-20，随 §15 用户认证上线）** | server1:`/etc/nginx/conf.d/opencode-web.conf`、`/etc/systemd/system/vt-gateway.service`、`/opt/my-vibe-trading/.env` | 备份 `opencode-web.conf.pre-userauth-20260920-122204`、`vt-gateway.service.pre-userauth-*`、`.env.pre-userauth-*`。**顺序不可逆**：必须先 `VIBE_TRADING_USER_AUTH=1` + `API_AUTH_KEY` 就位并验证登录，才删串码（§15.9）。`$remote_addr` 为**覆盖**语义，客户端自带 XFF 无法伪造回环（§15.11 实测）。**CICD 必复现** |

### 11.1 用户报错「远程 API 访问需要 API 密钥」的真实根因（2026-09-19 19:00 实证定论，更正原 §11#8 诊断）

> ⚠️ 初版记录「XFF 置回环后 403 解决、无需填密钥」——**不完整且有误导性**。以下为 journalctl 时间线 + 三组对照实验的完整定论记录；已排除假设附证伪命令，**勿回头重复调查**。

**时间线（journalctl 实测）**：17:01:22 nginx conf mtime 变更 + Reload（XFF 改字面量 `127.0.0.1`）后 GET 恢复 200，但 **POST 在其之后仍 403**——`17:05:51 127.0.0.1:0 - "POST /sessions" 403`、`17:18:41` 同样 403。IP 已是回环却仍被拒 ⇒ 存在**第二个独立故障**。

**真实根因：`proxy_set_header Host $host;`。nginx `$host` 会剥掉端口**，gateway 收到 `Host: <ECS_PUBLIC_IP>`（无端口）而浏览器发 `Origin: http://<ECS_PUBLIC_IP>:4096`。`_origin_matches_request_host()`（`agent/src/api/security.py:400-420`）比较 `origin_port`（4096）与 `request.url.port`（None ⇒ http 默认按 80）⇒ 不相等 ⇒ `403 {"detail":"Cross-site request denied"}`。

- **只打非安全方法**：`security.py:489-490` 仅在 method ∉ `{GET, HEAD, OPTIONS}` 时执行 `_reject_cross_site_browser_request` ⇒ 表现为「页面能打开、设置能读，一发消息就失败」。
- **为什么定位困难**：`frontend/src/lib/api.ts:295-307` 的 `errorFromResponse()` 把**任何** 401/403 的 `detail` 无条件覆盖成 i18n 文案 `agent.authRequired`（「远程 API 访问需要 API 密钥…」），真实的 `Cross-site request denied` 不可见，调查被引向 API KEY / loopback / XFF 方向整整一天。此为上游候选缺陷（已登记 mymain-wiki §2.3）。

**已实施修复（2026-09-19 19:00:58，生产，当前生效）**：`proxy_set_header Host $host;` → `proxy_set_header Host $http_host;`；备份 `/etc/nginx/conf.d/opencode-web.conf.pre-hostfix-20260919-190058`；`nginx -t` 通过后 reload。Host 白名单不受影响：`_is_allowed_loopback_host()` 经 `_host_without_port()` 先剥端口再比对 `API_ALLOWED_HOSTS`（该变量设在 systemd unit，见 §11.2），故 `<ECS_PUBLIC_IP>:4096` → `<ECS_PUBLIC_IP>` 仍命中。

**三组对照实验（直连 gateway :8081、绕过 nginx，决定性证据）**：

| # | Host 头 | Origin 头 | 结果 |
|---|---|---|---|
| 1 | `<ECS_PUBLIC_IP>`（无端口） | `http://<ECS_PUBLIC_IP>:4096` | **403** `Cross-site request denied` |
| 2 | `<ECS_PUBLIC_IP>:4096`（带端口） | `http://<ECS_PUBLIC_IP>:4096` | **201** ✅ |
| 3 | `<ECS_PUBLIC_IP>`（无端口） | `http://<ECS_PUBLIC_IP>`（隐含 80） | **201** ✅ ← 对照组：80==80 故通过，排除其余一切解释 |

**修复后外网端到端验证**（本机 Mac，出口 IP `<OBSERVER_CLIENT_IP>`，全部请求带浏览器风格 `Origin` + `Sec-Fetch-Site: same-origin`）：

| 请求 | 修复前 | 修复后 |
|---|---|---|
| `POST /sessions` | 403 | **201** ✅ |
| `POST /options/payoff` | 403 | **200** ✅ |
| `POST /auth/sse-ticket`（EventSource 路径） | 403 | **200** ✅ |
| `GET /`、`/settings/llm`、`/api/portfolio`、`/alpha/list`、`/live/status` | 200 | **200** ✅ |
| 无串码 `POST /sessions` | 401 | **401** ✅（鉴权未被削弱） |

测试产生的 4 个一次性会话已 `DELETE` 清理，7 个真实会话完好。

**已排除假设（均有实测命令与结果，勿再回头验证）**：

| 假设 | 证伪证据 |
|---|---|
| ❌ `.env` 设了 `API_AUTH_KEY` 触发 key-first | `grep -c '^API_AUTH_KEY=' /opt/my-vibe-trading/.env` → **0**；`VIBE_TRADING_API_KEY` 同为 **0** |
| ❌ 宿主机 `gateway-start.sh` 传了 `--proxy-headers` 才是原因 | 实测 `serve_main(['--host','127.0.0.1','--port','8081'])`，**未传**——且这无关紧要：**uvicorn `proxy_headers` 默认即 `True`**、`forwarded_allow_ips` 默认 `127.0.0.1`（生产 conda 环境实测 uvicorn **0.48.0**，`Config.__init__` 签名 `proxy_headers default = True`）。「配置零命中」只说明走默认值，不能反推功能关闭 |
| ❌ 「17:01 的 XFF 修复已解决问题」 | 17:05:51 / 17:18:41 的 `POST /sessions` 403 均发生在 17:01:22 reload **之后**（journalctl） |

> **版本漂移注记**：生产 conda 环境 uvicorn **0.48.0** vs `requirements-lock.txt:3845` 钉 `uvicorn==0.52.4`。两版本 `proxy_headers` 默认均为 `True`，本节结论不依赖具体版本，但漂移本身应择机对齐。

### 11.2 回环信任的真实机制与暴露面实测（2026-09-19，`nginx -T` / `ss -tlnp` / journalctl）

「无需 API 密钥」**不是**「nginx 与 gateway 同机带来的天然回环信任」，而是**头部谎报**：

nginx 对每个请求发送**字面量** `X-Forwarded-For 127.0.0.1`；uvicorn 默认 `proxy_headers=True` + `forwarded_allow_ips` 默认 `127.0.0.1`（TCP 对端确为 127.0.0.1，同机反代 ⇒ 在信任名单内）⇒ 采信该头并重写 `scope["client"]` ⇒ `_is_local_client()` 恒 True ⇒ `Principal(LOOPBACK_TRUST)` ⇒ **零凭证全权 API 访问**。**挡在公网与完整 API 之间的只有 nginx 那一个共享串码**——串码泄露即等于完整权限，且 gateway 审计日志中所有客户端 IP 均为假的 `127.0.0.1`。

- **取证标记**：uvicorn access log 中地址带 `:0` 端口 ⇒ 来自 XFF 头（无端口信息）；真实 TCP 对端会带端口（对照 `127.0.0.1:56370`）。这是区分「XFF 派生」与「真实 peer」的硬证据。
- **伪造 XFF 实证**：`16:59:23 203.0.113.9:0 - "POST /sessions" 403`——`203.0.113.9` 属 TEST-NET-3（RFC 5737 文档保留段），正常互联网流量不可能以此为真实源地址；当时 nginx 尚未强制覆盖 XFF，客户端自带的伪造值被 uvicorn 采信。这是 §15.9 拆串码时 XFF 必须用 `$remote_addr`（**覆盖**）而非 `$proxy_add_x_forwarded_for`（**追加**）的具体依据。
- **暴露面**：`ss -tlnp` 实测 vt-gateway 仅监听 **`127.0.0.1:8081`**（与 §4 一致；与仓库 `OpencodeAgent/gateway-start.sh` 的 `0.0.0.0` **矛盾**——宿主机版脚本用 `127.0.0.1`）；opencode serve 仅监听 `127.0.0.1:4098`；公网直连探测 `http://<ECS_PUBLIC_IP>:8081` 返回 `http=000` 不可达 ⇒ **无绕过 nginx 的暴露面**。
- **配置来源实测**：`FORWARDED_ALLOW_IPS` 在 unit、`.env`、进程 `/proc/<pid>/environ` 三处**均未设置** ⇒ 走 uvicorn 默认（nginx 同机恰好正确；若将来 gateway 与 nginx 分离，默认值会静默失效，建议显式 `--forwarded-allow-ips=127.0.0.1` 把隐式依赖变成契约）；`API_ALLOWED_HOSTS=<ECS_PUBLIC_IP>,localhost,127.0.0.1` 设在 **systemd unit**（`/etc/systemd/system/vt-gateway.service:18`），**不在** `.env`；`systemctl show -p User` → **root**（用户认证 CLI 与 `users.db` 属主须一致，见 §15.5）。

---

## 12. 备份与回滚

| 备份 | 路径 |
|------|------|
| 生产全量备份（units/nginx/.env/.opencode/auth.json/repo HEAD/本地改动 patch） | server1:`/opt/my-vibe-trading-backup-20260919-084447/` |
| 旧 opencode.json（native，含 12 子代理） | `/opt/my-vibe-trading/.opencode/opencode.json.prod-native-bak` |
| nginx 旧配置 | `/etc/nginx/conf.d/opencode-web.conf.pre-eb-bak` |
| liteLLM 旧配置 | 47:`/mnt/quantdata/litellm/{config.yaml,.env}.bak-20260919-140904` |

**回滚到旧生产（native opencode web）**：
```bash
# 1. nginx 指回 4097
sed -i 's|proxy_pass http://127.0.0.1:8081;|proxy_pass http://127.0.0.1:4097;|' /etc/nginx/conf.d/opencode-web.conf
# （如需恢复后端 basic-auth 注入，从 .pre-eb-bak 还原整文件）
nginx -t && systemctl reload nginx
# 2. 停新栈、起旧生产
systemctl disable --now vt-gateway vt-opencode-serve
systemctl enable --now opencode-web
```
**引擎级回退（保留新栈但用 native Python 引擎）**：`vt-gateway` 设 `VIBE_TRADING_ENGINE=native` 后重启（F8 卡一键回退；bridge 增量、native 路径零触碰）。

---

## 13. 已知限制与后续迭代项（CICD 跟踪）

| # | 项 | 现状 | 后续 |
|---|----|------|------|
| 1 | **多租户** | Phase 1 = **仅认证 + 共享工作区**（用户裁决 2026-09-19）：用户有独立账号/会话，但全部操作同一工作区；`Principal.tenant` 已填充但**零过滤**（§15，**已部署**，见 §15.11） | 后续迭代：per-tenant 数据隔离（sessions/runs/memory/uploads 过滤）、T10 容器（每租户全栈）、T11 薄路由生产接线 |
| 2 | **工作目录/多租户对齐** | HOME=VIBE_TRADING_HOME 基=`/opt/my-vibe-trading`（单实例） | 多租户下需 per-tenant 卷/目录；ENV_PATH 与 Settings 写入路径需复核 |
| 3 | **opencode 子代理** | 已关（用户要求暂不开启） | 后续探索开启 12 领域子代理（render_config 传 subagents.json 即恢复） |
| 4 | **OKX/yfinance 取数** | ECS 外网受限→FAIL（crypto/美港 backtest 取数不可用） | 如需，配代理或走内网数据源；A 股/CH 不受影响 |
| 5 | **cron 周期任务** | 旧 `cron_jobs` 指向 `127.0.0.1:4097`（旧 opencode web） | 如启用周期任务，需改指新 gateway/serve 端点 |
| 6 | **CH health_check 修复** | 本地重应用于 engine-bridge 工作树 | **上游候选**：应并入 `mymain-engine-bridge` 分支（health_check 发凭证） |
| 7 | **liteLLM 工作区** | 临时重指到 `<LITELLM_WORKSPACE>`（原 dev 工作区） | 长期应恢复/新购生产专用工作区额度，或确认此工作区可承载生产多租户负载与计费 |
| 8 | **版本钉死** | opencode 1.18.30 / omo 4.19.4 | 升级须跑 drift-alarm 复演（DEPLOY-GUIDE §T10.5） |
| 9 | **GitHub 可达性** | ECS→github.com 间歇超时（443） | 部署改用 git bundle/OSS 桥接兜底；本次经重试 fetch 成功 |
| 10 | **SSH 通道** | 本机→ECS SSH 可用但间歇 `Bad file descriptor`（本地网络抖动） | CICD 宜用 OSS 桥接/云助手；本次靠重试完成 |
| 11 | **TLS 缺失** | 入口仍是明文 `http://<ECS_PUBLIC_IP>:4096`；§15 用户认证上线后为**真实用户名/密码明文过公网**（用户普遍复用密码） | 强烈建议申请子域（如 `<VT_TLS_SUBDOMAIN>`）+ Let's Encrypt（同机 invest-assistant 已有 `<EXISTING_TLS_DOMAIN>` :443 先例）；`security.py:238-241` 明确把 HSTS 责任留给 TLS 终止层 ⇒ 上 TLS 后须在 nginx 补 HSTS |
| 12 | **无限流** | 应用层不做（用户裁决 2026-09-19）；`POST /auth/login` 是未认证的 scrypt CPU/内存放大器（`n=2**14` ≈16MB/次） | 零代码补救：nginx `limit_req_zone` 覆盖 `/auth/login`、`/auth/register`（已列为 §15.9 部署步骤）；scrypt 成本 + 邀请码闸门 + 统一错误文案为已保留防线 |
| 13 | **`test_router_proxy.py` 既有失败** | `OpencodeAgent/tests/` 基线 168 passed / **1 failed** / 1 skipped：`test_buffered_response_passes_status_body_and_length` Content-Length `'29' == '31'`（router 发紧凑 JSON、测试按默认分隔符计算长度）；已在 `fc41781f` 的 pristine `git worktree` 复现同一失败 ⇒ **分支既有**，与用户认证/本轮变更无关 | 独立修复（改测试断言或 router 序列化口径，二选一） |

---

## 14. 关键运维命令

```bash
# 状态
systemctl status vt-opencode-serve vt-gateway nginx
journalctl -u vt-gateway -f          # gateway 日志
journalctl -u vt-opencode-serve -f   # serve 日志（含 omo/MCP）
# 重启
systemctl restart vt-opencode-serve  # 重启引擎（首启会装 omo 插件，慢）
systemctl restart vt-gateway
# 配置重渲染（改 tmpl.host / .env 后）
cd /opt/my-vibe-trading/repo/OpencodeAgent/config
set -a; source /opt/my-vibe-trading/.env; set +a
/opt/miniconda3/envs/legonanobot/bin/python -c "import sys;sys.path.insert(0,'.');from render_config import render;from pathlib import Path;Path('/opt/my-vibe-trading/.opencode/opencode.json').write_text(render(Path('/opt/my-vibe-trading/.opencode/opencode.json.tmpl.host'),Path('vibe-trading-tools.json'),None))"
systemctl restart vt-opencode-serve vt-gateway
# 代码更新
cd /opt/my-vibe-trading/repo && git fetch origin mymain-engine-bridge && git checkout mymain-engine-bridge && git pull
/opt/miniconda3/envs/legonanobot/bin/pip install -e . -e OpencodeAgent/nano-search-mcp
cd frontend && npm ci --registry=https://registry.npmmirror.com && npm run build
systemctl restart vt-opencode-serve vt-gateway
# 访问
浏览器: http://<ECS_PUBLIC_IP>:4096 （用户 vibe + 串码<见 .env OPENCODE_WEB_GATE_CODE>）
```

---

## 15. 用户认证系统（**2026-09-20 已部署生产并端到端验证**，实录见 §15.11）

> ✅ **状态边界（2026-09-20 更新）**：§11.1 的 `$http_host` 修复于 2026-09-19 19:00:58 上线；本节用户认证系统已于 **2026-09-20 部署 server1 并端到端验证通过**（§15.11）——`/auth/login` 等端点已在生产可用，**nginx 串码已移除**，防线改为每用户 session token + 邀请码注册闸门。设计全文：`.omo/plans/vibe-trading-user-auth.md`（含 Oracle 对抗性复审 6 阻断项与采纳台账 §4.5）。

### 15.1 目标

1. 拆除 nginx 共享串码（§11.2：串码泄露即公网完整 API 权限）；
2. 用户不再需要理解/填写 API KEY——LLM 凭证已由 opencode 在服务端桥接 liteLLM（§5），模型配置全在服务侧；
3. 配置页对非 admin 隐藏、写端点后端锁 admin。

### 15.2 总开关与启动期不变量

`VIBE_TRADING_USER_AUTH`（默认 `0`）：关闭时行为与今天/上游**逐字节一致**，默认路径**零 SQLite 查询**——这是整个 patch 可上游的硬前提。

**启动期不变量**：`flag=1` 且 `API_AUTH_KEY` 为空 ⇒ **拒绝启动**（fail fast，非仅告警）。§11.2 的「拆串码后仍走 loopback 信任 = 公网裸奔」危险象限由此**从设计上排除**，而非靠部署顺序与运维纪律规避。

新增 4 个 env 全部进 `agent/src/config/env_schema.py`（env-var AST 门禁禁裸 `os.getenv`）：`VIBE_TRADING_USERS_DB_PATH`、`VIBE_TRADING_SESSION_TTL_DAYS`（默认 7）、`VIBE_TRADING_ALLOW_SELF_REGISTER`（默认 1）。

### 15.3 存储

SQLite `<VIBE_TRADING_HOME>/users.db`（生产 = `/opt/my-vibe-trading/.vibe-trading/users.db`，权限 0600，属主与 systemd `User=root` 一致，WAL 模式含 `-wal`/`-shm` 伴生文件），三表 `users`/`sessions`/`invites`（`PRAGMA user_version` 迁移）。密码 `hashlib.scrypt`（`n=2**14, r=8, p=1`，每用户 16B 盐，`hmac.compare_digest` 恒定时间校验，格式带算法标识便于迁移）；session token 为 `secrets.token_urlsafe(32)` 明文**只在登录响应中出现一次，DB 只存 sha256**（DB 泄露 ≠ 凭据泄露）；7 天滑动过期（续期写事务 >60s 节流）；会话校验固定 **JOIN `users` 且 `is_active=1`** ⇒ 停用/降级下一请求即生效。username 入库前 `strip().lower()` 归一（SQLite UNIQUE 字节精确，防 `Alice`/`alice` 视觉冒充）。

### 15.4 端点（冻结契约，plan §2.5；新文件 `user_auth_routes.py` + `runtime_settings_routes.py`）

| 方法 + 路径 | 守卫 | 说明 |
|---|---|---|
| `GET /auth/mode` | **公开、恒注册** | `{"user_auth": <bool>}` —— 前端 capability 门控（§15.7），flag=0 时也返回 `false` |
| `POST /auth/register` | 公开（需邀请码） | 201 + `{token,username,role,display_name}`；`400` 校验失败/邀请码无效或耗尽；`409` 用户名已存在；`403` 注册已关闭 |
| `POST /auth/login` | 公开 | 200 同上；失败**一律** 401 `Invalid username or password`（绝不区分用户不存在/密码错误，防用户名枚举） |
| `POST /auth/logout` / `GET /auth/me` | Bearer | 204 / 200 `{username,role,display_name}` |
| `POST /auth/change-password` | Bearer | 204；旧密码不匹配/新密码不合规回 **400 而非 401**（401 会被前端全局会话销毁误杀）；成功后吊销其他 session |
| `GET /settings/runtime` | Bearer（任意已登录用户） | **脱敏**只读：仅 `provider`/`model_name`/`sse_timeout_seconds`，绝不含 `api_key_hint`/`env_path`/providers 目录（普通用户聊天页需要 SSE 看门狗参数） |

注册制为**邀请码闸门**：码 `secrets.token_urlsafe(16)`（128 bit），只存 sha256、生成时明文只显示一次，`used_count += 1` 与建号在**同一事务**（防一码多用竞态），支持 `max_uses`/`expires_at`。

### 15.5 管理员 CLI

`python -m src.api.user_admin` 子命令：`create-admin` / `create-user` / `invite` / `list` / `deactivate` / `reset-password` / `revoke-sessions`。首个 admin 无法由 admin 创建 ⇒ CLI bootstrap 必须存在。**运行要求：`cwd=agent/`（生产 = `/opt/my-vibe-trading/repo/agent`）、以 root 执行**（users.db 属主一致性，见 §11.2）。密码经 `getpass` 交互输入，**绝不接受命令行参数**（shell history / `ps` 泄露）；输出不含任何 hash 或 token 明文。

### 15.6 角色门禁（`admin_auth.py`）

flag=1 时**写端点**锁 admin：settings（含 connection/portfolio/qveris 写，经 `require_settings_write_auth` 本体 flag-aware 自动覆盖）、实盘交易（`/mandate/commit`、`/live/*` 下单/撤单/halt）、channels（`/channels/start`、`stop`、`pairing`）、scheduled 任务增删改；**只读端点保持 `require_auth`**（普通用户需要看到状态才能理解为什么不能改）。`require_admin` 在**请求时**读 flag（flag=0 ⇒ 委托被包裹原依赖，逐字节不变）：静态 admin-only 会弄坏桌面端——Electron shell 在 `desktop/electron/src/main.ts:94` 每次启动注入 per-launch `API_AUTH_KEY` bearer，属 SHARED_KEY principal（Oracle B2）。flag=1 时同时放行 SHARED_KEY 作 break-glass（`users.db` 损坏时机器通道不被切断）。

### 15.7 前端 capability 门控（Oracle B3）

前端是否启用登录门禁**只认** `GET /auth/mode`，取不到/任何失败**默认 `false`**（fail-open 到旧行为，绝不把人锁在登录页）。两个已验证的破坏点决定了不能靠探测：① `SPAStaticFiles` 按请求从磁盘读 `frontend/dist` ⇒ `npm run build` 完成那一瞬间登录 UI 就对**未重启的旧后端**生效，形成锁死窗口；② SPA catch-all（`agent/src/api/spa.py:14-20`）把一切未匹配 GET 变成 `200 + index.html` ⇒ **状态码探测功能存在与否不可能**。前端 6 项行为（RequireAuth 旁路、`localApiAccessSection` 保留、`agent.authRequired` 原文案、401/403 全局登出、`/settings` NAV 可见性）全部按 flag 分支 ⇒ flag=0 时前端行为与今天逐字节一致。

### 15.8 工作树文件清单

后端新增：`agent/src/api/{user_store.py, user_store_schema.py, password_hashing.py, user_auth_routes.py, admin_auth.py, runtime_settings_routes.py, user_admin.py}`；改造：`security.py`（session 分支 + 启动期不变量，~15 行/1-2 函数）、`env_schema.py`（4 env）、`session/models.py`（`AuthMethod.USER_SESSION` + `Principal.role`）、`api_server.py`（注册 + `require_admin` 注入）；测试：`agent/tests/{test_user_auth.py, test_user_auth_api.py, test_auth_mode_endpoint.py}`（35 用例）。前端：`pages/Login.tsx`、`components/auth/RequireAuth.tsx`、`stores/auth.ts`、`apiAuth.ts`/`api.ts`/`router.tsx`/`Layout.tsx`/`Settings.tsx`/`Agent.tsx`/`useSSE.ts` 改造 + 8 locale i18n。基线数字见 `mymain-wiki/branch/MYMAIN_DIVERGENCE.md` §3.1。

### 15.9 部署顺序（必须严格照序，plan Phase 5）

1. **备份 3 份**：应用 `cp -a /opt/my-vibe-trading /opt/my-vibe-trading-backup-$(date +%Y%m%d-%H%M%S)`；nginx `cp -a /etc/nginx/conf.d/opencode-web.conf{,.pre-userauth-bak}`（nginx conf 在 `/etc/nginx`，**不在**应用备份里）；前端 `cp -a frontend/dist{,.pre-userauth}`（§15.7 破坏点 ①：构建即上线）。
2. 部署代码 + `pip install -e .` + `cd frontend && npm ci && npm run build`。
3. systemd unit 同时加 `Environment=VIBE_TRADING_USER_AUTH=1` **与** `Environment=API_AUTH_KEY=<新生成的长随机值>`（缺 key 会触发 §15.2 不变量拒绝启动）。**此时先不删 nginx auth_basic / XFF 字面量**。
4. 以 root、`cwd=agent/` 执行 `create-admin <you>` + `invite --uses 10`（可在 restart 之前做，缩小「flag 已开但无 admin」窗口）。
5. `systemctl restart vt-gateway`，用串码 + 新账号**双通道**验证登录可用。
6. **确认登录可用后**才删 `auth_basic`/`auth_basic_user_file`，并把 `X-Forwarded-For 127.0.0.1` 改为 `X-Forwarded-For $remote_addr`——**覆盖而非追加**：`$proxy_add_x_forwarded_for` 的不可欺骗性完全依赖 uvicorn 右到左遍历 + 信任名单两个前提，一旦有人设 `FORWARDED_ALLOW_IPS=*`，攻击者自带 `X-Forwarded-For: 127.0.0.1` 即可复活 loopback 信任绕过全部鉴权（§11.2 的 `203.0.113.9` 伪造案为实证）。**保留 `Host $http_host`，禁止回退 `$host`**（§11.1）。`nginx -t && systemctl reload nginx`。
7. nginx 加 `limit_req_zone $binary_remote_addr zone=vt_auth:10m rate=10r/m;` 应用于 `/auth/login`、`/auth/register`（`limit_req zone=vt_auth burst=5 nodelay;`，零应用代码，§13#12）；建议同批上 TLS + HSTS（§13#11）。

### 15.10 回滚（分段，plan §7 —— 两段成立条件不同，混用会出事故）

| 阶段 | 场景 | 动作 |
|---|---|---|
| **nginx 改动之前** | 用户认证出问题 | unit 设 `VIBE_TRADING_USER_AUTH=0` + 重启 gateway ⇒ 回到 §11.2 现状。**dist 无需回滚**（capability 门控读到 `user_auth:false` 自动旁路登录） |
| **nginx 改动之后** | 同上 | ⚠️ 单设 flag=0 **不够**：D14 已让 XFF 携带真实 IP ⇒ client=公网 IP ⇒ loopback 分支不命中 ⇒ 结果是**全员 403 而不是恢复**。必须**先恢复 nginx 备份**（`cp -a .pre-userauth-bak` → `nginx -t` → reload，串码 + XFF 字面量一起回来）**再** flag=0 + 重启 |
| 任意 | 前端登录页坏了 | `cp -a frontend/dist.pre-userauth frontend/dist`（无需重启，按请求读盘） |
| 任意 | users.db 损坏 | 删除后重新 `create-admin`（全部会话失效、邀请码需重新生成）；HTTP break-glass 走 `API_AUTH_KEY`（§15.6） |

### 15.11 部署实录（2026-09-20，server1，全绿）

**代码传输**：本地 4 个 commit（`7a7abfd8` CH 探针 / `43e297a6` 后端 / `ba86f453` 前端 / `27e32932` 文档）→ push fork → 服务器 `git pull --ff-only` 至 `27e32932`，脏项 0。依赖清单零变更 ⇒ **无需 `pip install`**（editable install 自动识别新模块）。

**前端产物**：服务器 node 为 **v20.20.2**，不满足 `frontend/package.json` 的 `engines >=22.22.0`（vite 8.2.2 自身只要求 `^20.19.0 || >=22.12.0`，故是项目声明不满足而非 vite 不可运行）。为不承担该不确定性，**dist 在本机 node v24 构建后 tar-over-ssh 投递**（134 文件 / 4.6M），同时避开服务器 `npm ci` 的 npmmirror 网络依赖与 §11#2 记载的 OOM 风险。
> ⚠️ macOS `tar` 会写入 `._*` AppleDouble 伴生文件（实测解出 270 文件而非 134）并透传本地 uid（`502 games`）。投递后必须 `find dist -name '._*' -delete` + `chown -R root:root dist`。后续投递宜用 `COPYFILE_DISABLE=1 tar`。

**凭证落位（关键安全细节）**：`/etc/systemd/system/vt-gateway.service` 实测为 **0644 全局可读**，`/opt/my-vibe-trading/.env` 为 **0600**。故 `API_AUTH_KEY`（`openssl rand -hex 32`，64 字符）**只写入 `.env`**，unit 中 `API_AUTH_KEY` 行数为 **0**；非敏感的 `VIBE_TRADING_USER_AUTH=1` 写入 unit 第 19 行（`systemctl cat` 可见，便于运维）。管理员初始密码与邀请码分别写入 `/opt/my-vibe-trading/{admin-initial-password,invite-codes.txt}`（均 0600），**全程未进入任何日志或对话**。

**管理员与邀请码**：`create-admin admin` 与 `invite --uses 10` 经 pty 驱动（CLI 仅接受 `getpass`，拒绝命令行参数）。`users.db` 落在 `/opt/my-vibe-trading/.vibe-trading/users.db`（0600 root:root），**未**误落到 `/root/.vibe-trading/`——证明 `HOME` / `VIBE_TRADING_HOME` 传递正确。

**验证结果（全部实测）**：

| 验证项 | 结果 |
|---|---|
| 启动期不变量 | 未触发（flag=1 且 key 就位）；gateway 18s 内 healthy，active/enabled |
| `GET /auth/mode`（经 nginx，无凭证） | `200 {"user_auth":true}` |
| **loopback 信任已关** | 回环直连无凭证 `GET /sessions` → **401**（改造前为 200）；`/health` 仍 200 |
| **中间态：串码在、flag=1** | 带串码但无 session token 的 6 个端点 **全部 401** ⇒ 证明 XFF 谎报已不能换取访问权，串码此时已是冗余层 |
| 公网登录 | `POST /auth/login` → **200**，`role=admin`，token 43 字符 |
| **需求 1** | `POST /sessions` 仅带 session token（**无任何 API key**）→ **201** |
| **需求 3** | `GET /settings/runtime` → 200 且**仅** `{provider, model_name, sse_timeout_seconds}`，无 `api_key_hint`/`env_path`/`providers` |
| 真实对话（原故障点） | `POST /sessions/{sid}/messages` → **200**，助手 ~24s 回复「2」⇒ engine bridge → serve → omo → liteLLM 全链路通 |
| SSE | `POST /auth/sse-ticket` → 200 |
| 错误密码 | 401 `{"detail":"Invalid username or password"}`（统一文案，无枚举） |
| 共享 key break-glass | `GET /settings/llm` 带 `API_AUTH_KEY` → 200（T11 router / 桌面端 / CLI 通道未断） |
| **XFF 伪造防护** | 客户端自带 `X-Forwarded-For: 127.0.0.1` / `127.0.0.1, 127.0.0.1` / `::1` → **全部 401**，且日志记真实公网 IP ⇒ `$remote_addr` **覆盖**语义生效（若用 `$proxy_add_x_forwarded_for` 追加 + `FORWARDED_ALLOW_IPS=*` 则可被伪造） |
| **审计日志** | 客户端 IP 由改造前的全量 `127.0.0.1:0` 变为真实公网 IP（`:0` 端口仍标记地址来自 XFF）⇒ 日志首次具备取证价值 |
| 服务健康 | vt-gateway / vt-opencode-serve / nginx 全 active；6 分钟窗口 gateway 日志 error/traceback/500 计数 **0** |

**本次部署产生的备份（回滚依据）**：

| 备份 | 路径 |
|---|---|
| 应用全量（1.5G） | `/opt/my-vibe-trading-backup-20260920-122204/` |
| nginx conf | `/etc/nginx/conf.d/opencode-web.conf.pre-userauth-20260920-122204` |
| systemd unit | `/etc/systemd/system/vt-gateway.service.pre-userauth-20260920-122204` |
| `.env` | `/opt/my-vibe-trading/.env.pre-userauth-20260920-122204` |
| 前端 dist | `/opt/my-vibe-trading/dist.pre-userauth-20260920-122204/`（已移出 repo 保持 git 干净） |
| 服务器原已暂存的 CH 补丁 | `/opt/my-vibe-trading/ch-healthcheck-staged-20260920-122204.patch`（内容已由 `7a7abfd8` 承载，`git diff` 逐字比对一致后才丢弃） |

> ⚠️ **部署前必须清理服务器 repo 的已暂存本地补丁**：`git status --porcelain` 显示 `M ` 时 M 在**第一列 = staged**，`git diff` 看不到（只显未暂存），必须用 `git diff --cached`。本次即因此差点漏判，若直接 pull 会静默回退 CH 探针修复 → CH 强制鉴权下健康检查误判不可达 → 回测 loader 降级到网络源。

**遗留风险（部署后新增，需运维知悉）**：`/auth/login` 现已公网可达且**无限流**（用户裁决本轮不做，plan D11）。scrypt `n=2**14` 每次约 16MB 内存，构成未认证的 CPU/内存放大器；server1 仅 7.4G 内存且**同机运行独立的 invest-assistant 生产栈**，持续洪泛有波及邻居的风险。零应用代码的补救：nginx `http` 段加 `limit_req_zone $binary_remote_addr zone=vt_auth:10m rate=10r/m;`，并在 `location` 内对 `/auth/login`、`/auth/register` 加 `limit_req zone=vt_auth burst=5 nodelay;`。另：管理员用户名 `admin` 可预测，在无登录限流的前提下建议改为不可猜测的用户名（`create-user --role admin` 建新号后 `deactivate admin`）。
