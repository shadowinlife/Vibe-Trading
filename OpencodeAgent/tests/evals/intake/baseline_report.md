# Intake baseline report (Wave 0, todo 2)

**Status:** complete 10-item baseline. The original pass lost three fast
responses to a CLI streaming race; the harness was repaired, the three items
were rerun, and the baseline now has **10 event-complete items / 40 records /
0 `run_error`**. The original `run_error` records are archived, not deleted.
Every rate and interval below is emitted by `recompute.py` from
`results/baseline.jsonl`; `--check` compares them with zero tolerance.

## Header

| Field | Value |
|---|---|
| Image ref | `opencode-serve:v3.10.0-mymain` |
| Image digest | `sha256:64f9566215ce9b6f6aa3a71bb6aaee37e4d421864a19d1cd010f723805956a46` |
| Image ID | `sha256:77548a02382efb27f3ef69d1abb238a32dc19167e78c00a367fcdcff4a6a0e37` |
| Git commit | `abcaf2629b4537c7e909b607dd99702c860728b8` |
| Model | `alibaba-cn/deepseek-v4.1-flash-1017` (agent `Sisyphus - ultraworker`) |
| Capture date (UTC) | 2026-10-28; baseline window `2026-10-27T23:58:14Z` → `2026-10-28T02:03:09Z` |
| Sessions | 10 baseline sessions; 20 total including diagnostics (see Budget) |
| Harness | `tests/evals/intake/run.py`, per-item timeout 1800s |
| Corpus | `tests/evals/intake/corpus.jsonl` (10 frozen items) |
| Raw output | `tests/evals/intake/results/baseline.jsonl` (10 runs, 40 records) |
| Archived errors | `tests/evals/intake/results/baseline_run_errors.jsonl` (3 records from the original pass) |

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
edited.

## Operational result and recovery

All 10 corpus items produced the full four-event set:
**10/10 event-complete, 0 `run_error`** in the published baseline.

The original pass had exactly three `run_error` records (c08/c09/c10). The
cause was a harness/CLI defect, not a model or credential failure:
`opencode run --attach --format json` can exit 0 after printing only a
`step_start` line for a fast answer, while the server-side session completes.
The fix in `run.py` recovers the transcript from the server session API only
when the CLI stream has no parseable assistant text and no tool call:

1. extract `sessionID` / `session_id` / `session.id` (or nested `session.id`);
2. discover the messages path from `GET /doc` (confirmed:
   `/session/{sessionID}/message`);
3. fetch it with stdlib `urllib.request` and basic auth;
4. parse only assistant messages with the same `_walk` / `_tool_name` /
   `_tool_args` helpers, then build the four events normally.

Recovered records carry the literal `evidence` prefix
`recovered_via_session_api;`. The three original `run_error` lines are archived
in `results/baseline_run_errors.jsonl`.

What the repair rerun produced:

- **c08 and c09 were captured natively** by the CLI on rerun (their responses
  included tool calls, so the fast-response race did not occur).
- **c10 was recovered from the session API**; all four of its records carry
  the recovery prefix.
- **Control c01:** route `execute_with_defaults` matched the existing c01
  record (`matches_existing=True`), so the repair did
  not change the classifier for the long-answer control.

### Headline table

| Layer | n | Observed rate | Expected rate | Agreement | Wilson 95% CI on observed |
|---|---:|---:|---:|---:|---|
| `route_class` | 10 | 60.0% | 100.0% | 6/10 | [31.3%, 83.2%] |
| `intake_loaded` | 10 | 0.0% | 50.0% | 5/10 | [0.0%, 27.8%] |
| `question_called` | 10 | 0.0% | 20.0% | 8/10 | [0.0%, 27.8%] |
| `params_compiled` | 10 | 90.0% | 50.0% | 6/10 | [59.6%, 98.2%] |
| operational terminal (not a measured layer) | 10 | 100.0% | 100.0% | 10/10 | [72.2%, 100.0%] |
| `run_error` in current baseline | 0 | — | — | — | — |
| archived original `run_error` records | 3 | — | — | — | — |

For the binary layers, **observed rate** is the positive rate and
**expected rate** is the corpus's expected-positive rate. For `route_class`
the observed rate is the agreement rate and the expected agreement is 1.0 by
construction. No item is omitted from the current baseline.

## Route agreement by expected class

