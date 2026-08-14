# Vibe-Trading 前端能力差距分析报告

> **调研日期**: 2026-08-13
> **调研范围**: 前端代码能力盘点、后端分析工具盘点、能力差距核对、同类产品对比、研报可视化频率排名、私募基金经理视角迭代建议
> **调研方法**: 4 路并行探索（前端代码分析 + 后端工具分析 + 竞品调研 + 研报可视化标准调研）

---

## 〇、配套文档：前端集成知识库（协议级开发上下文）

> **2026-08-14 新增**：本报告回答"**缺什么、先做什么**"；当另一个前端团队（例如 IM 插件视图层）要**实际动手重建同类能力**时，需要的是协议级契约文档。该知识库已整理完成，位于：
>
> 📁 **[`./frontend-integration/`](./frontend-integration/README.md)**（入口：`frontend-integration/README.md`）

| 文档 | 内容 |
|---|---|
| `00-architecture-and-conventions.md` | 架构总览、认证模型（Bearer + SSE 一次性票据）、错误约定、CORS/CSRF、SSE 传输通用机制 |
| `01-sessions-chat-sse.md` | 会话/消息/研究目标端点 + **聊天 SSE 全事件协议**（26 类事件的 payload 字段级定义） |
| `02-runs-backtest-artifacts.md` | `/runs` 端点族、全部产物文件 Schema（equity/trades/positions/metrics/validation/risk_xray/run_card）、指标单位语义 |
| `03-alpha-zoo.md` · `04-swarm.md` | 因子目录/Bench/Compare 与多智能体团队端点 + 各自 SSE 进度流事件 |
| `05-correlation-regime.md` · `06-scheduled-research.md` | 相关性矩阵/机制时间线；定时研究任务与 Playbook 模板 |
| `07-live-trading-runtime.md` | 实盘 Mandate 承诺 / Kill-switch / Runner / live SSE 事件（安全关键面） |
| `08-settings-channels-uploads.md` | 设置 / IM 通道 / 上传约束 / 系统探针 |
| `09-enums-validation-pitfalls.md` | **全量枚举参考 + 数值校验指南 + 18 条集成陷阱 + 客户端验收清单** |

知识库与本报告的关系：本篇的「能力差距核对表」（第三章）指出每项差距，`frontend-integration/` 则给出填补差距时所需的**后端契约细节**——端点、字段、枚举、SSE 事件、校验要求。两篇配合使用：先在本篇确定优先级，再到知识库查协议。

---

## 一、前端核心能力现状表

**技术栈**: React 19 + Vite 6 + TypeScript, react-router 8, **ECharts 6**（唯一图表库，tree-shaken 构建）, zustand, react-markdown + KaTeX + highlight.js, i18next（5 语言含 RTL 阿拉伯语）

### 1.1 页面/路由

| 路由 | 页面 | 核心能力 |
|---|---|---|
| `/` `/agent` | Agent 聊天工作台 | SSE 流式回答、工具进度条、Swarm 状态卡片、回测完成卡片（指标+迷你净值线）、研究目标面板、实盘授权卡片、文件上传、会话管理、聊天导出 .md |
| `/runs/:runId` | **RunDetail 回测结果** | 6 标签页：K线图（懒加载/批量加载）/ 交易表（筛选+分页+CSV导出）/ Studio（风险X光：HHI、有效N、波动、回撤、权重表；调仓笔记）/ 验证（蒙特卡洛+Bootstrap+Walk-Forward）/ 运行卡片（可复现哈希+产物校验）/ 策略代码 |
| `/compare` | 回测对比 | 双回测净值叠加（rebase/原始切换）+ 15 指标差异表（delta 箭头+比例条） |
| `/reports` | 报告库 | 运行列表搜索/状态/日期过滤、按收益/夏普排序 |
| `/correlation` | 相关性 | 滚动相关矩阵热力图（pearson/spearman, 30-365d）+ 相关性机制时间线（FUSED 状态） |
| `/alpha-zoo` | 因子库 | 462 因子目录浏览/搜索/分页、因子详情（公式+源码）、Bench 运行（SSE 进度+结果统计卡+主题堆叠柱图+Top IR 表）、多因子对比排名 |
| `/runtime` | 实盘监控 | 全局暂停开关、券商卡片（授权/授权倒计时/风险状态/连接器验证） |
| `/scheduled` | 定时研究 | 任务列表/状态、创建（时间/cron+IANA时区）、删除 |
| `/settings` | 设置 | LLM 提供商/模型/参数、数据源（Tushare/QVeris）、IM 通道、API Key、语言 |

### 1.2 图表组件（全部 ECharts）

