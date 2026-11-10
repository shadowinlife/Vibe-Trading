#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run check_mcp_execution_control.py --image IMAGE --evidence DIR
# 3. Or make executable and run:
#      chmod +x check_mcp_execution_control.py && ./check_mcp_execution_control.py --image IMAGE --evidence DIR
# ──────────────────

"""Run the real offline A1-A11 image verification and record HTTP evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from execution_control_backtest import BacktestEvidence, verify_backtests
from execution_control_common import (
    CONTRACT,
    PASSWORD,
    TOKEN,
    CheckFailure,
    command,
    require,
    sha,
)
from execution_control_factor import FactorEvidence, verify_factor
from execution_control_harness import Harness
from execution_control_legacy import LegacyEvidence, verify_legacy
from execution_control_payloads import setup_state
from execution_control_stop import StopEvidence, verify_confirmed_stop


def verify_fail_closed(h: Harness, invalid_count: int) -> None:
    token = h.state / "auth/service-token"
    saved_token = h.state / "auth/service-token.saved"
    token.rename(saved_token)
    try:
        h.expect(
            h.request(
                "A10",
                "missing-token-file",
                "GET",
                "/execution-instances?dispatch_key=missing",
            ),
            403,
            {"detail": "forbidden"},
        )
    finally:
        saved_token.rename(token)
    db = h.state / "runtime/strategy_runs.sqlite3"
    saved_db = h.state / "runtime/strategy_runs.sqlite3.saved"
    db.rename(saved_db)
    db.mkdir()
    try:
        for label, path in (
            ("find", "/execution-instances?dispatch_key=missing"),
            ("status", "/execution-instances/missing"),
            ("result", "/execution-instances/missing/result"),
        ):
            h.expect(
                h.request("A10", f"storage-{label}", "GET", path),
                503,
                {"error": "storage_unavailable", "contract_version": CONTRACT},
            )
    finally:
        db.rmdir()
        saved_db.rename(db)
    h.expect(h.request("startup", "health-after-failures", "GET", "/health"), 200)
    forbidden = (
        TOKEN,
        PASSWORD,
        "/vt-state/auth",
        "Traceback",
        "sqlite3.OperationalError",
        "SELECT * FROM",
    )
    for record in (h.evidence / "requests.jsonl").read_text().splitlines():
        for needle in forbidden:
            require(
                needle not in record, f"sensitive fragment in HTTP evidence: {needle}"
            )
    logs = h.docker("logs", "--timestamps", h.cid)
    for needle in (TOKEN, PASSWORD, "/vt-state/auth", "Traceback"):
        require(needle not in logs, f"sensitive fragment in container log: {needle}")
    h.save(
        "failure-matrix.json",
        {
            "auth": "401/403 fixed",
            "storage": "503 fixed",
            "unknown_ids": "404 fixed",
            "invalid_factor_cases": invalid_count,
            "drift": ["config", "source", "data"],
            "secret_scan": True,
        },
    )


def finalize(
    h: Harness,
    backtest: BacktestEvidence,
    factor: FactorEvidence,
    stop: StopEvidence,
    legacy: LegacyEvidence,
) -> None:
    failed = h.expect(
        h.request(
            "A3", "failed-status", "GET", "/execution-instances/mcp-long-drift-config-1"
        ),
        200,
    )
    status = {
        "running": legacy.running,
        "completed": backtest.terminal,
        "failed": failed,
        "cancelled": legacy.live_cancelled,
        "unknown": legacy.old_native,
    }
    require(
        {value["status"] for value in status.values()}
        == {"running", "completed", "failed", "cancelled", "unknown"},
        "five-state evidence",
    )
    h.save("status.json", status)
    h.save(
        "find.json",
        {
            "not_dispatched": backtest.not_found,
            "dispatched": backtest.found,
            "absent": stop.absent,
            "legacy": legacy.legacy_find,
        },
    )
    h.save("start.json", {"backtest": backtest.completions, "factor": factor.accepted})
    h.save(
        "stop.json",
        {
            "unknown": stop.first_unknown,
            "confirmed": stop.stopped,
            "restart": legacy.restart_unknown,
            "legacy_unknown": legacy.unknown_cancel,
        },
    )
    h.save("prepare.json", backtest.prepared)
    h.save(
        "regression.json",
        {
            "legacy_status": legacy.legacy_status,
            "legacy_cancel": legacy.cancelled,
            "five_states": sorted(status),
        },
    )
    verify_fail_closed(h, factor.invalid_count)
    for group in (f"A{index}" for index in range(1, 12)):
        h.mark(group)


def write_hashes(evidence: Path) -> None:
    rows = []
    for path in sorted(evidence.rglob("*")):
        if path.is_file() and path.name != "evidence-sha256.txt":
            rows.append(
                f"{sha(path.read_bytes())}  {path.relative_to(evidence).as_posix()}"
            )
    (evidence / "evidence-sha256.txt").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )


def run(image: str, evidence: Path) -> None:
    inspect = command(
        [
            "docker",
            "image",
            "inspect",
            image,
            "--format",
            "{{.Id}} {{.Architecture}} {{.Os}}",
        ]
    )
    require(inspect.returncode == 0, "image missing")
    image_id, architecture, operating_system = inspect.stdout.decode().split()
    require(
        image_id.startswith("sha256:") and len(image_id.removeprefix("sha256:")) == 64,
        "image ID is not 64-hex",
    )
    require(
        architecture == "amd64" and operating_system == "linux",
        f"wrong image platform {operating_system}/{architecture}",
    )
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "docker-images.txt").write_bytes(
        command(["docker", "images", "--digests", "--no-trunc", image]).stdout
    )
    source_path = (
        Path(__file__).resolve().parents[2]
        / "agent/tests/fixtures/execution_control/code/signal_engine.py"
    )
    source = source_path.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix=".vt-state-", dir=evidence) as temporary:
        state = Path(temporary)
        config, long_source = setup_state(state, source)
        h = Harness(image, evidence, state)
        h.save(
            "environment.json",
            {
                "image": image,
                "image_id": image_id.removeprefix("sha256:"),
                "platform": "linux/amd64",
                "network": "none",
                "host_ports": [],
                "credentials": "dummy-only",
                "contract_version": CONTRACT,
            },
        )
        try:
            h.start_container()
            versions = h.docker(
                "exec",
                h.cid,
                "sh",
                "-c",
                "uname -m; opencode --version; /opt/venv/bin/python --version",
            )
            (evidence / "versions.txt").write_text(versions, encoding="utf-8")
            backtest = verify_backtests(h, config, source)
            factor = verify_factor(h)
            stop = verify_confirmed_stop(h, config, long_source)
            legacy = verify_legacy(h, config, source, long_source)
            finalize(h, backtest, factor, stop, legacy)
            responses = len((evidence / "requests.jsonl").read_text().splitlines())
            h.save(
                "summary.json",
                {
                    "result": "PASS",
                    "groups": h.passes,
                    "request_count": responses,
                    "request_attempts": h.sequence,
                    "mocked_mcp": False,
                    "mocked_workers": False,
                },
            )
            print(
                f"PASS: A1-A11 real HTTP verification; responses={responses}; attempts={h.sequence}; evidence={evidence}",
                flush=True,
            )
        finally:
            h.stop_container()
    write_hashes(evidence)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    try:
        run(args.image, args.evidence.resolve())
    except (
        CheckFailure,
        OSError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
