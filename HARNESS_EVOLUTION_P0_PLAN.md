# HARNESS 演进 — P0 波次执行计划（Wave 1 任务分拆）

> 维护者：opencode (Sisyphus) ｜ 初版：2026-08-26 ｜ 状态：**待人工审批**
> 上游文档：`HARNESS_EVOLUTION_ROADMAP.md`（§2 总表 / §3 详情卡 / §5 波次）· `HARNESS_EVOLUTION_CAPABILITY_AUDIT.md`（K/G/Q 编号）
> 本文定位：ROADMAP §0.2 约定的分拆产物——将 8 个 P0 PLAN（A1-A6、E1、F1）分拆为**可执行、可量化测试、有证据产物**的 TASK，附代码勘察发现与待拍板决策点。**本文审批通过后方开始执行。**

---

## 1. 执行前代码勘察发现（对 AUDIT 假设的校正）

分拆前对仓库做了源码级勘察，以下 5 条事实与 AUDIT 假设存在偏差，直接影响任务设计：

| # | 发现 | 影响 |
|---|---|---|
| S1 | **`.opencode/skills` 在本仓库是符号链接** → `../agent/src/skills`（`ls -la` 证实），非"字节级分发副本"；`.opencode/` 整体不入 git | K18/F3 的"副本漂移"问题在**本仓库不存在**（symlink 天然同步）；漂移仅可能发生在 pip/ClawHub 分发产物中。A5 决策仍需要（双暴露是运行时事实），但 F3 紧迫度下降 |
| S2 | **`read_file`/`write_file` 的描述两表面不共用**：MCP 面描述在 `agent/mcp_server.py` 的 wrapper docstring（L1149、L1162），AGENT 面在工具类（`read_file_tool.py:22`、`write_file_tool.py:18`）。二者**不同文** | A3 必须同时改两处（与 HEAD commit 43b3f485 对 trading_* 的"双面镜像"做法一致）。AUDIT §1.1"工具类描述 → 双面生效"仅对镜像注册工具成立 |
| S3 | **镜像注册机制**（`_MIRRORED_TOOL_SOURCES`，mcp_server.py:2391）：`sentiment`、`quantlib_call` 等 10 工具直接复用工具类 JSON Schema | A1/A4 只改工具类 description 即双面生效 ✓ |
| S4 | **Q10/A7④ 已提前完成**：HEAD commit `43b3f485`（fix(trading_* entry-order hints)）已实现 trading_* 入口顺序 + get_market_data 仲裁提示，双面镜像 | P1 项预支；E1 评测集中 trading_* 相关 query 的基线应取该 commit 之后状态 |
| S5 | **评测基建在另一分支**：`agent_eval`（确定性策略/轨迹评测）+ `harness_bench`（tau²/SWE/terminal/FinanceBench/FinEval/BacktestBench 适配器 + `canonical_tool_manifest.json` 82 工具 schema 指纹）位于 `NewAgentMain` 分支（另一 worktree：`/Users/mgong/LegoNanoBot/vibe-trading-harness-evolution`），**不在当前 HEAD**；当前分支 `fix/trading-tool-routing-hints` 的 `agent/src/evals/` 仅剩 `__pycache__` 残片 | E1 落点需要决策（见 §5 决策点 D3）。当前 HEAD 可确认存在的评测相关基建：`agent/src/governance/manifest.py`（prompt hash manifest，E4 前提）与 `test_readme_counts.py` 动态锚点 |

**测试锚点机制确认**（B 批全局验收规则的落点）：`agent/tests/test_readme_counts.py` 为**动态锚点**——MCP 工具枚举/计数、技能计数、9 类 category 表、slash 命令表全部运行时派生并与 6 份 README 对账。任何暴露面或技能数变更会自动触发该测试，无需手工维护钉死值；但 **6 份 README 的正文数字需手工同步**（测试只报不一致，不改文件）。

---

## 2. 依赖图与执行顺序

```
立即并行启动（无依赖）：
  F1（盘点）──────────┐
  E1（评测集）        ├──→ A6（映射表，依赖 F1）
  A1 / A3 / A4（描述） │
  A2（撞名，方案已荐） ┘

阻塞项：
  A5 ←── DEC-1（技能双暴露主面决策，需用户拍板）
```

- **第一优先**：F1 + E1（度量与数据地基，后续一切的裁决依据）与 A1/A3/A4（零风险描述改动）并行；
- **第二优先**：A2（引用面较宽，单独一个 commit 便于回滚）；
- **F1 完成后**：A6；
- **DEC-1 拍板后**：A5 决策成文（本身只是文档，但内容取决于决策）。