| 组件 | 图表类型 | 特性 |
|---|---|---|
| CandlestickChart | K线+均线+BOLL+成交量/MACD/RSI/KDJ副图 | 买卖点标注、区间选择、dataZoom、图片导出 |
| EquityChart | 净值曲线+回撤面积图 | 最大回撤标注线 |
| MiniEquityChart | 迷你净值线 | 聊天卡片用 |
| CorrelationMatrix | 热力图 | RdBu 发散色阶 [-1,1] |
| MonteCarloPathsChart | 扇形图 | P5-P95/P25-P75 包络+样本路径+中位数+实际 |
| DistributionChart | 直方图 | 观测值标注线+CI 区间带 |
| WalkForwardChart | 柱线双轴 | 每窗口 OOS 收益+Sharpe 叠加 |
| RegimeTimeline | 折线+标注区域 | 边密度+FUSED 状态着色+阈值线 |
| ValidationPanel | 复合面板 | 蒙特卡洛+Bootstrap+Walk-Forward 三段 |

### 1.3 关键约束

> **`frontend/src/lib/echarts.ts` 仅注册了 Candlestick/Line/Bar/Heatmap 四种 series** — 饼图、散点图、雷达图、仪表盘、桑基图、箱线图均不可用，需先修改此文件注册新模块。这是多个新可视化能力的阻塞项。

### 1.4 导出能力

- 交易 CSV / 指标 CSV 下载（客户端 Blob）
- 聊天记录 Markdown 导出
- ECharts 图片保存（K线+净值图）
- Pine Script 复制（TradingView/TDX/MT5）
- Shadow 报告 HTML（后端渲染，新标签页打开）
- **无前端 PDF/xlsx/pptx 生成**

### 1.5 前端关键文件索引

| 文件 | 用途 |
|---|---|
| `frontend/src/lib/api.ts` | 全部 API 客户端 + TypeScript 数据契约（1184 行）— 前端能渲染什么的权威定义 |
| `frontend/src/lib/echarts.ts` | ECharts tree-shaken 构建 — 仅 4 种 series 注册 |
| `frontend/src/lib/indicators.ts` | 客户端 MA/EMA/BOLL/MACD/RSI/KDJ 计算 |
| `frontend/src/lib/formatters.ts` | 15 个回测指标标签 + 情绪阈值 |
| `frontend/src/lib/runReports.ts` | "报告级运行"判定逻辑 |
| `frontend/src/pages/*.tsx` | 10 个页面组件 |
| `frontend/src/components/charts/*.tsx` | 9 个图表组件 |
| `frontend/src/components/chat/*.tsx` | 聊天内嵌展示组件（MetricsCard, RunCompleteCard, SwarmDashboard 等） |

---

## 二、后端金融分析/量化研究工具表

**架构**: FastAPI 后端，~94 Agent 工具（70 个暴露为 MCP），265 个 quantlib 函数（19 模块），462 个 Alpha 因子，10 个回测引擎，89 个金融技能，30 个 Swarm 预设

### 2.1 按类别汇总

