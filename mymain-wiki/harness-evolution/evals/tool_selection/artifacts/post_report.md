# Tool-selection lexical baseline report

Deterministic lexical scoring of the Vibe-Trading agent's tool/skill
descriptions against the versioned query set. Regenerate with:

```
cd agent && python -m src.evals.tool_selection.run_eval --write-report src/evals/tool_selection/artifacts/baseline_report.md
```

## Corpus capture

- captured_at: `2026-08-26T02:07:13+00:00`
- MCP tools: 74
- bundled skills: 90
- queries: 158 entries across 19 domains

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 69/158 = 0.4367 |
| top-3 hit rate | 97/158 = 0.6139 |
| negative false-recall | 17/130 = 0.1308 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D01 | 9 | 4 | 6 | 0.4444 |
| D02 | 10 | 5 | 8 | 0.5000 |
| D03 | 5 | 2 | 3 | 0.4000 |
| D04 | 9 | 4 | 7 | 0.4444 |
| D05 | 11 | 6 | 7 | 0.5455 |
| D06 | 10 | 2 | 5 | 0.2000 |
| D07 | 10 | 4 | 5 | 0.4000 |
| D08 | 8 | 2 | 3 | 0.2500 |
| D09 | 10 | 3 | 7 | 0.3000 |
| D10 | 7 | 2 | 3 | 0.2857 |
| D11 | 8 | 5 | 5 | 0.6250 |
| D12 | 8 | 6 | 7 | 0.7500 |
| D13 | 9 | 4 | 5 | 0.4444 |
| D14 | 7 | 5 | 6 | 0.7143 |
| D15 | 7 | 3 | 3 | 0.4286 |
| D16 | 8 | 3 | 3 | 0.3750 |
| D17 | 11 | 4 | 6 | 0.3636 |
| D18 | 5 | 2 | 4 | 0.4000 |
| D19 | 6 | 3 | 4 | 0.5000 |

## Miss taxonomy

| bucket | misses |
|---|---|
| name-collision | 11 |
| boundary-missing | 8 |
| keyword-buried | 6 |
| name-reality-drift | 41 |
| dual-exposure | 0 |
| other | 23 |

## Complete miss list

