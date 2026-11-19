# F3 真实 QA — novice-onboarding-gprime

- 执行日期：2026-10-29（Asia/Shanghai）
- 构建时 HEAD：`4aab6cf4`；报告落盘时 HEAD：`07cee224`（HEAD 在 F3 期间前移，见 §1.2）
- 交付镜像：`opencode-serve:v3.11.2-onboarding-20261028-4aab6cf4`
- 范围：从 HEAD 全量构建；`tests/smoke-v311.sh` Stage 1/2（含一次 `/checkup` LLM 会话）；probe 以来行为面复核；汇总既有的 11 条有界探针证据；记录 40 条语料门未执行的 gap
- 只读边界：未修改产品代码、测试、skill、config 或 plan；未重跑 40 条语料；未重跑 11 条探针；未打 `:latest`、未 push。本报告是本次唯一入库写入物
- 环境：macOS arm64 主机；`linux/amd64` 镜像在 QEMU 下运行；Docker 27.4.0

## 0. 结论

**VERDICT: REJECT**

F3 的验收要求“容器全量构建后 harness 跑 40 语料 + 四条 `/` 命令运行时触发断言；四层分率相对基线达标；token/延迟均值涨幅 ≤10%（预算门）”。本次从 HEAD 的**全量构建、smoke、四条 `/` 命令运行时断言**都通过；但 **40 条语料门没有在本 build 上执行**（本环境三次尝试均未完成，见 §7），F3 缺少核心验收证据，因此不能判过。

在可执行的部分：镜像构建成功（§1），smoke `SMOKE_OK`（§2），四条 `/` 命令在 `GET /command?directory=/workspace` 中可见且 `/checkup` 有非空回复（§3），未发现构建/启动/命令回归。替代性的 11 条有界探针四层一致率全部不低于基线（7/10、8/10、8/10、8/10 vs 6/10、5/10、8/10、6/10，§5.1），token 均值 −2.5% 达标，但该探针不是本 build 的测量（§4 行为面 diff 非空），且探针自身的 wall 延迟 +40.6%、closing block 80% 均未达标（§5.2、§6）。本报告不把替代证据冒充 40 条门，也不把探针数字当作本 build 的复测。

## 1. 构建身份

### 1.1 镜像与构建

| 字段 | 值 |
|---|---|
| Tag | `opencode-serve:v3.11.2-onboarding-20261028-4aab6cf4` |
| Image `Id` | `sha256:eda8e65f4301b7d122c633ecf3b4a74c0ad6bfa8c3a584de4617da0cd8667a63` |
| Size | `4,149,493,655` bytes（4.15 GB） |
| Created | `2026-10-28T16:56:44.214835142Z`（2026-10-29 00:56:44 +0800） |
| Platform | `linux/amd64` |
| `RepoDigests` | `[]`（本地构建，从未 push；不要为它编造 digest） |
| 构建时 HEAD / 镜像内代码 | `4aab6cf4`。`build.sh` 用 `git archive mymain` 把 committed tree 复制到 `vendor/Vibe-Trading`；镜像带 `Config.Labels={"org.opencontainers.image.version":"22.04"}`，无 commit label，因此内容身份以 Image `Id` 为准 |
| Builder | classic/legacy builder（`DOCKER_BUILDKIT=0`；`Step 1/33` … `Step 33/33`，`Successfully built eda8e65f4301`） |
| Base image | `opencode-serve-base:latest`，`Id` `sha256:a15af069e67bb7309977effd4e8e708cb8ece60b518edf61d7f45db1bba29277`（local-only；本次 app 模式未重建 base） |
| OMO 版本 | `5.1.35`（Dockerfile `ARG OMO_VERSION=5.1.35`） |
| 构建命令 | `cd OpencodeAgent && DOCKER_BUILDKIT=0 ./build.sh --tag v3.11.2-onboarding-20261028-$(git rev-parse --short HEAD)`；本次 `git rev-parse --short HEAD` = `4aab6cf4`（8 字符，`core.abbrev=auto`） |
| 构建耗时 | 00:50:47 → 00:56:44 +0800，约 **5m57s** |
| 构建日志 | `/var/folders/1y/6fl87z5s1cdg4y564gbxsp2h0000gp/T/f3-build-20261029-005047.log`（648 行，75,457 bytes；host-side，未入库） |