| 类别 | 核心工具/函数 | 计算内容 | 输出类型 | 前端可达? |
|---|---|---|---|---|
| **行情数据** | `get_market_data` | 24 数据源 OHLCV，1m-1M 周期，自动降级链 | 时间序列 | RunDetail K线 |
| **技术指标** | `technical_indicators` | RSI/MACD/BOLL/SMA/EMA | 指标序列 | K线副图 |
| **基本面** | `get_fundamentals` | PIT 安全基本面面板（SEC XBRL），filed-date 锚定 | 日频宽面板 | 仅聊天文本 |
| | `get_financial_statements` | A/美/港三表+指标 | 按期行数据 | 仅聊天 |
| | `get_stock_profile` | 关键统计、分析师预期、持股、推荐趋势 | 快照 | 仅聊天 |
| | `get_research_reports` | A股卖方研报+**一致预期 EPS** | 列表+预期序列 | 仅聊天 |
| | `financial_rigor` | 精确估值验证、Benford 造假检测、三情景目标价 | 判定+数值 | 仅聊天 |
| **估值模型** | `run_dcf` | FCFF 桥、WACC、双终值、**WACC*g 敏感性网格** | 结构化+网格 | 仅聊天 |
| | `run_comps` | EV 桥、同业倍数矩阵、隐含估值区间 | 结构化 | 仅聊天 |
| | `project_three_statement` | 三表联动预测、平衡断言、循环迭代 | 预测 | 仅聊天 |
| | `export_*` | **xlsx 工作簿 + pptx 摘要** | 文件 | 无前端入口 |
| **因子研究** | `alpha_zoo/bench/compare` | 462 因子 IC/IR/alive-reversed-dead 分类 | 聚合统计+HTML | AlphaZoo 页（仅聚合） |
| | `factor_analysis` | **IC 序列、IR、分层分位净值** | IC 序列+净值曲线 | 无渲染 |
| | `research_papers` | arXiv/OpenAlex 论文因子简报 | 结构化简报 | 仅聊天 |
| | `sdm_*` | 策略开发经理：因子注册+**IC 衰减监控** | 存储+衰减信号 | 仅聊天 |
| | 假设注册表 | 假设生命周期管理 | 记录 | 无 UI |
| **回测** | 10 引擎 | 中国A/全球股票/印度/韩国/加密(含资金费+强平)/期货/外汇/跨市场/期权组合 | 完整产物集 | RunDetail |
| | 产物 | equity.csv, trades.csv, positions.csv, metrics.csv, validation.json, **risk_xray.json**, rebalance_notes, run_card | 多类型 | 部分渲染 |
| | `calc_metrics` | 18 指标：收益/波动/Sharpe/Sortino/Calmar/MaxDD/胜率/盈亏比/换手/基准/超额/IR/跟踪误差/Beta | 标量 | 指标卡 |
| | 验证 | 蒙特卡洛/Bootstrap/Walk-Forward | 结构化 | ValidationPanel |
| | `pattern_recognition` | 峰谷、K线形态、支撑阻力、头肩、双顶底、三角形 | 形态标注 | 仅聊天 |
| **风险分析** | `portfolio_risk_xray` | HHI/有效N、波动、回撤、**VaR/ES**、分散化比率、相关性/Beta | 结构化 | 仅渲染部分 |
| | quantlib `risk` | 历史/参数 VaR、CVaR、蒙特卡洛 GBM、**EVT GPD 尾部拟合** | 标量/路径 | 仅聊天 |
| | quantlib `var_backtest` | Kupiec/Christoffersen 检验、**巴塞尔红绿灯** | 报告 | 仅聊天 |
| | quantlib `timeseries` | ADF、协整、半衰期、GARCH、**马尔可夫机制转换** | 检验结果 | 仅聊天 |
| | quantlib `multipletesting` | Deflated Sharpe、PSR、BH FDR、**PBO** | 标量 | 仅聊天 |
| **期权** | `options_pricing` | BS 定价+Greeks | 标量 | 仅聊天 |
| | `options_payoff` | 多腿到期收益、盈亏平衡、最大盈亏、**收益曲线、spot*IV 情景矩阵** | 曲线+矩阵 | 仅聊天 |
| | `get_options_chain` | Yahoo 期权链（行权价/买卖/OI/IV/ITM） | 链快照 | 仅聊天 |
| | options_portfolio 引擎 | 期权回测：历史波动率、IV 微笑/偏斜、Greeks 盯市 | 回测产物 | 仅聊天 |
| **固收/信用** | quantlib `fixedincome` | 债券定价、YTM、久期、凸性、DV01、**NS/Svensson 曲线拟合** | 标量/曲线 | 仅聊天 |
| | quantlib `credit` | **Altman Z、Merton/KMV 违约距离**、信用利差 | 标量 | 仅聊天 |
| **归因/基金数学** | quantlib `attribution` | **Brinson-Fachler**（配置/选股/交互）、Carino 链接 | 归因表 | 仅聊天 |
| | quantlib `factormodel` | 风格暴露、因子收益、**风格漂移** | 暴露序列 | 仅聊天 |
| | quantlib `eventstudy` | 事件研究 CAR/CAAR、Patell/BMP 检验 | 检验结果 | 仅聊天 |
| | quantlib `fundmath` | XIRR/MOIC/DPI/TVPI、**瀑布分配、KS-PME/PME+** | 标量/瀑布 | 仅聊天 |
| | `cashflow_performance` | 不规则现金流 TWR/Dietz/XIRR | 标量 | 仅聊天 |
| **资金流/A股微观结构** | `get_fund_flow` | 主力/超大/大/中/小单净流入 | 日序列/分钟线 | 仅聊天 |
| | `get_northbound_flow` | 北向资金净流入（沪/深股通拆分） | 实时+日历史 | 仅聊天 |
| | `get_margin_trading` | 融资融券余额 | 日序列 | 仅聊天 |
| | `get_block_trades` | 大宗交易+溢折价+买卖席位 | 交易列表 | 仅聊天 |
| | `get_dragon_tiger` | 龙虎榜+买卖营业部 | 列表+席位 | 仅聊天 |
| | `get_shareholder_count` | 股东户数季度变化 | 季度序列 | 仅聊天 |
| | `get_lockup_expiry` | 限售解禁日程 | 日程表 | 仅聊天 |
| | `get_sector_info` | 板块归属+板块涨跌排名 | 列表/排名 | 仅聊天 |
| | `screen_market` | 全市场涨跌/成交量/换手排名 | 排名快照 | 仅聊天 |
| **机构/ETF** | `get_institutional_holdings` | SEC 13F 持仓+**季度环比变动** | 季度持仓 | 仅聊天 |
| | `etf_holdings` | ETF 穿透（美国 N-PORT 全组合/A股基金报告） | 持仓+权重 | 仅聊天 |
| **情绪/另类** | `sentiment` | 文本情绪 [-1,1]、**加密恐惧贪婪指数** | 标量/指数 | 仅聊天 |
| | `get_stock_news` | 东财/Yahoo 新闻 | 文章列表 | 仅聊天 |
| | `prediction_market` | Polymarket 隐含概率+**概率历史序列** | 概率序列 | 仅聊天 |
| **宏观** | `get_macro_series` | FRED 宏观序列（CPI/失业率/GDP/利率/国债） | 宏观序列 | 仅聊天 |
| **交易日志/影子账户** | `analyze_trade_journal` | 交易画像+**4 项行为偏差诊断** | 画像+诊断 | 仅聊天 |
| | `extract_shadow_strategy` | 盈利模式规则提取 | 规则档案 | 仅聊天 |
| | `run_shadow_backtest` | 影子回测+**差值归因** | 回测+归因 | 仅聊天 |
| | `render_shadow_report` | **8 节 HTML/PDF 报告** | 文件 | 后端渲染新标签页 |
| **订单簿** | `orderbook_depth` | L2 盘口、价差、深度失衡、冲击成本 | 快照 | 仅聊天 |
| **相关性** | `/correlation` + `/regime` | 滚动相关矩阵+边密度机制时间线 | 矩阵+时间线 | Correlation 页 |

