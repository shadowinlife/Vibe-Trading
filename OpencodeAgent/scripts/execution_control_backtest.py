"""Live backtest materialization, execution, drift and stop scenarios."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from execution_control_drift import verify_drifts
from execution_control_common import (
    CONTRACT,
    CSVS,
    RESULT_KEYS,
    SUMMARY_KEYS,
    JsonValue,
    canonical,
    mapping,
    require,
    sha,
)
from execution_control_harness import Harness
from execution_control_payloads import execute, prepare, prepare_payload, start_payload


@dataclass(frozen=True, slots=True)
class BacktestEvidence:
    not_found: dict[str, JsonValue]
    found: dict[str, JsonValue]
    prepared: dict[str, JsonValue]
    completions: list[dict[str, JsonValue]]
    terminal: dict[str, JsonValue]
    outer: dict[str, JsonValue]


def verify_backtests(
    h: Harness, config: dict[str, JsonValue], source: str
) -> BacktestEvidence:
    key, run_id = "happy.backtest", "mcp-long-happy-1"
    not_found = h.expect(
        h.request(
            "A3", "not-dispatched", "GET", f"/execution-instances?dispatch_key={key}"
        ),
        200,
    )
    require(
        not_found
        == {
            "state": "not_dispatched",
            "dispatch_key": key,
            "contract_version": CONTRACT,
        },
        "not_dispatched shape",
    )
    prepared = prepare(h, "A5", key, run_id, config, source)
    require(
        prepare(h, "A1", key, run_id, config, source) == prepared,
        "prepare replay changed identity/digests",
    )
    frozen_before = h.file_state(run_id)
    prepare(h, "A5", key, run_id, config, source)
    require(
        h.file_state(run_id) == frozen_before, "prepare replay rewrote bytes/mtimes"
    )
    frozen_config = mapping(
        json.loads((h.state / "runtime/runs" / run_id / "config.json").read_text())
    )
    require(
        set(frozen_config) == set(config) and len(frozen_config) == 15,
        "not the exact 15-field config",
    )
    require(
        (h.state / "runtime/runs" / run_id / "code/signal_engine.py").read_text()
        == source,
        "source drift",
    )
    run_root = h.state / "runtime/runs"
    modes = {
        relative: oct((run_root / run_id / relative).stat().st_mode & 0o777)
        for relative in ("config.json", "code/signal_engine.py")
    }
    staging = [path.name for path in run_root.glob(".*.staging")]
    require(
        set(modes.values()) == {"0o444"} and not staging,
        "run_dir is not atomic/read-only",
    )
    h.save(
        "a5-materialization.json",
        {"files": frozen_before, "modes": modes, "staging": staging},
    )
    config_sha = sha(canonical(config))
    require(
        prepared["config_sha256"] == config_sha
        and prepared["source_sha256"] == sha(source.encode()),
        "digest mismatch",
    )
    expected_input = sha(
        canonical(
            {
                "schema": "backtest-input-v1",
                "config_sha256": config_sha,
                "source_sha256": prepared["source_sha256"],
                "data_source": "local",
                "data_as_of": "2026-01-09",
                "snapshot_basis": "sanitized-local-csv-v1",
            }
        )
    )
    require(
        prepared["input_digest"] == expected_input
        and prepared["data_digest_status"] == "verified",
        "D-3/data proof",
    )
    changed_prepare = prepare_payload(
        key, run_id, config | {"commission_bps": "9.00"}, source
    )
    h.expect(
        h.request(
            "A1", "prepare-conflict", "POST", "/backtest/prepare", changed_prepare
        ),
        409,
        {
            "error": "idempotency_conflict",
            "remote_id": run_id,
            "contract_version": CONTRACT,
        },
    )
    unknown = h.expect(
        h.request("A3", "prepared-unknown", "GET", f"/execution-instances/{run_id}"),
        200,
    )
    require(
        unknown["status"] == "unknown" and unknown["contract_version"] == CONTRACT,
        "prepared must be unknown",
    )
    found = h.expect(
        h.request(
            "A3", "dispatched", "GET", f"/execution-instances?dispatch_key={key}"
        ),
        200,
    )
    require(
        found["state"] == "dispatched"
        and found["remote_id"] == run_id
        and found["status"] == "unknown",
        "dispatched shape",
    )

    start = start_payload(key, run_id, prepared)
    completions: list[dict[str, JsonValue]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(
                h.request,
                "A4",
                f"concurrent-{index}",
                "POST",
                "/backtest/execute",
                start,
            ): index
            for index in range(6)
        }
        for future in as_completed(futures):
            accepted = h.expect(future.result(), 202)
            completions.append(
                {
                    "request_index": futures[future],
                    "remote_id": accepted["remote_id"],
                    "replayed": accepted["replayed"],
                }
            )
    require(
        {item["remote_id"] for item in completions} == {run_id},
        "concurrent remote ids differ",
    )
    require(
        sum(item["replayed"] is False for item in completions) == 1,
        "not exactly one new start",
    )
    terminal = h.wait_terminal("A7", run_id)
    require(terminal["status"] == "completed", f"backtest failed: {terminal}")
    outer = h.expect(
        h.request(
            "A7", "backtest-result", "GET", f"/execution-instances/{run_id}/result"
        ),
        200,
    )
    require(
        set(outer) == RESULT_KEYS
        and outer["result_status"] == "available"
        and outer["has_more"] is False,
        "result envelope",
    )
    result = mapping(outer["result"])
    for digest in ("input_digest", "config_sha256", "source_sha256", "data_digest"):
        require(result[digest] == prepared[digest], f"result provenance {digest}")
    summary = mapping(result["summary"])
    require(
        summary["schema_version"] == "1.0" and SUMMARY_KEYS <= set(summary),
        "summary 1.0 fields",
    )
    artifacts = {mapping(item)["path"] for item in result["artifacts"]}
    require(CSVS <= artifacts, "four CSV manifest entries missing")
    for relative in CSVS:
        require(
            (h.state / "runtime/runs" / run_id / relative).stat().st_size > 0,
            f"empty {relative}",
        )
    h.save("backtest-result.json", outer)
    before_replay = h.file_state(run_id)
    replay = h.expect(execute(h, "A1", key, run_id, prepared), 202)
    require(
        replay["remote_id"] == run_id and replay["replayed"] is True,
        "execute replay identity",
    )
    require(
        h.file_state(run_id) == before_replay, "execute replay recomputed/rewrote files"
    )
    engine_markers = (
        (h.state / "runtime/runs" / run_id / "worker.log")
        .read_text()
        .count("VT_LOCAL_ENGINE_GENERATE")
    )
    require(engine_markers == 1, "engine ran twice")
    h.save(
        "a4-ordered.json",
        {
            "completion_order": completions,
            "worker": h.dispatch(key),
            "engine_marker_count": engine_markers,
        },
    )
    changed_start = start_payload(key, run_id, prepared) | {
        "build_fingerprint": "e" * 64
    }
    h.expect(
        h.request("A1", "execute-conflict", "POST", "/backtest/execute", changed_start),
        409,
        {
            "error": "idempotency_conflict",
            "original_remote_id": run_id,
            "contract_version": CONTRACT,
        },
    )

    verify_drifts(h, config, source, run_id, outer)

    return BacktestEvidence(not_found, found, prepared, completions, terminal, outer)
