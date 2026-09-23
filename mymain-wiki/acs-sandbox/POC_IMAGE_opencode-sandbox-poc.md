---
title: opencode-sandbox-poc 镜像血统卡（D9 最小 PoC：opencode + omo）
description: ACS Agent Sandbox claim-time 镜像换装 PoC 专用最小镜像的完整血统：范围裁决（D9：仅 opencode-ai@1.18.30 + oh-my-openagent@4.19.4，零 MCP / 零 VT gateway / 零 ClickHouse / 零 supervisord）、base 与版本钉、OMO 两级安装裁决（本次 tier-a 构建期成功）、ENTRYPOINT sleep infinity 依据（T2 spike 长驻前台进程实证）、envd 平台注入说明（镜像不含 envd）、参考配置形状（alibaba-cn provider + DashScope baseURL + qwen3.8-max，无任何密钥）、体积对照（1.39GB vs 全家桶 4.7GB）、与 v3.0.0-tenant 全栈血统的边界、验证状态（QEMU 模拟局限，真实运行时验证在 T8 原生 x86_64 沙箱）。触发词：sandbox-poc、opencode-sandbox-poc、PoC 镜像、镜像血统、D9、claim-time swap、最小镜像、tier-a、sleep infinity、envd、ACR、lineage。
type: reference
status: active
created: 2026-09-23
updated: 2026-09-23
tags: [acs, agent-sandbox, image, poc, opencode, omo, lineage]
related: [ACS_SANDBOX_SMOKE_2026-09-22.md, ../INDEX.md, ../../OpencodeAgent/Dockerfile.sandbox-poc]
---

# POC_IMAGE — opencode-sandbox-poc:v0.1（D9 最小 PoC）

> **一句话**：为验证「claim-time 换装 → `opencode serve` 可被 envd 唤起」而构建的**最小**镜像，只含 opencode CLI + omo 插件与一份无密钥参考配置；它是**本地个人部署制品，不上游**，与全栈 `opencode-serve:v3.0.0-tenant` 血统完全独立、互不影响。

## 1. 范围裁决（D9）

| 维度 | 本 PoC 镜像 | 全栈 v3.0.0-tenant（对照，未动） |
|---|---|---|
| opencode CLI | ✅ `opencode-ai@1.18.30` | ✅ 同钉 |
| OMO 插件 | ✅ `oh-my-openagent@4.19.4`（tier 见 §4） | ✅ 运行时 plugin 自动安装 |
| VT gateway / channels / supervisord | ❌ 无 | ✅ |
| VT MCP（77/82 工具面）+ ClickHouse 语义层 | ❌ 无 | ✅ |
| nano-search-mcp | ❌ 无 | ✅ |
| skills / AGENTS.md / subagents / 量化脚本 | ❌ 无 | ✅ |
| envd | ❌ **不打包**（平台注入，§5） | ❌ 同 |
| 密钥/token | ❌ **零**（运行时经 envVars/files.write 注入） | ❌ 同原则 |
| 体积（未压缩） | **1.39 GB** | 4.7 GB |

D9 判据：PoC 目标是**镜像换装机制本身**（claim-time in-place swap + envd 起 opencode），全栈内容只会放大 push/pull 与排障面，不增加验证信息量。全栈血统继续走原有 `OpencodeAgent/Dockerfile` 谱系，本卡不触碰。

## 2. 构建物与钉版本

- **Dockerfile**：`OpencodeAgent/Dockerfile.sandbox-poc`（仓库内唯一新文件，构建上下文 = `OpencodeAgent/`）。
- **Base**：`node:20-bookworm-slim`（Debian 12，提供 `/bin/bash` + coreutils `cp/mv/mkdir/sleep`，未压缩 ~200MB）。
- **版本钉**（与全栈血统同一漂移纪律，改动需走漂移告警复放）：`opencode-ai@1.18.30`、`oh-my-openagent@4.19.4`。
- 构建命令：`docker build --platform=linux/amd64 -f OpencodeAgent/Dockerfile.sandbox-poc -t opencode-sandbox-poc:v0.1 OpencodeAgent/`
- 镜像 tag（ACR 命名空间一律占位）：`<ACR_NS>/opencode-sandbox-poc:v0.1`
  - digest：`sha256:79bd36fc802ba2e991a472d742271326f301a1110db28a277b5efaf87480ed4a`（manifest 2414 B）
  - **仓库可见性**：新仓库首次推送后默认**私有**，ACS 沙箱集群侧无凭证（T6 实证 `insufficient_scope`）。T8 需要用户/编排方在控制台将 `<ACR_NS>/opencode-sandbox-poc` 切公共（或走 T6 结论的 imagePullSecret 路线）。本卡与镜像内均不含任何注册中心凭证。

