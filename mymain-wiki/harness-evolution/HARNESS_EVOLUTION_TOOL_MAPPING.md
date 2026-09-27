# HARNESS 演进研究 — 内部↔MCP 工具名权威映射表（Tool Name Mapping）

> 维护者：shadowinlife ｜ 初版：2026-08-26 ｜ 状态：**首版定稿（TASK-A6 产物）**
> 配套文档：`HARNESS_EVOLUTION_CAPABILITY_AUDIT.md`（能力审计 v2，K/G/Q 编号来源）· `HARNESS_EVOLUTION_ROADMAP.md`（PLAN 编号来源）· `HARNESS_EVOLUTION_P0_PLAN.md`（TASK-A6 任务定义）· `HARNESS_EVOLUTION_RESEARCH.md` · `HARNESS_EVOLUTION_PAPERS.md` · `HARNESS_EVOLUTION_BENCHMARKS.md`
> 本文档定位：HARNESS EVOLUTION 工程的**内外工具名权威映射表**——关闭 AUDIT 发现 **Q19/K25/G10**（内部注册表名与 MCP 面名漂移；swarm preset 白名单硬编码内部名；部分技能文档指向 MCP 客户端不可达的工具），并为 **PLAN-D3**（swarm 白名单移植映射工程化）与 **PLAN-F2/F4**（`financial_rigor`/`report_audit`/`sdm_*` 暴露评估）提供行集地基。

---

## 0. 证据来源、权威规则与总量对账

### 0.1 证据来源（F1 运行时盘点产物）

本文全部行集来自 **PLAN-F1 的运行时盘点产物**（非文档推演）：

- **产物路径**：`agent/scripts/artifacts/internal_tool_inventory.json`（机器可读行集）+ `agent/scripts/artifacts/internal_tool_inventory.md`（伴生可读版）；
- **采集时间**：产物 `captured_at` 字段为 `null`（确定性模式 `--no-timestamp`，可复现采集）；文件系统生成时间 **2026-08-26 09:33**（文件 mtime），晚于 AUDIT v2 定稿（2026-08-25），是对 AUDIT §2"部分盘点"（§6.3 诚实性声明）的**全量运行时替换**；
- **采集协议**（引自产物头部）：keyless 注册表在干净子进程中测量，与 `tests/test_readme_counts.py::_keyless_agent_tool_count` 完全同法（shell 工具关闭）；MCP 面经 `asyncio.run(mcp_server.mcp.list_tools())` 直接枚举。**Runtime is authoritative**（以运行时为权威，不以文档/源码注释为准）；
- **采集环境**：子环境已清除凭证门控键 `FRED_API_KEY`、`QVERIS_API_KEY`、`VIBE_TRADING_IWENCAI_KEY`、`VIBE_TW_STOCK_DB`（即"keyless"语义）。

### 0.2 权威规则（命名管辖）

> **内部名在 agent 注册表内部为权威；MCP 名在 MCP 面以及一切外部/子代理上下文中为权威。**

具体而言：

1. **agent 注册表内部**（`build_registry()` 输出、工具类实现、注册表测试）使用**内部名**（如 `pattern`、`options_pricing`）。本文不要求改动任何代码注册名（TASK-A6 边界：只动文档，不动代码/注册表/preset）；
2. **MCP 面**（`vibe-trading-mcp` 暴露的 74 工具）与**一切外部上下文**（MCP 客户端、基于 MCP 面的子代理、面向用户的文档表述）一律使用 **MCP 名**（如 `pattern_recognition`、`analyze_options`）；
3. **双名并见处**（本文映射表、技能文档中的映射标注）采用"MCP 名 + 括号注内部名"格式，如 `pattern_recognition`（内部名 `pattern`）；
4. 该规则与 AUDIT §8.2 路由元规则第 5 条一致："引用内部工具名（pattern/options_payoff 等）的上下文一律经映射表转 MCP 名"。

### 0.3 总量对账（F1 产物 reconciliation 节）

| 计数 | 数值 | 语义 |
|---|---|---|
| agent 注册表（keyless） | **107** | 干净子进程中 `build_registry()` 实际注册数 |
| MCP 面 | **74** | `mcp.list_tools()` 实际枚举数 |
| 内部工具（已注册、不在 MCP 面） | **48** | = 44 个无 MCP 等价物 + 4 个名称漂移对（表 A） |
| 仅审计条目（AUDIT §2 列出、keyless 不注册） | **4** | `bash` / `background_run` / `cancel_background` / `get_taiwan_stock_data` |
| 发现但被门控挡在 keyless 注册表外 | **9** | 4 个 agent 侧（shell 三件套 + 台股快照）+ 5 个 MCP 面有、注册表按门控隐藏（`get_macro_series`/`iwencai_search`/`qveris_*`×3） |
| MCP 面有、keyless 注册表无 | **15** | 含 3 个漂移对的 MCP 侧名（`analyze_options`/`analyze_options_payoff`/`pattern_recognition`）+ 5 个门控工具 + 7 个 MCP 侧运维/编排工具（`get_run_result`/`get_swarm_status`/`list_runs`/`list_skills`/`list_swarm_presets`/`reap_stale_runs`/`retry_run`） |

对账恒等式：**107 = 59（注册表∩MCP 同名）+ 44（注册表独有、无等价）+ 4（注册表独有、漂移对）**；**74 = 59 + 15**。

**名称漂移的机制**：同一能力在注册表与 MCP 面**各自注册了不同名字**——注册表侧是历史内部名（`options_pricing` 等），MCP 面侧是对外规范化名（`analyze_options` 等），二者互不在对方表面以本名出现，故 `mcp_surface_not_in_keyless_registry` 中出现了 3 个 MCP 侧名。这正是 Q19/K25 的实体。