构建日志关键行：

```text
=== Vendoring Vibe-Trading from .. (mymain branch) ===
=== VT vendored:     1349 Python files ===
...
Successfully built eda8e65f4301
Successfully tagged opencode-serve:v3.11.2-onboarding-20261028-4aab6cf4
=== App image done: opencode-serve:v3.11.2-onboarding-20261028-4aab6cf4 ===
```

构建日志中有一条**非致命**依赖告警，记录但不阻塞：Step 6 的 `pip install` 报 `alpaca-trade-api 3.2.0` 要求 `urllib3<2,>1.24` / `websockets<11,>=9.0`，而环境安装的是 `urllib3 2.8.0` / `websockets 17.3`；pip 继续并完成安装。这是既有依赖噪声，F3 不对依赖策略下结论。

### 1.2 HEAD 在 F3 期间前移（透明记录）

构建开始时 HEAD 是 `4aab6cf4`。F3 运行期间，F1/F2 收尾的两个 **docs-only** commit 落到分支上：

- `7e76673a` `docs(opencode-agent): close F1 after todo-14 escalation record`
- `07cee224` `docs(opencode-agent): close F2 after the smoke line-cap fix`

二者只改 `OpencodeAgent/tests/evals/intake/results/f1_plan_conformance.md` 和 `f2_code_quality.md`，不在镜像 `COPY` 列表里（Dockerfile 只 COPY `vendor/`、`config/`、`AGENTS.md`、`skills/`、`workspace/`、`entrypoint.sh`；`build.sh` 还会把 `vendor/Vibe-Trading/OpencodeAgent` 整个删掉）。为确认这一点，按 build.sh 的排除规则分别重建 `4aab6cf4` 与 `07cee224` 的 vendor tree：两边都是 **2250 个文件**，内容摘要同为 `ba3fdfdad2dc486d7a56a45631a9604fa6c89a22ba89c72d8d1ea6a90f4f67de`，即应用源码逐字节一致。

另用当前 HEAD（`07cee224`）做了一次等价性重建（临时 tag，已删除）：Image `Id` `sha256:b994658f5cc5…`，RootFS 前 11 层（含 vendor COPY）与交付镜像相同，从 **Step 11**（`PREBAKE_PLUGIN_SDK` 的 `npm install @opencode-ai/plugin`）开始层摘要不同。也就是说，差异来自不可复现的 npm 安装层，不是两个 docs commit 带来的源码差异。交付镜像仍是实际跑过 smoke 的 `4aab6cf4` 变体；`07cee224` 变体本身没有再跑一次 smoke（不额外消耗 LLM 会话），这一点如实记录，不冒充已验证。

## 2. Smoke

命令（在 `OpencodeAgent/` 下）：

```bash
bash tests/smoke-v311.sh opencode-serve:v3.11.2-onboarding-20261028-4aab6cf4
```

完整输出：

```text
=== Stage 1: command files + config render (opencode-serve:v3.11.2-onboarding-20261028-4aab6cf4, linux/amd64) ===
COMMAND_FILES_OK
WORKSPACE_CONTENT_OK: AGENTS.md=329 lines (cap 350), skills/intake/SKILL.md present
OK
RENDER_OK: agent.build/plan permission.question=allow
=== Stage 1 PASS ===
=== Stage 2: real startup + runtime command discovery ===
CONFIG_READY after ~15s
COMMAND_ENDPOINT: /command?directory=/workspace
RUNTIME_COMMANDS_OK: backtest checkup customize-opencode data-warehouse debugging escape-top-microstructure frontend git-master goal handoff html-report hyperplan init init-deep intake periodic-execution playwright refactor remove-ai-slops research-scenarios review review-work screen security-research security-review stop-continuation ulw-execute visual-qa websearch:web_search_help
=== Stage 2b: /checkup runtime trigger (one LLM session) ===
CHECKUP_REPLY_OK
=== SMOKE_OK ===
image: opencode-serve:v3.11.2-onboarding-20261028-4aab6cf4
runtime command list: backtest checkup customize-opencode data-warehouse debugging escape-top-microstructure frontend git-master goal handoff html-report hyperplan init init-deep intake periodic-execution playwright refactor remove-ai-slops research-scenarios review review-work screen security-research security-review stop-continuation ulw-execute visual-qa websearch:web_search_help
checkup reply (first 200 chars): ClickHouse 语义层首次连接超时，我继续使用网络源并行取数（稍后重试 CH）。
```

