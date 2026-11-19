# B-batch L3 — pre-registered verdict report

Criteria source: `../../../HARNESS_EVOLUTION_B_TEST_PLAN.md` §5.5 (pre-registered, frozen 2026-08-27 — thresholds were fixed before the experiment and are never adjusted post-hoc).

- traces tag: `b_batch`
- models: qwen3.8-max, kimi-k3
- primary caliber: strict top-1 (lenient is sensitivity-only, C4)
- non-inferiority margin δ: 0.05
- noise band X: 0.125
- CI construction: exact Clopper-Pearson binomial CI on the discordant-direction proportion, transformed to the delta scale

## VERDICT

**within noise band — uninterpretable, recorded as no effect**

## C1 — pooled strict non-inferiority (PRIMARY GATE)

| scope | n pairs | baseline | post | Δ | improved | regressed | exact 95% CI on Δ | McNemar p (report only) | NI verdict |
|---|---|---|---|---|---|---|---|---|---|
| POOLED strict | 286 | 0.9301 | 0.9406 | +0.0105 | 10 | 7 | [-0.0203, +0.0375] | 0.6291 | PASS |

Threshold: exact 95% CI lower bound > −0.05. Result: **PASS**

## C2 — noise-band interpretation rule

Pooled |Δ| = +0.0105 vs band 0.125 → **within noise band — uninterpretable, recorded as no effect** (neither improvement nor regression may be claimed).

## C3 — per-model strict non-inferiority (report only, no gate authority)

| scope | n pairs | baseline | post | Δ | improved | regressed | exact 95% CI on Δ | McNemar p (report only) | NI verdict |
|---|---|---|---|---|---|---|---|---|---|
| qwen3.8-max | 143 | 0.9231 | 0.9091 | -0.0140 | 5 | 7 | [-0.0585, +0.0375] | 0.7744 | FAIL |
| kimi-k3 | 143 | 0.9371 | 0.9720 | +0.0350 | 5 | 0 | [-0.0015, +0.0350] | 0.0625 | PASS |

## C4 — lenient sensitivity (SENSITIVITY ONLY — can never flip C1)

| scope | n pairs | baseline | post | Δ | improved | regressed | exact 95% CI on Δ | McNemar p (report only) |
|---|---|---|---|---|---|---|---|---|
| POOLED lenient | 286 | 0.9371 | 0.9406 | +0.0035 | 8 | 7 | [-0.0246, +0.0301] | 1 |

Structural guarantee: the verdict functions receive strict rows only; this section is computed separately and cannot change C1.

## C5 — absent-behavior probe (descriptive — no accuracy claim)

Excluded from the primary efficacy set: 30 pooled rows (15 absent ids requested).

- removed-capability pick events (must be 0): **0** → PASS

What the model picked instead (distribution only):

| post first pick | count |
|---|---|
| `tool:list_skills` | 8 |
| `skill:qveris` | 7 |
| `tool:get_market_data` | 4 |
| `skill:fundamental-filter` | 2 |
| `tool:web_search` | 2 |
| `tool:read_url` | 2 |
| `tool:analyze_trade_journal` | 2 |
| `tool:read_file` | 1 |
| `tool:load_skill` | 1 |
| `skill:data-routing` | 1 |