### 0.4 与 AUDIT 的增量（reconciliation 三类）

| 类别 | 数量 | 内容 |
|---|---|---|
| `audit_only`（AUDIT 有、keyless 注册表无） | 4 | shell 三件套（`--enable-shell-tools` / `VIBE_TRADING_ENABLE_SHELL_TOOLS=1` 才注册；**设计上从不上 MCP**）；`get_taiwan_stock_data`（需 `VIBE_TW_STOCK_DB` 指向 schema 合法的 SQLite 快照；**仅 agent 侧，从不上 MCP**） |
| `new_since_audit`（AUDIT v2 之后新增、AUDIT 名单未覆盖） | 24 | `alpha_compare`、`analyze_image`、`check_background`、`compact`、`etoro_*`×8、`generate_backtest_config`、`propose_mandate_profiles`、`session_search`、`trading_acc_cash_flow`、`trading_cancel_order`、`trading_capital_distribution`、`trading_capital_flow`、`trading_earnings_calendar`、`trading_financials`、`trading_history_deals`、`trading_place_order`、`trading_rehab` |
| `gated_not_registered`（发现但被门控挡在 keyless 注册表外） | 9 | 见 §0.3 第 5 行 |

> 本文表 B/表 C 的行集 = 48 个内部工具 + 4 个仅审计条目 = **52 行**，即 F1 产物 `tools` 数组中全部 `mcp_counterpart_status != "is-mcp-tool"` 的条目，无一遗漏（§5 覆盖性断言）。

---

## 1. 表 A — 名称漂移对（内部名 → MCP 等价物，共 4 对）

**判定依据**：仅收录 F1 产物中 `mcp_counterpart_status == "mapped"` 的条目，且每条与 AUDIT §8.1 移植映射表逐条一致。**不推测任何额外漂移对**——其余内部工具要么同名直通（`is-mcp-tool`），要么无 MCP 等价物（表 B）。

| 内部名 | 工具类 / 模块 | MCP 等价物 | 用途（F1 purpose 字段） | swarm preset 引用（角色） | 技能文档引用 |
|---|---|---|---|---|---|
| `options_pricing` | OptionsPricingTool / `src.tools.options_pricing_tool` | `analyze_options` | 期权定价：Black-Scholes 理论价与 Greeks | convertible_bond_team（option_analyst）、derivatives_strategy_desk（greeks_manager / strategy_designer / vol_analyst）、earnings_research_desk（event_options_analyst）、investment_committee（risk_officer）、risk_committee（tail_risk_analyst） | options-strategy |
| `options_payoff` | OptionsPayoffTool / `src.tools.options_payoff_tool` | `analyze_options_payoff` | 欧式多腿期权策略：确定性分段线性到期损益 + BS 现价/隐波情景 | investment_committee（risk_officer）、risk_committee（tail_risk_analyst） | options-payoff |
| `pattern` | PatternTool / `src.tools.pattern_tool` | `pattern_recognition` | 对回测数据跑图形形态检测（头肩、双顶/底、K 线形态、支撑/阻力等） | technical_analysis_panel（harmonic_analyst / wave_analyst） | candlestick、data-routing、earnings-revision、factor-research、geopolitical-risk、harmonic、perp-funding-basis、quant-statistics、social-media-intelligence、trade-journal、us-etf-flow（F1 宽扫描命中；逐条复核均为普通英文用词而非工具引用，见 §5.2） |
| `edit_file` | EditFileTool / `src.tools.edit_file_tool` | `write_file` | 在文件中查找并替换 old_text 的首次出现 | ml_quant_lab（data_scientist）、quant_strategy_desk（backtester） | backtest-diagnose、strategy-generate |

**语义差异备注（`edit_file` → `write_file`）**：内部 `edit_file` 是**查找-替换**语义（替换首个匹配），MCP 面 `write_file` 是**整文件写入**语义。AUDIT §8.1 将二者映射为同一移植落点（"文件写入"）；MCP 侧消费者要做精确修改时，需"先 `read_file` → 本地修改 → `write_file` 整文件写回"。此差异已在受影响技能文档（backtest-diagnose、strategy-generate）的标注中写明。

**D3 移植含义**：上述 4 个内部名出现在 9 个 preset 的 per-agent `tools:` 硬白名单中（上表 swarm 列）。基于 MCP 面的子代理移植这些角色时，白名单必须经本表运行时转换为 MCP 名，否则交集为空、角色失能（AUDIT §8.1 ⚠️ 移植映射表）。

---

## 2. 表 B — 无 MCP 等价物的内部工具及处置（共 48 个）

**行集**：F1 产物中 `mcp_counterpart_status == "no-equivalent"` 的全部条目 = 44 个 keyless 已注册 + 4 个仅审计条目。
**"为何不在 MCP 面"三分类**：**设计**（结构性刻意排除）/ **门控**（环境门控且按设计不上 MCP）/ **待评估**（AUDIT/ROADMAP 已立项评估，或新增工具尚无决策记录）。
**处置**对齐 P0_PLAN TASK-A6 列约定：已映射 / 待暴露评估 / 刻意不暴露 / 未暴露（无决策记录）。

### 2.1 Shell 与进程管理（设计：shell 永不上 MCP）

