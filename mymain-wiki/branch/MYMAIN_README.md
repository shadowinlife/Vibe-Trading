---
title: MYMAIN 发布记录（Release Log）
description: mymain 分支每次对齐/发布的 changelog——上游基线、差异总量、核心迭代、验证基线。发布或追溯历史版本时读。触发词：发布、release、tag、release/mymain、对齐。
type: reference
status: active
created: 2026-08-11
updated: 2026-08-30
tags: [branch, release, changelog]
related: [MYMAIN_DIVERGENCE.md]
---

# MYMAIN 发布记录（Release Log）

> `mymain` 是开源社区 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) `main` 的独立部署分支
> （分支定位与开发约定见 `MYMAIN_DIVERGENCE.md` 与根目录 `AGENTS.md` §8）。
>
> 本文档是 `mymain` 的**发布 changelog**：每次对齐 / 修正后的发布都会在这里记录
> 该版本相对上游的**核心差异**与本次发布的**核心迭代**。

## Tag 约定

- 发布 tag：**`release/mymain`** —— 移动 tag，始终指向 `mymain` 上最新一次发布 commit。
- 更新流程（每次发布完成后）：

  ```bash
  git tag -f release/mymain <release-commit>
  git push fork mymain
  git push -f fork release/mymain
  ```

- 历史不丢失：下表逐条永久记录每次发布的 commit SHA、上游基线与核心变更；
  移动 tag 前，先把 `git rev-parse release/mymain` 的 SHA 回填进上一条记录的
  「发布 commit」字段，即可随时 `git diff <sha>` 追溯任意历史版本。

## 发布记录（新 → 旧）

---

### release/mymain · 2026-08-30 — 基线：上游 `fb5013c2`

- **发布 commit**：`release/mymain` tag 所指 commit（即本条发布记录 commit）
- **上游基线**：`fb5013c2`（v0.1.14 后第 79 commit；本轮对齐覆盖 `80ffdda4` 之后的全部上游 commit）
- **差异总量**：626 个文件、+118,306/−140 行（复核命令 `git diff origin/main release/mymain --shortstat`；较上轮大增系 `mymain-wiki/` 知识库归档入库）
- **对齐方式**：**rebase**（34 个本地 commit 重放；回退点 `backup/mymain-2026-08-30`）

#### 核心差异（F1–F7 不变，均未被上游取代）

详见 `MYMAIN_DIVERGENCE.md` §2.1。本轮取代核查结论：上游 79 commit（UK LSE 股权支持、quantlib 微结构/Heston/copula/HRP 批次、live halt-sweep 持久化、backtest 数据窗/评估窗分离、connector onboarding 契约、stream retry 升级、前端 Studio 路由等）与本地七项能力**均无重叠**；本轮无 shadowinlife PR 合入（无分歧消除项）。分歧收敛审查：quantlib 微结构函数与 OpencodeAgent escape-top 不重叠（无重实现可去重）；上游 provenance `currency_conversion`/`quote_currency` 与本地 CH `extra_provenance` 互补共存。

#### 核心迭代（本次发布完成的工作）

1. **rebase 对齐**：34 个本地 commit 重放，1 处真冲突（reconciliation commit × 上游 uk_equity 测试集，解决为 clickhouse-first pin 与 uk_equity 并存）。
2. **历史卫生**：2 个 edit 停点从源头清理上轮记录的已知瑕疵——F2 commit 移除误入树的 `.omo` 会话文件；Phase 2 commit 移除 SKILL.md / mcp_server.py 两处冲突标记文本。
3. **计数基线不变**：MCP **OFF=77 / ON=82**、skills **91**、数据源 **26**、引擎 **10**。
4. **文档同步**：`MYMAIN_DIVERGENCE.md` 更新至新基线（含本轮迭代笔记与分歧收敛审查）；回填上一条记录的发布 commit SHA。

#### 验证基线（`legonanobot` 环境，全部通过）