分支策略建议（待批，见 §5 D4）：在当前 worktree 从现 HEAD 继续（本分支已承载 A7④），P0 波次每 TASK 一个 commit；E1 若需复用 `NewAgentMain` 的 harness_bench，则 E1 的 runner 部分在该 worktree 落地、评测集资产保持可移植（纯 YAML，两侧通用）。

---

## 3. TASK 卡片

> 验收断言均为可脚本化检查。描述改写遵守 prompt 缓存纪律（RESEARCH §8.0 P0-1）：只改 description 字段文本，不改工具注册顺序、不重排 system prompt。

---

### TASK-F1 · 内部工具面完整盘点（PLAN-F1）

- **目标**：以 `build_registry()` 运行时输出为权威，产出完整内部工具名单，对账 AUDIT §2 的 ~32 个 partial 名单；标注每个内部工具的 MCP 对应物、swarm 引用、技能引用。
- **落点**：新建 `agent/scripts/inventory_internal_tools.py` + 产物 `agent/scripts/artifacts/internal_tool_inventory.json`（+ 人读版 `.md` 表）。
- **实现要点**：
  1. 复用 `test_readme_counts.py::_keyless_agent_tool_count` 的子进程隔离模式（清空 `FRED_API_KEY`/`VIBE_TRADING_IWENCAI_KEY`/`QVERIS_API_KEY`/`VIBE_TW_STOCK_DB` 后子进程跑 `build_registry()`），输出全量工具名 + 工具类 + 模块路径 + description 首句；
  2. 同进程/子进程取 `mcp_server.mcp.list_tools()` 名单（74）；
  3. 差集 = 非 MCP 内部工具；逐个标注：MCP 等价物（AUDIT §8.1 映射 + 人工复核）、swarm preset 引用（grep `agent/src/swarm/presets/*.yaml` 的 `tools:` 白名单）、技能文档引用（grep `agent/src/skills/`）、门控来源（`check_available` / env gate）；
  4. 与 AUDIT §2 内部工具面表逐行对账，差异项逐条写解释。
- **验收**：
  - 脚本可复跑，两次运行产物一致（确定性断言）；
  - 名单覆盖率 = 100%（对运行时 `tool_names`）；
  - 与 AUDIT partial 名单的差异全部有显式记录（新增项/缺失项/门控差异）；
  - 产物含统计行：内部工具总数、有 MCP 对应物数、无对应物数、被 preset 引用数。
- **证据产物**：inventory JSON + MD 表 + 对账差异说明（写入产物文件头部注释）。
- **风险**：`build_registry()` 导入链较重（子进程 300s 超时已有先例，沿用）；taiwan_stock_data 等 env 门控工具需在报告中显式标注"当前 env 未注册"。

---

### TASK-E1 · 工具选择准确率评测集（PLAN-E1）

- **目标**：构造 ≥100 条金融域 query→期望命中对，覆盖 19 域与全部 K/G/Q 仲裁场景；定义评测协议；一键出分、同输入同分数。
- **落点**：
  - 评测集资产：`agent/src/evals/tool_selection/queries.yaml`（版本化资产，schema 见下）；
  - 运行器：`agent/src/evals/tool_selection/run_eval.py`；
  - 测试：`agent/tests/test_tool_selection_eval.py`（资产完整性 + 运行器确定性）。
- **评测集 schema**（每条）：
  ```yaml
  - id: D01-003
    query: "帮我取一下茅台最近一年的日线"      # 用户原话（中英混合覆盖）
    expected: {kind: tool, name: get_market_data}
    domain: D01
    negatives: [trading_quote, trading_history]   # 负向触发（不应命中）
    arbitration_ref: "K21"                        # 来源 K/G/Q 编号（可空）
    ```
- **构造来源**：AUDIT §7.2 路由决策表 37 行（触发关键词列→正例，负向触发列→负例，仲裁规则列→仲裁备注）+ §3 K1-K25 误判路径 + §4 G1-G10 重合组 + 每域补足至 ≥5 条（19 域 × 5 = 95 基线 + 仲裁专项 ≥10）。
- **评测协议（两级）**：
  - **(a) 确定性词法基线**（本 TASK 交付，零 LLM 成本，CI 可跑）：以 74 工具 description + 90 技能 frontmatter description 为语料，关键词/正则匹配打分，报告 top-1 命中、top-3 短名单命中、负例误召回；
  - **(b) LLM 裁决模式**（E2 阶段接入，本 TASK 只留接口）：给定工具面（全量 vs 门控后）让模型选工具，BoR/短名单方法学（PAPERS §F），基座模型按 DEC-2 固定。