| 内部名 | 用途 | 为何不在 MCP 面 | MCP 侧替代 | 处置 |
|---|---|---|---|---|
| `bash` | 在工作目录执行 shell 命令 | **设计+门控**：仅 `--enable-shell-tools` / `VIBE_TRADING_ENABLE_SHELL_TOOLS=1` 时注册；按设计永不上 MCP（AUDIT §2、README 安全默认） | 无 | 刻意不暴露 |
| `background_run` | 在受跟踪的后台进程组运行 shell 命令 | 同上 | 无 | 刻意不暴露 |
| `cancel_background` | 按 task_id 精确取消一个 background_run 任务 | 同上 | 无 | 刻意不暴露 |
| `check_background` | 查询后台任务状态、耗时与 300 秒自动超时前的剩余时间 | **设计**：agent 侧进程管理，外部无对应进程面 | 无 | 刻意不暴露 |

### 2.2 Agent 会话 / 记忆 / 技能自演进（设计：agent-only）

| 内部名 | 用途 | 为何不在 MCP 面 | MCP 侧替代 | 处置 |
|---|---|---|---|---|
| `compact` | 压缩对话历史以释放上下文空间 | **设计**：agent 会话上下文管理 | 无（宿主侧自行管理上下文） | 刻意不暴露 |
| `remember` | 持久化跨会话记忆 | **设计**：agent 记忆按设计仅 agent 可用（AUDIT §0.1 内部工具示例） | 无 | 刻意不暴露 |
| `session_search` | 按关键词搜索过往会话 | **设计**：agent 会话库 | 无 | 刻意不暴露 |
| `analyze_image` | 用多模态 LLM 查看本地图像并回答问题 | **设计**：依赖内置 agent 的多模态会话 | 无（MCP 客户端用宿主自身视觉能力） | 刻意不暴露 |
| `save_skill` | 把成功的工作流/策略模板保存为可复用技能 | **设计**：技能自演进（skill_writer 族）按设计仅 agent 可用 | 无 | 刻意不暴露 |
| `patch_skill` | 以文本替换方式修复/更新现有技能 | 同上 | 无 | 刻意不暴露 |
| `delete_skill` | 删除用户创建的技能及其全部文件 | 同上 | 无 | 刻意不暴露 |
| `skill_file` | 管理技能目录下的辅助文件 | 同上 | 无 | 刻意不暴露 |

### 2.3 实盘交易治理（设计：下单/撤单结构性排除于 MCP）

> README 明示："Order-placing tools stay off MCP (agent + CLI only)"；MCP 面 `trading_*` 只读族由 `is_readonly` 强制。

| 内部名 | 用途 | 为何不在 MCP 面 | MCP 侧替代 | 处置 |
|---|---|---|---|---|
| `trading_place_order` | 经所选交易连接器下单 | **设计**：下单结构性排除于 MCP（`is_readonly` 强制） | 无（仅 agent + CLI） | 刻意不暴露 |
| `trading_cancel_order` | 按 order id 撤销所选连接器上的挂单 | 同上 | 无 | 刻意不暴露 |
| `propose_mandate_profiles` | 提出 2-4 个有界自主实盘 mandate 档案（钳制在账户硬上限内） | **设计**：实盘 mandate 治理流程（agent + CLI 确认面） | 无 | 刻意不暴露 |
| `etoro_close_position` | 按 position id 平仓/部分平仓 eToro 头寸 | **设计**：订单操作族，同下单排除 | 无 | 刻意不暴露 |
| `etoro_cancel_close_order` | 按 order id 取消 eToro 市价平仓单（仅 paper；实盘 fail-closed，因撤单恢复敞口） | 同上 | 无 | 刻意不暴露 |
| `etoro_edit_position_stops` | 修改/清除 eToro 持仓止损/止盈（仅 paper；实盘 fail-closed 直至增量资金可量化） | 同上 | 无 | 刻意不暴露 |
| `etoro_copy_start` | 开始跟单某投资者或调整既有跟单配置 | **设计**：跟单交易订单族（资金操作） | 无 | 刻意不暴露 |
| `etoro_copy_close` | 按 mirror id 关闭/解除 eToro 跟单关系 | 同上 | 无 | 刻意不暴露 |
| `scheduled_research` | 检视计划研究并准备创建/取消提案 | **设计**：propose/confirm 两阶段——提案必须在所在表面（Web 卡片 / CLI y/N / IM confirm）人工确认后才落 job store，MCP 面无确认回路 | 无 | 刻意不暴露 |

### 2.4 本地环境门控（门控 + 设计：agent 侧）

| 内部名 | 用途 | 为何不在 MCP 面 | MCP 侧替代 | 处置 |
|---|---|---|---|---|
| `get_taiwan_stock_data` | 查询本地只读台股快照（TWSE/TPEx） | **门控+设计**：需 `VIBE_TW_STOCK_DB` 指向 schema 合法的 SQLite 快照；仅 agent 侧注册，按设计从不上 MCP | 无 | 刻意不暴露 |

### 2.5 假设 / 研究自动驾驶栈（未暴露，无决策记录）

> 本地假设注册表（`~/.vibe-trading`）工作流。AUDIT 未对其暴露与否作出决策；如未来需要 MCP 暴露，应另立 PLAN（可参照 F4 对 `sdm_*` 的三选一框架）。

