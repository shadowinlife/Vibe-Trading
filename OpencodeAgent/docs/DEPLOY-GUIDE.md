# opencode-serve 部署指南（宿主机直部署）

> 当前线上形态（2026-08-28 起）：**宿主机 systemd 直部署**，取代此前的 Docker 容器方案。
> 容器化构建文件（`Dockerfile` / `build.sh` / `docker-compose.yml`）仍保留在仓库中，见附录 B。

## 0. 部署架构

| 角色 | 地址 | 说明 |
|------|------|------|
| 部署目标机 | `120.26.181.156`（公网） | 阿里云 ECS，Alibaba Cloud Linux 8，systemd 托管 |
| ClickHouse 数据仓库 | `47.98.53.40`（公网）/ `172.24.165.51`（VPC 内网） | Docker 容器 `clickhouse`（clickhouse-server:24.8），HTTP `:8123` / native `:9000`，库 `ashare`（57 张表） |

访问链路：客户端 → `http://120.26.181.156:4097`（HTTP Basic Auth）→ opencode serve → VT MCP server（`ch_*` 语义层工具经 `llm_role` 只读账户走 VPC 内网访问 `172.24.165.51:8123`）。

> **凭证管理**：本仓库为 public fork，所有密钥/口令**不写入本文档、不提交入库**。
> 全部凭证存放于目标机 `/opt/my-vibe-trading/.env`（`chmod 600`），本文以 `<见服务器 .env>` 引用。

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

渲染动作：Jinja2 注入 CH 连接参数 → 合并工具治理清单为 permission deny（`trading_*` 不下发模型）→ JSON 校验后原子写入。

### repo → 宿主机适配清单（重新部署时必须重做）

| 文件 | 适配内容 |
|------|---------|
| `AGENTS.md` | `/opt/venv` → legonanobot conda；`/workspace` → `/opt/my-vibe-trading`；`/opt/vibe-trading` → `repo/`；容器/compose 措辞 → systemd/宿主机目录 |
| `config/opencode.json.tmpl` | `/opt/venv/bin/*` → `/opt/miniconda3/envs/legonanobot/bin/*`；`/opt/vibe-trading/agent/mcp_server.py` → `repo/agent/mcp_server.py`；`VT_MEMORY_BASE_DIR` `/workspace/.vt-memory` → `/opt/my-vibe-trading/.vt-memory` |
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

`/etc/systemd/system/opencode-serve.service`：

```ini
[Unit]
Description=opencode-serve (my-vibe-trading host deployment)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/my-vibe-trading
EnvironmentFile=-/opt/my-vibe-trading/.env
Environment=OPENCODE_CONFIG=/opt/my-vibe-trading/.opencode/opencode.json
ExecStart=/usr/bin/opencode serve --port 4097 --hostname 0.0.0.0
Restart=on-failure
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now opencode-serve
```

## 8. 验证清单

```bash
# 1) 服务活性
systemctl is-active opencode-serve          # → active
ss -tlnp | grep 4097                        # → opencode 进程监听

# 2) 健康检查（无凭证应 401，有凭证应 200）
curl -u "opencode:$OPENCODE_SERVER_PASSWORD" http://localhost:4097/health

# 3) MCP 工具计数（记忆全开应为 82）
cd /opt/my-vibe-trading/repo
VT_MEMORY=full VT_MEMORY_MCP_TOOLS=1 VT_MEMORY_BASE_DIR=/tmp/vtm-check \
  /opt/miniconda3/envs/legonanobot/bin/python -c "
import sys, asyncio; sys.path.insert(0, 'agent')
from mcp_server import mcp
print('tools:', len(asyncio.run(mcp.list_tools())))"

# 4) 端到端（agent 实跑：记忆 + ClickHouse 双链路）
opencode run --attach "http://opencode:$OPENCODE_SERVER_PASSWORD@localhost:4097" \
  "请依次调用 vibe-trading 的 memory_status 和 ch_list_tables 两个工具，各用一句话报告结果。"
```

端到端预期：`memory_status` 返回 `status: ok`；`ch_list_tables` 返回 `ok: true, database: ashare, count: 57`。

## 9. 日常运维

```bash
journalctl -u opencode-serve -f            # 日志
systemctl restart opencode-serve           # 重启
docker exec clickhouse ...                 # （在 47.98.53.40 上）CH 运维
```

- **升级代码**：见 §2；`git pull` 后必须重跑 `pip install -e`（依赖可能新增）再重启服务。
- **修改配置**：改 `.opencode/opencode.json.tmpl` 或 `.env` 后，重跑 §5 渲染命令再重启。
- **周期任务**：`cron_jobs/manage.py`（注册/暂停/验证），每次执行必须发钉钉通知（见 AGENTS.md 周期任务规范）。
- **数据同步**：CH 数据由 47.98.53.40 上的外部同步进程维护，本机不含同步逻辑；`ashare.table_sync_state` 可查每日同步状态，`is_sync=0`（如"partial-write suspected"）属 fail-closed 设计，次日自动重试。

## 10. 旧部署处置（2026-08-28 记录）

| 旧部署 | 处置 | 回滚方式 |
|--------|------|---------|
| 容器 `opencode-serve`（镜像 v2.1.1-mymain，端口 4097→4096） | `docker stop` + `restart=no`，容器与命名卷保留 | `docker start opencode-serve` |
| 宿主机 `opencode-web.service`（:4096，2026-07-02 旧代码 `/opt/Vibe-Trading`） | `systemctl stop` + `disable` | `systemctl enable --now opencode-web` |
| 宿主机 `ocwatch.service`（活动面板） | `systemctl stop` + `disable` | 同上 |

## 附录 A：安全注意

1. **:4097 公网监听**，认证仅有 HTTP Basic 单密码——务必在阿里云安全组按源 IP 收敛 4097 的入方向。
2. 全部凭证仅存 `/opt/my-vibe-trading/.env`（0600）；本仓库（public fork）中不得出现任何密钥/口令。
3. `ch_*` 工具强制使用 `llm_role` 只读账户，与读写账户隔离；不要为图方便把 default 账户填进 `CLICKHOUSE_LLM_*`。
4. CH 实例（47.98.53.40）8123/9000 监听 `0.0.0.0`，同样建议安全组收敛至 VPC 内网 + 运维 IP。

## 附录 B：旧容器化部署（保留参考）

镜像构建链路（`Dockerfile` / `Dockerfile.amd64` / `build.sh` / `docker-compose.yml`）仍保留在本目录，可用于其他机器的容器化部署；对应旧版操作说明见 git 历史中本文件的 2026-08-28 前版本。容器内配置渲染/探活逻辑（`entrypoint.sh` → `config/render_config.py`）与本文 §5 共用同一实现。