- **验收**：
  - ≥100 条且域覆盖 19/19（脚本断言）；
  - 全部 K/G/Q 标记的仲裁场景在 `arbitration_ref` 列可查到对应条目（脚本断言）；
  - `python run_eval.py` 一键出分；连跑两次分数一致；
  - 报告含分域细分 + 误选分类（撞名/越界/缺边界/埋没/名实不符/双暴露）。
- **证据产物**：queries.yaml + 基线分数报告（`artifacts/baseline_report.md`）。
- **与既有基建关系**：评测集为纯 YAML 资产，与 `NewAgentMain` 的 harness_bench/agent_eval 兼容（后续可作为其一个 adapter 的输入）；本 TASK 不依赖该分支，见 §5 D3。

---

### TASK-A1 · sentiment 工具描述重写（PLAN-A1）

- **目标**：消除 Q1/K3——单工具双职责 + 与技能撞名导致的路由混淆。
- **落点**：
  1. `agent/src/tools/sentiment_tool.py` 的 `description`（镜像注册，双面生效，S3）；
  2. `agent/src/skills/sentiment-analysis/SKILL.md` frontmatter description 补互指句。
- **改写草案**（工具 description，保留 Example）：
  > "Score ONE text's sentiment, or fetch the crypto Fear & Greed index — a dual-mode tool, not a market-sentiment framework. Mode 'sentiment_score': score arbitrary text (news headline, tweet, announcement) on -1 (bearish) to 1 (bullish). Mode 'fear_greed_index': fetch the crypto Fear & Greed Index (0-100, lower = more fear). For market-level sentiment frameworks (margin trading / northbound flow / put-call ratio), load the sentiment-analysis skill instead. Example: {\"mode\": \"sentiment_score\", \"text\": \"Tesla beats earnings estimates\"}"
  - 技能侧 description 追加：**"单文本情绪打分或加密恐贪指数数值请用 sentiment 工具；本技能是市场情绪面分析框架。"**
- **不做**：拆分工具 / 更名 `text_sentiment`（ROADMAP 明示长期选项不在本 PLAN）。
- **验收**：
  - 描述含两个模式各自触发场景句（关键词断言：`sentiment_score`+`fear_greed_index`+各自场景词）；
  - 工具描述含对 sentiment-analysis 的显式互指；技能描述含对 sentiment 工具的显式互指（双向 grep）；
  - E1 就绪后：文本打分类 query 对工具命中率、框架分析类 query 对技能命中率 ≥ 基线（E1 未就绪时先过关键词断言测试）；
  - `pytest agent/tests -k sentiment` 通过。
- **证据产物**：diff + 断言测试输出。

---

### TASK-A2 · sec-edgar / edgar-sec-filings 撞名治理（PLAN-A2）

- **方案推荐：① 更名 `sec-edgar` → `sec-edgar-fetch`**（保留两技能）。理由：
  - 职责一眼区分（`-fetch` 抓取接口 vs `edgar-sec-filings` 分析方法论），Q2/K5 根治；
  - 技能总数不变（90），6 份 README 的 badge/计数不动，仅技能表一行改名；
  - 合并案（②）会触发技能数 90→89、category 表两行变动（data-source 10→9、flow 8→7）、6 README badge 同步，爆炸半径显著更大，且 sec-edgar 自带 `references/`+`scripts/` 子目录（接口文档属性强），与方法论技能合并后结构混杂。
- **引用面清单（已勘察）**：
  | 位置 | 引用 | 处置 |
  |---|---|---|
  | `agent/src/skills/sec-edgar/`（目录、frontmatter name、正文自述） | 自身 | 目录改名 + 全量替换 |
  | `agent/src/skills/sec-edgar/references/*.md`、`scripts/*.py` 内的 `sec-edgar/references/...` 链接 | 链接前缀约定（SKILL.md 明示 read_file 以 skills/ 为根） | 随目录同步改名 |
  | `agent/tests/test_skill_reference_links.py:30` `_SKILLS_UNDER_TEST` | `"sec-edgar"` | 改 `"sec-edgar-fetch"` |
  | `README*.md` ×6 技能表（Data Source 行列举 `sec-edgar`） | 技能名 | 6 份同步改名 |
  | `agent/src/skills/edgar-sec-filings/SKILL.md` 正文提及 sec-edgar 的交叉引用句 | 互指句 | 更新为新名 |
  | `.opencode/skills/` | symlink（S1） | 自动跟随，无需手工 |
  | 2 个 swarm preset（earnings_research_desk、global_equities_desk）| 仅引用 `edgar-sec-filings`（未引用 sec-edgar） | **无需改动**（已核实） |
