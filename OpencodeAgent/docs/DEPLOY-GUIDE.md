# opencode-web 部署指南（宿主机直部署 + nginx 串码网关）

> 当前线上形态（2026-08-28 晚起）：**宿主机 systemd 直部署 `opencode web`**，对外由 **nginx :4096 固定串码网关**代理，取代此前的 `opencode serve` 直出方案与更早的 Docker 容器方案。
> 容器化构建文件（`Dockerfile` / `build.sh` / `docker-compose.yml`）仍保留在仓库中，见附录 B。

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