- Stage 1：四个命令文件（`checkup` / `screen` / `backtest` / `review`）存在且 frontmatter、`description`、`argument-hint`、`research-scenarios` 路由齐全；`skills/intake/SKILL.md` 存在；`/workspace/AGENTS.md` **329 行**（350 行上限、300 行下限）；`render_config.py` 渲染通过，`agent.build` / `agent.plan` 的 `permission.question=allow`。
- Stage 2：带认证的 `GET /config?directory=/workspace` 返回 200；命令端点从 `GET /doc` 发现（不是猜的），`GET /command?directory=/workspace` 返回 29 个运行时命令，四个必需命令是其中的子集；`opencode run --attach … --command checkup --format json "600519"` 退出 0，回复非空。
- Smoke 日志：`/var/folders/1y/6fl87z5s1cdg4y564gbxsp2h0000gp/T/f3-smoke-rerun-20261029-011658.log`；本次 smoke 墙钟约 6m01s（含一次 `/checkup` LLM 会话）。
- 本次 F3 的 LLM 会话数：**1**（成功的 smoke Stage 2b；首次失败的 smoke 未进入 Stage 2b）。

### 2.1 首次 smoke 调用的 transient（如实记录）

第一次调用（2026-10-29 01:00:10，日志 `…/f3-smoke-20261029-010010.log`）在 Stage 2 打印头之后**没有出现 `CONFIG_READY`**，15 分钟后被工具超时终止；trap 清理了容器，`docker ps -a` 为空。紧接着的第二次调用在 `CONFIG_READY after ~15s` 后完整通过。

这不是可复现的镜像失败（同一镜像、同一命令，第二次 15s 就绪），但暴露了一个 harness 硬化项：`smoke-v311.sh` 的 readiness 循环里 `curl -sf` **没有 `--max-time`**，如果请求落在 bootstrap 的 bind-早于-warm 窗口，单次请求可以挂过名义上的 300s 循环预算。按 AGENTS.md §10.3 的就绪纪律，后续应给 readiness 探测加单次超时；F3 不改测试，只记录。

## 3. 四条 `/` 命令的运行时证据

`GET /doc` 暴露的端点是 `/command?directory=/workspace`；`GET` 返回 29 个命令名（完整列表见 §2）。四个必需命令的运行时存在性：

| 命令 | 运行时列表中存在 | 附加证据 |
|---|---|---|
| `/checkup` | 是 | Stage 2b 实际触发一次会话，`CHECKUP_REPLY_OK` |
| `/screen` | 是 | 在 `RUNTIME_COMMANDS_OK` 列表中 |
| `/backtest` | 是 | 在 `RUNTIME_COMMANDS_OK` 列表中 |
| `/review` | 是 | 在 `RUNTIME_COMMANDS_OK` 列表中 |

运行时列表是**必需子集**断言，不是精确快照：29 个名字里同时包含 OMO/内置命令（如 `init`、`review-work`、`security-research`），多出的命令不会让 smoke 失败。

`/checkup` 回复（smoke 截取的前 200 字符）：

```text
ClickHouse 语义层首次连接超时，我继续使用网络源并行取数（稍后重试 CH）。
```

## 4. 行为面与 probe 镜像的差异

执行任务书指定的检查：

```bash
git diff --stat 71572199..HEAD -- \
  OpencodeAgent/AGENTS.md OpencodeAgent/skills \
  OpencodeAgent/config OpencodeAgent/config/command
```

输出：

```text
 OpencodeAgent/AGENTS.md              | 2 +-
 OpencodeAgent/skills/intake/SKILL.md | 1 -
 2 files changed, 1 insertion(+), 2 deletions(-)
```

**结果非空**，因此 probe 证据不能逐字节地平移到本 build。变更明细：