- **验收**：
  - 全仓 `grep -r "sec-edgar"` 残留 = 0（排除新名 `sec-edgar-fetch` 与日期性历史文本；README News 历史条目不改写——冻结历史，测试 `test_every_skill_count_in_the_prose_is_current` 已豁免 news bullets）；
  - `pytest agent/tests/test_skill_reference_links.py agent/tests/test_readme_counts.py` 通过；
  - 技能总数保持 90（`_bundled_skill_count()` 断言）。
- **证据产物**：grep 零残留输出 + 测试通过记录。

---

### TASK-A3 · read_file / write_file 作用域声明（PLAN-A3）

- **目标**：消除 Q3/K22——与宿主同名动词工具的作用域混淆。
- **落点（四处，S2 双面不同文）**：
  1. `agent/mcp_server.py` `write_file` wrapper docstring（L1149）；
  2. `agent/mcp_server.py` `read_file` wrapper docstring（L1162）；
  3. `agent/src/tools/write_file_tool.py:18` 工具类 description；
  4. `agent/src/tools/read_file_tool.py:22` 工具类 description。
- **改写草案**（两处表面语义一致，工具类保留各自行为细节）：
  - write_file（MCP docstring）：
    > "Write content to a file in the Vibe-Trading backtest workspace (run_dir). Relative paths resolve against the active run directory. Used to create config.json and signal_engine.py for backtesting workflows. Host files and source code belong to the host's own file tools, not this one."
  - read_file（MCP docstring）：
    > "Read a file from the Vibe-Trading backtest workspace (run_dir). Relative paths resolve against the active run directory. Use it to inspect backtest artifacts such as config.json, signal_engine.py, and result CSVs. Host files and source code belong to the host's own read tool, not this one."
  - 工具类版本在现有句式上追加同一作用域句（write_file 保留 "Creates parent directories automatically"，read_file 保留 "optional line limit" 行为说明）。
- **验收**：
  - 四处描述各含作用域关键词（断言：`backtest workspace`/`run_dir`/`relative path` 三词族命中）；
  - 与 AUDIT §8.2 输入 1 仲裁规则文本一致（"回测工作区相对路径→VT 对"）；
  - 两表面描述对账测试（新增轻量测试：MCP docstring 与工具类 description 的作用域句语义一致，防未来漂移）；
  - `pytest agent/tests -k "read_file or write_file or sandbox"` 通过。
- **证据产物**：diff + 断言测试。

---

### TASK-A4 · quantlib_call 首句前置高频用例（PLAN-A4）

- **目标**：消除 Q4/K16——286 函数唯一入口的高频用例词埋没，急用场景多付两轮发现成本。
- **落点**：`agent/src/tools/quantlib_tool.py` `description`（镜像注册，双面生效，S3）。
- **改写草案**（首句前置用例词，三步发现降为次段，Example 保留）：
  > "Compute VaR/CVaR, Black-Scholes prices and Greeks, deflated Sharpe, purged cross-validation, DCF / comps valuation — the single entry point to the tested finance-math library (src/quantlib, 286 functions). Read-only and pure-compute: it fetches no data and writes no files. Discovery: start with action='list' to see modules, then action='list' with a module to see its functions, then action='describe' for a signature. Example: quantlib_call(action=\"call\", module=\"risk\", function=\"historical_var\", kwargs={\"returns\": [0.01, -0.02], \"confidence\": 0.95})."
- **验收**：
  - 首 50 token 内含 VaR/DCF/Sharpe 关键词 ≥3（脚本断言，tiktoken 或空白切分近似）；
  - 三步发现模式（list→list→describe）说明仍在（关键词断言）；
  - 模块枚举内容无遗漏（与现描述的 13 个模块族对比，允许措辞变化、不允许信息丢失——对照检查表）；
  - `pytest agent/tests -k quantlib` 通过（工具行为未动，仅描述）。
- **证据产物**：首 50 token 关键词命中报告 + diff。

---

### TASK-A5 · 技能双暴露治理决策成文（PLAN-A5，**阻塞于 DEC-1**）

