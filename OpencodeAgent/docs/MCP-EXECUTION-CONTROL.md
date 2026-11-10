# MCP execution control contract

> Contract version: `mcp-execution-control-v1`
> Status: delivered-candidate. Gates G-control, G-backtest and G-factor remain `pending`.
> Verified by: `pending`. Verified at: `pending`.
> Human confirmation is required before any consumer treats this document as a delivery receipt.

This document is the published VT side of RM change request 0013. It describes the
routes, payloads, digests, filesystem effects and failure semantics implemented in
this branch. It is not a gate receipt, not an end-to-end integration report and not
evidence that the RM consumer has passed acceptance. The request document remains
the source of intent; where this document and the request differ, this document
records the implemented behavior and names the difference as an RM alignment item.

The contract adds an original-instance control plane for long executions. It does
not replace the strategy-run entry from VT-STRAT-0001, the cancel route from
VT-STRAT-0002 or the version precheck from VT-STRAT-0003. It also does not change
the artifact layout or the `summary` 1.0 object frozen by 0012.

## 1. Entry point and authentication

The routes are mounted by `agent/strategy_run_server.py`. The dedicated entry
mounts health, strategy runs, execution instances, factor prototype, backtest
prepare/execute and state export/import. It starts no scheduler and exposes no SPA
or catch-all fallback.

Every route in this document except `GET /health` requires the RM service token:

```http
Authorization: Bearer $RM_VT_STRATEGY_RUN_TOKEN
```

The expected value is read on every request from the file named by
`RM_VT_STRATEGY_RUN_TOKEN_FILE`. The file is a mounted `0600` file. A rotated mount
takes effect without a restart.

| Condition | Status | Fixed body |
|---|---|---|
| Missing `Authorization`, non-Bearer scheme, empty credential | `401` | `{"detail":"unauthorized"}` |
| Token file unset, missing, unreadable, empty, or value mismatch | `403` | `{"detail":"forbidden"}` |

The token path, its contents and the request header never appear in a response or
a log line. A token configuration failure does not stop the entry process.

Identifier grammars:

| Field | Limit | Pattern | Notes |
|---|---|---|---|
| `dispatch_key` on prepare | 1..200 | `^[A-Za-z0-9._~:-]+$` | Colon is accepted by prepare only. |
| `dispatch_key` on start, stop, find | 1..200 | `^[A-Za-z0-9._~-]+$` | Colon is rejected. Use the common safe set. |
| `remote_id`, `run_id`, `stop_key` | 1..128 | `^[A-Za-z0-9._~-]+$` | URL path safe. |

The prepare/start grammar difference is a real integration hazard. A key such as
`user:1:job:9:1` is accepted by prepare and then rejected with `400` by start,
stop or find. RM should use only `A-Za-z0-9._~-` until the grammars are unified.

Error bodies from execution and control routes are fixed text. A projection
detail, SQL fragment, filesystem path or token never reaches the body or a log
line. Unknown failure kinds fail closed as `storage_unavailable`.

## 2. Route map

| Method and path | Purpose | Success | Principal failures |
|---|---|---|---|
| `GET /execution-instances?dispatch_key=...` | Find an original instance by client dispatch key | `200` with one of `not_dispatched`, `dispatched`, `absent` | `400 invalid_request`, `503 storage_unavailable`, `503 identity_conflict` |
| `GET /execution-instances/{remote_id}` | Read the five-state status | `200` status body | `400 invalid_request`, `404 not_found`, `503 storage_unavailable` |
| `GET /execution-instances/{remote_id}/result` | Read the bounded result artifact | `200` available or unavailable body | `400 invalid_request`, `404 not_found`, `503 storage_unavailable` |
| `POST /execution-instances` | Start or replay a prepared backtest or a frozen factor declaration | `202` accepted-start body | `400 invalid_request`, `409 prepare_required`, `409 idempotency_conflict`, `409 input_digest_mismatch`, `503 storage_unavailable`, `503 worker_start_failed` |
| `POST /execution-instances/{remote_id}/stop` | Resolve one idempotent stop tuple | `202` stop body | `400 invalid_request`, `404 not_found`, `409 idempotency_conflict`, `503 storage_unavailable` |
| `POST /backtest/prepare` | Validate and freeze one backtest input set | `200` prepare response | `400 invalid_input`, `404 strategy_not_found`, `404 version_not_found`, `409 input_digest_mismatch`, `409 idempotency_conflict`, `503 storage_unavailable` |
| `POST /backtest/execute` | Execute only a fully prepared frozen backtest | `202` accepted-start body | `400 invalid_request`, `409 prepare_required`, `409 idempotency_conflict`, `409 input_digest_mismatch`, `503 storage_unavailable`, `503 worker_start_failed` |
| `POST /factor/prototype` | Start or replay one frozen factor prototype batch | `202` accepted-start body | `400 invalid_request`, `409 idempotency_conflict`, `409 input_digest_mismatch`, `503 storage_unavailable`, `503 worker_start_failed` |
| `POST /strategy-runs` | Legacy 0001 strategy-run start | `202 {"vt_run_id":"..."}` | `409 duplicate_run_key`, `422` model validation, `503 entry unavailable` |
| `GET /strategy-runs/{vt_run_id}` | Legacy 0001 three-field status | `200` status body | `404 unknown vt_run_id`, `503 entry unavailable` |
| `POST /strategy-runs/{vt_run_id}/cancel` | Legacy 0002 cancel with stop evidence | `202` cancel body | `404 strategy_run_not_found`, `503 entry unavailable` |

`GET /strategy-runs/{vt_run_id}/cancel` stays a `404`; only POST cancels. There
is no second start route, no second cancel route and no second version-query
route.

## 3. Find: `GET /execution-instances`

Request query:

| Parameter | Required | Constraint |
|---|---|---|
| `dispatch_key` | yes | 1..200, `^[A-Za-z0-9._~-]+$` |

The read examines the native `execution_dispatches` table and the legacy
`strategy_runs.run_key` column in one SQLite read transaction. It creates no run
record and starts no work.

### 3.1 `not_dispatched`

```json
{"state":"not_dispatched","dispatch_key":"user-1.job-9.1"}
```

Returned only when both the dispatch table and the legacy run table lack the key
and the read succeeded. A storage fault is `503 {"error":"storage_unavailable"}`.
An unreadable or unknown store is never downgraded to `not_dispatched`.