| 门禁 | 结果 |
|------|------|
| memory 套件 | 309 passed / 3 skipped |
| ClickHouse 套件（F5 + 语义层，10 文件） | 137 passed / 11 skipped |
| ClickHouse schema 门禁 | 53 passed / 1 skipped + comments gate exit 0 |
| README / SKILL 计数门禁（六 README 集合级校验） | 76 passed（上游新增 6 条 pin，含 quantlib badge 模块数双锚定） |
| env-var AST 门禁 | exit 0（3 条 WARN 来自上游 llm.py） |
| market_data / registry / source_order / settings_api | 133 passed |
| OpencodeAgent config render | 33 passed |
| MCP 工具计数 | OFF=77 / ON=82 |
| 自维护链路冒烟 | memory_save/recall/status → ok；reflect 关闭=skipped、开启=ok 并落 lessons JSONL（`VT_MEMORY_BASE_DIR` 生效） |
| 提交规范 | 单一作者 shadowinlife + DCO 签名 + 无 AI 归属行 |

---

### release/mymain · 2026-08-28 — 基线：上游 `80ffdda4`

- **发布 commit**：`fc41c949`（2026-08-30 rebase 前 SHA；本轮对齐后该 commit 已被重写。注：08-28 发布时 tag 未随之移动，本次按发布时分支头回填）
- **上游基线**：`80ffdda4`（v0.1.14 后第 117 commit；本轮对齐覆盖 `1907e47d` 之后的全部上游 commit）
- **差异总量**：282 个文件、+49,714/−140 行（复核命令 `git diff origin/main release/mymain --shortstat`）
- **对齐方式**：**rebase**（22 个本地 commit 重放；回退点 `backup/mymain-2026-08-28`）

#### 核心差异（F1–F7 不变，均未被上游取代）

详见 `MYMAIN_DIVERGENCE.md` §2.1。本轮取代核查结论：上游 117 commit（live 交易安全批次、`MARKET_DATA_ORDER_*` 数据源优先级覆盖、Portfolio 只读面板、Binance USD-M 对账、swarm 取消/重试等）与本地七项能力**均无重叠**；其中数据源优先级覆盖机制与 F5 **互补**——其 override 校验基于本地默认链快照，A 股 clickhouse-first 顺序自动成为被重排的基准。

#### 核心迭代（本次发布完成的工作）

1. **rebase 对齐**：4 处真冲突（F2/Phase 2 的六 README + SKILL.md + mcp_server.py 计数区；F5 的 SKILL.md 表述；Phase 1 的 market_data.py `_emit` 重构叠加），按「上游内容 + 本地增量」逐一解决。
2. **merge 解法回收**：上轮 merge commit 承载的解法（clickhouse-first 测试 pin、README_es ch_* 清单、MCP 计数）经 reconciliation commit 重新落地；新增上游测试（`test_source_order_overrides.py` / `test_settings_api.py` / `test_market_data.py` 的 override 系列）适配本地链全排列约束。
3. **计数基线**：MCP **OFF=77 / ON=82**、skills **91**、数据源 **26**（SKILL.md 口径）、引擎 **10**、agent 工具 **111**。
4. **文档同步**：`MYMAIN_DIVERGENCE.md` 更新至新基线（含本轮迭代笔记）；回填上一条记录的发布 commit SHA。

#### 验证基线（`legonanobot` 环境，全部通过）

| 门禁 | 结果 |
|------|------|
| memory 套件 | 309 passed / 3 skipped |
| ClickHouse 套件（F5 + 语义层，10 文件） | 137 passed / 11 skipped |
| ClickHouse schema 门禁 | 53 passed / 1 skipped + comments gate exit 0 |
| README / SKILL 计数门禁（六 README 集合级校验） | 70 passed |
| env-var AST 门禁 | exit 0 |
| market_data / registry / source_order / settings_api | 132 passed |
| OpencodeAgent config render | 24 passed |
| MCP 工具计数 | OFF=77 / ON=82 |
| 提交规范 | 单一作者 shadowinlife + DCO 签名 + 无 AI 归属行 |

---

### release/mymain · 2026-08-17（第二次）— 基线：上游 `0713336c` + OpencodeAgent（F7）

- **发布 commit**：`8b89d1b3`（rebase 前 SHA；2026-08-28 对齐后该 commit 已被重写）
- **上游基线**：`0713336c`（与上次发布相同；本次为本地 harness 层引入，无上游变更）
- **差异总量**：266 个文件、+47,520/−105 行（复核命令 `git diff 0713336c release/mymain --shortstat`）