- **目标**：就 90 技能双路径暴露（`.opencode/skills` 宿主面 + MCP `list_skills`/`load_skill`）做出决策并成文，输出 B5/F3 可直接执行的依据。
- **本仓库新事实（S1）**：`.opencode/skills` 为 symlink → 宿主面与源零漂移；双暴露的代价集中在**路由竞争**（宿主已加载 90 技能全文时，MCP 面 list_skills/load_skill 2 工具 + 90 条一行目录构成重复竞争面），而非副本漂移。
- **决策三要素（成文要求）**：① 主面选择；② 另一侧的关闭/降级方式；③ `.opencode/` 入口的定位（本仓库 = symlink 安装产物；分发产物 = 构建时复制）。
- **候选方案**（供 DEC-1 拍板，详见 §5）：
  - **方案甲（推荐）**：宿主面为主——opencode 宿主内经 symlink 原生加载技能；MCP 侧 `list_skills`/`load_skill` 保留但降级（描述声明"宿主已提供技能面时优先宿主"；B5 阶段评估注册时探测宿主环境）。理由：纯 MCP 客户端（Claude Code/Cursor 无 `.opencode` 分发）仍需 MCP 技能面，不能直接关闭；VT 开源分发主线是 pip/ClawHub 进宿主。
  - **方案乙**：MCP 面为主——文档引导宿主不安装技能副本（删除 symlink/分发），技能一律经 list_skills/load_skill 渐进披露。理由：单点维护、纯 MCP 客户端体验一致；代价：opencode 宿主失去原生技能体验（技能不再出现在宿主 skill 列表）。
- **验收**：决策文本含三要素；B5/F3 据其可直接执行（无二次决策）；写入本文档族（ROADMAP 修订或独立决策记录节）。
- **依赖**：DEC-1 用户拍板。

---

### TASK-A6 · 内外工具名映射表与技能文档统一（PLAN-A6，**依赖 F1**）

- **目标**：消除 Q19/K25/G10——内外双名漂移（`pattern` vs `pattern_recognition` 等）导致的 swarm 白名单不可移植、技能引导指向不可达工具。
- **落点**：
  1. 权威映射表：新建 `HARNESS_EVOLUTION_TOOL_MAPPING.md`（本文档族），以 F1 产物为行集，扩展 AUDIT §8.1 移植映射表；
  2. 技能文档统一：`agent/src/skills/` 中引用内部名处改为 MCP 名 + 括号注内部名（重点：strategy-dev-manager 的 `sdm_*`、引用 `pattern`/`options_payoff` 的技能）。
- **映射表列**：内部名 ｜ 工具类/模块 ｜ MCP 等价物 ｜ 无对应物时的替代方案 ｜ swarm preset 引用（哪些 preset 的哪些角色）｜ 技能文档引用 ｜ 处置（已映射/待暴露评估/刻意不暴露）。
- **验收**：
  - 映射表覆盖率 = 100%（对 F1 内部工具名单，脚本断言：F1 产物每个内部名在映射表有行）；
  - 技能文档中未标注的内部名引用数 = 0（grep 断言脚本：以 F1 内部名清单为模式扫 `agent/src/skills/**`，命中处必须是"映射标注"格式）；
  - `financial_rigor`/`report_audit`/`sdm_*` 三个无对应物项的替代方案列与 AUDIT §8.1 一致（暴露评估本身是 F2，P1，不在本 TASK）。
- **证据产物**：映射表文件 + grep 零残留报告。

---

## 4. 全局验收规则（P0 波次继承）

1. **测试锚点**：任何技能数/工具面变更 → `pytest agent/tests/test_readme_counts.py` 必须过；计数变更需同步 6 份 README 正文（P0 波次中仅 A2 合并案会触发，推荐方案不触发）；
2. **prompt 缓存纪律**：A 批只改 description 字段文本；不改工具注册顺序、不动 system prompt 结构（RESEARCH §8.0 P0-1）；
3. **每 TASK 一个 commit**，commit message 标注对应 PLAN/TASK 编号；
4. **波次放行门槛（双层，2026-08-26 修订）**：
   - **全局地板**：E1 聚合 top-1 不低于基线（0.4367）——保护未改动域，回归哨兵；
   - **定向提升（P0 成功标准）**：被改动工具对应的查询组 top-1 **必须提升**——A1→D13 sentiment 仲裁组、A3→D07 文件类组、A4→D09 quantlib 组（"算 VaR"类）、A2→D02 SEC 组。定向组无提升 = 该改动上游价值存疑，不进 PR；
   - **限定**：E1 词法基线是代理指标（位置加权关键词重叠），恰好能测 A1/A4 类改动；定向组未提升时先排查打分器伪影，最终语义级裁决在 E2 LLM-judge（DEC-2 基座固定后）。上游 PR 表述 = "定向组提升 + 全局无回归"。

## 5. 待人工拍板的决策点