## 3. ENTRYPOINT 裁决：`sleep infinity`

T2 claim-time 换装 spike 实证（`.omo/evidence/task-2-swap-spike.json` trials 4–6）：换装镜像的 CMD 若立即退出（`ubuntu:22.04` 的 bash、`bash:5`），容器重启 → `FailedPostStartHook` + `sandbox state is dead` → claim 超时失败；`nginx:latest`（长驻前台 CMD）则通过。本镜像无自带服务，**故意不起任何进程**——`opencode serve` 由后续 claim 流程经 envd 命令显式启动（§6），容器 PID1 只需长驻。`sleep`（coreutils）零输出零依赖；文档化回退 `["tail","-f","/dev/null"]`。

## 4. OMO 两级安装策略与本次裁决

镜像设计容忍两种落地（`/opt/omo-tier` 记录实际结果）：

- **tier-a（构建期）**：`npm install -g oh-my-openagent@4.19.4` 成功——npm 只**放置文件**；只有当 postinstall/native 步骤在 arm64 宿主 `--platform=linux/amd64` QEMU/Rosetta 模拟下**执行**架构相关代码才会 SIGILL 跌到 tier-b。
- **tier-b（运行时兜底）**：包装不上；`opencode.json` 的 `"plugin": ["oh-my-openagent@4.19.4"]` 钉让 opencode 在沙箱内**首次启动时**以原生 x86_64 自动安装（~30s，全栈血统验证过的模式）。

**本次实测：tier-a 成功**（`npm ls -g` 见 `oh-my-openagent@4.19.4`；`opencode --version` 模拟下正常输出 `1.18.30`，postinstall 的 benign `--version` 探针未 SIGILL）。注意：两种 tier 下 baked 配置的 plugin 钉**都保留**——它始终是 opencode 实际加载插件的机制；tier-a 是否让 opencode 首启跳过自装（opencode 是否扫全局 `node_modules`）属 T8 原生环境验证项，不作为本 PoC 的声称。

## 5. envd 注入（镜像不打包）

平台 agent-runtime sidecar 经 emptyDir `/mnt/envd` 注入 `envd-run.sh` 并以 `lifecycle.postStart` 执行：创建 uid-1000 用户（失败非致命，有直接编辑 `/etc/passwd` 回退）→ `nohup $ENVD_DIR/envd -isnotfc`。因此镜像只需满足 bash+coreutils（§2 base 满足）。T2 实证这一 hook 与镜像无关地工作。

## 6. 参考配置 `/opt/opencode-config/opencode.json`

形状派生自仓库 `OpencodeAgent/config/opencode.json.tmpl`（model 默认行、plugin 钉行 55–57）与生产宿主模板 `opencode.json.tmpl.host` 的 provider 块（alibaba-cn / OpenAI 兼容 DashScope baseURL / qwen3.8-max 模型声明）。**无任何 apiKey 占位值**——运行时认证走 opencode `auth.json` 或 claim 时 envVars。`/workspace` 为世界可写 agent 工作目录，claim 流程把该配置复制到 serve 生效位置。

## 7. 构建期裁剪说明

`opencode-ai` 的 npm 包经 optionalDependencies 落 **两个** 177MB x86_64 平台二进制（现代/baseline 变体），postinstall 将选定者 **hardlink** 到 `bin/opencode.exe`（launcher 唯一读取点）。Dockerfile 在安装后删除其嵌套 `node_modules/`（inode 经 hardlink 存活，删后 `opencode --version` 复验通过），使未压缩体积从 1.58GB 降至 **1.39GB**，回到 ≤1.5GB PoC 预算。

## 8. 验证状态与边界

| 项 | 状态 |
|---|---|
| amd64 构建、tier-a、npm ls、bins、config JSON、无密钥扫描 | ✅ 本地实证（证据 `.omo/evidence/task-7-image-build.log`，主仓） |
| `opencode --version` / `--help` 类轻量执行 | ✅ 模拟下通过（比历史预期更好，但不构成运行时承诺） |
| `opencode serve` 真启动、omo 首载、provider 调用、claim-time swap 全流程 | ⏳ **T8**（原生 x86_64 MicroVM，无模拟畸变） |
| ACR 仓库可见性切换 | ⏳ 用户/编排方控制台动作（本文档只登记仓库名占位 `<ACR_NS>/opencode-sandbox-poc`） |

## 9. 血统裁决（个人部署独有，不上游）

PoC 镜像 = **本地个人部署制品**（root AGENTS.md §8.3 同 F5/F7 范畴）：不为上游社区维护、不进入贡献队列、不与任何社区 PR 绑定。其存在意义是 T8 机制验证，终态（继续瘦身成生产沙箱镜像 / 被全栈镜像的 claim 流程取代）由 T8/T9 结果决定。
