# opencode-web 部署指南（宿主机直部署 + nginx 串码网关）

> 当前线上形态（2026-08-28 晚起）：**宿主机 systemd 直部署 `opencode web`**，对外由 **nginx :4096 固定串码网关**代理，取代此前的 `opencode serve` 直出方案与更早的 Docker 容器方案。
> 容器化构建文件（`Dockerfile` / `build.sh` / `docker-compose.yml`）仍保留在仓库中，见附录 B。
>
> **🆕 多租户容器形态（T10，`v3.0.0-tenant`）见下方「§T10」专章** —— 那是 engine-bridge
> 多租户未来的部署单元（每租户一个全栈容器 + 薄路由），与本文件的宿主机直部署形态**并存**；
> 宿主机直部署仍是当前线上事实生产，回退方式见 F8 卡（`ENGINE=native` 一键）。

---

## §T10. 多租户容器形态（engine-bridge，`opencode-serve:v3.0.0-tenant`）

> 本章描述 **新的单租户全栈容器**（plan `opencode-engine-bridge-v2` T10 / D2）。
> 它是多租户架构的部署单元：每租户一个容器（vt gateway + opencode serve + VT MCP + home
> 同容器），上游由薄 router（T11）按 Host/token 路由。**与上方宿主机直部署形态并存**——
> 宿主机直部署是当前线上事实生产（§0），本容器形态是多租户未来；两者经 F8 卡的回退程序
> （`VIBE_TRADING_ENGINE=native` 一键）互相兜底。

### T10.0 架构（双进程，supervisord 监管）

| 进程 | 监听 | 角色 |
|------|------|------|
| `opencode serve` | **127.0.0.1:4096**（容器内部，**不对外暴露**） | headless 引擎；桥的 legacy `/session` + `GET /event` SSE 后端 |
| vt gateway（uvicorn `api_server`） | **0.0.0.0:8080**（**唯一公开端口**，`EXPOSE 8080`） | React SPA + REST/SSE；`VIBE_TRADING_ENGINE=opencode`，作为 serve 的**纯 HTTP 客户端**（经 engine bridge），**不自 spawn serve** |

- **进程模型裁决**（plan T10，消除 Oracle 二轮注 1 的自相矛盾）：supervisord 管理双进程；
  serve 绑固定 `127.0.0.1:4096`；gateway 为纯 HTTP 客户端。serve 崩溃由 supervisord
  `autorestart` 拉起，桥经 T6 存活对账 + `_ensure_pumps` 在**下一次发送时自愈，无需重启
  gateway**（T8-1 修复 `90a4378a` 实证 8.03s 落终态）。
- **端口规划**（compose 内成文）：容器内 serve=4096（内部）/ gateway=8080（公开）；
  compose 宿主映射 `24096:8080`（T10 rig 隔离占用宿主 24096/28080；T14 占用 14096/18080，
  互不冲突）。生产由薄 router 终结公网 443/4096 后按租户转发到各容器的 8080。

### T10.1 版本钉死（B1 / D10 —— 冻结的执行，非违反）

| 面 | 旧（虚构钉版） | 新（精确钉版） | 依据 |
|----|------|------|------|
| opencode CLI | `opencode-ai@latest`（Dockerfile + Dockerfile.base） | **`opencode-ai@1.18.30`** | 全部桥验证跑在 1.18.30（T1 golden traces / T7 web E2E 67 检查 / T8 IM parity / T8-1 liveness）。备选 1.18.18(镜像)/1.18.23(宿主生产) 须先按 drift-alarm 复跑（见 T10.5） |
| OmO 插件 | `oh-my-openagent@latest`（tmpl + entrypoint 兜底块，**两处**） | **`oh-my-openagent@4.19.4`** | T2 §1.3：自 2026-08-01 起事实生产；5.0.0-beta 每 1-2 天发版 = 活体翻牌风险，钉版即为此 |
| base 镜像 | `FROM opencode-serve-base:latest`（移动 tag，本地/registry **两个不同镜像** split-brain） | **`FROM opencode-serve-base:v3.0.0-tenant`**（= 本地 `ea738ee663d1`，config digest `sha256:ea738ee663d1…`） | T2 §1.1。字面 `@sha256:<manifest-digest>` 需 registry push（用户门控，未做）；版本化 tag + 记录 config digest = 本地不可变等价，消除 `:latest` split-brain |

> ⚠️ **改任一钉版前必须跑 drift-alarm 复演**（见 T10.5）。`@latest` 不得残留在任何一处
> （Dockerfile / Dockerfile.base / tmpl / entrypoint 兜底 —— T2 冻结兼容清单全部 4 面）。

