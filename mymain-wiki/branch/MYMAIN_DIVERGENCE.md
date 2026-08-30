---
title: mymain 分支差异说明（DIVERGENCE）
description: mymain 与上游 main 的权威差异台账——独有功能 F1-F7、上游贡献队列、验证门禁、维护约定、已知债务。改代码/发 PR/rebase 前必读。触发词：分支差异、上游、回流、rebase、门禁、D1-D4。
type: delta
status: active
created: 2026-07-27
updated: 2026-08-30
tags: [branch, divergence, upstream, gates]
related: [MYMAIN_README.md, ../AGENTS.md]
---

# mymain 分支差异说明

> 维护者：shadowinlife ｜ 基线：HKUDS/Vibe-Trading `main` @ `fb5013c2`（v0.1.14 后，2026-08-30 对齐，上游自 `80ffdda4` 前进 79 commit）

## 1. 分支定位

`mymain` 是 shadowinlife 个人维护的**锁定演进分支**，承载记忆系统 T4 迭代与本地 ClickHouse A 股数据源，功能上领先于社区 `main`。本分支作为个人生产/验证环境的稳定基线，所有改动最终以社区 PR 形式回流上游；PR 合入后相应条目从本文档移除，全部合入后本分支回归纯跟踪分支。

相对 `origin/main` 的差异总量：**626 个文件、+118306/−140 行**（含 `mymain-wiki/` 知识库及其 harness-evolution 归档、分支级 AGENTS.md 扩展、ClickHouse 语义层 Phase 0–2 与 `OpencodeAgent/` harness 层）。功能历史组织为 **6 个单一功能 commit**（F1→F5 + docs，每个可独立作为社区 PR 候选）+ 语义层 Phase 0–2（fork PR #1，个人部署独有）+ OpencodeAgent harness（F7，个人部署独有）。

## 2. 核心功能差异

### 2.1 独有 feature（相对上游）

| # | Feature | 能力 | 核心文件 | 开关 | 上游关系 |
|---|---|---|---|---|---|
| **F1** | 反思课程存储 | 按策略类型 append-only JSONL 课程库；标签/子串检索；置信度更新；`auto_reflect_from_run_dir` | `agent/src/memory/reflections.py`（新增）+ 测试 | `VT_MEMORY_REFLECTIONS`（被 `VT_MEMORY=full` 预设隐含） | 上游无对应（remember_tool / memory CLI 是不同暴露面） |
| **F2** | MCP 记忆工具 | 五个 MCP 工具 memory_save / recall / reinforce / reflect / status；never-raise dict 包络适配层；memory-lifecycle SKILL 工作流 | `agent/src/memory/mcp_adapter.py`（新增）、`agent/mcp_server.py`（注册段）、`agent/src/skills/memory-lifecycle/SKILL.md` | `VT_MEMORY_MCP_TOOLS`（默认 OFF；当前分支 OFF=77 / ON=82，其中 3 个为语义层 ch_* 工具） | 上游无 MCP 面记忆工具（上游 MCP 面 62→70 后本轮零新增） |
| **F3** | 回测反思钩子 | `run_backtest` 成功后 fire-and-forget 提取 run_card 课程（MCP 与 in-process 入口均覆盖，非致命）；附延迟基准（bench marker，p50<200ms / p95<500ms）与 5 会话并发测试 | `agent/src/tools/backtest_tool.py`（钩子段）、`agent/tests/memory/test_latency_bench.py`、`test_concurrent_mcp.py`、`conftest.py`、`pyproject.toml` | 随 F1 联动 | 上游 post-backtest attribution 是 prompt 驱动，机制不同 |
| **F4** | MemoryGuard + 项目目录存储 | FastMCP middleware：工具调用后自动 memory_save + memory_reflect（零 LLM）；`VT_MEMORY_BASE_DIR` 支持记忆存项目目录；默认路径跟随 `get_runtime_root()`（`VIBE_TRADING_HOME` 感知） | `agent/src/memory/memory_guard.py`（新增）、`agent/src/memory/persistent.py`（`_default_memory_base`）、`agent/src/config/env_schema.py`（`MemoryConfig.base_dir`） | middleware **无条件注册**（债务 D1） | 上游 memory 仍锚定 `~/.vibe-trading`（上游对 persistent.py 的改动——FTS5 排序衰减、FTS5 tokenizer 下限——均不同关注面，两者并存） |
| **F5** | ClickHouse A 股数据源 + 语义层 | CH HTTP connector + OHLCV loader（DataLoaderProtocol）+ 基本面 Provider（回退 Tushare）+ 四只资金流工具 CH 优先回退；A 股 chain 与路由以 clickhouse 为首选。**语义层 Phase 0–2**（2026-08-12）：56 表 DDL 快照 + 9 表 444 列 COMMENT + 单位 registry（`clickhouse_units.py`）+ 显式 199 列（`clickhouse_columns.py`）+ `get_valuation` + llm_role 受约束灵活性通道（`ch_list_tables` / `ch_describe_table` / `ch_query`，sqlglot AST 守卫） | `agent/src/clickhouse_connector.py`、`agent/backtest/loaders/clickhouse.py`、`agent/src/tools/clickhouse_fallbacks.py`、`schema/clickhouse/`、`agent/src/tools/clickhouse_query_tool.py`、`agent/src/tools/clickhouse_explore_tools.py`、`agent/src/tools/valuation_tool.py` 等 97 文件 | `CLICKHOUSE_*`（DataConfig）；灵活性通道 `CLICKHOUSE_LLM_USER` / `CLICKHOUSE_LLM_PASSWORD` | 个人部署独有，不回流 |
| **F7** | OpencodeAgent harness 层 | opencode + omo + 本仓库 MCP 的独立部署 harness（Docker 镜像 `opencode-serve`）：问题处理协议（明确/开放/待澄清/宏观四类分流，Least-to-Most 收敛漏斗 + Step-Back 拆分 + 单轮 ≤3 问轮次预算）、防幻觉与诚实拒答纪律（数字溯源三来源、弃权一等公民、五要素拒答模板）、escape-top 微观结构信号（CH 数据层 + 7 门验证框架）、三层选股、VT 联邦行情 scanner、cron + 钉钉通知基础设施、nano-search-mcp（新浪财经/百炼搜索 12 工具） | `OpencodeAgent/`（整目录，源自独立仓库 vibetrading-opencode-instruct，2026-08-17 引入） | 容器 env（`CLICKHOUSE_*` / `CLICKHOUSE_LLM_*` / `DASHSCOPE_API_KEY` 等，见 `OpencodeAgent/.env.example`） | 个人部署独有，不回流；消费 F5/F6 语义层（ch_* 工具）与 F1–F4 记忆能力 |

### 2.2 已随对齐消除的历史分歧（上游已承接）