| query_id | expected (absent) | post first pick |
|---|---|---|
| qwen3.8-max:D01-007 | `tool:iwencai_search` | `skill:fundamental-filter` |
| qwen3.8-max:D11-001 | `tool:get_macro_series` | `tool:web_search` |
| qwen3.8-max:D11-002 | `tool:get_macro_series` | `tool:read_url` |
| qwen3.8-max:D16-001 | `tool:trading_connections` | `tool:list_skills` |
| qwen3.8-max:D16-002 | `tool:trading_select_connection` | `tool:list_skills` |
| qwen3.8-max:D16-003 | `tool:trading_check` | `tool:list_skills` |
| qwen3.8-max:D16-004 | `tool:trading_positions` | `tool:read_file` |
| qwen3.8-max:D16-005 | `tool:trading_account` | `tool:list_skills` |
| qwen3.8-max:D16-006 | `tool:trading_orders` | `tool:list_skills` |
| qwen3.8-max:D16-007 | `tool:trading_quote` | `tool:get_market_data` |
| qwen3.8-max:D16-008 | `tool:trading_history` | `tool:get_market_data` |
| qwen3.8-max:D18-001 | `tool:qveris_search` | `skill:qveris` |
| qwen3.8-max:D18-002 | `tool:qveris_inspect` | `tool:load_skill` |
| qwen3.8-max:D18-003 | `tool:qveris_execute` | `skill:qveris` |
| qwen3.8-max:D18-005 | `tool:qveris_execute` | `skill:qveris` |
| kimi-k3:D01-007 | `tool:iwencai_search` | `skill:fundamental-filter` |
| kimi-k3:D11-001 | `tool:get_macro_series` | `tool:web_search` |
| kimi-k3:D11-002 | `tool:get_macro_series` | `tool:read_url` |
| kimi-k3:D16-001 | `tool:trading_connections` | `tool:list_skills` |
| kimi-k3:D16-002 | `tool:trading_select_connection` | `skill:data-routing` |
| kimi-k3:D16-003 | `tool:trading_check` | `tool:list_skills` |
| kimi-k3:D16-004 | `tool:trading_positions` | `tool:analyze_trade_journal` |
| kimi-k3:D16-005 | `tool:trading_account` | `tool:analyze_trade_journal` |
| kimi-k3:D16-006 | `tool:trading_orders` | `tool:list_skills` |
| kimi-k3:D16-007 | `tool:trading_quote` | `tool:get_market_data` |
| kimi-k3:D16-008 | `tool:trading_history` | `tool:get_market_data` |
| kimi-k3:D18-001 | `tool:qveris_search` | `skill:qveris` |
| kimi-k3:D18-002 | `tool:qveris_inspect` | `skill:qveris` |
| kimi-k3:D18-003 | `tool:qveris_execute` | `skill:qveris` |
| kimi-k3:D18-005 | `tool:qveris_execute` | `skill:qveris` |

## Primary efficacy set provenance

- pooled paired observations: 286
- qwen3.8-max: 143 pairs
- kimi-k3: 143 pairs

McNemar exact p values above are reported for context only; the verdict is driven solely by the C1 exact-CI lower bound (and the C2 noise-band interpretation rule).

## C6 — disclosure-tax reduction (deterministic, non-LLM)

Measured 2026-08-27 on the committed surfaces (worktree checkout of the pre-B
commit 325732df as baseline; keyless/connectorless environment; token
approximation = wire-format chars / 3):

| surface | pre-B | post-B | reduction |
|---|---|---|---|
| MCP `tools/list` wire format (name + description + inputSchema) | 74 tools, ~28,569 tok | 59 tools, ~23,469 tok | **−5,100 tok/round (−17.9%)** |
| agent registry (descriptions only; schemas excluded → lower bound) | 107 tools, ~11,155 tok | 90 tools, ~10,343 tok | −812 tok/round (−7.3%) |

Pre-registered threshold: MCP-surface reduction ≥ 8,000 tok/round (76% of the
AUDIT §5 rough estimate of ~10.5k). Measured 5,100 < 8,000 → **C6 NOT MET as
pre-registered**. Provenance of the miss: the AUDIT estimate assumed ~700
disclosed tokens per gated tool; the wire-format measurement shows ~340 per
removed tool. The threshold was anchored to an estimate the measurement has
now falsified; per pre-registration discipline the threshold stands as written
and any recalibration requires explicit user adjudication (precedent: A7/A8).

## Decision-tree outcome (../../../HARNESS_EVOLUTION_B_TEST_PLAN.md §5.5)