### T10.2 volume 与状态对齐（B5）

命名卷挂在 **`/home/opencode`**（运行用户的 home），使**每一条有状态路径**都落卷、跨容器重建存续：

| 状态 | 容器内路径 | 是否落卷 |
|------|-----------|:--:|
| Settings `.env`（`ENV_PATH`，helpers.py:31 硬编码 `Path.home()`） | `/home/opencode/.vibe-trading/.env` | ✅ |
| 运行时根（sessions/goals/runs/uploads/swarm/FTS） | `/home/opencode/.vibe-trading/` | ✅ |
| VT 记忆（`VT_MEMORY_BASE_DIR`，**已并入卷内**，原 `/workspace/.vt-memory`） | `/home/opencode/.vibe-trading/.vt-memory` | ✅ |
| opencode server 状态（会话库/auth） | `/home/opencode/.local/share/opencode`、`.local/state`、`.cache` | ✅ |
| OmO 插件安装缓存（不再 `@latest` 重解析） | `/home/opencode/.opencode/node_modules` | ✅ |
| cron 状态/日志 | `/workspace/cron_jobs/{state,logs}` | ✅（bind mount） |

- **`VIBE_TRADING_HOME` 必须不设或恰好等于 `/home/opencode/.vibe-trading`**（= `ENV_PATH`
  基）。分叉则 Settings 写入落容器临时层、重建即丢——entrypoint 启动**硬校验**，分叉即
  `exit 1`（fail-closed）。
- 镜像层预创建上述 home 子目录并 `chown opencode:opencode`（本地 base `ea738ee663d1` 早于
  Dockerfile.base 的状态目录预创建，T2 §2.2）；命名卷首挂时从镜像播种，opencode 随后以
  `opencode` 身份写入（绝不 root → 无 EACCES 锁死）。

### T10.3 认证（D9 / B2 —— fail-closed）

- **`API_AUTH_KEY` 缺失 → entrypoint 拒绝启动**（`exit 1`，gateway 永不拉起）。理由：gateway
  把 loopback peer 视为 local（零认证）；薄 router 转发时 peer 可能是 loopback，缺 key =
  公网面零认证（`security.py:507-518`）。
- **`API_ALLOWED_HOSTS`**（**真实 env 名**，`env_schema.py:312` 经 `security.py:113
  _get_extra_loopback_hosts` 生效）由 compose env 注入；T11 开通脚本按租户公网域名设置
  （router 保留公网 Host）。**`EXTRA_LOOPBACK_HOSTS` 是内部 monkeypatch 注册键，不是 env
  变量**——写错则 router 转发的每个请求被 `_reject_untrusted_loopback_host` 403。
- 镜像/compose **不含任何真实密钥**：`.env`（gitignored）承载 `API_AUTH_KEY` /
  `OPENCODE_SERVER_PASSWORD` / `DASHSCOPE_API_KEY` / `CLICKHOUSE_*`；`.env.example` 仅占位符。

### T10.4 上传可达性（B6）+ MCP env 一致性

- server 生成配置（渲染后的 `opencode.json`）的 `permission.external_directory` 为**对象**
  （非字符串——kimaki deep-merge 陷阱），其 ALLOW 条目覆盖 `UPLOADS_DIR`
  （`/home/opencode/.vibe-trading/uploads/*` → `allow`，置于 `*`→`ask` 之后以满足 opencode
  findLast 覆盖序）。**永不进 session scope**（D9）。
- MCP 子进程 env（spawn 时由渲染配置固定）显式携带 `HOME=/home/opencode` +
  `VIBE_TRADING_HOME=/home/opencode/.vibe-trading`，与 gateway **逐字一致**（entrypoint 启动
  断言并打印 parity）；goal/session/run 路径因此解析到同一持久化根。

### T10.5 改钉版时的 drift-alarm 复演程序（D10 / spike §8）

任何 opencode CLI 钉版变更（1.18.30 → 其他）**必须**先复演，否则桥的事件词汇/形状假设可能静默漂移：

```bash
# 1. 对钉版目标起 serve（与 T1 rig 同构），复跑录制脚本
python agent/tests/fixtures/opencode_bridge/record_traces.py   # 8 类场景 golden traces
# 2. diff 词汇/形状（重点：permission.asked payload 字段、新事件类型存在性 —— spike §8 标 MED 敏感）
python agent/tests/fixtures/opencode_bridge/analyze_traces.py
# 3. 桥 golden 套件必须全绿（translator allowlist 使未知事件类型无害，但形状漂移会失败）
pytest agent/tests/opencode_bridge -q
```