### 3.2 `dispatched`

```json
{
  "state":"dispatched",
  "dispatch_key":"user-1.job-9.1",
  "remote_id":"mcp-long-demo-1",
  "status":"running",
  "run_dir":"/home/opencode/.vibe-trading/runs/mcp-long-demo-1",
  "started_at":"2026-11-10T00:00:02.000Z",
  "finished_at":null,
  "error_kind":null
}
```

`status` is one of `running`, `completed`, `failed`, `cancelled`, `unknown`.
`run_dir` is null for a legacy strategy-run row. `started_at`, `finished_at` and
`error_kind` are null when not observed. `error_kind` is non-null only for
`failed`, and unknown stored error text normalizes to `runner_error`. The find body
does not echo `contract_version`; the contract is fixed by the entry.

### 3.3 `absent`

```json
{
  "state":"absent",
  "dispatch_key":"user-1.job-9.1",
  "remote_id":"mcp-long-demo-1",
  "last_status":"cancelled",
  "absence_evidence":"terminal_record",
  "observed_at":"2026-11-10T00:05:00.000Z"
}
```

`absent` requires a durable dispatch row whose status is terminal plus a confirmed
stop whose evidence kind is one of `terminal_record`, `process_absent`,
`ttl_expired`, `explicit_absence`. `last_status` is the terminal dispatch status.
`observed_at` is the stop resolution time. A `process_exit` confirmation alone does
not produce `absent`. A bare `404` from another lookup is not absence evidence.

### 3.4 Cross-table collision

If a native dispatch and a legacy run share the same key but name different remote
identities, find returns `503 {"error":"identity_conflict"}`. The response carries
no remote id, run path or detail. If both tables name the same remote identity,
the native dispatch projection wins.

## 4. Status: `GET /execution-instances/{remote_id}`

The read resolves a native dispatch by `remote_id` first, then a legacy
`strategy_runs.vt_run_id`. A row that exists but cannot be observed is `unknown`,
not `404`. `404 {"error":"not_found"}` means neither table has the identity.
Malformed identifiers are `400 {"error":"invalid_request"}`. Storage faults are
`503 {"error":"storage_unavailable"}`.

Response body:

```json
{
  "remote_id":"mcp-long-demo-1",
  "dispatch_key":"user-1.job-9.1",
  "status":"running",
  "started_at":"2026-11-10T00:00:02.000Z",
  "finished_at":null,
  "error_kind":null,
  "run_dir":"/home/opencode/.vibe-trading/runs/mcp-long-demo-1",
  "contract_version":"mcp-execution-control-v1"
}
```

Rules:

- Terminal states are stable. Repeated reads return the same terminal state and
  never restart work.
- A nonterminal native row is `running` only when this supervisor still owns a live
  handle for the remote id. After a process restart, or with no live handle, it is
  `unknown`.
- Handle observation boundary: the read service reports `running` only when the
  host app registers an `execution_handle_provider`. The dedicated entry as
  committed does not register a process-wide provider, so a live native dispatch
  can read `unknown` until its terminal record is written. `unknown` is never
  proof of stop and must not release the RM execution slot.
- A legacy `running` row follows the same handle rule.
- `failed` always carries `error_kind`. The closed vocabulary is `timeout`,
  `runner_error`, `strategy_not_found`, `version_not_found`,
  `input_digest_mismatch`.
- Timestamps are UTC ISO-8601 with millisecond precision and a `Z` suffix, or null.
- `run_dir` is null for legacy rows.

## 5. Result read: `GET /execution-instances/{remote_id}/result`

The body has exactly seven fields:

```json
{
  "remote_id":"mcp-long-demo-1",
  "status":"completed",
  "result_status":"available",
  "result":{"summary":{"schema_version":"1.0"}},
  "progress":null,
  "has_more":false,
  "contract_version":"mcp-execution-control-v1"
}
```

`result_status` is `available` or `unavailable`. An unavailable body has
`result: null` and `progress: null`. `has_more` is always `false`; the result read
is not paginated. A malformed identifier or a result path that resolves outside
the server-declared run directory is `400 {"error":"invalid_request"}`. An unknown
remote id is `404 {"error":"not_found"}`. A storage fault is `503`.

Availability by row kind:

| Row kind | Available result | Unavailable result |
|---|---|---|
| Native backtest with prepare provenance | Completed row, valid summary 1.0, valid `run_card.json` artifact manifest | Any incomplete run, missing or invalid summary, missing or invalid card, declaration/row provenance disagreement |
| Native factor | Complete 0013 `result.json` bound to the dispatch identity, registry digest, tool schema and contract version | `result.json` missing or invalid, then the supervisor terminal record is used if present; otherwise unavailable |
| Legacy dispatch without prepare provenance | Bounded `result_path` JSON inside the run directory | Missing, oversized, non-finite, malformed, or escaping path |
| Legacy `strategy_runs` row | None | Always unavailable |

Size bounds are fail-closed: the factor `result.json` cap is 8 MiB, progress is
50 KiB, the backtest `run_card.json` cap is 8 MiB, and a generic result file is
50 KiB. An oversized file is unavailable or ignored; it is never truncated into a
partial success.

## 6. Backtest prepare: `POST /backtest/prepare`

Prepare validates and materializes one frozen backtest directory. It does not
fetch market data, start compute or incur data cost.

### 6.1 Request

```json
{
  "dispatch_key":"user-1.job-9.1",
  "run_dir":"/home/opencode/.vibe-trading/runs/mcp-long-demo-1",
  "config":{
    "strategy_id":"demo_momentum",
    "strategy_version":"v1",
    "market":"CN_A",
    "symbols":["000001.SZ"],
    "start_date":"2024-01-02",
    "end_date":"2024-12-31",
    "bar_interval":"1d",
    "data_source":"local",
    "data_as_of":"2024-12-31",
    "snapshot_basis":"local-csv-v1",
    "initial_cash":"100000.00",
    "currency":"CNY",
    "commission_bps":"2.50",
    "tax_bps":"5.00",
    "slippage_bps":"10.00"
  },
  "source":{
    "text":"print(\"hello from the frozen signal engine\")\n",
    "sha256":"4a42e6521d96837f823ec83fa2bf7e25f27ee03301682251f5704a9dd9441380"
  },
  "data_source":"local",
  "data_as_of":"2024-12-31",
  "snapshot_basis":"local-csv-v1",
  "contract_version":"mcp-execution-control-v1"
}
```

