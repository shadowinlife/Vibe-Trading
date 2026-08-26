# Vibe-Trading Internal Tool Inventory

Captured at: (deterministic mode: --no-timestamp) · Credential gates cleared in child env: FRED_API_KEY, QVERIS_API_KEY, VIBE_TRADING_IWENCAI_KEY, VIBE_TW_STOCK_DB

Protocol: keyless registry measured in a clean subprocess exactly like `tests/test_readme_counts.py::_keyless_agent_tool_count` (shell tools off); MCP surface via `asyncio.run(mcp_server.mcp.list_tools())`. Runtime is authoritative.

## Totals

- Agent registry (keyless): **107**
- MCP surface: **74**
- Internal tools (registered, absent from MCP): **48**
- Audit-only tools (listed in audit §2, not registered keyless): **4**
- Discovered but gated out of the keyless registry: **9**
- MCP surface tools not in the keyless registry: **15**

## Tool table

`mcp_counterpart_status`: mapped · no-equivalent · is-mcp-tool. Swarm refs list preset whitelists (agent roles in the JSON). Entries marked audit-only are not registered in the keyless environment.

| name | purpose | MCP counterpart | swarm refs | skill refs | gate |
|---|---|---|---|---|---|
| add_goal_evidence | Attach a concise evidence note, artifact reference, or tool result to the current research goal. | (on MCP surface) | — | — | none |
| alpha_bench | Bench a single alpha (alpha_id) or a whole zoo (zoo) on a universe over a period; computes IC mean/std/IR/positive-ratio per alpha and writes an HTML report. | (on MCP surface) | — | — | none |
| alpha_compare | Compare a hand-picked set of Alpha Zoo alphas (alpha_ids, >= 2) head-to-head on a universe over a period. | — (no equivalent) | — | — | none |
| alpha_zoo | Browse the bundled alpha zoo. | (on MCP surface) | — | — | none |
| analyze_image | Look at a local image with the multimodal LLM and answer a question about it. | — (no equivalent) | — | — | none |
| analyze_trade_journal | Analyze a user's trade journal (CSV/Excel broker export). | (on MCP surface) | portfolio_review_board | — | none |
| background_run — audit-only | Run a shell command in a tracked background process group. | — (no equivalent) | — | — | entrypoint flag --enable-shell-tools / env VIBE_TRADING_ENABLE_SHELL_TOOLS=1 |
| backtest | Run backtest: validate config.json + signal_engine.py, invoke built-in engine. | (on MCP surface) | commodity_research_team, convertible_bond_team, credit_research_team, earnings_research_desk, equity_research_team, etf_allocation_desk ×2, event_driven_task_force, factor_research_committee, fund_selection_panel, global_allocation_committee, global_equities_desk, investment_committee, macro_rates_fx_desk, ml_quant_lab, pairs_research_lab, quant_strategy_desk, sector_rotation_team, statistical_arbitrage_desk | — | none |
| bash — audit-only | Execute a shell command in the working directory. | — (no equivalent) | commodity_research_team ×3, convertible_bond_team ×4, credit_research_team ×4, crypto_research_lab ×4, crypto_trading_desk ×4, derivatives_strategy_desk ×3, earnings_research_desk ×4, equity_research_team ×4, etf_allocation_desk ×4, event_driven_task_force ×3, factor_research_committee ×4, fund_selection_panel ×3, fundamental_research_team ×4, geopolitical_war_room ×4, global_allocation_committee ×4, global_equities_desk ×4, investment_committee ×4, macro_rates_fx_desk ×4, macro_strategy_forum ×4, ml_quant_lab ×3, pairs_research_lab ×4, portfolio_review_board ×4, quant_strategy_desk ×5, risk_committee ×4, sector_rotation_team ×4, sentiment_intelligence_team ×4, social_alpha_team ×4, statistical_arbitrage_desk ×4, technical_analysis_panel ×6, value_investing_committee ×5 | ashare-pre-st-filter, backtest-diagnose, behavioral-finance, candlestick, chanlun, corporate-events, correlation-analysis, correlation-regime, doc-reader, elliott-wave, event-driven, factor-research, fund-analysis, fundamental-filter, harmonic, ichimoku, market-microstructure, minute-analysis, ml-strategy, multi-factor, okx-market, options-advanced, pair-trading, regulatory-knowledge, seasonal, smc, social-media-intelligence, strategy-dev-manager, strategy-generate, technical-basic, trade-journal, tushare, volatility, web-reader | entrypoint flag --enable-shell-tools / env VIBE_TRADING_ENABLE_SHELL_TOOLS=1 |
| cancel_background — audit-only | Cancel exactly one background_run task by task_id using its tracked process group/PID. | — (no equivalent) | — | — | entrypoint flag --enable-shell-tools / env VIBE_TRADING_ENABLE_SHELL_TOOLS=1 |
| cashflow_performance | Measure the return of an account that received client contributions or paid out withdrawals mid-period. | (on MCP surface) | — | — | none |
| check_background | Check background task status, elapsed time, and remaining time before the 300-second automatic timeout. | — (no equivalent) | — | — | none |
| compact | Compress conversation history to free context space. | — (no equivalent) | — | correlation-analysis | none |
| create_hypothesis | Create a durable research hypothesis in the local registry. | — (no equivalent) | — | strategy-dev-manager | none |
| delete_skill | Delete a user-created skill and all its files. | — (no equivalent) | — | — | none |
| edit_file | Find and replace the first occurrence of old_text with new_text in a file. | write_file | ml_quant_lab, quant_strategy_desk | backtest-diagnose, strategy-generate | none |
| etf_holdings | ETF look-through across two markets. | (on MCP surface) | fund_selection_panel | — | none |
| etoro_cancel_close_order | Cancel a pending eToro market close order by order id (paper only; live is fail-closed because this reinstates exposure). | — (no equivalent) | — | — | none |
| etoro_close_position | Close or partially close an open eToro position by position id. | — (no equivalent) | — | — | none |
| etoro_copy_close | Close or detach an eToro copy relationship by mirror id. | — (no equivalent) | — | — | none |
| etoro_copy_poll | Poll the outcome of an asynchronous eToro copy operation. | — (no equivalent) | — | — | none |
| etoro_copy_precheck | Dry-run whether the account can copy an investor with an account-currency amount. | — (no equivalent) | — | — | none |
| etoro_copy_start | Start copying an investor or adjust an existing copy allocation. | — (no equivalent) | — | — | none |
| etoro_edit_position_stops | Modify or clear stop-loss/take-profit on an open eToro position (paper only; live edits are fail-closed until incremental funding can be quantified). | — (no equivalent) | — | — | none |
| etoro_search_instruments | Search eToro instruments by ticker (BTC, AAPL), free text, or asset class (crypto, stocks, forex). | — (no equivalent) | — | — | none |
| extract_shadow_strategy | Extract implicit trading rules from the user's profitable roundtrips and produce a Shadow Account profile (3-5 human-readable if-then rules). | (on MCP surface) | — | — | none |
| factor_analysis | Factor analysis: compute IC/IR/layered NAV. | (on MCP surface) | convertible_bond_team, credit_research_team, equity_research_team ×2, etf_allocation_desk, event_driven_task_force, factor_research_committee ×3, fund_selection_panel ×2, global_allocation_committee, global_equities_desk, investment_committee ×2, ml_quant_lab, pairs_research_lab ×2, portfolio_review_board, quant_strategy_desk ×2, sector_rotation_team, social_alpha_team, statistical_arbitrage_desk | — | none |
| financial_rigor | Verify financial-data accuracy with exact decimal arithmetic (no float drift). | — (no equivalent) | fundamental_research_team ×2, value_investing_committee ×4 | bottleneck-hunter, data-routing, deep-company-series, management-deep-dive, research-discipline, thesis-tracker | none |
| generate_backtest_config | Generate a backtest config.json from a saved hypothesis. | — (no equivalent) | — | strategy-dev-manager | none |
| get_block_trades | Fetch recent A-share block trades (大宗交易) for one symbol from the Eastmoney datacenter: per-deal price, volume, amount, the premium/discount versus that day's close, and the buyer/seller broker seats (营业部). | (on MCP surface) | — | — | none |
| get_dragon_tiger | Fetch the A-share dragon-tiger board (龙虎榜) for a given trade date from Eastmoney's free datacenter API. | (on MCP surface) | — | — | none |
| get_financial_statements | Fetch a single stock's financial statements: balance sheet, income statement, cash-flow statement, or key per-period indicators (margins, ROE, EPS, etc.). | (on MCP surface) | convertible_bond_team, credit_research_team, fundamental_research_team ×2, value_investing_committee ×4 | — | none |
| get_fund_flow | PER-STOCK order-level net inflow for a GIVEN symbol: for each requested ticker, the main / super-large / large / medium / small-order net inflow (in CNY), as daily history or the current session's per-minute line. | (on MCP surface) | — | — | none |
| get_fundamentals | Fetch PIT-safe fundamental fields as daily wide panels aligned by filed date. | (on MCP surface) | fund_selection_panel | — | none |
| get_institutional_holdings | U.S. | (on MCP surface) | — | — | none |
| get_lockup_expiry | Fetch Chinese A-share lockup-expiry (restricted-share unlock, 限售解禁) data from Eastmoney. | (on MCP surface) | — | — | none |
| get_margin_trading | Fetch an A-share stock's daily margin-trading (融资融券) balances from Eastmoney's public datacenter: outstanding financing balance, financing buy amount, securities-lending balance, and combined RZRQ balance, one row per trading day (most recent first). | (on MCP surface) | — | — | none |
| get_market_data | Fetch normalized OHLCV market data through the repository loader layer. | (on MCP surface) | convertible_bond_team, credit_research_team, crypto_research_lab ×2, crypto_trading_desk ×2, derivatives_strategy_desk ×3, earnings_research_desk ×2, equity_research_team ×3, etf_allocation_desk ×2, event_driven_task_force, fund_selection_panel, global_allocation_committee ×3, global_equities_desk ×3, investment_committee ×3, macro_rates_fx_desk, macro_strategy_forum, pairs_research_lab ×2, portfolio_review_board ×3, quant_strategy_desk, risk_committee ×3, sector_rotation_team, sentiment_intelligence_team, statistical_arbitrage_desk ×3, technical_analysis_panel ×3, value_investing_committee ×4 | — | none |
| get_northbound_flow | MARKET-WIDE Northbound (Stock-Connect / 北向) net capital flow for the whole mainland China A-share market: the aggregate net inflow from Hong Kong, split into Shanghai-Connect (沪股通) and Shenzhen-Connect (深股通) channels (units: 10k CNY), as the latest realtime figure plus a recent daily history. | (on MCP surface) | — | — | none |
| get_options_chain | Fetch the US-listed options chain (calls and puts) for one expiration via Yahoo Finance: per-contract strike, bid/ask, last price, volume, open interest, implied volatility, and in-the-money flag, plus the list of available expirations (epoch seconds). | (on MCP surface) | derivatives_strategy_desk ×3, earnings_research_desk, investment_committee ×2, risk_committee | — | none |
| get_research_goal | Read the current finance research goal, criteria, claims, and latest evidence. | (on MCP surface) | — | — | none |
| get_research_reports | Fetch mainland A-share sell-side research coverage: recent broker research reports (title, brokerage, analyst, publish date, rating) with each broker's per-year EPS and PE forecasts from Eastmoney, plus the market consensus (mean) EPS forecast per forward fiscal year from THS (同花顺). | (on MCP surface) | — | — | none |
| get_sec_filings | Fetch U.S. | (on MCP surface) | — | — | none |
| get_sector_info | Look up Chinese A-share sector / concept board info via Eastmoney (free, no auth). | (on MCP surface) | — | — | none |
| get_shareholder_count | Fetch mainland A-share quarterly shareholder count (股东户数) from the Eastmoney datacenter: holder count per report period, quarter-over-quarter change (absolute and percent), and average holding (shares and market value) per account. | (on MCP surface) | — | — | none |
| get_stock_news | Fetch recent financial news headlines, read-only and no auth. | (on MCP surface) | event_driven_task_force | — | none |
| get_stock_profile | Fetch a read-only company profile for a US or Hong Kong listing from Yahoo Finance: valuation key statistics, analyst price targets and earnings/revenue estimates, institutional and insider ownership, and the analyst recommendation trend. | (on MCP surface) | credit_research_team, fund_selection_panel, fundamental_research_team ×2 | — | none |
| get_strategy_evidence | Return the computed per-regime evidence rows for one strategy. | (on MCP surface) | — | — | none |
| get_taiwan_stock_data — audit-only | Query the local read-only Taiwan stock snapshot for TWSE and TPEx stocks. | — (no equivalent) | — | — | env VIBE_TW_STOCK_DB (schema-valid SQLite snapshot) |
| link_autopilot_backtest | Read run_card.json from a completed backtest run directory, extract its metrics, and link the run to a research hypothesis. | — (no equivalent) | — | strategy-dev-manager | none |
| link_backtest | Attach a run card or backtest run directory to a research hypothesis. | — (no equivalent) | — | — | none |
| list_strategies | List discoverable strategies across the Alpha Zoo registry and the SDM strategy store. | (on MCP surface) | — | — | none |
| load_skill | Load documentation for a named skill. | (on MCP surface) | commodity_research_team ×3, convertible_bond_team ×4, credit_research_team ×4, crypto_research_lab ×4, crypto_trading_desk ×4, derivatives_strategy_desk ×3, earnings_research_desk ×4, equity_research_team ×4, etf_allocation_desk ×4, event_driven_task_force ×3, factor_research_committee ×4, fund_selection_panel ×3, fundamental_research_team ×4, geopolitical_war_room ×4, global_allocation_committee ×4, global_equities_desk ×4, investment_committee ×4, macro_rates_fx_desk ×4, macro_strategy_forum ×4, ml_quant_lab ×3, pairs_research_lab ×4, portfolio_review_board ×4, quant_strategy_desk ×5, risk_committee ×3, sector_rotation_team ×4, sentiment_intelligence_team ×4, social_alpha_team ×4, statistical_arbitrage_desk ×4, technical_analysis_panel ×5, value_investing_committee ×5 | — | none |
| options_payoff | Analyze a European multi-leg option strategy using deterministic piecewise-linear expiry math and Black-Scholes spot/IV scenarios. | analyze_options_payoff | investment_committee, risk_committee | options-payoff | none |
| options_pricing | Options pricing: compute theoretical price and Greeks using the Black-Scholes model. | analyze_options | convertible_bond_team, derivatives_strategy_desk ×3, earnings_research_desk, investment_committee, risk_committee | options-strategy | none |
| orderbook_depth | READ-ONLY crypto L2 order-book snapshot from OKX or Binance spot public REST (via ccxt) — the depth/quote feed equity sources here cannot provide. | (on MCP surface) | statistical_arbitrage_desk | — | none |
| patch_skill | Fix or update an existing skill by replacing specific text. | — (no equivalent) | — | — | none |
| pattern | Run chart pattern detection on backtest data (head-and-shoulders, double top/bottom, candlestick, support/resistance, etc.). | pattern_recognition | technical_analysis_panel ×2 | candlestick, data-routing, earnings-revision, factor-research, geopolitical-risk, harmonic, perp-funding-basis, quant-statistics, social-media-intelligence, trade-journal, us-etf-flow | none |
| portfolio_risk_xray | Portfolio risk x-ray: given symbols (and optional weights), fetch recent daily closes through the data fallback chain and compute concentration (HHI/effective N), annualized volatility, max drawdown, historical VaR/expected shortfall, diversification ratio, and correlation/beta. | — (no equivalent) | — | — | none |
| portfolio_summary | Read the latest sanitized snapshot of the user's locally configured read-only brokerage accounts. | — (no equivalent) | — | — | none |
| prediction_market | READ-ONLY prediction-market (event-contract) data from Polymarket's public endpoints — alternative data for event-driven research. | (on MCP surface) | — | — | none |
| propose_mandate_profiles | Propose 2-4 numbered bounded-autonomy live-trading mandate profiles for the user to pick from, each clamped to the account's hard ceilings. | — (no equivalent) | — | — | none |
| quantlib_call | Run a function from the tested finance-math library (src/quantlib): Black-Scholes and implied vol, bond math and curve fitting, Altman Z and Merton/KMV, stationarity/cointegration/GARCH/regime switching, VaR/CVaR/EVT and VaR backtesting (Kupiec/Christoffersen/Basel), Brinson-Fachler attribution, market impact, fund maths (XIRR/MOIC/DPI/TVPI/PME/waterfalls), TWR/Dietz/MWR, event studies (CAR/CAAR/Patell/BMP), style factor models, deflated Sharpe and PBO, purged cross-validation, and the valuation engine (DCF / comps / three-statement). | (on MCP surface) | — | — | none |
| query_strategies | Query the strategy store for strategies whose computed evidence passes the given filters. | (on MCP surface) | — | — | none |
| read_document | Read a document of any common format: PDF, Word (.docx), Excel (.xlsx/.xls), PowerPoint (.pptx), images (OCR), or plain text (txt/md/json/yaml/csv/html/code files). | (on MCP surface) | — | — | none |
| read_file | Read a file from the workspace. | (on MCP surface) | commodity_research_team ×3, convertible_bond_team ×4, credit_research_team ×4, crypto_research_lab ×4, crypto_trading_desk ×4, derivatives_strategy_desk ×3, earnings_research_desk ×4, equity_research_team ×4, etf_allocation_desk ×4, event_driven_task_force ×3, factor_research_committee ×4, fund_selection_panel ×3, fundamental_research_team ×4, geopolitical_war_room ×4, global_allocation_committee ×4, global_equities_desk ×4, investment_committee ×4, macro_rates_fx_desk ×4, macro_strategy_forum ×4, ml_quant_lab ×3, pairs_research_lab ×4, portfolio_review_board ×4, quant_strategy_desk ×5, risk_committee ×4, sector_rotation_team ×4, sentiment_intelligence_team ×4, social_alpha_team ×4, statistical_arbitrage_desk ×4, technical_analysis_panel ×6, value_investing_committee ×5 | — | none |
| read_url | Fetch web page content: provide a URL and receive the page as Markdown text. | (on MCP surface) | commodity_research_team ×2, convertible_bond_team, credit_research_team ×3, crypto_research_lab ×2, crypto_trading_desk ×2, earnings_research_desk ×2, equity_research_team, etf_allocation_desk, event_driven_task_force ×2, fundamental_research_team, geopolitical_war_room ×3, global_allocation_committee, global_equities_desk, macro_rates_fx_desk ×3, macro_strategy_forum ×3, sentiment_intelligence_team ×2, social_alpha_team ×3 | — | none |
| refresh_strategy_evidence | Rebuild the strategy-discovery evidence cache from real backtest run artifacts. | (on MCP surface) | — | — | none |
| remember | Persistent cross-session memory. | — (no equivalent) | — | asset-allocation | none |
| render_shadow_report | Generate the Shadow Account PDF (8 sections + charts) for a shadow_id. | (on MCP surface) | — | — | none |
| report_audit | Audit a research report's numeric data points for accuracy before publishing. | — (no equivalent) | value_investing_committee | bottleneck-hunter, deep-company-series, management-deep-dive, private-company-research, research-discipline | none |
| research_papers | Search academic finance/ML papers and turn a paper into a FACTOR BRIEF that feeds our own factor loop. | (on MCP surface) | — | — | none |
| run_research_autopilot | Start a research goal from a saved hypothesis. | — (no equivalent) | — | — | none |
| run_shadow_backtest | Run a multi-market backtest (A股/港股/美股/crypto) on a Shadow Account profile and compute delta-PnL attribution vs the user's realized trades. | (on MCP surface) | — | — | none |
| run_swarm | Run a multi-agent swarm team for complex analysis tasks. | (on MCP surface) | — | — | none |
| save_skill | Save a successful workflow or strategy template as a reusable skill. | — (no equivalent) | — | — | none |
| scaffold_signal_engine | Write a contract-correct code/signal_engine.py stub into a backtest run directory for a saved hypothesis. | — (no equivalent) | — | strategy-dev-manager | none |
| scan_shadow_signals | List today's symbols that fall within the Shadow Account's entry cadence (research use only — not a trade recommendation). | (on MCP surface) | — | — | none |
| scheduled_research | Inspect scheduled research and prepare create/cancel proposals. | — (no equivalent) | — | — | none |
| screen_market | Screen a whole market's listed instruments and return the top names ranked by a chosen metric: percent change, traded volume, turnover value (amount) or turnover rate. | (on MCP surface) | pairs_research_lab, statistical_arbitrage_desk | — | none |
| sdm_decay_scan | Run decay monitoring scan on active factors/strategies. | — (no equivalent) | — | strategy-dev-manager | none |
| sdm_register | Register a new factor or strategy extracted from a paper into the strategy store. | — (no equivalent) | — | strategy-dev-manager | none |
| sdm_status | Query or update factor/strategy status in the strategy store. | — (no equivalent) | — | strategy-dev-manager | none |
| search_hypotheses | Search hypotheses by text query and/or lifecycle status. | — (no equivalent) | — | — | none |
| search_symbol | Resolve a company name or ticker fragment to candidate trading symbols with their market, in the project's symbol convention (A-shares 600519.SH, Hong Kong 00700.HK, U.S. | (on MCP surface) | — | — | none |
| sentiment | Analyze market sentiment. | (on MCP surface) | — | — | none |
| session_search | Search past conversation sessions by keyword. | — (no equivalent) | — | — | none |
| skill_file | Manage auxiliary files in a skill directory. | — (no equivalent) | — | — | none |
| start_research_goal | Start or replace the current finance research goal for this session. | (on MCP surface) | — | — | none |
| technical_indicators | Compute common technical indicators (RSI, MACD, Bollinger Bands, SMA, EMA) for a trading symbol. | (on MCP surface) | risk_committee, technical_analysis_panel ×2 | — | none |
| trading_acc_cash_flow | Read account cash-flow movements for a clearing date (YYYY-MM-DD): deposit, withdrawal, FX, settlement, fees. | — (no equivalent) | — | — | none |
| trading_account | Read account summary from the selected trading connector profile. | (on MCP surface) | — | — | none |
| trading_cancel_order | Cancel an open order on the selected trading connector profile by order id. | — (no equivalent) | — | — | none |
| trading_capital_distribution | Read today's capital in-flow vs out-flow snapshot (super/big/mid/small buckets) for a symbol. | — (no equivalent) | — | — | none |
| trading_capital_flow | Read historical capital flow time series (institutional / retail inflow-outflow) for a symbol. | — (no equivalent) | — | — | none |
| trading_check | Check whether a trading connector profile is configured and reachable. | (on MCP surface) | — | — | none |
| trading_connections | List selectable trading connector profiles. | (on MCP surface) | — | — | none |
| trading_earnings_calendar | Read upcoming earnings calendar (code, name, EPS/revenue forecast, IV, IV rank) for US / HK. | — (no equivalent) | — | — | none |
| trading_financials | Read financial statements (INCOME / BALANCE / CASH_FLOW) for a symbol. | — (no equivalent) | — | — | none |
| trading_history | Read historical bars from the selected trading connector profile. | (on MCP surface) | — | — | none |
| trading_history_deals | Read historical FILL records (executed deals) for shadow-account cost-basis reconstruction. | — (no equivalent) | — | — | none |
| trading_orders | Read open orders from the selected trading connector profile. | (on MCP surface) | — | — | none |
| trading_place_order | Place an order through the selected trading connector profile. | — (no equivalent) | — | — | none |
| trading_positions | Read positions from the selected trading connector profile. | (on MCP surface) | — | — | none |
| trading_quote | Read a quote snapshot from the selected trading connector profile. | (on MCP surface) | — | — | none |
| trading_rehab | Read dividend / split / rights-issue adjustment factors for a symbol from the selected trading connector profile. | — (no equivalent) | — | — | none |
| trading_select_connection | Select the default trading connector profile for subsequent trading_* tool calls. | (on MCP surface) | — | — | none |
| update_hypothesis | Update a hypothesis, including lifecycle status and invalidation notes. | — (no equivalent) | — | — | none |
| update_research_goal_status | Update the current finance research goal status. | (on MCP surface) | — | — | none |
| web_search | Search the web across free engines (DuckDuckGo, Google, Bing, Brave, Mojeek, Yahoo). | (on MCP surface) | value_investing_committee ×4 | — | none |
| write_file | Write content to a file in the workspace. | (on MCP surface) | commodity_research_team ×3, convertible_bond_team ×4, credit_research_team ×4, crypto_research_lab ×4, crypto_trading_desk ×4, derivatives_strategy_desk ×3, earnings_research_desk ×4, equity_research_team ×4, etf_allocation_desk ×4, event_driven_task_force ×3, factor_research_committee ×4, fund_selection_panel ×3, fundamental_research_team ×4, geopolitical_war_room ×4, global_allocation_committee ×4, global_equities_desk ×4, investment_committee ×4, macro_rates_fx_desk ×4, macro_strategy_forum ×4, ml_quant_lab ×3, pairs_research_lab ×4, portfolio_review_board ×4, quant_strategy_desk ×5, risk_committee ×4, sector_rotation_team ×4, sentiment_intelligence_team ×4, social_alpha_team ×4, statistical_arbitrage_desk ×4, technical_analysis_panel ×6, value_investing_committee ×5 | — | none |