| 文件 | 变更 | 来源 commit | 对行为面的影响 |
|---|---|---|---|
| `OpencodeAgent/AGENTS.md` | 第 4 行 VT MCP 工具规模 `89 / 94` → `94 / 99`（并改写策略调度工具说明） | `bb27b8d4`（2026-10-28 22:24） | 文档行，但它在系统提示里；会改变模型看到的工具数量与调度工具描述 |
| `OpencodeAgent/skills/intake/SKILL.md` | 私有槽位默认值表删除 `资金性质` 一行 | `2b615dae`（2026-10-28 19:40；与 `71572199` 同父同主题的兄弟提交） | 明确改变了待澄清型私有槽位的集合与可提问项，可能改变 clarification 的问题数和默认值 |
| `OpencodeAgent/config/**`、`config/command/**` | 无变更 | — | 命令文件与渲染配置本身未变 |

更广的镜像差异（不在上面的四路径检查里，但会影响“probe 证据是否描述本 build”）：

- HEAD 还包含 strategy control-plane 合并（`d90d5308` 2026-10-28 22:14、`bb27b8d4` 22:24），涉及 `OpencodeAgent/Dockerfile`、`entrypoint.sh`、`scripts/check_startup_readiness.sh`、`agent/mcp_server.py`、`agent/src/agent_schedules/*`、`state_export/*`、`strategy_runs/*`。
- 证据：本次构建 vendor **1349** 个 Python 文件，probe 镜像的构建日志是 **1329**；本次容器 entrypoint 记录 `VT MCP server OK — 99 tools registered`，并启动并监督 `strategy-run entry`（:8000）；probe 镜像构建于 2026-10-28 18:29（`71572199` tree），早于 19:40 的 `2b615dae` 和 22:14/22:24 的 strategy 合并。
- 因此，**所有由模型观察到的 probe 指标**（四层一致率、closing block、两轮问答的问题集合、token/延迟）都是在 probe 镜像上测的，不是本 build 的复测；intake 行删除、工具数 89→94（带记忆 94→99）和 MCP 工具菜单变化都可能改变这些指标。
- 可以平移的只有**结构性/静态部分**：命令文件与 frontmatter、config 渲染断言（本次 Stage 1 在新镜像上重跑通过），以及运行时命令发现和 `/checkup`（本次 Stage 2 在新镜像上重跑通过）。路由分类器和 closing-block 场景文本在四路径 diff 中未变，但没有在本 build 上做端到端复测。

结论：不要把 `wave1_frozen10_report.md` 的数字写成 `v3.11.2-onboarding-20261028-4aab6cf4` 的测量结果；它描述的是 `v3.11.1-onboarding-20261028-71572199-fix1`。

## 5. 行为证据（11 条有界探针，probe 镜像）

> 以下数字来自 `results/wave1_frozen10_report.md`（probe 镜像 `opencode-serve:v3.11.1-onboarding-20261028-71572199-fix1`）。`analyze_probe.py --check` 于 2026-10-29 在 `07cee224` 上重跑通过（`--check OK`），说明报告与已提交 JSONL 无漂移；但这不等于本 build 的复测。

### 5.1 四层一致率（冻结十，agreement = observed == expected）

| layer | baseline agreement (n=10) | probe agreement (n=10) | delta | discordant | exact McNemar p |
|---|---|---|---|---|---|
| `route_class` | 6/10 | 7/10 | +10 pp | 3 | 1.0000 |
| `intake_loaded` | 5/10 | 8/10 | +30 pp | 5 | 0.3750 |
| `question_called` | 8/10 | 8/10 | +0 pp | 0 | 1.0000 |
| `params_compiled` | 6/10 | 8/10 | +20 pp | 2 | 0.5000 |

- 四层 paired 点估计全部不低于基线；probe 的门裁决：`todo6_behaviour` pass、`todo9_no_regression` pass。
- 口径提醒：门指标是 agreement，不是 observed-true。observed-true 下 `params_compiled` 基线 9/10、probe 7/10 看起来下降，但 `params_compiled` 对 clarify/direct-answer 期望为 FALSE，observed-true 会奖励过度执行；基线的 9/10 是它的缺陷，不是质量。
- 11 条描述性一致率：`route_class` 7/11、`intake_loaded` 9/11、`question_called` 8/11、`params_compiled` 8/11。
- 探针规模：44 条记录 / 11 条 item（每条四层齐全），11/11 文本；两条 item（`c02`、`c03`）用 120s 会话轮询恢复。n=10 的 McNemar 功效低，p 值仅供审计，不作显著性声明。

