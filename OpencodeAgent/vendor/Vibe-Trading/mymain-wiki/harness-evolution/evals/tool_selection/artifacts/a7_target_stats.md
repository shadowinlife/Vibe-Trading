# A7 target — paired results (tag `a7_target`)

Models: qwen3.8-max, kimi-k3

### Strict top-1

| scope | n | baseline (95% CI) | post (95% CI) | Δ | improved | regressed | McNemar p |
|---|---|---|---|---|---|---|---|
| qwen3.8-max | 60 | 0.8333 [0.7197, 0.9069] | 0.8333 [0.7197, 0.9069] | +0.0000 | 5 | 5 | 1.00000 |
| kimi-k3 | 60 | 0.8333 [0.7197, 0.9069] | 0.8833 [0.7782, 0.9423] | +0.0500 | 5 | 2 | 0.45312 |
| POOLED | 120 | 0.8333 [0.7565, 0.8894] | 0.8583 [0.7848, 0.9096] | +0.0250 | 10 | 7 | 0.62906 |

### Lenient top-1 (format-tolerant)

| scope | n | baseline (95% CI) | post (95% CI) | Δ | improved | regressed | McNemar p |
|---|---|---|---|---|---|---|---|
| qwen3.8-max | 60 | 0.8500 [0.7389, 0.9190] | 0.8333 [0.7197, 0.9069] | -0.0167 | 4 | 5 | 1.00000 |
| kimi-k3 | 60 | 0.8500 [0.7389, 0.9190] | 0.9333 [0.8407, 0.9738] | +0.0833 | 5 | 0 | 0.06250 |
| POOLED | 120 | 0.8500 [0.7753, 0.9030] | 0.8833 [0.8137, 0.9292] | +0.0333 | 9 | 5 | 0.42395 |

### Baseline-failure recovery rate (pooled)

| outcome | baseline failures | recovered | recovery rate |
|---|---|---|---|
| strict | 20 | 10 | 0.5000 |
| lenient | 18 | 9 | 0.5000 |

### Strict top-1 flips

- `qwen3.8-max:A7-05` regressed: `skill:correlation-analysis` -> `tool:get_market_data`
- `qwen3.8-max:A7-10` improved: `skill:correlation-analysis` -> `skill:pair-trading`
- `qwen3.8-max:A7-16` improved: `skill:fundamental-filter` -> `tool:iwencai_search`
- `qwen3.8-max:A7-19` improved: `skill:fundamental-filter` -> `tool:iwencai_search`
- `qwen3.8-max:A7-21` regressed: `tool:web_search` -> `tool:get_macro_series`
- `qwen3.8-max:A7-32` regressed: `tool:trading_connections` -> `tool:get_research_reports`
- `qwen3.8-max:A7-36` regressed: `tool:trading_positions` -> `None`
- `qwen3.8-max:A7-37` regressed: `tool:trading_orders` -> `tool:trading_connections`
- `qwen3.8-max:A7-38` improved: `trading_quote` -> `tool:trading_quote`
- `qwen3.8-max:A7-49` improved: `tool:technical_indicators` -> `skill:technical-basic`
- `kimi-k3:A7-16` improved: `skill:fundamental-filter` -> `tool:iwencai_search`
- `kimi-k3:A7-21` improved: `web_search` -> `tool:web_search`
- `kimi-k3:A7-23` improved: `tool:get_institutional_holdings` -> `tool:web_search`
- `kimi-k3:A7-36` regressed: `tool:trading_positions` -> `trading_positions`
- `kimi-k3:A7-49` improved: `tool:technical_indicators` -> `skill:technical-basic`
- `kimi-k3:A7-53` improved: `tool:get_stock_profile` -> `tool:get_research_reports`
- `kimi-k3:A7-58` regressed: `tool:query_strategies` -> `query_strategies`