| 内部名 | 用途 | 为何不在 MCP 面 | MCP 侧替代 | 处置 |
|---|---|---|---|---|
| `create_hypothesis` | 在本地注册表创建持久研究假设 | 未暴露（无决策记录）：本地假设注册表工作流 | 无 | 未暴露（无决策记录） |
| `update_hypothesis` | 更新假设生命周期状态与失效备注 | 同上 | 无 | 未暴露（无决策记录） |
| `search_hypotheses` | 按文本查询和/或生命周期状态搜索假设 | 同上 | 无 | 未暴露（无决策记录） |
| `link_backtest` | 把 run card 或回测运行目录挂到研究假设上 | 同上 | 无 | 未暴露（无决策记录） |
| `link_autopilot_backtest` | 读取已完成回测目录的 run_card.json 指标并关联到假设 | 同上 | 无 | 未暴露（无决策记录） |
| `generate_backtest_config` | 从已保存假设生成回测 config.json | 同上 | `write_file`（手写 config，无假设关联） | 未暴露（无决策记录） |
| `scaffold_signal_engine` | 为已保存假设写入契约正确的 signal_engine.py 桩 | 同上 | `write_file`（手写桩代码） | 未暴露（无决策记录） |
| `run_research_autopilot` | 从已保存假设启动研究目标 | 同上 | `start_research_goal`（MCP 面有，但无假设关联） | 未暴露（无决策记录） |

### 2.6 财务严谨性与报告审计（✅ 已暴露：DEC-3，2026-08-30）

| 内部名 | 用途 | 为何不在 MCP 面 | MCP 侧替代（AUDIT §8.1） | 处置 |
|---|---|---|---|---|
| `financial_rigor` | 以精确十进制算术（无浮点漂移）校验财务数据准确性 | **已在 MCP 面**（DEC-3，镜像注册 `_MIRRORED_TOOL_SOURCES`，同名） | 同名 `financial_rigor` | ✅ 已暴露（eebf48af） |
| `report_audit` | 发布前审计研究报告的数字数据点准确性 | **已在 MCP 面**（DEC-3，同上） | 同名 `report_audit` | ✅ 已暴露（eebf48af） |

### 2.7 SDM 策略生命周期（✅ 已裁决不暴露：DEC-4，2026-08-30）

> DEC-4 裁决（2026-08-30）：**不新增 MCP 注册**。读侧由 strategy-discovery 三件套（`list_strategies` / `query_strategies` / `get_strategy_evidence`，同一策略库的只读门面）承担；写侧（register / status 更新 / decay 扫描）维持 agent-only。strategy-dev-manager 技能文档已改写为双面指引。

| 内部名 | 用途 | 为何不在 MCP 面 | MCP 侧替代 | 处置 |
|---|---|---|---|---|
| `sdm_register` | 把从论文提取的因子/策略注册进策略库 | 写侧：策略库喂推荐，写入面留在本地运行时（DEC-4） | 无（agent-only） | ✅ 已裁决不暴露（eebf48af） |
| `sdm_status` | 查询/更新策略库中因子/策略的生命周期状态 | 写侧同上 | 读侧：`list_strategies` / `query_strategies` / `get_strategy_evidence` | ✅ 已裁决（读侧三件套，写侧 agent-only） |
| `sdm_decay_scan` | 对活跃因子/策略批量跑衰减监控扫描 | 运维性质（对照 B3 同族处置） | 无（agent-only） | ✅ 已裁决不暴露（eebf48af） |

### 2.8 组合 / 连接器只读扩展（未暴露，无决策记录）

> 均为 `new_since_audit` 新增工具。只读类连接器扩展未来或有暴露价值，但 AUDIT/ROADMAP 尚无决策——如实标注，不预设。

| 内部名 | 用途 | 为何不在 MCP 面 | MCP 侧替代 | 处置 |
|---|---|---|---|---|
| `alpha_compare` | 对精选（≥2 个）Alpha Zoo 因子做同 universe/period 头对头比较 | 未暴露（无决策记录） | 近邻：`alpha_bench`（整园评测，MCP 面有；不做精选子集头对头） | 未暴露（无决策记录） |
| `portfolio_risk_xray` | 组合风险 X 光：集中度（HHI/有效 N）、年化波动、最大回撤、历史 VaR/ES、分散率、相关性/beta | 未暴露（无决策记录） | 近邻：`quantlib_call`（risk 模块）+ `get_market_data`（MCP 侧消费者自行组装输入） | 未暴露（无决策记录） |
| `portfolio_summary` | 读取本地配置的只读券商账户最新脱敏快照 | 未暴露（无决策记录）：依赖本地 /portfolio 快照库 | 近邻：MCP 面 `trading_*` 只读族（单连接器读取，无跨源聚合快照） | 未暴露（无决策记录） |
| `etoro_search_instruments` | 按 ticker/自由文本/资产类别搜索 eToro 标的 | 未暴露（无决策记录） | 近邻：`search_symbol`（通用标的解析，非 eToro 源） | 未暴露（无决策记录） |
| `etoro_copy_poll` | 轮询异步 eToro 跟单操作的结果 | 未暴露（无决策记录） | 无 | 未暴露（无决策记录） |
| `etoro_copy_precheck` | 干跑：账户能否以账户币种金额跟单某投资者 | 未暴露（无决策记录） | 无 | 未暴露（无决策记录） |
| `trading_acc_cash_flow` | 按清算日读取账户资金流水（存取/换汇/结算/费用） | 未暴露（无决策记录） | 无 | 未暴露（无决策记录） |
| `trading_capital_distribution` | 读取某标的今日资金进出快照（超大/大/中/小单分桶） | 未暴露（无决策记录） | 近邻：`get_fund_flow`（东财源订单桶净流入，非券商账户视角） | 未暴露（无决策记录） |
| `trading_capital_flow` | 读取某标的历史资金流时序（机构/散户进出） | 未暴露（无决策记录） | 近邻：`get_fund_flow`（不同数据源） | 未暴露（无决策记录） |
| `trading_earnings_calendar` | 读取美股/港股未来财报日历（EPS/营收预期、IV、IV rank） | 未暴露（无决策记录） | 无 | 未暴露（无决策记录） |
| `trading_financials` | 经所选连接器读取标的财报（INCOME/BALANCE/CASH_FLOW） | 未暴露（无决策记录） | 近邻：`get_financial_statements`（公开源，非连接器账户视角） | 未暴露（无决策记录） |
| `trading_history_deals` | 读取历史 FILL 记录（影子账户成本重建用） | 未暴露（无决策记录） | 无 | 未暴露（无决策记录） |
| `trading_rehab` | 读取标的分红/拆股/配股复权因子 | 未暴露（无决策记录） | 无 | 未暴露（无决策记录） |

