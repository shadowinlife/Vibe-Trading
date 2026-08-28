# C-batch (routing layer) — pre-registered verdict report

> ⛔ **FINAL DISPOSITION (2026-08-28): ROLLED BACK.** Per the pre-registered
> decision tree (R1 failed) and the user's decision, C1-C3 were fully reverted:
> `search_tools`, the disclosure tiers, and the routing meta-rules were removed;
> the MCP default surface is back to 59 (post-B state) and the agent surface to
> 90; README/SKILL.md/test anchors were restored; the mechanism code was NOT kept
> (removed outright, including `VIBE_TRADING_TIERED_TOOLS`). This file and the
> judge traces below are retained as the failure record. See ROADMAP §9.

Criteria source: `HARNESS_EVOLUTION_C_PLAN.md` §5 (pre-registered, frozen 2026-08-27 — thresholds fixed before the experiments, never adjusted post-hoc).

Scope: PLAN-C1 (`search_tools` meta-tool), PLAN-C2 (tiered disclosure), PLAN-C3 (routing meta-rules).

## VERDICT

**Deterministic gates (D/S) PASS. LLM-judge end-to-end routing gate (R1) FAILS — the tiered
surface measurably reduces top-1 routing accuracy in the judge simulation. Per the
pre-registered decision tree, C2 must NOT ship as the default MCP surface; the user chose
to roll back C1-C3 entirely (not keep them opt-in). Executed 2026-08-28.**

---

## 1. Implementation landed

| item | what shipped |
|---|---|
| C1 | `search_tools` meta-tool: deterministic CJK-aware retrieval over a curated 203-entry corpus (AUDIT §7.2 triggers + prior knowledge), on both the agent registry (`SearchToolsTool`) and MCP (`@mcp.tool search_tools`). Recall activates tools server-wide (`Visibility(True)` transform) so they become visible + callable. |
| C2 | Tiered disclosure: 12 always-on verbs + `search_tools` = 13 default keyless MCP surface; ~45 on-demand tools hidden by a `Visibility(False)` transform and restored via recall; gated tools (B1/B2) unchanged. Agent surface honors `disclosure="tiered"` behind `VIBE_TRADING_TIERED_TOOLS`. |
| C3 | Five routing meta-rules injected into the agent system prompt (`routing_meta_rules` block, phantom-guarded on `search_tools` presence) + MCP client-guide prose in the READMEs / SKILL.md. |

Full test suite: **12094 passed**, 4 pre-existing `src/providers/` failures (documented in the
B-batch verdict as HEAD-existing, unrelated to routing).

---

## 2. Deterministic verification (D/S criteria) — ALL PASS

Runner: `src/evals/tool_selection/c_batch_deterministic.py`. Primary efficacy set = 143 queries
(15 structural-miss excluded: they target credential-gated tools absent keyless).

| # | criterion | result | threshold | verdict |
|---|---|---|---|---|
| D1 | recall@7 | **0.9371** (134/143) | ≥ 0.90 | PASS |
| D4 | recall@3 (selectivity) | 0.8951 | ≥ 0.80 | PASS |
| D2 | median shortlist size | 5 | ≤ 7 | PASS |
| D3 | negative-outrank rate | 0.0273 (3/110) | ≤ 0.05 | PASS |
| S1 | disclosure-tax reduction | **0.7908** (6974→1459 tiktoken; chars/3 0.8008) | ≥ 0.60 | PASS |
| S4 | default keyless surface | 13 tools | == 13 | PASS |

Interpretation: the **retrieval engine is solid** — when a search is issued, the right capability
is in the top-7 shortlist 93.7% of the time, the shortlist stays in the comfort zone, negatives
are suppressed, and the disclosure tax drops ~79%.

---

## 3. LLM-judge end-to-end routing (R criteria) — R1 FAILS

Runner: `src/evals/tool_selection/c_batch_judge.py`. Two arms over the 143 primary queries:
- **Arm A (baseline):** full 59-tool + 90-skill surface, frozen E2 protocol.
- **Arm B (tiered):** Stage 1 shows the always-visible set (12 always-on tools + skill one-line
  catalog) with the PLAN-C3 routing rules; the judge picks or answers `SEARCH`; on `SEARCH` the
  deterministic `search_tools` shortlist is unioned in and the judge picks again (Stage 2).

Scoring: lenient top-1 (bare-name-forgiven, the documented format-artifact correction) as primary.