| # | 决策 | 选项 | 建议 |
|---|---|---|---|
| **D1 = DEC-1** | 技能双暴露主面（阻塞 A5 → B5/F3） | 甲：宿主面为主、MCP 侧保留降级 ／ 乙：MCP 面为主、宿主不装副本 | **甲**（理由见 TASK-A5；纯 MCP 客户端仍需 MCP 技能面） |
| **D2** | A2 方案 | ① 更名 sec-edgar-fetch ／ ② 合并单技能 | **①**（爆炸半径小、计数不动，理由见 TASK-A2） |
| **D3** | E1 落点 | a：当前 worktree 新建 `agent/src/evals/tool_selection/` ／ b：移师 NewAgentMain worktree 与 harness_bench 合流 | **a**（评测集是纯资产，先落地不阻塞；harness_bench 合流留给 E2/Wave 2 决策——NewAgentMain 未合入 main，过早绑定有分支风险） |
| **D4** | 分支策略 | a：当前分支 `fix/trading-tool-routing-hints` 继续 ／ b：为 P0 波次新开分支 | **a**（该分支已承载同族工作 A7④；若需走 PR 再以该分支为准） |

## 6. 验证命令清单（执行期使用）

```bash
# 测试锚点（暴露面/技能数/README 对账）
cd agent && python -m pytest tests/test_readme_counts.py -q

# 技能链接完整性（A2）
python -m pytest tests/test_skill_reference_links.py -q

# MCP 面工具数（无 key 环境）
python -c "import asyncio, sys; sys.path.insert(0,'.'); import mcp_server as m; print(len(asyncio.run(m.mcp.list_tools())))"

# 无 key agent 注册表规模（F1 基线）
env -u FRED_API_KEY -u VIBE_TRADING_IWENCAI_KEY -u QVERIS_API_KEY -u VIBE_TW_STOCK_DB \
  python -c "from src.tools import build_registry; print(len(build_registry().tool_names))"

# E1 一键出分
python -m src.evals.tool_selection.run_eval --report artifacts/baseline_report.md
```

---

## 7. 决策记录（DEC-1 已决）

### DEC-1 · 技能双暴露主面选择 —— **已决：方案甲（宿主面为主，MCP 侧保留降级）**

- **决策日期**：2026-08-26 ｜ **决策人**：用户拍板（P0 计划审批同步做出）｜ **状态**：✅ 已决，阻塞解除（A5 完成 → B5/F3 可据本节直接执行）
- **决策三要素**：
  1. **主面**：opencode 宿主面为主——90 技能经 `.opencode/skills` 由宿主原生加载（本仓库为 symlink → `agent/src/skills`，安装产物零漂移），作为技能内容与路由的第一路径。
  2. **另一侧处置**：MCP 侧 `list_skills` / `load_skill` **保留但降级**——纯 MCP 客户端（Claude Code / Cursor 等，无 `.opencode` 分发）仍以此面为唯一技能路径，不能关闭；降级动作（B5 执行依据）：两工具描述追加"宿主已提供技能面时优先宿主路径"声明；注册时宿主环境探测（检测到宿主技能面已加载同套技能时降权/提示）列入 B5 评估，非 P0 范围。
  3. **`.opencode/` 入口定位**：本仓库 = symlink 安装产物（开发态，天然同步，F3 的漂移担忧在本仓库不成立）；分发产物（pip / ClawHub 安装）= 构建时复制，其同步正确性仍归 F3（P1）——F3 职责由此收窄为"保证分发副本与源一致"，而非"防止宿主面漂移"。
- **量化预期**（对齐 ROADMAP PLAN-A5）：决策后**有效暴露路径 = 1**（宿主会话内技能内容只经宿主面进入上下文；MCP 技能工具仅在纯 MCP 客户端场景承担职责）。
- **下游执行依据**：
  - B5（P1）：按要素 2 执行 MCP 侧降级，验收"暴露路径数 = 1 + 对侧调用返回主面指引"；
  - F3（P1）：按要素 3 执行分发副本同步机制（生成脚本或漂移检测），范围收窄为分发产物。
- **证据**：S1 勘察（`.opencode/skills` symlink，`ls -la` 证实）；AUDIT K18/Q7（双暴露运行时事实）；ROADMAP §4 DEC-1 建议案。

---

## 8. 后测结果与上游裁决（2026-08-26）

### 8.1 E1 双层门槛结果