#### 核心差异（F1–F6 基础上新增 F7，均个人部署独有、不回流）

| # | 能力 | Commit | 核心内容 | 规模 |
|---|------|--------|----------|------|
| F7 | OpencodeAgent harness 层 | `35bb27a1` | opencode + omo + 本仓库 MCP 的独立部署 harness（Docker 镜像 `opencode-serve`）：问题处理协议（明确/开放/待澄清/宏观四类分流，Least-to-Most 漏斗 + Step-Back 拆分 + 单轮 ≤3 问轮次预算）、防幻觉与诚实拒答纪律（数字溯源三来源、弃权一等公民、五要素拒答模板）、escape-top 微观结构信号（CH 数据层 + 7 门验证）、三层选股、VT 联邦行情 scanner、cron + 钉钉通知、nano-search-mcp（12 工具） | 144 文件 +34,498 |

> F7 源自独立仓库 `shadowinlife/vibetrading-opencode-instruct`（最终态
> `ac2d92f` + `1687097`，已存档），2026-08-17 整体引入 `OpencodeAgent/` 管理。

#### 核心迭代（本次发布完成的工作）

1. **scripts 库迁移**：删除与 VT 重复的 backtest/（VT 回测引擎替代，22 个自研信号构建器
   迁入 vibe_bridge/）、chanlun/（VT chanlun skill）、memory/（VT F1–F4）、experiment/；
   microstructure（~40 文件）/ screening / realtime 数据层从 DuckDB 迁至 VT
   clickhouse_connector 与 market_data 联邦（含 DuckDB→ClickHouse SQL 方言转换与
   优雅降级契约）；7 门验证框架、单位换算知识等独有方法论完整保留。
2. **AGENTS.md 重写**（605 行）：新增问题处理协议与防幻觉诚实拒答纪律两个 CRITICAL
   章节；场景 A–F 重构（C 重写、F 新增宏观/事件驱动）；能力索引对齐 mymain。
3. **补齐 escape-top-microstructure skill**（AGENTS.md 原已引用但缺失）。
4. **打包适配 mymain**：工具计数 59→73/78、CLICKHOUSE_LLM_* 语义层凭据贯通、
   vendoring 重构为 git archive（杜绝开发残留）、单仓 ECS 构建流程、tag v2.1.0-mymain。
5. **文档登记**：`MYMAIN_DIVERGENCE.md` §2.1 F7 行 + §5 引入笔记；根 AGENTS.md §8.2
   harness 层条目。

#### 验证基线（`legonanobot` 环境，全部通过）

| 门禁 | 结果 |
|------|------|
| OpencodeAgent scripts compileall | 通过（microstructure 零 duckdb 残留） |
| CLI 冒烟（escape_top/concentration/margin_buy_vs_sse/joint_escape_top --help） | 全部通过 |
| 优雅降级（CLICKHOUSE 不可达 → `{"available": false}` + exit 0） | 通过 |
| shell 语法（build.sh / ecs-build.sh / entrypoint.sh） | 通过 |
| 上次发布的 VT 门禁（memory 329/2、CH 137/11、计数 54、env gate、MCP 73/78） | 沿用通过（本次未改 VT 代码） |

---

### release/mymain · 2026-08-17 — 基线：上游 `0713336c`

- **发布 commit**：`57bf9563`
- **上游基线**：`0713336c`（v0.1.13 后第 158 commit；本次对齐覆盖 `1bf1d8b4` 之后的 144 个上游 commit）
- **差异总量**：124 个文件、+12,981/−105 行（含本文档与 `MYMAIN_DIVERGENCE.md`、`AGENTS.md` 等分支级文档及 ClickHouse 语义层 Phase 0–2；复核命令 `git diff 0713336c release/mymain --shortstat`）

#### 核心差异（相对上游基线的 6 项本地能力，均未被上游取代）

