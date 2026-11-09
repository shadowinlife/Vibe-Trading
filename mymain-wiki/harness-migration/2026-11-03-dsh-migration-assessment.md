---
title: opencode+omo → DeepSeek Harness 迁移可行性评估
description: 在「生产模型短期固定为 DeepSeek 模型组」的约束下，评估把 mymain 的 opencode + oh-my-openagent + VT MCP 生产 harness 迁移到 DeepSeek Harness（dsh）的可行性、好处、代价与必须先验证的门槛。含当前依赖面、dsh 能力面、逐项对照、工作量粗估与分阶段建议。
type: research
status: active
created: 2026-11-03
updated: 2026-11-03
tags: [harness, migration, deepseek-harness, dsh, opencode, omo, mcp, model-provider, readiness]
related: [../features/f7-opencode-agent.md, ../features/f7-boot-cost.md, ../branch/MYMAIN_DIVERGENCE.md, ../harness-evolution/README.md]
---

# opencode+omo → DeepSeek Harness 迁移可行性评估

> **一句话结论**：**技术上可行，但不是「迁移」，而是「换 harness 重建一层」。** 真正可平移的只有 VT MCP 工具面、服务端记忆和 skills/AGENTS.md 的文本内容；opencode 的配置装配、权限裁剪、服务/认证/就绪契约、会话与命令面都要重写。在当前「模型经 Alibaba OpenAI-compatible 代理提供」的现实下，dsh 的 DeepSeek 原生优势只能兑现一部分；加上 dsh 仍是零稳定版、明确声明未审计/非 production-ready 的 developer preview，**短期不建议直接切换生产**，建议先做 2 周定向 spike，过三道硬门槛再决定。

- 评估日期：2026-11-03（Asia/Shanghai）
- 当前生产 harness：opencode 1.19.3 + `oh-my-openagent@5.1.35` + VT MCP（`VT_MEMORY_MCP_TOOLS=1` 时 101 工具），镜像线 `v3.17.0-upstream-align-20261102`（`mymain-wiki/branch/MYMAIN_DIVERGENCE.md:207-215`）
- dsh 本地盘点对象：`/Users/mgong/LegoNanoBot/deepseek-harness`，HEAD `ce7073d512`，根版本 `0.2.2-alpha.2`，MIT（`package.json:3-4`、`LICENSE:1`）
- dsh 公共最新：npm `0.2.1-rc.5`（2026-10-29 发布）+ `0.2.2-alpha.3`（2026-11-03 发布）；**41 个 npm 版本全部为预发布，36 个 GitHub release 中 0 个 stable**（<https://registry.npmjs.org/@deepseek-ai/dsh>、<https://github.com/deepseek-ai/deepseek-harness/releases>，访问 2026-11-03）

---

## 0. 结论先行

### 0.1 能不能迁

**能，但迁移面是「harness 产品层」，不是「工具层」。**

- VT 的 101 个工具、5 个记忆工具、MemoryGuard、ClickHouse 取数、策略库/调度都在 `agent/mcp_server.py` 服务端，是标准 MCP，harness 无关（`agent/mcp_server.py:9-31`；`mymain-wiki/branch/MYMAIN_DIVERGENCE.md:28-36`）。
- 13 个仓库 skills 是 `SKILL.md` + frontmatter，dsh 也吃同类格式，文本资产大体可复用（`OpencodeAgent/Dockerfile:119-122`；`deepseek-harness/packages/skill/skill-filesystem/README.md:36`）。
- 385 行 `OpencodeAgent/AGENTS.md` 是普通 Markdown，dsh 原生支持项目级 AGENTS.md（`deepseek-harness/packages/context/agent-instructions/README.md:34-38`）。
- 但 opencode 的 `opencode.json` 装配、`permission` 语义、`opencode serve` 的 REST/SSE 端点、Basic auth、`/config`+`/mcp` 就绪探针、会话库、4 个文件式 slash command、OMO 的 14 agents/10 categories 模型档位，dsh 要么没有等价物，要么形状不同（详见 §3.5、§4）。

### 0.2 值不值得迁（在模型固定为 DeepSeek 的前提下）

**收益是「有条件」的，成本是「确定」的。**

- 条件：只有把生产从 Alibaba OpenAI-compatible 代理切到 DeepSeek 官方 API，dsh 的原生适配器与 `dsh_*` wire extensions 才能完整兑现（`deepseek-harness/docs/deepseek-llm-api-wire-extensions.md:5,41-113`；`packages/llm/llm-deepseek/README.md:55-85`）。如果继续走 Alibaba 代理，dsh 只能以 `llm-pi-ai` 的通用 OpenAI-compatible 模式运行，DeepSeek 原生扩展全部拿不到。
- 成本：dsh 零稳定版、约每周 3 个预发布、README 明确 `THERE WILL BE COMPATIBILITY-BREAKING CHANGES`；`SAFETY.md` 明确 `has not undergone a security audit and must not be treated as secure or production-ready`；无 health/readiness、无官方 Docker/K8s、`--host` 拒绝 `0.0.0.0`、Web 认证是单 owner 的 token+cookie（§3.4）。
- 因此：**短期建议「并行 spike + 单工作流试点」，不建议 big-bang 切换。** 判定门槛见 §8。

### 0.3 最容易被误判的三件事

1. **「换 dsh 就自动更懂 DeepSeek」不成立**（在当前代理路径下）。dsh 的 DeepSeek 原生 reasoning/`dsh_*` 扩展只接官方适配器；Alibaba 代理走 `llm-pi-ai`，只保留通用 `reasoning_effort` / `thinking` 方言（§3.3）。
2. **「OMO 编排很重，迁走会损失很多」被高估**。当前配置里 14 agents + 10 categories 只有模型/努力度覆盖，没有 `mode`/prompt/tools；测试只认 `{build, plan}` 为 primary，归档 transcript 里没有 `task` 调用证据（`OpencodeAgent/config/oh-my-openagent.json:3-173`；`OpencodeAgent/tests/test_config_render.py:327-333`；`OpencodeAgent/tests/evals/intake/baseline_report.md:18`）。OMO 真正承重的是「插件加载 + 模型档位 + 命令/技能目录」。
3. **「dsh 内置工具少，所以 101 个 MCP 工具没问题」不成立**。dsh 的 MCP 工具是直接注册为原生工具、每次请求带全量 schema（`deepseek-harness/packages/mcp/mcp-client/README.md:28,163-171`），没有开箱 tool-search；历史证据是 25-30 个可见工具后选择准确率退化、约 100 个崩塌（`mymain-wiki/harness-evolution/HARNESS_EVOLUTION_SUMMARY.md:14-18`；`HARNESS_EVOLUTION_RESEARCH.md` §4.2）。**换 dsh 不会自动解决披露税；当前 opencode 同样是全量暴露，迁移后仍需单独治理。**

---

## 1. 评估边界与方法

**读过的当前仓库证据**：root `AGENTS.md` §8-10、`mymain-wiki/AGENTS.md`、`MYMAIN_DIVERGENCE.md` §2/§3/§5、F7/F7-boot-cost/F13 卡片、`OpencodeAgent/{AGENTS.md,config/*,skills/*,Dockerfile*,entrypoint.sh,docs/*,tests/*}`、`agent/mcp_server.py` 与记忆适配层、`harness-evolution/` 裁决档案。

**读过的 dsh 证据**：`docs/architecture.md`、`docs/agent-lifecycle.md`、`docs/tool-catalog.md`、`docs/subsystems/{mcp,skills,subagent,web-server,approval,permission-presets,llm-streaming,product-telemetry}.md`、`docs/user/guide/{providers,public-deployments,python-sdk,plugin-market,mcp-memory}.md`、`packages/*/README.md` 与关键源码、`packages/bundle/base/cordis.patch.yml`、`packages/bundle/web-app/presets/*.patch.yml`、`apps/cli/reference/README.md`、`SAFETY.md`、`AGENTS.md`、`CONTRIBUTING.md`。