| 指标 | 基线 | 后测 | 判定 |
|---|---|---|---|
| 全局 top-1 | 0.4367 | 0.4367 | ✅ 地板守住（无回归） |
| 全局 top-3 | 0.6076 | 0.6139 | +1 |
| A1 定向（D13 sentiment） | 0.3333 | **0.4444** | ✅ 提升（D13-002 恐贪查询翻转命中） |
| A3 定向（D07 文件类） | 0.3000 | **0.4000** | ✅ 提升（D07-009 write_file 翻转命中） |
| A2 定向（D02 SEC） | 0.5000 | 0.5000 | ➖ 持平（更名价值在语义消歧，词法代理不可测，留 E2） |
| A4 定向（D09 quantlib） | 0.4000 | 0.3000 | ⚠️ -1，归因词法代理盲区（D09-004"讲讲 VaR"方法论查询，动词语义超出关键词重叠分辨力；top-3 反升 6→7），留 E2 裁决 |

两处翻转归因详见 `agent/src/evals/tool_selection/artifacts/post_report.md` 的 Delta Analysis 节（D04-003 = 打分器长度偏差伪影；D09-004 = 词法歧义，非真实路由回归）。

### 8.2 逐 TASK 上游普遍适用性裁决（2026-08-26 依 E2 终局结果修订）

> ⚠️ §8.1 的词法层裁决已被 §10 的 E2 LLM-judge 终局结果**部分推翻**：
> 词法基线测得的提升未通过语义仲裁。裁决以本节为准。

| TASK | 普遍适用性 | 裁决 | 理由 |
|---|---|---|---|
| E1+E2 | ✅ 普遍 | **PR 候选（旗舰）** | 上游无工具选择评测基建；评测集+词法运行器+4 家族 LLM-judge 协议+统计模块全自包含；null result 本身是贡献 |
| A6 | 🟡 部分 | **拆分**：技能文档可达性注记 → PR（外部客户端按技能走撞墙是可达性问题，与路由准确率无关，E2 不推翻）；映射表文档 → 本地保留 | |
| A1 | ✅ 普遍 | **本地保留（暂缓上游）** | E2 测得路由中性（仅 1 条 qwen 真实提升）；无测量收益的 PR 上游说服力弱 |
| A3 | ✅ 普遍 | **本地保留（暂缓上游）** | E2 天花板效应：基线已全对，无可测空间 |
| A4 | ✅ 普遍 | **本地保留（暂缓上游）** | E2 路由中性；唯一"回归"为模型澄清行为事件，非路由回归 |
| A2 | ✅ 普遍 | **本地保留（暂缓上游）** | 撞名客观存在但 E2 未测得收益，且更名属 breaking——无测量收益不做 breaking 变更 |
| F1 | 🟡 可选 | **本地保留，暂缓** | 脚本自包含有上游价值，产物服务本地治理；待上游有兴趣再提 |
| A5 | ❌ 本地 | **本地保留** | 决策依赖本仓库 `.opencode` symlink 形态，非普遍 |

### 8.3 PR 拆分方案（2026-08-26 修订，fork → origin，关联 #1218）

1. **PR-1（旗舰）** `eval: tool-selection suite with 4-family LLM-judge protocol`：
   评测集 + 词法运行器 + LLM-judge 运行器/统计模块 + 面板配置 + golden trace +
   完整报告（含 null result 与有效性校正）——新基建 + 诚实证据，独立评审；
2. **PR-2** `docs(skills): reachability notes`：A6 技能文档注记部分（小 PR）；
3. **暂缓**：A1/A3/A4/A2 描述类改动留本地——待 B 批暴露面工程后复测
   （PAPERS §F：呈现数量是主导变量，描述治理的收益可能在裁剪后的表面上才显形）；
4. 已在本分支的 43b3f485（A7④ trading_* 提示）同样暂缓——E2 未覆盖其收益证明。

提交规范：DCO `Signed-off-by`（CONTRIBUTING.md 强制）+ 关联 #1218。
（2026-08-26 修正：CONTRIBUTING.md §Attribution 明文禁止 `Co-Authored-By:` 与
AI 追溯行——本仓库提交仅保留 DCO sign-off，规则已固化至仓库根本地 `AGENTS.md`。）

---

## 10. E2 LLM-judge 终局结果（2026-08-26）

### 10.1 执行规模

4 模型家族（qwen3.8-max / deepseek-v4-flash-0731 / kimi-k3 / glm-5.2，
全部 DashScope）× 2 表面（基线冻结快照 vs P0 后）× 158 条 = **1264 次主调用**
+ 192 次确定性探针，8 进程并发 ~75 分钟完成；估算成本 ~$20（价格表
estimate:true，对外引用前须核实）。

