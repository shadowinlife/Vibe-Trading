# LLM-judge panel statistics report

Paired design: every query is its own control across the frozen
baseline corpus and the current post corpus. Headline test: exact
McNemar (two-sided binomial on discordant pairs); Wilson 95% CIs per
surface. Deterministic given the golden traces.

- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`

## Panel and pins

| model | role | provider | temperature | max_response_tokens |
|---|---|---|---|---|
| qwen3.8-max | primary | dashscope | 0.0 | 500 |
| deepseek-v4-flash-0731 | sensitivity | dashscope | 0.0 | 2000 |
| kimi-k3 | sensitivity | dashscope | 0.0 | 1000 |
| glm-5.2 | sensitivity | dashscope | 0.0 | 2000 |

- budget caps per (model, surface) run: 25000000 tokens / 700 calls
- price table: **estimate:true** — cost figures below are unverified

## Per-model paired results (exact McNemar)

### Top-1

| model | n pairs | baseline (95% CI) | post (95% CI) | Δ | improved | regressed | McNemar p |
|---|---|---|---|---|---|---|---|
| qwen3.8-max | 158 | 0.8861 [0.8271, 0.9267] | 0.9051 [0.8493, 0.9416] | +0.0190 | 9 | 6 | 0.6072 |
| deepseek-v4-flash-0731 | 158 | 0.5633 [0.4854, 0.6382] | 0.5696 [0.4917, 0.6443] | +0.0063 | 21 | 20 | 1 |
| kimi-k3 | 158 | 0.8418 [0.7769, 0.8905] | 0.8987 [0.8418, 0.9367] | +0.0570 | 13 | 4 | 0.04904 |
| glm-5.2 | 158 | 0.8797 [0.8198, 0.9216] | 0.8671 [0.8054, 0.9114] | -0.0127 | 11 | 13 | 0.8388 |

### Top-3

| model | n pairs | baseline (95% CI) | post (95% CI) | Δ | improved | regressed | McNemar p |
|---|---|---|---|---|---|---|---|
| qwen3.8-max | 158 | 0.9684 [0.9281, 0.9864] | 0.9620 [0.9196, 0.9825] | -0.0063 | 5 | 6 | 1 |
| deepseek-v4-flash-0731 | 158 | 0.6076 [0.5298, 0.6803] | 0.6076 [0.5298, 0.6803] | +0.0000 | 22 | 22 | 1 |
| kimi-k3 | 158 | 0.9051 [0.8493, 0.9416] | 0.9684 [0.9281, 0.9864] | +0.0633 | 12 | 2 | 0.01294 |
| glm-5.2 | 158 | 0.9430 [0.8953, 0.9697] | 0.9494 [0.9033, 0.9741] | +0.0063 | 9 | 8 | 1 |

### Negative false-recall and invalid responses (plain rates)

| model | neg baseline | neg post | Δ | invalid baseline | invalid post | Δ |
|---|---|---|---|---|---|---|
| qwen3.8-max | 0.0077 (130) | 0.0000 (130) | -0.0077 | 0.0127 (158) | 0.0063 (158) | -0.0063 |
| deepseek-v4-flash-0731 | 0.0231 (130) | 0.0231 (130) | +0.0000 | 0.0190 (158) | 0.0190 (158) | +0.0000 |
| kimi-k3 | 0.0077 (130) | 0.0000 (130) | -0.0077 | 0.0000 (158) | 0.0000 (158) | +0.0000 |
| glm-5.2 | 0.0154 (130) | 0.0077 (130) | -0.0077 | 0.0127 (158) | 0.0000 (158) | -0.0127 |

## Pooled across models

Model is a stratification variable here: pooling assumes the
description change acts in the same direction across judges. Check
the per-model table for heterogeneity before trusting pooled p-values.

- per-model heterogeneity (top-1 Δ): 3 improved / 1 regressed / 0 flat (of 4 models with paired data)
- pooled pairs: 632
- pooled top-1: baseline 0.7927 -> post 0.8101, Δ +0.0174, improved 54 / regressed 43, McNemar p 0.3099
- pooled top-3: baseline 0.8560 -> post 0.8718, Δ +0.0158, improved 48 / regressed 38, McNemar p 0.3318

## Flip lists (top-1 outcome flipped between surfaces)

### qwen3.8-max (15 flips)

| query_id | direction | baseline first | post first |
|---|---|---|---|
| D01-001 | improved | - | tool:get_market_data |
| D01-003 | regressed | tool:screen_market | - |
| D01-006 | improved | - | tool:orderbook_depth |
| D02-005 | regressed | tool:get_sec_filings | - |
| D02-006 | improved | skill:sec-edgar | skill:sec-edgar-fetch |
| D02-010 | regressed | skill:fundamental-filter | iwencai_search |
| D05-003 | improved | tool:technical_indicators | skill:technical-basic |
| D06-007 | regressed | skill:alpha-zoo | tool:alpha_zoo |
| D08-003 | regressed | tool:get_options_chain | - |
| D09-001 | regressed | tool:quantlib_call | - |
| D09-005 | improved | tool:quantlib_call | skill:quant-statistics |
| D09-006 | improved | - | skill:correlation-analysis |
| D13-001 | improved | - | tool:sentiment |
| D16-007 | improved | tool:trading_connections | tool:trading_quote |
| D16-008 | improved | tool:trading_connections | tool:trading_history |

### deepseek-v4-flash-0731 (41 flips)

| query_id | direction | baseline first | post first |
|---|---|---|---|
| D01-001 | improved | get_market_data | tool:get_market_data |
| D01-002 | regressed | tool:get_market_data | get_market_data |
| D01-003 | improved | screen_market | tool:screen_market |
| D01-005 | regressed | tool:search_symbol | search_symbol |
| D02-001 | regressed | tool:get_financial_statements | get_financial_statements |
| D02-004 | regressed | tool:get_fundamentals | get_fundamentals |
| D02-006 | improved | skill:sec-edgar | skill:sec-edgar-fetch |
| D03-002 | regressed | tool:get_stock_news | get_stock_news |
| D04-001 | regressed | tool:get_fund_flow | get_fund_flow |
| D04-007 | improved | get_shareholder_count | tool:get_shareholder_count |
| D05-003 | regressed | skill:technical-basic | technical_indicators |
| D05-006 | regressed | tool:pattern_recognition | pattern_recognition |
| D06-001 | regressed | tool:alpha_zoo | skill:alpha-zoo |
| D06-002 | improved | query_strategies | tool:query_strategies |
| D06-003 | improved | list_strategies | tool:list_strategies |
| D06-004 | improved | alpha_bench | tool:alpha_bench |
| D06-008 | regressed | skill:multi-factor | - |
| D08-004 | improved | options-strategy | skill:options-strategy |
| D09-002 | regressed | tool:quantlib_call | quantlib_call |
| D09-005 | improved | quantlib_call | skill:quant-statistics |
| D10-001 | improved | quantlib_call | skill:valuation-model |
| D10-003 | improved | - | skill:management-deep-dive |
| D11-001 | improved | get_macro_series | tool:get_macro_series |
| D11-004 | improved | macro-analysis | skill:macro-analysis |
| D12-002 | improved | prediction_market | tool:prediction_market |
| D13-002 | regressed | tool:sentiment | sentiment |
| D14-005 | regressed | skill:credit-analysis | credit-analysis |
| D15-001 | improved | analyze_trade_journal | tool:analyze_trade_journal |
| D15-002 | improved | - | tool:analyze_trade_journal |
| D15-004 | regressed | tool:run_shadow_backtest | skill:shadow-account |
| D16-001 | regressed | tool:trading_connections | trading_connections |
| D16-006 | improved | trading_connections | tool:trading_orders |
| D17-001 | regressed | tool:start_research_goal | start_research_goal |
| D17-003 | improved | add_goal_evidence | tool:add_goal_evidence |
| D17-008 | regressed | tool:list_skills | - |
| D18-001 | regressed | tool:qveris_search | qveris_search |
| D18-002 | regressed | tool:qveris_inspect | qveris_inspect |
| D18-004 | regressed | skill:qveris | skill:data-routing |
| D19-001 | improved | web_search | tool:web_search |
| D19-003 | improved | read_document | tool:read_document |
| D19-004 | improved | read_document | tool:read_document |

### kimi-k3 (17 flips)

| query_id | direction | baseline first | post first |
|---|---|---|---|
| D02-002 | improved | get_financial_statements | tool:get_financial_statements |
| D02-006 | improved | skill:sec-edgar | skill:sec-edgar-fetch |
| D02-008 | improved | get_institutional_holdings | tool:get_institutional_holdings |
| D03-005 | improved | tool:web_search | tool:get_stock_news |
| D04-002 | improved | get_northbound_flow | tool:get_northbound_flow |
| D04-003 | improved | get_margin_trading | tool:get_margin_trading |
| D06-002 | improved | query_strategies | tool:query_strategies |
| D07-002 | improved | backtest | tool:backtest |
| D07-009 | regressed | tool:write_file | write_file |
| D08-001 | improved | analyze_options | tool:analyze_options |
| D08-003 | improved | get_options_chain | tool:get_options_chain |
| D16-001 | regressed | tool:trading_connections | trading_connections |
| D17-006 | regressed | tool:retry_run | tool:list_runs |
| D18-001 | improved | qveris_search | tool:qveris_search |
| D18-005 | improved | qveris_execute | tool:qveris_execute |
| D19-001 | improved | web_search | tool:web_search |
| D19-005 | regressed | skill:web-reader | tool:read_url |

### glm-5.2 (24 flips)

| query_id | direction | baseline first | post first |
|---|---|---|---|
| D01-001 | improved | - | tool:get_market_data |
| D01-003 | regressed | tool:screen_market | screen_market |
| D01-007 | improved | iwencai_search | tool:iwencai_search |
| D02-001 | regressed | tool:get_financial_statements | get_financial_statements |
| D02-003 | regressed | skill:financial-statement | tool:load_skill |
| D02-006 | improved | skill:sec-edgar | skill:sec-edgar-fetch |
| D02-008 | regressed | tool:get_institutional_holdings | get_institutional_holdings |
| D03-002 | improved | - | tool:get_stock_news |
| D03-005 | regressed | tool:get_stock_news | tool:web_search |
| D04-001 | regressed | tool:get_fund_flow | get_fund_flow |
| D04-007 | regressed | tool:get_shareholder_count | get_shareholder_count |
| D07-001 | improved | tool:write_file | tool:backtest |
| D09-005 | improved | tool:quantlib_call | skill:quant-statistics |
| D10-005 | improved | thesis-tracker | skill:thesis-tracker |
| D12-001 | improved | prediction_market | tool:prediction_market |
| D12-002 | improved | prediction_market | tool:prediction_market |
| D14-003 | regressed | skill:fund-analysis | tool:list_skills |
| D15-003 | regressed | tool:extract_shadow_strategy | skill:shadow-account |
| D16-003 | regressed | tool:trading_check | tool:trading_connections |
| D16-007 | improved | tool:trading_connections | tool:trading_quote |
| D18-004 | regressed | skill:qveris | skill:data-routing |
| D18-005 | regressed | tool:qveris_execute | qveris_execute |
| D19-001 | regressed | tool:web_search | web_search |
| D19-006 | improved | tool:read_document | skill:doc-reader |

## Lexical-vs-semantic agreement (run_eval proxy blind spots)

Agreement of the lexical top-1 outcome with the LLM-judge top-1
outcome per query; disagreements are where the lexical proxy cannot
see what the judge sees.

| model | surface | agreement | disagreements |
|---|---|---|---|
| qwen3.8-max | baseline | 0.5290 | 73 |
| qwen3.8-max | post | 0.4937 | 80 |
| deepseek-v4-flash-0731 | baseline | 0.5419 | 71 |
| deepseek-v4-flash-0731 | post | 0.5253 | 75 |
| kimi-k3 | baseline | 0.5226 | 74 |
| kimi-k3 | post | 0.4873 | 81 |
| glm-5.2 | baseline | 0.4968 | 78 |
| glm-5.2 | post | 0.5063 | 78 |

disagreement set — qwen3.8-max / baseline: D01-003, D01-004, D01-008, D02-001, D02-008, D02-009, D02-010, D03-001, D03-003, D04-001, D04-002, D04-008, D04-009, D05-006, D05-008, D05-009, D05-011, D06-001, D06-002, D06-003, D06-004, D06-005, D06-006, D06-008, D06-009, D07-001, D07-002, D07-003, D07-004, D07-008, D07-009, D07-010, D08-001, D08-003, D08-004, D08-006, D08-008, D09-002, D09-003, D09-008, D09-009, D10-001, D10-002, D10-003, D10-005, D10-006, D11-003, D11-004, D11-006, D12-001, D12-006, D13-002, D13-004, D13-005, D13-006, D13-008, D14-005, D14-007, D15-001, D15-003, D15-004, D15-006, D16-001, D16-007, D17-001, D17-003, D17-007, D17-008, D17-010, D18-001, D18-003, D18-005, D19-001

disagreement set — qwen3.8-max / post: D01-001, D01-004, D01-006, D01-008, D02-001, D02-005, D02-007, D02-008, D02-009, D03-001, D03-003, D04-001, D04-002, D04-003, D04-008, D04-009, D05-003, D05-006, D05-008, D05-009, D05-011, D06-001, D06-002, D06-003, D06-004, D06-005, D06-006, D06-007, D06-008, D06-009, D07-001, D07-002, D07-003, D07-004, D07-008, D07-010, D08-001, D08-004, D08-006, D08-008, D09-001, D09-002, D09-003, D09-004, D09-005, D09-006, D09-008, D09-009, D10-001, D10-002, D10-003, D10-005, D10-006, D11-003, D11-004, D11-006, D12-001, D12-006, D13-001, D13-004, D13-005, D13-006, D13-008, D14-005, D14-007, D15-001, D15-003, D15-004, D15-006, D16-001, D16-008, D17-001, D17-003, D17-007, D17-008, D17-010, D18-001, D18-003, D18-005, D19-001

disagreement set — deepseek-v4-flash-0731 / baseline: D01-004, D01-007, D01-008, D02-001, D02-002, D04-001, D04-003, D04-004, D04-005, D04-006, D04-007, D04-008, D04-009, D05-001, D05-002, D05-003, D05-006, D05-008, D05-009, D05-011, D06-001, D06-005, D06-007, D06-008, D06-009, D07-003, D07-004, D07-008, D07-010, D08-001, D08-002, D08-006, D08-008, D09-001, D09-002, D09-006, D09-008, D09-009, D10-002, D10-005, D10-006, D11-001, D11-002, D11-006, D12-002, D12-006, D13-001, D13-002, D13-004, D13-005, D13-006, D13-008, D14-001, D14-002, D14-005, D14-007, D15-002, D15-003, D15-004, D16-001, D16-003, D16-005, D16-007, D17-001, D17-002, D17-005, D17-008, D17-010, D18-001, D19-003, D19-004

disagreement set — deepseek-v4-flash-0731 / post: D01-001, D01-002, D01-003, D01-004, D01-005, D01-007, D01-008, D02-002, D02-004, D02-005, D02-007, D03-002, D04-004, D04-005, D04-006, D04-008, D04-009, D05-001, D05-002, D05-008, D05-009, D05-011, D06-002, D06-003, D06-004, D06-005, D06-007, D06-009, D07-003, D07-004, D07-008, D07-009, D07-010, D08-001, D08-002, D08-004, D08-006, D08-008, D09-001, D09-004, D09-005, D09-006, D09-008, D09-009, D10-001, D10-002, D10-003, D10-005, D10-006, D11-002, D11-004, D11-006, D12-006, D13-001, D13-002, D13-004, D13-005, D13-006, D13-008, D14-001, D14-002, D14-007, D15-001, D15-003, D16-003, D16-005, D16-006, D16-007, D17-002, D17-003, D17-005, D17-010, D18-002, D18-004, D19-001

disagreement set — kimi-k3 / baseline: D01-001, D01-003, D01-004, D01-006, D01-008, D02-001, D02-002, D02-009, D03-001, D03-003, D04-001, D04-003, D04-008, D04-009, D05-006, D05-008, D05-009, D05-011, D06-003, D06-004, D06-005, D06-006, D06-008, D06-009, D07-001, D07-003, D07-004, D07-008, D07-009, D07-010, D08-004, D08-006, D08-008, D09-002, D09-003, D09-005, D09-006, D09-008, D09-009, D10-001, D10-002, D10-003, D10-005, D10-006, D11-003, D11-004, D11-006, D12-001, D12-006, D13-001, D13-002, D13-004, D13-005, D13-006, D13-008, D14-005, D14-007, D15-001, D15-003, D15-004, D15-005, D15-006, D16-001, D16-005, D16-006, D16-007, D17-001, D17-003, D17-006, D17-007, D17-008, D17-010, D18-003, D19-005

disagreement set — kimi-k3 / post: D01-001, D01-003, D01-004, D01-006, D01-008, D02-001, D02-007, D02-008, D02-009, D03-001, D03-003, D03-005, D04-001, D04-002, D04-003, D04-008, D04-009, D05-006, D05-008, D05-009, D05-011, D06-002, D06-003, D06-004, D06-005, D06-006, D06-008, D06-009, D07-001, D07-002, D07-003, D07-004, D07-008, D07-009, D07-010, D08-001, D08-003, D08-004, D08-006, D08-008, D09-002, D09-003, D09-004, D09-005, D09-006, D09-008, D09-009, D10-001, D10-002, D10-003, D10-005, D10-006, D11-003, D11-004, D11-006, D12-001, D12-006, D13-001, D13-004, D13-005, D13-006, D13-008, D14-005, D14-007, D15-001, D15-003, D15-004, D15-005, D15-006, D16-005, D16-006, D16-007, D17-001, D17-003, D17-007, D17-008, D17-010, D18-001, D18-003, D18-005, D19-001

disagreement set — glm-5.2 / baseline: D01-003, D01-004, D01-006, D01-007, D01-008, D02-001, D02-008, D02-009, D02-010, D03-001, D03-002, D03-003, D03-005, D04-001, D04-002, D04-008, D04-009, D05-006, D05-008, D05-009, D05-011, D06-001, D06-002, D06-003, D06-004, D06-005, D06-006, D06-008, D06-009, D07-002, D07-003, D07-004, D07-008, D07-009, D07-010, D08-001, D08-003, D08-004, D08-006, D08-008, D09-002, D09-003, D09-006, D09-008, D09-009, D10-001, D10-002, D10-003, D10-006, D11-003, D11-004, D11-006, D12-002, D12-006, D13-001, D13-002, D13-004, D13-005, D13-006, D13-008, D14-005, D14-007, D15-001, D15-003, D15-004, D15-006, D16-001, D16-006, D16-007, D17-001, D17-003, D17-007, D17-008, D17-010, D18-001, D18-003, D18-005, D19-001

disagreement set — glm-5.2 / post: D01-001, D01-004, D01-006, D01-008, D02-003, D02-007, D02-009, D02-010, D03-001, D03-003, D04-002, D04-003, D04-007, D04-008, D04-009, D05-006, D05-008, D05-009, D05-011, D06-001, D06-002, D06-003, D06-004, D06-005, D06-006, D06-008, D06-009, D07-001, D07-002, D07-003, D07-004, D07-008, D07-010, D08-001, D08-003, D08-004, D08-006, D08-008, D09-002, D09-003, D09-004, D09-005, D09-006, D09-008, D09-009, D10-001, D10-002, D10-003, D10-005, D10-006, D11-003, D11-004, D11-006, D12-001, D12-006, D13-001, D13-004, D13-005, D13-006, D13-008, D14-003, D14-005, D14-007, D15-001, D15-004, D15-006, D16-001, D16-003, D16-006, D17-001, D17-003, D17-007, D17-008, D17-010, D18-001, D18-003, D18-004, D19-006

## Determinism audit (probe first-pick agreement)

| model | surface | queries | repeats | agreement | confidence |
|---|---|---|---|---|---|
| qwen3.8-max | baseline | 8 | 3 | 0.9583 | ok |
| qwen3.8-max | post | 8 | 3 | 0.9167 | REDUCED-CONFIDENCE (<95%) |
| deepseek-v4-flash-0731 | baseline | 8 | 3 | 0.9167 | REDUCED-CONFIDENCE (<95%) |
| deepseek-v4-flash-0731 | post | 8 | 3 | 0.8750 | REDUCED-CONFIDENCE (<95%) |
| kimi-k3 | baseline | 8 | 3 | 1.0000 | ok |
| kimi-k3 | post | 8 | 3 | 1.0000 | ok |
| glm-5.2 | baseline | 8 | 3 | 1.0000 | ok |
| glm-5.2 | post | 8 | 3 | 0.8750 | REDUCED-CONFIDENCE (<95%) |

## Invalid-response audit

| model | surface | invalid | scored | rate |
|---|---|---|---|---|
| qwen3.8-max | baseline | 2 | 158 | 0.0127 |
| qwen3.8-max | post | 1 | 158 | 0.0063 |
| deepseek-v4-flash-0731 | baseline | 3 | 158 | 0.0190 |
| deepseek-v4-flash-0731 | post | 3 | 158 | 0.0190 |
| kimi-k3 | baseline | 0 | 158 | 0.0000 |
| kimi-k3 | post | 0 | 158 | 0.0000 |
| glm-5.2 | baseline | 2 | 158 | 0.0127 |
| glm-5.2 | post | 0 | 158 | 0.0000 |

## Cost summary (estimate:true — verify prices before quoting)

| model | surface | api calls | prompt tokens | completion tokens | est. cost USD |
|---|---|---|---|---|---|
| qwen3.8-max | baseline | 158 | 1897362 | 140784 | $6.1512 |
| qwen3.8-max | post | 158 | 1911582 | 143136 | $6.2103 |
| deepseek-v4-flash-0731 | baseline | 158 | 1926313 | 109911 | $0.7098 |
| deepseek-v4-flash-0731 | post | 158 | 1940059 | 108593 | $0.7123 |
| kimi-k3 | baseline | 158 | 1865091 | 43868 | $1.2287 |
| kimi-k3 | post | 158 | 1878679 | 42809 | $1.2342 |
| glm-5.2 | baseline | 158 | 1863113 | 72153 | $2.1517 |
| glm-5.2 | post | 158 | 1877491 | 69633 | $2.1560 |

## Validity correction and final verdict (2026-08-26 addendum)

Two scoring artifacts were identified and corrected offline (trace
re-analysis, no new API calls):

1. **Format compliance confound**: deepseek-v4-flash-0731 (heavily) and
   kimi-k3 (occasionally) answer with bare capability names
   (`get_market_data`) instead of the required prefixed ids
   (`tool:get_market_data`). Strict id scoring reads these as misses even
   though the routing decision is correct. Format-TOLERANT re-scoring
   (bare name -> candidate id when unambiguous) is the valid construct
   measure; strict scoring is reported above as protocol compliance.
2. **Rename-reconciliation artifact**: D02-006's expected target was
   reconciled to the post-rename skill name, so baseline-surface correct
   answers (`skill:sec-edgar`) score as misses — one artificial
   "improvement" per model. Excluded from deltas.

Corrected paired top-1 (tolerant scoring, D02-006 excluded):

| model | baseline | post | Δ | improved | regressed | McNemar p |
|---|---|---|---|---|---|---|
| qwen3.8-max | 0.8917 | 0.9045 | +0.0127 | 8 | 6 | 0.791 |
| deepseek-v4-flash-0731 | 0.8917 | 0.8790 | -0.0127 | 8 | 10 | 0.815 |
| kimi-k3 | 0.9363 | 0.9299 | -0.0064 | 1 | 2 | 1.000 |
| glm-5.2 | 0.9172 | 0.9108 | -0.0064 | 6 | 7 | 1.000 |
| **POOLED** | | | | **23** | **25** | **0.885** |

Target-group inspection (tolerant): A1 sentiment +1 genuine improvement
(qwen3.8-max D13-001 miss->hit); A3 file scope unchanged (baseline
already correct — ceiling); A4 quantlib unchanged — its one apparent
qwen regression (D09-001) is an agent-mode clarification reply, not a
routing decision; A2 SEC unchanged — D02-006 flips are the rename
artifact, D02-005's apparent regression is a tool_calls-format reply
whose underlying choice (get_sec_filings) is correct.

**Final verdict**: the P0 description changes are ROUTING-NEUTRAL under
the four-family LLM judge (pooled p=0.885). Baseline accuracy 0.88-0.94
indicates a ceiling effect: strong models route well even on pre-change
descriptions in the full-surface regime. The lexical baseline (top-1
0.4367) agrees with the judge only ~50% of the time — it is a weak
proxy, and its measured "improvements" (D13/D07 groups) did not survive
semantic arbitration. The null result is the evidence: description
governance alone does not move routing accuracy at this surface size —
the dominant lever per PAPERS §F is the NUMBER of tools presented, i.e.
the B-batch exposure-surface engineering.