### 2.2 关键发现：暴露面断层

后端工具按暴露面分三层，**前端只能直接调用 REST 层**：

| 暴露面 | 工具数 | 前端可达 |
|---|---|---|
| REST API（runs/sessions/alpha/swarm/correlation/scheduled/live/settings/shadow-reports） | ~15 端点 | 可达 |
| MCP Server | 70 工具 | 仅通过聊天 |
| Agent 工具注册表 | ~94 工具 | 仅通过聊天 |

**结论：~80% 的分析计算能力只以聊天 Markdown 文本形式呈现，无结构化前端渲染。**

---

## 三、能力差距核对表（当前持有 vs 前端可展示）

| 后端能力 | 计算状态 | 前端展示状态 | 差距等级 |
|---|---|---|---|
| 回测净值/回撤/指标 | 完整 | EquityChart+MetricsCard | 无差距 |
| K线+技术指标+交易标注 | 完整 | CandlestickChart | 无差距 |
| 蒙特卡洛/Bootstrap/Walk-Forward | 完整 | ValidationPanel | 无差距 |
| 相关矩阵+机制时间线 | 完整 | Correlation 页 | 无差距 |
| Alpha 目录/Bench/Compare | 完整 | AlphaZoo 页 | 无差距 |
| **月度收益热力图** | equity.csv 可推导 | 无 | 重大差距 |
| **年度收益条形图** | 可推导 | 无 | 重大差距 |
| **因子 IC 时序图** | factor_analysis 输出 ic_series.csv | 仅聚合计数 | 重大差距 |
| **分层回测净值图** | factor_analysis 输出 group_equity.csv | 无 | 重大差距 |
| **因子相关性热力图** | 可计算 | 无 | 中等差距 |
| **期权收益图+情景矩阵** | options_payoff 完整输出 | 零渲染 | 重大差距（README 明确 Planned） |
| **Greeks 仪表盘** | options_pricing | 无 | 重大差距 |
| **期权链表格** | get_options_chain | 无 | 中等差距 |
| **Brinson 归因表/瀑布图** | quantlib.attribution + /attrib | 无 | 重大差距（买方刚需） |
| **风格暴露图** | quantlib.factormodel | 无 | 重大差距（指增产品日常） |
| **DCF 敏感性矩阵** | run_dcf 输出 WACC*g 网格 | 无 | 重大差距（研报标配） |
| **Comps 倍数矩阵** | run_comps | 无 | 重大差距 |
| **盈利预测表** | get_research_reports 一致预期 | 无 | 重大差距（研报首页必备） |
| **PE-Band 估值通道** | 可计算 | 无 | 中等差距 |
| **PB-ROE 散点** | 可计算 | 无（散点图未注册） | 中等差距 |
| **持仓结构饼图/Treemap** | positions.csv 有权重 | 仅表格（饼图未注册） | 重大差距 |
| **行业分布图** | 可推导 | 无 | 重大差距 |
| **VaR/CVaR 卡片** | quantlib.risk | 无 | 中等差距 |
| **risk_xray 尾部/分散化/相关性子载荷** | 已计算+api.ts 已定义类型 | **已定义但未渲染** | 中等差距（快赢） |
| **资金流仪表盘**（主力/北向/两融/龙虎榜/大宗） | 8 个工具 | 零渲染 | 重大差距（A股特色高频） |
| **13F 持仓表+季度变动** | get_institutional_holdings | 无 | 中等差距 |
| **ETF 穿透持仓表** | etf_holdings | 无 | 中等差距 |
| **股东户数/解禁日历** | 工具存在 | 无 | 中等差距 |
| **情绪仪表盘/恐贪指数** | sentiment | 无 | 中等差距 |
| **宏观序列图** | get_macro_series | 无 | 中等差距 |
| **预测市场概率曲线** | prediction_market | 无 | 低优先 |
| **订单簿深度图** | orderbook_depth | 无 | 低优先 |
| **交易行为诊断可视化** | analyze_trade_journal 4 项偏差 | 仅聊天文本 | 中等差距 |
| **影子账户报告** | 8 节 HTML/PDF | 后端渲染新标签页 | 中等差距（可集成） |
| **假设注册表** | 后端 MVP | 无 UI | 低优先 |
| **IC 衰减监控** | sdm_decay_scan | 无 | 低优先 |
| **事件研究图** | quantlib.eventstudy | 无 | 低优先 |
| **瀑布分配图** | quantlib.fundmath | 无 | 低优先 |

