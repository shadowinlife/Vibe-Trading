1. 当前项目在开发时需要使用`legonanobot`这个`conda env`
2. 2. tushare 的 token 配置在 `agent/.env` 的 `TUSHARE_TOKEN`（本地调试凭据，不入库）; 任何密钥/token 一律不得写入 commit 或 tracked 文件
3. 3. 整个项目的编码风格必须符合开源社区硬性标准, 所有提交需要先经过通过`code-review`的skills进行复核
4. 4. 最终输出给我的内容使用`:zh`中文`
5. 5. 对 GitHub PR/Issue 进行评论时, 使用 `gh` CLI (如 `gh pr comment <number> --repo <owner/repo> --body '...'`) 而非 GitHub API MCP 工具, 因为 PAT token 对上游仓库没有写入权限
6. 6. **提交PR前必须完整阅读所有社区约束性文件** — 每次提交PR前, 必须先逐条阅读以下全部文件并检查所有细节, 确认全部符合后才能将PR从Draft转为正式提交。未完成完整检查的PR一律只能保持Draft形态:
7.    - `CONTRIBUTING.md` — DCO签发(`git commit -s`)、禁止AI trailers、代码风格(black/ruff)、文件行数上限(400行practical/800行hard cap)、无硬编码密钥/路径/URL、删除未使用代码
8.    - `AGENT_CONTRIBUTOR_GUIDE.md` — 安全本地检查命令、高风险操作需explicit approval(broker订单/OAuth/MCP/部署/force-push等)、安全规则(禁止commit密钥/token)、PR必须包含(goal/affected areas/out of scope/test plan/risk/rollback)、禁止AI-assistant attribution trailers
9.    - `SECURITY.md` — 安全漏洞报告政策
10.    - `.github/PULL_REQUEST_TEMPLATE.md` — PR模板: Summary/Why/Changes/Test Plan/Checklist(无保护区域变更/无硬编码值/遵循CONTRIBUTING.md/文档已更新)
11. 7. **提交commit时禁止包含任何AI标记** — CONTRIBUTING.md和AGENT_CONTRIBUTOR_GUIDE.md均明确规定: 禁止添加`Co-Authored-By:` trailers和AI归属行(AI-Model, AI-Contributed等)到commit message或PR description中。DCO `Signed-off-by:`是唯一允许的commit trailer。每次commit使用`git commit -s`签名。此规则优先于全局AGENTS.md中的AI Commit Tracking规则。

# 8. mymain 分支特有约定

> **当前分支定位**: `mymain` 是开源社区 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) `main` 分支的一个**独立部署分支**，路线为**纯粹的 opencode + omo + VT MCP 集成**（2026-09-27 起）：opencode 作为 harness 直接消费本仓库 MCP 全工具面。前端兼容路线（F8 引擎桥/多租户）**不在本分支**，原独立演进于 `mymain-engine-bridge` 分支，**2026-10-10 已废弃封存**（opencode 作为桥接 agents 的方式被证明不可行；分支保留为只读档案，不再演进）。
>
> **分支知识库**: `mymain-wiki/AGENTS.md` 是本分支持久记忆的路由入口（功能差异、开发历史、验证证据、研究裁决、未落地资产）。在此分支上工作前先读它，按需深入。

## 8.1 与上游的关系

- **周期性对齐**: 本分支需要周期性从开源社区 `main` 对齐回全套 patch（通常每周一次，或重大发布后立即跟进）。本仓库远端命名：`origin` = HKUDS/Vibe-Trading（上游），`fork` = shadowinlife/Vibe-Trading。对齐机制（rebase 或 merge+carve）与五步流程见 `mymain-wiki/branch/MYMAIN_DIVERGENCE.md` §4.1。
- **差异追踪**: 所有与上游 `main` 的功能差异记录在 `mymain-wiki/branch/MYMAIN_DIVERGENCE.md` 中。每次 rebase 后需更新该文档。
- **冲突处理**: rebase 冲突优先保留本分支的本地改造逻辑，但需确保不与上游新增功能冲突。

## 8.2 本地独特改造

本分支在以下方面拥有独特的本地改造，这些差异集中在以下目录：

| 改造领域 | 涉及目录/文件 | 说明 |
|----------|--------------|------|
| **数据访问层** | `agent/backtest/loaders/`、`agent/src/market_data.py`、`agent/src/tools/` 中的 flow 工具 | ClickHouse 作为 A 股主力数据源（`clickhouse` 优先于 `tencent`），数据联邦模式（CH 提供 T-1 历史 199 列 + 网络源提供当日 OHLCV） |
| **记忆管理** | `agent/src/memory/`（reflections、mcp_adapter、lifecycle、hierarchy、persistent、memory_guard） | 反思课程存储（JSONL append-only）、MCP 记忆工具（5 个）、MemoryGuard 中间件（FastMCP middleware，自动触发 memory_save + memory_reflect）、回测自动反思钩子、层级路由文件名修复、`VT_MEMORY_BASE_DIR` 支持项目目录存储 |
| **Agent harness 层** | `OpencodeAgent/`（2026-08-17 引入，源自 vibetrading-opencode-instruct） | opencode + omo + 本仓库 MCP 的独立部署 harness（Docker 镜像 opencode-serve）：问题处理协议（四类分流 + 轮次预算）、防幻觉与诚实拒答纪律、escape-top 微观结构信号、三层选股、cron 通知基础设施；消费 ch_* 语义层工具与记忆能力，个人部署独有不回流。**2026-09-27 回退**：12 领域子代理层与 nano-search-mcp 已移除（验证不够有效），opencode 主循环直接集成 VT MCP 全工具面；通用检索由 VT `web_search` 承接，A 股结构化检索为已知缺口待未来引擎 |

## 8.3 社区贡献约定

1. **所有功能性 patch 必须遵守社区规范**: 每个 patch 在提交前必须通过 `CONTRIBUTING.md`、`AGENT_CONTRIBUTOR_GUIDE.md`、`SECURITY.md` 的完整检查。
2. **随演进随时贡献回社区**: 每个独立的功能 patch 在本地验证稳定后，立即以独立 PR 形式提交到上游 `HKUDS/Vibe-Trading`。PR 保持 Draft 形态直到完整检查通过。
3. **commit message 清洁**: 提交到社区的 commit 不得包含 `AI-Contributed`、`AI-Model`、`Co-Authored-By` 等 AI 归属行。使用 `git commit -s` 签名 DCO。
4. **PR 描述模板**: 每个社区 PR 必须包含: Summary / Why / Changes / Test Plan / Checklist（参考 `.github/PULL_REQUEST_TEMPLATE.md`）。
5. **差异文档同步**: 社区 PR 合入后，从 `mymain-wiki/branch/MYMAIN_DIVERGENCE.md` 移除对应条目。全部合入后本分支回归为纯跟踪分支。

## 8.4 分支与提交纪律（2026-10-29 起）

1. **`mymain` 是本地开发的保护分支**：禁止在 `mymain` 上直接提交功能改动；本地开发必须从 `mymain` 拉出独立 worktree 与功能分支，完成验证后再通过 PR 合并回 `mymain`。
2. **标准流程**：

   ```bash
   git fetch fork
   git worktree add ../mymain-<feature> -b feat/<feature> fork/mymain
   # 在 worktree 内开发、验证、git commit -s
   git push fork feat/<feature>
   gh pr create --repo shadowinlife/Vibe-Trading --base mymain --head feat/<feature>
   # 合并后
   git worktree remove ../mymain-<feature>
   ```

3. **每个全新 feature 必须打 tag**：不可变命名（语义化版本 + 日期，如 `vX.Y.Z-<feature>-YYYYMMDD`），打在合并点或 PR head 上并推送到 fork；禁止移动、覆盖或复用已有 tag。
4. **提交与 PR 纪律**：`git commit -s`（DCO 是唯一允许的 trailer），禁止任何 AI 归属行；PR 描述遵循 `.github/PULL_REQUEST_TEMPLATE.md`，合入前跑通对应测试与 `--check` 门。
5. 既有历史提交不追溯重写；本规则自本节合入后生效。

# 9. 环境约束（强制）

1. **当前环境禁止 `scp` 命令**: 任何文件传输、部署、上传、同步任务一律不得使用 `scp`。需要跨主机复制时改用 `rsync`、`ssh <host> 'cat > <path>'`、`git push`/`git fetch`，或目标平台提供的 API/CLI；脚本、文档、示例与子代理提示词中也不得出现 `scp` 调用。
2. **违规即阻断**: 若某个既有脚本或文档依赖 `scp`，必须先就地替换为上述替代方案再执行；不得以“历史遗留”为由放行。

# 10. 服务启动就绪纪律（opencode sandbox / ACS）

> 来源：2026-10-23 ACS sandbox startupProbe canary 与镜像预烘。本节是 `mymain` 本地分支知识，**不回流上游**。

## 10.1 故障形状

生产 2026-10-22 连续三次置备失败，形状固定：

- sandbox 实际创建成功，pod 进入 `Running`，容器内 `opencode serve` 已 bind `0.0.0.0:4096`；
- 首次 `/config` bootstrap（plugin SDK 安装、skill index、`opencode.db`）尚未完成，请求 TCP 能连上却拿不到响应；
- 上游 route-wait 在应用就绪前反复探测，日志表现为 `ReadTimeout` / `503` / `route not ready after Ns`，最终判失败并 kill sandbox；
- 生产模板没有 probe。Kubernetes 的 `Ready` 是 pod 级：容器进程起来就算，不等于应用可服务。

`Ready` 在任何时刻都不证明「首次请求会很快返回」，它只证明容器没死。

## 10.2 根因

`opencode serve` 的启动顺序是「先 bind 端口，再做首次初始化」。bind 之后端口立即可连，而首次 bootstrap 会阻塞 event loop 数十秒。没有 `startupProbe` 时，manager 的 WaitReady 只等到 pod `Ready`，随即把 route 标成 `running`，把尚未 warm 的应用直接暴露给上游。

任何「先 listen 后初始化」的长驻进程都有这个形状，不是 opencode 独有。

## 10.3 强制规则：就绪必须由真实 warm 路径定义

1. **「进程存活 / 端口可连」不是就绪**。凡是 bind 早于初始化的服务，必须提供与真实业务路径一致的 readiness 端点（本项目为 `GET /config?directory=/home/opencode`），并由 `startupProbe` 或 `readinessProbe` 门控。
2. **优先 `startupProbe`**：它在成功前抑制 readiness/liveness，且不会因为启动慢被重启。`readinessProbe` 只适合启动快、但可能阶段性失去服务能力的场景。
3. **probe 必须打真实入口**。探一个静态 `/health`、而业务入口另有 bootstrap 成本，等于没门控。
4. **禁止用「进程存活 + 端口可连」的脚本或 sidecar 推断就绪**。

## 10.4 预算规则

probe 预算必须覆盖 bootstrap 尾延迟，并与上游 route-wait / create timeout 同向对齐。**不允许**只放大 route-wait 预算来掩盖慢启动：那只是把失败推迟，并把 bootstrap 期间的 `ReadTimeout` 变成常态。

生产 canary 采用的具体 profile：

```yaml
startupProbe:
  exec:
    command:
      - /bin/bash
      - -c
      - 'curl -sf --max-time 40 -u "opencode:${OPENCODE_SERVER_PASSWORD}" "http://127.0.0.1:4096/config?directory=/home/opencode"'
  periodSeconds: 5
  timeoutSeconds: 45
  failureThreshold: 6