| 项 | 去向 |
|---|---|
| `run_gc(dry_run=True)` 压缩副作用门控 | 本分支 PR #973 **原样合入**上游（`397c76c`） |
| 层级路由 `.md` 后缀 writer 修复（PR #972） | 上游 #984 + 孤儿恢复 `5b638b2` + pin 测试 `9ae0f71` 等价落地；#972 的回归测试与注释被上游收编（Co-authored-by: shadowinlife） |
| README MCP 工具数同步（PR #974） | 上游 `7539577` 自行修正并新增 `test_readme_counts.py` 锚定 |
| 本地"读时容忍"无后缀条目（`_is_category_entry`） | 被上游 `recover_extensionless_entries()` 孤儿恢复取代，对齐时主动移除 |
| 本地 routed 命名 `<category>/{type}_{slug}.md` | 采纳上游 `<category>/<slug>.md`（上游 pin 测试明确排除本地方案） |
| A 股 volume 单位不一致（#1062）Phase 1/2 修复 | shadowinlife PR #1065（loader 按 market 声明 `volume_units` + `_provenance.volume_unit`）与 PR #1067（baostock 股→手归一化 + 跨源一致性测试 + 缓存 v3→v4）于 2026-08-11 合入上游；mymain 经 2026-08-17 对齐继承 |

**2026-08-11 核查结论**：上游自 `6c44732` 前进 120 commit（v0.1.13 发布），逐项核对 F1–F5 均**未被上游取代**——上游本轮新增能力（quantlib_call / alpha zoo MCP 工具 / 机构持仓 / ETF 穿透 / 预测市场 / 论文检索 / 加拿大市场 / eToro / 桌面端等）与本地五项能力无重叠；本地 F4 的 `_default_memory_base()` 继续复用上游共享基础设施 `get_runtime_root()`，F5 沿用上游 loader 注册模式（`VALID_SOURCES` + `_loader_modules` + `FALLBACK_CHAINS`），无重复实现需要移除。

**2026-08-17 核查结论**：上游自 `1bf1d8b4` 前进 144 commit（tickerall 数据源、Options Lab / tearsheet / factor research panel、Copilot SDK / Novita provider、桌面端加固、reasoning effort 全 provider 贯通、grounding recovery 等），逐项核对 F1–F5 与语义层均**未被上游取代**——上游对 reflections / mcp_adapter / memory_guard / memory-lifecycle / backtest_tool 零触碰，MCP 面零新增工具注册，与 ClickHouse 无重叠；上游唯一 memory 提交 `fdb4cdd9`（FTS5 tokenizer 下限对齐）与 F4 不同关注面，自动合并共存。本轮合入上游的 shadowinlife PR（#1065 / #1067 volume 修复、#1091 tearsheet、#1096 Options Lab、#1099 factor research panel）均来自独立 feature 分支，与 mymain 本地 commit 无交集。计数随本轮对齐更新：数据源 25→26（上游 tickerall + 本地 clickhouse）、skills 维持 90、MCP 维持上游基数 70（本分支含 3 个 ch_* 语义层工具为 73/78）。

### 2.3 上游贡献队列（含代码点，按依赖序逐步推入以缩小分歧）

| 序 | Feature | 具体代码修改点 | 提交前置工作 |
|---|---|---|---|
| **① F4 路径部分** | memory 路径跟随 `VIBE_TRADING_HOME` | `persistent.py::_default_memory_base()`（改调用期求值）；新增旧路径 `~/.vibe-trading/memory` → 新根目录的一次性迁移 + 启动告警；新增 `VIBE_TRADING_HOME` 覆盖/迁移测试 | 同步更新上游文档"memory 仍锚定 ~/.vibe-trading"表述；`MemoryConfig.base_dir` 声明已就绪 |
| **② F1** | 反思课程存储 | `reflections.py` 整文件新增；`env_schema.py` 增加 `VT_MEMORY_REFLECTIONS` 与 `VT_MEMORY=full` 预设语义；`.env.example`；测试套件 | 顺手实施迭代笔记中的非阻塞建议（`_iter_lessons` 重命名、逐行读、自定义 encoder）减少 PR 往返 |
| **③ F2** | MCP 记忆工具 | `mcp_adapter.py` 整文件新增；`mcp_server.py` 五工具注册段（含 `VT_MEMORY_MCP_TOOLS` 门控）；`memory-lifecycle/SKILL.md`；**六份 README**（含 2026-08-16 新增 README_es.md；skills 89→90、Tool 类 10→11 及相关 prose）；`agent/SKILL.md`（skills=90、Finance Skills 小节标题）；测试 | README 计数更新必须随 PR 一并提交（上游 pin 测试强制）；注意上游 MCP 基数为 70（本分支头注释 73/78 含 3 个不回流的 ch_* 工具，PR 需按当时上游基数重述） |
| **④ F3** | 回测反思钩子 | `backtest_tool.py` daemon 线程钩子；`conftest.py` bench marker；`pyproject.toml` markers；bench/并发测试 | 依赖 ②（反思存储 API） |
| **⑤ F4 中间件部分** | MemoryGuard | `memory_guard.py` 整文件新增；`mcp_server.py` 注册段 | **必须先解决 D1（加 env 门控开关）与 D2（dedup/增长）**，否则过不了社区评审 |
| ✗ F5 | ClickHouse | — | 暂不回流（个人部署独有） |

### 2.4 已知上游缺陷（mymain 跟踪）

| 缺陷 | 上游 Issue | mymain 状态 |
|---|---|---|
| （当前无未决项） | — | — |

**#1062 闭环记录（2026-08-17）**：A 股回退链 volume 单位不一致（tencent/mootdx/eastmoney/tushare=手 vs baostock=股，相差 100x）经 shadowinlife 两阶段 PR 修复并已上游合入——Phase 1 [PR #1065](https://github.com/HKUDS/Vibe-Trading/pull/1065)（loader 按 market 声明 `volume_units`，`_provenance` 输出 `volume_unit`，tencent/eastmoney 市场依赖：A 股=手、HK=股）与 Phase 2 [PR #1067](https://github.com/HKUDS/Vibe-Trading/pull/1067)（baostock 股→手归一化 + 跨源一致性测试 + 缓存版本隔离 v3→v4），均于 2026-08-11 合入，规范单位定为「手」；mymain 经 2026-08-17 对齐继承全部修复（含 `get_market_data` 工具描述与 `mcp_server.py` docstring 的单位说明）。mymain 的 CH `stk_factor_pro.vol` 为 tushare 口径（手），与归一化方向一致，无需改动。原始实证（2026-08-11）：`600519.SH` 2026-07-31 tencent=55,128 手 vs baostock=5,512,752 股，比值恰 100.0x，经成交额交叉验证确认同一物理量。

## 3. E2E 验证方式

### 3.1 测试套件与静态门禁（conda env `legonanobot`，macOS arm64 / Python 3.12）

