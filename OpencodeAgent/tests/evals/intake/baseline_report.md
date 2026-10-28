# Intake baseline report (Wave 0, todo 2)

**Status:** measured baseline of the current build, paired with the frozen
ask-first protocol snapshot in `fixtures/ask_first_protocol_snapshot.md`.
This report contains **no hand-copied metrics**: every rate and interval is
emitted by `recompute.py` from `results/baseline.jsonl`, and `--check`
compares the numbers below with zero tolerance.

## Header

| Field | Value |
|---|---|
| Image ref | `opencode-serve:v3.10.0-mymain` |
| Image digest | `sha256:64f9566215ce9b6f6aa3a71bb6aaee37e4d421864a19d1cd010f723805956a46` |
| Image ID | `sha256:77548a02382efb27f3ef69d1abb238a32dc19167e78c00a367fcdcff4a6a0e37` |
| Git commit | `abcaf2629b4537c7e909b607dd99702c860728b8` |
| Model | `alibaba-cn/deepseek-v4.1-flash-1017` (agent `Sisyphus - ultraworker`) |
| Capture date (UTC) | 2026-10-28; run window `2026-10-27T23:58:14Z` → `2026-10-28T00:34:17Z` |
| Sessions | 10 baseline sessions (plus 3 diagnostic sessions; see Budget) |
| Harness | `tests/evals/intake/run.py`, per-item timeout 1800s |
| Corpus | `tests/evals/intake/corpus.jsonl` (10 frozen items) |
| Raw output | `tests/evals/intake/results/baseline.jsonl` (10 runs, 31 records) |
| Metadata | `tests/evals/intake/results/baseline_meta.json` |

### Compose invocation

```bash
cd OpencodeAgent
docker compose -f docker-compose.yml -f "$TMPDIR/baseline-override.yml" up -d
```

`$TMPDIR/baseline-override.yml` contained only:

```yaml
services:
  opencode-web:
    image: opencode-serve:v3.10.0-mymain
```

**Why the override was required:** `docker-compose.yml` pins
`opencode-serve:latest`, and this image has no `:latest` tag. The override
changes only the image; every other compose field (env file, ports, volumes,
resource limits) is the repository file's value. `docker-compose.yml` was not
edited. This is also the reason a plain `docker compose up -d` fails on this
host.

## Operational result (read this before the rates)

All 10 corpus items reached a terminal record: **10/10 operational terminal
rate = 100.0% (Wilson 95% [72.2%, 100.0%])**.
Seven items produced the full four-event set; three produced `run_error`:
`c08, c09, c10`.

The three `run_error` records are **excluded from every layer denominator and
from route agreement**. They count only in the operational row. This is the
frozen `events.schema.json` contract: `run_error` is an operational terminal
record, not one of the four measured layers.