```

- 单次等待 45s 覆盖正常约 35s 的 bootstrap；首次 probe 可能在 server listen 前快速失败，随后 5s 周期会在 warm 完成后通过（canary 实测 00:04:01 失败一次，00:04:23 后成功）。
- 总预算 `6 × (45s + 5s) ≈ 300s`，与 `RM_ROUTE_WAIT_BUDGET_S=300` 对齐；
- 若 `RM_CREATE_TIMEOUT_S`、CLI `--request-timeout` 或前端 loader 上限变化，probe 总预算必须**一起**重算，不能单边放宽；
- 若 probe 反而拖慢 bootstrap（短请求反复取消初始化），先加 `timeoutSeconds`（如 120），不要加 probe 次数。

## 10.5 镜像打包规则：慢启动在构建期就该失败

发布前必须执行：

```bash
cd OpencodeAgent
./scripts/check_startup_readiness.sh
```

- 默认预算 60s、`OFFLINE=1`（`--network none`），量的是「容器启动到首次认证 `GET /config` 返回 200」的墙钟；
- 判定：日志必须同时出现 `opencode server listening` 与 `[entrypoint] warmup complete`，且首次 `/config` 200 不超过 `STARTUP_BUDGET_S`；
- 缺少 `warmup complete` 或首次 200 超预算时脚本非零退出，**构建/发布应据此失败**；
- `REQUIRE_IMAGE=1` 把「本地镜像不存在」从 `SKIP` 升级为失败，发布流水线应置 1。

镜像侧已完成预烘：`OpencodeAgent/Dockerfile` 的 `PREBAKE_PLUGIN_SDK=1`（默认开启，`--build-arg PREBAKE_PLUGIN_SDK=0` 可关闭）把 `@opencode-ai/plugin` 同时装进 `/home/opencode/.config/opencode` 与 `/home/opencode/.opencode`。两处都必须有：只烘 global 目录时，项目目录仍会在离线状态下重试安装。

实测（amd64 镜像在 aarch64 + QEMU 下，离线）：

| 场景 | 首次 `/config` 200 |
|---|---|
| 预烘前（v3.8.0 原镜像） | 78.6s |
| 预烘后 | 8.0s |
| 预烘后独立复跑 | 8.2s |

native amd64 的生产 bootstrap 约 35s（生产观测值，不是 QEMU）。QEMU 数字只能用于同环境前后对比，不能当作生产时延。

v3.8.2（opencode 1.19.3 + OMO 5.1.35）QEMU 离线实测：listening 6.8s，首次 `/config` 200 = 164.3s；`STARTUP_BUDGET_S=300` 通过。OMO 5.1.40 在 opencode 1.19.3 上首次 `/config` 挂起，镜像 pin 5.1.35。

## 10.6 Canary 证据（2026-10-23）

一次成功的 create 时序：

| 项 | 值 |
|---|---|
| `claim_s` | 43.42s |
| pod Ready | `1/2 Running` 直到 bootstrap 完成，随后 `2/2` |
| `Startup probe failed` | 00:04:01Z，此时 server 尚未 listening（listening 日志在 00:04:04），属预期噪声 |
| `warmup complete` | 00:04:23.853Z |
| `Ready=True` | 00:04:23Z（k8s 只到秒） |
| `RESTARTS` | 0 |
| route-wait | `{"ready": true, "route_ready_s": 0.1, "attempts": 1}` |

对照历史失败形状（`claim 77s 到 89s` + `route 35s 到 61s`），本次是单次 create 成功、单次 route-wait 成功、零重启。三条 caveat 必须一起记住，不要把结论说过头：

1. `Ready` 的 `lastTransitionTime` 只到秒级，无法证明「200 早于 Ready」在毫秒尺度成立；能证明的是采样序列里没有 Ready 早于 bootstrap。
2. `claim_s=43.42` 与 bootstrap 结束相差 0.4 到 3.4s（受 ssh 启动与 pod 创建时间的区间约束），严格意义的「claim_s 包含完整 bootstrap」**未被证明**；被证明的是旧的失败形状不再出现。
3. route-wait 在 create 返回约 10s 后才调用，`0.1s` 只是「调用时已就绪」的快照；路由传播的真实耗时上界应读作不超过约 10s。

## 10.7 指针

- 执行计划与通过条件：`.omo/plans/sandbox-startup-probe-canary.md`
- 原始证据：`.omo/evidence/sandbox-startup-probe/`（`t3-ready-timeline.txt`、`t4-route.txt`、`t9-image-check.txt`）
- 生产模板建议 patch：`.omo/evidence/sandbox-startup-probe/t6-prod-patch.diff`。**仅作为 HIL review 输入，未 apply**；apply 前需确认生产 sandbox 已归零且模板保持 `replicas: 0`。
- 打包期检查脚本与失败处置：`OpencodeAgent/scripts/check_startup_readiness.sh`、`OpencodeAgent/docs/IMAGE-MANUAL.md` §9.5