---

## 3. 表 C — 完整内部工具面紧凑参考表（48 + 4 = 52 行）

**行集**：F1 产物 `tools` 数组中 `mcp_counterpart_status != "is-mcp-tool"` 的**全部**条目——48 个内部工具（44 无等价 + 4 漂移对）+ 4 个仅审计条目。本表为 D3/F2/F4 的机器可查行集；`mcp_counterpart_status` 取值：`mapped`（表 A 漂移对）/ `no-equivalent`（表 B）。

| # | 内部名 | 类 / 模块 | 用途一行 | mcp_counterpart_status | MCP 等价物 | 门控 | 处置 |
|---|---|---|---|---|---|---|---|
| 1 | `alpha_compare` | AlphaCompareTool / `alpha_compare_tool` | 精选 Alpha Zoo 因子子集头对头比较 | no-equivalent | — | none | 未暴露（无决策记录） |
| 2 | `analyze_image` | AnalyzeImageTool / `image_vision_tool` | 多模态 LLM 看本地图像答问 | no-equivalent | — | none | 刻意不暴露 |
| 3 | `background_run` † | BackgroundRunTool / `background_tools` | 受跟踪后台进程组运行 shell | no-equivalent | — | `--enable-shell-tools` / `VIBE_TRADING_ENABLE_SHELL_TOOLS=1` | 刻意不暴露 |
| 4 | `bash` † | BashTool / `bash_tool` | 工作目录执行 shell 命令 | no-equivalent | — | 同 #3 | 刻意不暴露 |
| 5 | `cancel_background` † | CancelBackgroundTool / `background_tools` | 按 task_id 取消一个后台任务 | no-equivalent | — | 同 #3 | 刻意不暴露 |
| 6 | `check_background` | CheckBackgroundTool / `background_tools` | 后台任务状态/剩余时间查询 | no-equivalent | — | none | 刻意不暴露 |
| 7 | `compact` | CompactTool / `compact_tool` | 压缩对话历史释放上下文 | no-equivalent | — | none | 刻意不暴露 |
| 8 | `create_hypothesis` | CreateHypothesisTool / `hypothesis_tool` | 创建持久研究假设 | no-equivalent | — | none | 未暴露（无决策记录） |
| 9 | `delete_skill` | DeleteSkillTool / `skill_writer_tool` | 删除用户创建技能及文件 | no-equivalent | — | none | 刻意不暴露 |
| 10 | `edit_file` | EditFileTool / `edit_file_tool` | 文件查找-替换（首个匹配） | **mapped** | `write_file` | none | 已映射（表 A） |
| 11 | `etoro_cancel_close_order` | EtoroCancelCloseOrderTool / `trading_connector_tool` | 取消 eToro 平仓单（paper；live fail-closed） | no-equivalent | — | none | 刻意不暴露 |
| 12 | `etoro_close_position` | EtoroClosePositionTool / `trading_connector_tool` | eToro 平仓/部分平仓 | no-equivalent | — | none | 刻意不暴露 |
| 13 | `etoro_copy_close` | EtoroCopyCloseTool / `trading_connector_tool` | 关闭/解除 eToro 跟单 | no-equivalent | — | none | 刻意不暴露 |
| 14 | `etoro_copy_poll` | EtoroCopyPollTool / `trading_connector_tool` | 轮询异步跟单操作结果 | no-equivalent | — | none | 未暴露（无决策记录） |
| 15 | `etoro_copy_precheck` | EtoroCopyPrecheckTool / `trading_connector_tool` | 跟单可行性干跑 | no-equivalent | — | none | 未暴露（无决策记录） |
| 16 | `etoro_copy_start` | EtoroCopyStartTool / `trading_connector_tool` | 开始跟单/调整跟单配置 | no-equivalent | — | none | 刻意不暴露 |
| 17 | `etoro_edit_position_stops` | EtoroEditPositionStopsTool / `trading_connector_tool` | 修改 eToro 止损/止盈（paper；live fail-closed） | no-equivalent | — | none | 刻意不暴露 |
| 18 | `etoro_search_instruments` | EtoroSearchInstrumentsTool / `trading_connector_tool` | 搜索 eToro 标的 | no-equivalent | — | none | 未暴露（无决策记录） |
| 19 | `financial_rigor` | FinancialRigorTool / `financial_rigor_tool` | 精确十进制算术校验财务数据 | `financial_rigor`（DEC-3 同名镜像） | — | none | ✅ 已暴露（eebf48af） |
| 20 | `generate_backtest_config` | GenerateBacktestConfigTool / `autopilot_tool` | 从假设生成回测 config.json | no-equivalent | — | none | 未暴露（无决策记录） |
| 21 | `get_taiwan_stock_data` † | TaiwanStockDataTool / `taiwan_stock_data_tool` | 本地台股快照查询（TWSE/TPEx） | no-equivalent | — | env `VIBE_TW_STOCK_DB`（schema 合法 SQLite） | 刻意不暴露 |
| 22 | `link_autopilot_backtest` | LinkAutopilotBacktestTool / `autopilot_tool` | 读 run_card.json 指标并关联假设 | no-equivalent | — | none | 未暴露（无决策记录） |
| 23 | `link_backtest` | LinkBacktestTool / `hypothesis_tool` | run card/运行目录挂到假设 | no-equivalent | — | none | 未暴露（无决策记录） |
| 24 | `options_payoff` | OptionsPayoffTool / `options_payoff_tool` | 多腿期权到期损益 + 情景分析 | **mapped** | `analyze_options_payoff` | none | 已映射（表 A） |
| 25 | `options_pricing` | OptionsPricingTool / `options_pricing_tool` | BS 期权定价与 Greeks | **mapped** | `analyze_options` | none | 已映射（表 A） |
| 26 | `patch_skill` | PatchSkillTool / `skill_writer_tool` | 文本替换修复/更新技能 | no-equivalent | — | none | 刻意不暴露 |
| 27 | `pattern` | PatternTool / `pattern_tool` | 回测数据图形形态检测 | **mapped** | `pattern_recognition` | none | 已映射（表 A） |
| 28 | `portfolio_risk_xray` | PortfolioRiskXrayTool / `portfolio_risk_tool` | 组合集中度/波动/回撤/VaR X 光 | no-equivalent | — | none | 未暴露（无决策记录） |
| 29 | `portfolio_summary` | PortfolioSummaryTool / `portfolio_tool` | 本地只读券商账户脱敏快照 | no-equivalent | — | none | 未暴露（无决策记录） |
| 30 | `propose_mandate_profiles` | ProposeMandateProfilesTool / `propose_mandate_tool` | 提出有界自主实盘 mandate 档案 | no-equivalent | — | none | 刻意不暴露 |
| 31 | `remember` | RememberTool / `remember_tool` | 持久化跨会话记忆 | no-equivalent | — | none | 刻意不暴露 |
| 32 | `report_audit` | ReportAuditTool / `report_audit_tool` | 研究报告数字准确性审计 | `report_audit`（DEC-3 同名镜像） | — | none | ✅ 已暴露（eebf48af） |
| 33 | `run_research_autopilot` | RunResearchAutopilotTool / `autopilot_tool` | 从假设启动研究目标 | no-equivalent | — | none | 未暴露（无决策记录） |
| 34 | `save_skill` | SaveSkillTool / `skill_writer_tool` | 保存工作流为可复用技能 | no-equivalent | — | none | 刻意不暴露 |
| 35 | `scaffold_signal_engine` | ScaffoldSignalEngineTool / `autopilot_tool` | 为假设写 signal_engine.py 桩 | no-equivalent | — | none | 未暴露（无决策记录） |
| 36 | `scheduled_research` | ScheduledResearchTool / `scheduled_research_tool` | 计划研究检视与提案准备 | no-equivalent | — | none | 刻意不暴露 |
| 37 | `sdm_decay_scan` | SdmDecayScanTool / `sdm_decay_scan_tool` | 活跃因子/策略衰减扫描 | 无 | — | none | 刻意不暴露（DEC-4） |
| 38 | `sdm_register` | SdmRegisterTool / `sdm_register_tool` | 论文因子/策略注册进策略库 | 无 | — | none | 刻意不暴露（DEC-4） |
| 39 | `sdm_status` | SdmStatusTool / `sdm_status_tool` | 策略库生命周期状态查询/更新 | 读侧：strategy-discovery 三件套 | — | none | 刻意不暴露（DEC-4；写侧 agent-only） |
| 40 | `search_hypotheses` | SearchHypothesesTool / `hypothesis_tool` | 假设文本/状态搜索 | no-equivalent | — | none | 未暴露（无决策记录） |
| 41 | `session_search` | SessionSearchTool / `session_search_tool` | 过往会话关键词搜索 | no-equivalent | — | none | 刻意不暴露 |
| 42 | `skill_file` | SkillFileTool / `skill_writer_tool` | 技能目录辅助文件管理 | no-equivalent | — | none | 刻意不暴露 |
| 43 | `trading_acc_cash_flow` | TradingAccCashFlowTool / `trading_connector_tool` | 账户清算日资金流水读取 | no-equivalent | — | none | 未暴露（无决策记录） |
| 44 | `trading_cancel_order` | TradingCancelOrderTool / `trading_connector_tool` | 按 order id 撤单 | no-equivalent | — | none | 刻意不暴露 |
| 45 | `trading_capital_distribution` | TradingCapitalDistributionTool / `trading_connector_tool` | 标的今日资金进出分桶快照 | no-equivalent | — | none | 未暴露（无决策记录） |
| 46 | `trading_capital_flow` | TradingCapitalFlowTool / `trading_connector_tool` | 标的历史资金流时序 | no-equivalent | — | none | 未暴露（无决策记录） |
| 47 | `trading_earnings_calendar` | TradingEarningsCalendarTool / `trading_connector_tool` | 美/港财报日历（预期/IV） | no-equivalent | — | none | 未暴露（无决策记录） |
| 48 | `trading_financials` | TradingFinancialsTool / `trading_connector_tool` | 连接器视角标的财报读取 | no-equivalent | — | none | 未暴露（无决策记录） |
| 49 | `trading_history_deals` | TradingHistoryDealsTool / `trading_connector_tool` | 历史 FILL 记录（影子账户成本） | no-equivalent | — | none | 未暴露（无决策记录） |
| 50 | `trading_place_order` | TradingPlaceOrderTool / `trading_connector_tool` | 经所选连接器下单 | no-equivalent | — | none | 刻意不暴露 |
| 51 | `trading_rehab` | TradingRehabTool / `trading_connector_tool` | 分红/拆股/配股复权因子 | no-equivalent | — | none | 未暴露（无决策记录） |
| 52 | `update_hypothesis` | UpdateHypothesisTool / `hypothesis_tool` | 假设生命周期/失效备注更新 | no-equivalent | — | none | 未暴露（无决策记录） |

