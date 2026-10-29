# A8 full (regression guard) — paired results (tag `a8_full`)

Models: qwen3.8-max, kimi-k3

### Strict top-1

| scope | n | baseline (95% CI) | post (95% CI) | Δ | improved | regressed | McNemar p |
|---|---|---|---|---|---|---|---|
| qwen3.8-max | 158 | 0.9430 [0.8953, 0.9697] | 0.8987 [0.8418, 0.9367] | -0.0443 | 1 | 8 | 0.03906 |
| kimi-k3 | 158 | 0.9304 [0.8796, 0.9607] | 0.9241 [0.8719, 0.9560] | -0.0063 | 5 | 6 | 1.00000 |
| POOLED | 316 | 0.9367 [0.9043, 0.9587] | 0.9114 [0.8749, 0.9380] | -0.0253 | 6 | 14 | 0.11532 |

### Lenient top-1 (format-tolerant)

| scope | n | baseline (95% CI) | post (95% CI) | Δ | improved | regressed | McNemar p |
|---|---|---|---|---|---|---|---|
| qwen3.8-max | 158 | 0.9494 [0.9033, 0.9741] | 0.9051 [0.8493, 0.9416] | -0.0443 | 0 | 7 | 0.01562 |
| kimi-k3 | 158 | 0.9620 [0.9196, 0.9825] | 0.9494 [0.9033, 0.9741] | -0.0127 | 1 | 3 | 0.62500 |
| POOLED | 316 | 0.9557 [0.9270, 0.9734] | 0.9272 [0.8932, 0.9510] | -0.0285 | 1 | 10 | 0.01172 |

### Baseline-failure recovery rate (pooled)

| outcome | baseline failures | recovered | recovery rate |
|---|---|---|---|
| strict | 20 | 6 | 0.3000 |
| lenient | 14 | 1 | 0.0714 |

### Strict top-1 flips

- `qwen3.8-max:D01-001` improved: `get_market_data` -> `tool:get_market_data`
- `qwen3.8-max:D06-001` regressed: `tool:alpha_zoo` -> `skill:alpha-zoo`
- `qwen3.8-max:D06-006` regressed: `tool:get_strategy_evidence` -> `None`
- `qwen3.8-max:D06-007` regressed: `skill:alpha-zoo` -> `tool:alpha_zoo`
- `qwen3.8-max:D12-001` regressed: `tool:prediction_market` -> `prediction_market`
- `qwen3.8-max:D14-004` regressed: `skill:etf-analysis` -> `skill:etf_analysis`
- `qwen3.8-max:D16-004` regressed: `tool:trading_positions` -> `tool:trading_connections`
- `qwen3.8-max:D17-004` regressed: `tool:run_swarm` -> `tool:list_swarm_presets`
- `qwen3.8-max:D19-006` regressed: `skill:doc-reader` -> `tool:read_document`
- `kimi-k3:D02-010` improved: `tool:iwencai_search` -> `skill:fundamental-filter`
- `kimi-k3:D03-005` regressed: `tool:get_stock_news` -> `tool:web_search`
- `kimi-k3:D04-003` regressed: `tool:get_margin_trading` -> `get_margin_trading`
- `kimi-k3:D08-001` improved: `analyze_options` -> `tool:analyze_options`
- `kimi-k3:D12-001` regressed: `tool:prediction_market` -> `prediction_market`
- `kimi-k3:D14-002` improved: `etf_holdings` -> `tool:etf_holdings`
- `kimi-k3:D15-004` regressed: `tool:run_shadow_backtest` -> `run_shadow_backtest`
- `kimi-k3:D16-001` improved: `trading_connections` -> `tool:trading_connections`
- `kimi-k3:D16-002` regressed: `tool:trading_select_connection` -> `tool:trading_connections`
- `kimi-k3:D16-004` regressed: `tool:trading_positions` -> `tool:trading_connections`
- `kimi-k3:D16-005` improved: `trading_account` -> `tool:trading_account`