```bash
# memory 套件（含上游孤儿恢复/GC/pin 测试与上游新增 FTS5 衰减/tokenizer 测试）——基线 309 passed / 3 skipped
python -m pytest agent/tests/memory/ agent/tests/test_persistent_memory.py \
  agent/tests/test_memory_orphan_recovery.py agent/tests/test_memory_gc.py \
  agent/tests/test_env_schema.py -q

# ClickHouse 套件（F5 原始 3 文件 + 语义层 Phase 0–2 测试）——基线 137 passed / 11 skipped（skip = 需真实 CH 连接）
python -m pytest agent/tests/test_clickhouse_loader.py \
  agent/tests/test_clickhouse_fundamentals.py agent/tests/test_clickhouse_flow.py \
  agent/tests/test_clickhouse_anchor.py agent/tests/test_clickhouse_query_guard.py \
  agent/tests/test_clickhouse_semantic_tools.py agent/tests/test_clickhouse_unit_conversions.py \
  agent/tests/test_clickhouse_units.py agent/tests/test_tushare_fallbacks.py \
  agent/tests/test_valuation_tool.py -q

# ClickHouse schema 门禁（DDL 导出/注释门禁单测 + comments.yaml 覆盖门禁）——基线 53 passed / 1 skipped / exit 0
python -m pytest tools/test_ci_clickhouse_comments_gate.py \
  tools/test_clickhouse_apply_comments.py tools/test_clickhouse_export_ddl.py -q
python tools/ci_clickhouse_comments_gate.py

# README/SKILL.md 计数门禁——基线 76 passed（六份 README + manifest 全套 pin，含 quantlib badge 函数/模块数双锚定）
python -m pytest agent/tests/test_readme_counts.py agent/tests/test_distribution_skill_manifest.py -q

# env-var AST 门禁——基线 exit 0（4 条 WARN 来自上游 llm.py，与本分支无关）
python tools/ci_env_var_gate.py

# 延迟基准（默认跳过，显式运行）
python -m pytest agent/tests/memory/test_latency_bench.py -m bench
```

### 3.2 端到端冒烟（本地）

```bash
# MCP 工具计数门控：OFF=77 / ON=82（上游基数 74 + 本分支 3 个 ch_* 语义层工具 + 5 个 memory_* 工具）
cd agent
python -c "import asyncio, mcp_server; print(len(asyncio.run(mcp_server.mcp.list_tools())))"
VT_MEMORY_MCP_TOOLS=1 python -c "import asyncio, mcp_server; print(len(asyncio.run(mcp_server.mcp.list_tools())))"

# memory 工具往返（VT_MEMORY_MCP_TOOLS=1 启动后）
#   memory_save → memory_recall → memory_status 应返回 ok 包络；
#   memory_reflect 在 VT_MEMORY_REFLECTIONS 关闭时应返回 skipped 而非 error

# 回测反思钩子：VT_MEMORY_REFLECTIONS=1 跑一次 run_backtest，
#   成功后数秒内 <runtime_root>/memory/reflections/<策略类型>.jsonl 应新增一条课程

# ClickHouse 联邦取数（CH 可达时）：get_market_data 拉取 000001.SZ，
#   日志应显示 clickhouse 命中；断开 CH 后应静默回退 tencent/mootdx 网络链
```

### 3.3 远端登录与接入方式

| 场景 | 登录/接入方式 |
|---|---|
| **远端 ClickHouse**（F5 数据源） | `agent/.env` 配置 `CLICKHOUSE_HOST`（个人部署缺省 `172.24.165.51`）、`CLICKHOUSE_PORT=8123`、`CLICKHOUSE_USER`、`CLICKHOUSE_PASSWORD`、`CLICKHOUSE_DATABASE=ashare`；HTTP 接口，无 TLS，密码仅存本地 `.env`（不入 commit）。灵活性通道（ch_* 工具）另需 `CLICKHOUSE_LLM_USER` / `CLICKHOUSE_LLM_PASSWORD`（SELECT-only `llm_role`，30s/2GB/100 万行/50MB profile，绝不回退 default 用户） |
| **远端 MCP 服务**（F2 工具暴露） | 服务端 `python mcp_server.py --transport http --host 0.0.0.0 --port 8900`，并设 `VIBE_TRADING_MCP_ALLOWED_HOSTS=<客户端可见的主机名/IP>`（缺省仅放行 loopback，DNS-rebinding 防护 GHSA-p3c9）；客户端指向 `http://<host>:8900/mcp`（Streamable HTTP，单端点；旧客户端可用 `--transport sse`） |
| **远端 Web UI / API** | `vibe-trading serve` + `agent/.env` 设 `API_AUTH_KEY`；远端浏览器首次进入 Settings 输入一次 key，API 请求携带 `Authorization: Bearer <key>`；未带 key 的非 loopback 客户端敏感端点一律 403 |
| **GitHub fork 推送** | SSH 认证（`git@github.com:shadowinlife/Vibe-Trading.git`，本机 SSH key）；API 操作走 `gh` CLI（已登录 token）；注意 mymain 分支保护，见 §4.2 |

## 4. 维护约定

### 4.1 上游对齐流程（每次上游重大推进时）

1. `git fetch origin && git branch backup/mymain-<date> mymain`（先留回退点）。
2. 检查 `git log mymain..origin/main` 与 `gh pr list --author shadowinlife`：确认本分支哪些 PR/hunk 已被上游吸收 → 对齐时**主动丢弃**这些 hunk，不留重复实现；同时逐项核对 F1–F5 是否被上游同类能力取代。
3. 对齐并重整历史，原则：**上游机制优先**（上游有等价或更强实现时采纳上游、删除本地分叉），本地只保留纯增量能力；最终历史必须是「上游 main 为祖先 + 每个 feature 一个独立 commit + 单一作者」。两种等价做法：
   - `git rebase origin/main` 逐 commit 解冲突（适合 patch 少、冲突面小的轮次）；
   - **merge + carve**（本轮采用）：临时分支上 `git merge mymain` 一次性解冲突得到目标树，再从 `origin/main` 逐 feature 切出干净 commit，最后校验目标树与 merge 树逐字节一致（适合上游推进大、共享文件多的轮次）。
4. 全量复验 §3.1/§3.2 基线。
5. 更新本文档（基线 commit、差异总览、已消除分歧、验证基线）。

### 4.2 分支保护与推送

- fork/mymain 保护：**禁止 force-push、禁止删除**（enforce_admins=true）；保持普通提交通道开放。
- 历史重写后只能 force-push，流程：**GitHub API 临时启用 `allow_force_pushes` → `git push fork mymain --force-with-lease` → 立即恢复保护**（保护空窗控制在分钟级）。
- 不在本分支直接开发新特性；新改动走独立 feature 分支，验证通过后合入。

### 4.3 上游 pin 测试带来的持续义务

- **README/SKILL.md 计数**（`test_readme_counts.py`、`test_distribution_skill_manifest.py`）：每增删一个 skill / loader / MCP 顶层工具，必须同步六份 README（2026-08-16 起含 README_es.md）+ `agent/SKILL.md` 计数（当前：skills=90、Tool 类=11、sources=26、本分支 MCP 头注释 73/78、SKILL.md `Available MCP Tools (73)` 与 `Finance Skills (90)` 小节标题）。注：上游 pin 测试只锚定 en/zh/ja/ko/ar 五份（翻译允许滞后），README_es.md 不锚定，本分支自行保持六份同步。
- **命名一致性**（`test_recovered_orphan_and_new_write_agree_on_the_same_path`）：routed 条目命名保持 `<category>/<slug>.md`，不得改回带类型前缀方案。
- **env-var AST 门禁**（`tools/ci_env_var_gate.py`）：新增环境变量必须声明进 `env_schema.py` 并经 config accessor 读取，禁止裸 `os.getenv` / `os.environ`。