> † = 仅审计条目（AUDIT §2 列出，keyless 环境不注册；`registered_keyless: false`）。其余 48 行均 `registered_keyless: true`。
> 模块前缀均为 `src.tools.`（例：`alpha_compare_tool` = `src.tools.alpha_compare_tool`）。

---

## 4. 下游消费指引（D3 / F2 / F4）

1. **PLAN-D3（swarm 白名单移植映射工程化）**：30 个 preset 的 per-agent `tools:` 硬白名单引用了表 A 全部 4 个内部名与表 B 的 `bash`（30 preset 全量）、`financial_rigor`、`options_*`、`pattern` 等。基于 MCP 面的子代理在加载白名单时必须经**表 A 做运行时名转换**（内部名→MCP 名），并对**表 B 行做可达性裁决**：刻意不暴露项从白名单剔除并在角色 prompt 中声明能力缺口（AUDIT §8.1 子代理草案已按此原则用 74 MCP 工具重写白名单）；
2. **PLAN-F2（`financial_rigor` / `report_audit` 暴露评估）**：表 B §2.6 给出两工具的唯一在案替代（`quantlib_call` + 提示词约束 / 提示词自检，与 AUDIT §8.1 逐字一致）。暴露与否的成本收益评估属 F2（P1），本文不预设结论；
3. **PLAN-F4（`sdm_*` 与 MCP 面策略统一）**：表 B §2.7 记录现状（仅内置运行时）。"暴露 / 文档降级 / 技能改写"三选一的决策属 F4（P2）；在此之前，strategy-dev-manager 技能文档已加注可达性声明（TASK-A6 技能统一的一部分，见 §5.1）；
4. **路由层**：AUDIT §8.2 撞名仲裁表"内部名 × MCP 名"行的裁决规则维持不变——**一律以 MCP 名为准**，本文是其唯一权威映射来源。