The request is strict: unknown fields are rejected. `config` must contain exactly
the 15 neutral fields. The top-level `data_source`, `data_as_of` and
`snapshot_basis` duplicate the embedded config values and must match exactly,
including case. `source.text` is the exact UTF-8 source text. `source.sha256` must
equal `sha256(source.text.encode("utf-8"))`; a mismatch is `400 invalid_input`.

`run_dir` must be absolute and resolve inside the configured run roots. In the
production image the canonical root is `/home/opencode/.vibe-trading/runs/`. The
final segment must match `mcp-long-[A-Za-z0-9._-]+-[1-9][0-9]*`, be at most 128
characters and equal the resolved directory name. That final segment is the
remote identity. Prepare does not return a separate `remote_id`.

### 6.2 Success response

```json
{
  "run_dir":"/home/opencode/.vibe-trading/runs/mcp-long-demo-1",
  "config_sha256":"773fc86715c8599596e27c17352fcb5e3aecb230fc6c2d015827b219f61383fa",
  "source_sha256":"4a42e6521d96837f823ec83fa2bf7e25f27ee03301682251f5704a9dd9441380",
  "input_digest":"01ff6fbbb4edd179023cdb9f11f63845fc67b73001dd3101e2ecfdc1be55d418",
  "data_source":"local",
  "data_as_of":"2024-12-31",
  "data_digest":null,
  "data_digest_status":"unavailable",
  "contract_version":"mcp-execution-control-v1"
}
```

`config_sha256`, `source_sha256` and `input_digest` are 64 lowercase hex
characters. `config_sha256` is the SHA-256 of the canonical JSON bytes of
`request.config`. `source_sha256` is the SHA-256 of the frozen source bytes.
`input_digest` is the D-3 digest in section 10.

`data_digest` is a snapshot digest when the configured local input file set is
completely enumerable and readable. Otherwise it is `null` with
`data_digest_status="unavailable"`. A non-local `data_source` always reports
`unavailable`; this is not an error and does not block execution. `verified` means
only that the declared local files were hashed, not that every row's OHLC coverage
was executed.

### 6.3 Materialization

- The source bytes must equal the frozen `code/signal_engine.py` bytes of
  `strategy_id` and `strategy_version`. A byte difference is
  `409 input_digest_mismatch`.
- The directory is staged as a sibling temporary directory, fsynced and renamed
  into place. A failure leaves no half-written run directory.
- `config.json` and `code/signal_engine.py` are written read-only (`0444`). No
  later path rewrites them.
- An existing target directory is never overwritten.
- The same `dispatch_key` with the same canonical declaration replays the stored
  response and rewrites nothing. The replay still verifies the frozen files; drift
  is `409 input_digest_mismatch`.
- The same `dispatch_key` with a different declaration is
  `409 {"error":"idempotency_conflict","remote_id":"<original>"}`. Prepare uses
  `remote_id`; start uses `original_remote_id`.

### 6.4 Prepare failures

| Condition | Status | Body |
|---|---|---|
| Schema, key grammar, relative or outside run dir, bad run-id segment, chronology, duplicate provenance, source sha mismatch, extra field | `400` | `{"error":"invalid_input"}` |
| Strategy directory absent | `404` | `{"error":"strategy_not_found"}` |
| Version config or source absent | `404` | `{"error":"version_not_found"}` |
| Source bytes differ from the frozen version | `409` | `{"error":"input_digest_mismatch"}` |
| Same key, different declaration | `409` | `{"error":"idempotency_conflict","remote_id":"..."}` |
| Storage or filesystem failure | `503` | `{"error":"storage_unavailable"}` |

## 7. Backtest execute: `POST /backtest/execute`

Execute consumes only a frozen directory created by prepare. It never rewrites
`config.json` or `code/signal_engine.py`.

### 7.1 Request

```json
{
  "dispatch_key":"user-1.job-9.1",
  "kind":"backtest",
  "run_id":"mcp-long-demo-1",
  "run_dir":"/home/opencode/.vibe-trading/runs/mcp-long-demo-1",
  "input_digest":"01ff6fbbb4edd179023cdb9f11f63845fc67b73001dd3101e2ecfdc1be55d418",
  "config_sha256":"773fc86715c8599596e27c17352fcb5e3aecb230fc6c2d015827b219f61383fa",
  "source_sha256":"4a42e6521d96837f823ec83fa2bf7e25f27ee03301682251f5704a9dd9441380",
  "build_fingerprint":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
  "prompt":"Run the in-box backtest on this frozen directory:\nbacktest(run_dir='/home/opencode/.vibe-trading/runs/mcp-long-demo-1')\nDo not modify, rewrite, or regenerate config.json or code/signal_engine.py.",
  "contract_version":"mcp-execution-control-v1"
}
```

Binding rules:

- A dispatch row must exist with `kind=backtest`; otherwise `409 prepare_required`.
- `run_id`, `run_dir`, `input_digest`, `config_sha256` and `source_sha256` must
  equal the prepare row exactly. `run_dir` is the exact string returned by
  prepare. `run_id` is its final path segment.
- `build_fingerprint` must be 64 lowercase hex. It is caller supplied; prepare
  does not publish it. VT binds it into the start declaration digest, but it does
  not derive it and it is not part of D-3.
- `prompt` must equal the fixed prompt byte for byte. The only accepted text is
  three lines: `Run the in-box backtest on this frozen directory:`, then
  `backtest(run_dir=<python repr of run_dir>)`, then
  `Do not modify, rewrite, or regenerate config.json or code/signal_engine.py.`
  A different prompt is `400 invalid_request`.
- The frozen inputs are revalidated before a new start: config bytes, source bytes,
  D-3 digest and, when prepare reported `verified`, the local data digest. Drift
  is recorded durably as `failed/input_digest_mismatch` before any start, and the
  request answers `409 {"error":"input_digest_mismatch","original_remote_id":"..."}`.
  A repeated request after that failure returns the same conflict, never an accept.
- `begin_start` writes the full canonical start declaration and its digest before
  any process is spawned. The start row must still be `dispatched` and have no
  prior start digest. An identical start declaration replays the original remote
  id with `replayed=true`; a different one is
  `409 {"error":"idempotency_conflict","original_remote_id":"..."}`.
