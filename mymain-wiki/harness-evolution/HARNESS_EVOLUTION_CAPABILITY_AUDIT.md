# HARNESS 演进研究 — 能力审计与路由基线（Capability Audit）

> 维护者：shadowinlife ｜ 初版：2026-08-25 ｜ 修订：**2026-08-25 v2（审计范围收窄至 vibe-trading 项目本身）** ｜ 状态：**修订定稿**
> 配套文档：`HARNESS_EVOLUTION_RESEARCH.md`（架构决策定稿）· `HARNESS_EVOLUTION_PAPERS.md`（论文索引）· `HARNESS_EVOLUTION_BENCHMARKS.md`（评测基准）
> 本文档定位：HARNESS EVOLUTION 工程的**基础调研文档**——对 vibe-trading 开源项目自身全部 tools / skills / swarm presets 的责任领域聚类、跨域误路由分析、域内重合分析与描述质量审计。

> **审计范围（v2 重要修订）**：本文档只审计 **vibe-trading 项目自身拥有的能力**——其 74 个 MCP 工具、~106 个 agent 内部注册表工具（其中 ~32 个不在 MCP 面）、90 个捆绑技能、30 个 swarm preset。本地会话加载的其他 MCP 服务器/工具/技能（alibaba-code-server、github、NanoSearch、opencode 内置、Context7、Exa、codegraph、grep.app、用户级/系统级技能等，合计 164 工具 + 8 技能）**不在审计范围内**，仅在与 VT 能力发生路由碰撞时以"宿主边界"形式提及（§3 中类型=边界的行）。

**本文档服务三个下游目标**：

1. **领域子代理（subagent）制作**——每个领域"这些工具/技能能解决哪些问题"的白名单输入（§8.1）；
2. **路由模型优化**——跨域仲裁规则、懒加载候选、同名动词消歧表（§8.2）；
3. **描述质量治理**——识别写得差的 description，给出重写/合并/封闭工作清单（§8.3）。

---

## 0. 审计范围、方法与证据基础

### 0.1 审计对象

**路由面（routing surface）= agent 选择能力时实际看到的 description 文本**。vibe-trading 项目自身的路由面分两层：

| 表面 | 规模 | 说明 |
|---|---|---|
| **MCP 工具面** | **74 工具** | `vibe-trading-mcp` 对外暴露；README L1370 枚举 + `test_readme_counts.py` 钉死；与本 session 可见 VT 工具逐一对账一致 |
| **agent 内部注册表** | **~106 工具** | 其中 **~32 个不在 MCP 面**（`options_pricing`、`options_payoff`、`financial_rigor`、`pattern`、`report_audit`、`edit_file`、`bash`、`remember`、`skill_writer`、`sdm_*`、`scheduled_research`、`hypothesis` 等）；**swarm preset 白名单直接引用它们**，故纳入审计（§2 末"内部工具面"小节） |
| **捆绑技能** | **90 个** | `agent/src/skills/`，9 类 category（frontmatter 字段，测试锚定） |
| **swarm preset** | **30 个** | `agent/src/swarm/presets/` YAML；per-agent `tools:` 硬白名单 + `skills:` 软白名单 |

**MCP 面合计 194 个可路由能力单元（74 + 90 + 30）。**

> 对照 `HARNESS_EVOLUTION_PAPERS.md` §F 的学术证据：工具选择准确率在 **25-30 个可见工具后退化、~100 个崩塌**（2605.24660 / 2604.21816）。VT 的 74 个 MCP 工具**单独**已远超退化区间、逼近崩塌阈值；叠加 90 条技能一行描述后为 **164 条竞争描述**——这是本项目路由优化的核心矛盾，也是 RESEARCH.md §8 P0-1"工具面重组"的直接动因。

### 0.2 范围界定（v2 修订）

**在审计范围内**（vibe-trading 项目资产）：

- `agent/mcp_server.py` 的 74 个 MCP 工具（64 个 `@mcp.tool` 包装器 + 10 个镜像注册）；
- `agent/src/tools/` 内部注册表的 ~32 个非 MCP 工具（经 swarm 白名单与源码审计确认）；
- `agent/src/skills/` 的 90 个捆绑技能及其 9 类 category 分类学；
- `agent/src/swarm/presets/` 的 30 个 preset（含 per-agent 白名单）；
- VT 的分发产物 `.opencode/skills/`（90 技能的本地分发副本，ClawHub/opencode 安装产物——双暴露面问题属 VT 自身分发设计，见 K18/Q7）。

**不在审计范围内**（本地会话加载、非 VT 资产）：

| 排除项 | 规模 | 处置 |
|---|---|---|
| alibaba-code-server（Aone） | 60 工具 | 整域移出（原 D20） |
| github MCP + grep_app | 45 工具 | 整域移出（原 D21） |
| opencode 内置（bash/文件/LSP/team/session…） | 43 工具 | 整域移出（原 D22） |
| search_mcp（NanoSearch，新浪源） | 12 工具 | 移出；D01/D02/D03 相应行删除 |
| context7 / codegraph / websearch(Exa) | 4 工具 | 移出 |
| 用户级技能（a1/code-platform/pua/find-skills/code-review-excellence）与系统级技能（3） | 8 技能 | 移出 |

> 排除项与 VT 工具的路由碰撞（如 VT 的 `read_file` 与宿主 `read` 撞名）仍会影响 VT 工具的实际命中率，此类情况保留在 §3 中并标记为**类型=边界**，优化动作只落在 VT 侧描述。

### 0.3 仓库侧结构事实（源码审计，2026-08-25）

- **注册双轨制**：64 个 `@mcp.tool` 手写包装器委托内部注册表执行 + 10 个镜像工具（`_MIRRORED_TOOL_SOURCES`：institutional_holdings / etf_holdings / prediction_market / research_papers / quantlib_call / cashflow_performance / orderbook_depth / sentiment / technical_indicators / get_fundamentals）直接复用工具类 JSON Schema；
- **MCP 工具无 category/group/tag 字段**（fastmcp tags 未使用）——工具面没有现成领域分类；**技能侧有 9 类 category 分类学**（data-source 10 / strategy 19 / analysis 23 / asset-class 9 / crypto 7 / flow 8 / tool 10 / research 3 / risk-analysis 1），可作工具面分类模板；
- **门控不对称**：`get_macro_series`（FRED_API_KEY）/ `iwencai_search`（IWENCAI_KEY）/ `qveris_*`（QVERIS_API_KEY+paid）在 MCP 面**恒注册、调用时失败**（`_execute_key_gated` 错误信封），而 agent 侧缺 key 直接不注册（`check_available()`）；shell 工具永不上 MCP 面；`trading_place_order`/`trading_cancel_order` 结构性排除（`is_readonly=True` 强制）；
- **无 `search_tools`/`activate_tools` 元工具**：74 工具全量静态暴露，无按会话激活；现成抓手是 swarm 侧 `build_swarm_registry` 白名单机制（agent 侧已有会话级激活先例，未接到 MCP 面）；
- **swarm 白名单语义**：`tools:` = 硬白名单（精确工具名，运行时交集强制）；`skills:` = 软白名单（仅 prompt 注入，`load_skill` 不受限，空列表 = 注入全部技能描述）；无全局默认工具集，`[bash, read_file, write_file, load_skill]` 四件套是约定非默认；
- **内部工具面**：agent 注册表 ~106 工具（无 key 时，测试注释口径），~32 个不在 MCP 面；`list_skills` 输出不含 category（数据模型里有）。

### 0.4 方法（六步）

1. **盘点**：74 工具以 README 枚举 + 测试钉死值为准并与 session 对账；技能取 `agent/src/skills/` 90 个；preset 取 `list_swarm_presets` runtime 输出与 YAML 源码；
2. **聚类**：按责任领域建立 19 域分类法（§1），每个能力单元归**唯一主域** + 显式标注跨域链接；
3. **跨域分析**：识别同一意图可命中多个 VT 能力的情况，逐条给出误判路径（§3）；与宿主工具的碰撞标记为"边界"类；
4. **域内分析**：逐域通读 description，识别描述重合组（§4）；
5. **质量审计**：按"过泛/撞名/多职责/缺边界/埋没关键词/名实不符"六类问题分级，产出优化/封闭候选（§5）；
6. **Review + 列扩展**：对照原始 description 校验表格正确性（§6），定义面向路由优化的 v2 列（§7）。

### 0.5 判定原则

- **命中（hit）**：用户意图 → agent 选中正确的 VT 能力。命中依赖三要素：描述关键词覆盖、与竞争能力的边界清晰度、名称可联想性；
- **问题严重度**：按"误路由概率 × 误路由代价"分级。高代价 = 选错后浪费整轮上下文（如加载错误技能全文、发起错误 swarm）；
- 所有 description 引文均为本 session 实际可见文本的原文摘录（§6 抽检依据）。

---

## 1. 责任领域分类法（19 域，VT 范围）

| ID | 领域 | 责任边界 | 工具 | 技能 | Swarm |
|---|---|---|---|---|---|
| D01 | 行情数据与标的解析 | OHLCV/实时行情/订单簿/市场快照/标的代码解析/数据源路由 | 5 | 8 | 0 |
| D02 | 基本面与财报 | 三大报表、财务指标、SEC 文件、公司画像、机构持仓 | 5 | 4 | 0 |
| D03 | 新闻与研报 | 个股新闻、卖方覆盖与一致预期、学术论文 | 3 | 0 | 0 |
| D04 | 资金流与市场微观结构 | 主力/北向/两融/大宗/龙虎榜/解禁/股东户数/订单流指标 | 7 | 3 | 0 |
| D05 | 技术分析 | 指标计算与 9 个流派信号引擎 | 2 | 9 | 1 |
| D06 | 量化因子与策略发现 | 因子库/IC 检验/策略目录/证据门控 | 7 | 6 | 2 |
| D07 | 回测与策略工程 | 回测执行/执行建模/平台导出/诊断 | 3 | 6 | 1 |
| D08 | 期权与衍生品 | BS 定价/多腿 payoff/期权链/funding 与基差 | 3 | 5 | 1 |
| D09 | 风险、统计与组合 | VaR/统计检验/归因/相关性/资产配置/对冲/配对 | 2 | 8 | 4 |
| D10 | 估值与公司深度研究 | 估值模型/投资框架/管理层/深度系列/研究纪律/报告撰写 | 0 | 10 | 3(+2) |
| D11 | 宏观、行业与监管 | 宏观周期/全球宏观/地缘/大宗商品/行业轮动/监管规则 | 2 | 6 | 4 |
| D12 | 事件驱动与特殊情境 | 并购/增减持/ST 预警/跨上市溢价/财报事件/预测市场 | 1 | 6 | 2 |
| D13 | 情绪与另类数据 | 情绪打分/恐贪/社媒/链上/DeFi/稳定币/清算/解锁 | 1 | 7 | 4 |
| D14 | 基金、ETF 与固收 | 基金筛选/ETF 分析/信用债/可转债/股息 | 1 | 5 | 4 |
| D15 | 用户行为与影子账户 | 交割单分析/影子策略提炼/跨市场回测/报告 | 5 | 2 | 0 |
| D16 | 券商连接（只读） | 交易连接器画像/账户/持仓/订单/行情（不下单） | 8 | 0 | 0 |
| D17 | 研究编排与治理 | 研究目标/证据账本/swarm 调度/技能元工具/数据路由 | 13 | 2 | (30 经此调度) |
| D18 | 付费数据市场 | QVeris 搜索/检视/执行 | 3 | 1 | 0 |
| D19 | 网络与文档读取（VT 侧） | VT 自有网页搜索/URL 抓取/PDF 读取 | 3 | 2 | 0 |
| | **合计** | | **74** | **90** | **30** |

> 注：技能列按主域唯一归属计数；跨域能力在 §3 单独登记，不影响此表计数。Swarm "(+2)" 表示跨域 preset 计入其他域。另有 ~32 个内部注册表工具不属 MCP 路由面，见 §2 末小节。

---

## 2. 能力总表（按责任领域聚类，VT 范围）

> 标记说明：`✦Kx` = 涉及跨域误路由，详见 §3 第 x 行；`◆Gx` = 涉及域内重合组，详见 §4 第 x 组；`◆Qx` = 描述质量问题，详见 §5 第 x 项。
> "核心职责"为 description 的忠实压缩（非原文），原文引文见 §3-§5 的逐条证据。

