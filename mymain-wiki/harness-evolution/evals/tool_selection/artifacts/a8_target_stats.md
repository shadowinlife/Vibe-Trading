# A8 target — paired results (tag `a8_target`)

Models: qwen3.8-max, kimi-k3

### Strict top-1

| scope | n | baseline (95% CI) | post (95% CI) | Δ | improved | regressed | McNemar p |
|---|---|---|---|---|---|---|---|
| qwen3.8-max | 70 | 0.8857 [0.7904, 0.9409] | 0.9571 [0.8814, 0.9853] | +0.0714 | 6 | 1 | 0.12500 |
| kimi-k3 | 70 | 0.9714 [0.9017, 0.9921] | 0.9571 [0.8814, 0.9853] | -0.0143 | 0 | 1 | 1.00000 |
| POOLED | 140 | 0.9286 [0.8735, 0.9607] | 0.9571 [0.9097, 0.9802] | +0.0286 | 6 | 2 | 0.28906 |

### Lenient top-1 (format-tolerant)

| scope | n | baseline (95% CI) | post (95% CI) | Δ | improved | regressed | McNemar p |
|---|---|---|---|---|---|---|---|
| qwen3.8-max | 70 | 0.9000 [0.8077, 0.9507] | 0.9571 [0.8814, 0.9853] | +0.0571 | 5 | 1 | 0.21875 |
| kimi-k3 | 70 | 0.9714 [0.9017, 0.9921] | 0.9714 [0.9017, 0.9921] | +0.0000 | 0 | 0 | 1.00000 |
| POOLED | 140 | 0.9357 [0.8823, 0.9658] | 0.9643 [0.9191, 0.9847] | +0.0286 | 5 | 1 | 0.21875 |

### Baseline-failure recovery rate (pooled)

| outcome | baseline failures | recovered | recovery rate |
|---|---|---|---|
| strict | 10 | 6 | 0.6000 |
| lenient | 9 | 5 | 0.5556 |

### Strict top-1 flips

- `qwen3.8-max:A8-03` improved: `None` -> `tool:cashflow_performance`
- `qwen3.8-max:A8-12` improved: `tool:search_symbol` -> `tool:get_sector_info`
- `qwen3.8-max:A8-13` regressed: `tool:get_sector_info` -> `None`
- `qwen3.8-max:A8-22` improved: `prediction_market` -> `tool:prediction_market`
- `qwen3.8-max:A8-54` improved: `skill:shadow-account` -> `tool:extract_shadow_strategy`
- `qwen3.8-max:A8-56` improved: `skill:shadow-account` -> `tool:run_shadow_backtest`
- `qwen3.8-max:A8-66` improved: `tool:get_market_data` -> `skill:akshare`
- `kimi-k3:A8-20` regressed: `tool:get_sector_info` -> `get_sector_info`