- C1 PASS (pooled strict non-inferiority, exact CI lower bound −0.0203 > −0.05)
- C2 applies (pooled |Δ| = 0.0105 ≤ noise band 0.125 → delta recorded as no effect)
- C3 report-only: qwen per-model FAIL (CI [−0.0585, +0.0375]), kimi PASS (+0.0350) — no gate authority
- C4 lenient sensitivity: Δ +0.0035, cannot flip C1
- C5 PASS: zero removed-capability pick events; fallback distribution is the
  intended arbitration (trading_quote/trading_history queries fall back to
  get_market_data — exactly K21's rule, now enforced structurally)
- C6 NOT MET (5.1k < 8k threshold)

**Literal pre-registered outcome: "C6 未达 → 收益不成立，即使 C1 过亦不上游".**

Routing side: the exposure-surface cut is **proven non-inferior** (no loss
larger than 2.0pp at 95% confidence on 286 paired observations) with the
point delta inside the judge test-retest noise band (honest null — neither
improvement nor regression may be claimed). The deterministic benefit is real
but smaller than the pre-registered benefit bar: −5,100 tok/round on the MCP
surface (−17.9%) plus the agent-surface reduction. Whether that benefit is
worth upstreaming is a user decision between (a) holding the literal
pre-registration (local-only) and (b) an explicitly-adjudicated recalibration
of C6 to the measured reality (the 8k bar was anchored to a falsified
estimate).

## User adjudication (2026-08-27): judged on the measured data — BENEFIT HOLDS

The user directed the verdict to be decided on the measured data rather than
the falsified-estimate-anchored bar (option (b) above). This is an explicit
user adjudication per pre-registration discipline (A7/A8 precedent) — the
8k threshold is NOT silently moved; the literal pre-registered outcome above
stands as written and is overridden here by recorded adjudication.

Measured-data judgment:

1. **Deterministic benefit, measured not estimated.** MCP surface −5,100
   tok/round (−17.9%; 74→59 tools, −20.3%); agent registry 107→90 tools
   (−15.9%) with −812 description-tokens as a schemas-excluded lower bound.
   The keyless/connectorless state is the FRESH-INSTALL default, so every
   new deployment pays zero disclosure tax on the 15 removed tools in every
   planning round.
2. **Cost side measured at zero.** Routing proven non-inferior (95% CI lower
   bound −2.03pp against a −5pp margin); point delta inside the judge
   test-retest noise band (honest null); zero hallucinated calls of removed
   tools (C5).
3. **Structural quality benefit, measured in behavior.** K21 arbitration is
   enforced by surface structure (absent trading_quote/trading_history
   queries fall back to get_market_data); the keyless call-time-failure mode
   (tool disclosed → called → error envelope → wasted round) is structurally
   eliminated because tools that can only fail are no longer disclosed.
4. **Calibration lesson recorded.** The AUDIT mechanism (gating → disclosure
   tax removed) is confirmed by measurement; only its per-tool constant was
   wrong (~340 measured vs ~700 assumed). Future benefit bars must anchor to
   measured per-tool constants, not audit estimates.

**Adjudicated outcome: 收益成立 — C1 PASS + C6 recalibrated to measured
reality → B 批放行，转为上游 PR 候选**（与 E1/E2 评测基建旗舰 PR 并列，
PR 拆分遵循 ../../../HARNESS_EVOLUTION_P0_PLAN.md §8.3 约定）。

## Final disposition (2026-08-27): upstream contribution deferred

Following the benefit adjudication above, the user made the final disposition
decision on 2026-08-27: **暂时不对上游贡献 (no upstream contribution for
now)**. The B batch stays local together with the rest of the branch
(including the E1/E2 eval-infrastructure flagship PR); the "转为上游 PR 候选"
status recorded above is deferred. The benefit finding itself (C1 PASS + C6
recalibration → 收益成立) stands unchanged — this is a disposition decision,
not an overturning of the adjudication. Re-consideration timing is a future
user decision.

A third-party adversarial review preceded the disposition (2026-08-27,
isolated process + differentiated model glm-5.2, 5 parallel lanes):
**all lanes PASS, zero blocking issues**. The single real finding —
`agent/tests/test_trading_availability.py` (14 probe tests) missed from
commit 41d805a7 — was verified green and committed as 5d22be39. See
../../../HARNESS_EVOLUTION_ROADMAP.md §8.4, ../../../HARNESS_EVOLUTION_P0_PLAN.md §8.2/§8.3,
and the local review brief B_BATCH_REVIEW_BRIEF.md §10 (git-excluded).