---

## 5. 验证（grep 命令与结果）

以下全部命令于 2026-08-26 在仓库根目录执行（工作目录 `/Users/mgong/LegoNanoBot/Vibe-Trading`）。

### 5.1 技能文档统一后的残留扫描（断言：命中处必须为映射标注格式）

**命令**：

```bash
grep -rn -E 'options_pricing|options_payoff|edit_file|sdm_register|sdm_status|sdm_decay_scan|financial_rigor|report_audit' agent/src/skills/ \
  | grep -v -E 'agent/src/skills/(sec-edgar-fetch|sec-edgar|edgar-sec-filings|sentiment-analysis)/' \
  | grep -v -E 'templates/(decay_report\.md|strategy_signal_engine\.py|factor_signal_engine\.py)'
```

**结果**：全部命中均为以下三类之一（无一处裸内部名引用）：

1. **漂移名已替换为 MCP 名 + 括号注内部名**（格式如 `analyze_options`（内部名 `options_pricing`））：
   - `options-strategy/SKILL.md` L167/L170（`options_pricing` → `analyze_options`）
   - `options-payoff/SKILL.md` L21（`options_payoff` → `analyze_options_payoff`）
   - `strategy-generate/SKILL.md` L15、`strategy-generate/examples.md` L17/L33/L49、`backtest-diagnose/SKILL.md` L18/L71（`edit_file` → `write_file`，并保留查找-替换 vs 整文件写入的语义差异说明）
2. **无等价物内部工具加"内部工具，不在 MCP 面；替代：…"标注**（替代方案与表 B / AUDIT §8.1 一致）：
   - `financial_rigor`：thesis-tracker L33/L118、bottleneck-hunter L4（frontmatter description）/L94/L100、data-routing L147、management-deep-dive L101、deep-company-series L108/L147、research-discipline L29
   - `report_audit`：bottleneck-hunter L141、private-company-research L140、management-deep-dive L183、deep-company-series L107/L114/L147、research-discipline L30
3. **`sdm_*` 保留内部名（无 MCP 名可替换），所在文档均已加显式可达性声明**（"仅在内置 agent 运行时可用，MCP 客户端不可达"）：
   - `strategy-dev-manager/SKILL.md`（文首 L9 + Tool Reference 表后 L157 双处声明）
   - `strategy-dev-manager/examples.md`（L3）
   - `strategy-dev-manager/references/scheduled_decay_scan.md`（L3）、`decay_thresholds.md`（L3）、`strategy_metrics.md`（L3）、`strategy_extraction_guide.md`（L3）
   - 声明覆盖后，文档正文的 `sdm_*(...)` 调用示例（代码块/行内调用）按原样保留——它们是工作流调用样例而非可达性引导，且该三工具无 MCP 名。

**补充断言**（排除标注行后，无标注标记的残留行全部位于 strategy-dev-manager 的声明覆盖文档内）：

```bash
grep -rn -E 'options_pricing|options_payoff|edit_file|sdm_register|sdm_status|sdm_decay_scan|financial_rigor|report_audit' agent/src/skills/ \
  | grep -v -E 'agent/src/skills/(sec-edgar-fetch|sec-edgar|edgar-sec-filings|sentiment-analysis)/' \
  | grep -v -E 'templates/(decay_report\.md|strategy_signal_engine\.py|factor_signal_engine\.py)' \
  | grep -v -E '内部|internal'
```