OmO 钉版变更同理（continuation 的 6.4s re-prompt 间隙是 OmO 版本敏感项，spike §8）。

### T10.6 构建与运行（单租户本地）

```bash
cd OpencodeAgent
# base 镜像（本地已存在则跳过；digest-pin 见 T10.1）
docker tag ea738ee663d1 opencode-serve-base:v3.0.0-tenant   # 仅首次
# app 镜像（vendoring mymain-engine-bridge + 构建前端 SPA + 钉版）
DOCKER_PLATFORM=linux/amd64 ./build.sh --app --tag v3.0.0-tenant
# 配置（scratch key，绝不用生产凭据）
cp .env.example .env   # 填 API_AUTH_KEY / OPENCODE_SERVER_PASSWORD / DASHSCOPE_API_KEY / CLICKHOUSE_*
# 起（宿主 24096 → 容器 gateway 8080）
docker compose up -d
curl -sf http://localhost:24096/health    # gateway /health（preflight 成功后才应答）
```

> **本地 arm64 Mac 运行时**：amd64 容器经 **Rosetta** 仿真运行（已实证 `opencode --version`
> 正常打印，非 QEMU SIGILL）。生产工件仍是 amd64；ECS（amd64）原生运行。

### T10.7 与宿主机直部署形态的关系

- 本容器形态**不取代**当前线上宿主机直部署（§0）；两者并存。多租户上线经 T11 router +
  T12 隔离矩阵验收后逐租户迁移。
- 回退：容器内 `VIBE_TRADING_ENGINE=native` 切回 Python 引擎（F8 卡一键回退程序）；或整体
  回退到宿主机直部署（§10 处置表的回滚路径）。

---

## §T11. 多租户薄路由 + 租户开通（engine-bridge，`deploy/router/`）

> 本章描述 **T10 容器之上的多租户接入层**（plan `opencode-engine-bridge-v2` T11 / D2 / D9-F6）：
> 一个薄反代（token/Host → tenant → upstream，**不含业务逻辑**）+ 一个开通脚本。
> 深度文档见 [`../deploy/router/README.md`](../deploy/router/README.md)（路由规则表、SSE 配方、
> 唤醒/回收/栅栏三套先例配方、env 参考、T12 交接）；本章只给部署动作。

### T11.0 架构

```
公网 → router（:28080，Host 保留）→ 租户 A 容器（gateway :8080 / serve :4096 内部）
                                  → 租户 B 容器（同上）
        ↑ tenant_registry.json（token_sha256 → tenant → upstream；Host → tenant）
        ↑ provision_tenant.py 写入，router 按 mtime 热加载（新租户无需重启）
```

- **router 只做路由**：业务鉴权仍在租户 gateway（它用同一把 Bearer key 对自己的
  `API_AUTH_KEY` 复核）。registry 只存 **token 的 sha256**，明文 key 只存在于租户
  `0600 tenant.env`——registry 泄漏 ≠ 凭据泄漏。
- **Host 保留**（D9/F6 配方）：公网 Host 原样转发，租户 `API_ALLOWED_HOSTS=<公网host>`
  信任它；改写成 upstream host 会让每个请求被 `_reject_untrusted_loopback_host` 403。
- **wake-on-inbound**：上游无响应 → 问容器状态 → `docker start` → 轮询 gateway `/health`
  → **重放原请求（含 body）**；超时/后端不可用 → `503 + Retry-After +` 兜底页（点名租户），
  绝不静默挂死。本机实测（暖卷、amd64/Rosetta）**19.7–40.9s** 拿到 201 + 真 session_id。
- **空闲回收真值检查**（opencode-router 配方）：代理侧活动记录不充分（SSE/WS 不打访问日志）
  → 回收前 `docker exec` 问引擎 `GET /session?limit=1&roots=true` 读 `time.updated`；
  引擎沉默且代理无记录 → **fail closed 保留**。回收间隔策略属 T12
  （`VT_ROUTER_RECLAIM_INTERVAL_S` 默认 0=关）。
- **租户删除栅栏**（openwork directory-fence）：per-tenant 串行化 dispose 与 prompt admission，
  删除/回收不会与在途消息竞争；SSE 流的 admission 由流结束时释放（不是 handler 返回时）。

### T11.1 开通一个租户

```bash
cd OpencodeAgent
python deploy/provision_tenant.py \
    --tenant acme --public-host acme.example.com --host-port 28081 \
    --out-dir /srv/vt-tenants --container-prefix vt-tenant --volume-prefix vt-tenant \
    --image opencode-serve:v3.0.0-tenant \
    --base-env .env --print-key          # --print-key 只打一次，交给租户
docker compose -f /srv/vt-tenants/acme/docker-compose.yml up -d
# 或一次起全部已开通租户（开通脚本自动维护 include 列表）
docker compose -f /srv/vt-tenants/fleet.yml up -d
```