| id | domain | query | expected | winner | taxonomy |
|---|---|---|---|---|---|
| D01-001 | D01 | 帮我取贵州茅台近一年的日线行情数据 | tool:get_market_data | skill:tushare | boundary-missing |
| D01-003 | D01 | 今天A股涨得最多的股票是哪些，给我排个行 | tool:screen_market | skill:regulatory-knowledge | name-reality-drift |
| D01-004 | D01 | 腾讯控股的股票代码是什么 | tool:search_symbol | skill:tushare | name-reality-drift |
| D01-006 | D01 | 看一下BTC-USDT的盘口深度，一万U买入的冲击成本多大 | tool:orderbook_depth | tool:get_market_data | boundary-missing |
| D01-008 | D01 | akshare被限流了，脚本里直连通达信取A股日线 | skill:mootdx | skill:akshare | boundary-missing |
| D02-001 | D02 | 拉一下茅台最近几年的利润表和关键财务指标 | tool:get_financial_statements | skill:tushare | name-reality-drift |
| D02-007 | D02 | 从10-K的风险因素和管理层讨论里提取投资信号 | skill:edgar-sec-filings | skill:ashare-pre-st-filter | other |
| D02-008 | D02 | What did Berkshire Hathaway buy and sell last quarter? Check the 13F | tool:get_institutional_holdings | tool:trading_check | other |
| D02-009 | D02 | AAPL的分析师目标价和机构持仓情况 | tool:get_stock_profile | skill:earnings-forecast | name-reality-drift |
| D02-010 | D02 | Screen A-shares for PE below 20 and ROE above 15 percent | skill:fundamental-filter | tool:screen_market | boundary-missing |
| D03-001 | D03 | 茅台最近有什么新闻和快讯 | tool:get_stock_news | skill:eastmoney | name-reality-drift |
| D03-003 | D03 | 券商对茅台未来三年的EPS一致预期是多少 | tool:get_research_reports | skill:earnings-forecast | other |
| D03-005 | D03 | 今天全球市场有什么财经大新闻 | tool:get_stock_news | skill:eastmoney | name-reality-drift |
| D04-001 | D04 | 看一下茅台最近的主力资金净流入 | tool:get_fund_flow | skill:eastmoney | name-reality-drift |
| D04-002 | D04 | 北向资金今天净买入多少 | tool:get_northbound_flow | skill:sentiment-analysis | name-reality-drift |
| D04-003 | D04 | 茅台的融资余额最近怎么变化 | tool:get_margin_trading | skill:sentiment-analysis | other |
| D04-008 | D04 | How should I interpret northbound flow as a signal, including sector allocation? | skill:hk-connect-flow | tool:get_northbound_flow | name-collision |
| D04-009 | D04 | 美股ETF的资金流和行业轮动广度怎么看 | skill:us-etf-flow | skill:sector-rotation | other |
| D05-003 | D05 | 给我茅台的通用技术面买卖信号 | skill:technical-basic | skill:sentiment-analysis | name-reality-drift |
| D05-006 | D05 | 帮我识别这段行情的头肩顶和三角形形态 | tool:pattern_recognition | skill:chanlun | name-reality-drift |
| D05-008 | D05 | 用一目均衡表看现在该买还是卖 | skill:ichimoku | skill:adr-hshare | name-reality-drift |
| D05-009 | D05 | 最近K线上有没有早晨之星这类形态 | skill:candlestick | skill:chanlun | name-reality-drift |
| D05-011 | D05 | 谐波形态Gartley的点位测算 | skill:harmonic | skill:chanlun | other |
| D06-001 | D06 | 你们有哪些预置的量化因子库 | tool:alpha_zoo | skill:sentiment-analysis | name-reality-drift |
| D06-002 | D06 | Which strategies have computed evidence for bear markets? | tool:query_strategies | tool:get_strategy_evidence | other |
| D06-003 | D06 | 浏览一下策略目录和各自的证据状态 | tool:list_strategies | skill:etf-analysis | name-reality-drift |
| D06-004 | D06 | Bench the GTJA191 alpha zoo on CSI300 — IC and IR | tool:alpha_bench | skill:alpha-zoo | name-collision |
| D06-005 | D06 | 我自己算好了因子CSV，做IC检验和分位分层回测 | tool:factor_analysis | tool:analyze_trade_journal | other |
| D06-006 | D06 | 这个策略在各市场状态下的回测证据给我看看 | tool:get_strategy_evidence | skill:sentiment-analysis | name-reality-drift |
| D06-008 | D06 | 多因子截面打分选A股，构建TopN组合 | skill:multi-factor | skill:ashare-pre-st-filter | keyword-buried |
| D06-009 | D06 | Turn this academic paper into a validated factor with decay monitoring | skill:strategy-dev-manager | tool:research_papers | other |
| D07-001 | D07 | 回测这个双均线策略，2023到2024年 | tool:backtest | skill:etf-analysis | name-reality-drift |
| D07-002 | D07 | Run the backtest for my RSI strategy config | tool:backtest | tool:run_shadow_backtest | name-collision |
| D07-003 | D07 | 帮我写一个动量策略并回测评估 | skill:strategy-generate | skill:etf-analysis | name-reality-drift |
| D07-004 | D07 | 回测表现太差了，帮我诊断根因 | skill:backtest-diagnose | skill:shadow-account | name-reality-drift |
| D07-008 | D07 | A股加加密货币的混合组合，signal_engine怎么写 | skill:cross-market-strategy | tool:backtest | other |
| D07-010 | D07 | 读一下回测工作区里的signal_engine.py | tool:read_file | tool:backtest | keyword-buried |
| D08-001 | D08 | 这个AAPL的call期权用BS模型值多少钱，Greeks是多少 | tool:analyze_options | tool:quantlib_call | keyword-buried |
| D08-003 | D08 | 拉一下AAPL的期权链，隐含波动率和持仓量 | tool:get_options_chain | tool:get_market_data | name-reality-drift |
| D08-004 | D08 | 期权策略方法论入门，从定价到多腿回测 | skill:options-strategy | skill:convertible-bond | name-reality-drift |
| D08-005 | D08 | Draw the payoff diagram for my multi-leg strategy with breakevens | skill:options-payoff | tool:analyze_options_payoff | name-collision |
| D08-006 | D08 | 波动率面SABR建模和日历价差套利 | skill:options-advanced | skill:corporate-events | other |
| D08-008 | D08 | 加密衍生品整体框架，期限结构和波动率微笑 | skill:crypto-derivatives | skill:sentiment-analysis | name-reality-drift |
| D09-002 | D09 | Compute the deflated Sharpe ratio of my backtest | tool:quantlib_call | tool:run_shadow_backtest | other |
| D09-003 | D09 | 基金有申购赎回现金流，真实收益率怎么算 | tool:cashflow_performance | skill:fund-analysis | name-reality-drift |
| D09-004 | D09 | 系统性讲讲VaR、压力测试和蒙特卡洛怎么做 | skill:risk-analysis | tool:quantlib_call | boundary-missing |
| D09-005 | D09 | Run an ADF unit-root test and an Engle-Granger cointegration test | skill:quant-statistics | tool:get_run_result | other |
| D09-006 | D09 | 两只股票的相关性结构、半衰期和Kalman对冲比率 | skill:correlation-analysis | skill:tushare | keyword-buried |
| D09-008 | D09 | 做资产配置，用风险预算或者全天候框架 | skill:asset-allocation | skill:ashare-pre-st-filter | name-reality-drift |
| D09-009 | D09 | 组合业绩归因，Brinson行业和选股分解 | skill:performance-attribution | skill:eastmoney | other |
| D10-001 | D10 | 用DCF给这家公司估值，加敏感性分析 | skill:valuation-model | skill:corporate-events | other |
| D10-002 | D10 | Look at this company through the deep-value and quality lenses | skill:investor-lenses | skill:deep-company-series | other |
| D10-003 | D10 | 评估一下这家公司管理层靠不靠谱 | skill:management-deep-dive | skill:corporate-events | name-reality-drift |
| D10-005 | D10 | 我刚买了这只股票，帮我写投资论点和红线 | skill:thesis-tracker | skill:tushare | name-reality-drift |
| D10-006 | D10 | Find the hidden beneficiaries of the AI infrastructure supply chain | skill:bottleneck-hunter | tool:get_options_chain | other |
| D11-003 | D11 | 宁德时代属于哪些行业和概念板块 | tool:get_sector_info | skill:eastmoney | name-reality-drift |
| D11-004 | D11 | 现在处于什么宏观周期阶段，央行政策怎么解读 | skill:macro-analysis | skill:financial-statement | name-reality-drift |
| D11-006 | D11 | 地缘战争风险怎么交易，有什么前兆信号 | skill:geopolitical-risk | skill:ashare-pre-st-filter | name-reality-drift |
| D12-001 | D12 | 美联储下次降息的市场隐含概率是多少 | tool:prediction_market | skill:sentiment-analysis | name-reality-drift |
| D12-006 | D12 | A股业绩预告超预期怎么提前捕捉 | skill:earnings-forecast | skill:ashare-pre-st-filter | other |
| D13-001 | D13 | 给这条新闻文本打个情绪分，看多还是看空 | tool:sentiment | skill:sentiment-analysis | name-collision |
| D13-004 | D13 | Extract financial signals from Twitter and Reddit posts | skill:social-media-intelligence | tool:extract_shadow_strategy | other |
| D13-005 | D13 | BTC链上数据怎么样，巨鲸和MVRV | skill:onchain-analysis | skill:tushare | keyword-buried |
| D13-006 | D13 | USDT的铸造销毁和交易所稳定币储备 | skill:stablecoin-flow | tool:get_block_trades | other |
| D13-008 | D13 | 清算热力图上哪里是猎止损区 | skill:liquidation-heatmap | skill:adr-hshare | name-reality-drift |
| D14-005 | D14 | Credit bond research — ratings, spreads and default risk | skill:credit-analysis | skill:private-company-research | other |
| D14-007 | D14 | Is this dividend sustainable? Check for yield traps | skill:dividend-analysis | tool:trading_check | other |
| D15-001 | D15 | 分析我的交割单，看看交易行为有什么偏差 | tool:analyze_trade_journal | skill:shadow-account | name-reality-drift |
| D15-003 | D15 | 从我赚钱的交易里提炼影子策略规则 | tool:extract_shadow_strategy | skill:shadow-account | name-collision |
| D15-004 | D15 | 影子策略跨市场回测，和我实际交易做归因对比 | tool:run_shadow_backtest | skill:shadow-account | name-collision |
| D15-006 | D15 | 今天有哪些标的符合我影子账户的入场节奏 | tool:scan_shadow_signals | skill:shadow-account | name-collision |
| D16-001 | D16 | 我配置了哪些券商连接器 | tool:trading_connections | skill:eastmoney | name-reality-drift |
| D16-002 | D16 | 把IBKR纸面账户设为默认连接器 | tool:trading_select_connection | skill:adr-hshare | name-reality-drift |
| D16-004 | D16 | 看看我券商账户里的持仓 | tool:trading_positions | skill:eastmoney | name-reality-drift |
| D16-006 | D16 | 我账户里现在有哪些挂单 | tool:trading_orders | skill:adr-hshare | name-reality-drift |
| D16-008 | D16 | 用连接器取AAPL的历史K线 | tool:trading_history | tool:get_market_data | boundary-missing |
| D17-001 | D17 | 开始一个研究目标，设定预算和验收准则 | tool:start_research_goal | skill:tushare | name-reality-drift |
| D17-003 | D17 | 给当前研究目标追加一条可溯源证据 | tool:add_goal_evidence | skill:credit-analysis | name-reality-drift |
| D17-004 | D17 | 跑一个投委会多智能体团队分析茅台 | tool:run_swarm | skill:tushare | name-reality-drift |
| D17-006 | D17 | 上次那个失败的swarm run帮我重试 | tool:retry_run | tool:run_swarm | other |
| D17-007 | D17 | Poll my swarm run progress without blocking | tool:get_swarm_status | tool:run_swarm | name-collision |
| D17-008 | D17 | 列出所有可用的金融技能 | tool:list_skills | skill:regulatory-knowledge | name-reality-drift |
| D17-010 | D17 | 做数据任务之前，该选哪个数据源 | skill:data-routing | skill:tushare | boundary-missing |
| D18-001 | D18 | 免费源没有这个数据，去付费市场搜一下有没有 | tool:qveris_search | skill:eastmoney | name-reality-drift |
| D18-003 | D18 | 执行这个QVeris付费能力 | tool:qveris_execute | skill:qveris | name-collision |
| D18-005 | D18 | 用QVeris执行刚检视过的那个付费能力，注意会话预算 | tool:qveris_execute | skill:qveris | name-collision |
| D19-001 | D19 | 搜一下美联储最新的政策声明 | tool:web_search | skill:ashare-pre-st-filter | name-reality-drift |
| D19-005 | D19 | 网页文章怎么转成Markdown来读 | skill:web-reader | tool:read_url | boundary-missing |
| D19-006 | D19 | Which tool reads documents and how do I use it? | skill:doc-reader | tool:refresh_strategy_evidence | keyword-buried |