- Only a new claim spawns `backtest/runner.py <run_dir>` with a 3600 second
  timeout. Spawn failure answers `503 {"error":"worker_start_failed"}` and leaves
  the dispatch nonterminal with no second worker.
- Crash window: if the process dies after `begin_start` but before the worker is
  spawned, a later identical start replays the stored start digest and does not
  spawn again. The row remains nonterminal and reads as `unknown`; RM must inspect
  and stop it rather than blind retry. This is a known recovery boundary, not a
  license to start a duplicate.

### 7.2 Accepted response

The accepted-start body is the same for backtest and factor:

```json
{
  "remote_id":"mcp-long-demo-1",
  "dispatch_key":"user-1.job-9.1",
  "kind":"backtest",
  "status":"running",
  "replayed":false,
  "contract_version":"mcp-execution-control-v1"
}
```

The accepted projection always says `status:"running"`, including a replay of an
already terminal declaration. Current execution state must be read from find or
status. `replayed=true` means the original declaration was returned; it does not
mean the work completed.

`POST /execution-instances` accepts the same union of backtest and factor start
bodies. For a backtest row it preserves the generic start compatibility path,
including an abbreviated legacy declaration. `POST /backtest/execute` is the
strict route: it requires a full prepare declaration and rejects an abbreviated
one with `409 prepare_required`.

## 8. Factor prototype: `POST /factor/prototype`

### 8.1 Request

```json
{
  "dispatch_key":"user-1.job-9.1",
  "kind":"factor",
  "contract_version":"mcp-execution-control-v1",
  "spec":{
    "registry_digest":"d65ae6e5360e6d61462c863d43dfe9e8a733606362c7cc164a93da31531c2a58",
    "candidate_ids":["academic_strev"],
    "symbols":["000001.SZ"],
    "start_date":"2023-01-02",
    "split_date":"2024-01-02",
    "end_date":"2024-12-31",
    "source":"alpha_zoo",
    "as_of":"2025-01-02",
    "forward_return_horizon_days":5,
    "n_groups":5
  }
}
```

Constraints:

- `candidate_ids` is non-empty, unique, at most 200, and each id must exist in the
  selected registry. Unknown, duplicate, blank or over-limit ids are
  `400 invalid_request`.
- `symbols` is non-empty, unique, at most 500. Blank entries are rejected.
- `start_date < split_date <= end_date` and `as_of >= end_date`.
- `forward_return_horizon_days` is a strict integer from 1 to 252. The RM alias is
  accepted on the wire; booleans and strings are rejected.
- `n_groups` is a strict integer from 2 to 20.
- `source` is exactly `alpha_zoo`.
- `registry_digest` is a 64 lowercase hex client declaration. VT does not trust it:
  it recomputes the digest from the default registry and the requested candidate
  ids. A mismatch is `409 input_digest_mismatch`; nothing is claimed or spawned.

### 8.2 Start and replay

The remote id is deterministic:

```text
factor_id = "factor-" + sha256(canonical_json({"dispatch_key": dispatch_key, "spec": spec}))[:32]
```

`canonical_json` is defined in section 10. The run directory is
`<runtime_root>/runs/<factor_id>`. The canonical `spec.json` is written before any
worker starts. The same dispatch key and identical canonical declaration returns
the same remote id and does not spawn again. A changed declaration under the same
key is `409 {"error":"idempotency_conflict","original_remote_id":"..."}`.

The accepted response is the six-field body in section 7.2 with `kind:"factor"`.
For the example, `remote_id` is `factor-817d4e829783d7f27df13621bc7e0aa5`.

The worker command is `python -m src.factors.prototype_worker <run_dir>` with a
3600 second timeout. When the worker finishes it writes `result.json` and, for a
batch that progressed, `progress.json`. The supervisor writes its own
`worker_result.json` terminal record separately.

### 8.3 Factor result

A complete `result.json` has exactly these top-level fields:

```json
{
  "remote_id":"factor-817d4e829783d7f27df13621bc7e0aa5",
  "dispatch_key":"user-1.job-9.1",
  "registry_digest":"d65ae6e5360e6d61462c863d43dfe9e8a733606362c7cc164a93da31531c2a58",
  "tool_schema_version":"alpha-zoo-v1+alpha-bench-v1+factor-analysis-v1",
  "contract_version":"mcp-execution-control-v1",
  "status":"completed",
  "completed":1,
  "total":1,
  "candidates":[{"candidate_id":"academic_strev","status":"completed","reasons":[],"metrics":{},"artifacts":{}}],
  "quality_bounds":{"selection_window":["2023-01-02","2024-01-01"],"oos_window":["2024-01-02","2024-12-31"]},
  "artifacts":{"ic_series":"/home/opencode/.vibe-trading/runs/factor-817d4e829783d7f27df13621bc7e0aa5/artifacts/ic_series.csv","ic_summary":"/home/opencode/.vibe-trading/runs/factor-817d4e829783d7f27df13621bc7e0aa5/artifacts/ic_summary.json","group_equity":"/home/opencode/.vibe-trading/runs/factor-817d4e829783d7f27df13621bc7e0aa5/artifacts/group_equity.csv"},
  "artifact_paths":["/home/opencode/.vibe-trading/runs/factor-817d4e829783d7f27df13621bc7e0aa5/artifacts/ic_series.csv"]
}
```

For a completed run, `candidates` has one row per requested candidate in
declaration order. A partial, cancelled or failed run contains only the
processed candidates. Each row has `candidate_id`, `status` (`completed`,
`skipped`, `insufficient_data`), `reasons`, `metrics` and per-phase `artifacts`. `completed` is exactly the number
of `completed` rows. `total` is the number of requested candidates. Progress is
driven only by real candidate outcomes; sleep, duplicate work or repeated counting
are not valid progress.

`quality_bounds` reports:

- `selection_window`: `[start_date, split_date - 1 day]`.
- `oos_window`: `[split_date, end_date]`.
- `sample_coverage` and `missing_data_ratio`, with aliases `coverage` and
  `missing_ratio`.
- `n_obs` and observation-weighted `group_return_spread`.
- `overfit_caveat`.

Values that cannot be measured are the string `"unavailable"`, never zero-filled.
Run-level coverage and in-sample spread are published only when every candidate
completed and the measurements exist. Partial, cancelled and failed runs keep
aggregate quality unavailable while preserving per-candidate measured outcomes.