产出：`tenant.env`(0600，含生成的 `API_AUTH_KEY`/`OPENCODE_SERVER_PASSWORD`、
`API_ALLOWED_HOSTS`、`VIBE_TRADING_SSE_TIMEOUT`、D11 的 `LANGCHAIN_*`、
`VIBE_TRADING_CHANNELS_AUTO_START=false`)、`agent.json`（channels 段**占位凭据** +
全部 `enabled:false` + `operators:[]`，fail-closed）、`docker-compose.yml`（T10 端口/卷约定）、
三个 named volume（home 卷按 B5 骨架预建 + `chown opencode`）、registry 条目、`fleet.yml`。

- **`opencode.json` 不由开通脚本渲染**：仍走既有 `config/opencode.json.tmpl` +
  `render_config.py`（`entrypoint.sh` 启动时渲染并编译工具治理清单）——治理面不分叉。
- **幂等**：重跑复用已有 key（`--rotate-key` 才换新）、生成文件字节稳定、`agent.json`
  只写一次（运维填入的真凭据不会被占位符覆盖，卷内同理）。
- **绝不写入真凭据**：`--base-env` 只透传白名单内的模型/数据源凭据
  （`DASHSCOPE_*`/`CLICKHOUSE_*`/`TUSHARE_TOKEN`）；bot 凭据永远是占位符。

### T11.2 跑 router

```bash
cd OpencodeAgent/deploy
# 宿主直跑（本地/E2E 形态：upstream = http://127.0.0.1:<host-port>）
VT_ROUTER_REGISTRY=/srv/vt-tenants/tenant_registry.json \
VT_ROUTER_ADMIN_TOKEN_SHA256=$(python -c "import hashlib,os;print(hashlib.sha256(os.environ['ADMIN'].encode()).hexdigest())") \
    python -m router.cli serve --port 28080
python -m router.cli show                 # 路由表（无密钥）
python -m router.cli reclaim --dry-run    # 真值检查裁决，不停任何容器
```

容器形态（`deploy/router/Dockerfile.router` + `docker-compose.router.yml`，本机已构建并
实测转发/SSE/断连传播）：

- 需要 docker CLI + `/var/run/docker.sock`（唤醒后端）；镜像以 root 运行——socket 本身即
  root 等价权限，故**不要**把 admin 面暴露公网，也不要与租户网络混布。
- **upstream 必须用容器名**（`provision_tenant.py --upstream http://vt-tenant-acme:8080`）
  并与租户同网络；用 `127.0.0.1:<host-port>` 会打到 router 容器自己的 loopback → 连接失败
  → 触发无意义的唤醒（本机实测确认此坑）。
- registry 挂**目录**不挂单文件，且该目录必须是 Docker VM 共享路径：本机（colima）
  `/tmp` **未共享**——挂进去是空目录、单文件挂载会materialize成目录（`IsADirectoryError`）。
  用 `$HOME` 下路径或 named volume；也不要把整个 tenants 目录挂进去（那会把各租户
  明文 `tenant.env` 暴露给 router，它只需要 sha256）。
- ECS 唤醒路径**只文档化未实现**（plan 禁止对真实基础设施调 ECS API）：配方见
  `router/backend.py::EcsBackend` docstring（`DescribeTasks`/`UpdateService desiredCount=1`
  + ALB 目标组健康，或 Lambda 唤醒 authorizer；真值检查走 `ExecuteCommand`/service-connect）。

### T11.3 验证

```bash
# 无 docker 的单测（路由解析/Host 保留/SSE 头/栅栏/回收真值/开通渲染）
pytest OpencodeAgent/tests/ -q      # 无 docker：路由/Host 保留/SSE 头/栅栏/回收真值/开通渲染
# 双租户全链 E2E（需 T10 镜像 + 已开通租户；59 项检查，含 1 次真实模型回合）
python deploy/e2e_multi_tenant.py --registry /srv/vt-tenants/tenant_registry.json \
    --tenants-dir /srv/vt-tenants --tenants a,b --measure-resources \
    --out .omo/evidence/opencode-engine-bridge-v2/t11-router
```

T10 的 `tests/test_config_render.py`（47 项）不受影响：开通脚本不渲染 `opencode.json`。
T12 的隔离矩阵直接复用 `deploy/e2e_rig.py`（`Rig`/`RouterProcess`/`Recorder`/`docker_rss_mb`）
+ `--tenants` 参数化 + `--measure-resources` 采样钩子（本机实测每容器 ~1.0–1.4 GiB）。