结果：仅剩 `strategy-dev-manager/` 6 个文档中的 `sdm_*` 调用示例与声明行本身（均已被各文档文首声明覆盖）；`options_*`/`edit_file`/`financial_rigor`/`report_audit` 四个名字**零残留**未标注命中。

### 5.2 歧义命中复核（"pattern" 普通英文用词，reviewed-and-left）

**命令**：

```bash
grep -rn '`pattern`' agent/src/skills/
# 结果：无任何匹配（全部技能文档中不存在反引号包裹的 `pattern` 工具引用）
```

F1 产物的 `skill_refs` 将 11 个技能列为引用了 `pattern`（candlestick、data-routing、earnings-revision、factor-research、geopolitical-risk、harmonic、perp-funding-basis、quant-statistics、social-media-intelligence、trade-journal、us-etf-flow）。逐条人工复核这些技能中的 "pattern" 出现，**全部为普通英文用词而非工具引用**，故保留原样（reviewed-and-left）。代表性命中：

| 技能 | 代表行 | 判定 |
|---|---|---|
| candlestick | `\| Pattern \| Signal \| Description \|`（表头） | 普通英文 |
| data-routing | "the runner routes by symbol pattern and falls back" | 普通英文 |
| earnings-revision | "\| Language Pattern \| Interpretation \| Signal \|" | 普通英文 |
| factor-research | "monotonic rising (or falling) pattern" | 普通英文 |
| geopolitical-risk | "Historical pattern:" | 普通英文 |
| harmonic | `def _classify_pattern(...)`（example_signal_engine.py 源码标识符） | 代码标识符 |
| perp-funding-basis | "**Historical pattern statistics (BTC):**" | 普通英文 |
| quant-statistics | "residuals vs fitted values show no obvious pattern" | 普通英文 |
| social-media-intelligence | `re.search(pattern, text_lower)`（正则变量） | 代码标识符 |
| trade-journal | "intraday-heavy pattern" / "Classic disposition pattern" | 普通英文 |
| us-etf-flow | "\| Sector rotation \| … \| Sector flow pattern \|" | 普通英文 |

因此 `pattern` → `pattern_recognition` 的映射在技能文档侧**无需任何替换**（表 1 中 F1 宽扫描的 skill_refs 为词频命中，非工具引用）。

### 5.3 覆盖性断言（表 B/表 C 行集 = F1 全部非 MCP 条目）

**命令**（从 F1 JSON 提取全部 `mcp_counterpart_status != "is-mcp-tool"` 的工具名，逐一断言其在映射表中以反引号形式出现）：

```bash
grep -A2 -E '"mcp_counterpart_status": "(mapped|no-equivalent)"' agent/scripts/artifacts/internal_tool_inventory.json \
  | grep '"name":' | sed 's/.*"name": "\([^"]*\)".*/\1/' > /tmp/vt_internal_names.txt
wc -l /tmp/vt_internal_names.txt   # 52
while IFS= read -r n; do
  [ -z "$n" ] && continue
  if grep -qF "\`$n\`" HARNESS_EVOLUTION_TOOL_MAPPING.md; then pass=$((pass+1)); else echo "FAIL  $n"; fail=$((fail+1)); fi
done < /tmp/vt_internal_names.txt
```

**结果**：

```
52 /tmp/vt_internal_names.txt
total=52 pass=52 fail=0
```

**52/52 全部通过**——F1 产物 `tools` 数组中每一个 `mcp_counterpart_status != "is-mcp-tool"` 的内部工具名（48 内部工具 + 4 仅审计条目）均出现在本文表 B 或表 C 中，无一遗漏。

### 5.4 总量对账复核

```bash
grep -c '"mcp_counterpart_status": "is-mcp-tool"' agent/scripts/artifacts/internal_tool_inventory.json   # 59
grep -c '"mcp_counterpart_status": "mapped"' agent/scripts/artifacts/internal_tool_inventory.json        # 4
grep -c '"mcp_counterpart_status": "no-equivalent"' agent/scripts/artifacts/internal_tool_inventory.json # 48
```

59（同名直通）+ 4（漂移对，表 A）+ 48（无等价，表 B 行集）= 111 条 `tools` 数组条目 = 107 keyless 注册 + 4 仅审计；59 + 15（`mcp_surface_not_in_keyless_registry`）= 74 MCP 面。与 §0.3 恒等式一致。

### 5.5 不触碰清单核验

```bash
grep -rn -E 'options_pricing|options_payoff|edit_file|sdm_register|sdm_status|sdm_decay_scan|financial_rigor|report_audit' \
  agent/src/skills/sec-edgar-fetch/ agent/src/skills/edgar-sec-filings/ agent/src/skills/sentiment-analysis/
# 结果：no hits —— 三个排除目录本就无目标内部名引用，本次亦未做任何改动
```

另：`strategy-dev-manager/templates/` 下 3 个文件（`decay_report.md` 的落款行 "Generated by … sdm_decay_scan"、`strategy_signal_engine.py` / `factor_signal_engine.py` 的生成注释 "Generated by sdm_register"）为**模板渲染产物文本**而非面向读者的工具引用，reviewed-and-left，不在 grep 断言范围内（上述命令已显式排除）。

---

> 本文档为 TASK-A6 证据产物；行集更新时须以 F1 采集协议重跑盘点并同步本文（运行时为权威）。