`artifacts` has exactly the keys `ic_series`, `ic_summary`, `group_equity`.
`artifact_paths` lists every real retained artifact. The merged files are
`artifacts/ic_series.csv`, `artifacts/ic_summary.json` and
`artifacts/group_equity.csv`; no HTML report is an authority.

The result reader validates the remote id, dispatch key, registry digest,
`tool_schema_version`, `contract_version`, status, counts and artifact key set. An
incomplete or invalid `result.json` falls back to the supervisor
`worker_result.json` terminal record inside the run directory. If neither exists
or can be read, the projection is `unavailable`. A `worker_result.json` outside the
declared run directory is `400 invalid_request`, never a read.

`progress`, when valid, is:

```json
{"completed":1,"total":1,"skipped":0,"insufficient":0,"processed":1}
```

It must equal the result's counts. An inconsistent progress document is returned
as `null`, not repaired.

## 9. Stop: `POST /execution-instances/{remote_id}/stop`

### 9.1 Request

The path `remote_id` and body `remote_id` must be equal. A mismatch is
`400 invalid_request`.

```json
{
  "dispatch_key":"user-1.job-9.1",
  "remote_id":"mcp-long-demo-1",
  "stop_key":"stop-1",
  "deadline_at":"2026-11-10T00:05:00Z",
  "contract_version":"mcp-execution-control-v1"
}
```

`deadline_at` is a required timezone-aware ISO-8601 string. The server stores it
as epoch milliseconds. The same `stop_key` with the same tuple replays the stored
state without a second signal. Reusing a `stop_key` on a different remote or
dispatch is `409 {"error":"idempotency_conflict","original_remote_id":"..."}`.
An unknown remote id is `404 {"error":"not_found"}`. Storage or supervisor faults
are `503 {"error":"storage_unavailable"}`.

### 9.2 Response

```json
{
  "remote_id":"mcp-long-demo-1",
  "dispatch_key":"user-1.job-9.1",
  "stop_key":"stop-1",
  "stop_state":"requested",
  "evidence_kind":null,
  "resolved_at":null,
  "replayed":false,
  "contract_version":"mcp-execution-control-v1"
}
```

`stop_state` is exactly `requested`, `confirmed` or `unknown`. The response is
`202` in all three states. `evidence_kind` is non-null only for `confirmed`.
`resolved_at` is the resolution time or null.

### 9.3 Evidence and monotonicity

The evidence vocabulary is:

| Evidence | Producer | Meaning |
|---|---|---|
| `terminal_record` | Native supervisor | The durable terminal row and its matching `worker_result.json` record exist |
| `process_exit` | Native supervisor | The owned process group exited with code 0 |
| `process_absent` | Native supervisor | The process group is absent after a nonzero exit |
| `ttl_expired` | Accepted vocabulary | Resource TTL expired and the instance can no longer run |
| `explicit_absence` | Accepted vocabulary | An explicit metadata absence lookup proved the instance gone |

The native path emits `terminal_record`, `process_exit` and `process_absent`.
`ttl_expired` and `explicit_absence` are accepted durable evidence classes but are
not emitted by this supervisor.

Rules:

- A stop is persisted as `requested` before any signal.
- `confirmed` requires an allowed evidence class. `terminal_record` is proven by
  the durable terminal row and may carry an empty metadata object; every other
  class needs non-empty proof. `unknown` carries no evidence.
- `unknown` is final. It cannot be upgraded to `confirmed`.
- `confirmed` is final. It cannot be downgraded.
- An overdue `requested` stop resolves atomically to `unknown`; it never retries
  and never invents evidence.
- A new `stop_key` on a dispatch with an earlier confirmed stop returns that
  earlier confirmed evidence with `replayed=true` and does not signal again. The
  response still echoes the caller's `stop_key`.
- `unknown` continues to occupy the RM execution slot. RM must not release the
  slot or start the next queue item on an `unknown` stop.
- After a process restart the supervisor owns no live handle for the old remote
  id. A nonterminal row reads `unknown`, no old pid is signalled, and restart
  reconciliation does not replay or rewrite the row.
- Signals go only to process groups created and still owned by this supervisor,
  and only after `os.getpgid(pid) == pgid` confirms the handle. There is no bare
  pid kill and no signal by a pid read from storage.

## 10. Canonical JSON and digest formulas

### 10.1 Canonical JSON

Canonical JSON means UTF-8 bytes from `json.dumps` with:

- `sort_keys=True`
- `separators=(",", ":")`
- `ensure_ascii=True`
- `allow_nan=False`

Non-finite numbers are rejected. The same value must produce the same bytes on
every host.

### 10.2 D-3 backtest input digest

```text
input_digest = sha256(canonical_json({
  "schema": "backtest-input-v1",
  "config_sha256": config_sha256,
  "source_sha256": source_sha256,
  "data_source": data_source,
  "data_as_of": data_as_of,
  "snapshot_basis": snapshot_basis
}))
```

The canonical document has exactly these six keys. For the section 6 example the
digest is:

```text
01ff6fbbb4edd179023cdb9f11f63845fc67b73001dd3101e2ecfdc1be55d418
```

D-3 deliberately excludes RM's `declared_digest` and `build_fingerprint`. RM's
current prepare digest includes both fields. RM must align its formula with D-3,
or recompute D-3 before comparing, or prepare will report a different
`input_digest` for the same inputs. `build_fingerprint` remains a start-bound
field; it is not part of the input digest.

### 10.3 D-6 selected-registry digest

```text
registry_digest = sha256(canonical_json({
  "schema": "factor-registry-v1",
  "tool_schema_version": "alpha-zoo-v1+alpha-bench-v1+factor-analysis-v1",
  "candidates": [
    {
      "candidate_id": "...",
      "zoo": "...",
      "universe": ["..."],
      "columns_required": ["..."],
      "min_warmup_bars": 0
    }
  ]
}))
```

The candidate array is sorted by `candidate_id`. Within a candidate, `universe`
and `columns_required` keep their registry metadata order. The document has
exactly the five candidate keys shown. The tool schema version is exactly
`alpha-zoo-v1+alpha-bench-v1+factor-analysis-v1`.

For `academic_strev` alone the digest is
`d65ae6e5360e6d61462c863d43dfe9e8a733606362c7cc164a93da31531c2a58`.