---


## 0. 部署架构

| 角色 | 地址 | 说明 |
|------|------|------|
| 部署目标机 | `120.26.181.156`（公网） | 阿里云 ECS，Alibaba Cloud Linux 8，systemd 托管 |
| ├ 对外入口 | `http://120.26.181.156:4096` | **nginx**（`conf.d/opencode-web.conf`）：固定串码 Basic Auth（用户 `vibe`），注入后端凭证后反代 |
| └ 内部服务 | `127.0.0.1:4097` | `opencode-web.service`：`opencode web`，**仅监听回环**，不直接暴露公网 |
| ClickHouse 数据仓库 | `47.98.53.40`（公网）/ `172.24.165.51`（VPC 内网） | Docker 容器 `clickhouse`（clickhouse-server:24.8），HTTP `:8123` / native `:9000`，库 `ashare`（57 张表） |

访问链路：浏览器/CLI → `http://120.26.181.156:4096`（nginx 校验串码，注入 `Authorization: Basic <opencode 后端凭证>`）→ `127.0.0.1:4097` opencode web → VT MCP server（`ch_*` 语义层工具经 `llm_role` 只读账户走 VPC 内网访问 `172.24.165.51:8123`）。

> **凭证管理**：本仓库为 public fork，所有密钥/口令**不写入本文档、不提交入库**。
> 全部凭证存放于目标机 `/opt/my-vibe-trading/.env`（`chmod 600`），本文以 `<见服务器 .env>` 引用。
> 网关串码为 `OPENCODE_WEB_GATE_CODE`（同样存于该 `.env`，nginx htpasswd 文件 `/etc/nginx/opencode-web.htpasswd` 属 `root:nginx 640`）。

## 1. 目录布局

```
/opt/my-vibe-trading/                  # agent 工作目录（opencode serve 的 cwd）
├── repo/                              # git clone -b mymain（Vibe-Trading 全部代码）
├── .opencode/                         # 项目级配置家目录（不使用 ~/.config/opencode）
│   ├── opencode.json.tmpl             # 配置模板（Jinja2，宿主机路径版）
│   ├── render_config.py               # 渲染器：模板 + 工具治理清单 → opencode.json
│   ├── opencode.json                  # 渲染产物（含 CH 凭证，勿外传）
│   ├── oh-my-openagent.json           # OMO 配置：全部 agent/subagent = alibaba-cn/qwen3.8-max
│   ├── vibe-trading-tools.json        # 工具治理清单（disabled: trading_*）
│   ├── tui.json / package.json
│   └── skills/                        # 5 个 OpenCode skills（data-warehouse / html-report /
│                                      #   periodic-execution / escape-top-microstructure /
│                                      #   research-scenarios）
├── AGENTS.md                          # agent 行为规范（宿主机适配版，见 §5 适配清单）
├── scripts/                           # 计算脚本（microstructure / screening / realtime / vibe_bridge）
├── cron_jobs/                         # 周期任务（manage.py / trigger.sh / notifier.py + logs/ + state/）
├── analysis/  reports/  runs/  tmp/  sql/
├── .vt-memory/                        # VT 记忆库（VT_MEMORY_BASE_DIR，重启不丢失）
└── .env                               # 环境变量（chmod 600，唯一凭证存放点）
```

## 2. 代码部署（禁止 scp，走 git）

```bash
# 首次
git clone -b mymain --single-branch https://github.com/shadowinlife/Vibe-Trading.git /opt/my-vibe-trading/repo

# 后续更新
cd /opt/my-vibe-trading/repo && git pull
/opt/miniconda3/envs/legonanobot/bin/pip install -e . -e OpencodeAgent/nano-search-mcp
systemctl restart opencode-serve
```

fork 仓库为 public，HTTPS 匿名 clone 即可，无需在目标机配置 git 凭证。

## 3. Python 环境（conda legonanobot）

复用目标机既有 conda 环境，editable install 指向 `/opt/my-vibe-trading/repo`：

```bash
/opt/miniconda3/envs/legonanobot/bin/pip install -e /opt/my-vibe-trading/repo \
    -e /opt/my-vibe-trading/repo/OpencodeAgent/nano-search-mcp
```