**外部公共来源**：npm registry、GitHub Releases/Tags/API、官方文档站、PyPI。公共核查基于 commit `8bd073392eef6f54e921a093b95076248eb08cbb`；本地 dsh 盘点基于 `ce7073d512`，两者行号可能漂移，凡引用本地 `path:line` 的以本地 checkout 为准。

**方法限制**：未运行 dsh（无 `node_modules`、未 install/build/test）、未连真实 VT MCP 到 dsh、未验证 Alibaba 网关对新 wire body 的接受度、未做性能/并发基准。凡属推断的地方标 **inference**；无法证明的标 **unknown**。

---

## 2. 当前 harness 的真实依赖面

### 2.1 生产形态

当前是两条部署形态并存：

- 容器/ACS sandbox：`opencode serve --port 4096 --hostname 0.0.0.0` + Basic auth `opencode:${OPENCODE_SERVER_PASSWORD}`，entrypoint 预热 `/config?directory=/workspace`、轮询 `/mcp` 直到 `vibe-trading=connected`，再启动 `strategy_run_server.py:8000`（`OpencodeAgent/entrypoint.sh:129-199,432,443-547`；`OpencodeAgent/Dockerfile:200-202`）。
- 宿主直部署：`opencode web` + nginx :4096 串码网关（`OpencodeAgent/docs/DEPLOY-GUIDE.md:3-4,70-109`）。

两者共享同一套配置/harness 逻辑，差异主要在 CLI 子命令、端口和 skills 路径。本次评估以容器 `serve` 形态为主。

### 2.2 P0/P1/P2 依赖表（摘要）

| 依赖 | 位置 | 为什么重要 | 级别 | harness 无关？ |
|---|---|---|---|---|
| `opencode` 二进制/CLI（`serve`/`run --attach`/`--format json`/`--command`/`--agent`/`--model`） | `Dockerfile.base:32`；`entrypoint.sh:432`；`workspace/cron_jobs/trigger.sh:45-55` | 运行时、cron、评测驱动 | P0 | 否 |
| `opencode.json` schema（`model`/`provider`/`mcp`/`agent.permission`/`plugin`/`tool_output`） | `config/opencode.json.tmpl:1-82`；`render_config.py:174-178` | 配置装配、权限、工具面 | P0 | 否 |
| Provider `alibaba-cn` + `enable_thinking` 注入 | `opencode.json.tmpl:3-29`；`render_config.py:141-144`；`test_config_render.py:235-236` | 模型身份与 thinking 行为；provider ID 不能改 | P0 | 否 |
| 代理接线（`LLM_BASE_URL`/`LLM_API_KEY`/`x-sandbox-id`） | `opencode.json.tmpl:22-29`；`entrypoint.sh:124-127,201-224` | 生产密钥不进沙箱、RM 归属/配额 | P0 | 部分（OpenAI-compatible baseURL） |
| OMO 插件 pin `oh-my-openagent@5.1.35` | `opencode.json.tmpl:80-82`；`Dockerfile:52-59` | OMO 模型档位/命令/技能/MCP 目录；5.1.40 在 opencode 1.19.3 上首挂 | P0（若保留 OMO） | 否 |
| 13 个仓库 skills + 全局目录 | `Dockerfile:119-122`；`F13:41-46`；`OpencodeAgent/README.md:19,78` | 领域 playbook；项目副本会反向遮蔽全局 | P0 | 否（格式相近） |
| 385 行 AGENTS.md + cwd 发现 | `Dockerfile:117,143-149`；`entrypoint.sh:420-429` | 系统提示自动加载；cwd 错则全丢 | P0 | 部分（AGENTS.md 约定） |
| 101 个 VT MCP 工具（stdio） | `opencode.json.tmpl:36-57`；`mcp_server.py:9-31` | 全部金融工具面 | P0 | **是**（标准 MCP） |
| 5 个 memory 工具 + MemoryGuard | `mcp_server.py:869-1017` | 自动记忆/反思；生产默认开启 | P0 | **是**（服务端） |
| 长回测超时 600s + 20s progress heartbeat | `opencode.json.tmpl:43`；`mcp_server.py:1026-1077`；`IMAGE-MANUAL.md:306-308` | 分钟级回测能否完成 | P0 | 部分（需 progress 支持） |
| 工具治理 deny / per-agent scoping | `vibe-trading-tools.json:1-7`；`render_config.py:74-87`；`opencode.json.tmpl:58-79` | 成本与攻击面；不实现则全暴露 | P0 | 部分（意图可移植） |
| `opencode serve` 端点（`/config`/`/mcp`/`/health`/`/command`、`POST /session/{id}/message`、abort） | `entrypoint.sh:444-495`；`tests/evals/intake/run.py:147-197` | 就绪、命令发现、会话恢复、前端/控制面 | P0 | 否 |
| Basic auth | `entrypoint.sh:444,463`；`Dockerfile:200-202` | 沙箱访问安全 | P0 | 部分（HTTP Basic 通用） |
| `strategy_run_server.py:8000` | `entrypoint.sh:498-547`；`agent/strategy_run_server.py:1-10,142-180` | RM 策略运行/状态入口 | P0 | **是**（独立 HTTP） |
| StartupProbe `/config?directory=/workspace` + 300s 预算 | root `AGENTS.md:99-116`；`entrypoint.sh:443-495` | K8s 就绪；bind 早于初始化 | P0 | 部分（探针需重做） |
| OMO 14 agents + 10 categories 模型/努力度 | `oh-my-openagent.json:3-173` | 分层 reasoning effort | P1 | 否 |
| OMO 4 个自带 MCP（websearch/context7/grep_app/lsp） | `F13:24` | 额外检索能力；不在 `opencode.json` 声明 | P1 | 否 |
| 4 个文件式 slash command（`$ARGUMENTS`） | `config/command/*.md:1-11`；`Dockerfile:115` | 四条产品入口 | P1 | 否 |
| `tool_output` 8,000 行 / 256KB | `opencode.json.tmpl:32-35` | 工具结果截断行为 | P1 | 否 |
| Session storage / `opencode.db` / `.omo` | `Dockerfile.base:68`；`Dockerfile:145`；`AGENTS.md:76` | 会话恢复/审计/继续 | P1 | 否 |
| `@opencode-ai/plugin` SDK 预烘 | `Dockerfile:61-94,151-170` | 冷启动零下载 | P1 | 否 |
| `models.json` 235 providers / 5.96MB | `Dockerfile:96-102`；`MYMAIN_DIVERGENCE.md:214` | 离线模型目录；实际只用 1 个模型 | P2 | 部分 |
| `tui.json` / `config/package.json` / `question` 权限 | `Dockerfile:111-112,159-168`；`question_tool_probe.md:39-56` | 容器内不消费/未证明 | P2 | 否 |
| 12 领域子代理 / nano-search / F8 | `MYMAIN_DIVERGENCE.md:443-445,504-509` | 已回退/移出，不在迁移面 | — | — |

完整证据见 §10.1。核心判断：**P0 集中在 opencode 的运行时/配置/服务契约与 VT MCP 的调用契约；真正 harness 无关的只有 MCP 服务端、ClickHouse 环境、strategy-run 服务。**

### 2.3 OMO 的真实承重面比名义小

