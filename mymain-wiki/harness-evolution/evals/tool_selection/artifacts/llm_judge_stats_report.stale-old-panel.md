# LLM-judge panel statistics report

Paired design: every query is its own control across the frozen
baseline corpus and the current post corpus. Headline test: exact
McNemar (two-sided binomial on discordant pairs); Wilson 95% CIs per
surface. Deterministic given the golden traces.

- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`

## Panel and pins

| model | role | provider | temperature | max_response_tokens |
|---|---|---|---|---|
| qwen3.8-max | primary | dashscope | 0.0 | 80 |
| deepseek-v4-flash-0731 | sensitivity | dashscope | 0.0 | 500 |
| kimi-k2.6 | sensitivity | dashscope | 0.0 | 80 |
| glm-5.1 | sensitivity | dashscope | 0.0 | 80 |

- budget caps per (model, surface) run: 25000000 tokens / 700 calls
- price table: **estimate:true** — cost figures below are unverified

## Per-model paired results (exact McNemar)

### Top-1

| model | n pairs | baseline (95% CI) | post (95% CI) | Δ | improved | regressed | McNemar p |
|---|---|---|---|---|---|---|---|
| qwen3.8-max | 0 | n/a | n/a | n/a | - | - | n/a |
| deepseek-v4-flash-0731 | 0 | n/a | n/a | n/a | - | - | n/a |
| kimi-k2.6 | 0 | n/a | n/a | n/a | - | - | n/a |
| glm-5.1 | 0 | n/a | n/a | n/a | - | - | n/a |

### Top-3

| model | n pairs | baseline (95% CI) | post (95% CI) | Δ | improved | regressed | McNemar p |
|---|---|---|---|---|---|---|---|
| qwen3.8-max | 0 | n/a | n/a | n/a | - | - | n/a |
| deepseek-v4-flash-0731 | 0 | n/a | n/a | n/a | - | - | n/a |
| kimi-k2.6 | 0 | n/a | n/a | n/a | - | - | n/a |
| glm-5.1 | 0 | n/a | n/a | n/a | - | - | n/a |

### Negative false-recall and invalid responses (plain rates)

| model | neg baseline | neg post | Δ | invalid baseline | invalid post | Δ |
|---|---|---|---|---|---|---|
| qwen3.8-max | n/a (0) | 0.0000 (2) | n/a | n/a (0) | 0.0000 (2) | n/a |
| deepseek-v4-flash-0731 | n/a (0) | n/a (0) | n/a | n/a (0) | n/a (0) | n/a |
| kimi-k2.6 | n/a (0) | n/a (0) | n/a | n/a (0) | n/a (0) | n/a |
| glm-5.1 | n/a (0) | n/a (0) | n/a | n/a (0) | n/a (0) | n/a |

## Pooled across models

Model is a stratification variable here: pooling assumes the
description change acts in the same direction across judges. Check
the per-model table for heterogeneity before trusting pooled p-values.

- per-model heterogeneity (top-1 Δ): 0 improved / 0 regressed / 0 flat (of 0 models with paired data)
- pooled pairs: 0
- pooled top-1: baseline n/a -> post n/a, Δ n/a, improved 0 / regressed 0, McNemar p 1
- pooled top-3: baseline n/a -> post n/a, Δ n/a, improved 0 / regressed 0, McNemar p 1

## Flip lists (top-1 outcome flipped between surfaces)

### qwen3.8-max (0 flips)

(none)

### deepseek-v4-flash-0731 (0 flips)

(none)

### kimi-k2.6 (0 flips)

(none)

### glm-5.1 (0 flips)

(none)

## Lexical-vs-semantic agreement (run_eval proxy blind spots)

Agreement of the lexical top-1 outcome with the LLM-judge top-1
outcome per query; disagreements are where the lexical proxy cannot
see what the judge sees.

| model | surface | agreement | disagreements |
|---|---|---|---|
| qwen3.8-max | baseline | n/a | 0 |
| qwen3.8-max | post | 0.5000 | 1 |
| deepseek-v4-flash-0731 | baseline | n/a | 0 |
| deepseek-v4-flash-0731 | post | n/a | 0 |
| kimi-k2.6 | baseline | n/a | 0 |
| kimi-k2.6 | post | n/a | 0 |
| glm-5.1 | baseline | n/a | 0 |
| glm-5.1 | post | n/a | 0 |

disagreement set — qwen3.8-max / post: D01-001

## Determinism audit (probe first-pick agreement)

| model | surface | queries | repeats | agreement | confidence |
|---|---|---|---|---|---|
| qwen3.8-max | baseline | 8 | 3 | 0.9583 | ok |
| qwen3.8-max | post | 8 | 3 | 0.9583 | ok |
| deepseek-v4-flash-0731 | baseline | 8 | 3 | 0.8333 | REDUCED-CONFIDENCE (<95%) |
| deepseek-v4-flash-0731 | post | 8 | 3 | 0.7917 | REDUCED-CONFIDENCE (<95%) |
| kimi-k2.6 | baseline | 8 | 3 | 1.0000 | ok |
| kimi-k2.6 | post | 8 | 3 | 1.0000 | ok |
| glm-5.1 | baseline | 8 | 3 | 1.0000 | ok |
| glm-5.1 | post | 8 | 3 | 1.0000 | ok |

## Invalid-response audit

| model | surface | invalid | scored | rate |
|---|---|---|---|---|
| qwen3.8-max | baseline | 0 | 0 | n/a |
| qwen3.8-max | post | 0 | 2 | 0.0000 |
| deepseek-v4-flash-0731 | baseline | 0 | 0 | n/a |
| deepseek-v4-flash-0731 | post | 0 | 0 | n/a |
| kimi-k2.6 | baseline | 0 | 0 | n/a |
| kimi-k2.6 | post | 0 | 0 | n/a |
| glm-5.1 | baseline | 0 | 0 | n/a |
| glm-5.1 | post | 0 | 0 | n/a |

## Cost summary (estimate:true — verify prices before quoting)

| model | surface | api calls | prompt tokens | completion tokens | est. cost USD |
|---|---|---|---|---|---|
| qwen3.8-max | baseline | 0 | 0 | 0 | $0.0000 |
| qwen3.8-max | post | 2 | 24198 | 980 | $0.0703 |
| deepseek-v4-flash-0731 | baseline | 0 | 0 | 0 | $0.0000 |
| deepseek-v4-flash-0731 | post | 0 | 0 | 0 | $0.0000 |
| kimi-k2.6 | baseline | 0 | 0 | 0 | $0.0000 |
| kimi-k2.6 | post | 0 | 0 | 0 | $0.0000 |
| glm-5.1 | baseline | 0 | 0 | 0 | $0.0000 |
| glm-5.1 | post | 0 | 0 | 0 | $0.0000 |