- Python 3.11.15；`vibe-trading-ai` 以 editable 方式安装（当前 0.1.14 @ mymain）。
- `sqlglot` 是 `ch_*` 工具 AST 守卫的硬依赖，缺失会导致 3 个 ch_* 工具静默缺席（MCP 计数从 82 塌缩）——`pip install -e` 会自动拉取，验证时务必确认。
- MCP server 启动方式：`/opt/miniconda3/envs/legonanobot/bin/python /opt/my-vibe-trading/repo/agent/mcp_server.py`（由渲染后的 opencode.json 调起）。

## 4. opencode 与 oh-my-openagent

```bash
npm install -g opencode-ai@latest     # 当前部署版本 1.18.23
```

- OMO 以 opencode 插件形式装载：opencode.json 中 `"plugin": ["oh-my-openagent@latest"]`，首次启动自动安装到 `.opencode/node_modules/`。
- `.opencode/oh-my-openagent.json` 将**全部** agent/subagent（build / hephaestus / oracle / librarian / explore / multimodal-looker / prometheus / metis / momus / atlas 等）统一配置为 `alibaba-cn/qwen3.8-max` + `reasoningEffort: max`。
- 模型供应商 `alibaba-cn` 的认证来自环境变量 `DASHSCOPE_API_KEY`（systemd EnvironmentFile 注入，不入任何配置文件）。

## 5. 配置目录（项目级 `.opencode/`，非默认全局目录）

- opencode 启动时以 `/opt/my-vibe-trading` 为 cwd，自动发现项目级 `.opencode/`；systemd unit 另以 `OPENCODE_CONFIG=/opt/my-vibe-trading/.opencode/opencode.json` 显式指定。
- **不要**使用默认全局配置目录：旧的 `/root/.config/opencode/opencode.json`（2026-07 旧部署残留）已挪至 `opencode.json.stale-20260828`，防止与项目配置合并串扰。
- 配置渲染（repo `config/render_config.py`，单测 `tests/test_config_render.py` 覆盖）：

```bash
set -a && source /opt/my-vibe-trading/.env && set +a
/opt/miniconda3/envs/legonanobot/bin/python \
    /opt/my-vibe-trading/.opencode/render_config.py \
    --template /opt/my-vibe-trading/.opencode/opencode.json.tmpl \
    --manifest /opt/my-vibe-trading/.opencode/vibe-trading-tools.json \
    --target   /opt/my-vibe-trading/.opencode/opencode.json
```

渲染动作：Jinja2 注入 CH 连接参数 → 合并工具治理清单为 permission deny（`trading_*` 不下发模型）→ 由 `subagents.json` 生成领域子代理节（`agent.quant-agent` / `agent.web-docs-agent`：deny 全 MCP 命名空间 + 白名单 allow）→ JSON 校验后原子写入，并把 `prompts/` 复制到渲染产物旁（opencode 的 `{file:}` 只接受配置文件目录内的引用，探针实测于 1.18.23）。

### repo → 宿主机适配清单（重新部署时必须重做）

| 文件 | 适配内容 |
|------|---------|
| `AGENTS.md` | `/opt/venv` → legonanobot conda；`/workspace` → `/opt/my-vibe-trading`；`/opt/vibe-trading` → `repo/`；容器/compose 措辞 → systemd/宿主机目录 |
| `config/opencode.json.tmpl` | `/opt/venv/bin/*` → `/opt/miniconda3/envs/legonanobot/bin/*`；`/opt/vibe-trading/agent/mcp_server.py` → `repo/agent/mcp_server.py`；`VT_MEMORY_BASE_DIR` `/workspace/.vt-memory` → `/opt/my-vibe-trading/.vt-memory` |
| `config/subagents.json` + `config/prompts/` | 原样同步到 `.opencode/`（prompt 引用为配置目录相对路径，渲染器自动落位，无需改路径） |
| `cron_jobs/trigger.sh`、`cron_jobs/manage.py` | `OPENCODE_API` 默认端口 `4096` → `4097`（repo 内 4096 是容器内端口，勿改 repo） |
| `OpencodeAgent/AGENTS.md`（repo 侧） | 计数口径随 mymain 演进同步（当前 MCP OFF=77 / ON=82、技能 91） |

## 6. 环境变量（`/opt/my-vibe-trading/.env`，chmod 600）

