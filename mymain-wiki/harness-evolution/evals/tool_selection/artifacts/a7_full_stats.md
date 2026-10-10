# A7 full (regression guard) — paired results (tag `a7_full`)

Models: qwen3.8-max, kimi-k3

### Strict top-1

| scope | n | baseline (95% CI) | post (95% CI) | Δ | improved | regressed | McNemar p |
|---|---|---|---|---|---|---|---|
| qwen3.8-max | 158 | 0.9051 [0.8493, 0.9416] | 0.9430 [0.8953, 0.9697] | +0.0380 | 9 | 3 | 0.14600 |
| kimi-k3 | 158 | 0.8987 [0.8418, 0.9367] | 0.9304 [0.8796, 0.9607] | +0.0316 | 7 | 2 | 0.17969 |
| POOLED | 316 | 0.9019 [0.8641, 0.9300] | 0.9367 [0.9043, 0.9587] | +0.0348 | 16 | 5 | 0.02660 |

> Lenient top-1 suppressed: the baseline traces predate the format-tolerant field (reused full-surface baseline). Strict top-1 is the non-inferiority metric for this set.

### Baseline-failure recovery rate (pooled, strict)

| outcome | baseline failures | recovered | recovery rate |
|---|---|---|---|
| strict | 31 | 16 | 0.5161 |

### Strict top-1 flips

- `qwen3.8-max:D01-001` regressed: `tool:get_market_data` -> `get_market_data`
- `qwen3.8-max:D01-003` improved: `None` -> `tool:screen_market`
- `qwen3.8-max:D02-005` improved: `None` -> `tool:get_sec_filings`
- `qwen3.8-max:D02-010` improved: `iwencai_search` -> `skill:fundamental-filter`
- `qwen3.8-max:D06-007` improved: `tool:alpha_zoo` -> `skill:alpha-zoo`
- `qwen3.8-max:D08-003` improved: `None` -> `tool:get_options_chain`
- `qwen3.8-max:D09-001` improved: `None` -> `tool:quantlib_call`
- `qwen3.8-max:D11-001` regressed: `tool:get_macro_series` -> `None`
- `qwen3.8-max:D16-004` improved: `tool:trading_connections` -> `tool:trading_positions`
- `qwen3.8-max:D16-008` regressed: `tool:trading_history` -> `tool:trading_connections`
- `qwen3.8-max:D17-004` improved: `tool:list_swarm_presets` -> `tool:run_swarm`
- `qwen3.8-max:D19-006` improved: `tool:read_document` -> `skill:doc-reader`
- `kimi-k3:D05-003` improved: `tool:technical_indicators` -> `skill:technical-basic`
- `kimi-k3:D06-001` improved: `skill:alpha-zoo` -> `tool:alpha_zoo`
- `kimi-k3:D07-009` improved: `write_file` -> `tool:write_file`
- `kimi-k3:D08-001` regressed: `tool:analyze_options` -> `analyze_options`
- `kimi-k3:D14-002` regressed: `tool:etf_holdings` -> `etf_holdings`
- `kimi-k3:D15-005` improved: `render_shadow_report` -> `tool:render_shadow_report`
- `kimi-k3:D16-002` improved: `tool:trading_connections` -> `tool:trading_select_connection`
- `kimi-k3:D16-004` improved: `tool:trading_connections` -> `tool:trading_positions`
- `kimi-k3:D17-006` improved: `tool:list_runs` -> `tool:retry_run`