### 4.4 提交规范

- `git commit -s` DCO 签名；commit message 与 PR 描述**禁止** AI 归属行（`Co-Authored-By` / `AI-Model` / `AI-Contributed`）。
- 所有 commit 作者保持单一身份 `shadowinlife <shadowinlife@gmail.com>`，与社区规范一致。
- 社区 PR 提交前逐条检查 `CONTRIBUTING.md` / `AGENT_CONTRIBUTOR_GUIDE.md` / `SECURITY.md` / PR 模板；未完成检查的 PR 保持 Draft。

### 4.5 已知债务（上游化前必须处理）

| # | 债务 | 影响 | 处理时机 |
|---|---|---|---|
| D1 | MemoryGuard **无条件注册**，无 env 开关 | 社区评审必拒 | 贡献队列 ⑤ 前置 |
| D2 | guard 存储按日命名 + 时间戳内容，dedup 失效、GC 默认关闭下无限增长 | 本地磁盘缓慢膨胀 | 贡献队列 ⑤ 前置 |
| D3 | `VIBE_TRADING_HOME` 迁移缺口：上游 #925 迁移不覆盖 memory，旧路径历史记忆不自动迁移 | 设了 `VIBE_TRADING_HOME` 的环境丢历史记忆可见性 | 贡献队列 ① 内容 |
| D4 | `MEMORY_BASE` import 期求值，env 变更需重启进程 | monkeypatch 测试不便 | 贡献队列 ① 顺手改调用期求值 |

### 4.6 待办研究任务

| # | 任务 | 背景与目标 |
|---|---|---|
| R1 ✅ | **ClickHouse 语义层深度研究**（2026-08-12 完成。正式调研结论（英文原文，含全部出处链接/架构图/数据流/场景决策示例）：[`CLICKHOUSE_SEMANTIC_LAYER_RESEARCH.md`](../clickhouse/CLICKHOUSE_SEMANTIC_LAYER_RESEARCH.md)；中文决策报告：[`CLICKHOUSE_SEMANTIC_LAYER_REPORT.md`](../clickhouse/CLICKHOUSE_SEMANTIC_LAYER_REPORT.md)） | 决策结论：**分层混合 + 语义下沉数据库**——不引入官方 mcp-clickhouse 作主接口（UInt64 损坏 #111 / readonly 可击穿 #131 / 无结果上限）；保留强化 F5 领域工具层为主通道；新增 L0 地基（DDL 入仓库 + COMMENT COLUMN 结构化注释 + llm_role 只读用户）与 L2/L3 分层探索 + 受约束 SQL 逃生舱；暂不引入 dbt SL/Cube（单消费者不满足价值判据）。落地分四阶段（Phase 0 地基 → Phase 1 主通道强化，协同 #1065/#1067 → Phase 2 灵活性通道 → Phase 3 可选演进）。原始调研证据——2026-08-11 调研结论：mymain 的 CH 语义层是「隐性的、代码携带的」——业务→表列映射/单位换算固化在 Python 工具链（connector 模板 + fallbacks 换算 + 工具描述 + skill 文档），数据库层零语义（仓库无 DDL、列无 COMMENT，CH 实例当前不可达未直查 `system.columns`）。已证实的缺口：① `get_market_data` 的 `SELECT *` 泄漏路径（pe_ttm/pb/total_mv 等 ~199 列）无语义标注，契约漂移；② 语义与数据物理分离，任何绕过工具链的直连（含 ClickHouse MCP）立即丢失全部语义。研究方向：a) 语义下沉数据库——CH 列 COMMENT + schema DDL 入仓库；b) 语义视图层——views + COMMENT（Altinity 模式：视图变工具）；c) SELECT * 泄漏路径显式工具化（如 `get_valuation`：pe_ttm/pb/total_mv 固定模板 + tushare daily_basic 兜底）；d) 指标字典——pe_ttm TTM 口径、amount/volume 单位、close vs close_hfq 选择规则；e) 若引入 ClickHouse MCP 供人工探索，语义层必须先库化或包领域工具层（官方 hdx-evals 证明领域工具 > 裸 SQL：准确率 +18%）。相关证据链见 #1062 审计评论与 opencode 会话记录 |

## 5. 迭代笔记

### 2026-07-27 五-agent 并行评审（Goal/QA/CodeQuality/Security/ContextMining）

- **死代码**：`build_default_adapter()` 从未被调用 → 移除（`mcp_adapter.py`）。
- **同步阻塞**：`auto_reflect_from_run_dir()` 在回测成功路径同步 I/O → daemon 线程 fire-and-forget（`backtest_tool.py`）。
- **误导错误信息**：锁超时误报为"启用 VT_MEMORY_REFLECTIONS" → `save_lesson` 用 `None`（关闭）vs `""`（锁超时）区分。
- **BLOCKING 文档**：早期 commit 含 `AI-Contributed` 行 → 提交社区 PR 前必须 `rebase -i` 清理。
- 非阻塞建议（留待社区 PR）：`_iter_lessons` 重命名为 `_read_lessons`；大 JSONL 逐行读取；`json.dumps(default=str)` 改自定义 encoder + 警告。

### 2026-08-04 rebase（基线 `3a752d5`）合规修复

- env-var AST 门禁 6 处违规修复（`VT_MEMORY_BASE_DIR` 入 `MemoryConfig`、`CH_*` → `CLICKHOUSE_*`、移除 `__import__("os")` 规避写法）。
- MemoryGuard 排除 `memory_*` 前缀工具自触发（消除自录制噪音）。
- 回测双重反思去重：guard 的 `_TOOLS_THAT_PRODUCE_INSIGHTS` 移除 `backtest`，保留 run_card 钩子为唯一反思入口。
- 对抗性审查（含 Oracle 复核）确认 mymain 五项能力均未与上游重复。

### 2026-08-07 rebase（基线 `6c44732`）

- 上游前进 52 commit；本分支 10 个 patch 全部重放，冲突集中在 memory 三文件、mcp_server、README、SKILL.md、market_data。
- **简化**：上游吸收 #973（原样合入）、以 #984 + 孤儿恢复等价替代 #972、以 `test_readme_counts` 替代 #974 后，本地主动移除读时容忍、统一 routed 命名、丢弃已回流 hunk——PR2 的"bugfix 半"完全由上游承接，本地只保留"feature 半"，memory 分歧面从 7 文件收窄到 5 个纯增量文件。
- **新增义务**：memory-lifecycle skill 使 bundled skills 89→90、Tool 类 10→11，五份 README + SKILL.md 已同步（过上游 pin 测试）。
- 验证基线：memory **317/2**、ClickHouse **13/8**、README+manifest 门禁 **48 passed**、env gate **exit 0**、MCP **OFF=62 / ON=67**；纯本地新增文件 black 24.10 / ruff clean（共享文件不整体 reformat，上游 CI 不跑 black --check 且其自身文件亦有漂移；`test_tier2_integration.py` 的 3 处 ruff 报告为上游既有问题）。