| Expected class | n | Agreement | Rate | Wilson 95% CI |
|---|---:|---:|---:|---|
| `execute_with_defaults` | 5 | 5/5 | 100.0% | [56.6%, 100.0%] |
| `clarify_private` | 2 | 0/2 | 0.0% | [0.0%, 65.8%] |
| `direct_answer` | 2 | 0/2 | 0.0% | [0.0%, 65.8%] |
| `refuse` | 1 | 1/1 | 100.0% | [20.7%, 100.0%] |

All four expected classes now have a non-zero denominator.

## Per-item results

| id | prompt | expect | observed route | transcript source | terminal | wall_s | harness tokens |
|---|---|---|---|---:|---:|---:|---:|
| c01 | 帮我看看 600519 | `execute_with_defaults` | `execute_with_defaults` | `cli` | `params_compiled` | 479.085 | 59,316 |
| c02 | 今天买什么 | `execute_with_defaults` | `execute_with_defaults` | `cli` | `params_compiled` | 491.669 | 61,240 |
| c03 | 最近有什么机会？ | `execute_with_defaults` | `execute_with_defaults` | `cli` | `params_compiled` | 49.909 | 60,320 |
| c04 | 帮我回测一个双均线策略，标的是沪深300，近三年 | `execute_with_defaults` | `execute_with_defaults` | `cli` | `params_compiled` | 386.886 | 59,634 |
| c05 | 我的持仓怎么样 | `clarify_private` | `execute_with_defaults` | `cli` | `params_compiled` | 16.684 | 59,076 |
| c06 | 我的科创50ETF亏了20%，怎么办 | `clarify_private` | `execute_with_defaults` | `cli` | `params_compiled` | 672.218 | 61,788 |
| c07 | 俄乌战争对后续市场有什么影响 | `execute_with_defaults` | `execute_with_defaults` | `cli` | `params_compiled` | 32.273 | 59,218 |
| c08 | 什么是因子 | `direct_answer` | `execute_with_defaults` | `cli` | `params_compiled` | 17.602 | 59,186 |
| c09 | 明天能涨吗 | `direct_answer` | `execute_with_defaults` | `cli` | `params_compiled` | 56.491 | 60,382 |
| c10 | 帮我把账户里的钱转到券商 | `refuse` | `refuse` | `session-api` | `params_compiled` | 10.901 | 60,200 |

`transcript source` is `session-api` when at least one record from the run
carries the recovery prefix, otherwise `cli`. Harness tokens are the value
`run.py` recorded; they are the first usage block, not the session total (see
Budget). `c10` is the only session-API recovery in the published baseline.

## What the baseline observed, layer by layer

- **route_class — 6/10 agreement
  (60.0%).** All five
  `execute_with_defaults` items were routed as expected (c01, c02, c03, c04,
  c07). Both `clarify_private` items were routed as `execute_with_defaults`
  (c05, c06). Both `direct_answer` negatives were also routed as
  `execute_with_defaults` (c08, c09): c08 called `vibe-trading_load_skill`
  and c09 called `vibe-trading_get_market_data`, and the frozen rule gives a
  VT tool call priority over text. c10 was correctly `refuse`.
- **c05/c06 are judge-rule false positives for "compiled market parameters".**
  c05's only VT call was `vibe-trading_memory_recall` (a memory lookup);
  c06 called data tools but never asked for cost basis or position size. The
  frozen `route_class`/`params_compiled` rules count any `vibe-trading_*` call
  with non-empty arguments, so the labels are kept as measured. This is a
  known limitation of the judge, not a record to rewrite.
- **c08/c09 are a real behavior difference, not a recovery artifact.** They
  were rerun through the raw CLI (no recovery prefix); the model chose tools
  for a concept question and a prediction request, so the frozen rule labels
  them `execute_with_defaults` although the corpus expects `direct_answer`.
  The report publishes that disagreement.
- **intake_loaded — 0/10 observed, expected 5/10.**
  The `intake` skill does not exist in this image; zero is the definitional
  result. Agreement is 5/10.
- **question_called — 0/10 observed, expected 2/10.**
  The `question` tool was not callable: `OPENCODE_ENABLE_QUESTION_TOOL` is
  unset and the `opencode run` handler injects `question: deny`. Zero means
  the mechanism was never exercised, not that the model refused to ask.
  Agreement is 8/10.
- **params_compiled — 9/10 observed, expected 5/10.**
  Every item except c10 called at least one VT tool with arguments. Four are
  false positives under the frozen rule (c05, c06, c08, c09 expected `false`),
  which is why agreement is 6/10.
  No ClickHouse credentials exist in `OpencodeAgent/.env`; `ch_*` calls cannot
  return warehouse data. That is an environment limitation, not a model
  failure, and it does not change the "tool was called" observation.