- 配置声明：14 agents + 10 categories，全部 `alibaba-cn/deepseek-v4.1-flash-1017`；11 agents + 5 categories 用 `reasoningEffort: max`，3 + 5 用 `high`；每条都有 `thinking.type: enabled`（`oh-my-openagent.json:3-173`）。
- 但**没有 `mode`/prompt/tools/temperature/hand`**；测试明确断言没有 `mode: primary`，因此模板中的 `question` allow 只覆盖 `{build, plan}`（`test_config_render.py:327-333`；`opencode.json.tmpl:58-68`）。
- 运行时证据只有一处：intake 基线报告记录 agent 为 `Sisyphus - ultraworker`（`tests/evals/intake/baseline_report.md:18`）。这证明某个 OMO agent 名称在评测路径可用，但**不能证明** sisyphus/hephaestus/prometheus/metis/momus/atlas 等在生产请求路径被默认选中。
- 生产 AGENTS.md 里确实写了 Prometheus/explore/librarian/build/oracle 的并行编排要求（`OpencodeAgent/AGENTS.md:265-295`），但归档 transcript 的工具调用集中在 `skill`/`vibe-trading_*`/`bash`/`memory_recall`，没有 `task` 调用（原盘点对 `harness-evolution/evals` transcript 的检索）。
- 结论：**OMO 当前更像「插件包 + 模型档位 + 命令/技能目录」，不是已被证明的生产编排依赖。** 这降低了「丢掉 OMO 编排」的感知成本，但也意味着迁移后不能指望 dsh 的 subagent 自动补位——需要重新设计并由评测证明。

### 2.4 两个模型平面必须分开看

| 平面 | 当前实现 | 换 dsh 后 |
|---|---|---|
| **Harness 模型** | opencode 调 `alibaba-cn/deepseek-v4.1-flash-1017`；provider 块声明 `enable_thinking: true`、`reasoning_effort: high`、1M/500k/131k；代理模式注入 `baseURL`/`apiKey`/`x-sandbox-id`（`opencode.json.tmpl:3-29`） | 需要按 dsh 的 `llm-pi-ai` 或 `llm-deepseek` 重接；`enable_thinking` 顶层字段是硬缺口（§3.3） |
| **VT 内部 LLM** | `LANGCHAIN_PROVIDER=dashscope` + `LANGCHAIN_MODEL_NAME=qwen3.8-max` + `DASHSCOPE_BASE_URL`；swarm worker / vision / OCR 用（`Dockerfile:172-175`；`IMAGE-MANUAL.md:226-228`；`agent/SKILL.md:63`） | **完全不变**；换 harness 不会统一模型栈 |

所以「生产模型固定 DeepSeek」只约束了第一个平面。若目标是「全栈 DeepSeek」，还要单独迁 VT 内部的 ChatLLM；那是另一条工作流，不在本评估的 harness 迁移收益里。

---

## 3. dsh 的能力面（与迁移相关的部分）

### 3.1 架构、组合与入口

- **全插件 Cordis**：模型适配器、工具注册表、session log、agent loop 本身都是插件；注册是 effect，卸载可回滚（`docs/architecture.md:11-19`）。服务通过 `ctx.<key>` 查找，依赖用 `inject` 声明，加载顺序由服务可用性驱动，不由行顺序决定（`docs/cordis-primer.md:7-15`；`packages/bundle/base/cordis.patch.yml:11-13`）。
- **profile / bundle / preset**：`web`/`headless`/`sdk`/`acp` 是模板；template 映射 `web=[dsh-base,dsh-web-app]`、`headless=[dsh-base,dsh-headless]`（`packages/util/official-bundles/src/bundles.ts:10-26`）。Web 把工具/提示/委派行从 host 平面移到每个 session 选中的 preset 平面（`packages/bundle/web-app/cordis.patch.yml:467-533`）。
- **组合是 patch 层叠**：bundle 的 `cordis.patch.yml` → profile 的 `cordis.patch.yml` → `$DSH_HOME/cordis.patch.yml` → 每个 `--patch`；后层按 row id 命中，**整块替换 `config`，不是深合并**（`docs/architecture.md:25`；`apps/cli/reference/README.md:11`）。迁移时最容易踩的坑：以为写一个 key 就能覆盖，实际要重述整块。
- **入口**：`dsh <profile>` / `dsh web` / `dsh --profile headless "task"` / `dsh --profile sdk` / `dsh --profile acp`；没有默认 profile，不带 profile 会报 `--profile <name> is required`（`apps/cli/README.md:11-18`；`apps/cli/src/args.ts:175-178`）。
- **启动时序**：`runProfile()` 先 `boot()` 挂载树，`boot()` 返回后才 `appReady.commit()`（`apps/cli/src/profile-boot.ts:323-345`）；但 Web 端口在 webserver 插件激活时就立即 listen（`packages/host/webserver/README.md:28`；`docs/subsystems/web-server.md:106,205`）。**这与 opencode 的「先 bind 后初始化」同形**，不能把端口可连当 readiness。

### 3.2 MCP / skills / subagents

**MCP**（`packages/mcp/mcp-client`）

- 只支持 **stdio** 与 **Streamable HTTP**；没有 SSE、没有 WebSocket transport（`packages/mcp/mcp-client/README.md:28,55-60`；`src/transport.ts:29-46`）。
- 每 server 一行 Cordis 配置；工具命名 `mcp__<serverName>__<rawName>`，例如 `mcp__vibe-trading__backtest`（`README.md:71-81`）。`serverName` 限 `[A-Za-z0-9_-]{1,32}`。
- 工具**直接注册为原生工具**，模型每次请求带 description/schema，没有 mediator 二次转发（`README.md:28,163-171`）。
- 默认 `toolCallTimeoutMs=60,000`；`startupTimeoutMs` 未设（manager 侧 30,000）；`closeTimeoutMs=5,000`；重连 500ms 起、上限 30s、10 次后卸载工具；`maxInstructionBytes=32,768`（`README.md:61-67,85,93`）。
- **没有 OAuth flow**；stdio 用 `env`，HTTP 用 `headers`；不支持 prompt templates、elicitation、task-based execution、resource subscriptions（`README.md:55-60,208-213`；`packages/mcp/mcp-manager/README.md`）。
- stdio 子进程会 scrub 匹配 `/KEY|PASSWORD|SECRET|TOKEN/i` 的 ambient 变量和 `DSH_*`，显式 `env` 可覆盖（`README.md:137`）。这对 VT 的 `CLICKHOUSE_*`/`VT_MEMORY*` 注入是利好，但注意 `VT_MEMORY*` 含 `MEMORY` 不含敏感词，需实测。

**Skills**（`packages/skill/*`）

- 磁盘形态：`<name>/SKILL.md` 或顶层 `<name>.md`；**不递归** `**/SKILL.md`；name 必须 kebab-case（`packages/skill/skill-filesystem/README.md:36`）。
- Frontmatter：必需 `name`/`description`；可选 `whenToUse`/`metadata`/`disable-model-invocation`/`user-invocable`；非法值整条丢弃并 warning（`README.md:36-38`）。
- 发现优先级：`<project>/.dsh/skills`(100) → `<project>/.agents/skills`(200) → `customSkillDirs`(300) → `<dshHome>/skills`(400) → `<agentsHome>/skills`(500)；project root 是最近的 `.git` 祖先（`README.md:50-56`）。
- **渐进披露明确**：目录只暴露 name + capped description，正文按需 `skill` 工具加载；body 每次 load 重读（`packages/skill/tool-skill/README.md:54-56`；`skill-filesystem/README.md:42`）。
- 第三方目录可直接放：`customSkillDirs` 或上述根（`skill-filesystem/README.md:70-73`）。

**Subagents**（`packages/subagent/*`）

- 模式：`spawn`（全新 in-process child，可 continue）、`fork`（父会话已完成历史 seed）、`acp`、`codex`、`claude-code`、`dsh-sdk`（`packages/subagent/README.md:27-33`）。
- `maxDepth` 默认 1，`maxActiveSubagents` 默认 8；`agentPreset` 支持领域专用 child preset，`allowedAgentPresets` 暴露给模型选择（`packages/subagent/subagent/README.md:49-65`；`packages/subagent/tool-subagent/README.md:49-77`）。
- 委派立即返回 child id，完成通知以 user message 注入父 session；本地 child 可 message/resume，外部 backend 一次性（`packages/subagent/subagent/README.md:59`）。
- 后台 jobs 是独立运行时，进程内、不跨重启；`maxConcurrentJobsPerOwner=10`（`packages/jobs/jobs-local/README.md:24-32`）。

### 3.3 模型接入（迁移的第一硬门槛）

dsh 有两个 shipped adapter：

| Adapter | 端点/协议 | 能力 | 迁移含义 |
|---|---|---|---|
| `@deepseek-ai/dsh-llm-deepseek` | 默认 `https://api.deepseek.com/anthropic`，Anthropic Messages 形状，追加 `/v1/messages`、`/v1/files` | `reasoningEffort: off/low/high/max` → `output_config.effort`；`thinking: disabled` 锁 `off`；`maxTokens` 默认 256k；`streamIdleTimeoutMs` 300k；retry normal 5 次；**没有 `headers` 字段** | 只有走官方 DeepSeek API 才有意义；对接 Alibaba 的 OpenAI-compatible 代理不匹配 |
| `@deepseek-ai/dsh-llm-pi-ai` | `api: openai-completions`/`openai-responses`/`anthropic-messages` + `baseURL` | `headers?: Record<string,string>`（可放 `x-sandbox-id`；在 `cordis.patch.yml` 配置，不在 Models UI）；`models[].reasoningEfforts` 映射；`compat.thinkingFormat` 可选 `openai/deepseek/openrouter/together/baseten/zai/qwen/chat-template/qwen-chat-template/string-thinking/ant-ling`；`cacheRetention` | **Alibaba 代理走这条**；`reasoning_effort` 可发，但顶层 `enable_thinking` 不是一等字段 |

关键事实（逐条对应 §8 的 G1）：

- **自定义 baseURL + 自定义 header：可以。** `packages/llm/llm-pi-ai/src/config.ts:102-104`（`api`/`baseURL`）、`:152`（`headers`）；`docs/config-catalog.md:2036-2037` 明确 “Provider request headers, validated against Fetch…”。
- **`reasoning_effort`：可以。** 需 `compat.supportsReasoningEffort` + `models[].reasoningEfforts`；`thinkingFormat: 'deepseek'` 的实测 wire body 是 `{"thinking":{"type":"enabled"},"reasoning_effort":"high"}`（`packages/llm/llm-pi-ai/tests/adapter.spec.ts:683,701`）。
- **顶层 `enable_thinking`：不是已暴露的一等配置。** 只能通过 `chatTemplateKwargs`/`chatTemplateArgs` 以 `chat_template_kwargs`/`chat_template_args` 形式发（`packages/llm/llm-pi-ai/src/catalog.ts:419-428`；`tests/catalog.spec.ts:992,1000`）；这要求 `thinkingFormat` 是 `chat-template`/`qwen-chat-template` 一类，而不是 `deepseek`。`qwen` format 的 wire 映射在 `@earendil-works/pi-ai@1.3.0` 内部，checkout 没有 `node_modules`、没有 body 断言 → **unknown**。
- **`dsh_*` wire extensions 只走官方 adapter。** `dsh_plugin_packages`、`dsh_session_log`、`x-deepseek-harness-user-id/session-id/compact` 都明确 “provider-neutral LLM interface and `llm-pi-ai` do not implement these additions”（`docs/deepseek-llm-api-wire-extensions.md:5,24-30,41-113`）。**这直接决定了「换 dsh」的收益上限**：走 Alibaba 代理时，这些 DeepSeek 原生能力全部拿不到。
- **数据治理反直觉点**：`dsh_session_log` 默认开启，会把 session 内容（cwd、system prompt、user/assistant、tool args/results、compaction 摘要等）随请求发到解析后的 `baseURL`（含 gateway），默认 8 MiB/请求（`docs/deepseek-llm-api-wire-extensions.md:74-113`）。pi-ai 不实现它，所以当前 Alibaba 代理路径下不会触发；**一旦切官方 API，必须显式关闭/审计**。

### 3.4 服务、认证、就绪、部署

- Web 默认 `127.0.0.1:3080`；`--host` 只接受具体 IPv4/IPv6 字面量，**`0.0.0.0`/`::` 在加载时拒绝**；TLS 用 `--tls-cert`/`--tls-key`，证书只读一次（`apps/cli/reference/README.md:109,113`）。
- 认证是**进程级 launch token + 浏览器 signed cookie**：启动 URL 带一次性 token，`/ui/` 换 cookie；Host/Origin fence 在认证前；缺失/过期/错误 authority 401；cookie 默认 30 天、host-only、HttpOnly、SameSite=Strict（`packages/client/connection/README.md:41-51`）。**没有账号/RBAC/多租户；也没有 logout**（删 credential record + 重启才能撤销全部，`README.md:83`）。
- API 面是**浏览器 RPC**：HTTP `POST /api/<namespace>/<method>` + WebSocket `/api/remote.mux`；SSE 只用于 client HMR / route handler 内部，不是公开 chat API；没有 OpenAPI（`docs/api-gateway.md:126-132`；`packages/api/gateway/README.md:41`；`packages/client/hmr/src/index.ts:168`）。
- Headless 是**一次性任务**：`dsh --profile headless "task"` 或 `--json -`，stdout 只出最终文本/NDJSON，**不监听端口**，没有 live approval channel，退出码 0 仅当 `turn/end` reason=`completed`（`packages/bundle/headless/README.md`；`apps/cli/reference/README.md:44`）。
- SDK/ACP 都是 **stdio JSON-RPC**（NDJSON），不是 HTTP 服务（`packages/sdk/README.md:12`；`packages/acp/README.md:12`）。
- **没有 health/readiness 端点**；`WebServer` 文档只说“activation 时立即 listen，listen 失败则 initialization 失败”（`docs/subsystems/web-server.md:106`；全仓 `grep '/health'/'readinessProbe'` 只命中测试 fixture）。**没有任何 Dockerfile/K8s manifest**，只有 sshd 测试 fixture（`find . -iname 'Dockerfile*'`）。
- 运行时：Node `>=24.9.0`、pnpm `11.28.5`；原生依赖 `native/system`（Linux Landlock/POSIX flock）、`node-pty 1.2.0-beta.15`、`@vscode/ripgrep`；SQLite 用 Node 内置 `node:sqlite`（`package.json:7-10`；`native/README.md:5-7`；`packages/session-query/session-query-sqlite/src/schema.ts:50`）。

### 3.5 与 opencode/omo 的「形状差异」清单

| 能力 | opencode/omo 现状 | dsh | 迁移动作 |
|---|---|---|---|
| 配置 | `opencode.json` + `oh-my-openagent.json`，schema 固定 | profile/bundle + patch 层叠，整块替换 config | 重写组合，不能 1:1 翻译 |
| 权限 | 全局/每-agent `permission` deny；可按工具名/前缀 | session 级 sandbox mode + approval policy + 单次 escalation；**无 per-tool glob** | 重新设计治理；VT 工具 deny 要在 MCP 暴露层或自定义插件做 |
| 命令 | `.opencode/command/*.md` + `$ARGUMENTS`；运行时 29 个 | 只有 plugin 注册；**无文件发现** | 4 个仓库命令需写插件；OMO 25 个命令会消失 |
| 技能 | `~/.config/opencode/skills` + project 优先级 4>3>2 | `.dsh/skills`/`.agents/skills`/`~/.dsh/skills`，rank 100-500 | 迁移目录 + frontmatter 改写 |
| 子代理 | OMO prompt 要求 + 12 领域层已回退 | spawn/fork/preset + 控制工具 | 重新设计编排；不要假设 OMO 等价物 |
| 模型 | provider `alibaba-cn` + opencode 硬编码 `enable_thinking` 注入 | `llm-deepseek`（官方）或 `llm-pi-ai`（自定义） | 先验证 G1；可能需要自定义 adapter 插件 |
| 会话 | opencode DB + REST `/session` | JSONL append-only + V4 session format + SDK/ACP | 不能互读；需 cutover |
| 服务 | `opencode serve` REST/SSE + Basic auth | HTTP `/api` RPC + WS mux + stdio SDK/ACP + token/cookie | 控制面重写 |
| 就绪 | `/config`、`/mcp`、`/health` | 无 health；端口先 bind；CLI 启动行等 Loader settle | 自建 probe（stdout 或自定义 `/health` 插件） |
| 部署 | Dockerfile + compose + K8s startupProbe | 无官方 Docker/K8s；`--host` 拒绝 0.0.0.0 | 自建镜像/编排；host 用 Pod IP |
| 记忆 | VT MCP 5 工具 + MemoryGuard | **无 first-party memory**；推荐外部 MCP memory | VT 记忆服务端可原样接；不要期待 dsh 内建 |
| 多租户 | 单 owner | 单 owner | 无变化；都不是多租户 |

---

## 4. 逐项可行性对照（迁移动作与结论）

| 能力 | 现状 | dsh | 可行性 | 迁移动作 |
|---|---|---|---|---|
| **VT MCP 101 工具** | stdio，`type: local`，600s timeout | stdio 支持；工具名变 `mcp__vibe-trading__*`；默认 60s | **高**（协议层） | 改 serverName/工具名映射；验证 600s 超时与 progress；重跑工具路由评测 |
| **记忆 5 工具 + MemoryGuard** | 服务端 FastMCP middleware | 标准 MCP tools；无内建 memory | **高** | 原样接；确认 `VT_MEMORY_MCP_TOOLS=1` 注入；MemoryGuard 不动 |
| **13 个 skills** | `SKILL.md` + `argument-hint`/`triggers`/`user-invocable` | `SKILL.md` + `whenToUse`/`disable-model-invocation`/`user-invocable` | **高**（文本） | frontmatter 改写；目录迁到 `~/.dsh/skills` 或项目 `.dsh/skills`；验证触发 |
| **AGENTS.md 385 行** | cwd-based 自动加载 | 原生支持，64KB 预算 | **高** | 内容平移；清理 `policy/`/`sql/`/`docs/views/` 死引用；验证嵌套语义 |
| **4 个 slash commands** | `.opencode/command/*.md` + `$ARGUMENTS` | 无文件发现 | **中** | 写一个 command 注册插件；或放弃命令面，改由 skills/AGENTS 路由 |
| **权限裁剪** | deny `trading_*`/`qveris_*`/`iwencai_search`；explore/multimodal deny VT | 无 per-tool glob | **中低** | 在 MCP 暴露层裁剪（B 批资产）或自定义 plugin/tool wrapper |
| **模型接线** | `alibaba-cn/deepseek-v4.1-flash-1017`，`enable_thinking`，1M/500k/131k | `llm-pi-ai` 可 baseURL/headers/reasoning_effort；无顶层 `enable_thinking` | **待验证（G1）** | 先实测网关；必要时写 `LlmAdapter` 插件补 `enable_thinking` |
| **长回测 600s** | opencode `resetTimeoutOnProgress` + 20s heartbeat | 默认 60s；progress 支持未验证 | **待验证（G2）** | 改 `toolCallTimeoutMs`；确认 progress 通知；否则服务端拆短任务 |
| **会话/恢复** | opencode DB + REST | JSONL V4 + SDK/ACP | **低（历史不可迁）** | 设计 cutover；历史会话留 opencode；新会话用 dsh |
| **服务/认证/就绪** | `serve` REST + Basic auth + `/config` probe | `/api` RPC + WS + token/cookie；无 health；拒绝 0.0.0.0 | **低（需重写）** | 写 adapter/gateway；自建 readiness；改 RM/前端/cron |
| **部署镜像** | Ubuntu 22.04 + Python 3.12 + Node 20；4.33GB | Node 24.9 + pnpm + native deps；无 Dockerfile | **中** | 新建镜像；验证 ARM64/QEMU/离线；重测冷启动 |
| **可观测** | opencode logs + `/health` + RM 指标 | 无 health；有 session telemetry/OTel | **中低** | 自建 stdout/OTel 采集；重做告警 |
| **数据治理** | 无 session 上传扩展 | 官方 adapter 默认 `dsh_session_log` 8MiB/请求 | **需决策** | 若切官方 API，显式关闭/审计；走 pi-ai 则不触发 |

---

## 5. 好处（按兑现条件排序）

### 5.1 模型族对齐（有条件，收益最大也最不确定）

- dsh 以 DeepSeek 模型族为主要目标：官方 adapter、reasoning effort/thinking 方言、`dsh_*` wire extensions 都围绕 DeepSeek 设计；`thinkingFormat: deepseek` 明确支持 “DeepSeek V4 behind an OpenAI-compatible gateway”（`docs/user/guide/providers.md`；`packages/llm/llm-pi-ai/tests/adapter.spec.ts:683,701`）。
- 若生产切到 DeepSeek 官方 API：可获得 `dsh_session_log`、`dsh_plugin_packages`、`x-deepseek-harness-*` 等原生 wire 能力（`docs/deepseek-llm-api-wire-extensions.md:5,24-113`），以及官方 adapter 的 cache-hit 指标映射（`packages/llm/llm-deepseek/README.md:143,177`）。
- 若继续走 Alibaba 代理：只能拿到通用 `reasoning_effort`/`thinking` 方言；原生扩展全部没有。**此时「厂商对齐」的收益主要是 roadmap/兼容性，不是立刻可测的性能提升。**

### 5.2 架构与可维护性

- Cordis 全插件 + capability seam + typed events + effect 可逆；扩展点有文档、包边界清晰（`docs/architecture.md:11-19,134-138`；`packages/README.md:98`）。
- VT 特有需求（如工具暴露治理、ClickHouse 语义层提示、RM 调度适配）可以写成插件/preset，不必 fork 主循环；对比 opencode 需要改 `opencode.json`/插件 SDK，dsh 的 seam 更明确。
- 升级指南是强制流程：每个 externally perceptible breaking change 必须写 `docs/upgrade-guide/v<version>/...`（`AGENTS.md:11`；`.agents/skills/dsh-create-upgrade-guide/SKILL.md:9-45`）。这对长期维护是加分项，但前提是你能承受高频升级。

### 5.3 内置能力替代一部分 OMO/插件

- subagent（spawn/fork/ACP/Codex/Claude Code/dsh-sdk + `agentPreset`）、plan/todo/goal、compaction、output spill、repeat guard、LSP/terminal/browser/computer-use、schedule（`docs/tool-catalog.md:18-52`；`packages/subagent/README.md:27-33`；`packages/plan/README.md:12`）。
- TS SDK + Python SDK + ACP，都是 stdio JSON-RPC，适合做自动化/CI/控制面（`packages/sdk/README.md:12`；`packages/acp/README.md:12`；`python/sdk/README.md`）。
- 对当前部署的直接价值：**不再需要预烘 63MB 插件树和 `@opencode-ai/plugin` 双目录**，理论上缩短冷启动链路（当前冷启动成本见 `mymain-wiki/features/f7-boot-cost.md:13-24,53-61`）。但 dsh 自身冷启动未测，不能先验地认为更快。

### 5.4 持久化/测试/运维纪律

- Session 是 append-only event log + JSONL fsync + torn-tail 恢复；每 session 单 writer；resume/fork/export 都有（`packages/session/session-persistence-jsonl/README.md:75`；`packages/session/session-persistence/README.md:53,59-61`；`packages/core/session/README.md:82-84`）。
- Snapshot/replay 测试、CI 覆盖率门、升级指南、pre-stable 但显式的兼容政策（`snapshots/AGENTS.md:1-17`；`vitest.snapshot.config.ts:52-56`；`AGENTS.md:7`）。
- MIT，第三方依赖披露完整（`LICENSE:1`；`THIRD_PARTY_NOTICES.md`）。

### 5.5 安全模型（相对当前是加分，但不是银弹）

- sandbox 三模式（read-only/workspace-write/danger-full-access），Linux bwrap/Landlock、macOS Seatbelt、Windows restricted token；无法执行时 fail closed（`packages/sandbox/README.md:12,22-25`；`packages/sandbox/sandbox/README.md:60-66`）。
- fs-sandbox 对 mutation 做 workspace fence；bash denial 可用 `sandbox_permissions` + justification 单次 escalation（`packages/fs/fs-sandbox/README.md:12,30-38`；`packages/shell/tool-bash/README.md:68-70`）。
- MCP stdio 子进程 scrub 敏感 ambient 变量（`packages/mcp/mcp-client/README.md:137`）。
- 但 credential store 对同 OS 用户的 agent 不可隐藏（`packages/credentials/credentials-local/README.md:12`），且 `SAFETY.md` 明确未审计、非 production-ready。

---

## 6. 坏处与风险（按严重度排序）

| # | 风险 | 严重度 | 概率 | 说明与证据 |
|---|---|---|---|---|
| R1 | **零稳定版 + 高频 breaking** | 高 | 高 | 41 个 npm 版本全部预发布；36 个 GitHub release 0 stable；README 明确 `THERE WILL BE COMPATIBILITY-BREAKING CHANGES`；约每周 3 个预发布（npm/GitHub registry，访问 2026-11-03） |
| R2 | **明确非 production-ready / 未安全审计** | 高 | 高 | `SAFETY.md` 原文；无 `SECURITY.md`；sandbox/approval 只降低风险，不保证隔离 |
| R3 | **控制面/部署契约重写** | 高 | 高 | 当前 RM/ACS/前端/cron 依赖 `opencode serve` REST/SSE + Basic auth + `/config` probe；dsh 是 `/api` RPC + WS + token/cookie；无 health；`--host` 拒绝 0.0.0.0；无 Docker/K8s 资产（§3.4） |
| R4 | **101 工具披露税回归** | 高 | 高 | dsh MCP 直接注入全量 schema，无 tool-search；历史证据 25-30 工具后退化、~100 崩塌（`HARNESS_EVOLUTION_SUMMARY.md:14-18`）；迁移后需重做 B 批暴露面治理 |
| R5 | **`enable_thinking` 接线缺口** | 高 | 中高 | dsh 无顶层字段；`thinkingFormat: deepseek` 发 `thinking:{type}`+`reasoning_effort`；Alibaba 网关是否接受未知；`qwen` format 无 body 断言（§3.3） |
| R6 | **长回测超时/进度支持未验证** | 高 | 中 | 当前依赖 600s + 20s progress reset；dsh 默认 60s；progress 通知/重置未验证（`opencode.json.tmpl:43`；`mcp_server.py:1026-1077`；`mcp-client/README.md:61,85`） |
| R7 | **会话/状态不可互迁** | 中高 | 高 | opencode DB vs dsh JSONL V4；历史会话不能读；cron/frontend 的 resume 依赖 `/session`（§3.5） |
| R8 | **命令/技能目录缩水** | 中 | 高 | 运行时 29 commands/16 skills，其中 25/3 来自 OMO/内建；dsh 无文件式 command；需写插件或放弃（`f3_real_qa.md:82,92`；`MYMAIN_DIVERGENCE.md:239`；§3.5） |
| R9 | **权限模型粒度不足** | 中 | 高 | dsh 无 per-tool glob；当前 deny `trading_*`/`qveris_*`/`iwencai_search` 与 explore/multimodal scoping 需在 MCP 层或插件实现（§3.5） |
| R10 | **运维/可观测缺口** | 中高 | 高 | 无 health/readiness、无官方容器资产；Node 24.9 + native deps；ARM64/QEMU/离线未验证；冷启动/资源未测（§3.4） |
| R11 | **证据基线重置** | 中高 | 高 | 所有 eval（tool routing、intake 40 条、closing block、startup canary、F13）都是 opencode 口径；F3 已 REJECT、F4 partial，本身未闭环（`F3:12-16`；`F4:12-25`） |
| R12 | **数据治理** | 中 | 条件触发 | 官方 API 的 `dsh_session_log` 默认 8MiB/请求上传 session 内容；pi-ai 不实现（当前代理路径不触发）；切官方 API 必须显式关闭/审计（`docs/deepseek-llm-api-wire-extensions.md:74-113`） |
| R13 | **两个模型平面不统一** | 中 | 确定 | VT 内部仍是 DashScope `qwen3.8-max`；换 harness 不改变 swarm/vision/OCR（`Dockerfile:172-175`） |
| R14 | **上游不接受外部 PR** | 中 | 高 | `CONTRIBUTING.md:7-20` 明确当前不接受 external PR；遇到缺口只能自己 patch/等上游 |
| R15 | **包名/标签陷阱** | 低中 | 高 | TS SDK npm `latest` 是 `0.0.1-rc.1`，`next` 才是 `0.2.1-rc.5`；Python 官方包是 `deepseek-harness-sdk`，`deepseek-harness` 是第三方（registry，访问 2026-11-03） |

---

## 7. 迁移工作量粗估（inference）

> 以「生产级 cutover、保留现有能力、不重写 VT 服务端」为前提。人周为量级，不是承诺。

| 工作包 | 内容 | 估时 |
|---|---|---|
| **G1 模型验证** | 用 `llm-pi-ai` 接 Alibaba 代理；测 `thinkingFormat`/`reasoning_effort`/`enable_thinking`/1M/500k/131k/图片；必要时写 `LlmAdapter` 插件 | 3-5 人日 |
| **G2 工具面验证** | VT MCP stdio 接入；101 工具 token 成本与路由准确率；600s 超时 + progress；工具名映射 | 3-5 人日 |
| **G3 控制面验证** | headless/SDK 驱动替代 `opencode run --attach`；cron 恢复；readiness 方案；auth 方案 | 5-8 人日 |
| **核心 port** | profile/bundle 组合、skills frontmatter/目录、AGENTS.md、command 插件、权限/MCP 暴露治理 | 8-12 人日 |
| **部署/运维** | Docker 镜像（Node 24.9 + native deps）、K8s probe、认证/反代、日志/OTel/告警 | 8-15 人日 |
| **评测/回归** | tool routing、intake 40 条、closing block、长回测、启动、并发/资源 | 8-12 人日 |
| **试点/切换** | 单工作流试点 4 周 + 回滚预案 + 历史会话 cutover | 10-15 人日 |
| **合计** | — | **约 7-12 人周**（不含上游 breaking 的重工） |

这个量级与「换一个 harness 产品层」相称，不是「改配置」。如果只做 spike 不做 production，3-5 人日即可拿到 G1/G2/G3 的结论。

---

## 8. 必须先验证的三道门槛（spike 定义）

### G1：模型网关兼容性（决定收益是否成立）

**要证明**：dsh 能对 Alibaba 的 `alibaba-cn/deepseek-v4.1-flash-1017` 端点产生与当前等价的请求，并正确解析响应。

- 必需：`reasoning_effort` 的 `high`/`max` 语义；thinking 开启；1M context / 500k input / 131k output 的声明；`x-sandbox-id` header；附件/多模态是否可用。
- 两种路线：
  1. 若网关接受 `thinking: {type: enabled}` + `reasoning_effort`：用 `llm-pi-ai` + `compat.thinkingFormat: deepseek`，`headers` 放 `x-sandbox-id`。
  2. 若网关只认顶层 `enable_thinking`：写一个最小 `LlmAdapter` 插件（dsh 的 adapter 是插件，`docs/architecture.md:11-19`），或验证 `qwen`/`chat-template` format 是否产生可接受的 body。
- 失败判定：网关返回 4xx/忽略 thinking；或 `max` 被降级且无法观测；或 1M/500k/131k 被拒。失败则「换 dsh」的主要收益不成立，应暂缓。
- 证据锚点：`packages/llm/llm-pi-ai/src/config.ts:102-104,152`；`tests/adapter.spec.ts:683,701`；`docs/deepseek-llm-api-wire-extensions.md:5,41-113`。

### G2：工具面与披露税（决定能否保住现有能力）

**要证明**：101 个 VT MCP 工具在 dsh 上可连接、可调用、长任务可完成，且工具选择质量不显著低于当前。

- 连接：stdio + `env` 注入 `CLICKHOUSE_*`/`VT_MEMORY*`；工具名 `mcp__vibe-trading__*`；5 个记忆工具出现。
- 长任务：`backtest` 等分钟级调用在 600s 内完成；progress heartbeat 能重置超时（或改 `toolCallTimeoutMs` + 服务端拆任务）。
- 披露税：测每轮 input token、工具选择准确率（用 `harness-evolution/evals/tool_selection` 的 353 条语料、LLM 判官）；对比当前 opencode 基线。
- 治理：如果 101 工具导致退化，优先复用 B 批「裁掉不可用工具」资产（74→59，−17.9% token/轮），或在 dsh 写一个 `deferLoading`/tool-search 插件（协议有 `deferLoading`，但没有 shipped 实现，`packages/llm/llm/src/types.ts:516-523`）。
- 失败判定：长回测超时无法解决；或工具选择准确率显著低于当前且无治理手段。失败则迁移后核心能力不可用。

### G3：控制面/部署契约（决定能否上生产）

**要证明**：RM/ACS/前端/cron 能在不重写业务的前提下驱动 dsh，并且 K8s 能正确判就绪。

- 驱动：用 `dsh --profile headless --json` 或 TS/Python SDK 替代 `opencode run --attach`；确认 session resume、事件流、token/usage、退出码。
- 就绪：dsh 无 health；要么等 stdout 启动 URL 行（Loader settle 后打印），要么写一个 `ctx.webServer.register('/health')` 插件；不能只探 TCP（端口先 bind）。
- 认证/网络：`--host` 拒绝 `0.0.0.0`；容器内用 Pod IP 或反代；token/cookie 与当前 Basic auth 不兼容，需要 adapter 或反代层转换；单 owner，无多租户。
- 失败判定：无法在不改 RM/前端的前提下驱动；或 readiness 不能稳定判真；或认证链路需要大改且无资源。

**建议的 spike 顺序**：G1（3-5 天）→ G2（3-5 天）→ G3（5-8 天）。G1 失败就停，不必做 G2/G3。

---

## 9. 建议

### 9.1 短期（现在）

**不切生产，做定向 spike。** 固定 dsh 版本（建议 pin 到 `0.2.1-rc.5` 或 `0.2.2-alpha.3` 的 commit，不要用浮动 `latest`；TS SDK 若用，显式 pin `next`/精确版本，因为 npm `latest` 是 `0.0.1-rc.1`）。产出 G1/G2/G3 的实测报告，再决定。

### 9.2 中期（若 G1-G3 通过）

**单工作流试点 4 周，不做 big-bang。** 选一个非关键、可回滚的路径（例如 cron 的 `checkup` 或 intake/research-scenario），与 opencode 并行跑；用同一套 VT MCP、同一套 skills/AGENTS 内容，比较工具路由、延迟、token、启动、失败率。历史会话留在 opencode，新会话走 dsh，明确 cutover 边界。

### 9.3 长期（若决定全面迁移）

**先解决「模型接入路径」再谈 harness。** 如果生产继续走 Alibaba 代理，dsh 的 DeepSeek 原生优势拿不到，迁移收益主要是架构/维护性，不足以抵消 maturity 风险；如果生产会切 DeepSeek 官方 API，dsh 的收益才完整，此时应把「官方 API 迁移」与「harness 迁移」作为一个组合项目，而不是两件事分别做。

### 9.4 如果目标只是「减少 harness 维护/供应商胶水」

有一个更便宜的中间方案：**保留 opencode，先做减法**——裁掉未使用的 OMO agents/categories/commands，复用 B 批工具暴露治理（74→59），把 `AGENTS.md` 的死引用清掉，完善 startup/readiness 与长任务观测。这些改动不依赖 dsh 的稳定性，能先拿到一部分收益，也为未来迁移准备更干净的基线。

### 9.5 明确的「不要做」

- 不要假设「dsh 内置工具少」就能抵消 101 个 MCP 工具的披露税。
- 不要假设「dsh 的 DeepSeek adapter」能直接接 Alibaba 的 OpenAI-compatible 代理（它默认是 Anthropic Messages 形状，且没有 `headers`）。
- 不要假设 OMO 的编排是已验证的生产依赖；它当前更多是模型档位 + 目录。
- 不要在没有 G3 的情况下做 big-bang；dsh 没有 health、拒绝 `0.0.0.0`、会话格式不兼容，回滚成本很高。
- 不要把「VT 内部 LLM」算进这次迁移；它仍是 DashScope `qwen3.8-max`。

---

## 10. 证据索引与限制

### 10.1 当前仓库（相对 `mymain-boot/`）

- 路线与回退：`AGENTS.md:15,33`；`mymain-wiki/AGENTS.md:20-24`；`mymain-wiki/branch/MYMAIN_DIVERGENCE.md:18,28-36,203,207-215,225,239,443-450,504-509`
- 配置：`OpencodeAgent/config/opencode.json.tmpl:1-82`；`oh-my-openagent.json:3-173`；`vibe-trading-tools.json:1-7`；`render_config.py:74-87,90-124,141-149,174-178`
- 模型：`opencode.json.tmpl:3-29`；`Dockerfile:172-175`；`IMAGE-MANUAL.md:212-228`；`DEPLOY-GUIDE.md:80`
- Skills/命令：`Dockerfile:115,119-122`；`OpencodeAgent/README.md:19,78-86`；`config/command/*.md:1-11`；`mymain-wiki/features/f13-ashare-evidence-skills.md:16,22,37,43-46,72-76,91`
- MCP/记忆：`agent/mcp_server.py:9-31,33-42,869-1017,1026-1077`；`agent/src/memory/mcp_adapter.py:10-17`；`memory_guard.py:8-10,56-76`
- 部署/就绪：`OpencodeAgent/Dockerfile:41-122,143-206`；`Dockerfile.base:1-32,57-72`；`entrypoint.sh:129-199,348-405,407-496,498-560`；`docker-compose.yml:16-31`；`scripts/check_startup_readiness.sh:71-82`；root `AGENTS.md:88-164`；`mymain-wiki/features/f7-boot-cost.md:13-24,39,53-67,73`
- 评测/证据：`OpencodeAgent/tests/test_config_render.py:235-236,327-333,371-439,511-519`；`tests/test_agents_md.py:99-114`；`tests/evals/intake/baseline_report.md:3-8,18,52-61`；`tests/evals/intake/results/question_tool_probe.md:39-56`；`tests/evals/intake/plugin_feasibility.md:18-28,160`；`mymain-wiki/harness-evolution/HARNESS_EVOLUTION_SUMMARY.md:14-18,83-111`；`HARNESS_EVOLUTION_RESEARCH.md` §4.2
- 死配置/漂移：`OpencodeAgent/AGENTS.md:8,383-384`；`Dockerfile:111-112,130-137,159-168`；`.env.example:47-49`；`mymain-wiki/features/f7-opencode-agent.md:21-22,31`；`DEPLOY-GUIDE.md:33-35`

### 10.2 dsh（本地 `/Users/mgong/LegoNanoBot/deepseek-harness`，HEAD `ce7073d512`）

- 架构/组合：`docs/architecture.md:11-25,81-105,134-138`；`docs/agent-lifecycle.md:8-83`；`packages/util/official-bundles/src/bundles.ts:10-26`；`packages/bundle/base/cordis.patch.yml:11-13,281-497`；`packages/bundle/web-app/cordis.patch.yml:467-533,593`；`apps/cli/src/profile-boot.ts:82-88,180-198,300-345`
- 入口/服务：`apps/cli/README.md:11-18`；`apps/cli/reference/README.md:11,41-44,53,109-131`；`apps/cli/src/args.ts:175-178`；`packages/host/webserver/README.md:28,36-37,61,73`；`docs/subsystems/web-server.md:5,106,205`；`docs/api-gateway.md:126-132`；`packages/client/connection/README.md:41-51,83`
- MCP：`packages/mcp/mcp-client/README.md:28,34-53,55-67,71-81,85,93,107-109,133,137,163-171,208-213`；`packages/mcp/mcp-resources/README.md:12,30-32`；`src/transport.ts:29-46`
- Skills：`packages/skill/skill-filesystem/README.md:36-38,42,50-56,70-73,82-88`；`packages/skill/tool-skill/README.md:12,54-56,89`；`packages/skill/skill-office/README.md:12,30-38`
- Subagents/jobs：`packages/subagent/README.md:27-33`；`packages/subagent/subagent/README.md:49-65`；`packages/subagent/tool-subagent/README.md:49-77,160`；`packages/jobs/jobs-local/README.md:24-32`；`packages/jobs/tool-jobs/README.md:12,26`
- 模型：`packages/llm/llm-pi-ai/src/config.ts:96-166,152,158,269`；`packages/llm/llm-pi-ai/README.md:34,79-80,101-107,146-148`；`packages/llm/llm-pi-ai/src/catalog.ts:101-115,130-136,389-460,419-428`；`packages/llm/llm-pi-ai/tests/adapter.spec.ts:683,701-705`；`packages/llm/llm-deepseek/README.md:55-59,83-85,103,143,177`；`packages/llm/llm-deepseek/src/config.ts:19-56`；`docs/deepseek-llm-api-wire-extensions.md:5,24-30,41-113`
- Session/持久化：`packages/core/session/README.md:12,82-84`；`packages/core/session/src/types.ts:90`；`packages/session/session-persistence/README.md:53,59-61`；`packages/session/session-persistence-jsonl/README.md:75`；`packages/session-query/session-query-sqlite/src/schema.ts:8,50`；`docs/session-format-status.md:20-56`
- 安全/权限：`packages/sandbox/README.md:12,22-25`；`packages/sandbox/sandbox/README.md:60-66`；`packages/fs/fs-sandbox/README.md:12,30-38`；`packages/shell/tool-bash/README.md:12,68-70`；`docs/subsystems/approval.md:24-48`；`docs/subsystems/permission-presets.md:12-22`；`packages/credentials/credentials-local/README.md:12`
- 工具/上下文/压缩：`docs/tool-catalog.md:18-52`；`packages/context/agent-instructions/README.md:34-38,64-68,90`；`packages/compaction/compaction-basic/README.md:67-88`；`packages/spill/spill-policy/README.md:38,43`；`packages/guard/repeat-tool-reminder/README.md:41-52`；`packages/llm/llm/src/types.ts:516-523`
- 扩展/政策：`docs/cookbook/adding-a-package.md:5-26`；`packages/AGENTS.md:7`；`packages/extensions/README.md:25-29`；`AGENTS.md:7,11`；`packages/README.md:89`；`.agents/skills/dsh-create-upgrade-guide/SKILL.md:9-45`；`CONTRIBUTING.md:7-20`；`LICENSE:1`
- 平台/运行时：`package.json:3-10`；`native/README.md:5-7`；`native/system/README.md:12-20`；`packages/subprocess/subprocess-local/package.json:55`；`packages/fs/tool-fs-search/package.json:32`
- 明确缺口：`grep` 无 `/health`/`readinessProbe` shipped route；`find . -iname 'Dockerfile*'` 仅 sshd fixture；`grep` 无 `customCommand|commandDirs|.dsh/commands`；`find packages -maxdepth 2 -iname '*memory*'` 无 first-party memory

### 10.3 公共来源（访问 2026-11-03）

- <https://github.com/deepseek-ai/deepseek-harness>
- <https://github.com/deepseek-ai/deepseek-harness/releases>（36 releases：20 alpha + 16 rc + 0 stable）
- <https://github.com/deepseek-ai/deepseek-harness/blob/8bd073392eef6f54e921a093b95076248eb08cbb/README.md>（developer preview / breaking changes）
- <https://github.com/deepseek-ai/deepseek-harness/blob/8bd073392eef6f54e921a093b95076248eb08cbb/SAFETY.md>（未审计 / 非 production-ready）
- <https://github.com/deepseek-ai/deepseek-harness/blob/8bd073392eef6f54e921a093b95076248eb08cbb/docs/user/guide/providers.md>
- <https://github.com/deepseek-ai/deepseek-harness/blob/8bd073392eef6f54e921a093b95076248eb08cbb/docs/subsystems/mcp.md>
- <https://github.com/deepseek-ai/deepseek-harness/blob/8bd073392eef6f54e921a093b95076248eb08cbb/docs/subsystems/skills.md>
- <https://github.com/deepseek-ai/deepseek-harness/blob/8bd073392eef6f54e921a093b95076248eb08cbb/docs/subsystems/subagent.md>
- <https://github.com/deepseek-ai/deepseek-harness/blob/8bd073392eef6f54e921a093b95076248eb08cbb/docs/user/guide/public-deployments.md>
- <https://github.com/deepseek-ai/deepseek-harness/blob/8bd073392eef6f54e921a093b95076248eb08cbb/docs/api-gateway.md>
- <https://github.com/deepseek-ai/deepseek-harness/blob/8bd073392eef6f54e921a093b95076248eb08cbb/docs/user/develop/basic/index.md>
- <https://github.com/deepseek-ai/deepseek-harness/blob/8bd073392eef6f54e921a093b95076248eb08cbb/docs/user/develop/basic/publish.md>
- <https://github.com/deepseek-ai/deepseek-harness/blob/8bd073392eef6f54e921a093b95076248eb08cbb/docs/subsystems/product-telemetry.md>
- <https://github.com/deepseek-ai/deepseek-harness/blob/8bd073392eef6f54e921a093b95076248eb08cbb/docs/deepseek-llm-api-wire-extensions.md>
- <https://registry.npmjs.org/@deepseek-ai/dsh>（最新 `0.2.1-rc.5`，`0.2.2-alpha.3`；41 版本全预发布）
- <https://registry.npmjs.org/@deepseek-ai/dsh-sdk-client>（`latest=0.0.1-rc.1`，`next=0.2.1-rc.5`）
- <https://pypi.org/project/deepseek-harness-sdk/>（`0.2.1rc2`，2026-10-17）
- <https://pypi.org/project/deepseek-harness/>（第三方包，非官方 SDK）
- <https://deepseek-harness.github.io/deepseek-harness/>
- <https://github.com/cordiverse/cordis>

### 10.4 置信度

- **高**：当前仓库的配置/部署/测试证据（直接读文件）；dsh 的本地源码/文档行为（直接读文件）；npm/GitHub/PyPI 元数据；README/SAFETY 的稳定性与安全声明。
- **中**：dsh 的「每 turn 工具数」（由 bundle/preset 行手工计数，非运行时抓包）；发布节奏（按时间戳派生）；docs site 与仓库文档的一致性。
- **低/未知**：Alibaba 网关对 `thinking:{type}`/`chat_template_kwargs` 的接受度；`qwen` thinkingFormat 的 wire body；dsh 在生产规模下的并发/吞吐/冷启动；ARM64/QEMU/离线镜像；dsh 是否支持 MCP progress 超时重置；官方 API 的 `dsh_session_log` 实际外发内容。

### 10.5 下一步（可直接执行的验证）

1. 固定 dsh 版本（pin commit），用 `llm-pi-ai` + `openai-completions` + `baseURL` + `headers.x-sandbox-id` 接 Alibaba 代理，测 `thinkingFormat: deepseek` 与 `chatTemplateKwargs` 两条路，记录真实请求/响应。
2. 用 stdio 接 VT MCP，跑 101 工具的 token 计数与 20 条代表性 query 的路由准确率；对比 `harness-evolution/evals/tool_selection` 现有基线。
3. 用 `dsh --profile headless --json` 跑一条分钟级 `backtest`，验证 600s 超时/progress；再跑一条需要恢复的 session。
4. 在容器里验证 `dsh web` 的 `--host`（Pod IP）、token/cookie 认证、反代 `Host`/`Origin`、自建 readiness（stdout 启动行或 `/health` 插件）。
5. 若走官方 DeepSeek API，显式检查 `dsh_session_log`/`dsh_plugin_packages` 开关与外发内容，形成数据治理结论后再谈生产。