### 2026-08-11 merge + carve 对齐（基线 `c33133f4`，本次）

- 上游前进 120 commit（v0.1.13）；采用 **merge + carve** 流程：临时分支一次性合并解冲突，再从 `origin/main` 切出 6 个单一功能 commit（F1 反思存储 → F2 MCP 记忆工具 → F3 回测钩子 → F4 MemoryGuard/路径 → F5 ClickHouse → docs），carve 结果与 merge 树逐字节一致。
- **冲突面极小**：仅 2 处真冲突——`agent/SKILL.md`（上游 Canada 表述 + 计数区）与 `agent/mcp_server.py` 头注释（上游 62→70 工具计数）；其余 13 个共同改动文件全部自动合并（README×5、env_schema、persistent、registry、market_data、test_tier2、test_env_schema、pyproject、.env.example）。
- **取代核查**：F1–F5 均未被上游取代；上游新增 MCP 工具（quantlib_call、alpha_zoo/alpha_bench、机构数据四件套等）与本地能力无重叠。本地 MCP 计数随之更新：头注释 70/75、冒烟门控 OFF=70 / ON=75；skills 89→90 差异依旧（上游本轮未新增 skill）。
- **共享基础设施**：F4 继续复用上游 `get_runtime_root()`；F5 沿用上游 loader 注册/chain 模式，与上游新增 `ca_equity` chain 并存无冲突；上游 FTS5 排序衰减修复（`454364eb`）与本地 `_default_memory_base()` 在 persistent.py 不同区域并存。
- 验证基线：memory **321/2**（+4 为上游新增 FTS5 衰减测试）、ClickHouse **13/8**、README+manifest 门禁 **54 passed**（上游新增 6 条 pin 测试）、env gate **exit 0**、MCP **OFF=70 / ON=75**；ruff clean。注：本机 black 升至 26.1.0，对 2 个 CH 测试文件有纯风格重排要求，为保持与已验证树逐字节一致未跟随重排（上游 CI 不跑 black --check）。

### 2026-08-11 增量对齐（基线 `1bf1d8b4`）+ #1062 修复推进

- 上游前进 14 commit（c33133f4 → 1bf1d8b4）：swarm 三连修（retry 工件隔离 / raw-envelope 分类 / path-shaped agent id 拒绝）、backtest benchmark 修正字段舍入 + excess_return 一致性、agent compaction 全量过 summarizer、RSI Wilder-EWM 平滑、token 成本优化、README 新闻。合并**零冲突**（上游改动面与 F1–F5 无交集）。
- 验证基线：memory **321/2**、ClickHouse **13/8**、README+manifest 门禁 **54 passed**、env gate **exit 0** —— 与合并前完全一致。
- **#1062（上游 volume 单位不一致）推进**：完成 Phase 0 全量单位审计（实证矩阵：tencent/eastmoney/akshare/mootdx(暂定)/tushare=手，baostock=股，HK 链全链=股；tencent/eastmoney 市场依赖；akshare 官方文档标注错误——文档写「股」实测为「手」），Phase 1/2 分别以 Draft PR #1065 / #1067 提交上游。详见 §2.4。
- **新增 §4.6 待办研究任务**：R1 ClickHouse 语义层深度研究（语义下沉数据库 / 视图层 / SELECT * 泄漏路径工具化 / 指标字典 / MCP 引入前置条件）。

### 2026-08-12 ClickHouse 语义层落地（R1 执行：Phase 0–2 全完成，分支 `feat/clickhouse-semantic-layer`）

R1 研究结论（[`CLICKHOUSE_SEMANTIC_LAYER_RESEARCH.md`](../clickhouse/CLICKHOUSE_SEMANTIC_LAYER_RESEARCH.md) / [`CLICKHOUSE_SEMANTIC_LAYER_REPORT.md`](../clickhouse/CLICKHOUSE_SEMANTIC_LAYER_REPORT.md) / [`CLICKHOUSE_ITERATION_PLAN.md`](../clickhouse/CLICKHOUSE_ITERATION_PLAN.md)）全部落地；同步管道诊断与修复全程见 [`CLICKHOUSE_SYNC_DIAGNOSIS.md`](../clickhouse/CLICKHOUSE_SYNC_DIAGNOSIS.md)。

**Phase 0 地基（仓库侧）**
- `schema/clickhouse/`：56 张表 DDL 快照（`tools/clickhouse_export_ddl.py` 幂等导出、`--check` 漂移检测）+ `comments.yaml`（9 张 Tier-1 表 444 列，约定 `unit=/adjust=/caliber=/source=/desc=/ambiguous_with=`）+ README（真源关系与漂移处理流程）。
- `tools/`：`ci_clickhouse_comments_gate.py`（CI 门禁，接入 `test.yml`，负向测试通过）、`clickhouse_apply_comments.py`（dry-run/apply/verify）。
- 生产库 COMMENT 已应用 **444/444**，`system.columns` 空 COMMENT 计数 = 0。

**P0.0 同步管道诊断与修复（宿主 `/opt/qdata/sync`，仓库外）**
- 双根因：① **状态投毒**——空结果被永久标记已同步（未来日期一并标记）→ 全部 trade_date 表冻结在 2026-07-28；② **None 泄漏**——tushare Arrow-backed `str` dtype 绕过 `dtype==object` 清理 → 非空 String 列插入报 `DataError` → `set -e` 连带中止 snapshot/period 模式。
- 修复：`_query_with_retry` 页级重试+退避；`_paginate` 末页容错（tushare 在 offset 越过数据末尾时**报错而非返回空页**）；未来日期过滤；空结果 7 天宽限窗口；`set -e` 改模式解耦。
- 回填追平：trade_date **15 表**（~4000 万行）/ period **8 表** / snapshot **8 表** 全部至 T-1。
- `idx_weight` 深分页墙：offset ≥ ~102000 被 tushare 拒绝，末页容错兜底；`20260630` 去重（204000→102000，0 重复对）。**注意**：`idx_weight.trade_date` 为 `String` 存 `'YYYYMMDD'`（无连字符），与 `stk_factor_pro` 的 `Date` 类型不同。

**P0.3 安全加固**
- `llm_role`（SELECT-only on `ashare.*` + `llm_profile`：30s/2GB/100 万行/50MB）；`default` 设密码 + networks 收紧至 `127.0.0.1/::1/172.16.0.0/12`。
- 踩坑：users.d 用 `password_sha256_hex` 与镜像自带 `users.xml` 的空 `<password>` 合并冲突（Code 36 crash-loop）→ 改用同字段 `<password>` 覆盖。凭据原子分发 `/opt/qdata/.env` + `agent/.env` + `daily_sync.sh` 健康检查。V1–V7 验证通过，cron 已恢复。