| # | 能力 | Commit | 核心内容 | 规模 |
|---|------|--------|----------|------|
| F1 | 反思课程存储 | `c4aa2774` | JSONL append-only 反思存储（`reflections.py`）+ `VT_MEMORY_REFLECTIONS` 等特性开关 | 5 文件 +643 |
| F2 | MCP 记忆工具 | `c0909374` | 记忆生命周期 5 个工具经 MCP 暴露（`mcp_adapter.py`）+ `memory-lifecycle` SKILL 文档 + 多语言 README 更新 | 11 文件 +870/−42 |
| F3 | 回测自动反思钩子 | `7291d2d0` | 回测完成后自动触发 memory_save + memory_reflect（`backtest_tool.py`）+ 并发 / 延迟性能测试 | 6 文件 +413 |
| F4 | MemoryGuard + 存储路径 | `c459eebb` | FastMCP middleware 自动触发 save/reflect（`memory_guard.py`）；`VT_MEMORY_BASE_DIR` 支持项目级存储 | 5 文件 +229/−6 |
| F5 | ClickHouse A 股数据源 | `0fc4a455` | ClickHouse 作为 A 股主力数据源（T-1 历史 199 列 + 网络源联邦当日 OHLCV），覆盖资金流 / 龙虎榜 / 融资融券 / 北向 4 类 flow 工具 | 16 文件 +2049/−6 |
| F6 | ClickHouse 语义层 Phase 0–2 | `0620c448`→`3305e8ff`（fork PR #1 `e4ba22df`） | 56 表 DDL 快照 + 9 表 444 列 COMMENT + 单位 registry（`clickhouse_units.py`）+ 显式 199 列消除 SELECT * + `get_valuation` + llm_role 受约束灵活性通道（`ch_list_tables` / `ch_describe_table` / `ch_query`，sqlglot AST 守卫） | 97 文件 +8421/−73 |

> F1–F6 的上游取代核查结论（2026-08-17）：上游本轮新增（tickerall 数据源、Options Lab、
> tearsheet、factor research panel、Copilot / Novita provider、桌面端加固等）与上述能力
> **均无重叠**，全部保留；上游对 F1–F4 核心文件零触碰、MCP 面零新增工具注册。
> 详见 `MYMAIN_DIVERGENCE.md` §2。

#### 核心迭代（本次发布完成的工作）

1. **上游对齐**：merge `1bf1d8b4` 之后的 144 个 commit，仅 1 处真冲突
   （`agent/SKILL.md` 计数/表述区 2 块：两侧各自 24→25 sources——本地 clickhouse vs 上游 tickerall），
   合并解决为 26 sources + 90 skills，保留上游 tickerall explicit-only 表述。
2. **fork 语义层回合**：fork/mymain 上的语义层 Phase 0–2（2026-08-12 经 fork PR #1 合入，
   本地未拉回）合并回本地，仅 1 处真冲突（`agent/src/market_data.py` provenance 块：
   上游 #1065 的 `volume_unit` × fork 的 `entry` 变量重构），两者合并保留。
3. **新 README 同步**：上游本轮新增第六份 README（README_es.md），同步 skills 89→90、Tool 类 10→11。
4. **计数基线更新**：数据源 25→26（clickhouse + tickerall）；MCP 工具 70/75→73/78
   （+3 为语义层 ch_* 工具，非上游新增）；skills 维持 90。
5. **#1062 闭环**：A 股 volume 单位不一致经 shadowinlife PR #1065 / #1067 上游合入
   （2026-08-11），本次对齐继承；`MYMAIN_DIVERGENCE.md` §2.4 清空、§2.2 登记。
6. **文档同步**：`MYMAIN_DIVERGENCE.md` 全面更新至新基线；回填上一条记录的发布 commit SHA。

#### 验证基线（`legonanobot` 环境，全部通过）

| 门禁 | 结果 |
|------|------|
| memory 套件 | 329 passed / 2 skipped |
| ClickHouse 套件（F5 + 语义层，10 文件） | 137 passed / 11 skipped |
| ClickHouse schema 门禁（3 个 tools 单测 + comments gate） | 54 passed / exit 0 |
| README / SKILL 计数门禁 | 54 passed |
| env-var AST 门禁（`tools/ci_env_var_gate.py`） | exit 0 |
| MCP 工具计数 | OFF=73 / ON=78 |
| 提交规范 | 单一作者 shadowinlife + DCO 签名 + 无 AI 归属行 |

---

### release/mymain · 2026-08-11 — 基线：上游 v0.1.13（`c33133f4`）