## Reconciliation against audit §2

Audit partial list: 14 rows expanding to 28 runtime tool names. Confirmed at runtime: 24.

### Confirmed (audit-listed and registered keyless)

`create_hypothesis`, `delete_skill`, `edit_file`, `financial_rigor`, `link_autopilot_backtest`, `link_backtest`, `options_payoff`, `options_pricing`, `patch_skill`, `pattern`, `portfolio_risk_xray`, `portfolio_summary`, `remember`, `report_audit`, `run_research_autopilot`, `save_skill`, `scaffold_signal_engine`, `scheduled_research`, `sdm_decay_scan`, `sdm_register`, `sdm_status`, `search_hypotheses`, `skill_file`, `update_hypothesis`

### New since audit (internal, registered, not in the audit list)

| name | purpose |
|---|---|
| alpha_compare | Compare a hand-picked set of Alpha Zoo alphas (alpha_ids, >= 2) head-to-head on a universe over a period. |
| analyze_image | Look at a local image with the multimodal LLM and answer a question about it. |
| check_background | Check background task status, elapsed time, and remaining time before the 300-second automatic timeout. |
| compact | Compress conversation history to free context space. |
| etoro_cancel_close_order | Cancel a pending eToro market close order by order id (paper only; live is fail-closed because this reinstates exposure). |
| etoro_close_position | Close or partially close an open eToro position by position id. |
| etoro_copy_close | Close or detach an eToro copy relationship by mirror id. |
| etoro_copy_poll | Poll the outcome of an asynchronous eToro copy operation. |
| etoro_copy_precheck | Dry-run whether the account can copy an investor with an account-currency amount. |
| etoro_copy_start | Start copying an investor or adjust an existing copy allocation. |
| etoro_edit_position_stops | Modify or clear stop-loss/take-profit on an open eToro position (paper only; live edits are fail-closed until incremental funding can be quantified). |
| etoro_search_instruments | Search eToro instruments by ticker (BTC, AAPL), free text, or asset class (crypto, stocks, forex). |
| generate_backtest_config | Generate a backtest config.json from a saved hypothesis. |
| propose_mandate_profiles | Propose 2-4 numbered bounded-autonomy live-trading mandate profiles for the user to pick from, each clamped to the account's hard ceilings. |
| session_search | Search past conversation sessions by keyword. |
| trading_acc_cash_flow | Read account cash-flow movements for a clearing date (YYYY-MM-DD): deposit, withdrawal, FX, settlement, fees. |
| trading_cancel_order | Cancel an open order on the selected trading connector profile by order id. |
| trading_capital_distribution | Read today's capital in-flow vs out-flow snapshot (super/big/mid/small buckets) for a symbol. |
| trading_capital_flow | Read historical capital flow time series (institutional / retail inflow-outflow) for a symbol. |
| trading_earnings_calendar | Read upcoming earnings calendar (code, name, EPS/revenue forecast, IV, IV rank) for US / HK. |
| trading_financials | Read financial statements (INCOME / BALANCE / CASH_FLOW) for a symbol. |
| trading_history_deals | Read historical FILL records (executed deals) for shadow-account cost-basis reconstruction. |
| trading_place_order | Place an order through the selected trading connector profile. |
| trading_rehab | Read dividend / split / rights-issue adjustment factors for a symbol from the selected trading connector profile. |