### D01 行情数据与标的解析（5 工具 / 8 技能）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| get_market_data | 工具 | 多市场 OHLCV（yfinance/okx/tushare/baostock/tencent/akshare/ccxt/mt5/auto 等源 + `_provenance`） | "取茅台近一年日线" | ✦K1 |
| screen_market | 工具 | 全市场当日排行（涨跌幅/成交量/成交额/换手） | "今天涨得最多的股票" | ✦K13 ◆Q6 |
| orderbook_depth | 工具 | 加密 L2 订单簿深度 + 失衡 + 模拟市价单冲击成本 | "BTC 盘口/10000U 买入冲击" | |
| search_symbol | 工具 | 名称/代码 → 标的符号（A/HK/US/加/加密/指数 + SEC CIK） | "腾讯的代码" | VT 唯一解析入口 |
| iwencai_search | 工具 | 问财自然语言 A 股选股（需 key） | "市盈率低于 15 的银行股" | ✦K13 ◆Q12 |
| yfinance | 技能 | Yahoo Finance 美/港/加 OHLCV+研究数据，免费 | 脚本内直连美股数据 | ◆G1 |
| akshare | 技能 | AKShare 全品类聚合（A/美/港/期货/宏观/外汇），免费 | tushare/yfinance 的备份 | ◆G1 |
| tushare | 技能 | tushare 财经数据接口（需 token） | A 股财务/高质量面板 | ◆G1 |
| mootdx | 技能 | 通达信 TCP 直连 A 股 OHLCV，免 key 免 IP 限流 | akshare 被限流时的稳定备份 | ◆G1 |
| eastmoney | 技能 | 东财免鉴权接口集（资金流/龙虎榜/两融/报表/选股） | 脚本内直连东财 | ◆G1 |
| ccxt | 技能 | 100+ 加密交易所统一库 | OKX 不可用时的备份 | ◆G1 |
| okx-market | 技能 | OKX V5 REST（现货/衍生品/funding/持仓量） | OKX 数据直连 | ◆G1 |
| minute-analysis | 技能 | 分钟级数据获取与回测输入 | 分钟级回测 | |

> **data-routing**（技能）主域归 D17（路由治理）：它是"所有数据需求先加载"的元路由，与 ✦K1 直接相关。

### D02 基本面与财报（5 工具 / 4 技能）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| get_financial_statements | 工具 | A 股(新浪)/美港(东财→SEC) 报表与关键指标，按期返回 | "茅台利润表/主要指标" | ✦K6 |
| get_fundamentals | 工具 | 美股 PIT 基本面字段面板（SEC XBRL，按公告日对齐） | "美股 ROE/净利面板，防前视" | ✦K5 |
| get_sec_filings | 工具 | SEC filing 列表 / 单个 XBRL 概念序列 | "AAPL 最近 10-K / Revenue 序列" | ✦K5 |
| get_stock_profile | 工具 | 美/港公司画像（估值统计/分析师目标/机构与内部持仓） | "AAPL 分析师目标价" | |
| get_institutional_holdings | 工具 | 13F 机构持仓（经理组合/谁持有某票/经理排名） | "伯克希尔上季度买了什么" | |
| financial-statement | 技能 | 三表解读方法论（勾稽/盈利质量/杜邦/10+ 造假红旗） | "帮我分析这份财报" | ✦K6 |
| sec-edgar | 技能 | EDGAR 抓取接口用法（CIK/filings/companyfacts） | "怎么拉 SEC 数据" | ✦K5 ◆Q2 |
| edgar-sec-filings | 技能 | SEC filing 分析方法论（风险因子/管理层讨论/信号） | "从 10-K 提取投资信号" | ✦K5 ◆Q2 |
| fundamental-filter | 技能 | PE/PB/ROE/财务字段选股过滤（A 股与美港） | "低估值高 ROE 筛选" | ✦K13 |

> A 股定期报告**全文正文**能力（年报原文查证）由宿主侧服务器提供，不在本审计范围（§0.2）；VT 侧结构化取数以 get_financial_statements 为入口。

### D03 新闻与研报（3 工具 / 0 技能）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| get_stock_news | 工具 | 个股新闻（A 股东财 / 美港 Yahoo 真实文章）或全球财经快讯 | "茅台最近新闻" | ✦K17 |
| get_research_reports | 工具 | A 股**个股**卖方覆盖 + 分年 EPS 一致预期（东财+同花顺） | "茅台券商盈利预测" | ✦K4 ◆Q18 |
| research_papers | 工具 | arXiv/OpenAlex 学术论文检索 + 因子简报抽取（证据锚定，未陈述项不推断） | "动量因子的论文" | ✦K4 ◆Q18 |

> 公告全文/行业研报全文/IR 纪要/监管处罚等文本检索能力由宿主侧服务器提供，不在本审计范围；"研报"一词的路由歧义仍存在于 VT 的 get_research_reports 与宿主行业研报工具之间（§3 K4 备注）。

### D04 资金流与市场微观结构（7 工具 / 3 技能）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| get_fund_flow | 工具 | 主力/超大单/大单/中小单净流入（A/港/美，日级或分时） | "主力资金流向" | |
| get_northbound_flow | 工具 | 北向资金实时净流入 + 日度历史（沪/深股通） | "北向今天买了多少" | |
| get_margin_trading | 工具 | 融资融券余额与买入额（A 股，日度） | "融资余额变化" | |
| get_block_trades | 工具 | 大宗交易明细（折溢价 + 买卖席位） | "茅台大宗交易" | |
| get_dragon_tiger | 工具 | 龙虎榜（全市场名单 / 个股席位） | "今天龙虎榜" | |
| get_lockup_expiry | 工具 | 限售解禁历史日程 / 未来解禁日历 | "未来 90 天解禁" | |
| get_shareholder_count | 工具 | 股东户数季度变化 + 户均持股 | "筹码集中度" | |
| hk-connect-flow | 技能 | 互联互通资金流分析方法论（北向/南向/行业配置/套利） | 北向信号解读 | |
| us-etf-flow | 技能 | 美股 ETF 资金流/行业轮动广度/风格因子流 | 机构资金动向 | ✦K15 |
| market-microstructure | 技能 | 价差/订单流毒性 VPIN/Amihud/Roll/集合竞价与大宗机制 | 流动性与微观结构研究 | |

### D05 技术分析（2 工具 / 9 技能 / 1 swarm）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| technical_indicators | 工具 | RSI/MACD/BB/SMA/EMA 指标值计算（Wilder-EWM 口径） | "算一下 AAPL 的 RSI" | ✦K2 ◆Q17 |
| pattern_recognition | 工具 | 头肩/双顶/三角形/楔形/通道识别（需 run_dir OHLCV） | 回测后形态复盘 | ✦K25（内部名 `pattern`） |
| technical-basic | 技能 | 趋势 EMA/ADX + 回归 BB/RSI + 量价 OBV 三维投票复合信号 | "通用技术面信号" | ✦K2 ◆G2 |
| ichimoku | 技能 | 一目均衡表五线系统信号 | 点名"一目均衡"时 | ◆G2 |
| smc | 技能 | 聪明钱概念 BOS/ChoCH/FVG/订单块 | 点名"SMC/ICT"时 | ◆G2 |
| candlestick | 技能 | 15 种 K 线形态识别（纯 pandas） | 点名"K 线形态"时 | ◆G2 |
| elliott-wave | 技能 | 艾略特波浪 5 浪推动/3 浪调整识别 | 点名"波浪理论"时 | ◆G2 |
| harmonic | 技能 | 谐波形态 XABCD（Gartley/Bat/Butterfly/Crab） | 点名"谐波"时 | ◆G2 |
| chanlun | 技能 | 缠论分型/笔/中枢/买卖点（czsc） | 点名"缠论"时 | ◆G2 |
| volatility | 技能 | 历史波动率百分位均值回归策略 | 波动率策略 | ◆G2 ◆Q16 |
| seasonal | 技能 | 月份/星期日历效应策略 | 季节性效应 | ◆G2 |
| technical_analysis_panel | swarm | 6 流派并行（经典 TA+一目+谐波+波浪+SMC）→ 信号聚合 | "多流派技术面共振" | |

### D06 量化因子与策略发现（7 工具 / 6 技能 / 2 swarms）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| alpha_zoo | 工具 | 因子库浏览（Kakushadze101/GTJA191/Qlib158/学术/基本面，元数据查询） | "有哪些因子" | ✦K7 ◆G8 |
| alpha_bench | 工具 | 单因子/整库在 universe 上的 IC/IR 基准评测（含 --strict 同域随机对照） | "沪深 300 上跑因子库" | ◆G8 |
| factor_analysis | 工具 | 因子 IC/IR + 分位分层回测（输入为 CSV） | 自有因子检验 | |
| list_strategies | 工具 | 策略目录浏览（Alpha Zoo + SDM，含证据状态） | "有哪些策略" | ✦K7 ◆G8 |
| query_strategies | 工具 | 证据门控策略查询（市场状态/交易数/成本/Sharpe 过滤，stale fail-closed） | "熊市里哪些策略有证据" | ✦K7 ◆G8 |
| get_strategy_evidence | 工具 | 单策略分市场状态证据行（回测支撑/成本盈亏平衡） | "这个策略的证据" | ◆G8 |
| refresh_strategy_evidence | 工具 | 证据缓存重建（写操作，运维向） | 回测产物入库 | 低频运维 ◆Q13 |
| alpha-zoo | 技能 | 因子库使用指南（与同名工具配对） | | ✦K7 |
| factor-research | 技能 | 因子研究框架（IC/IR/分位/因子组合） | 因子研究方法论 | |
| multi-factor | 技能 | 多因子截面打分 + TopN 组合构建 | 多因子选股 | |
| strategy-discovery | 技能 | 策略发现证据门控机制使用指南 | | ✦K7 |
| strategy-dev-manager | 技能 | 论文/研报 → 因子策略验证入库 + 衰减监控（SDM；内部工具 `sdm_*`） | "把这篇论文变成策略" | ✦K25 |
| ml-strategy | 技能 | sklearn walk-forward 机器学习预测策略 | ML 选股/预测 | |
| factor_research_committee | swarm | 因子挖掘+验证并行 → 组合构建 → 回测评审 | 量化基金因子评审流程 | |
| ml_quant_lab | swarm | 特征工程+模型设计并行 → 样本外严格验证 | ML 量化实验 | |

### D07 回测与策略工程（3 工具 / 6 技能 / 1 swarm）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| backtest | 工具 | 向量化回测（config.json + signal_engine.py，10 市场引擎） | "回测这个策略" | 域核心 |
| write_file | 工具 | 写回测工作区文件（config/signal_engine） | 回测配套写文件 | ✦K22 ◆Q3 |
| read_file | 工具 | 读文件 | 回测产物读取 | ✦K22 ◆Q3 |
| strategy-generate | 技能 | 策略创建/修改/优化 + 回测评估（驱动 backtest 工具） | "写一个动量策略并回测" | |
| backtest-diagnose | 技能 | 回测失败/表现不佳的根因诊断与修复 | "回测为什么这么差" | |
| execution-model | 技能 | 滑点公式/市场冲击/VWAP-TWAP 执行假设 | 回测执行建模 | |
| cross-market-strategy | 技能 | 跨市场组合的 signal_engine 编写 | "A 股+加密组合" | |
| vnpy-export | 技能 | 回测策略 → vnpy CtaTemplate 可运行类 | "导出实盘可用的代码" | |
| pine-script | 技能 | 策略 → TradingView/通达信/同花顺/东财/MT5 代码 | "导出到 TradingView" | |
| quant_strategy_desk | swarm | 选股+因子并行 → 回测 → 风险审计 → 终版报告 | 量化策略全流程 | |

### D08 期权与衍生品（3 工具 / 5 技能 / 1 swarm）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| analyze_options | 工具 | BS 单腿定价 + Greeks | "这个 call 值多少" | ✦K14 ◆G3 ◆Q18 ✦K25（内部名 `options_pricing`） |
| analyze_options_payoff | 工具 | 多腿策略 payoff/盈亏平衡/现货-IV 情景矩阵 | "铁蝶到期损益" | ✦K14 ◆G3 ◆Q18 ✦K25（内部名 `options_payoff`） |
| get_options_chain | 工具 | 美股期权链（bid/ask/OI/IV/到期日列表） | "AAPL 期权链" | ✦K14 |
| options-strategy | 技能 | BS 定价/Greeks/多腿回测框架 | 期权策略方法论 | ✦K14 ◆G3 |
| options-payoff | 技能 | Payoff 图/盈亏平衡/多腿可视化/Greeks 情景方法论 | "画 payoff 图" | ◆G3 撞名 |
| options-advanced | 技能 | 波动率面 SABR/LocalVol/日历价差/波动率套利/做市 | 高级波动率策略 | ◆G3 |
| crypto-derivatives | 技能 | 加密衍生品（funding 套利/期限结构/期权波动率微笑） | 加密衍生品策略 | ✦K11 ◆G3a |
| perp-funding-basis | 技能 | 永续 funding 区间/年化基差/套息构建/跨所套利 | "funding 套利" | ✦K11 ◆G3a |
| derivatives_strategy_desk | swarm | 波动率分析 → 策略设计 → Greeks 风控（顺序链） | 期权交易台工作流 | |