### 5.2 Closing block

- 契约分母（`c01,c02,c03,c04,c07,c27`）：**4/6 = 66.7%**（Wilson 95% [30.0%, 90.3%]）。
- 有产物分母（`c01,c03,c04,c07,c27`）：**4/5 = 80.0%**；阈值 90.0% → **fail**。
- `c02`：600s cap 触发，恢复的会话在 tool loop 中被截断，**没有产物**；按契约计为 miss。
- `c27`：有产物，但把 `数据截止 T-1` 改写成具体日期（`数据截止 2026-10-28`），是**契约合规 miss**，不是路由 miss。
- 已接受的限制：closing block 是“契约 + 阈值”，不是硬追加；硬追加插件仍是 F-wave 跟进项（`F-wave: closing-block hard-append plugin`）。因此 80% 是实测 gap，不隐藏为通过。

### 5.3 两轮澄清探针（IS-1 / IS-3）

- 会话 `ses_e7d0938b5ffezQWuSkvB9tOqkA`；turn 1 提出 **3 个问题 × 3 个选项**，每题有 escape hatch（“以上都不是 / 未知”），并给出“默认 = 1A + 2B + 3C”的直接执行出口。
- turn 2（`1A 2B 3C`）退出 0，耗时 **179s**；产物 `/workspace/analysis/portfolio_2026-10-28/report.md`，**12,432 bytes / 171 行**；closing block 四行齐全（`complete=true`）；**用户轮次 = 2**。
- `question` 工具调用 **0**：headless `opencode run` 给每个 session 注入 `question: deny`，`OPENCODE_ENABLE_QUESTION_TOOL=1` 不生效；操作者已裁定**文本反问同样合格**（IS-3 判据 `route_class == clarify_private OR question_called`，`question_called` 仅作诊断）。

## 6. Token / 延迟预算（probe §5）

| metric | baseline mean (n=10) | probe mean (n=10) | change | threshold |
|---|---|---|---|---|
| tokens | 60,036.0 | 58,561.8 | **−2.5%** | ≤ +10% |
| wall_s | 221.372 | 311.165 | **+40.6%** | ≤ +10% |

预算裁决：**`fail`**（token 子门通过，延迟子门失败）。

这是 probe 镜像上的数字，不是本 build 的复测；F3 的“token/延迟均值涨幅 ≤10%”预算门在本 build 上**没有被证明**。已有证据的方向是：token 略降，延迟显著上升（QEMU + 执行路径的既有 `[~]` 限制）。不要把它写成“预算通过”。

## 7. Gap：40 条语料门未执行

F3 的核心是 40 条语料 harness；本环境**没有**完成它。三次尝试如下，全部有证据文件，没有编造 40 条通过结果：

| # | 镜像 | 规模 | 结果 | 失败类别 | 证据 |
|---|---|---|---|---|---|
| 1 | `v3.11.0-onboarding-20261028-6e635820` | 40 条 / 61 会话 | 39/40 complete，1 个 `run_error`（`c17` 回测工具挂起）；裁决 fail | 判定器缺陷（拒答顺序、`**1）` 选项形状、产物含编号下一步）+ 1 处真实回归（开放型直接开问）+ closing 9/13 = 69.2% | `wave1_corpus40_attempt1.jsonl`（157 记录 / 40 run ids，sha `3f360f1f…`）、`wave1_texts/`（39 个非空文本）、`wave1_report.md` §1 |
| 2 | `v3.11.1-onboarding-20261028-71572199-fix1` | 25 条受影响项（`c01-c10` + 全部 `execute_with_defaults`） | 5/25 complete（`c05,c08,c10,c19,c22`）；20 个 `run_error` 全为 `timeout after 300.0s`；closing 0/20；budget inconclusive | harness 超时（300s/条上限在 QEMU + 数据路径下不可用） | `wave1_corpus40_attempt2_harness_timeout.jsonl`（40 记录 / 25 ids）、`wave1_fix1_a_attempt2_harness_timeout.jsonl`（22 记录）、`wave1_fix1_b_attempt2_harness_timeout.jsonl`（18 记录）、`wave1_texts_attempt2_harness_timeout/`（50 文件中 5 个非空）、`wave1_corpus40_fix1.jsonl`、`wave1_report.md` §2 |
| 3 | `v3.11.1-onboarding-20261028-71572199-fix1` | 25 条（900s/条） | `c01` 完成（445.6s）后驱动卡死；计划记录**83 分钟**无进展，取消。JSONL 时间跨度 12:23:57Z–14:01:49Z；24 个 `run_error`（2 个 900s 超时 `c02,c14`，22 个 lane 拆除期间的即时 `Error response from daemon` / `exit=137`） | driver / lane stall，取消 | `wave1_attempt3_a.jsonl`（16 记录 / 13 ids）、`wave1_attempt3_b.jsonl`（12 记录 / 12 ids）、`wave1_texts_attempt3/`（50 文件中 2 个非空） |

