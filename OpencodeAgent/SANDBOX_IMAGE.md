# opencode-sandbox-poc — ACS Agent Sandbox 池镜像构建正典

> **出处**：2026-09-25 自 aliyun-acs-agent-service（Resource Manager）项目移交（用户裁决：镜像线由
> vibe-trading 项目负责维护）。原驻 worktree `feat/acs-sandbox-integration`（@ f7af06f7，已退役，
> 分支保留可考古）。`Dockerfile.sandbox-poc` 与该权威源逐字节一致。
> **消费方**：RM 沙箱池模板 `opencode-poc`（ACS Agent Sandbox）。
> **未来**：镜像版本控制 / 环境变量注入等在 RM 管理后台承接（Backlog，2026-09-25 裁决）。

## 现网基线

| 项 | 值 |
|---|---|
| 镜像 | `<ACR-VPC-ENDPOINT>/test/opencode:v0.1`（集群内 pull 必须走 VPC 端点） |
| digest | `sha256:79bd36fc802ba2e991a472d742271326f301a1110db28a277b5efaf87480ed4a`（内容不变则跨仓一致） |
| 尺寸 | 1.39GB 未压缩（PoC 预算 ≤1.5GB） |
| 平台 | linux/amd64（arm64 host colima 交叉构建） |

## 版本钉矩阵（升级前必读）

| 组件 | 钉版 | 说明 |
|---|---|---|
| 基础镜像 | `node:20-bookworm-slim` | Debian 12；自带 bash + coreutils（平台注入的 envd postStart hook 硬依赖） |
| opencode | `opencode-ai@1.18.30` | 漂移纪律：与生产 Dockerfile 钉版一致 |
| omo | `oh-my-openagent@4.19.4` | Tier-a 构建期全局安装；失败自动落 Tier-b 运行时插件（沙箱首启 ~30s 自装，全栈线已验证模式） |

## 构建

```bash
# arm64 host 交叉构建 amd64。P30：构建期 npm 只落文件、不执行目标架构二进制 → 无 SIGILL；
# tier-a omo 安装若执行 native 代码失败 → Dockerfile 内置 tier-b 回退，构建不会因此失败。
docker build --platform=linux/amd64 \
  -f OpencodeAgent/Dockerfile.sandbox-poc \
  -t opencode-sandbox-poc:v0.1 OpencodeAgent/
```

## 硬性约束（实测实证，勿破坏）

1. **ENTRYPOINT 必须长驻前台进程**（现 `sleep infinity`）——CMD 退出即被平台判 "sandbox state is dead"，claim 失败（T2 实证 / P05）。
2. **零密钥**——LLM auth 全运行时注入（claim 时经 envd 写 auth.json/envVars）；镜像内不落任何 key。
3. **bash + coreutils 必须在**——平台注入的 envd-run.sh postStart hook 依赖（建 uid-1000 运行用户、nohup 起 envd）。
4. `opencode serve` **不在镜像内启动**——由池模板 command 起，serve 密码经集群 env secret 注入（名称与真实值见 RM 仓本地 INTERNAL 文档，不入本仓）。

## 镜像仓库（ACR EE 实例，占位符化）

| 项 | 值 |
|---|---|
| 公网端点（构建机 push） | `<ACR-PUBLIC-ENDPOINT>`（namespace `test` / repo `opencode`） |
| VPC 端点（集群内 pull 必须） | `<ACR-VPC-ENDPOINT>` |
| 凭据 | 本机 `~/.acs-sandbox/acr-credentials`（600）+ 集群 imagePullSecret（dockerconfigjson **必须同时含公网+VPC 两 host 的 auths**，P13 怪癖：带 scope 的 token 端点对垃圾凭证也签匿名 token，验凭证用无 scope+Basic ping） |
| 真实值 | RM 仓本地 `ACS_DEPLOYMENT_INTERNAL.md` §3.3（gitignored；**本文档零凭据纪律**） |

## 与 RM 的集成点

- 池模板 `opencode-poc`（SandboxSet）引用 VPC 端点镜像；**镜像升级 = 更新池模板引用并重建池**（存量沙箱 env/镜像不热更）。
- serve 密码轮换需同步三处：本机文件、集群 env secret、重建池（见 INTERNAL §3.2 轮换注意）。
- RM 仓相关正典：`docs/repo-map.md`（跨仓地图）· INTERNAL §3.4/§3.6（本地）· ADR-002（镜像线出 RM 仓范围）。