The client `registry_digest` is a declaration to verify, not an authority. VT
recomputes it from the live default registry and rejects a mismatch before any
dispatch row or worker is created.

### 10.4 Local data digest

When `data_source` is `local`, VT can compute a snapshot digest without invoking a
loader:

```text
data_digest = sha256(canonical_json({
  "schema": "backtest-local-data-v1",
  "files": [[relative_path, sha256], ...]
}))
```

Only files required by every declared symbol participate. Pairs are sorted by
relative path. Paths are resolved like the local loader and rendered relative to
its `data-bridge` directory. Any missing, unreadable or unenumerable input makes
the whole digest `null` with status `unavailable`. A partially hashed set is never
reported as `verified`.

## 11. Neutral backtest config

The config has exactly these 15 fields:

```text
strategy_id, strategy_version, market, symbols, start_date, end_date,
bar_interval, data_source, data_as_of, snapshot_basis, initial_cash,
currency, commission_bps, tax_bps, slippage_bps
```

Rules:

- Extra fields are rejected. Every field is required.
- `symbols` is non-empty and unique after boundary whitespace normalization.
- `start_date < end_date <= data_as_of`.
- `initial_cash` is a positive finite decimal.
- `commission_bps`, `tax_bps` and `slippage_bps` are finite, non-negative
  decimals. Negative, `NaN` and `Infinity` are rejected.
- `data_source`, `data_as_of` and `snapshot_basis` must also equal their
  top-level prepare duplicates exactly. This duplicate-provenance check is an
  equality check, not a normalization.
- Costs and cash are kept as decimal text in the native execution block. They are
  not converted through binary float in the contract.

Native interval mapping, used when the frozen config is adapted for execution:

| Neutral | Native |
|---|---|
| `1m` | `1m` |
| `5m` | `5m` |
| `15m` | `15m` |
| `30m` | `30m` |
| `1h`, `1H` | `1H` |
| `4h`, `4H` | `4H` |
| `1d`, `1D` | `1D` |
| `1w`, `1W` | `1W` |
| `1M` | `1M` |

Minute `1m` and month `1M` are case sensitive. Prepare validates the neutral
fields and chronology but does not run the native interval adapter. An unsupported
interval is rejected when the worker adapts the frozen config.

### 11.1 Commission, tax and slippage

When the neutral execution cost block exists, the engine uses only the declared
values:

```text
commission = notional * commission_bps / 10000          # on open and on close
tax        = notional * tax_bps / 10000                 # on close only
buy_price  = raw_price * (1 + slippage_bps / 10000)     # direction +1
sell_price = raw_price * (1 - slippage_bps / 10000)     # direction -1
```

There is no minimum commission, no transfer fee, no default stamp tax, no engine
fee multiplier, and no maker, taker or funding default when the neutral block is
present. The all-in cost is exactly commission plus tax on a close.

Worked example: 100 shares at 100.0, `commission_bps=2.50`, `tax_bps=5.00`,
`slippage_bps=20.00`. The buy fills at 100.2 with fee 2.505. The sell fills at
99.8 with fee 7.485. This is the regression asserted by
`agent/tests/test_backtest_execution_config.py`.

Legacy native configs that do not contain a neutral marker keep their existing
engine fee path. The neutral path does not alter that compatibility behavior.

## 12. Horizon and label-end rules

Forward labels use positional trading bars, not calendar days. For a factor row at
position `p` in the close index and horizon `h`, `label_end` is the close index
date at position `p + h`.

A row is unusable when the factor date is absent from the close index or when
`p + h` runs past the last observed bar. The window is never shortened and no
future bar is invented.

- In-sample rows are usable only when `label_end < split_date`.
- Out-of-sample rows are usable only when
  `factor_date >= split_date`, `label_end <= end_date` and `label_end <= as_of`.
- A label that crosses the split boundary is masked to `NaN` in the forward return
  frame and excluded from observations, IC and coverage. It is not filled,
  clamped or counted in either phase.
- If every row is unusable, the phase is `insufficient_data` with a reason. It is
  never reported as a successful measurement.

The parent bench window is the full `[start_date, end_date]`. The label mask is the
single authority for which rows belong to each phase. Phase-local analysis inputs
restrict the coverage denominator to that same phase without rewriting the raw
bench CSVs.

## 13. Legacy strategy runs

The 0001 and 0002 contracts are unchanged. They remain the authority for
strategy-run start, status and cancel.

### 13.1 `POST /strategy-runs`

Body fields: `strategy_id`, `strategy_version`, `run_key` (1..512, stored
verbatim), optional `sandbox_id`, optional `upstream`, optional `timeout_s`
(60..86400, default 3600). At least one of `sandbox_id` or `upstream` is required.

- `202 {"vt_run_id":"..."}` on success.
- `409 {"error":"duplicate_run_key","vt_run_id":"..."}` for a reused `run_key`.
- `422` for model validation.
- `503 {"detail":"entry unavailable"}` when the service cannot be constructed.

### 13.2 `GET /strategy-runs/{vt_run_id}`

The body stays exactly three fields:

```json
{"status":"running","error":null,"error_kind":null}
```

`status` is `running`, `completed`, `failed` or `cancelled`. A failed row always
carries non-empty `error` and `error_kind`. Unknown ids are
`404 {"detail":"unknown vt_run_id"}`.

### 13.3 `POST /strategy-runs/{vt_run_id}/cancel`

No request body. The route returns:

```json
{"vt_run_id":"vt-123","status":"cancelled","stop_state":"confirmed"}
```

`status` is one of `running`, `completed`, `failed`, `cancelled`. `stop_state` is
`confirmed` or `unknown`.

- A terminal row is `confirmed` without a new signal.
- A live code run is signalled only through its same-process handle and becomes
  `cancelled/confirmed` after the durable transition.
- A workflow run, a missing handle or an unobservable process returns
  `running/unknown`. It does not signal a bare pid and does not fabricate a stop.
- Unknown ids are `404 {"detail":"strategy_run_not_found"}`.
- Entry faults are `503 {"detail":"entry unavailable"}`.

### 13.4 `run_key` projection and collisions

Find treats a legacy `run_key` as a dispatch key:

| Tables that contain the key | Projection |
|---|---|
| Neither | `not_dispatched` |
| Legacy only | `dispatched` with `remote_id = vt_run_id`, `run_dir = null`, legacy timestamps and status |
| Native only | Native dispatch projection |
| Both, same remote id | Native dispatch projection |
| Both, different remote ids | `503 {"error":"identity_conflict"}` |

The collision response exposes no remote id or detail. A key that exists only in
the legacy table is not a native instance; it has no prepare row, no frozen
`run_dir` and no result artifact through this contract.

### 13.5 Version precheck boundary

There is no second version-query route. Prepare resolves the strategy directory,
version config and version source directly and returns `strategy_not_found` or
`version_not_found`. This is the 0003 precheck boundary only: it proves the
strategy and version files exist. It does not validate market capability, trading
assumptions, strategy readiness or version semantics. RM should run its own 0003
check before prepare when those facts matter.

## 14. Acceptance map

Local tests cover the shapes below. The gates remain pending; this table is not a
receipt.

| Item | Implemented behavior | Local evidence |
|---|---|---|
| A1 find three states | Not dispatched only on a successful empty read; absent requires terminal status plus confirmed non-`process_exit` evidence; storage faults are 503 | `agent/tests/test_execution_control_service.py`, `test_execution_control_routes.py` |
| A2 idempotent start | Prepare row and start digest are durable before spawn; same declaration replays one remote id; changed declaration is 409 | `test_execution_control_start_stop.py`, `test_backtest_execute.py` |
| A3 status | Five states, stable terminals, unknown for unobservable rows, failed always has `error_kind` | `test_execution_control_routes.py`, `test_execution_control_service.py` |
| A4 stop evidence | Requested before signal; confirmed only with evidence; unknown and confirmed are final; repeated stop is idempotent | `test_execution_control_start_stop.py`, `test_execution_supervisor.py` |
| A5 materialize and readback | Atomic read-only run directory, 15-field config, frozen source, three digests | `test_backtest_prepare.py`, `test_backtest_execute.py` |
| A6 reject mismatch | Config, source, input or data drift rejects before a run; no overwrite | `test_backtest_prepare.py`, `test_backtest_execute.py` |
| A7 frozen execute | Worker consumes only the frozen directory; replay does not recompute; result carries provenance | `test_backtest_execute.py`, `test_backtest_http_contract.py` |
| A8 factor input validation | Empty, duplicate, unknown, over-limit, bad dates and bad bounds are rejected before claim | `test_execution_control_contract.py`, `test_factor_prototype.py` |
| A9 factor output and progress | Completion counts match real rows; reasons are preserved; quality is unavailable when unmeasured; no fabricated progress | `test_factor_prototype.py`, `test_factor_prototype_local_integration.py` |
| A10 auth and version | Token enforced on every new route; write, status, result and stop bodies echo the contract version; find and legacy bodies do not; no token or path is logged | `test_backtest_http_contract.py`, `test_execution_control_routes.py` |
| A11 legacy contracts | `run_key`, 0002 cancel, 0003 precheck and 0012 summary/artifact behavior stay unchanged | `test_strategy_run_routes.py`, `test_strategy_run_cancel.py`, `test_backtest_execute.py` |

A10 is deliberately not "every response echoes the version": find returns the
frozen three-state shape without `contract_version`, and the legacy 0001/0002
bodies stay as they were. RM must not require a version field on those bodies.

## 15. RM integration prerequisites and known mismatches

These are consumer-side alignment items. VT does not edit RM and does not claim
that any of them is resolved.

1. **D-3 versus RM prepare digest.** RM's current `_input_digest` adds
   `declared_digest` and `build_fingerprint` to the canonical document. VT D-3 has
   exactly six keys and excludes both. RM must align or recompute D-3 before it
   compares values, or every prepare result will look different.
2. **`build_fingerprint` is not published by prepare.** The prepare response has
   no build fingerprint. Execute requires a 64-hex `build_fingerprint` supplied by
   RM and binds it into the start digest. RM must produce it from its own image or
   build metadata and must not expect VT to validate it against a prepare field.
3. **No `backtest.execute` receipt enum.** RM's execution capability enum currently
   covers control find/start/status/stop, backtest materialize and factor
   prototype. It has no `backtest.execute` member. The VT route exists, but RM
   cannot issue a receipt naming that capability until the enum is extended. No
   receipt is issued by this document.
4. **No live adapters.** RM currently has protocols and test fakes for
   prepare/find/start/status and separate stop/find seams. It has no concrete live
   HTTP adapter for this contract. `BacktestBackend` does not include a result-read
   method, so result read needs its own adapter method. A live adapter must send
   the fixed prompt through the shared box turn primitive and must not rewrite the
   frozen source.
5. **Carhart warmup fixture drift.** The current VT registry publishes
   `academic_carhart_mom.min_warmup_bars=253`. The RM fixture still uses `252`.
   For the RM calibration pair `academic_bab` and `academic_carhart_mom`, the
   current VT digest is
   `85e98816be958990e2cf73c3695ad81e0e6511a779533c469156dff568ef7fa4`. The same
   pair with `academic_carhart_mom.min_warmup_bars=252` hashes to
   `cc589e39c8a20cb1baca29f3da8cb82fb981d63ad4bc04df0739c47191d226a4`. RM must
   freeze the newly published VT metadata and digest. Reusing the old fixture
   will fail the D-6 recomputation with `409 input_digest_mismatch`.
6. **Dispatch key grammar.** Prepare accepts colon (`:`), while start, stop and
   find reject it. RM should use only `A-Za-z0-9._~-` in keys until VT unifies the
   grammar. Do not depend on the prepare-only colon acceptance.
7. **Factor split boundary.** VT accepts `split_date == end_date` in the factor
   spec. RM 0013 prose says `start_date < split_date < end_date`. RM should send a
   strict split earlier than `end_date` unless the boundary is explicitly aligned.
8. **Find version echo.** Find bodies do not carry `contract_version`. RM must
   treat the contract version as fixed by the entry and must not require the field
   on find. Status, start, stop and result do carry it.
9. **`unknown` is not stopped.** The dedicated entry may report `unknown` for a
   live native dispatch because no cross-request handle provider is registered.
   RM must keep the execution slot, query find and stop, and must not read
   `unknown` as a completed, failed or cancelled run.