| 变量 | 必填 | 说明 |
|------|:--:|------|
| `DASHSCOPE_API_KEY` | ✅ | DashScope key（qwen3.8-max 推理）。**不入库** —— `<见服务器 .env>` |
| `OPENCODE_SERVER_PASSWORD` | ✅ | HTTP Basic Auth 密码（用户名 `opencode`）。`<见服务器 .env>` |
| `TUSHARE_TOKEN` | ✅ | Tushare Pro token（数据联邦当日补数）。**不入库** —— `<见服务器 .env>` |
| `CLICKHOUSE_HOST` / `CLICKHOUSE_PORT` | ✅ | `172.24.165.51` / `8123`（同 VPC 内网地址） |
| `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD` | ✅ | 读写账户（default），供 loader/脚本。`<见服务器 .env>` |
| `CLICKHOUSE_DATABASE` | ✅ | `ashare` |
| `CLICKHOUSE_LLM_USER` / `CLICKHOUSE_LLM_PASSWORD` | ✅ | `llm_role` 只读账户，专供 `ch_*` 语义层工具；缺失时 ch_* 报错且**绝不回退** default。`<见服务器 .env>` |
| `VT_MEMORY` / `VT_MEMORY_MCP_TOOLS` / `VT_MEMORY_BASE_DIR` | ✅ | `full` / `1` / `/opt/my-vibe-trading/.vt-memory` —— 记忆体系全开（MCP 工具 77→82） |
| `LANGCHAIN_PROVIDER` / `LANGCHAIN_MODEL_NAME` | ✅ | `dashscope` / `qwen3.8-max` |
| `DASHSCOPE_BASE_URL` | ✅ | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `TZ` | 可选 | `Asia/Shanghai` |
| `DINGTALK_WEBHOOK` / `SMTP_HOST` / `SMTP_AUTH_CODE` | 可选 | 周期任务通知通道 |

## 7. systemd 服务

`/etc/systemd/system/opencode-web.service`：

```ini
[Unit]
Description=opencode-web (my-vibe-trading host deployment, web mode)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/my-vibe-trading
EnvironmentFile=-/opt/my-vibe-trading/.env
Environment=OPENCODE_CONFIG=/opt/my-vibe-trading/.opencode/opencode.json
ExecStart=/usr/bin/opencode web --port 4097 --hostname 127.0.0.1
Restart=on-failure
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now opencode-web
```

> 注意：web 进程**只绑 127.0.0.1**——对外流量一律由 nginx :4096 进入（见 §7.1）；cron_jobs 的 `OPENCODE_API=http://127.0.0.1:4097` 不受影响，零改动。
> 旧 `opencode-serve.service`（serve 模式、监听 `0.0.0.0:4097`）已 stop+disable，unit 文件保留作回滚，见 §10。

### 7.1 nginx 串码网关（:4096）

`/etc/nginx/conf.d/opencode-web.conf` 要点（完整文件在服务器上）：

```nginx
map $http_upgrade $ocw_connection { default upgrade; '' ""; }

server {
    listen 4096;
    server_name _;
    auth_basic "opencode-web gate";
    auth_basic_user_file /etc/nginx/opencode-web.htpasswd;   # 用户 vibe + 串码(apr1)
    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:4097;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Upgrade $http_upgrade;              # WebSocket/SSE
        proxy_set_header Connection $ocw_connection;
        proxy_read_timeout 3600s;                            # SSE 长连接
        proxy_buffering off;
        proxy_set_header Authorization "Basic <base64(opencode:OPENCODE_SERVER_PASSWORD)>";
        # ↑ 浏览器只需过 nginx 串码；后端自身密码由 nginx 注入，双层不互相干扰
    }
}
```

改串码：`printf 'vibe:%s\n' "$(openssl passwd -apr1 '新串码')" > /etc/nginx/opencode-web.htpasswd && chown root:nginx !$ && chmod 640 !$ && systemctl reload nginx`（同时更新 `.env` 的 `OPENCODE_WEB_GATE_CODE`）。

## 8. 验证清单

```bash
# 1) 服务活性
systemctl is-active opencode-web           # → active
ss -tlnp | grep 4097                       # → 仅 127.0.0.1:4097（opencode web）
ss -tlnp | grep 4096                       # → 0.0.0.0:4096（nginx）

# 2) 网关三重检查（串码见 .env 的 OPENCODE_WEB_GATE_CODE）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4096/                       # → 401（无串码）
curl -s -o /dev/null -w '%{http_code}\n' -u "vibe:$OPENCODE_WEB_GATE_CODE" http://127.0.0.1:4096/   # → 200
curl -s -o /dev/null -w '%{http_code}\n' --connect-timeout 8 http://120.26.181.156:4097/health    # → 000/拒绝（4097 不公网）

# 3) MCP 工具计数（记忆全开应为 82）
cd /opt/my-vibe-trading/repo
VT_MEMORY=full VT_MEMORY_MCP_TOOLS=1 VT_MEMORY_BASE_DIR=/tmp/vtm-check \
  /opt/miniconda3/envs/legonanobot/bin/python -c "
import sys, asyncio; sys.path.insert(0, 'agent')
from mcp_server import mcp
print('tools:', len(asyncio.run(mcp.list_tools())))"

# 4) 端到端（agent 实跑：记忆 + ClickHouse 双链路）
opencode run --attach "http://opencode:$OPENCODE_SERVER_PASSWORD@127.0.0.1:4097" \
  "请依次调用 vibe-trading 的 memory_status 和 ch_list_tables 两个工具，各用一句话报告结果。"
```