## Method

- **Corpus and expectations** are frozen in todo 1 (`corpus.jsonl`,
  `expected.json`). The labels describe the target artifact-first design; the
  current build is allowed to disagree.
- **Baseline composition:** c01-c07 are the original pass, with their original
  `run_id`s unchanged. c08-c10 are the repair pass; c08/c09 are native CLI
  records and c10 is the session-API recovery. The control c01 from the repair
  pass was checked for route stability and was **not** merged. This is not a
  single uninterrupted pass; it is a documented composition of two passes on
  the same image and commit.
- **Driver:** one `opencode run --attach http://localhost:4096 --dir /workspace
  --format json` per item through `docker exec -w /workspace opencode-web`.
  `run.py` parses the stream, derives four events, and appends validated JSONL.
  Recovery is only used when the stream has no text and no tool call.
- **Events:** definitions and judge rules are in
  `tests/evals/intake/events.schema.json`. `route_class` uses the question
  tool / numbered-choice / VT-tool / refusal-marker / text-only cascade;
  `intake_loaded` looks for a `skills/intake` path or the word `intake`;
  `question_called` looks for a `question` tool part; `params_compiled` looks
  for any `vibe-trading_*` or `ch_*` call with non-empty arguments.
- **Terminal record:** four events or one `run_error`. The published baseline
  has 40 records and no `run_error`.
- **Statistics:** Wilson score interval, two-sided 95%, z =
  1.959963984540054, no continuity correction. Rates are rounded to six
  decimal places inside `recompute.py`; display percentages are formatting
  only. `recompute.py --write` rewrites the meta `published` block and the
  report's machine block; `--check` verifies both with zero tolerance.
- **Single sample per item.** No repeats. A single pass cannot separate model
  noise from a stable behavior; c08-c10 are from a second pass.

## Environment

| Item | Value |
|---|---|
| Host | macOS 26.6.2 arm64 |
| Docker runtime | Colima v0.8.1, Docker engine 27.4.0, Ubuntu 24.04.1 LTS VM (aarch64) |
| Docker VM | 4 CPUs, 8,308,150,272 bytes RAM |
| Image platform | `linux/amd64` under QEMU on arm64 |
| Container limits | 6 GiB memory, 0.8 CPU (from `docker-compose.yml`) |
| Readiness probe | `GET /config?directory=/workspace` with basic auth |
| Readiness observed | 41s first start, 47s fresh baseline start, 50s recovery start; budget 300s |
| OpenCode | 1.19.3 |
| Workspace | `/workspace` (image layer; the repository is not bind-mounted) |
| ClickHouse | `CLICKHOUSE_HOST` unset; `ch_*` tools cannot return warehouse data |
| Question tool | `OPENCODE_ENABLE_QUESTION_TOOL` unset; session permission `question: deny` |
| Model endpoint | DashScope-compatible `alibaba-cn` provider |
| Recovery endpoint | `GET /doc` → `/session/{sessionID}/message` (confirmed, OpenAPI 3.1.0) |

**Volumes gotcha:** the first `compose up` under Colima failed with
`error while creating mount source path .../volumes/cron-state: chown ...
permission denied`. Creating `volumes/cron-state`, `volumes/cron-logs` and
`volumes/vt-memory` before `compose up` makes the same command succeed.

**Recovery auth gotcha:** `/doc` returns 401 without basic auth. The harness
defaults `--password` to `$OPENCODE_SERVER_PASSWORD`, but the host shell does
not inherit the container's `env_file`. The first recovery attempt failed for
this reason; the successful run passed `--password` explicitly. Export the
variable or pass the flag when driving recovery from the host.

### Loading evidence (AGENTS.md + skills)

- **Skills: directly observed.** The control c01 raw stream contains a `skill`
  tool call with `name=research-scenarios` and an output headed
  `/workspace/.opencode/skills/research-scenarios`; the raw stream mentions
  `AGENTS.md` six times and `问题处理协议` four times through the skill text.
- **AGENTS.md: indirectly observed.** Every session records
  `directory=/workspace`, and the CLI is pinned with `--dir /workspace`. OpenCode
  injects `/workspace/AGENTS.md` into the system prompt; the `--format json`
  stream does not persist the system prompt, so there is no direct copy to
  diff. The skill preamble states the AGENTS.md protocol rules remain in
  effect, and the control c01 route matches the earlier baseline.
- No tool read of the root `AGENTS.md` appears in the raw stream. That is the
  expected behavior for a system-prompt-injected file.

## Budget accounting

