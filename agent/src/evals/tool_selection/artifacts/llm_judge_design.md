# E2 LLM-judge evaluation — protocol design

Semantic arbiter for the tool-selection suite: where the lexical baseline
(`run_eval.py`) measures whether description *wording* separates expected
target from competitors, the LLM judge measures whether a real model, given
the **full routing surface** (74 MCP tools + 90 bundled skills, each as
`<kind>:<name> — <description>`) plus one user query, actually picks the
right capability. It is the arbiter for description changes the lexical
baseline cannot measure — the AUDIT Q2 rename (`sec-edgar` →
`sec-edgar-fetch`) and Q4 keyword front-loading in particular.

Runner: `python -m src.evals.tool_selection.run_llm_judge` (see `--help`).
Pins live in `judge_config.yaml`; this document records the protocol
decisions.

## Frozen prompt template

**Template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`**

The hash covers the UTF-8 bytes of
`SYSTEM_PROMPT + "\n" + USER_TEMPLATE + "\n" + CANDIDATE_LINE` with
placeholders intact (it pins the template, not one rendered prompt). It is
recomputed at runtime and pinned in three places that must agree: the trace
header, this document, and `tests/test_llm_judge.py`. A trace recorded
under a different template hash is refused (exit 2) — mixing templates in
one trace would silently corrupt resume.

```
SYSTEM:
You are a strict tool router for a finance research agent. Given a user
request and the available capabilities (tools and skills, each with id and
description), select the capabilities that best serve the request. Answer
with ONLY a JSON object: {"first": "<id>", "second": "<id>", "third": "<id>"}
where <id> is a candidate id exactly as listed, in order of suitability. No
explanation, no markdown fences.

USER:
## Candidates
<kind>:<name> — <description>        (one line per candidate)