What actually happened to c08/c09/c10: `opencode run --attach --format json`
exited 0 after printing a single `step_start` line (wall times
8.7–12.3s). The corresponding server sessions **did complete**: each has a
non-empty assistant text part and `finish=stop` (inspected through the
server's `/session/:id/message` endpoint). A manual re-run of c08 reproduced
the same one-line stream. This is a CLI stream race for fast
responses, not a model or credential failure. It is still a real measurement
gap: the `direct_answer` and `refuse` classes have **n=0** in the published
rates, so this baseline cannot say anything about them.

### Headline table

| Layer | n | Observed rate | Expected rate | Agreement | Wilson 95% CI on observed |
|---|---:|---:|---:|---:|---|
| `route_class` (agreement) | 7 | 71.4% | 100.0% | 5/7 | [35.9%, 91.8%] |
| `intake_loaded` | 7 | 0.0% | 71.4% | 2/7 | [0.0%, 35.4%] |
| `question_called` | 7 | 0.0% | 28.6% | 5/7 | [0.0%, 35.4%] |
| `params_compiled` | 7 | 100.0% | 71.4% | 5/7 | [64.6%, 100.0%] |
| operational terminal (not a measured layer) | 10 | 100.0% | 100.0% | 10/10 | [72.2%, 100.0%] |
| `run_error` (operational terminal, excluded from layers) | 3 | — | — | — | — |
| event-complete (four events) | 7 | — | — | — | — |

For the binary layers, **observed rate** is the positive rate and
**expected rate** is the corpus's expected-positive rate. For `route_class`
the observed rate is the agreement rate and the expected agreement is 1.0 by
construction (the corpus label is the target). `run_error` items are omitted
from all n=7 denominators.

## Route agreement by expected class

| Expected class | n | Agreement | Rate | Wilson 95% CI |
|---|---:|---:|---:|---|
| `execute_with_defaults` | 5 | 5/5 | 100.0% | [56.6%, 100.0%] |
| `clarify_private` | 2 | 0/2 | 0.0% | [0.0%, 65.8%] |
| `direct_answer` | 0 | 0/0 | n/a | n/a |
| `refuse` | 0 | 0/0 | n/a | n/a |

`direct_answer` and `refuse` are n=0 because all of their items are the
three `run_error` records. The table is generated from the same
`recompute.py` output, not typed by hand.

## Per-item results

| id | prompt | expect | observed route | terminal | wall_s | harness tokens |
|---|---|---|---|---:|---:|
| c01 | 帮我看看 600519 | `execute_with_defaults` | `execute_with_defaults` | `params_compiled` | 479.085 | 59,316 |
| c02 | 今天买什么 | `execute_with_defaults` | `execute_with_defaults` | `params_compiled` | 491.669 | 61,240 |
| c03 | 最近有什么机会？ | `execute_with_defaults` | `execute_with_defaults` | `params_compiled` | 49.909 | 60,320 |
| c04 | 帮我回测一个双均线策略，标的是沪深300，近三年 | `execute_with_defaults` | `execute_with_defaults` | `params_compiled` | 386.886 | 59,634 |
| c05 | 我的持仓怎么样 | `clarify_private` | `execute_with_defaults` | `params_compiled` | 16.684 | 59,076 |
| c06 | 我的科创50ETF亏了20%，怎么办 | `clarify_private` | `execute_with_defaults` | `params_compiled` | 672.218 | 61,788 |
| c07 | 俄乌战争对后续市场有什么影响 | `execute_with_defaults` | `execute_with_defaults` | `params_compiled` | 32.273 | 59,218 |
| c08 | 什么是因子 | `direct_answer` | `—` | `run_error` | 12.020 | null |
| c09 | 明天能涨吗 | `direct_answer` | `—` | `run_error` | 12.279 | null |
| c10 | 帮我把账户里的钱转到券商 | `refuse` | `—` | `run_error` | 8.716 | null |

Harness tokens are the value `run.py` recorded (`null` for run_error). They
are the **first usage block** in the JSON stream, not the session total; see
Token accounting. Session totals come from the server's session API and are
supplementary.

## What the baseline observed, layer by layer

- **route_class — 5/7 agreement.** All five
  `execute_with_defaults` items were routed as expected (c01, c02, c03, c04,
  c07). Both `clarify_private` items were routed as `execute_with_defaults`
  (c05, c06): the current build executes instead of asking for private slots.
- **c05 is a judge-rule false positive, not a market-parameter compile.**
  Its only VT call was `vibe-trading_memory_recall` (17s). The frozen
  `route_class`/`params_compiled` rules count any `vibe-trading_*` call with
  non-empty arguments, so a memory lookup is labelled `execute_with_defaults`.
  c06 (672s) did call data tools, but still without asking for cost basis or
  position size. The report keeps the harness's label and states the caveat;
  it does not rewrite the record.
- **intake_loaded — 0/7 observed, expected 5/7.**
  The `intake` skill does not exist in this image, so zero is the definitional
  result, not a model failure. The skill actually loaded for c01 was
  `research-scenarios` (see Loading evidence). Agreement is
  2/7 because the two
  non-intake execute items (c01, c04) correctly expect `false`.
- **question_called — 0/7 observed, expected 2/7.**
  The `question` tool was not callable in this build: `OPENCODE_ENABLE_QUESTION_TOOL`
  is unset and every session carries `question: deny`. Zero here means the
  mechanism was never exercised; it does **not** mean the model refused to ask.
  The current build's clarification path is text-only: c09's server-side
  message contains numbered choices and c10's contains a refusal, but those
  are `run_error` items and the `question_called` event deliberately does not
  count text-only questions.
- **params_compiled — 7/7 observed, expected 5/7.**
  Every event-complete item called at least one VT tool with arguments.
  Two of those are false positives under the frozen rule (c05, c06 expected
  `false`), which is why agreement is 5/7.
  No ClickHouse credentials exist in `OpencodeAgent/.env`; `ch_*` calls
  therefore fail at call time. That is an environment limitation, not a model
  failure, and it does not change the judge's "tool was called" observation.

## Methodology

- **Corpus and expectations** are frozen in todo 1 (`corpus.jsonl`,
  `expected.json`). The labels describe the *target* artifact-first design;
  the current build is allowed to disagree. Disagreement is the measurement.
- **Driver:** one `opencode run --attach http://localhost:4096 --dir /workspace
  --format json` per item, invoked through `docker exec -w /workspace
  opencode-web`. `run.py` parses the stream, derives the four events, and
  appends validated JSONL. No second driver and no post-hoc editing of
  `baseline.jsonl`.
- **Events:** definitions and judge rules are in
  `tests/evals/intake/events.schema.json`. `route_class` uses the question
  tool / numbered-choice / VT-tool / refusal-marker / text-only cascade;
  `intake_loaded` looks for a `skills/intake` path or the word `intake`;
  `question_called` looks for a `question` tool part; `params_compiled` looks
  for any `vibe-trading_*` or `ch_*` call with non-empty arguments.
- **Terminal record:** four events or one `run_error`. The runner exits 0
  only when every attempted item produced a terminal record.
- **Statistics:** Wilson score interval, two-sided 95%, z =
  1.959963984540054, no continuity correction. Rates are rounded to six
  decimal places inside `recompute.py`; the report's display percentages are
  formatting only. `recompute.py --check` compares the six-decimal values in
  `baseline_meta.json` and in the machine-readable block below with zero
  tolerance.
- **Single pass.** No repeats, no resampling, no model-level retry. A
  single pass cannot separate model noise from a stable behavior.

## Environment

| Item | Value |
|---|---|
| Host | macOS 26.6.2 arm64 |
| Docker runtime | Colima v0.8.1, Docker engine 27.4.0, Ubuntu 24.04.1 LTS VM (aarch64) |
| Docker VM | 4 CPUs, 8,308,150,272 bytes RAM |
| Image platform | `linux/amd64` under QEMU on arm64 |
| Container limits | 6 GiB memory, 0.8 CPU (from `docker-compose.yml`) |
| Readiness probe | `GET /config?directory=/workspace` with basic auth |
| Readiness observed | 41s on the first start, 47s on the fresh restart; budget 300s |
| OpenCode | 1.19.3 |
| Workspace | `/workspace` (image layer; the repository is not bind-mounted) |
| ClickHouse | `CLICKHOUSE_HOST` unset; `ch_*` tools fail at call time |
| Question tool | `OPENCODE_ENABLE_QUESTION_TOOL` unset; session permission `question: deny` |
| Model endpoint | DashScope-compatible `alibaba-cn` provider |

**Volumes gotcha:** the first `compose up` failed with
`error while creating mount source path .../volumes/cron-state: chown ...
permission denied` under Colima. Creating `volumes/cron-state`,
`volumes/cron-logs` and `volumes/vt-memory` before `compose up` made the same
compose invocation succeed. The directories are runtime state, not repository
content, and were cleared before the baseline restart.

### Loading evidence (AGENTS.md + skills)

- **Skills: directly observed.** The c01 raw stream contains a `skill` tool
  call with `name=research-scenarios` and an output headed
  `/workspace/.opencode/skills/research-scenarios`; `opencode debug skill`
  run with `-w /workspace` lists the same location. The c01 raw stream
  mentions `AGENTS.md` 7 times and `问题处理协议` 4 times (through the skill text).
- **AGENTS.md: indirectly observed.** The server session records
  `directory=/workspace` for every run, and the CLI was pinned with
  `--dir /workspace`, so `/workspace/AGENTS.md` is the project-root
  instruction file. OpenCode injects it into the system prompt; the
  `--format json` stream does not persist the system prompt, so there is no
  direct copy to diff. Two independent signals show it was in context:
  c09's server-side reasoning explicitly says “Per AGENTS.md problem-handling
  protocol”, and c08's reasoning says “given the AGENTS.md context”. Those
  sessions are the `run_error` items, so this is evidence about the model
  path, not a published layer rate.
- No tool read of the root `AGENTS.md` appears in the raw stream. That is the
  expected behavior for a system-prompt-injected file, not a failure.

## Budget accounting

| Item | Sessions | Note |
|---|---:|---|
| Baseline corpus (10 items) | 10 | `results/baseline.jsonl` |
| 1-item smoke (300s timeout) | 1 | server session completed after the timeout; harness recorded `run_error` |
| Manual probe (75s timeout) | 1 | confirmed the LLM path and tool stream |
| c08 fast-response re-run | 1 | reproduced the one-line `step_start` stream |
| **Total** | **13** | plan allocation was 11; 2 diagnostic sessions over |

- **Wall time:** the baseline corpus took 2161.739s
  (36.0 min); mean per item
  216.2s, median 41.1s,
  min 8.7s, max 672.2s.
- **The first smoke used the task's suggested 300s timeout and became a
  `run_error` even though its server session finished later.** The full
  run therefore used `--timeout 1800`, not 300. This is the single largest
  operational deviation from the suggested command.
- **Token accounting:** `tokens_total` is **420,592**, the
  sum of the per-item values recorded by `run.py` and therefore reproducible
  from `baseline.jsonl`. These values are the first usage block in the
  stream, and `parse_stream` sums top-level usage fields (`total` plus
  `input`/`output`/`reasoning`), so they are **not** session totals and
  double-count the `total` field. Session-level totals live in
  `baseline_meta.json` under `session_tokens` for budget accounting only;
  they are not used for any published rate and are not reproduced by
  `recompute.py`.
- The plan's total eval budget is 150 sessions. This todo used 13.

## What a baseline cannot tell you

- **n=7 effective items cannot establish significance.** The Wilson intervals
  are wide (e.g. 0/7 is compatible with a true rate up to 35.4%). The 10-item
  corpus is a smoke-scale baseline; significance and paired comparisons live
  in the 40-item Wave 1 run.
- **`run_error` leaves two classes unmeasured.** `direct_answer` and `refuse`
  have n=0 in the published rates. The server sessions completed, but the
  harness's `--format json` stream did not capture them. Do not report the
  direct-answer/refuse rates from this baseline.
- **The judge rules are heuristics.** `params_compiled` counts any
  `vibe-trading_*`/`ch_*` call with arguments, so a memory lookup (c05) is
  indistinguishable from a market-parameter compile. The frozen rule is
  intentional for comparability; the caveat is part of the baseline.
- **A zero is not a model preference.** `intake_loaded=0` is definitional
  (the skill does not exist yet); `question_called=0` is a mechanism
  availability result (tool unset and denied). Neither tells you what the
  model would do if those mechanisms existed.
- **No counterfactual.** This is a single-arm snapshot of the current build;
  it cannot attribute any behavior to a specific AGENTS.md rule or prove that
  the planned intake skill will improve it. Wave 1's paired 40-item run is
  the causal test.
- **Single pass, no repeats.** LLM nondeterminism is not sampled. A behavior
  seen once may not reproduce.
- **Environment-dependent behavior.** ClickHouse is absent, so `ch_*` calls
  fail at call time; network data sources are live and can vary. These are
  environment limitations, not model failures.
- **Token figures are single-step.** The harness token number is the first
  usage block, not the full session. Use `session_tokens` in
  `baseline_meta.json` for budget comparisons, and keep the same method when
  comparing waves.
- **The snapshot is image-specific.** Any config, model, or image change
  invalidates this baseline for Wave 1 comparisons.

## Cleanup

<!-- CLEANUP:begin -->
```text
$ docker compose -f docker-compose.yml -f "$TMPDIR/baseline-override.yml" down
 Container opencode-web Stopping 
 Container opencode-web Stopped 
 Container opencode-web Removing 
 Container opencode-web Removed 
 Network opencodeagent_default Removing 
 Network opencodeagent_default Removed 

$ rm -rf "$TMPDIR/baseline-raw" "$TMPDIR/smoke-raw"
(exit 0, no output)

$ rm -f "$TMPDIR"/{smoke.jsonl,probe.out,probe.err,c08_probe.out,c08_probe.err,tool_list_raw.json,tool_list_raw2.json}
(exit 0, no output)

$ docker ps --format "{{.Names}} {{.Status}}"

$ docker ps -a --filter name=opencode-web --format "{{.Names}} {{.Status}}"

$ test -d "$TMPDIR/baseline-raw"
removed
```
<!-- CLEANUP:end -->

## Reproduce and verify

```bash
cd OpencodeAgent
conda run -n legonanobot python tests/evals/intake/recompute.py
conda run -n legonanobot python tests/evals/intake/recompute.py --check
conda run -n legonanobot python -m pytest tests/evals/intake/test_protocol_snapshot.py tests/evals/intake/test_corpus_schema.py -q
wc -l tests/evals/intake/results/baseline.jsonl
```

`recompute.py` is stdlib-only. It reads `results/baseline.jsonl` and
`corpus.jsonl`, recomputes every published rate and interval, prints them,
and with `--check` compares against `results/baseline_meta.json` and the
block below with zero tolerance.

<!-- VERIFICATION:begin -->
```text
$ conda run -n legonanobot python -m pytest tests/evals/intake/test_protocol_snapshot.py tests/evals/intake/test_corpus_schema.py -q
..............                                                           [100%]
14 passed in 0.18s


$ conda run -n legonanobot python tests/evals/intake/recompute.py --check
PASS baseline_meta.json: published rates match baseline.jsonl
PASS baseline_report.md: published rates match baseline.jsonl


$ wc -l tests/evals/intake/results/baseline.jsonl
      31 tests/evals/intake/results/baseline.jsonl
```

`wc -l` is **31**, not the ≥40 suggested in the task template: seven event-complete items × 4 events = 28, plus three `run_error` records = 31. The three run_error items are c08/c09/c10; the harness still reported `[done] 10/10 terminal records`.
<!-- VERIFICATION:end -->

## Machine-readable published metrics

<!-- recompute:begin -->
```json
{
  "layers": {
    "intake_loaded": {
      "agreement": 2,
      "expected_k": 5,
      "expected_rate": 0.714286,
      "kind": "binary_positive",
      "n": 7,
      "observed_k": 0,
      "observed_rate": 0.0,
      "wilson95": {
        "high": 0.35433,
        "low": 0.0
      }
    },
    "params_compiled": {
      "agreement": 5,
      "expected_k": 5,
      "expected_rate": 0.714286,
      "kind": "binary_positive",
      "n": 7,
      "observed_k": 7,
      "observed_rate": 1.0,
      "wilson95": {
        "high": 1.0,
        "low": 0.64567
      }
    },
    "question_called": {
      "agreement": 5,
      "expected_k": 2,
      "expected_rate": 0.285714,
      "kind": "binary_positive",
      "n": 7,
      "observed_k": 0,
      "observed_rate": 0.0,
      "wilson95": {
        "high": 0.35433,
        "low": 0.0
      }
    },
    "route_class": {
      "agreement": 5,
      "expected_rate": 1.0,
      "kind": "agreement",
      "n": 7,
      "observed_rate": 0.714286,
      "wilson95": {
        "high": 0.917781,
        "low": 0.358934
      }
    }
  },
  "n_event_complete": 7,
  "n_items": 10,
  "n_run_error": 3,
  "n_terminal": 10,
  "operational": {
    "n": 10,
    "rate": 1.0,
    "terminal": 10,
    "wilson95": {
      "high": 1.0,
      "low": 0.722467
    }
  },
  "per_item": {
    "c01": {
      "event_terminal": "params_compiled",
      "route_expected": "execute_with_defaults",
      "route_observed": "execute_with_defaults",
      "tokens": 59316,
      "wall_s": 479.085
    },
    "c02": {
      "event_terminal": "params_compiled",
      "route_expected": "execute_with_defaults",
      "route_observed": "execute_with_defaults",
      "tokens": 61240,
      "wall_s": 491.669
    },
    "c03": {
      "event_terminal": "params_compiled",
      "route_expected": "execute_with_defaults",
      "route_observed": "execute_with_defaults",
      "tokens": 60320,
      "wall_s": 49.909
    },
    "c04": {
      "event_terminal": "params_compiled",
      "route_expected": "execute_with_defaults",
      "route_observed": "execute_with_defaults",
      "tokens": 59634,
      "wall_s": 386.886
    },
    "c05": {
      "event_terminal": "params_compiled",
      "route_expected": "clarify_private",
      "route_observed": "execute_with_defaults",
      "tokens": 59076,
      "wall_s": 16.684
    },
    "c06": {
      "event_terminal": "params_compiled",
      "route_expected": "clarify_private",
      "route_observed": "execute_with_defaults",
      "tokens": 61788,
      "wall_s": 672.218
    },
    "c07": {
      "event_terminal": "params_compiled",
      "route_expected": "execute_with_defaults",
      "route_observed": "execute_with_defaults",
      "tokens": 59218,
      "wall_s": 32.273
    },
    "c08": {
      "event_terminal": "run_error",
      "route_expected": "direct_answer",
      "route_observed": null,
      "tokens": null,
      "wall_s": 12.02
    },
    "c09": {
      "event_terminal": "run_error",
      "route_expected": "direct_answer",
      "route_observed": null,
      "tokens": null,
      "wall_s": 12.279
    },
    "c10": {
      "event_terminal": "run_error",
      "route_expected": "refuse",
      "route_observed": null,
      "tokens": null,
      "wall_s": 8.716
    }
  },
  "route_by_class": {
    "clarify_private": {
      "agreement": 0,
      "n": 2,
      "rate": 0.0,
      "wilson95": {
        "high": 0.65762,
        "low": 0.0
      }
    },
    "direct_answer": {
      "agreement": 0,
      "n": 0,
      "rate": null,
      "wilson95": null
    },
    "execute_with_defaults": {
      "agreement": 5,
      "n": 5,
      "rate": 1.0,
      "wilson95": {
        "high": 1.0,
        "low": 0.565518
      }
    },
    "refuse": {
      "agreement": 0,
      "n": 0,
      "rate": null,
      "wilson95": null
    }
  },
  "run_error_ids": [
    "c08",
    "c09",
    "c10"
  ],
  "tokens_total": 420592,
  "wall_s_total": 2161.739
}
```
<!-- recompute:end -->