**统计**: 重大差距 14 项 | 中等差距 13 项 | 低优先 6 项

---

## 四、同类产品前端展示能力对比表

### 4.1 开源量化平台

| 产品 | 核心可视化 | 报告生成 | 差异化展示 |
|---|---|---|---|
| **QuantConnect** | 净值/回撤/基准/多空暴露/换手/保证金/订单标注K线、运行时统计横幅 | **可下载 PDF 报告**+可自定义 HTML 模板 | **月度收益热力图**、危机事件回测对比、策略容量曲线、订单成交标注、Top5回撤标注 |
| **Qlib** | IC/RankIC 时序、IC 分布、IC Q-Q 图、累计收益、月度风险指标 | Notebook 图形报告(plotly) | 预测分自相关->换手估算、成本前后超额对比 |
| **VectorBT** | Plotly 交互式仪表盘：K线+订单标记+净值+回撤+持仓 | 无 | **参数优化 2D/3D 热力图**、批量策略对比 |
| **OpenBB** | 拖拽式仪表盘：行情/技术分析/组合绩效/行业暴露/风险指标 | 仪表盘导出 PDF/图片 | AI Copilot、自定义 Widget 布局 |
| **FreqUI** | 实时交易+K线买卖标记、累计收益/余额曲线、多机器人仪表盘 | 无 | **多回测结果并排对比**、Plot Configurator |
| **Backtrader/Zipline** | matplotlib 静态图（K线+指标+买卖点+资金曲线） | pyfolio tearsheet / quantstats HTML | Jupyter 工作流 |
| **VNPY** | Qt K线图表（实时 Tick 更新）、回测成交/委托/日盈亏对话框 | 无 | 实时行情驱动（可视化偏薄是公认短板） |

### 4.2 商业终端（基金经理日常）

| 产品 | 核心可视化 | 基金经理用途 |
|---|---|---|
| **Wind 万得** | F9 深度资料（单页公司全景）、**组合盈亏/风险图表化实时监控**、EDB 宏观作图、AMS 绩效归因（多模型） | F9 看公司、组合监控与归因、研报跟踪、Excel 插件 |
| **Bloomberg** | **PORT 归因/风险暴露/因子分解**、MARS 衍生品风险、多屏图表 | 归因报告（日/周/月）、风险暴露监控、跨资产情景分析 |
| **iFinD 同花顺** | **财务纵比（可视化对比）**、产业链图谱+动态推演、宏观指标可视化 | 行业比较、产业链研究、估值建模 |
| **Choice 东财** | 组合回测盈亏、EDB、Level-2 盘口 | AI 研报总结（溯源）、EQBT 量化回测 |
| **朝阳永续** | **一致预期追踪**、AI 业绩前瞻、超预期线索 | 私募四维风格评价（配置/操作/行业/市道）、AI 尽调报告 |

### 4.3 Prosumer/中国量化平台

| 产品 | 核心可视化 | 差异化 |
|---|---|---|
| **TradingView** | 多图表布局、10万+指标、热力图、策略回测可视化 | Bar Replay 回放、Pine 生态、社区 |
| **Koyfin** | 估值倍数图（P/E/EV-EBITDA）、宏观仪表盘、**组合回撤+相关性追踪** | 5900+筛选条件、预置图形模板 |
| **Finviz** | **市场热力图（板块树状图）**、快照图墙 | 70+条件筛选器 |
| **聚宽** | 收益曲线(vs基准)/回撤/胜率/换手、**因子看板** | Notebook 分享社区、模拟盘/实盘监控 |
| **BigQuant** | 拖拽建模+收益/回撤/换手/夏普、调仓持仓复盘 | 2000+ AI 因子库、对话式策略开发 |