- **发布 commit**：`9217c701`
- **上游基线**：`c33133f4`（v0.1.13；本次对齐覆盖 `6c44732` 之后的 120 个上游 commit）
- **差异总量**：39 个文件、+4,487/−53 行（含本文档与 `MYMAIN_DIVERGENCE.md`、`AGENTS.md` 等分支级文档；复核命令 `git diff c33133f4 release/mymain --shortstat`）

#### 核心差异（相对上游 v0.1.13 的 5 项本地能力，均未被上游取代）

| # | 能力 | Commit | 核心内容 | 规模 |
|---|------|--------|----------|------|
| F1 | 反思课程存储 | `c4aa2774` | JSONL append-only 反思存储（`reflections.py`）+ `VT_MEMORY_REFLECTIONS` 等特性开关 | 5 文件 +643 |
| F2 | MCP 记忆工具 | `c0909374` | 记忆生命周期 5 个工具经 MCP 暴露（`mcp_adapter.py`）+ `memory-lifecycle` SKILL 文档 + 5 语言 README 更新 | 11 文件 +870/−42 |
| F3 | 回测自动反思钩子 | `7291d2d0` | 回测完成后自动触发 memory_save + memory_reflect（`backtest_tool.py`）+ 并发 / 延迟性能测试 | 6 文件 +413 |
| F4 | MemoryGuard + 存储路径 | `c459eebb` | FastMCP middleware 自动触发 save/reflect（`memory_guard.py`）；`VT_MEMORY_BASE_DIR` 支持项目级存储 | 5 文件 +229/−6 |
| F5 | ClickHouse A 股数据源 | `0fc4a455` | ClickHouse 作为 A 股主力数据源（T-1 历史 199 列 + 网络源联邦当日 OHLCV），覆盖资金流 / 龙虎榜 / 融资融券 / 北向 4 类 flow 工具 | 16 文件 +2049/−6 |

> F1–F5 的上游取代核查结论（2026-08-11）：上游本轮新增（quantlib_call、alpha zoo MCP、
> 机构数据、加拿大市场、eToro、桌面端等）与上述能力**均无重叠**，全部保留。
> 详见 `MYMAIN_DIVERGENCE.md` §2。

#### 核心迭代（本次发布完成的工作）

1. **上游对齐**：merge 上游 `6c44732` 之后的 120 个 commit（v0.1.13），仅 2 处真冲突
   （`agent/SKILL.md` 计数区、`agent/mcp_server.py` 工具数头注释），均已解决。
2. **历史重整**：原 11 个交错 commit 经 merge + carve 重整为 **6 个单一功能 commit**
   （F1→F2→F3→F4→F5→docs），carve 树与 merge 树逐字节一致；每个 commit 可独立作为社区 PR 候选。
3. **计数基线同步**：MCP 工具 62→70（`VT_MEMORY_MCP_TOOLS=1` 时 75）、skills 89→90、数据源 24→25。
4. **文档同步**：`MYMAIN_DIVERGENCE.md` 全面重写至新基线；新增本文档作为发布追踪入口。

#### 验证基线（`legonanobot` 环境，全部通过）

| 门禁 | 结果 |
|------|------|
| memory 套件 | 321 passed / 2 skipped |
| ClickHouse 套件 | 13 passed / 8 skipped |
| README / SKILL 计数门禁 | 54 passed |
| env-var AST 门禁（`tools/ci_env_var_gate.py`） | exit 0 |
| MCP 工具计数 | OFF=70 / ON=75 |
| 提交规范 | 单一作者 shadowinlife + DCO 签名 + 无 AI 归属行 |

---

## 附录：发布检查清单

每次发布打 tag 前必须完成：

1. [ ] merge / rebase 上游 `main` 并解决冲突
2. [ ] 取代核查：确认本地能力未被上游取代，更新 `MYMAIN_DIVERGENCE.md`
3. [ ] `MYMAIN_DIVERGENCE.md` §3.1 全部测试门禁通过
4. [ ] 提交规范：单一作者 `shadowinlife` + `git commit -s` DCO + 无 AI 归属行
5. [ ] 本文档新增一条发布记录（核心差异 + 核心迭代），并回填上一条记录的发布 commit SHA
6. [ ] `git tag -f release/mymain <commit>` + 推送分支与 tag