### D09 风险、统计与组合（2 工具 / 8 技能 / 4 swarms）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| quantlib_call | 工具 | 286 个受测金融数学函数（BS/债券/Altman/GARCH/VaR/归因/deflated Sharpe/purged CV/DCF），三步发现（list→describe→call） | "算 VaR / deflated Sharpe" | ✦K16 ◆Q4 |
| cashflow_performance | 工具 | 含申赎现金流组合的 TWR/Dietz/XIRR/MOIC/DPI/TVPI | "有资金进出的真实收益率" | 小众 ◆Q9 |
| risk-analysis | 技能 | VaR/CVaR/最大回撤/蒙特卡洛/EVT/历史情景压力测试 | 风险度量 | ✦K16 |
| quant-statistics | 技能 | ADF 单根/协整/GARCH/回归诊断/Bootstrap/假设检验 | 统计检验 | ✦K8 ◆G4 |
| asset-allocation | 技能 | MPT/Black-Litterman/风险预算/全天候 + 5 优化器 | 资产配置 | |
| hedging-strategy | 技能 | Beta 对冲/期权保护/尾部/跨资产对冲 + 对冲比率 | 对冲设计 | |
| performance-attribution | 技能 | Brinson 行业/选股归因 + 因子 alpha 分解 + 择时 | 业绩归因 | |
| correlation-analysis | 技能 | 相关性/协整/半衰期/Kalman 对冲比率/**配对交易信号生成** | 相关性研究 | ✦K8 ◆G4 ◆Q5 越界 |
| correlation-regime | 技能 | 相关性状态检测（边密度+滞回）+ 危机归因 | "市场状态变了吗" | ✦K8 |
| pair-trading | 技能 | 价差/比率 Z-score 均值回归配对策略（需≥2 标的） | "做配对交易" | ✦K8 ◆G4 |
| risk_committee | swarm | 回撤/尾部/市场状态并行审查 → 风险官签核 | 风险委员会 | |
| portfolio_review_board | swarm | 归因/风险/执行质量并行 → CIO 再平衡决策 | 组合复盘 | |
| pairs_research_lab | swarm | 相关性扫描+协整检验并行 → 配对策略 → 微观结构评审 | 配对研究 | |
| statistical_arbitrage_desk | swarm | 配对扫描+微观结构并行 → 套利策略 → 风控评审 | 统计套利 | |

### D10 估值与公司深度研究（0 工具 / 10 技能 / 3+2 swarms）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| valuation-model | 技能 | DCF/DDM/SOTP + PE-Band/PB-ROE/EV-EBITDA + 敏感性 + 估值陷阱 | "这家公司值多少" | |
| investor-lenses | 技能 | 12 个投资视角（深度价值/质量/反向/GARP/周期/法证做空…） | 叠加分析视角 | |
| management-deep-dive | 技能 | 管理层诚信/能力/治理评估 + 段永平三问 | "管理层靠谱吗" | |
| private-company-research | 技能 | 未上市公司六视角深研（置信度标注/公允价值区间） | "SpaceX 值多少" | |
| deep-company-series | 技能 | 单公司八部出版级深度长文（~12 万字，含事实核查清单） | 出版级深研系列 | 自述与单一研报区分 ✓ |
| thesis-tracker | 技能 | 投资论点维护（假设/红线/锚）+ 季度复检 + 加减仓建议 | "买入后写论点/季报复检" | |
| bottleneck-hunter | 技能 | 超级趋势 → 供应链 L2/L3 瓶颈 → 隐形受益标的 | "AI 基建的隐形赢家" | |
| research-discipline | 技能 | 研究启动前偏差自查（龙头/英语/叙事/确认/近因偏差） | 任何研究任务开场 | 元纪律 |
| behavioral-finance | 技能 | 行为金融应用（过度反应/动量解释/去偏差清单） | 行为面解释 | |
| report-generate | 技能 | 专业研报生成规范（结构/格式/评级/术语） | "写成研报" | |
| investment_committee | swarm | 多空辩论 → 风险评审 → PM 拍板 | 投委会决策 | ✦K12 |
| value_investing_committee | swarm | 巴菲特/芒格/段永平/李录四视角对抗 → 主席综合 | 价值投资深审 | |
| fundamental_research_team | swarm | 财务/估值/质量三维并行 → 买方深研报告 | 基本面深研 | |
| equity_research_team | swarm | 宏观→行业→个股三层 → 研报编辑整合 | 股票研究全流程 | 跨 D11 |
| global_equities_desk | swarm | A 股+港美+加密分析师 → 全球策略师 | 跨市场选股 | 跨 D11/D13 |

### D11 宏观、行业与监管（2 工具 / 6 技能 / 4 swarms）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| get_macro_series | 工具 | FRED 宏观序列（CPI/失业/GDP/联邦基金利率/10Y） | "美国 CPI 走势" | ◆Q12 |
| get_sector_info | 工具 | A 股个股所属板块 / 行业板块当日涨跌排名 | "宁德属于哪些概念" | ✦K19 ◆Q14 |
| macro-analysis | 技能 | 宏观周期定位 + 央行政策解读 → 大类资产倾向 | "现在处于什么周期" | ✦K9 ◆G5 |
| global-macro | 技能 | 全球宏观框架（央行传导/汇率预测/地缘/资本流动） | 全球宏观因子 | ✦K9 ◆G5 |
| geopolitical-risk | 技能 | 地缘危机信号量化/前兆识别/事件驱动策略 | "战争风险怎么交易" | ✦K20 ◆G5 |
| commodity-analysis | 技能 | 大宗商品（油供需/金定价/铜预测/库存/期限结构/季节性） | "铜价怎么看" | ◆G5 |
| sector-rotation | 技能 | 申万行业景气评分/动量排名/产业链传导/多维比较 | "该轮动到哪个行业" | ✦K19 |
| regulatory-knowledge | 技能 | 金融监管知识库（涨跌停/ST 退市/PDT/熔断/加密监管/税务） | "创业板涨跌停规则" | 跨域知识 |
| macro_strategy_forum | swarm | 全球+国内+政策三视角并行 → 首席策略师 | 宏观策略会 | |
| macro_rates_fx_desk | swarm | 利率+汇率+商品通胀 → 宏观 PM | 跨资产宏观台 | |
| geopolitical_war_room | swarm | 地缘+能源冲击+供应链并行 → 应急配置手册 | 地缘危机推演 | |
| commodity_research_team | swarm | 供给+需求并行深研 → 周期策略师 | 商品研究 | |

### D12 事件驱动与特殊情境（1 工具 / 6 技能 / 2 swarms）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| prediction_market | 工具 | Polymarket 事件合约隐含概率（search/event/market/history 四模式；状态与结算分离） | "降息概率多少" | ✦K20 ◆Q15 |
| corporate-events | 技能 | 并购套利价差/大股东增减持/股权激励/定增配股/ST 退市预警 | 公司事件解读 | ◆G6 |
| ashare-pre-st-filter | 技能 | A 股 ST/*ST 风险预测（营收/利润/净资产/分红 + 处罚记录） | "明年会不会被 ST" | ◆G6 |
| adr-hshare | 技能 | ADR/H 股/A 股跨上市溢价追踪与套利信号 | 双重上市溢价 | ◆G6 |
| earnings-forecast | 技能 | 盈利预测与一致预期（自上而下/自下而上/SUE/PEAD/预期修正） | 业绩超预期捕捉 | ✦K10 ◆G6 |
| earnings-revision | 技能 | 盈利预期修正/指引分析/PEAD（US/HK） | 美股预期修正 | ✦K10 ◆G6 |
| event-driven | 技能 | 事件驱动策略（新闻/公告/宏观事件情绪打分，CSV schema） | 事件策略构建 | ◆G6 |
| event_driven_task_force | swarm | 事件扫描 → 影响深析 → 策略构建（顺序链） | 事件驱动特别调查 | |
| earnings_research_desk | swarm | 基本面 + 预期修正 + 期权/事件 → 财报策略师 | 财报季深研 | 跨 D02 |

### D13 情绪与另类数据（1 工具 / 7 技能 / 4 swarms）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| sentiment | 工具 | **双模式**：文本情绪打分（-1..1，本地）+ 加密恐贪指数（免 key） | "给这条新闻打分" | ✦K3 ◆Q1 |
| sentiment-analysis | 技能 | 市场情绪框架（恐贪/PCR/两融/北向 + 社媒舆情量化） | A 股情绪面分析 | ✦K3 |
| social-media-intelligence | 技能 | Twitter/Telegram/Discord/Reddit 金融信号提取 | 社媒情绪因子 | ✦K17 |
| onchain-analysis | 技能 | 链上数据（活跃地址/巨鲸/TVL/DEX + MVRV/NVT/SOPR） | "BTC 链上怎么样" | ✦K11 ◆G7 |
| stablecoin-flow | 技能 | USDT/USDC 铸造销毁/交易所储备/资金轮动指标 | 稳定币流向 | ✦K11 ◆G7 |
| defi-yield | 技能 | DeFi 收益（借贷/LP/质押/farming + 可持续性） | DeFi 收益率比较 | ◆G7 |
| liquidation-heatmap | 技能 | 清算水平/级联/猎止损区/支撑阻力信号 | 清算热力图 | ◆G7 |
| token-unlock-treasury | 技能 | 代币解锁日程/项目财库/抛压预测 | 解锁抛压 | ◆G7 |
| crypto_research_lab | swarm | 链上 + DeFi + 情绪三维并行 → Alpha 综合 | 加密资产研究 | |
| crypto_trading_desk | swarm | funding/基差 + 清算/微观 + 链上流 + 风控（执行导向） | 加密交易台 | |
| sentiment_intelligence_team | swarm | 新闻 + 社媒 + 资金流并行 → 复合情绪打分与反转信号 | 市场情绪情报 | |
| social_alpha_team | swarm | Twitter/Telegram/Reddit 并行 → 社媒情绪因子 | 社媒另类数据 | |

### D14 基金、ETF 与固收（1 工具 / 5 技能 / 4 swarms）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| etf_holdings | 工具 | ETF 穿透（美国 N-PORT 全持仓 / A 股报告披露；holdings/lookup） | "510300 持仓什么" | ✦K15 |
| fund-analysis | 技能 | 基金筛选（晨星/夏普/风格箱/漂移/经理评价/FOF） | 基金筛选 | |
| etf-analysis | 技能 | ETF 筛选/费率/跟踪误差/流动性/量化配置框架 | ETF 选择 | ✦K15 |
| credit-analysis | 技能 | 信用债评级/利差/违约风险/城投债/可转债定价 | 信用债研究 | |
| convertible-bond | 技能 | A 股转债三维估值/下修强赎回售博弈/双低轮动 | 转债策略 | |
| dividend-analysis | 技能 | 股息股（收益质量/派息可持续/除息机制/收益率陷阱） | 红利策略 | |
| fund_selection_panel | swarm | 量化筛选 → Brinson 归因 → FOF 权重优化 | 基金遴选 | |
| etf_allocation_desk | swarm | 筛选 + 宏观配置 + 风险预算 → 组合优化与回测 | ETF 配置 | |
| credit_research_team | swarm | 信用 + 利率 + 行业三维 → 固收策略 | 信用研究 | |
| convertible_bond_team | swarm | 债底/股权期权/内嵌期权三维 → 转债策略 | 转债研究 | |

### D15 用户行为与影子账户（5 工具 / 2 技能）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| analyze_trade_journal | 工具 | 交割单解析（同花顺/东财/富途）+ 4 项行为诊断 | "分析我的交割单" | ◆G9 |
| extract_shadow_strategy | 工具 | 盈利回合 → 3-5 条人话规则（影子账户提炼） | | ◆G9 |
| run_shadow_backtest | 工具 | 影子策略跨市场回测（A/港/美/加密，按结算币种分池）+ 差值归因 | | ◆G9 |
| render_shadow_report | 工具 | 影子账户 8 段 HTML/PDF 报告 | | ◆G9 |
| scan_shadow_signals | 工具 | 当日匹配影子入场节奏的标的扫描 | | ◆G9 |
| shadow-account | 技能 | 影子账户全流程（提炼→回测→归因→报告） | "影子账户" | ◆G9 |
| trade-journal | 技能 | 交割单分析方法论 + 工具指引 | "看看我的交易记录" | ◆G9 |

### D16 券商连接（只读）（8 工具 / 0 技能）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| trading_connections | 工具 | 列出可选交易连接器 profile | **入口：先调这个** | ◆Q10 |
| trading_select_connection | 工具 | 选择默认连接器 profile | | ◆Q10 |
| trading_check | 工具 | 检查连接器配置/可达（绝不下单） | | ◆Q10 |
| trading_account | 工具 | 读账户数据 | | ◆Q10 |
| trading_positions | 工具 | 读持仓 | "我的持仓" | ◆Q10 |
| trading_orders | 工具 | 读当前挂单（+成交） | | ◆Q10 |
| trading_quote | 工具 | 连接器行情快照 | | ◆Q10 ✦K21 |
| trading_history | 工具 | 连接器历史 K 线（IBKR/SDK） | | ✦K21 |

### D17 研究编排与治理（13 工具 / 2 技能）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| start_research_goal | 工具 | 创建研究目标（预算/准则/risk tier；live-trading 结构性拒绝） | "开始一个研究任务" | |
| get_research_goal | 工具 | 读当前目标快照 | | |
| add_goal_evidence | 工具 | 追加可溯源证据（hash/data_as_of/矛盾声明） | 证据记账 | |
| update_research_goal_status | 工具 | 目标生命周期更新（complete 需审计行） | | |
| run_swarm | 工具 | 启动 swarm 多智能体团队（30 preset，DAG，流式进度） | "跑投委会" | ✦K12 |
| get_swarm_status | 工具 | swarm 进度轮询（不阻塞） | | |
| get_run_result | 工具 | swarm 终版报告 + 任务摘要（孤儿 run 自动对账） | | |
| list_runs | 工具 | 近期 run 列表（含 stale 标记） | | |
| retry_run | 工具 | 重试失败/停滞 run（resume 回放失败子图） | | |
| reap_stale_runs | 工具 | 收割孤儿 run（运维） | | 低频运维 ◆Q13 |
| list_swarm_presets | 工具 | 枚举 preset 团队与必填变量 | "有哪些团队" | |
| list_skills | 工具 | 枚举技能（仅 name+description，**不含 category**） | | ✦K18 ◆Q7 ◆Q11 |
| load_skill | 工具 | 加载技能全文（skeleton + section/offset 分页） | | ✦K18 ◆Q7 |
| research-goal | 技能 | 目标驱动研究工作流指引 | | ✦K18 配对 |
| data-routing | 技能 | **数据源单一路由器**（任何数据任务前先加载） | 数据源选择 | ✦K1 元规则 |

### D18 付费数据市场（3 工具 / 1 技能）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| qveris_search | 工具 | 付费能力市场搜索（免费） | "免费源没有的数据" | ◆Q12 |
| qveris_inspect | 工具 | 参数 schema 检视（免费） | | ◆Q12 |
| qveris_execute | 工具 | 付费执行（会话预算管控） | | ◆Q12 |
| qveris | 技能 | 付费市场使用策略（何时升级付费源） | | ◆Q12 |

> 全组依赖 QVERIS_API_KEY + paid mode：无 key 时 MCP 面恒暴露但每次调用必失败——死重披露税（§5 Q12）。

### D19 网络与文档读取（VT 侧）（3 工具 / 2 技能）

| 能力 | 类型 | 核心职责 | 典型触发场景 | 标记 |
|---|---|---|---|---|
| web_search | 工具 | 网页搜索（DDG 优先 + 多引擎兜底，免 key） | 通用搜索 | ✦K23 ◆Q8 |
| read_url | 工具 | Jina Reader URL→Markdown | 读文章/文档页 | ✦K23 |
| read_document | 工具 | 文档读取（PDF/Word/Excel/PPT/图片 OCR，40+ 格式） | 读 PDF/交割单文件 | ✦K24 |
| web-reader | 技能 | 网页阅读指引（**指向 read_url**） | | ✦K23 |
| doc-reader | 技能 | 文档阅读指引（**指向 read_document**） | | ✦K24 |

> 宿主侧另有多个搜索/抓取/阅读工具（百炼/Exa/webfetch/look_at/read 等，§0.2 排除项），与 VT 本域工具的碰撞见 §3 K23/K24（类型=边界）。

### VT 内部工具面（agent 注册表，非 MCP 路由面）

> 深度排查新增小节：以下工具不在 74 MCP 工具之列，但**被 swarm preset 白名单直接引用**或承担项目核心功能，是子代理移植与命名治理的直接对象。名单为源码审计 + README 证据确认的部分清单（完整清单以 `build_registry()` 为准，无 key 时注册表共 ~106 工具）。

| 内部工具 | 用途 | 证据/引用方 | 标记 |
|---|---|---|---|
| options_pricing | BS 定价（MCP 对应物 analyze_options） | investment_committee 白名单（risk_officer） | ✦K25 ◆G10 名实漂移 |
| options_payoff | 多腿 payoff（MCP 对应物 analyze_options_payoff） | investment_committee 白名单 | ✦K25 ◆G10 名实漂移 |
| pattern | 形态识别（MCP 对应物 pattern_recognition） | technical_analysis_panel 白名单（harmonic/wave analyst） | ✦K25 ◆G10 名实漂移 |
| financial_rigor | 财务严谨性校验 | fundamental_research_team 白名单（financial/valuation analyst） | ✦K25 ◆G10 ◆Q19 MCP 无对应物 |
| report_audit | 报告审计（有限 JSON） | 部分 preset 综合角色 | ✦K25 ◆G10 ◆Q19 MCP 无对应物 |
| edit_file | 文件编辑 | quant_strategy_desk 白名单（backtester） | ✦K25 ◆G10 MCP 无对应物 |
| bash / background_run / cancel_background | shell 执行（`--enable-shell-tools` 才入内部注册表；永不上 MCP） | 全部 preset 基础四件套 | 治理正确 ✓ |
| remember | 持久记忆写入 | agent 会话 | 不上 MCP 为刻意设计 |
| skill_writer | 用户技能 CRUD（自进化） | agent 会话 | 不上 MCP 为刻意设计 |
| sdm_register / sdm_status / sdm_decay_scan | SDM 策略生命周期 | strategy-dev-manager 技能引用 | ✦K25 ◆G10 ◆Q19 技能引用了 MCP 面不可达的工具 |
| scheduled_research | 定时研究（propose/confirm 两段式） | agent 会话（2026-08-24 新增） | 内部 |
| hypothesis / run_research_autopilot / scaffold_signal_engine / link_autopilot_backtest | 研究自动驾驶 | agent 会话 | 内部 |
| portfolio_summary / portfolio_risk_xray | 组合快照与风险 X 光 | agent 会话 | 内部 |
| taiwan_stock_data | 台股快照（VIBE_TW_STOCK_DB 门控，仅 agent 侧） | 注册门控 | 内部 |

---

## 3. 跨域能力与误路由风险（inter-domain confusion，VT 范围）

> 判定：同一用户意图**合理命中 ≥2 个 VT 能力**，且 description 未提供仲裁边界。
> **类型**：`项目内` = 碰撞双方均为 VT 能力；`边界` = 碰撞一方为宿主侧工具（§0.2 排除项），优化动作只落在 VT 侧描述。
> 证据列为 description 原文摘录（本 session 可见文本）。

| # | 类型 | 跨域能力组 | 跨域 | 误判路径 | 证据（原文摘录） | 缓解方向 |
|---|---|---|---|---|---|---|
| K1 | 项目内 | 数据源技能群 × get_market_data 的 source 参数 × trading_history | D01×D16 | get_market_data 已内建多源自动路由，但 7 个数据源技能各自描述"免费/备份/直连"，agent 取 A 股数据时可能先加载 tushare/akshare 技能写脚本，而不是直接调工具；反之需要脚本直连时又只调工具。data-routing 技能试图仲裁但自身又增加一跳 | get_market_data: "Prefer ``auto``; ``yahoo``/``yfinance`` serve Canada, US, and HK equities"；mootdx: "Use as the stable A-share OHLCV fallback when akshare's East Money scrape is throttled"；data-routing: "The single ROUTER for every data need. Load this skill BEFORE any backtest, data-fetch, or research task" | 固化 data-routing 为 always-on 元规则；数据源技能描述统一加"何时用技能（脚本直连/特殊接口）vs 何时用 get_market_data（标准 OHLCV）" |
| K2 | 项目内 | technical_indicators 工具 × technical-basic 技能 × 9 个流派技能 | D05 | "算 RSI"应命中工具，"给我技术面信号"应命中技能；但 technical-basic 描述含"RSI"，工具描述含"technical indicators"，双向越界 | technical_indicators: "Compute common technical indicators (RSI, MACD, Bollinger Bands, SMA, EMA)"；technical-basic: "Core technical indicator collection (trend EMA/ADX + mean-reversion BB/RSI + volume-price OBV/volume ratio), generates a composite signal" | 工具描述加"只算指标值，不产生交易信号"；技能描述加"信号/策略层，需要指标值请用 technical_indicators" |
| K3 | 项目内 | sentiment 工具 × sentiment-analysis 技能 | D13 | 工具名与技能名几乎相同。工具=文本打分+加密恐贪（双模式）；技能=A 股情绪框架（含两融/北向）。"分析一下市场情绪"两者都命中，且工具的双模式让"恐贪指数"与"文本打分"互相稀释 | sentiment: "Analyze market sentiment. Two modes: (1) 'sentiment_score' scores arbitrary text… (2) 'fear_greed_index' returns the crypto Fear & Greed Index"；sentiment-analysis: "市场情绪分析——恐贪指数/Put-Call Ratio/融资融券/北向资金信号解读" | 工具更名/描述收窄为"文本情绪打分 + 加密恐贪指数"；技能描述声明"框架性情绪分析，需要单文本打分用 sentiment 工具" |
| K4 | 项目内 | "研报"两层：get_research_reports × research_papers | D03 | 中文"研报"同时覆盖个股卖方覆盖（东财）与学术论文（arXiv）；两个工具描述都含"research reports/papers"语义。（行业研报全文在宿主侧工具，VT 用户说"研报"时本域两工具先竞争） | get_research_reports: "Fetch mainland A-share sell-side research coverage and consensus forecasts"；research_papers: "Search academic finance/ML papers" | 两描述互斥定位：个股卖方预期 / 学术论文；"研报"默认路由到个股卖方（最高频） |
| K5 | 项目内 | 美股财报四重面：get_sec_filings × get_fundamentals × sec-edgar × edgar-sec-filings | D02 | 两个工具（filing 列表/XBRL 序列 vs PIT 面板）+ 两个技能（抓取接口用法 vs 分析方法论），且两个技能名只差词序。"看 AAPL 财报"四者皆似合理 | get_sec_filings: "Fetch U.S. SEC EDGAR filings or reported XBRL financials"；get_fundamentals: "Fetch PIT-safe fundamental fields as daily wide panels"；sec-edgar: "U.S. SEC EDGAR fetch interface — resolve a ticker to its CIK…"；edgar-sec-filings: "SEC EDGAR filing analysis — 10-K, 10-Q, 8-K… generate investment signals" | sec-edgar 更名 sec-edgar-fetch 或并入 edgar-sec-filings；工具描述互指：要序列→get_sec_filings(metric)，要面板→get_fundamentals |
| K6 | 项目内 | A 股财报：get_financial_statements × financial-statement 技能 | D02 | 结构化报表数据（工具）与三表解读方法论（技能）都响应"分析茅台财报"；取数与解读的取舍无人仲裁 | get_financial_statements: "Fetch a stock's financial statements or key per-period indicators"；financial-statement: "财报三表深度解读——三表勾稽关系、盈利质量" | 规则：量化取数→工具；解读框架→技能。技能描述加"配合 get_financial_statements 使用" |
| K7 | 项目内 | 策略发现五重面：list_strategies × query_strategies × alpha_zoo × strategy-discovery × alpha-zoo | D06 | "有什么策略/因子"意图面对 3 工具 + 2 技能；list 与 query 的差异（目录浏览 vs 证据过滤）在名称上不可见；两个同名前缀技能与两个同名前缀工具互相映射 | list_strategies: "List discoverable strategies across the Alpha Zoo and the SDM strategy store"；query_strategies: "Query strategies whose computed per-regime evidence passes the filters"；alpha_zoo: "Browse the bundled Alpha Zoo registry" | 工具描述互指分工；技能描述声明"先读我再用工具"的顺序；长期考虑 list/query 合并为一个带 filter 参数的工具 |
| K8 | 项目内 | 相关性三技能：correlation-analysis × pair-trading × correlation-regime（+quant-statistics） | D09 | correlation-analysis 描述**明文声称**"pair-trading signal generation"，直接踏入 pair-trading 技能领地；quant-statistics 又声称"ADF unit-root / cointegration tests"，与 correlation-analysis 的"Engle-Granger / Johansen cointegration"重复 | correlation-analysis: "…Engle-Granger / Johansen cointegration, half-life, Kalman dynamic hedge ratio, cross-market linkage analysis, and pair-trading signal generation"；pair-trading: "Trades mean reversion using the spread/ratio Z-score of two correlated instruments"；quant-statistics: "ADF unit-root / cointegration tests, GARCH…" | correlation-analysis 删除"pair-trading signal generation"或改为"配对研究输入"；协整检验归 quant-statistics，相关性结构归 correlation-analysis |
| K9 | 项目内 | 宏观双技能：macro-analysis × global-macro | D11 | 两者都覆盖央行政策。边界（国内周期定位 vs 全球汇率/资本流）只在细读描述后可见 | macro-analysis: "Macroeconomic cycle positioning and central-bank policy interpretation… with output in the form of major-asset allocation tilts"；global-macro: "Global macro analysis framework (central bank policy transmission / FX forecasting / geopolitical risk / capital flows)" | 描述首句加市场域标签：macro-analysis="中国/单市场周期"；global-macro="跨市场/汇率/资本流" |
| K10 | 项目内 | 财报预期双技能：earnings-forecast × earnings-revision | D12 | 都做分析师预期。边界是 A 股（forecast）vs US/HK（revision），但 earnings-forecast 描述未显式写"A 股"，earnings-revision 写了"US/HK" | earnings-forecast: "盈利预测与一致预期分析（自上而下/自下而上预测法/SUE/PEAD/分析师预期修正），捕捉业绩超预期交易机会"；earnings-revision: "Earnings estimate revisions, guidance analysis, and post-earnings drift (PEAD)… for US/HK equities" | earnings-forecast 描述显式加"（A 股）"；两者互指市场边界。注意两技能都声称 PEAD——需划界 |
| K11 | 项目内 | 加密另类数据七技能 + 衍生品两技能 | D13×D08 | funding rate 主题同时出现在 crypto-derivatives（"funding-rate arbitrage"）与 perp-funding-basis（"funding rate regimes… carry trade"）；链上主题在 onchain-analysis 与 stablecoin-flow 间部分重叠 | crypto-derivatives: "perpetual funding-rate arbitrage, futures term-structure contango/backwardation trading, and option volatility-smile"；perp-funding-basis: "funding rate regimes, annualized basis signals, carry trade construction, and funding rate arbitrage between exchanges" | crypto-derivatives 定位"综合衍生品框架"，perp-funding-basis 定位"funding/基差专项深潜"；描述互指 |
| K12 | 边界 | run_swarm × 宿主编排工具（task/team 等） | D17 | "多智能体分析茅台"意图在宿主侧有通用编排工具竞争；VT 的 30 个领域 preset 是差异化红利，但 run_swarm 描述未声明"金融研究流程优先走我" | run_swarm: "Run a swarm multi-agent team… e.g., the 'investment_committee' preset runs bull analyst, bear analyst, risk officer, and portfolio manager" | run_swarm/list_swarm_presets 描述首句声明"金融研究多智能体流程的领域入口（30 个金融 preset）"；通用单次派生留给宿主工具 |
| K13 | 项目内 | 选股三重面：iwencai_search × screen_market × fundamental-filter | D01×D02 | "帮我选低估值银行股"：iwencai（自然语言，需 key）、screen_market（仅当日排行，无基本面）、fundamental-filter（技能，驱动 tushare/yfinance）都可能命中；screen_market 描述未声明"无基本面过滤"，最易误选 | iwencai_search: "Run a natural-language A-share research query against iWenCai"；screen_market: "Screen a market's listed instruments and rank the top names by a metric… biggest movers or most-actively-traded"；fundamental-filter: "filter stocks by PE/PB/ROE, financial statement fields" | screen_market 描述补"仅行情排行快照，不做基本面过滤"；选股意图优先 iwencai（有 key）或 fundamental-filter |
| K14 | 项目内 | 期权簇：3 工具 × 3 技能 | D08 | "分析这个期权策略"面对 analyze_options（单腿）、analyze_options_payoff（多腿）、get_options_chain（数据）+ 三个技能；Greeks 关键词出现在全部 6 个描述中 | analyze_options: "Calculate Black-Scholes option price and Greeks"；analyze_options_payoff: "Analyze a multi-leg option strategy's payoff and spot/IV scenarios"；options-strategy: "Black-Scholes pricing, Greeks analysis, and multi-leg backtesting" | 工具描述互指：单腿定价→analyze_options；多腿损益→analyze_options_payoff；市场数据→get_options_chain。技能声明方法论层 |
| K15 | 项目内 | ETF 三重面：etf_holdings × etf-analysis × us-etf-flow | D14×D04 | "分析沪深 300ETF"：持仓穿透（工具）、产品分析方法（技能）、资金流（us-etf-flow，且是美股专属但名称不带市场域）都可能命中 | etf_holdings: "ETF look-through across two markets… holdings… lookup"；etf-analysis: "ETF分析：产品筛选、费率对比、跟踪误差、流动性评估"；us-etf-flow: "US ETF fund flow analysis, sector rotation breadth, and style factor flows" | us-etf-flow 描述已带"US"但中文触发易忽略；etf-analysis 声明"产品层分析，持仓用 etf_holdings，美股资金流用 us-etf-flow" |
| K16 | 项目内 | quantlib_call × 方法论技能群（risk-analysis/quant-statistics/asset-allocation…） | D09×D06 | quantlib_call 实现了多数方法论技能里的数学（VaR/deflated Sharpe/purged CV/DCF），但名称无领域信息；agent 需要"算 VaR"时可能加载 risk-analysis 技能全文才发现应调 quantlib_call——或根本不知道它存在 | quantlib_call: "Run a function from the tested finance-math library… Black-Scholes and implied vol, bond math… VaR/CVaR/EVT… deflated Sharpe and PBO, purged cross-validation… Start with action='list'" | quantlib_call 描述前置高频用例（"算 VaR、deflated Sharpe、DCF 用我"）；方法论技能描述声明"计算走 quantlib_call" |
| K17 | 项目内 | 新闻与社媒：get_stock_news × social-media-intelligence | D03×D13 | "最近有什么关于 XX 的消息"：结构化新闻工具（限东财/Yahoo 源）与社媒信号技能都可能命中；突发消息新闻工具滞后时无 VT 侧兜底声明 | get_stock_news: "Fetch recent financial news headlines… China A-share (SH/SZ/BJ) headlines from Eastmoney; US (.US) and Hong Kong (.HK)… from Yahoo Finance" | 规则：结构化个股新闻→get_stock_news；社媒舆情→social-media-intelligence；get_stock_news 描述补"查无结果时可用 web_search 兜底" |
| K18 | 项目内 | 技能双暴露面：list_skills/load_skill（MCP）× VT 分发到宿主的技能副本 | D17 | **同一套 90 技能**经两个机制暴露：VT 分发把技能安装进宿主技能目录（`.opencode/skills/`，ClawHub/opencode 安装产物），同时 VT MCP 又提供 list_skills/load_skill。已验证两目录名称集合一致、内容字节相同。宿主内两条路径重复加载同一技能；纯 MCP 客户端场景下又只剩 VT 路径 | list_skills: "List all available finance skills with names and descriptions. Use load_skill(name) to get the full documentation" | 分发文档声明单宿主单路径；list_skills 输出补 category 字段（数据已有，见 §0.3）；.opencode/ 副本补同步机制（当前无生成脚本、无 git 跟踪，存在漂移风险） |
| K19 | 项目内 | 行业：get_sector_info × sector-rotation | D11 | 数据（板块归属/当日排名）与方法论（轮动框架）名称相近；"分析行业"意图需先数据后方法，但两者描述无衔接 | get_sector_info: "Look up Chinese A-share sector / concept board info… membership… ranking"；sector-rotation: "行业轮动分析——申万行业景气度评分、行业动量排名" | sector-rotation 描述加"板块数据用 get_sector_info" |
| K20 | 项目内 | 事件预测：prediction_market × geopolitical-risk × event-driven | D12×D11 | "台海冲突概率/降息概率"：预测市场给的是市场隐含概率（结构化），地缘/事件技能给的是分析框架；两者都合理但输出性质完全不同，混用会把框架推测当市场定价 | prediction_market: "READ-ONLY prediction-market (event-contract) data from Polymarket… its price IS the market-implied probability"；geopolitical-risk: "quantify crisis signals, identify precursors" | prediction_market 描述已较好；geopolitical-risk/event-driven 加"市场隐含概率用 prediction_market" |
| K21 | 项目内 | trading_history/trading_quote × get_market_data | D16×D01 | 两者都取 OHLCV/报价。trading_history 走券商连接器（账户视角、可能含实时），get_market_data 走数据源聚合；"取 AAPL 行情"两者都命中 | trading_history: "Read historical bars from the selected trading connector profile"；trading_quote: "Read a quote snapshot from the selected trading connector profile" | 规则：无连接器配置→get_market_data；账户相关/连接器实时→trading_*。trading_* 描述加"需先配置连接器" |
| K22 | 边界 | read_file/write_file × 宿主 read/write 动词对撞 | D07 | 宿主有同名动词的读写工具，作用域/路径约定不同（宿主 read 要绝对路径、VT read_file 相对工作区）；agent 混用导致行为不一致。优化只能落在 VT 侧 | read_file: "Read the contents of a file"；write_file: "Write content to a file. Used to create config.json and signal_engine.py for backtesting workflows" | VT 对描述标注作用域："用于回测 run_dir 工作区（相对路径）"；长期考虑命名空间前缀 |
| K23 | 边界 | web_search × read_url × web-reader 链 × 宿主搜索/抓取工具 | D19 | VT 内部：搜索（web_search）与抓取（read_url）分工清晰但描述互不提及，"搜到之后读哪条"无衔接；外部：宿主侧另有多个搜索引擎与抓取器，VT 工具需在描述中声明自身定位（免 key 兜底） | web_search: "Search the web via DuckDuckGo and return top results"；read_url: "Fetch a web page and convert it to clean Markdown text"；web-reader: "Read web pages… Use the `read_url` tool directly" | web_search 描述补"配合 read_url 读取结果页"并更新引擎现状（实现已是多引擎兜底，描述仍写 DuckDuckGo 单引擎——描述滞后，见 Q8） |
| K24 | 边界 | read_document × doc-reader × 宿主阅读器 | D19 | VT 内部：read_document 是唯一文档读取器，doc-reader 技能正确指向它（无冲突）；边界：宿主 read/look_at 也声称能读 PDF，VT 侧需声明优势（OCR 兜底 + 40+ 格式 + 分页进度） | read_document: "Extract text from a PDF document with OCR fallback for scanned pages"；doc-reader: "Read any common document/data file… Use the `read_document` tool" | read_document 描述补全格式面（当前首句只写 PDF，实际支持 Word/Excel/PPT/图片等 40+ 格式——描述滞后） |
| K25 | 项目内 | 内部工具面 × MCP 面命名漂移：options_pricing/options_payoff/pattern/financial_rigor/report_audit/edit_file/sdm_* | 内部×D05/D06/D08/D10 | 同一能力在内部注册表与 MCP 面名字不同（`pattern` vs `pattern_recognition`、`options_payoff` vs `analyze_options_payoff`）；swarm preset 白名单写内部名，MCP 客户端只见过 MCP 名——子代理移植时白名单直接失效；`financial_rigor`/`report_audit`/`sdm_*` 在 MCP 面**无对应物**，外部子代理无法复现 preset 行为 | swarm preset 白名单原文：risk_officer 的 `options_pricing, options_payoff`；harmonic_analyst 的 `pattern`；financial_analyst 的 `financial_rigor` | 建立内外名称映射表并写入文档；评估把 financial_rigor/report_audit 以只读形式暴露到 MCP（或提供等价替代）；sdm_* 在 strategy-dev-manager 技能中声明"仅限内部运行时" |

---

## 4. 域内重合矩阵（intra-domain overlap，VT 范围）

> 与 §3 的区别：这里是**同一领域内部**描述高度相似、触发词重叠的 VT 能力组——即使路由"选对了领域"，仍要在组内二次选择。

| 组 | 域 | 成员 | 描述重合点（摘录） | 命中影响 | 建议 |
|---|---|---|---|---|---|
| G1 | D01 | yfinance / akshare / tushare / mootdx / eastmoney / ccxt / okx-market（7 技能） | 互相声称备份关系："Primary fallback for tushare and yfinance"（akshare）；"Use as the stable A-share OHLCV fallback when akshare's East Money scrape is throttled"（mootdx）；"Fallback when OKX is unavailable"（ccxt） | 7 个描述构成一张口头降级链，但无单一事实源；agent 需加载多个技能才能拼出路由——data-routing 技能存在但非强制 | 降级链收归 data-routing 单点维护；各源技能描述只写"我能做什么"，不写"我是谁的备份" |
| G2 | D05 | technical-basic / ichimoku / smc / candlestick / elliott-wave / harmonic / chanlun / volatility / seasonal（9 技能） | 同构模板："XX signal engine… generates trading signals"。除流派名外描述几乎不可区分 | 用户不点名流派时无法选择；点名时靠名称精确匹配（命中率尚可）。technical_analysis_panel swarm 的存在说明"全流派并行"才是默认解 | 每个流派技能描述补"适用场景一句话"（如 chanlun="A 股中枢结构"）；通用技术面请求默认路由 technical-basic 或 technical_analysis_panel |
| G3 | D08 | options-strategy / options-payoff / options-advanced（3 技能） | "Greeks"出现在全部三个描述；"multi-leg"出现在 strategy 与 payoff；options-payoff 与工具 analyze_options_payoff 名称撞车 | 期权方法论请求三选一困难 | options-strategy="入门到回测"、options-payoff="损益图与盈亏平衡专项"、options-advanced="波动率面与做市进阶"；payoff 技能与工具描述互指 |
| G3a | D08 | crypto-derivatives × perp-funding-basis | 见 K11（funding 套利主题双写） | 跨 D13 的域内重合 | 见 K11 |
| G4 | D09 | quant-statistics × correlation-analysis × pair-trading | 协整主题三写："ADF unit-root / cointegration tests"（quant-statistics）；"Engle-Granger / Johansen cointegration"（correlation-analysis）；pair-trading 整技能即协整应用 | 协整请求三处命中 | 检验归 quant-statistics、结构分析归 correlation-analysis、策略构建归 pair-trading；删除 correlation-analysis 的越界表述 |
| G5 | D11 | macro-analysis / global-macro / geopolitical-risk / commodity-analysis | macro 双技能见 K9；geopolitical-risk 与 global-macro 都声称地缘（"geopolitical risk"同时出现在两描述） | 宏观请求四选二困难 | 见 K9；global-macro 描述删地缘或改为"地缘的宏观传导"，危机事件本身归 geopolitical-risk |
| G6 | D12 | corporate-events / ashare-pre-st-filter / adr-hshare / earnings-forecast / earnings-revision / event-driven | "事件"语义弥散：ST 预警同时是 corporate-events 的"ST/退市预警"与 ashare-pre-st-filter 的全部职责 | ST 请求双命中；财报事件双命中（K10） | ashare-pre-st-filter 是 ST 专项唯一权威（描述已较清晰）；corporate-events 删除 ST 细化表述只留入口 |
| G7 | D13 | onchain-analysis / stablecoin-flow / defi-yield / liquidation-heatmap / token-unlock-treasury | 链上主题族：stablecoin-flow 的"exchange stablecoin reserves"与 onchain-analysis 的链上数据部分重叠；其余按子域区分尚清晰 | 中等：链上请求可能先命中 onchain-analysis 再发现需要专项技能 | onchain-analysis 描述声明"总览层；稳定币/DeFi/清算/解锁有专项技能" |
| G8 | D06 | alpha_zoo / alpha_bench / list_strategies / query_strategies / get_strategy_evidence / refresh_strategy_evidence | 见 K7；另有命名混淆：alpha_bench（评测）vs alpha_zoo（浏览）仅一词之差 | 高 | 见 K7；refresh_strategy_evidence 移入运维面（Q13） |
| G9 | D15 | analyze_trade_journal / extract_shadow_strategy / run_shadow_backtest / render_shadow_report / scan_shadow_signals + shadow-account / trade-journal 技能 | 5 工具是**严格顺序流水线**（解析→提炼→回测→报告→扫描），但描述各自独立，未声明顺序依赖；入口不明时 agent 可能直接调 run_shadow_backtest 而缺前置 | 中：流水线跳步会报错，但错误信息能自纠 | 5 个工具描述统一加流水线位置（"第 2/5 步，需先 extract…"）；shadow-account 技能声明为总入口 |
| G10 | 内部 | options_pricing / options_payoff / pattern / financial_rigor / report_audit / sdm_*（内部工具面） | 内部名与 MCP 名漂移、preset 白名单引用内部名、部分工具 MCP 面无对应物（详见 K25） | 高（对子代理移植）：外部可见面与 swarm 白名单两套词汇 | 建立映射表；评估缺失对应物工具的 MCP 暴露；技能文档统一使用 MCP 名 + 括号注内部名 |

---

## 5. 描述质量问题清单（优化 / 封闭候选，VT 范围）

> 问题类型：**过泛**（名称/描述覆盖太宽）· **撞名**（与他者名称难区分）· **多职责**（一工具多不相关模式）· **缺边界**（未声明不做什么）· **埋没**（关键词未在描述前部出现）· **名实不符**（描述滞后于实现 / 名称与实现漂移）。
> 严重度 = 误路由概率 × 代价。行动 ∈ {重写描述, 合并, 改名, 封闭/懒加载, 保持}。

| # | 能力 | 问题类型 | 严重度 | 证据/说明 | 建议行动 |
|---|---|---|---|---|---|
| Q1 | sentiment（工具） | 多职责 + 过泛 | **高** | 单工具承载"文本打分"与"加密恐贪指数"两个不相关职责；名称与 sentiment-analysis 技能撞车（K3） | 重写：描述显式双模式分工；长期拆分为两个工具或更名 text_sentiment |
| Q2 | sec-edgar × edgar-sec-filings（技能对） | 撞名 | **高** | 名称只差词序，职责不同（抓取接口 vs 分析方法论）（K5） | sec-edgar 更名 `sec-edgar-fetch`；或两技能合并为"SEC 文件：抓取+分析" |
| Q3 | read_file / write_file | 缺边界 | **高** | 与宿主同名动词工具作用域不同（K22）；write_file 描述已带半句用途（"Used to create config.json and signal_engine.py"）但 read_file 描述仅 "Read the contents of a file"，无任何作用域信息 | 两描述补作用域声明："回测工作区（相对路径）"；read_file 对齐 write_file 的用途句式 |
| Q4 | quantlib_call | 埋没 + 过泛（名称） | **高** | 286 函数的唯一入口但名称无领域信息；"Start with action='list'"的三步发现模式让急用场景（算 VaR）多付两轮；高频用例词（VaR/DCF/Sharpe）在描述中段才出现（K16） | 重写：首句前置"计算 VaR/CVaR、Black-Scholes、deflated Sharpe、purged CV、DCF/可比公司估值——286 个受测金融数学函数的唯一入口" |
| Q5 | correlation-analysis（技能） | 越界声明 | 中 | 描述声称"pair-trading signal generation"，与 pair-trading 技能职责冲突（K8/G4） | 重写：删除或改为"配对交易研究输入（策略构建用 pair-trading）" |
| Q6 | screen_market | 缺边界 | 中 | 描述未声明"仅当日行情排行快照、无基本面过滤"，易被选股意图误选（K13） | 重写：补"不做基本面/多因子过滤（用 iwencai_search 或 fundamental-filter）" |
| Q7 | list_skills / load_skill × 宿主技能面 | 双暴露 | **高** | 同一套 90 技能双路径暴露，.opencode/ 副本无同步机制保证（K18）；list_skills 输出不含 category（数据模型里有） | 分发文档声明单宿主单路径；list_skills 补 category 输出 |
| Q8 | web_search | 名实不符 + 缺衔接 | 中 | 描述写 "Search the web via DuckDuckGo"，但实现已是多引擎顺序兜底（DuckDuckGo→Google→Bing→Brave→Mojeek→Yahoo，README 2026-06-15 条目）；且未提及与 read_url 的衔接（K23） | 重写：更新为"多引擎免 key 网页搜索（DDG 优先，限流自动切换）"；补"结果页用 read_url 读取" |
| Q9 | cashflow_performance | 小众（非缺陷） | 低 | 场景窄（含申赎现金流的收益计量），描述质量本身好 | 保持；描述首句加触发词"基金/组合有资金进出时的真实收益率" |
| Q10 | trading_*（8 工具） | 缺入口顺序 | 中 | 8 工具无调用顺序声明（应先 trading_connections → trading_select_connection）；无连接器配置时全部为死重（K21） | trading_connections 描述标"从这里开始"；其余标"需先选择连接器" |
| Q11 | list_skills 输出 | 信息缺失 | 中 | 只返回 name+description，category 字段存在却未暴露——路由侧拿不到现成分类 | 工程修改：输出补 category |
| Q12 | qveris_* / get_macro_series / iwencai_search | 门控不对称 | 中 | MCP 面恒注册、调用时才因缺 key 失败；无 key 会话中这 5 个工具是纯披露税（每个 ~700 token）（§0.3） | 改为注册时门控（对齐 agent 侧 check_available 语义），或移入 search_tools 懒加载层 |
| Q13 | reap_stale_runs / refresh_strategy_evidence | 运维暴露 | 低 | 运维/缓存重建工具占据研究面披露位 | 封闭出默认面（移入运维工具组或懒加载） |
| Q14 | get_sector_info | 多模式单名 | 低 | membership/ranking 双模式，名称不体现 | 描述补模式说明（已部分有） |
| Q15 | prediction_market | 定位缺失 | 低 | 描述详尽（含状态/结算辨析）但未声明"事件概率查询的首选入口"（K20） | 首句补定位 |
| Q16 | volatility（技能） | 撞名（概念） | 低 | 技能名与普通名词"波动率"相同，波动率概念查询易误加载 | 更名 `volatility-strategy` 或描述首句声明"这是一个策略技能" |
| Q17 | technical_indicators × technical-basic | 层级不分 | 中 | 指标计算（值）与信号生成（判断）两层在描述上互相渗透（K2） | 见 K2 |
| Q18 | 研报两工具（K4）/ 期权簇（K14） | 族内缺互指 | 中 | 同族工具描述各自完整但不互指，族内选择靠细读 | 族内描述统一加"何时用本工具 vs 兄弟姐妹" |
| Q19 | 内部工具面命名（K25/G10） | 名实漂移 | **高**（对移植） | `pattern` vs `pattern_recognition`、`options_payoff` vs `analyze_options_payoff` 等内外双名；`financial_rigor`/`report_audit`/`sdm_*` 无 MCP 对应物；strategy-dev-manager 技能引导的工作流依赖 MCP 面不可达的工具 | 建立内外名称映射表；评估缺失工具的只读暴露；技能文档统一 MCP 名 |

**封闭/懒加载候选汇总**（按收益排序，全部为 VT 侧动作）：
1. **无 key 时的 5 个 key 门控工具**（qveris_* ×3、get_macro_series、iwencai_search）——改注册时门控，立省 ~3.5k token/轮；
2. **无连接器配置时的 8 个 trading_*** ——条件暴露，省 ~5.6k token/轮；
3. **reap_stale_runs、refresh_strategy_evidence** ——运维工具移出研究面，省 ~1.4k token/轮；
4. **技能双暴露关闭一侧**（K18）——opencode 宿主内关闭 MCP 侧 list_skills/load_skill 或反之（省 2 工具 + 消除双路径混淆）。
合计约 **10.5k token/规划轮**（74 工具面 ~52k 披露税的 20%），且全部是"移除无效暴露"，零能力损失。

---

## 6. Review 记录（正确性校验，2026-08-25）

### 6.1 校验方法

1. **计数对账**：工具/技能/preset 计数与三个独立来源交叉验证——本 session function schema 实测、README 枚举（L1370）+ `test_readme_counts.py` 钉死值、`agent/src/skills/` 目录实测；
2. **引文校验**：§3-§5 所有 description 引文逐条对照本 session 可见的 function/skill 原文；
3. **源码审计**（两个并行 explore 代理）：`agent/mcp_server.py` 注册机制、`agent/src/swarm/` 白名单语义、技能目录关系。

### 6.2 校验发现与修正

| # | 发现 | 处理 |
|---|---|---|
| R1 | 初稿手工计数 VT 工具为 73，与 README 钉死的 74 不符 | 逐一对账 74 个官方名单：**本 session 可见 VT 工具与官方 74 完全一致**，73 为手工计数算术错误。已修正 |
| R2 | RESEARCH.md 使用"77 工具（memory ON 时 82）"口径 | 与当前测试钉死值 74 存在基线漂移（记忆类工具与口径变化）。本文以 74 为准并在 §0.1 注明 |
| R3 | 技能双暴露面假设 | **证实**：`.opencode/skills/`（90 个，git 未跟踪、无生成脚本）与 `agent/src/skills/`（90 个）名称集合完全一致、抽查内容字节相同。K18/Q7 成立 |
| R4 | "工具面是否有现成分类" | **证伪**：MCP 工具无 category/group/tag 字段（fastmcp tags 未使用）；分类学只存在于技能侧（9 类）。§1 的 19 域分类法是本文新建的，非仓库既有 |
| R5 | swarm preset 白名单语义 | 证实：`tools:` = 硬白名单（精确工具名，运行时交集强制）；`skills:` = 软白名单（仅 prompt 注入，`load_skill` 不受限，空列表=注入全部技能描述）。§8.1 据此设计 |
| R6 | swarm preset 引用了 MCP 面不存在/不同名的内部工具 | 证实：`options_pricing`、`options_payoff`、`financial_rigor`、`pattern`、`report_audit`、`edit_file` 等在 agent 内部注册表（~106 工具）但不在 74 MCP 工具中。新增 K25/G10/Q19 与 §2"内部工具面"小节 |
| R7 | web_search 描述滞后于实现 | 描述写 DuckDuckGo 单引擎；README 2026-06-15 条目证实实现为六引擎顺序兜底。新增 Q8 |
| R8 | read_document 描述面窄于实现 | 描述首句只写 PDF；README 证实支持 Word/Excel/PPT/图片 OCR 等 40+ 格式。并入 K24 缓解项 |
| R9 | opencode `skill` 工具 available_items 另含 ~19 个内置/共享命令 | 属宿主侧（§0.2 排除项），v2 修订移出审计面，仅备注 |
| R10 | **审计范围修正（v2）** | 用户明确优化对象仅 vibe-trading 项目本身。初稿的 D20（Aone 60 工具）/D21（GitHub 45 工具）/D22（opencode 43 工具）整域移出；D01/D02/D03/D19 中的 NanoSearch/Exa/opencode 行删除；K/G/Q 全量重编号（旧 K1/K26-K29、G8-G13/G16、Q7/Q15 随排除项删除）；与宿主工具碰撞的条目保留为"类型=边界"。总路由面从 366 收窄为 **194**（74+90+30）+ ~32 内部工具 |

### 6.3 诚实性声明（已知局限）

- **"典型触发场景"列是审计者推断**，非遥测数据。上线后应以真实路由日志回填（§7 的"命中失败案例"列即为此预留）；
- **严重度分级是先验判断**（误路由概率 × 代价的定性估计），待 PAPERS.md §F 建议的逐查询评测（BoR/短名单实验）量化；
- 19 域分类法服务于路由与子代理设计，**不是**仓库官方分类（仓库官方只有技能侧 9 类）；
- **边界类条目（K12/K22/K23/K24）**：碰撞对方在审计范围外，VT 侧只能靠自身描述优化缓解，不能根治——根治依赖宿主侧路由规则（§8.2 元规则的接收方是未来路由器/子代理编排层）；
- 内部工具面名单为**部分清单**（源码审计 + README 证据可确认者），完整名单以 `build_registry()` 运行时为准。

---

## 7. 列扩展：从"盘点表"到"路由决策表"（v2）

> 需求 6 的回答：v1 表回答了"有什么、归哪域、和谁撞"，但**不足以直接驱动路由优化与子代理制作**——还缺"什么查询该命中它、什么查询不该、冲突时谁赢、以什么成本暴露"。以下 9 列为 v2 提案。

### 7.1 v2 新增列定义

| 列 | 定义 | 服务对象 | 数据来源 |
|---|---|---|---|
| **触发关键词** | 应命中本能力的用户短语（中英文） | 路由模型训练/评测集构造 | 先验 + 路由日志回填 |
| **负向触发** | 看似相关但**不应**命中的查询 | 精确率（防过度召回） | 先验 + 失败案例 |
| **仲裁规则** | 与竞争能力同时命中时的裁决句 | 路由器硬规则 | §3/§4 的缓解列 |
| **披露层级** | always-on（常驻 ~15 动词）/ on-demand（search_tools 召回）/ lazy（技能式一行目录）/ gated（条件暴露） | 披露税治理（PAPERS §F：懒加载 -95% 工具 token） | §5 封闭候选 + 频率 |
| **子代理归属** | 属于哪个领域子代理的白名单 | 目标 1（子代理制作） | §8.1 |
| **频率先验** | 高/中/低 | 披露层级与评测抽样 | 先验，遥测校正 |
| **描述 token 成本** | description 近似 token 数 | 披露税预算（~700 token/工具中位数） | schema 实测 |
| **质量等级 + 行动** | A（保持）/ B（重写）/ C（合并/封闭） | 目标 3（描述治理） | §5 |
| **命中失败案例** | 真实误路由记录（日期/查询/误选/正解） | 持续回归评测 | 上线后回填 |

### 7.2 v2 扩展表（路由关键子集：§3-§5 标记项）

> 全量 194 行的 v2 化是后续工程（需遥测支撑）；本表先覆盖路由关键子集——跨域/重合/质量标记项。

| 能力 | 域 | 触发关键词 | 负向触发 | 仲裁规则 | 披露层级 | 行动 |
|---|---|---|---|---|---|---|
| get_market_data | D01 | K线/OHLCV/日线/行情数据/取数 | "我的券商账户行情"（→trading_quote） | 标准 OHLCV 一律走本工具（auto 源）；脚本直连才用数据源技能 | always-on | A |
| search_symbol | D01 | 代码是什么/标的解析/找代码 | — | VT 标的解析唯一入口 | always-on | A |
| screen_market | D01 | 今天涨最多/成交最大/排行 | 基本面选股（→iwencai/fundamental-filter） | 仅行情快照排行 | on-demand | B：补边界（Q6） |
| iwencai_search | D01 | 自然语言选股/条件选股 | 非 A 股选股 | A 股自然语言选股首选（有 key） | gated | B：key 门控（Q12） |
| data-routing（技能） | D17 | 选哪个数据源/数据怎么取 | — | 元规则：数据任务前置加载 | always-on（技能目录行） | A：升级为强制前置 |
| 数据源技能群（7） | D01 | 脚本直连 tushare/akshare/OKX… | 标准取数（→get_market_data） | 降级链由 data-routing 单点维护 | lazy | B：删除互相声称的备份表述（G1） |
| get_financial_statements | D02 | 财报/利润表/财务指标 | 财报解读方法（→financial-statement 技能） | 结构化取数入口 | always-on | A（K6 互指） |
| get_fundamentals | D02 | 美股基本面面板/PIT/防前视 | 单期财报（→get_financial_statements） | 美股因子对齐场景 | on-demand | A |
| get_sec_filings | D02 | 10-K/filing/Revenue 序列 | 面板数据（→get_fundamentals） | SEC 原始文件与 XBRL 序列 | on-demand | B：与 get_fundamentals 互指（K5） |
| sec-edgar / edgar-sec-filings（技能） | D02 | EDGAR 怎么抓 / 10-K 怎么分析 | — | 合并或改名 sec-edgar-fetch | lazy | C：合并（Q2） |
| get_stock_news | D03 | 新闻/消息/快讯 | 社媒舆情（→social-media-intelligence） | 结构化个股新闻首选；无结果→web_search | on-demand | B（K17 衔接） |
| get_research_reports / research_papers | D03 | 券商预测/一致预期 / 学术论文 | 行业研报全文（宿主侧） | "研报"默认=个股卖方 | on-demand | B：两层互指（Q18） |
| technical_indicators | D05 | 算 RSI/MACD/指标值 | 交易信号（→technical-basic/流派技能） | 只算值不给信号 | always-on | B：层级声明（Q17） |
| TA 流派技能群（9） | D05 | 点名流派名（缠论/波浪/SMC…） | 通用技术面（→technical-basic） | 点名才加载；默认 technical-basic 或 swarm | lazy | B：各补一句适用场景（G2） |
| alpha_zoo / alpha_bench | D06 | 有哪些因子/因子库评测 | 策略（→list_strategies） | 因子层入口 | on-demand | B：与策略族互指（K7） |
| list_strategies / query_strategies | D06 | 有哪些策略/熊市策略 | 因子（→alpha_zoo） | list=目录，query=证据过滤；长期合并 | on-demand | B（K7） |
| backtest | D07 | 回测/跑一下策略 | 策略编写（→strategy-generate 技能） | 回测执行唯一入口 | always-on | A |
| strategy-generate（技能） | D07 | 写个策略/策略思路 | — | 驱动 backtest 的工作流技能 | lazy | A |
| analyze_options / analyze_options_payoff / get_options_chain | D08 | 期权定价/payoff/期权链 | 方法论（→options 技能） | 单腿→前者；多腿→中者；数据→后者 | on-demand | B：族内互指（Q18） |
| options 技能群（3） | D08 | 期权方法论/损益图/波动率面 | 定价计算（→analyze_options） | 入门/损益专项/进阶三分 | lazy | B（G3） |
| quantlib_call | D09 | VaR/deflated Sharpe/DCF/金融数学 | 方法论学习（→对应技能） | 计算唯一入口；技能声明"计算走我" | always-on | B：前置高频用例（Q4） |
| correlation-analysis（技能） | D09 | 相关性/协整结构 | 配对策略构建（→pair-trading）；纯检验（→quant-statistics） | 删除越界声明 | lazy | B（Q5） |
| pair-trading / quant-statistics（技能） | D09 | 配对策略 / 统计检验 | — | 策略归前者、检验归后者 | lazy | B：协整归属声明（G4） |
| macro-analysis / global-macro（技能） | D11 | 国内周期定位 / 全球汇率资本流 | 地缘危机（→geopolitical-risk） | 首句加市场域标签 | lazy | B（K9） |
| earnings-forecast / earnings-revision（技能） | D12 | A 股业绩预期 / 美港预期修正 | — | 市场边界显式化；PEAD 归属划清 | lazy | B（K10） |
| sentiment（工具） | D13 | 文本情绪打分/加密恐贪 | A 股情绪框架（→sentiment-analysis 技能） | 双模式分工写明 | on-demand | B：重写或拆分（Q1） |
| sentiment-analysis（技能） | D13 | 市场情绪面/两融北向情绪 | 单文本打分（→sentiment 工具） | 框架层 | lazy | B（K3） |
| 加密另类技能群（5） | D13 | 链上/稳定币/DeFi/清算/解锁 | — | onchain-analysis 为总览层 | lazy | B：总览/专项互指（G7） |
| prediction_market | D12 | 事件概率/降息概率/选举概率 | 危机分析框架（→geopolitical-risk） | 市场隐含概率唯一入口 | on-demand | B：补定位（Q15） |
| etf_holdings | D14 | ETF 持仓/穿透 | 产品分析（→etf-analysis）；美股 ETF 资金流（→us-etf-flow） | 持仓数据入口 | on-demand | B：三重互指（K15） |
| shadow 流水线（5 工具） | D15 | 交割单/影子账户 | — | 严格顺序流水线，描述标步骤号 | on-demand | B：标流水线位置（G9） |
| trading_*（8 工具） | D16 | 我的账户/持仓/券商行情 | 市场数据（→get_market_data） | 先 trading_connections；无配置时整组不暴露 | gated | B：入口顺序 + 条件暴露（Q10/Q12） |
| run_swarm / list_swarm_presets | D17 | 投委会/多智能体研究/跑团队 | 单一专项派生（宿主工具） | 金融研究流程先查 preset | always-on | B：领域入口声明（K12） |
| list_skills / load_skill | D17 | 金融技能列表/加载技能全文 | 宿主原生技能面（同套技能） | 单宿主单路径 | gated | C：双暴露治理（Q7） |
| web_search / read_url | D19 | 搜一下/读这个网页 | 新浪域文本（宿主工具） | 搜索→web_search，读页→read_url；描述互指 | on-demand（web_search 可 always） | B（Q8/K23） |
| read_document | D19 | PDF/Word/Excel/文档读取 | 纯文本代码（宿主 read） | VT 文档读取唯一入口；补全格式面 | on-demand | B（K24） |
| 内部工具面（K25 组） | 内部 | —（不直接面向路由） | — | 建立内外名称映射；补缺失 MCP 对应物 | 不适用 | B（Q19） |

---

## 8. 三个下游目标的输入

### 8.1 目标 1：领域子代理（subagent）设计草案

**复用基础（源码审计结论）**：swarm preset 的 `tools:` 是硬白名单（精确工具名、运行时交集强制），可**直接作为子代理工具面**；`skills:` 是软白名单（仅 prompt 注入），作为知识面声明。30 个 preset 的 per-agent 白名单是现成的"角色级"白名单库。

**⚠️ 移植映射表（K25/Q19）**：preset 引用的部分工具只存在于 agent 内部注册表，不在 74 MCP 工具中。基于 MCP 面的子代理只能映射为：

| preset 内部工具 | MCP 面等价物 | 说明 |
|---|---|---|
| options_pricing | analyze_options | BS 定价 |
| options_payoff | analyze_options_payoff | 多腿损益 |
| pattern | pattern_recognition | 形态识别 |
| financial_rigor | （无直接等价）→ quantlib_call + 提示词约束 | 内部财务严谨性校验 |
| report_audit | （无等价）→ 提示词自检 | 报告审计 |
| edit_file | write_file | 文件写入 |
| sdm_register / sdm_status / sdm_decay_scan | （无等价） | SDM 生命周期，仅内部运行时可用 |

**子代理草案（12+1，全部白名单取自 74 MCP 工具）**：

| 子代理 | 域覆盖 | 工具白名单（MCP 面） | 技能白名单 | 对应 swarm |
|---|---|---|---|---|
| market-data-agent | D01+D04 | get_market_data, screen_market, orderbook_depth, search_symbol, iwencai_search, get_fund_flow, get_northbound_flow, get_margin_trading, get_block_trades, get_dragon_tiger, get_lockup_expiry, get_shareholder_count, get_sector_info | data-routing（强制）, 按需源技能 | — |
| fundamentals-text-agent | D02+D03 | get_financial_statements, get_fundamentals, get_sec_filings, get_stock_profile, get_institutional_holdings, get_stock_news, get_research_reports, research_papers | financial-statement, edgar-sec-filings（合并后）, fundamental-filter | earnings_research_desk |
| quant-agent | D06+D07 | alpha_zoo, alpha_bench, factor_analysis, list_strategies, query_strategies, get_strategy_evidence, backtest, write_file, read_file, pattern_recognition, quantlib_call | strategy-generate, factor-research, multi-factor, strategy-discovery, strategy-dev-manager, ml-strategy, backtest-diagnose, execution-model, cross-market-strategy | quant_strategy_desk, factor_research_committee, ml_quant_lab |
| derivatives-agent | D08 | analyze_options, analyze_options_payoff, get_options_chain, get_market_data | options-strategy, options-payoff, options-advanced, crypto-derivatives, perp-funding-basis | derivatives_strategy_desk |
| risk-portfolio-agent | D09 | quantlib_call, cashflow_performance, get_market_data | risk-analysis, quant-statistics, asset-allocation, hedging-strategy, performance-attribution, correlation-analysis, correlation-regime, pair-trading | risk_committee, portfolio_review_board, pairs_research_lab, statistical_arbitrage_desk |
| valuation-agent | D10+D12 | get_financial_statements, get_stock_profile, prediction_market, get_market_data | valuation-model, investor-lenses, management-deep-dive, thesis-tracker, bottleneck-hunter, research-discipline, behavioral-finance, report-generate, corporate-events, ashare-pre-st-filter, adr-hshare, earnings-forecast, earnings-revision, event-driven | investment_committee, value_investing_committee, fundamental_research_team, event_driven_task_force |
| macro-sector-agent | D11 | get_macro_series, get_sector_info, get_market_data | macro-analysis, global-macro, geopolitical-risk, commodity-analysis, sector-rotation, regulatory-knowledge | macro_strategy_forum, macro_rates_fx_desk, geopolitical_war_room, commodity_research_team, sector_rotation_team |
| altdata-agent | D13 | sentiment, prediction_market, get_market_data, orderbook_depth | sentiment-analysis, social-media-intelligence, onchain-analysis, stablecoin-flow, defi-yield, liquidation-heatmap, token-unlock-treasury | crypto_research_lab, crypto_trading_desk, sentiment_intelligence_team, social_alpha_team |
| funds-fi-agent | D14 | etf_holdings, get_market_data, get_financial_statements | fund-analysis, etf-analysis, credit-analysis, convertible-bond, dividend-analysis, us-etf-flow | fund_selection_panel, etf_allocation_desk, credit_research_team, convertible_bond_team |
| user-analytics-agent | D15 | analyze_trade_journal, extract_shadow_strategy, run_shadow_backtest, render_shadow_report, scan_shadow_signals | shadow-account, trade-journal | — |
| web-docs-agent | D19 | web_search, read_url, read_document | web-reader, doc-reader | — |
| trading-connector-agent | D16 | trading_*（8，条件暴露） | — | — |
| **orchestrator（路由层）** | D17 | start_research_goal, get_research_goal, add_goal_evidence, update_research_goal_status, list_swarm_presets, run_swarm, get_swarm_status, get_run_result, list_runs, retry_run | research-goal, data-routing | 调度全部 30 preset |

> 子代理工具面平均 ~13 个——正好落在 PAPERS §F"平均呈现 7 个工具即接近 50 个的覆盖率"的舒适区。technical_analysis_panel 归入 market-data-agent 的 swarm 触发面。代码平台/通用编码能力属宿主侧（§0.2 排除项），不设 VT 子代理。

### 8.2 目标 2：路由模型优化输入

**输入 1——撞名/动词表（必须硬编码仲裁，VT 侧）**：

| 动词/概念 | 冲突面 | 仲裁规则 |
|---|---|---|
| read_file / write_file × 宿主 read/write | 边界 | 回测工作区相对路径→VT 对；VT 描述声明作用域（Q3） |
| web_search × 宿主搜索引擎 | 边界 | 中文财经/宿主专属源→宿主；免 key 兜底→VT web_search |
| read_url × 宿主抓取器 | 边界 | 保留 1 个主抓取器；VT 描述声明"配合 web_search" |
| read_document × 宿主阅读器 | 边界 | 扫描件/多格式文档→VT（OCR+40 格式）；VT 描述补全格式面 |
| sentiment 工具 / 技能 | 项目内 | 单文本打分→工具；框架分析→技能 |
| correlation / pair / cointegration | 项目内 | 检验→quant-statistics；结构→correlation-analysis；策略→pair-trading |
| 研报（两层）| 项目内 | 个股卖方→get_research_reports；学术→research_papers |
| macro ×2 / earnings ×2 | 项目内 | 按市场域：国内/全球；A 股/美港 |
| run_swarm × 宿主编排 | 边界 | 金融研究流程→run_swarm preset；通用单次派生→宿主 |
| trading_history × get_market_data | 项目内 | 无连接器→get_market_data；账户视角→trading_* |
| 内部名 × MCP 名 | 项目内 | 一律以 MCP 名为准（映射表见 §8.1） |

**输入 2——披露层级提案**（对齐 RESEARCH.md §8 P0-1"12-15 常驻动词 + search_tools 元工具"）：

- **always-on（建议 12）**：get_market_data, search_symbol, get_financial_statements, get_stock_news, get_research_reports, technical_indicators, backtest, quantlib_call, run_swarm, list_swarm_presets, start_research_goal, web_search；
- **gated（条件暴露）**：qveris_*（有 key）、iwencai_search（有 key）、get_macro_series（有 key）、trading_*（有连接器）、list_skills/load_skill（宿主已有技能面时关闭）；
- **on-demand**：其余 ~45 工具经 `search_tools` 元工具召回（**待建**，本文 §7.2 的触发关键词列即其索引语料）；
- **lazy（一行目录）**：90 技能维持现状（渐进披露已验证有效）。

**输入 3——路由元规则**（写入路由器 system 层）：
1. 任何数据任务前先查 data-routing（或将其规则编译进路由器）；
2. 金融研究流程类请求先查 list_swarm_presets 再决定宿主通用派生；
3. 标的解析只走 search_symbol；
4. 同族工具按 §7.2 仲裁规则裁决，禁止"都试试"；
5. 引用内部工具名（pattern/options_payoff 等）的上下文一律经映射表转 MCP 名。

### 8.3 目标 3：描述优化工作清单（按优先级）

**P0（高严重度，立即做）**：

| 工作项 | 对应 | 验收标准 |
|---|---|---|
| sentiment 工具描述重写（双模式分工） | Q1/K3 | 描述含两个模式各自的触发场景；与 sentiment-analysis 技能互指 |
| sec-edgar / edgar-sec-filings 合并或改名 | Q2/K5 | 名称可一眼区分抓取与分析 |
| read_file / write_file 作用域声明 | Q3/K22 | 两描述各含一句作用域（回测工作区/相对路径） |
| quantlib_call 首句前置高频用例 | Q4/K16 | 首 50 token 内含 VaR/DCF/Sharpe 关键词 |
| 技能双暴露治理决策 | Q7/K18 | 单宿主单路径成文；.opencode/ 副本同步机制确认 |
| 内部工具面命名治理 | Q19/K25/G10 | 内外名称映射表落文档；financial_rigor/report_audit 的 MCP 暴露评估有结论 |

**P1（中严重度）**：correlation-analysis 删越界声明（Q5）；screen_market 补边界（Q6）；trading_* 入口顺序（Q10）；list_skills 输出补 category（Q11）；key 门控工具改注册时门控（Q12）；web_search 描述更新多引擎现状+衔接（Q8）；technical 层级声明（Q17）；族内互指（Q18：研报两工具/期权簇/策略发现族）。

**P2（低严重度/观察）**：cashflow_performance 触发词（Q9）；运维工具移出研究面（Q13）；get_sector_info 模式说明（Q14）；prediction_market 定位（Q15）；volatility 更名评估（Q16）；TA 流派技能各补适用场景（G2）；shadow 流水线标步骤号（G9）；数据源技能删互称备份表述（G1）。

**封闭/懒加载执行清单**（收益 = 每规划轮节省的披露税，全部 VT 侧）：
1. qveris_* ×3 + get_macro_series + iwencai_search 无 key 时不注册 —— 省 ~3.5k token/轮；
2. trading_* ×8 无连接器时不注册 —— 省 ~5.6k token/轮；
3. reap_stale_runs + refresh_strategy_evidence 移出默认面 —— 省 ~1.4k token/轮；
4. 技能双暴露关闭一侧 —— 省 2 工具 + 消除混淆。
合计约 **10.5k token/规划轮**（74 工具面 ~52k 披露税的 20%），零能力损失。

---

## 附录 A：30 个 swarm preset 全清单（域归属）

| Preset | 域 | agents | 必填变量 | 一行描述（runtime 视图） |
|---|---|---|---|---|
| investment_committee | D10 | 4 | target, market | 多空辩论 → 风险评审 → PM 拍板 |
| value_investing_committee | D10 | 5 | company, market | 巴菲特/芒格/段永平/李录四视角对抗 → 主席综合 |
| fundamental_research_team | D10 | 4 | target, market | 财务/估值/质量三维并行 → 买方深研报告 |
| equity_research_team | D10×D11 | 4 | market, goal | 宏观→行业→个股三层 → 研报编辑 |
| global_equities_desk | D10×D11 | 4 | goal | A 股+港美+加密分析师 → 全球策略师 |
| global_allocation_committee | D10×D11 | 4 | goal | A 股+加密+港美并行 → 数据驱动跨市场配置 |
| earnings_research_desk | D12×D02 | 4 | target | 基本面+预期修正+期权/事件 → 财报策略师 |
| event_driven_task_force | D12 | 3 | market | 事件扫描 → 影响深析 → 策略构建 |
| quant_strategy_desk | D07 | 5 | market, goal | 选股+因子并行 → 回测 → 风险审计 → 终版报告 |
| factor_research_committee | D06 | 4 | market, factor_type | 因子挖掘+验证并行 → 组合构建 → 回测评审 |
| ml_quant_lab | D06 | 3 | market, target_variable, goal | 特征工程+模型设计并行 → 样本外验证 |
| technical_analysis_panel | D05 | 6 | target, timeframe | 6 流派并行 → 信号聚合评分 |
| pairs_research_lab | D09 | 4 | market | 相关性扫描+协整并行 → 配对策略 → 微观评审 |
| statistical_arbitrage_desk | D09 | 4 | market, goal | 配对扫描+微观并行 → 套利策略 → 风控 |
| risk_committee | D09 | 4 | goal | 回撤/尾部/市场状态并行 → 风险官签核 |
| portfolio_review_board | D09 | 4 | portfolio, review_period, goal | 归因/风险/执行并行 → CIO 再平衡 |
| derivatives_strategy_desk | D08 | 3 | target, view | 波动率 → 策略设计 → Greeks 风控 |
| crypto_research_lab | D13 | 4 | target, timeframe | 链上+DeFi+情绪并行 → Alpha 综合 |
| crypto_trading_desk | D13 | 4 | target, timeframe | funding/基差+清算/微观+链上流+风控（执行向） |
| sentiment_intelligence_team | D13 | 4 | market, timeframe | 新闻+社媒+资金流并行 → 复合情绪信号 |
| social_alpha_team | D13 | 4 | target, timeframe | Twitter/Telegram/Reddit 并行 → 社媒因子 |
| macro_strategy_forum | D11 | 4 | market, horizon | 全球+国内+政策并行 → 首席策略师 |
| macro_rates_fx_desk | D11 | 4 | goal, timeframe | 利率+汇率+商品通胀 → 宏观 PM |
| geopolitical_war_room | D11 | 4 | crisis, market | 地缘+能源+供应链并行 → 应急配置手册 |
| commodity_research_team | D11 | 3 | commodity, horizon | 供给+需求并行 → 周期策略师 |
| sector_rotation_team | D11 | 4 | market, goal | 周期+景气+资金流并行 → 轮动策略回测 |
| fund_selection_panel | D14 | 3 | fund_type, goal | 量化筛选 → Brinson 归因 → FOF 优化 |
| etf_allocation_desk | D14 | 4 | risk_profile | 筛选+宏观配置+风险预算 → 组合优化回测 |
| credit_research_team | D14 | 4 | target | 信用+利率+行业三维 → 固收策略 |
| convertible_bond_team | D14 | 4 | market, goal | 债底/股权期权/内嵌期权三维 → 转债策略 |

## 附录 B：盘点统计对账（v2 修订）

| 口径 | 数值 | 来源 |
|---|---|---|
| VT MCP 工具 | **74** | README L1370 + test_readme_counts.py 钉死；与本 session 逐一对账一致（64 @mcp.tool + 10 镜像） |
| VT agent 内部注册表 | ~106（无 key 时） | 测试注释口径；其中 ~32 个不在 MCP 面（§2 内部工具面小节） |
| VT 捆绑技能 | **90**（9 类） | agent/src/skills/ 实测；.opencode/skills/ 为同套分发副本 |
| VT swarm preset | **30** | list_swarm_presets runtime + agent/src/swarm/presets/ 30 YAML |
| **MCP 面可路由单元** | **194** | 74 + 90 + 30 |
| 披露税估算 | ~52k token/规划轮（74 工具全量） | PAPERS §F 中位数 ~700 token/工具 |
| 移出审计面（宿主侧） | 164 工具 + 8 技能 | alibaba-code-server 60 + github/grep_app 45 + opencode 内置 43 + NanoSearch 12 + context7/codegraph/Exa 4；用户级技能 5 + 系统级技能 3 |
| 历史口径 | RESEARCH.md"77 工具（memory ON 82）" | 2026-08-22 口径，与当前 74 存在漂移（R2） |

---

*本文档为 HARNESS EVOLUTION 工程基础调研文档（审计范围：vibe-trading 项目自身）。下一步：① 按 §8.3 P0 清单执行描述重写（6 项）；② 建 `search_tools` 元工具（以 §7.2 触发关键词为索引）；③ 路由遥测上线后回填 §7.2"命中失败案例"列；④ 按 §8.1 草案试点 2-3 个领域子代理（建议先 quant-agent 与 web-docs-agent，前者工具面最完整、后者仲裁规则最明确）；⑤ 内部工具面命名治理（Q19）与 swarm 白名单移植映射表落地。*