**漂移治理（用户指令）**
- `engine._apply_upstream_drift`：tushare 上游新列 → 自动 `ALTER ADD COLUMN`（Nullable）+ `schema_drift_log` 审计表；上游消失列仅记录绝不删。回填中实战触发 18 条事件（`fin_express`/`fin_forecast` 自动扩列 `update_flag`）。仓库侧闭环见 `schema/clickhouse/README.md`。

**Phase 1 主通道强化（仓库代码）**
- P1.1 `SELECT *` 消除 → `clickhouse_columns.py` 显式 199 列；P1.2 `_provenance` 单位元数据（`market_data.py`，纯增量）；P1.3 `get_valuation` 工具（固定模板 + COMMENT 口径 + tushare daily_basic 兜底）；P1.4 `clickhouse_units.py` registry（读 `comments.yaml`、fail-soft 内建回退），移除 `clickhouse_fallbacks.py` ×10⁴ 与 `tushare_fallbacks.py` 北向 **×100 硬编码（实测 100 倍缺陷：north_money 原始即万元，2026-08-12 CH 与 tushare 活数据逐位一致）**。
- 验证：CH 套件 **53 passed / 9 skipped**（基线 13/8 保持）；回测回归单/多标的 **bit-for-bit 一致**；锚点行 600519.SH 2026-07-27（close=1289.5、vol=31990.44 手、amount=4129228.56 千元、total_mv=161198022.32 万元）通过。

**Phase 2 灵活性通道（仓库代码）**
- `ch_list_tables` / `ch_describe_table` / `ch_query`（仅 `llm_role` 连接，**绝不回退 default**）；sqlglot AST 守卫（单一 SELECT；拒 DDL/DML/Command/UNION/GLOBAL/SETTINGS/INTO/占位符/表函数；表白名单 live→snapshot 降级；LIMIT 500 注入/钳制；~50KB 截断+显式声明；#111 自定义序列化；审计日志）。
- MCP 镜像 3 工具，计数 **70→73**（5 份 README + `agent/SKILL.md` 同步）。
- 验证：65 guard 测试 + 17 攻击向量全拒；golden set 经 SSH 隧道 + llm_role 实测 **16/16（100%）**。

**已知边界 / 遗留**
- `llm_role` 1M 行/50MB 限额限制全表扫描聚合（设计内；探索通道面向键高效查询）。
- `/opt/qdata/sync` 管道改动在宿主侧、仓库外（Phase 3 纳入 git 后再版本化）。
- `idx_weight` 月末大日期（~10 万行）受 tushare 深分页墙限制，末页容错保证数据完整但单次抓取封顶 ~102k 行。
- `stk_cyq_chips`/`stk_cyq_perf` 维持排除（token 无筹码接口权限）。

### 2026-08-17 merge 对齐（基线 `0713336c`）+ fork 语义层回合

- 上游前进 144 commit（1bf1d8b4 → 0713336c）：tickerall 托管 MT5 数据源、Options Lab / tearsheet / factor research panel、Copilot SDK / Novita provider、桌面端 dormant update / Windows 打包加固、reasoning effort 全 provider 贯通、grounding recovery、swarm 元数据脱敏、跨平台 hash lock、README_es.md 西语 README 等。采用**直接 merge**（增量轮次、冲突面小）。
- **冲突面**：上游合并仅 1 处真冲突——`agent/SKILL.md` 计数/表述区 2 块（两侧各自 24→25 sources：本地 clickhouse vs 上游 tickerall），合并解决为 **26 sources + 90 skills**，保留上游 tickerall explicit-only 表述；随后回合 fork/mymain（语义层 Phase 0–2，fork PR #1，2026-08-12 合入 fork 但本地未拉回）又仅 1 处真冲突——`agent/src/market_data.py` provenance 块（上游 #1065 的 `volume_unit` 叠加 fork 的 `entry` 变量重构），两者合并保留。
- **新义务**：上游新增第六份 README（README_es.md），已同步 skills 89→90、Tool 类 10→11（上游 pin 测试不锚定 es，本分支自行保持六份同步）。
- **计数更新**：数据源 25→26（SKILL.md description / Backtesting 标题 / get_market_data 表行）；skills 维持 90；MCP 70/75→**73/78**（+3 为语义层 ch_list_tables / ch_describe_table / ch_query，非上游新增）。
- **取代核查**：F1–F5 与语义层均未被上游取代（上游对 F1–F4 核心文件零触碰、MCP 零新增注册、与 ClickHouse 无重叠）；上游唯一 memory commit `fdb4cdd9`（FTS5 tokenizer 下限）与 F4 不同关注面，自动合并共存。
- **#1062 闭环**：Phase 1/2（#1065 / #1067）已上游合入（2026-08-11），经本轮对齐继承；§2.4 清空、§2.2 登记。
- 验证基线：memory **329/2**（+8 为上游新测试）、ClickHouse 全套（F5+语义层 10 文件）**137/11**、schema 门禁 **54 passed + comments gate exit 0**、README+manifest 门禁 **54 passed**、env gate **exit 0**、MCP **OFF=73 / ON=78**、loader 覆盖 pin **8 passed**（A 股 chain 链首仍 clickhouse）。

### 2026-08-17 OpencodeAgent harness 引入（F7）

- 独立仓库 `vibetrading-opencode-instruct`（opencode + omo + VT MCP 的 A 股量化研究 harness，Docker 镜像 `opencode-serve`）整体引入 `OpencodeAgent/` 目录管理，成为 mymain 的个人部署能力（F7，不回流社区）。
- **引入前的迁移改造**（在 instruct 仓库内完成）：
  - scripts 库瘦身：删除与 VT 重复的 `backtest/`（VT 回测引擎替代，22 个自研信号构建器迁入 `vibe_bridge/signal_builders/` 经 VT `generate(data_map)` 契约继续可用）、`chanlun/`（VT chanlun skill 替代）、`memory/`（VT F1–F4 替代）、`experiment/`（占位脚手架移除）。
  - 数据层统一：`microstructure/`（逃顶信号 ~40 文件）与 `screening/`（三层选股）从 DuckDB 迁至 VT `clickhouse_connector`（ashare 库同构 tushare schema，SQL 方言 DuckDB→ClickHouse）；`realtime/quote_adapter` 改走 VT `market_data` 联邦（移除盘中成交量外推）；保留 7 门验证框架、单位换算知识、集成判定等独有方法论。
  - 补齐 AGENTS.md 已引用但缺失的 `escape-top-microstructure` skill。
  - harness AGENTS.md 重写（605 行）：新增**问题处理协议**（明确可执行/开放型/待澄清型/宏观型四类分流；开放型走 Least-to-Most 六维收敛漏斗、待澄清型走槽位澄清（工具先行、只问用户私有槽位）、宏观型走 Step-Back 拆分（可解/不可解二分 + 代理问题转译菜单，方法论约束 MacKinlay 1997 / Kothari & Warner 2007）；硬性轮次预算：每意图 1 轮 ≤3 问、绝不复读第二轮）与**防幻觉诚实拒答纪律**（数字溯源三来源、LLM 禁做数学、弃权一等公民、五要素拒答模板、不过度承诺）。调研依据：OpenBB 子问题路由、TradingAgents 角色辩论、ai-hedge-fund 失败契约、Anthropic/ClariQ/Qulac/Abstain-R1 澄清与弃权文献。
  - 打包适配 mymain：工具计数 59→73/78、CLICKHOUSE_LLM_* 语义层凭据贯通（.env.example / opencode.json.tmpl / entrypoint ctx）、vendoring 重构为 `git archive`（杜绝开发残留混入 vendor）、镜像 tag v2.1.0-mymain。