### 4.4 行业标准提炼（Table Stakes）

| 类别 | 必备图表 | Vibe-Trading 现状 |
|---|---|---|
| 回测绩效 | 净值vs基准、回撤曲线、**月度收益热力图**、关键统计表 | 缺月度热力图 |
| 交易复盘 | 成交记录表、K线+买卖点、持仓时序 | 基本具备 |
| 风险暴露 | 多空暴露时序、行业暴露、杠杆率 | 缺失 |
| 因子研究 | **IC时序、IC分布、分层净值曲线、因子相关性矩阵**、换手分析 | 仅聚合计数 |
| 报告导出 | **PDF/HTML 一键报告**、基准对比、Top-N 回撤标注 | 仅 CSV+图片 |
| 归因 | **Brinson 归因、因子归因** | 缺失 |
| 组合监控 | 实时持仓盈亏、行业分布、风格暴露 | 缺失 |

---

## 五、研报可视化频率排名（重要性评判依据）

基于券商研报实例（广发金工、华泰金工因子月报）+ 本项目技能文档编码的专业标准 + QuantStats/QuantConnect 行业规范交叉验证。

### Tier S：每份报告必有 / 每日必看（频率 ~95-100%）

| 排名 | 可视化 | 场景 |
|---|---|---|
| 1 | **盈利预测表**（未来2-3年营收/净利润/EPS/PE预测） | 券商个股研报首页必放，评级和目标价的算术基础 |
| 2 | **净值曲线 vs 基准**（含超额收益线） | 买方每日必看；所有量化策略报告的第一张图 |
| 3 | **股价走势图**（常叠加目标价/评级区间） | 个股研报首页/封面惯例 |
| 4 | **IC序列图 + IC统计表**（IC均值/IR/IC>0占比/胜率） | 每一篇因子研报的核心证据图 |
| 5 | **分层回测净值图**（5/10分组净值曲线+多空收益） | 每一篇因子研报的第二核心图 |
| 6 | **持仓盈亏与持仓列表** | 买方基金经理每日开盘第一件事 |
| 7 | **回撤曲线（水下图/underwater chart）** | 买方日常风控第一指标；回撤控制是基金经理核心KPI |

### Tier A：标准配置（频率 ~60-90%）

| 排名 | 可视化 | 场景 |
|---|---|---|
| 8 | **回测绩效指标表**（年化/夏普/最大回撤/Calmar/胜率/换手率 vs 基准） | 每份策略回测报告的开篇表格 |
| 9 | **估值对比表**（PE/PB/EV-EBITDA vs 同业 vs 自身历史分位） | 个股研报估值章节标配 |
| 10 | **行业景气度/行业对比图** | 行业研报必有；策略报告常用 |
| 11 | **月度收益热力图** | 回测 tearsheet 标配；买方月度复盘 |
| 12 | **风格暴露图**（Barra 因子敞口相对基准） | 量化私募指增产品每日监控；金工研报标配 |
| 13 | **年度收益条形图** | tearsheet 标配 |
| 14 | **DCF敏感性分析表**（WACC * 永续增长率矩阵） | 含DCF估值的深度报告必带 |
| 15 | **PE-Band 图**（估值通道图） | 估值章节高频图表 |
| 16 | **行业分布/持仓结构图**（饼图或条形） | 买方组合监控、基金季报 |

### Tier B：常见但场景化（频率 ~30-60%）

| 排名 | 可视化 | 场景 |
|---|---|---|
| 17 | Brinson归因表（配置/选股/交互效应，按行业） | 买方季度绩效复盘、FOF尽调、基金评价报告 |
| 18 | 因子相关性热力图 | 多因子合成研报 |
| 19 | 换手率分析图（分组换手率/组合换手率时序） | 因子研报的交易成本可行性论证 |
| 20 | 滚动夏普/滚动波动率曲线 | tearsheet 与买方风险仪表盘 |
| 21 | PB-ROE 散点图（四象限） | 估值章节、行业横向比较 |
| 22 | 收益分布直方图 | tearsheet |
| 23 | 三表走势图（营收/利润/毛利率/ROE多年趋势） | 深度报告财务分析章节 |
| 24 | VaR/CVaR 指标（多为数字卡片而非图表） | 风险仪表盘、风控报告 |

### Tier C：Nice-to-have / 专项场景（频率 <30%）

| 排名 | 可视化 | 场景 |
|---|---|---|
| 25 | 杜邦分解树 | 部分深度财务分析章节 |
| 26 | 因子IC衰减图（不同持有期IC） | 专项因子研报 |
| 27 | 蒙特卡洛模拟分布图 | 回测稳健性验证附录 |
| 28 | Top5回撤明细表 | tearsheet 补充 |
| 29 | 策略对比表（多策略横向） | 策略配置类报告 |
| 30 | 因子暴露雷达图 | 产品尽调 PPT 偶见 |