替代证据只有 **11 条有界探针**（600s/条 + 会话轮询恢复）：它是目前唯一完整的行为测量，但 paired n=10、McNemar 功效低，且报告自身记录了三处路由缺陷（`c06` 执行而非澄清、`c09` 执行预测而非回答、`c10/c27` 被拒答优先分类器误路由）。它**不能替代** 40 条门。

按本次 F3 的范围裁决，**没有再次启动 40 条运行**；上表是既有证据的汇总，不是新跑的结果。因此本 build 的 40 条结果状态是：**未执行 / unknown**。

## 8. 清理收据

- Smoke 容器由脚本的 `EXIT` trap 删除；`docker ps -a` 无任何行，`docker ps -q | wc -l` = **0**。
- smoke 的 `${TMPDIR}/smoke-v311-checkup-*.json` 已随 trap 删除，无残留。
- `$TMPDIR/wave1`（probe 遗留的 lane 目录，含原始会话分片）已删除；无 `$TMPDIR/wave1probe*` 目录残留。
- 等价性重建的临时 tag `opencode-serve:f3-head-equivalence-07cee224` 已 `docker rmi` 删除；未打 `:latest`，未 push。当前只保留交付 tag `opencode-serve:v3.11.2-onboarding-20261028-4aab6cf4`。
- 保留的审计日志（文件，不是 lane 目录）：构建日志见 §1.1；首次失败的 smoke 日志 `…/f3-smoke-20261029-010010.log`；通过的 smoke 日志 `…/f3-smoke-rerun-20261029-011658.log`。

## 9. 复现命令

```bash
# 构建（DOCKER_BUILDKIT=0 是强制的：base 只在本地，BuildKit 会尝试远端拉取而失败）
cd OpencodeAgent
DOCKER_BUILDKIT=0 ./build.sh --tag v3.11.2-onboarding-20261028-$(git rev-parse --short HEAD)

# smoke（Stage 1 + 运行时命令发现 + 一次 /checkup LLM 会话）
bash tests/smoke-v311.sh opencode-serve:v3.11.2-onboarding-20261028-4aab6cf4

# probe 报告的静态一致性（不重跑探针）
conda run -n legonanobot python tests/evals/intake/analyze_probe.py --check
```

40 条语料 gate 的复跑不包含在本报告内；在本机（arm64 + QEMU）上需要更长/更稳的 per-item 预算和可恢复的 driver，否则会重复 §7 的三种失败形状。

## 10. 裁决依据（不复述 VERDICT 行）

- **阻断**：40 条语料门未在本 build 上执行（§7）。F3 的核心验收是这条门，11 条探针不能替代。
- **通过**：从构建时 HEAD 的全量构建、smoke Stage 1/2、四条 `/` 命令运行时发现与 `/checkup` 回复（§1–§3）。
- **支持性但未达标**：11 条探针四层一致率全部 ≥ 基线、token −2.5%；但延迟 +40.6%、closing block 80% 未达 90%，且探针不是本 build 的复测（§4–§6）。
- **不隐藏的偏差**：HEAD 在 F3 期间前移两个 docs-only commit；等价性重建显示源码 tree 逐字节一致，但 npm 插件层不可复现，`07cee224` 变体本身未再跑 smoke（§1.2）。
