# Teaching an AI agent to stop memorizing the whole tool catalog — a plain-English summary

> Full technical version (all data and criteria): `HARNESS_EVOLUTION_SUMMARY.md`
> Period: 2026-08-21 ~ 2026-08-28

## What was the problem?

Our AI finance assistant (opencode + vibe-trading MCP) exposes 74 tools and 90
skills. The catch: **every planning step, the model re-reads all 164 capability
descriptions** — roughly 52k tokens burned per round. Published evidence says
tool-selection accuracy starts degrading past 25-30 visible tools and collapses
near 100.

Imagine forcing someone to re-read a 164-page index before every decision —
expensive, and increasingly error-prone.

## What did we do first?

No code changes for two days. Instead: a numbered audit of every routing
weakness, a literature + open-source review, and — most importantly — **a fair
exam system** (158 realistic finance questions, two independent AI judges,
statistical tests with pre-registered pass/fail thresholds). That exam system
turned out to be the most valuable deliverable: it caught two "improvements"
that looked real but weren't.

## Four approaches tried — what survived?

### A. Rewrite the tool descriptions ❌ No effect

Intuition says better descriptions should improve routing. We rewrote a dozen,
then ran controlled A/B experiments with four frontier open-source models as
judges. Result: **zero measurable difference** (pooled p=0.885). Strong models
already pick correctly 88-94% of the time — a ceiling effect. Two later
description batches even measured regressions; both were rolled back.

### B. Stop serving tools that can't run ✅ Succeeded

The direct approach: key-gated tools simply don't appear when the key is
absent; broker tools don't appear without a configured connector. The surface
shrank from 74 to 59 tools, **saving 17.9% of per-turn tokens**, with
statistically proven non-inferior routing accuracy and zero hallucinated calls
in live tests. The only approach fully admitted.

### C. Hide tools behind a "search" meta-tool ❌ Failed

Ambitious version: keep 12 tools resident, let the model search for the rest.
The search engine itself passed its exam (93.7% recall, −79% tokens). But
end-to-end it fell apart: **the model often couldn't be bothered to search**,
grabbed whatever visible tool was "close enough," and accuracy dropped 11 to 33
percentage points across all four configurations. Fully rolled back.

### D. Specialist sub-agents ("triage to the right clinic") ⚠️ Conditionally passed

Final approach, borrowed from hospitals: instead of one generalist facing 164
descriptions, create specialist rooms — a quant room holding only 11 tools, a
document room holding only 3. The main agent just triages.

- **Triage accuracy 99.1%**, mis-delegation 3.6% — all thresholds passed;
- Accuracy inside each room is unchanged (no better, no worse — but tokens
  are saved regardless);
- Each decision reads **86% fewer tokens** and runs **about a third faster**,
  with the long-tail stalls visibly shortened;
- Trivial one-shot questions are a few seconds slower (triage itself costs
  time); heavy multi-step tasks (write a strategy + run a backtest) come out
  ahead.

Live testing surfaced two issues invisible to the lab harness: the main agent
**won't triage on its own** — its operating instructions must explicitly say
"send this kind of work to the specialist"; and the tool whitelist only fenced
off our own MCP server, leaving a sibling server's search tool reachable. Fixes
for both are prepared.

## Where things stand

- Approach B is live in production (−17.9% disclosure tax per planning turn);
- Approach D's two pilot agents are stored as production-ready candidate
  configs, waiting for the production branch to stabilize; one residual
  question (occasionally grabbing the "manual" instead of the "tool" for
  similarly-named pairs) must be confirmed fixed before the remaining eleven
  rooms open;
- Approaches A and C are archived with full failure evidence and are
  explicitly marked **do not retry**.

## The biggest win isn't any single approach

It's the discipline: **register the pass/fail bar before running the
experiment, vaccinate the judges with consistency probes, and allow "no
effect" as an honest answer.** That discipline killed three seductive ideas in
one week and kept the two that actually work.