## User request
<query text>
```

Candidate order is corpus order: tools first in registration order, then
skills in loader order. Candidate ids are `tool:<name>` / `skill:<name>`.
Description whitespace is collapsed to a single line so one line is exactly
one candidate (pinned under `candidate_format` in `judge_config.yaml`).

## Scoring definitions

Against `queries.yaml` `expected: {kind, name}` → `expected_id =
"<kind>:<name>"`:

- **top1_hit** — `first == expected_id`.
- **top3_hit** — `expected_id ∈ {first, second, third}`.
- **neg_false_recall** (conservative) — a listed negative id appears in the
  model's top-3 **while the expected id does not**. Negative names are
  resolved to ids via the scored corpus's own name→kind mapping (a name
  exposed under both kinds yields both ids). The conservatism is deliberate:
  a negative that merely appears alongside a successful expected pick is not
  a routing failure, and counting it would inflate the metric.
- **invalid response** — no JSON object can be extracted from the reply.
  Invalid responses count as misses (top1/top3 false) and are tallied
  separately in the report; one bad response never crashes the run.
  Parseable JSON with missing keys is *valid* with `None` slots, not
  invalid.

## Determinism stance

- Generation pins: `temperature: 0.0`, `max_response_tokens: 80` (one
  documented per-model exception — see the panel change log), fixed
  template, fixed candidate order. The judge is treated as deterministic
  *in expectation*, not by guarantee: provider-side batching/sampling
  nondeterminism can persist even at temperature 0.
- Therefore the **determinism probe** (`--probe-only`): the first
  `sample_queries: 8` query ids, each repeated `repeats: 3` times, with the
  agreement rate of `first` reported per query and overall. Probe records
  live in their own JSONL (`llm_judge_probe_<model>_<surface>.jsonl`), never
  in the main trace.
- Any reported accuracy delta between surfaces that is smaller than the
  probe's disagreement band must be read as noise, not as an effect.

## Golden trace, resume, budget

- Trace: append-only JSONL `llm_judge_trace_<model>_<surface>.jsonl`.
  First line is a header `{header, prompt_template_sha256, model, surface,
  corpus_captured_at, config_pins}`; one line per scored query with the raw
  response, parsed picks, scores, latency, provider-reported token counts,
  and UTC timestamp. Per-line `prompt_sha256` hashes the actually-sent
  `system + "\n" + user` text.
- **Resume**: on startup, completed `query_id`s in the trace are skipped,
  and spent tokens/calls are re-derived from the trace — a restart cannot
  double-spend budget.
- **Budget** (per model+surface run): `max_input_tokens_per_model_run:
  25,000,000` and `max_calls_per_model_run: 700`, checked **before** each
  call so a run can never overshoot; violation aborts cleanly with exit
  code 3 naming the cap. Next-call token estimate: the last
  provider-reported prompt count (the prompt is near-constant per surface),
  falling back to a chars/3 heuristic, plus `max_response_tokens`.
- **Retry policy**: at most ONE retry, only on transient network errors
  (connection/timeout/5xx/rate-limit), recorded in the trace via
  `api_calls` and `retried` (budget call accounting sums `api_calls`).
  Non-transient failures (auth, request shape) abort the run instead of
  burning every remaining query as a miss.
- **Model availability**: a model whose `api_key_env` is absent from
  `agent/.env` is reported `skipped (no <env> key)`; the run continues.

## Cost honesty

The price table in `judge_config.yaml` is marked `estimate: true` with
placeholder values; report cost figures carry that warning verbatim and must
not be quoted externally until the prices are verified. Token counts are
provider-reported (`usage.prompt_tokens` / `usage.completion_tokens`), never
estimated after the fact.

## Surfaces

- `--surface baseline` — frozen pre-change corpus
  (`corpus_baseline_snapshot.yaml`, exported from git history).
- `--surface post` (default) — current corpus (`corpus_snapshot.yaml`).

Both surfaces are scored with identical template, pins and query set, so
the paired delta is attributable to the description changes alone.

## A5-A8 targeted extension (2026-08-26)

The A5-A8 quantitative test plan (`HARNESS_EVOLUTION_A5_A8_TEST_PLAN.md`)
reused this protocol with four documented extensions. The judge panel narrowed
to `judge_config_a5a8.yaml` — qwen3.8-max (primary, cap 500) + kimi-k3
(sensitivity, cap 1000), both DashScope under one key, temperature 0.0. This
is a deliberate subset of the E2 4-family panel (user constraint: the target
environment is always head-tier SOTA open-source models); `judge_config.yaml`
stays untouched for E2 reproducibility.

### Format-tolerant (lenient) scoring

`score_response_lenient` layers over the strict contract: it forgives ONLY a
missing `kind:` prefix — a bare-name pick equal to the expected bare name —
and never a wrong-kind pick (`tool:x` vs expected `skill:x` is NOT forgiven).
Introduced because E2 found kimi-k3 (and severely deepseek-v4-flash-0731)
emitting bare capability names — a format artifact, not a routing mistake.
Strict stays the primary caliber; lenient is the parallel caliber that stops
format-only flips polluting the paired comparison. The frozen prompt template
is unchanged (template sha256 unchanged), so pre-existing traces stay valid —
but a reused baseline recorded before this field existed has no lenient
column, and its lenient view is suppressed rather than misread as all-miss.

### Tagged traces, subset filtering, corpus capture

`run_llm_judge` gained `--queries-file` / `--refs` (subset filtering),
`--tag` (namespaced traces `llm_judge_trace_<model>_<surface>_<tag>.jsonl`),
and explicit `--corpus` / `--baseline-corpus` / `--post-corpus` overrides.
`capture_corpus.py` snapshots the full surface with a `captured_at` stamp.
Target corpora: `queries_A7_target.yaml` (60), `queries_A8_target.yaml` (70);
the full regression guard stays `queries.yaml` (158). Paired statistics +
recovery rate + the §6.1 pre-registered verdict live in `a7a8_stats.py`.

### Baseline reuse (sequential isolation)

Order isolation: A7 baseline = pre-A7, A8 baseline = post-A7. The full-158
baselines reuse prior-stage post traces instead of re-running: **A7 full
baseline = E2 post traces** (byte-identical copy; strict-only, no lenient
field, so the A7-full lenient view is suppressed), **A8 full baseline = A7
full post traces**. This is valid because the corpus surface is byte-identical
across each reuse and the template hash is unchanged; both reuses are
disclosed in `a6_a8_verdict.md` and the per-set stats reports.

### A6-A8 verdicts — DO NOT RE-TEST

Final verdicts in `a6_a8_verdict.md`; golden traces preserved in this
directory. Under the SOTA-open-source target environment, description-wording
changes do NOT improve routing:

- **T-A7** — weak/localized effect. Only 2/4 pre-registered criteria passed
  (recovery 50% ✅, full-set non-inferior ✅; target McNemar p=0.629 ❌,
  target Δ+2.5pp ❌). The full-set +3.48pp p=0.027 is a safety-guard metric,
  not an efficacy metric, and partly includes bare-name→prefix format flips.
  Struck as **no improvement** (changes reverted).
- **T-A8** — REJECTED. Full-set significant regression (lenient pooled
  p=0.012 / qwen strict p=0.039), regressions spilling into unrelated domains
  (D06/D16/D17/D19). Struck as **no improvement** (changes reverted).
- **T-A6** — complete. Non-routing verification: the internal↔MCP mapping
  table exists and skill-doc internal-name references were already unified
  (refined metric counts 0 unannotated backticked tool refs).

These conclusions are **final for the A batch**. Combined with E2 (A1-A4
routing-neutral, pooled McNemar p=0.885), the evidence is that polishing
description wording does not move routing accuracy at the SOTA ceiling. **Do
not re-run A1-A8 description tests.** The real routing lever is reducing the
number of tools presented per decision (B/C/D batches). The next legitimate
re-test is an E2-style full-surface comparison AFTER the B batch changes the
exposure surface — and it must first fix the methodology gaps logged in the
2026-08-27 review (power-aligned thresholds, a margin-based non-inferiority
criterion, a named primary caliber, and a judge test-retest noise floor).

## Panel change log

- 2026-08-26 (user-directed expanded protocol): panel is qwen3.8-max
  (primary) + deepseek-v4-flash-0731, kimi-k2.6, glm-5.1 (sensitivity), all
  DashScope-hosted under the same key, temperature 0.0,
  max_response_tokens 80. Smoke-verified UNAVAILABLE on this account:
  glm-5.3 and bare deepseek-v4 — do not use.
- 2026-08-26 (documented deviation, empirical): deepseek-v4-flash-0731
  consumes its max_tokens budget with reasoning BEFORE emitting the visible
  answer — at the pinned 80 it returned empty content on 24/24 probe calls
  (completion_tokens=80, response_raw=''). Its max_response_tokens is raised
  to 500 so a visible answer can exist; qwen3.8-max, kimi-k2.6 and glm-5.1
  stay at 80 (all three answer fully within 80 visible tokens; glm-5.1's
  reasoning tokens are billed separately and do not consume the cap on this
  endpoint). The frozen prompt template is unchanged (template sha256
  unchanged). Evidence for the 80-token failure is preserved as
  `llm_judge_probe_deepseek-v4-flash-0731_<surface>.pin80-evidence.jsonl`.
- 2026-08-26 (cap escalation, empirical): the user-directed panel switch
  landed as schema_version 3 after a cap pathology sequence: (1) original
  probes at cap 80 showed deepseek-v4-flash-0731 returning EMPTY content on
  24/24 calls (reasoning consumes the budget before the visible answer;
  evidence archived as *.pin80-evidence.jsonl); (2) a cap-500 rerun still
  produced 18/24 empty responses; (3) final caps: flash 2000, glm-5.2 2000
  (smoke-verified truncation <=300), kimi-k3 1000, qwen3.8-max 500.
  Old-panel (kimi-k2.6/glm-5.1) probes and the cap-80 qwen matrix prefix
  were deleted; the stale stats report is preserved as
  llm_judge_stats_report.stale-old-panel.md. Final probes + matrix rerun
  under schema_version 3.