- **路径适配**：`OpencodeAgent/build.sh` 的 `VT_SOURCE` 缺省改为 `..`（仓库根），vendoring 时排除 `OpencodeAgent/` 自身防递归；`deploy/ecs-build.sh` 改为单仓库流程（clone Vibe-Trading mymain → `OpencodeAgent/` 内构建）。
- 原独立仓库保留存档，后续以 `OpencodeAgent/` 为准。

### 2026-08-21 OpencodeAgent 接线优化（工具治理 / 上下文瘦身 / 模型统一）

基于 harness 工程调研（MCP 工具面经济学：工具选择准确率在 25-30 个可见工具后退化、~100 个崩塌；schema 披露税每规划轮重付；Anthropic/OpenBB/Harness 的收敛解 = 常驻动词最小化 + 按需激活 + 技能渐进披露）对 F7 接线层做首轮优化，全部改动限于 `OpencodeAgent/`：

- **O1 工具治理清单落地**：`config/vibe-trading-tools.json` 此前被 COPY 进镜像但无消费者（opencode 不读该文件）。新增 `config/render_config.py`（entrypoint 渲染逻辑抽出为单一事实源，含 24 项测试）：启动时把清单 `disabled` 列表编译为 opencode `permission` deny 项（键格式 `vibe-trading_<glob>`），被 deny 工具不进入模型可见工具列表。当前策略 `trading_*`——容器为纯研究部署、无 broker connector，8 个只读 trading_* 工具只有 schema 成本无能力收益。
- **O2 按 agent 裁剪工具面**：`opencode.json.tmpl` 为 `explore` / `multimodal-looker` 两个非金融取数 agent deny `vibe-trading_*` 与 `search_mcp_*`。
- **O3 AGENTS.md 瘦身（605→388 行）**：场景 A/B/B2/C/D/E/F 逐步 playbook 与 html-report 细节迁入新技能 `skills/research-scenarios/`（按需加载）；AGENTS.md 保留场景路由表 + 强制"识别场景后先加载 research-scenarios 技能"规则；VT 记忆机制文档收敛为使用规则。常驻纪律层（问题处理协议/防幻觉/回测方法论/风险硬约束）不动。测试设 450 行护栏防回涨。
- **O4 模型统一**：oh-my-openagent.json 全部 agents/categories 统一 `alibaba-cn/qwen3.8-max`（多模态，multimodal-looker 无需降级）；opencode.json.tmpl 默认模型、entrypoint fallback、`.env.example` LANGCHAIN_MODEL_NAME、IMAGE-MANUAL 同步。
- **O5 编排纪律**：AGENTS.md OMO 节新增「编排单通道规则」——VT swarm 与 OMO 子代理两通道不得嵌套（token 成本按层复合 + 电话效应）；上下文压缩后必须 re-grounding（重取 research goal / analysis 持久化状态）再继续。
- 验证：`OpencodeAgent/tests/test_config_render.py` **24 passed**（模板渲染 / 清单编译 / agent scoping / 模型统一 / AGENTS.md 预算 / 技能契约）；nano-search-mcp 回归 **193 passed**；渲染样例人工核对（permission 块 + agent 块 + qwen3.8-max 正确）。

### 2026-08-21 merge 对齐（基线 `1907e47d`）

- 上游前进 183 commit（0713336c → 1907e47d）：**v0.1.14 发布**（VietnamEquity 第 10 引擎、Strategy Discovery 证据门控 4 工具、Run Detail 因子研究/持仓/tearsheet 面板、Options Lab、Intel Mac 安装修复）、quantlib 265→286 函数（CDS/固定收益批次）、agent loop 加固（stall watchdog、确定性工具结果缓存、compaction 验证账本、identity gate 与缓存顺序修复）、swarm 失败/取消运行重放（保留已完成任务产物）、tencent loader 截断窗口 fail-closed、`test_readme_counts.py` 六 README 计数 pin 测试套件强化。采用**直接 merge**（增量轮次）。
- **冲突面（8 文件）**：5 份 README + `agent/SKILL.md` + `agent/mcp_server.py` + `agent/src/market_data.py`，全部为计数同步与结构重构叠加，逐一解决：
  - **MCP 计数统一**：上游 70→74（+4 strategy discovery 工具），叠加本地 3 个 ch_* 语义层工具 = **77**（`VT_MEMORY_MCP_TOOLS=1` 时 +5 memory 工具 = **82**）；六份 README 工具清单、 prose 计数、repo-tree 注释与 `mcp_server.py` docstring 全部同步；README_es 工具清单补齐 ch_*（pin 测试带来的新义务，此前 es 清单沿上游不含本地工具）。
  - **技能计数**：上游 90（+strategy-discovery）+ 本地 memory-lifecycle = **91**；六份 README 的 badge/bullet/OpenSpace 段/repo-tree 注释共 36 处同步。
  - **引擎/数据源**：采纳上游 10 引擎（VietnamEquity）；数据源 26（上游 25 + clickhouse，SKILL.md 口径）。
  - **market_data.py**：采纳上游 `_emit()` 助手重构，本地 ClickHouse provenance 富集经其 `extra_provenance` 参数保留（语义不变）。
- **既有分歧修复（发现于本轮验证）**：`test_market_data.py::test_detect_source` 与 `test_registry.py::test_chains_ordered_by_ip_ban_risk` 自 F5 落地起与本地 clickhouse-first 路由不一致（pin 的是上游 tencent-first 口径），本轮修正为 pin 本地设计（A 股检测 = clickhouse；a_share chain 以 clickhouse 领头、后接原审核序），并加 mymain divergence 注释防 rebase 误回退。
- **取代核查**：F1–F5、语义层、F7 均未被上游取代——上游对 memory（reflections/mcp_adapter/memory_guard）、clickhouse（connector/loader/语义层工具）、OpencodeAgent 零触碰；上游 Strategy Discovery 是新增能力面（Alpha Zoo + SDM 证据门控），与本地能力无重叠；agent loop/swarm 的上游加固与本地改动（grounding、记忆钩子）不同关注面，自动合并共存。
- **计数更新**：MCP 73/78 → **77/82**；skills 90 → **91**；数据源维持 26（SKILL.md 口径，README 沿用"23 free + 可选 key 源"上游表述不变）；引擎 9 → **10**。
- 验证基线：MCP 运行时计数 **77**（memory OFF，ch_* 与 strategy discovery 全在）；`test_readme_counts.py` **57 passed**（六 README 与代码计数全对齐）；memory 套件 **152 passed / 2 skipped**；合并影响面聚焦扫描（agent_loop / swarm / clickhouse / get_market_data / backtest_tool）**554 passed / 15 skipped**；`test_market_data.py` + `test_registry.py` **80 passed**。