## Limitations

- This is a **lexical baseline**, a regression sentinel for description
  wording (trigger front-loading, name distinguishability, boundary
  phrasing). It is not a measurement of LLM routing quality; real
  selection also depends on the model's reasoning over full schemas.
- A miss here means the *descriptions as written* do not lexically
  separate the expected target from the winner. Some misses are honest
  evidence of AUDIT findings (K/G/Q items); others are artifacts of
  the bag-of-tokens model. The taxonomy bucket distinguishes the cases.
- The corpus snapshot is frozen on purpose: description edits land
  *after* this baseline and are measured against it. Rebuilding the
  snapshot resets the baseline and is a deliberate act.
- LLM-judge mode (semantic scoring of the same query set) is future
  work E2; this suite is its deterministic anchor.

## P0 Delta Analysis (vs frozen baseline, 2026-08-26)

| Metric | Baseline | Post | Delta |
|---|---|---|---|
| top-1 accuracy | 69/158 = 0.4367 | 69/158 = 0.4367 | 0 (global floor held) |
| top-3 hit rate | 96/158 = 0.6076 | 97/158 = 0.6139 | +1 |
| negative false-recall | 16/130 = 0.1231 | 17/130 = 0.1308 | +1 (see D09-004) |

Target-group verdicts (two-tier gate):
- A1 sentiment (D13): 0.3333 → 0.4444, +1 hit — IMPROVED. D13-002 (crypto Fear & Greed
  query) flipped hit: dual-mode clarification works.
