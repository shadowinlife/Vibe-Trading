"""Pre-spawn frozen-input drift and chronology refusal scenarios."""

from __future__ import annotations

from execution_control_common import CONTRACT, JsonValue, require
from execution_control_harness import Harness
from execution_control_payloads import execute, prepare, prepare_payload


def verify_drifts(
    h: Harness,
    config: dict[str, JsonValue],
    source: str,
    run_id: str,
    outer: dict[str, JsonValue],
) -> None:
    for drift in ("config", "source", "data"):
        key, drift_id = f"drift.{drift}", f"mcp-long-drift-{drift}-1"
        prepared = prepare(h, "A6", key, drift_id, config, source)
        if drift == "data":
            h.container_python(
                "from pathlib import Path\n"
                "with Path('/vt-state/data/000001.SZ.csv').open('ab') as stream:\n"
                "    stream.write(b'# isolated drift\\n')\n"
            )
        else:
            relative = "config.json" if drift == "config" else "code/signal_engine.py"
            h.container_python(
                "from pathlib import Path\np=Path('/vt-state/runtime/runs/%s/%s')\np.chmod(0o600)\np.write_bytes(b'tampered')\n"
                % (drift_id, relative)
            )
        h.expect(
            execute(h, "A6", key, drift_id, prepared),
            409,
            {
                "error": "input_digest_mismatch",
                "original_remote_id": drift_id,
                "contract_version": CONTRACT,
            },
        )
        row = h.dispatch(key)
        require(
            row is not None
            and row["status"] == "failed"
            and row["error_kind"] == "input_digest_mismatch",
            "drift not durable",
        )
        require(
            row["worker_pid"] is None
            and not (h.state / "runtime/runs" / drift_id / "worker.log").exists(),
            "drift spawned",
        )
        if drift == "data":
            h.container_python(
                "from pathlib import Path\n"
                "Path('/vt-state/data/000001.SZ.csv').write_bytes("
                "Path('/fixtures/execution_control/000001.SZ.csv').read_bytes())\n"
            )
    require(
        h.expect(
            h.request(
                "A6",
                "original-result-intact",
                "GET",
                f"/execution-instances/{run_id}/result",
            ),
            200,
        )
        == outer,
        "valid result changed",
    )
    bad = config | {"data_as_of": "2026-01-08"}
    h.expect(
        h.request(
            "A6",
            "chronology-refusal",
            "POST",
            "/backtest/prepare",
            prepare_payload("bad.chronology", "mcp-long-chronology-1", bad, source),
        ),
        400,
        {"error": "invalid_input", "contract_version": CONTRACT},
    )
    require(
        h.dispatch("bad.chronology") is None
        and not (h.state / "runtime/runs/mcp-long-chronology-1").exists(),
        "chronology side effect",
    )
