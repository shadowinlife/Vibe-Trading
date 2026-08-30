# harness_bench soak rig

Long-running memory-behaviour measurement for the harness comparison
(harness-evolution plan, todo 3 scaffold; baseline run in todo 8, PoC run in
todo 14).

## Standard workload loop

Both harnesses run the SAME workload definition, `DEFAULT_WORKLOAD` in
`soak.py` (`harness_bench_standard_workload_v1`). One iteration = one
`tools/list` round-trip, one lightweight network-free `tools/call`
(`analyze_options`), one report generation + validation. The definition is
hashed (`workload_sha256`) into every artefact so two soak runs are only
compared when they ran the identical loop.

`duration_hours` is the config knob (4h for the real runs; fractional values
are valid, so the rig is testable at seconds scale, e.g. `0.0005` ≈ 1.8s).
RSS is sampled at a fixed frequency (`sample_interval_seconds`), independent
of iteration cadence: one sample at t≈0, then every interval, plus a final
sample at the end.

## Boundary semantics (read before interpreting any soak number)

- Baseline side = `docker stats` against the opencode container (container
  boundary).
- PoC side = `psutil` against the Python process (process boundary).

The boundary asymmetry must be stated wherever the two soak results are
presented: the container boundary accounts the whole cgroup (opencode runtime
+ plugins + the MCP server process tree), while the process boundary accounts
only the harness process's own RSS. Because the measurement boundaries are
not equivalent, soak numbers from the two sides are REFERENCE INFORMATION
ONLY: todo 8/14 reports present them side by side and declare the boundary
difference; the 4h RSS growth never enters the decision-gate pass formula.

## Failure behaviour

Sampling a non-existent process/container returns a structured error object
(`{"kind": "target_not_found" | ..., "target", "boundary", "message"}`)
recorded under `sample_errors` in the artefact — the rig never raises or
crashes on a missing target. Workload iteration failures are counted in
`workload_errors` and likewise never abort the run.

## Artefact schema — `soak_<label>.json`

| field | meaning |
| --- | --- |
| `soak_version`, `label` | schema version, run label |
| `boundary` | `process` or `container` (see semantics above) |
| `sampler_target` | pid or container name sampled |
| `workload_name`, `workload_sha256` | workload definition identity |
| `duration_hours_requested`, `duration_seconds_actual` | requested vs real duration |
| `sample_interval_seconds` | fixed sampling period |
| `started_at`, `finished_at` | ISO-8601 UTC bounds |
| `iterations_completed`, `workload_errors` | workload loop accounting |
| `rss_timeseries` | `[{t_seconds, rss_mb}]` — successful samples only |
| `sample_errors` | `[{t_seconds, error{kind,target,boundary,message}}]` |
| `growth_mb_per_hour` | least-squares slope of the series (null if <2 samples) |

`validate_soak_artifact()` enforces the schema and names the offending field.

## Usage

```bash
cd agent
# seconds-scale smoke run (mock workload, process boundary)
python -m src.evals.harness_bench.soak --label smoke \
    --duration-hours 0.0005 --sample-interval 0.2 --executor mock
# real MCP workload, process boundary
python -m src.evals.harness_bench.soak --label mcp_smoke \
    --duration-hours 0.001 --sample-interval 1 --executor mcp
# container boundary (baseline-side pattern; requires a running container)
python -m src.evals.harness_bench.soak --label baseline \
    --duration-hours 4 --sample-interval 30 --sampler container \
    --container opencode-serve
```