### 关键观察

**卖方研报的核心可视化是"表格"而非"图表"** — 盈利预测表、估值对比表、指标表的出现频率高于任何花哨图形；而**买方日常的核心是"曲线+敞口"** — 净值/回撤/风格暴露构成每日监控三角。平台前端若资源有限，应先把"表格引擎 + 净值/回撤曲线 + IC/分层两件套"做到专业级渲染（数字对齐、单位规范、A/E标注、分位着色），这比任何 3D 或交互式炫技都更接近专业用户的真实工作流。

---

## 六、私募基金经理视角：前端迭代优先级建议

> **评判标准**: (1) 与同类产品的差距（竞品普遍有而 Vibe-Trading 没有 = 高优先）；(2) 金融研报使用频次（Tier S/A = 高优先）；(3) 后端计算已就绪（只需前端渲染 = 高 ROI）

### P0 — 必做（竞品标配 * 研报高频 * 后端已就绪）

| # | 能力 | 差距依据 | 频率依据 | 实现要点 |
|---|---|---|---|---|
| **1** | **回测 Tearsheet 增强：月度收益热力图 + 年度收益条形图 + Top-N 回撤标注** | QuantConnect/pyfolio/QuantStats 标配，所有竞品均有；Vibe-Trading 完全缺失 | Tier A（60-90%），tearsheet 必带 | 数据源 equity.csv 已有，纯前端推导+渲染；回撤标注需从 equity 序列提取 peak/trough/recovery |
| **2** | **因子研究标准图组：IC 时序图 + IC 统计卡 + 分层净值图 + 因子相关性热力图** | Qlib/聚宽/BigQuant 标配；Vibe-Trading AlphaZoo 仅显示聚合计数，**factor_analysis 已输出 ic_series.csv 和 group_equity.csv 但零渲染** | Tier S（~95%），每篇因子研报核心证据 | 需新增 REST 端点暴露 factor_analysis 产物；IC 图=柱状+均线；分层净值=多序列折线；相关性热力图可复用 CorrelationMatrix 组件 |
| **3** | **持仓结构可视化：持仓权重饼图/Treemap + 行业分布图** | Wind/Bloomberg/QuantConnect 均有持仓结构图；Vibe-Trading 权重仅表格 | Tier A（行业分布图 60-90%），买方每日必看 | **需先在 lib/echarts.ts 注册 PieChart**；数据源 positions.csv 已有；行业映射需 get_sector_info 或 ETF 分类 |
| **4** | **绩效归因面板：Brinson 归因表/瀑布图 + 因子归因** | Bloomberg PORT / Wind AMS 核心能力，买方季度复盘必备；Vibe-Trading quantlib.attribution 已实现但零前端 | Tier B（30-60%），但买方刚需 | 需新增 REST 端点暴露 /attrib 结果；Brinson 表=权重*收益*三效应；瀑布图用 BarChart 堆叠 |
| **5** | **期权分析面板：收益图 + spot*IV 情景矩阵 + Greeks 卡片 + 期权链表格** | README 明确 "Options Lab: surface/dashboard Planned"；**后端 analyze_options_payoff 已完整输出收益曲线+情景矩阵但零渲染** | 期权研报/策略报告高频 | 收益图=Line；情景矩阵=Heatmap；Greeks=指标卡；期权链=表格 |

### P1 — 专业感来源（竞品常见 * 研报标配 * 后端已就绪）

| # | 能力 | 差距依据 | 频率依据 | 实现要点 |
|---|---|---|---|---|
| **6** | **估值表格中心：DCF 敏感性矩阵（WACC*g 热力表）+ Comps 倍数矩阵 + 盈利预测表（A/E 列区分）** | Wind/iFinD/Koyfin 均有估值工具；Vibe-Trading 估值引擎已实现+xlsx/pptx 导出但零前端 | 盈利预测表 Tier S（研报首页必放）；DCF 敏感性 Tier A | 敏感性矩阵=Heatmap；盈利预测表需 get_research_reports 一致预期数据；**表格渲染质量是关键** |
| **7** | **A股资金流仪表盘：主力资金流 + 北向资金 + 两融余额 + 龙虎榜 + 大宗交易** | Wind/Choice/iFinD 均有资金流监控；Vibe-Trading 有 8 个资金流工具但零前端 | A股研报高频（资金面分析是标配章节） | 需新增 REST 端点；折线图+表格；北向资金实时+历史双线 |
| **8** | **风险仪表盘补全：VaR/CVaR 卡片 + 风格暴露条形图 + risk_xray 尾部/分散化/相关性子载荷渲染** | Bloomberg MARS / Wind AMS 标配；**risk_xray 的 tail_risk/diversification/correlation 子载荷已在 api.ts 定义类型但未渲染**（快赢！） | VaR Tier B；风格暴露 Tier A（指增产品每日监控） | 快赢项：risk_xray 子载荷已有类型定义，只需在 StudioTab 补充渲染 |
| **9** | **一致预期/盈利前瞻追踪：EPS 预测修正时序 + 超预期线索** | **朝阳永续核心卖点**（机构粘性来源）；Vibe-Trading get_research_reports 已有一致预期 EPS 但零前端 | 盈利预测表 Tier S | 折线图+预测修正表格 |
| **10** | **一键研报导出：PDF/HTML 研究报告（含图表嵌入）** | QuantConnect PDF 报告是标配；Vibe-Trading 仅 CSV+图片导出 | 报告导出是行业标准 | 可复用后端 report-generate 技能 + Shadow 报告渲染管线；前端需报告模板选择+预览 |