端到端预期：`memory_status` 返回 `status: ok`；`ch_list_tables` 返回 `ok: true, database: ashare, count: 57`。
从外部机器访问：浏览器开 `http://120.26.181.156:4096`（用户 `vibe` + 串码）；CLI 用 `opencode run --attach "http://vibe:$OPENCODE_WEB_GATE_CODE@120.26.181.156:4096"`（nginx 代为注入后端凭证）。

## 9. 日常运维

```bash
journalctl -u opencode-web -f              # 日志
systemctl restart opencode-web             # 重启
nginx -t && systemctl reload nginx         # 改网关配置后
docker exec clickhouse ...                 # （在 47.98.53.40 上）CH 运维
```

- **升级代码**：见 §2；`git pull` 后必须重跑 `pip install -e`（依赖可能新增）再重启服务。
- **修改配置**：改 `.opencode/opencode.json.tmpl` 或 `.env` 后，重跑 §5 渲染命令再重启。
- **周期任务**：`cron_jobs/manage.py`（注册/暂停/验证），每次执行必须发钉钉通知（见 AGENTS.md 周期任务规范）。
- **数据同步**：CH 数据由 47.98.53.40 上的外部同步进程维护，本机不含同步逻辑；`ashare.table_sync_state` 可查每日同步状态，`is_sync=0`（如"partial-write suspected"）属 fail-closed 设计，次日自动重试。

## 10. 旧部署处置（2026-08-28 记录）

| 旧部署 | 处置 | 回滚方式 |
|--------|------|---------|
| 宿主机 `opencode-serve.service`（serve 模式，`0.0.0.0:4097` 直出公网） | `systemctl stop` + `disable`，unit 文件保留；被 `opencode-web.service`（web 模式，`127.0.0.1:4097`）+ nginx :4096 串码网关取代 | `systemctl disable --now opencode-web` 后 `systemctl enable --now opencode-serve`，并摘除 `conf.d/opencode-web.conf` reload nginx |
| 容器 `opencode-serve`（镜像 v2.1.1-mymain，端口 4097→4096） | `docker stop` + `restart=no`，容器与命名卷保留 | `docker start opencode-serve` |
| 宿主机 `opencode-web.service`（:4096，2026-07-02 旧代码 `/opt/Vibe-Trading`） | `systemctl stop` + `disable`；unit 文件已于 2026-08-28 晚被同名新 unit（web 模式，`/opt/my-vibe-trading`）覆盖 | 需重建 unit 指向旧代码目录 |
| 宿主机 `ocwatch.service`（活动面板） | `systemctl stop` + `disable` | 同上 |

## 附录 A：安全注意

1. **:4096 公网监听**，前置 nginx 固定串码 Basic Auth（用户 `vibe`），后端 `opencode web` 只绑 `127.0.0.1:4097` 且自带 `OPENCODE_SERVER_PASSWORD` 由 nginx 注入——双层认证；务必在阿里云安全组按源 IP 收敛 4096 的入方向。4097 已不再监听公网（收敛前若有安全组规则可一并移除）。
2. 全部凭证仅存 `/opt/my-vibe-trading/.env`（0600），含 `OPENCODE_WEB_GATE_CODE` 串码备份；htpasswd 文件 `/etc/nginx/opencode-web.htpasswd` 为 `root:nginx 640`（nginx worker 需可读，过严会出现 401→500）。本仓库（public fork）中不得出现任何密钥/口令/串码。
3. `ch_*` 工具强制使用 `llm_role` 只读账户，与读写账户隔离；不要为图方便把 default 账户填进 `CLICKHOUSE_LLM_*`。
4. CH 实例（47.98.53.40）8123/9000 监听 `0.0.0.0`，同样建议安全组收敛至 VPC 内网 + 运维 IP。

## 附录 B：旧容器化部署（保留参考）

镜像构建链路（`Dockerfile` / `Dockerfile.amd64` / `build.sh` / `docker-compose.yml`）仍保留在本目录，可用于其他机器的容器化部署；对应旧版操作说明见 git 历史中本文件的 2026-08-28 前版本。容器内配置渲染/探活逻辑（`entrypoint.sh` → `config/render_config.py`）与本文 §5 共用同一实现。