### 2026-08-28 rebase 对齐（基线 `80ffdda4`）

- 上游前进 117 commit（1907e47d → 80ffdda4）：live 交易安全大批次（Alpaca 订单全生命周期归属/恢复、kill-switch sweep 跨重启持久化、券商错误包络 fail-closed）、**数据源优先级覆盖机制**（`MARKET_DATA_ORDER_*` env + Settings 页卡片 + 热应用，#1231）、Portfolio 多券商持仓只读面板、Binance USD-M 只读对账、swarm 取消/重试 backoff、`get_market_data` 入参校验与 registry 驱动 allow-list、memory FTS GC 清理、forex metals 定价修复等。
- **本轮改用 rebase**（§4.1 第一种做法，用户指定）：22 个本地非 merge commit 逐一重放到新基线。冲突面 4 处——F2 与 Phase 2 的六 README + SKILL.md + mcp_server.py 计数区（按「上游内容 + 本 commit 增量」逐层解决：74→77/82、90→91、25→26 源、9→10 引擎）、F5 的 SKILL.md 计数/表述、Phase 1 的 market_data.py（继续沿用上轮方案：`_emit()` 助手 + `extra_provenance` 承载 CH provenance）。
- **rebase 固有代价——merge commit 解法会丢失**：上轮 merge 分辨率（test_market_data/test_registry 的 clickhouse-first pin、README_es 的 ch_* 清单、SKILL.md/mcp_server.py 计数）全部以 reconciliation commit `8a05a7c1` 重新落地，并补齐本轮新增上游测试的本地适配（`test_source_order_overrides.py` / `test_settings_api.py` 的 a_share override 必须是含 clickhouse 的全排列）。
- **上游新机制与 F5 的关系（互补，非取代）**：`MARKET_DATA_ORDER_A_SHARE` 覆盖机制要求值是**本地默认链的全排列**——本地默认链以 clickhouse 领头后，该机制在其上正常工作（快照 `_DEFAULT_CHAINS` 取自修改后的 FALLBACK_CHAINS）；Settings 页「数据源优先级」卡片随之对 A 股显示 clickhouse-first 顺序，用户可自助调整而无需改代码。这正是「mymain 能力以配置形式注入上游机制」的落地路径。
- **取代核查**：F1–F7 均未被上游取代——上游对 reflections/mcp_adapter/memory_guard、clickhouse 全家、OpencodeAgent 零触碰；本轮上游无 shadowinlife PR 合入（无分歧消除项）；上游 memory FTS GC 修复（#1174）作用于 lifecycle/persistent，与 F4 不同关注面，自动共存。
- **环境注意**：`legonanobot` 环境缺 `sqlglot`（ch_* 工具的 AST 守卫依赖，pyproject 已声明）会导致 3 个 ch_* 工具静默缺席、MCP 计数塌缩到 74/79——本轮验证前已 `pip install sqlglot>=30`；上游 #1129 的「工具模块导入失败具名报错」让该问题在日志可见。
- 验证基线：memory **309/3**、ClickHouse **137/11**、schema 门禁 **53 passed / 1 skipped + comments gate exit 0**、README+manifest 门禁 **70 passed**（含六 README 的 MCP 工具清单集合级校验）、env gate **exit 0**、MCP **OFF=77 / ON=82**、market_data/registry/source_order/settings_api **132 passed**、OpencodeAgent config render **24 passed**。
- 已知瑕疵（**2026-08-30 已关闭**）：F2/Phase 2 两个中间 commit 分别混入一个 `.omo` 会话文件与两处冲突标记文本（rebase 冲突解决期间的 `git add -A` 事故），最终树已干净；社区 PR carve 从最终树提取，不受影响。→ 2026-08-30 rebase 经 edit 停点从源头 commit 移除，历史中亦不再存在。

### 2026-08-30 rebase（基线 `fb5013c2`）

- 上游前进 79 commit（`80ffdda4` → `fb5013c2`）：UK LSE 股权（`.L` 检测 / SDRT 0.5% 买方印花税 / GBp→GBP loader 归一 / 财报 Yahoo 路由）、quantlib 微结构批次（VPIN / Roll / Amihud / Kyle）+ Heston / copula / HRP、live halt-sweep 跨重启持久化与 episode 绑定、backtest `warmup_bars` / `evaluation_start_date` 数据窗与评估窗分离、order-plan 拒绝经 `_on_plan_rejected` 上浮、connector onboarding 契约 + keyring 凭据、provider stream retry 升级与 Retry-After、前端 Studio 路由与 Run Detail 滚动等。
- **本轮继续 rebase**（§4.1 第一种做法）：34 个本地 commit 重放。设 2 个 edit 停点做历史卫生：F2 移除误入树的 `.omo/run-continuation/*.json` 会话文件（此前一直在树中）；Phase 2 移除 SKILL.md / mcp_server.py 两处冲突标记文本（原由 reconciliation commit 兜底，现前置到源头 commit）。真冲突仅 1 处：reconciliation commit 与上游 uk_equity 测试集在 `test_settings_api.py` 叠加，解决为 clickhouse-first pin 与 uk_equity 断言并存；`test_registry.py` 自动合并（uk_equity 集合 + clickhouse 链 pin 各就各位）。
- **取代核查**：F1–F7 均未被上游取代——上游对 memory（reflections / mcp_adapter / memory_guard / persistent）、clickhouse 全家、OpencodeAgent、schema/clickhouse 零触碰；本轮无 shadowinlife PR 合入（无分歧消除项）。
- **分歧收敛审查**：quantlib 新增微结构函数（VPIN / Roll / Amihud / Kyle）与 OpencodeAgent escape-top 微观结构信号**不重叠**（escape-top 代码 grep 零命中 amihud/vpin/kyle/roll，无重实现可去重）；上游 market_data provenance 新增 `currency_conversion` / `quote_currency` 字段，与本地 CH `extra_provenance` 通道互补共存；UK `.L` 源模式与本地 a_share clickhouse 检测各居 `_SOURCE_PATTERNS` 不同行。结论：本轮无可移除的重复实现，分歧面保持 F1–F7 纯增量。
- 验证基线：memory **309/3**、ClickHouse **137/11**、schema 门禁 **53/1 + comments gate exit 0**、README+manifest 门禁 **76 passed**（上游 +6 pin）、env gate **exit 0**（3 条 WARN 来自上游 llm.py）、market_data/registry/source_order/settings_api **133 passed**（+1 上游 uk chain 测试）、OpencodeAgent config render **33 passed**、MCP **OFF=77 / ON=82**；自维护链路冒烟（memory_save/recall/status/reflect + `VT_MEMORY_BASE_DIR` 落盘 lessons JSONL）全绿。