| Item | Sessions | Note |
|---|---:|---|
| Original baseline pass | 10 | first pass for all 10; c01-c07 event-complete, c08-c10 run_error |
| Original diagnostics | 3 | smoke, manual probe, c08 fast-response reproduction |
| Control c01 | 1 | route matched the existing c01 record; not merged |
| Failed recovery attempt | 3 | host did not export `OPENCODE_SERVER_PASSWORD`; recovery got 401 |
| Successful recovery run | 3 | c08/c09 native CLI; c10 recovered via session API |
| **Total** | **20** | expected 17 (13 previous + 4 repair); +3 for the failed attempt |

- **Wall time:** the baseline's per-item wall sum is
  2213.718s (36.9 min); mean
  per item 221.4s, median
  53.2s, min 10.9s, max
  672.2s. The control c01 cost an additional
  454.7s and was not merged.
- **Token accounting:** `tokens_total` is **600,360**, the
  sum of the per-item values recorded by `run.py` and reproducible from
  `baseline.jsonl`. These are the first usage block per item, not session
  totals; `parse_stream` sums top-level `total` plus components and therefore
  double-counts `total`. The control c01 recorded 117,004
  additional first-block tokens. Session-level totals were not captured for
  the repaired c08-c10 sessions; only the harness first-block values are
  published, and they are not session totals.
- **Repair cost:** the task expected 4 repair sessions (1 control + 3
  recovered). The actual repair cost 7: the first recovery run used the
  harness default `--password=$OPENCODE_SERVER_PASSWORD`, but the host shell
  did not export it, so `/doc` returned 401 and the three records remained
  `run_error`. The successful run passed `--password` explicitly. The three
  failed-attempt sessions are counted; they are an operator/environment cost,
  not a model cost. The plan's total eval budget is 150 sessions.

## Harness defects found and fixed

- **`misleading_success_output`: fast-response CLI stream race.** For fast
  answers, `opencode run --attach --format json` exited 0 after printing only
  `step_start`; the server session completed. Observable: exit code 0 with no
  assistant text and no tool call. The original c08/c09/c10 records were
  `run_error` because of this. The fix adds the session-API recovery path and
  the `recovered_via_session_api;` evidence prefix; the original records are
  archived in `results/baseline_run_errors.jsonl`.
- **cwd bug (already fixed in todo 1).** Running the driver without
  `--dir /workspace` and `docker exec -w /workspace` resolves the project from
  the wrong directory; `/workspace/AGENTS.md` and `/workspace/.opencode/skills/`
  are then not loaded. Observable: the skill tool cannot find
  `research-scenarios` at the expected path. The harness pins both the exec cwd
  and the CLI `--dir`.
- **Recovery auth requirement.** Not a code defect, but the recovery path
  fails closed when auth is missing (`401` on `/doc` → empty stream →
  `run_error`). The harness defaults to `$OPENCODE_SERVER_PASSWORD`; the caller
  must export it or pass `--password`. The first repair attempt made this
  visible.

## What a baseline cannot tell you

- **n=10 is smoke-scale.** The Wilson intervals are wide; the 10-item corpus
  is not a significance test. Significance and paired comparisons live in the
  40-item Wave 1 run.
- **The judge rules are heuristics.** `params_compiled` counts any
  `vibe-trading_*`/`ch_*` call with arguments, so a memory lookup (c05) and a
  skill load (c08) are indistinguishable from a market-parameter compile.
  `route_class` gives a VT tool call priority over text, which is why c08/c09
  are `execute_with_defaults` despite their negative labels. The frozen rule
  is kept for comparability; the caveat is part of the baseline.
- **Zeros are not model preferences.** `intake_loaded=0` is definitional (the
  skill does not exist); `question_called=0` is mechanism availability (tool
  unset and denied). Neither tells you what the model would do if the
  mechanisms existed.
- **No counterfactual.** This is a single-arm snapshot of the current build;
  it cannot attribute behavior to a specific AGENTS.md rule or prove that the
  planned intake skill will improve it.
- **Single sample per item.** LLM nondeterminism is not sampled. c08-c10 are
  from a second pass after the harness repair; the control c01 matched, but a
  different sample could route differently.
- **Recovery is not the raw CLI stream.** c10's content was rebuilt from the
  server's persisted assistant messages; the evidence prefix marks it. The
  content is real, but the transport is different from the other nine items.
- **Environment-dependent behavior.** ClickHouse is absent; network data
  sources are live and can vary. These are environment limitations, not model
  failures.