Four simulation configurations were run to bound faithfulness (the tiered arm requires simulating
the model's *search decision*, which is sensitive to prompt/visible-set). All four show Arm B
worse; R1 fails in every configuration.

| config | Arm B visible set | pooled Arm A | pooled Arm B | Δ (B−A) | search rate | R1 (CI lower > −5pp) |
|---|---|---|---|---|---|---|
| 1 | 13 tools, minimal prompt | 0.944 | 0.608 | −0.336 | 52–59% | FAIL |
| 2 | 13 tools + routing rules | 0.948 | 0.832 | −0.115 | 79–80% | FAIL |
| 3 | 13 tools + routing rules, search_tools not a candidate | 0.944 | 0.808 | −0.136 | 75–78% | FAIL |
| 4 | 13 tools + skill catalog + routing rules (most design-faithful) | 0.962 | 0.755 | −0.206 | 12–19% | FAIL |

Per-model, most design-faithful configuration (config 4):

| model | n | Arm A | Arm B | Δ | discordant B+A− / A+B− | R6 search rate |
|---|---|---|---|---|---|---|
| kimi-k3 | 143 | 0.9650 | 0.7832 | −0.1818 | 4 / 30 | 18.9% |
| qwen3.8-max | 143 | 0.9580 | 0.7273 | −0.2308 | 5 / 38 | 11.9% |

R5 failure decomposition (config 4): arm-B misses are dominated by **selection errors** (the judge
picked a plausible visible tool/skill instead of the expected on-demand one) plus a smaller
**recall-miss** component (expected tool not in the shortlist). The search decision is the
bottleneck: when skills are visible the judge settles for a visible candidate and under-searches
(config 4 search rate 12–19%); when skills are hidden it over-searches but still mis-selects.

---

## 4. Interpretation

- **C1 is validated.** The retrieval mechanism (deterministic recall 0.937) is not the problem.
- **C2's routing cost is real in the judge simulation and robust in direction** (Arm B worse in
  all four configurations), though the **magnitude is configuration-sensitive** (−0.115 to −0.336).
  The one-shot judge likely *under-searches* relative to a deployed agent that has conversation
  context and multiple turns, so the true deployed cost may sit toward the smaller end — but every
  configuration exceeds the −5pp non-inferiority bound.
- Root cause: tiering moves the routing burden from "pick among all" to "decide to search, then
  pick". The judge's search decision is unreliable — it either settles for a generic/visible tool
  (`web_search`, or a sibling skill) or fails to recall the exact on-demand tool.

## 5. Decision-tree application (C_PLAN §5.4)

Pre-registered rule: "D1-D3+D5 pass AND S1-S4 pass AND R1 pass → C batch passes. R1 fails (CI
lower ≤ −5pp) → reject C2 default-on: revert the MCP surface to full exposure, keep `search_tools`
as an optional discovery tool; attribute via the R5 decomposition."

- D/S gates: **PASS**.
- R1: **FAIL** (all configurations).
- **Therefore C2 tiered disclosure must not be the default MCP surface as-is.**

## 6. Disposition & follow-ups

1. **Keep** C1 (`search_tools`) and C3 (routing meta-rules) — both validated / harmless.
2. **Do not ship C2 as the default keyless MCP surface** until the routing cost is closed.
   Concretely the default surface should be restored to the full registered set; `search_tools`
   remains available as an optional discovery aid. (The tiering machinery — `disclosure.py`,
   the `Visibility` transforms, `VIBE_TRADING_TIERED_TOOLS` — stays in place behind the switch so
   the design can be re-enabled once the search decision is fixed.)
3. **Attribution (R5) → concrete fixes to try before re-enabling C2:**
   - selection errors: make `search_tools` activation *inject the recalled tool's schema into the
     model's next turn* rather than relying on a second routing pass; reduce tool/skill sibling
     confusion (e.g. surface the arbitration hints in the shortlist).
   - search-decision reliability: the agent loop (multi-turn, with the C3 rules in-system) is the
     faithful environment — evaluate C2 there (L2 real-session test) rather than via the one-shot
     judge before re-enabling.
4. Deterministic disclosure-tax win (S1, ~79%) is preserved **only if** C2 is re-enabled; with C2
   default-off the tax benefit is deferred. This is the explicit trade the pre-registration encoded.

**ACTUAL FINAL DISPOSITION (user decision, 2026-08-28):** the user chose the stricter option —
not "keep C1/C3 opt-in" (items 1-2 above) but a **full rollback of C1-C3**. Rationale given: if
the search/recall mechanism lowers end-to-end hit rate, keeping it has no standalone value, and no
planned follow-up work consumes it. All C-batch code was removed outright (including
`VIBE_TRADING_TIERED_TOOLS`); MCP default surface restored to 59, agent surface to 90;
README/SKILL.md/test anchors restored. **Dependency check performed: the D-subagent batch does NOT
depend on C** (D1←B,A6; D2←B; D3←A6; D4←D1,D2 — subagents use fixed §8.1 whitelists, already in
the comfort zone), so D proceeds unaffected. E3 (routing telemetry) loses its C2 premise and is
deferred; B3's "restore via C1 lazy-load" path is dropped, keeping B-batch behavior. See ROADMAP §9.

## 7. Artifacts

- `c_batch_deterministic.json` — D/S criteria results.
- `c_batch_judge_<stamp>.jsonl` — per-query two-arm judge traces (4 runs, one per configuration).
- `HARNESS_EVOLUTION_C_PLAN.md` — plan + pre-registered criteria.