### P2 — 差异化（竞品少见或专项场景）

| # | 能力 | 说明 |
|---|---|---|
| **11** | 交易行为诊断可视化（4 项偏差雷达图/评分卡） | 后端 analyze_trade_journal 已计算 disposition/overtrading/chasing/anchoring；Vibe-Trading 特色能力，竞品无 |
| **12** | 订单簿深度图 + 清算热力图 | 加密专项；orderbook_depth 已计算；liquidation-heatmap 技能已有方法论 |
| **13** | 情绪仪表盘（恐贪指数仪表盘 + 文本情绪趋势） | 需注册 GaugeChart；竞品少见 |
| **14** | 假设注册表 UI + 研究流程看板 | 后端 MVP 已有；研究管理差异化 |
| **15** | 宏观序列叠加图（多 FRED 序列同屏） | 宏观研报常用；get_macro_series 已就绪 |
| **16** | 预测市场概率曲线 | 另类数据展示；prediction_market 已有时序 |

### 实施前置条件

1. **ECharts 模块注册**（`frontend/src/lib/echarts.ts`）：P0-3 需 PieChart；P2-13 需 GaugeChart。这是多个功能的阻塞项。
2. **REST 端点扩展**：factor_analysis 产物、/attrib 归因结果、options_payoff 结果、资金流工具、一致预期数据 — 当前均为 Agent/MCP 专属，前端无法直接调用。
3. **表格渲染引擎升级**：研报核心可视化是**表格而非图表**（盈利预测表、估值对比表、指标表出现频率高于任何图形）。专业级表格渲染（数字对齐、单位规范、A/E 标注、分位着色、同比自动计算）比任何交互式炫技都更接近投资经理真实工作流。

### 总结：最高优先级 5 项

> 从私募基金经理日常工作流出发，**按"竞品差距 * 研报频率 * 实现 ROI"三维排序**：

| 优先级 | 能力 | 一句话理由 |
|---|---|---|
| **#1** | 因子研究图组（IC 时序+分层净值） | 后端数据已就绪但零渲染，竞品（Qlib/聚宽）全有，因子研报 95% 频率 |
| **#2** | 回测 Tearsheet 增强（月度热力图+年度条形图+Top-N 回撤） | tearsheet 行业标准，QuantConnect/pyfolio 全有，纯前端推导即可 |
| **#3** | 持仓结构+行业分布可视化 | 买方每日必看，Wind/Bloomberg 标配，需注册 PieChart |
| **#4** | Brinson 归因面板 | 买方季度复盘刚需，Bloomberg PORT/Wind AMS 核心，后端已实现 |
| **#5** | 期权收益图+情景矩阵 | 后端完整计算零渲染，README 明确 Planned，实现成本低 |

---

## 附录：调研来源

- 前端代码分析：`frontend/src/` 全量页面/组件/图表/API 类型扫描
- 后端工具分析：`agent/mcp_server.py`（70 MCP 工具）、`agent/src/tools/`（~94 工具）、`agent/src/quantlib/`（265 函数）、`agent/backtest/`（10 引擎）、`agent/src/skills/`（89 技能）
- 竞品调研：QuantConnect 官方文档、Qlib 文档、FreqUI 文档、OpenBB 文档、vnpy GitHub、Wind 官网、iFinD 百科与官网、Choice 百科、Bloomberg PORT 资料、Koyfin 官网、Finviz Elite、TradingView 功能页、聚宽社区文档、米筐文档、BigQuant 官网、朝阳永续官网与清华培训材料、好买财富官网
- 研报标准：券商研报结构解析、广发金工/华泰金工因子月报实例、QuantStats tearsheet 规范、QuantConnect/Lean 报告规范、本项目 report-generate/valuation-model/factor-research/performance-attribution/risk-analysis 技能文档