10. **Stop requires `deadline_at`.** The 0013 stop example lists only the three
   identity keys. The implemented `StopRequest` also requires a timezone-aware
   `deadline_at` and `contract_version`. The server does not compute a default
   30 second deadline. RM must send the full tuple in section 9.1.
11. **Delivery state.** `verified_by` is `pending` and `verified_at` is `pending`.
   Status is delivered-candidate. G-control, G-backtest and G-factor are all
   `pending`. Human confirmation is required. No end-to-end RM run has been
   performed for this document, and no receipt exists.

## 16. Executor walkthrough

This is the complete prepare, start and result sequence using only this contract.
The base URL is the RM-known entry address; it is not hardcoded here.

```bash
# The token file is already mounted. Read it without printing it.
RM_VT_STRATEGY_RUN_TOKEN="$(cat "$RM_VT_STRATEGY_RUN_TOKEN_FILE")"
AUTH="Authorization: Bearer $RM_VT_STRATEGY_RUN_TOKEN"

# 1. Prepare. Use the exact source text and the sha256 of its UTF-8 bytes.
curl -sS -X POST "$VT_STRATEGY_RUN_BASE/backtest/prepare" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  --data-binary @prepare.json

# 2. Read back the frozen bytes through the file channel and compare sha256
#    with config_sha256 and source_sha256. Reject on any difference.
#    Derive run_id from the final segment of the returned run_dir.

# 3. Execute the prepared binding. The prompt must be the fixed text.
curl -sS -X POST "$VT_STRATEGY_RUN_BASE/backtest/execute" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  --data-binary @start.json

# 4. If the start response was lost, find before retrying.
curl -sS "$VT_STRATEGY_RUN_BASE/execution-instances?dispatch_key=user-1.job-9.1" \
  -H "$AUTH"

# 5. Poll status, then read the bounded result.
curl -sS "$VT_STRATEGY_RUN_BASE/execution-instances/mcp-long-demo-1" -H "$AUTH"
curl -sS "$VT_STRATEGY_RUN_BASE/execution-instances/mcp-long-demo-1/result" -H "$AUTH"
```

The sequence is safe to repeat: prepare replays the same frozen directory, execute
replays the same start declaration and result reads never restart the worker. A
`409` is a terminal answer for that declaration, not a reason to invent a new key.
A stop uses the separate stop route and the tuple in section 9.

## 17. Local verification

Run these from the repository root with the `legonanobot` environment. They do not
require the RM service, a live sandbox or a real token; route tests inject the
service providers.

```bash
# Contract vocabulary, canonical JSON and D-3/D-6 formulas.
python -m pytest agent/tests/test_execution_control_contract.py \
  agent/tests/test_factor_registry_freeze.py -q

# Store, start ordering, supervisor and stop monotonicity.
python -m pytest agent/tests/test_execution_control_store.py \
  agent/tests/test_execution_control_start_stop.py \
  agent/tests/test_execution_start_store.py \
  agent/tests/test_execution_supervisor.py -q

# HTTP auth, exact bodies, fixed errors and read paths.
python -m pytest agent/tests/test_execution_control_routes.py \
  agent/tests/test_backtest_http_contract.py -q

# Prepare, execute, neutral costs and backtest result.
python -m pytest agent/tests/test_backtest_prepare.py \
  agent/tests/test_backtest_execute.py \
  agent/tests/test_backtest_execution_config.py -q

# Factor prototype unit, HTTP and real offline integration.
python -m pytest agent/tests/test_factor_prototype.py \
  agent/tests/test_factor_prototype_local_integration.py -q

# Legacy 0001/0002 behavior.
python -m pytest agent/tests/test_strategy_run_routes.py \
  agent/tests/test_strategy_run_cancel.py -q

# This document's content and link assertions.
python -m pytest tools/test_mcp_execution_control_docs.py -q

# Private-file gate.
python tools/ci_private_files_gate.py
```

The tests use the same service-token path as production. A missing token file in a
route test is a deliberate `403` case. Do not put a real token in a test file, a
command line argument or a log.

## 18. Delivery verification status

The delivery candidate was verified on one locally built, never-pushed image:

| Item | Value |
|---|---|
| Image tag | `opencode-serve:v3.18.0-mcp-execution-control-20261109` |
| Image ID | `sha256:a6a6bbc74f6ad64cf6a7642573e761c12af0b655547b91984132e3fe3a5a8b60` |
| Short image ID | `a6a6bbc74f6a` |
| Platform | `linux/amd64`, offline, no host port, dummy credentials |
| RepoDigest | none, the image was not pushed |
| Built from | `2bbe193f`, image verification scripts `bd2dda81` |

Readiness on that image, with a 300 second budget:

| Check | Result |
|---|---|
| opencode server listening | 5.6s |
| first `/config` 200 after warmup | 24.3s |
| VT MCP connected | 28.7s |
| strategy-run entry listening | 32.0s |
| entry `/health` 200 | 34.9s |

The real HTTP matrix passed A1 through A11 with 365 recorded responses and 378
attempts. The 13 additional attempts are post-restart readiness probes that never
produced an HTTP response; they are not presented as responses. The run used no
mock for the MCP surface or worker processes. It recorded 30 negative responses:
9x400, 8x409, 6x404, 3x503, 2x401 and 2x403. A stop request with no evidence
stayed `requested`, and only the terminal-record stop became `confirmed`.

The full offline regression on this branch reported
`42 failed, 23060 passed, 172 skipped in 39:47`. The 42 FAILED node ids are
byte-identical to the `fork/mymain` baseline set, so the branch adds zero new
failures. The 42 baseline failures are pre-existing and are not claimed as fixed.

Static gates on the changed files: Ruff clean, Black clean except the pre-existing
whole-file drift in `agent/backtest/runner.py` that also fails on `fork/mymain`,
`git diff --check` clean, and the private-file gate green. The contract
documentation tests pass 22 of 22.

Evidence is kept outside Git under
`.omo/evidence/vt-mcp-execution-control/20261109T151921Z/`. The handoff
`vt-owner-handoff.md` indexes the A1 through A11 artifacts, and
`evidence-sha256.txt` is the recomputed manifest that `sha256sum -c` validates.
That local evidence is not an RM end-to-end run.

Accuracy boundaries: `verified_by` is `pending`, `verified_at` is `pending`, and
`delivered-candidate` is the only status claim. Gates G-control, G-backtest and
G-factor remain `pending`. Human confirmation is required. No receipt exists.