- A3 file scope (D07): 0.3000 → 0.4000, +1 hit — IMPROVED. D07-009 (write_file) flipped hit.
- A2 SEC rename (D02): 0.5000 → 0.5000 — FLAT. Rename value is LLM/human disambiguation,
  not measurable by the lexical proxy; deferred to E2.
- A4 quantlib front-load (D09): 0.4000 → 0.3000, −1 hit — flip attributed to a lexical-proxy
  limitation, not a defect (below). D09 top-3 improved 6 → 7.

Flip analysis:
1. D04-003 (get_margin_trading → sentiment-analysis): SCORER ARTIFACT. The position-weight
   formula (len−pos)/len gives every pre-existing term a small boost when text is appended
   to a description (sentiment-analysis gained the A1 cross-reference sentence). A thin race
   flipped. Known length-bias limitation of the v1 lexical scorer; the scorer is frozen for
   baseline comparability, bias flagged for the E2 upgrade.
2. D09-004 (risk-analysis → quantlib_call): LEXICAL AMBIGUITY. Query "系统性讲讲VaR、压力测试
   和蒙特卡洛怎么做" is methodology intent; the discriminator is verb semantics (讲讲 vs 计算),
   which keyword overlap cannot see. AUDIT Q4/K16 mandates exactly this front-loading; the
   computation-vs-methodology arbitration belongs to the E2 LLM judge / C3 router meta-rules.

Conclusion: global floor held; A1/A3 show measured target-group improvement; A2/A4 verdicts
defer to E2 LLM-judge (lexical proxy structurally cannot measure them). No change attributed
to a genuine routing regression.