### 10.2 有效性校正（两个评分伪影，离线重分析纠正）

1. **格式遵从混淆**：flash（重）/kimi（偶发）以裸名应答而非 `tool:` 前缀
   id——严格评分误判为 miss。格式宽容评分（裸名可无歧义映射则视为正确）
   才是路由质量的有效度量；
2. **更名对账伪影**：D02-006 的 expected 已对齐新名，基线面的正确旧名
   应答被记 miss——每模型 1 条人为"提升"，从 delta 中剔除。

### 10.3 校正后配对结果（宽容评分，剔除 D02-006）

| 模型 | 基线 | 后测 | Δ | imp/reg | McNemar p |
|---|---|---|---|---|---|
| qwen3.8-max | 0.8917 | 0.9045 | +0.0127 | 8/6 | 0.791 |
| deepseek-v4-flash-0731 | 0.8917 | 0.8790 | -0.0127 | 8/10 | 0.815 |
| kimi-k3 | 0.9363 | 0.9299 | -0.0064 | 1/2 | 1.000 |
| glm-5.2 | 0.9172 | 0.9108 | -0.0064 | 6/7 | 1.000 |
| **池化** | | | | **23/25** | **0.885** |

### 10.4 终局结论

**P0 描述改动在 4 家族 LLM-judge 下路由中性**（池化 p=0.885）。
- 基线准确率 0.88-0.94 → **天花板效应**：全表面呈现下强模型本就路由良好；
- 定向组：A1 +1 条真实提升（qwen D13-001）；A3 基线已全对；A4/A2 的表观
  翻转全部归因为模型行为事件（澄清式应答 / tool_calls 格式应答，其底层
  选择正确）或更名伪影——**无一条真实路由回归**；
- 词法基线与 LLM-judge 一致率仅 ~50%：词法是弱代理，其测得的"提升"未过
  语义仲裁——E1 双层门槛中的"定向提升"在语义层面未兑现；
- **战略推论**（Wave 2 依据）：描述治理单独不动路由准确率，主导变量是
  **呈现工具数量**（PAPERS §F）——B 批暴露面工程才是真杠杆；描述改动的
  收益可能在裁剪后的表面上复测时才显形。

---

## 9. 决策记录（DEC-2 已决）

### DEC-2 · E2 评测基座模型固定 —— **已决（2026-08-26）**

- **模型面板（4 家族，全部经 DashScope 同一 key 冒烟验证）**：
  | 模型 | 家族 | 角色 |
  |---|---|---|
  | `qwen3.8-max` | Qwen | 主判（与基座迁移 PoC 共用基线） |
  | `deepseek-v4-flash-0731` | DeepSeek | 敏感性 |
  | `kimi-k2.6` | Moonshot | 敏感性 |
  | `glm-5.1` | GLM | 敏感性（重推理型） |
  （候选中 `glm-5.3` 403 无权限、`deepseek-v4` 404、`glm-5.2` 输出为空——均排除；
  PAPERS §G MemDelta 教训：模型选择本身可翻转结论，跨家族一致的提升才是
  "普遍适用"硬证据）；
- **预算**：用户声明无上限。矩阵 = 158 条 × 2 表面（基线冻结快照 vs P0 后）×
  4 模型 = 1264 次主调用 + 192 次确定性探针（8 条 ×3 重复 ×2 表面 ×4 模型）；
  冒烟实测 ~12k 输入 token/调用，全矩阵估算 ~$45；运行器内置预算上限
  （单模型单轮 25M 输入 token / 700 次调用，超限即停，断点续跑）；
- **统计设计（周密版）**：配对设计（每条 query 自身对照）→ 逐模型 **McNemar
  精确检验**（配对二值结果的正确检验）+ Wilson 95% CI + 池化估计与模型间异质性；
  次级指标 top-3/负向误召回/JSON 解析失败率（>5% 降置信）；翻转清单逐条仲裁；
  词法基线 × LLM 判定一致性分析（不一致集 = 代理盲区清单）；成本联报；
- **三层冻结**：模型选定（本记录）+ 推理参数冻结（`judge_config.yaml`，
  temperature=0、max_tokens=80、提示词模板 sha256 入 trace）+ 协议冻结
  （golden trace JSONL，(prompt-hash, model) 可复现）；
- **执行载体**：`agent/src/evals/tool_selection/run_llm_judge.py`（E1 词法基线
  之上的语义级裁决层），对照面 = `corpus_baseline_snapshot.yaml`（git 历史
  f23dbbbd 导出的改动前冻结快照）vs `corpus_snapshot.yaml`（P0 改动后）。
