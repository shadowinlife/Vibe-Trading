# harness_bench — harness comparison benchmark suite

Evaluation foundation shared by BOTH harnesses under comparison (the
opencode+OMO baseline and the PydanticAI PoC). Research-only: nothing in this
package places orders or touches product runtime code.

| module / artefact | role |
| --- | --- |
| `parity_spec.json` + `parity.py` | the single parity contract (model mapping, generation params, seeds, budgets) both harnesses assert at runtime |
| `preflight.py` | environment preflight (Docker / HuggingFace / disk / opencode-serve probe) |
| `adapter.py` | `HarnessAdapter` protocol (`setup` / `run_task` / `teardown` / `report`) + deterministic `MockAdapter` reference implementation |
| `report_schema.json` + `report.py` | benchmark report contract + `validate_report()` naming offending fields |
| `manifest.py` + `canonical_tool_manifest.json` | the REAL MCP `tools/list` captured under a pinned env — the ONLY authority for tool-count thresholds |
| `soak.py` + `SOAK_RIG.md` | soak rig: standard workload loop, fixed-frequency RSS sampling, artefact validator (`soak_samplers.py` = process/container samplers, `soak_executors.py` = workload executors) |
| `mcp_spawn.py` | bounded MCP stdio client (smoke-test spawn pattern) shared by manifest capture and soak workload |

## Report shape

`metrics` is a table with one row per (benchmark, metric); every row is a
PERFORMANCE metric (higher is better). `total_cost` is the dedicated cost row:
cost is tracked but is NEVER a row-level performance metric — the decision
gate adjudicates performance rows row-by-row and cost only via the separate
total-cost condition. `skip_markers` discloses degraded benchmarks, and
`provenance` pins the run (harness id, parity_spec sha256, git commit,
timestamp, seeds used).

## Canonical tool manifest

Captured via `python -m src.evals.harness_bench.manifest --emit` (stdio
subprocess, `initialize → tools/list`, pinned env recorded in `env_pin`).
Callability classes: `normal` (well-formed envelope), `credential_gated`
(documented not-available envelope while credentials are absent — still
counts as CALLABLE), `governance_disabled` (disabled by the baseline
governance manifest, e.g. `trading_*`). `--check` re-captures and exits 1 on
drift, naming added/removed tools. Entries carry `schema_sha256` so todos
11/15 can assert surface integrity.

## Degraded-mode convention (skip markers)

A benchmark may be marked degraded ONLY when its environment is not runnable
for a structural reason: Docker missing, HuggingFace unreachable (and no
mirror works), benchmark data download/integrity failure, the opencode-serve
bridge preflight failing, or the run exceeding its parity-spec cost cap.
Choosing a degraded marker is a disclosure duty, never a convenience: the
adapter must record a `skip_markers` entry with the benchmark id, the concrete
`reason`, `degraded: true`, and a `decision`:

- `excluded_from_adjudication` — the benchmark drops out of the gate formula
  entirely (it counts against quorum as a missing two-sided row).
- `one_sided_measurement` — the baseline side is missing but the PoC side is
  still measured alone; the row is disclosed, not adjudicated.

Degraded benchmarks **do not participate in adjudication and are disclosed
explicitly** at the decision gate (todo 15): the gate report lists every skip
marker with its reason before any formula result, and quorum (≥3/5 benchmarks
with valid two-sided rows) is computed after exclusions. A run may not both
skip a benchmark and emit performance rows for it — `validate_report()`
rejects that contradiction.