### Audit-only (listed in audit §2, missing from the keyless registry)

| audit row | runtime name | explanation |
|---|---|---|
| bash / background_run / cancel_background | background_run | Not registered in the keyless environment: shell tools register only when the entry point enables them (--enable-shell-tools / VIBE_TRADING_ENABLE_SHELL_TOOLS=1); never exposed on MCP by design. |
| bash / background_run / cancel_background | bash | Not registered in the keyless environment: shell tools register only when the entry point enables them (--enable-shell-tools / VIBE_TRADING_ENABLE_SHELL_TOOLS=1); never exposed on MCP by design. |
| bash / background_run / cancel_background | cancel_background | Not registered in the keyless environment: shell tools register only when the entry point enables them (--enable-shell-tools / VIBE_TRADING_ENABLE_SHELL_TOOLS=1); never exposed on MCP by design. |
| taiwan_stock_data | get_taiwan_stock_data | Not registered in the keyless environment: check_available requires VIBE_TW_STOCK_DB to point at a schema-valid SQLite snapshot; agent-side only, never exposed on MCP. |

### Discovered but gated out of the keyless registry

| name | gate | on MCP surface | note |
|---|---|---|---|
| background_run | entrypoint flag --enable-shell-tools / env VIBE_TRADING_ENABLE_SHELL_TOOLS=1 | no | Agent-side only; not exposed on the MCP surface. |
| bash | entrypoint flag --enable-shell-tools / env VIBE_TRADING_ENABLE_SHELL_TOOLS=1 | no | Agent-side only; not exposed on the MCP surface. |
| cancel_background | entrypoint flag --enable-shell-tools / env VIBE_TRADING_ENABLE_SHELL_TOOLS=1 | no | Agent-side only; not exposed on the MCP surface. |
| get_macro_series | env FRED_API_KEY | yes | Exposed on the MCP surface; hidden from the keyless agent registry by its gate. |
| get_taiwan_stock_data | env VIBE_TW_STOCK_DB (schema-valid SQLite snapshot) | no | Agent-side only; not exposed on the MCP surface. |
| iwencai_search | env VIBE_TRADING_IWENCAI_KEY | yes | Exposed on the MCP surface; hidden from the keyless agent registry by its gate. |
| qveris_execute | env QVERIS_API_KEY + paid mode | yes | Exposed on the MCP surface; hidden from the keyless agent registry by its gate. |
| qveris_inspect | env QVERIS_API_KEY + paid mode | yes | Exposed on the MCP surface; hidden from the keyless agent registry by its gate. |
| qveris_search | env QVERIS_API_KEY + paid mode | yes | Exposed on the MCP surface; hidden from the keyless agent registry by its gate. |

## Audit §8.1 name mapping (as encoded)

| internal tool | MCP counterpart |
|---|---|
| edit_file | write_file |
| financial_rigor | — (no equivalent) |
| options_payoff | analyze_options_payoff |
| options_pricing | analyze_options |
| pattern | pattern_recognition |
| report_audit | — (no equivalent) |
| sdm_decay_scan | — (no equivalent) |
| sdm_register | — (no equivalent) |
| sdm_status | — (no equivalent) |