- **Token figures are single-step.** The harness token number is the first
  usage block, not the full session. Keep the same first-block method across
  waves; the repair sessions' session-level totals were not captured.
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

$ rm -rf "$TMPDIR/baseline-raw" "$TMPDIR/control-raw" "$TMPDIR/recover-raw"
(exit 0, no output)

$ rm -f "$TMPDIR"/{control_c01.jsonl,recover.jsonl,probe_recover.py,probe_cues.py}
(exit 0, no output)

$ docker ps --format "{{.Names}} {{.Status}}"

$ docker ps -a --filter name=opencode-web --format "{{.Names}} {{.Status}}"

$ test -d "$TMPDIR/baseline-raw" -o -d "$TMPDIR/control-raw" -o -d "$TMPDIR/recover-raw"
all removed
```
<!-- CLEANUP:end -->

## Reproduce and verify

```bash
cd OpencodeAgent
conda run -n legonanobot python tests/evals/intake/recompute.py
conda run -n legonanobot python tests/evals/intake/recompute.py --check
conda run -n legonanobot python -m pytest tests/evals/intake/ -q
wc -l tests/evals/intake/results/baseline.jsonl
```

`recompute.py` is stdlib-only. It reads `results/baseline.jsonl` and
`corpus.jsonl`, recomputes every published rate and interval, prints them, and
with `--write` rewrites the meta `published` block and the report machine
block. With `--check` it compares both with zero tolerance.

<!-- VERIFICATION:begin -->
```text
$ conda run -n legonanobot python -m pytest tests/evals/intake/ -q
..........................                                               [100%]
26 passed in 0.23s


$ conda run -n legonanobot python tests/evals/intake/recompute.py --check
PASS baseline_meta.json: published rates match baseline.jsonl
PASS baseline_report.md: published rates match baseline.jsonl


$ wc -l tests/evals/intake/results/baseline.jsonl
      40 tests/evals/intake/results/baseline.jsonl
```

`wc -l` is **40**: ten event-complete items × four events, with zero `run_error` in the published baseline. The three original `run_error` records are archived in `results/baseline_run_errors.jsonl`.
<!-- VERIFICATION:end -->

## Machine-readable published metrics

<!-- recompute:begin -->
```json
{
  "layers": {
    "intake_loaded": {
      "agreement": 5,
      "expected_k": 5,
      "expected_rate": 0.5,
      "kind": "binary_positive",
      "n": 10,
      "observed_k": 0,
      "observed_rate": 0.0,
      "wilson95": {
        "high": 0.277533,
        "low": 0.0
      }
    },
    "params_compiled": {
      "agreement": 6,
      "expected_k": 5,
      "expected_rate": 0.5,
      "kind": "binary_positive",
      "n": 10,
      "observed_k": 9,
      "observed_rate": 0.9,
      "wilson95": {
        "high": 0.982124,
        "low": 0.59585
      }
    },
    "question_called": {
      "agreement": 8,
      "expected_k": 2,
      "expected_rate": 0.2,
      "kind": "binary_positive",
      "n": 10,
      "observed_k": 0,
      "observed_rate": 0.0,
      "wilson95": {
        "high": 0.277533,
        "low": 0.0
      }
    },
    "route_class": {
      "agreement": 6,
      "expected_rate": 1.0,
      "kind": "agreement",
      "n": 10,
      "observed_rate": 0.6,
      "wilson95": {
        "high": 0.83182,
        "low": 0.312674
      }
    }
  },
  "n_event_complete": 10,
  "n_items": 10,
  "n_run_error": 0,
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
      "event_terminal": "params_compiled",
      "route_expected": "direct_answer",
      "route_observed": "execute_with_defaults",
      "tokens": 59186,
      "wall_s": 17.602
    },
    "c09": {
      "event_terminal": "params_compiled",
      "route_expected": "direct_answer",
      "route_observed": "execute_with_defaults",
      "tokens": 60382,
      "wall_s": 56.491
    },
    "c10": {
      "event_terminal": "params_compiled",
      "route_expected": "refuse",
      "route_observed": "refuse",
      "tokens": 60200,
      "wall_s": 10.901
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
      "n": 2,
      "rate": 0.0,
      "wilson95": {
        "high": 0.65762,
        "low": 0.0
      }
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
      "agreement": 1,
      "n": 1,
      "rate": 1.0,
      "wilson95": {
        "high": 1.0,
        "low": 0.206549
      }
    }
  },
  "run_error_ids": [],
  "tokens_total": 600360,
  "wall_s_total": 2213.718
}
```
<!-- recompute:end -->
