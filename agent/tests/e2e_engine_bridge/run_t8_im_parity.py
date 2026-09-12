#!/usr/bin/env python3
"""T8 IM-parity live runner: rig up -> parity suite -> hygiene -> rig down.

One command for the committed acceptance run (plan T8). Wraps the T7 rig
scripts WITHOUT editing them (imports their internals), on the T8-owned
ports/scratch root (14096/18080, /tmp/vt-t8-rig — the parallel T9 rig on
14097/18081 is never touched)::

    python3 agent/tests/e2e_engine_bridge/run_t8_im_parity.py
    python3 agent/tests/e2e_engine_bridge/run_t8_im_parity.py --death-observe-s 90
    python3 agent/tests/e2e_engine_bridge/run_t8_im_parity.py --cleanup-only

Steps:

1. rig up via ``start_rig.start`` (serve 14096 + gateway 18080, scratch
   VIBE_TRADING_HOME/XDG, production config renderer, frontend dist check);
2. ``pytest agent/tests/test_opencode_bridge_im_parity.py`` with
   ``ENGINE_BRIDGE_E2E=1`` and the T8 evidence/observation env (output tee'd
   into the evidence dir);
3. engine-session hygiene + cost: every opencode session the suite created is
   read from ``engine-sessions.jsonl``; scenario 2 KILLS the serve, so a
   transient serve is respawned on 14096 (same scratch XDG) purely to price
   and DELETE those sessions (spike §7h: the user's opencode data dir is
   shared read-as-is), then ``t8-cost.json`` is written;
4. serve/gateway logs copied to the evidence dir; rig down via ``stop_rig``
   (``--purge`` passthrough).

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / "agent"))

from tests.e2e_engine_bridge import start_rig, stop_rig  # noqa: E402
from tests.e2e_engine_bridge.imlib import EngineSessionRegistry  # noqa: E402

DEFAULT_RIG_ROOT = Path("/tmp/vt-t8-rig")
DEFAULT_EVIDENCE = (
    REPO_ROOT / ".omo" / "evidence" / "opencode-engine-bridge-v2" / "t8-im"
)
SERVE_PORT = 14096
GATEWAY_PORT = 18080


def _run_pytest(evidence: Path, rig_root: Path, death_observe_s: float) -> int:
    env = {
        **os.environ,
        "ENGINE_BRIDGE_E2E": "1",
        "ENGINE_BRIDGE_E2E_RIG_STATE": str(rig_root / "rig_state.json"),
        "ENGINE_BRIDGE_E2E_EVIDENCE": str(evidence),
        "T8_DEATH_OBSERVE_S": str(death_observe_s),
    }
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "agent/tests/test_opencode_bridge_im_parity.py",
        "-q",
        "--durations=10",
        "-rxXs",
    ]
    log_path = evidence / "pytest-t8.log"
    print(f"[t8] pytest -> {log_path}")
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert proc.stdout is not None
        for chunk in proc.stdout:
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            log.write(chunk)
        proc.wait()
    return proc.returncode or 0


def _spawn_transient_serve(rig_root: Path) -> tuple[subprocess.Popen, str]:
    """Respawn a serve on 14096 (same scratch XDG) for session cleanup only."""
    env = {
        **os.environ,
        "XDG_CONFIG_HOME": str(rig_root / "xdg"),
        "VIBE_TRADING_HOME": str(rig_root / "vt-home"),
    }
    log = rig_root / "serve-cleanup.log"
    proc = start_rig._spawn(
        [
            start_rig._find_opencode_bin(),
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(SERVE_PORT),
            "--print-logs",
            "--log-level",
            "INFO",
        ],
        cwd=rig_root / "workspace",
        env=env,
        log=log,
    )
    url = f"http://127.0.0.1:{SERVE_PORT}"
    start_rig._wait_http(f"{url}/app", 90.0, "transient cleanup serve", log)
    return proc, url


def _cleanup_engine_sessions(
    evidence: Path, rig_root: Path, serve_url: str | None
) -> None:
    rows = EngineSessionRegistry.read(evidence / "engine-sessions.jsonl")
    if not rows:
        print("[t8] no engine sessions recorded — nothing to clean")
        return
    proc = None
    reachable = serve_url is not None and stop_rig._req(serve_url, "GET", "/app")[0]
    if not reachable:
        print("[t8] serve is down (scenario-2 kill) — respawning for cleanup")
        proc, serve_url = _spawn_transient_serve(rig_root)
    costs: dict[str, float] = {}
    try:
        seen: set[str] = set()
        for row in rows:
            engine_id = row["engine_session_id"]
            if engine_id in seen:
                continue
            seen.add(engine_id)
            costs[engine_id] = stop_rig._session_cost(serve_url, engine_id)
            status, _ = stop_rig._req(serve_url, "DELETE", f"/session/{engine_id}")
            print(
                f"[t8] DELETE engine session {engine_id} ({row['scenario']}) -> {status}"
            )
    finally:
        if proc is not None:
            stop_rig._kill(proc.pid, "transient cleanup serve")
    report = {
        "engine_sessions": sorted(costs),
        "cost_usd_per_session": costs,
        "cost_usd_total": round(sum(costs.values()), 6),
        "scenarios": sorted({row["scenario"] for row in rows}),
        "note": (
            "serve-side model cost of the T8 IM-parity engine sessions "
            "(assistant message cost fields); sessions DELETEd after costing"
        ),
        "finished_at": time.time(),
    }
    (evidence / "t8-cost.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(
        f"[t8] cost report: total ${report['cost_usd_total']} -> {evidence / 't8-cost.json'}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rig-root", type=Path, default=DEFAULT_RIG_ROOT)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument(
        "--death-observe-s",
        type=float,
        default=660.0,
        help="scenario-2 observation window; 660 captures the full 600s "
        "polling-budget landing (default for the evidence run)",
    )
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument("--no-purge", action="store_true")
    parser.add_argument("--api-key", default="vt-t8-scratch-key")
    args = parser.parse_args()

    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    state_path = args.rig_root / "rig_state.json"

    rc = 0
    if not args.cleanup_only:
        if state_path.exists():
            print(f"[t8] reusing running rig at {args.rig_root}")
        else:
            args.rig_root.mkdir(parents=True, exist_ok=True)
            start_rig.start(
                args.rig_root,
                SERVE_PORT,
                GATEWAY_PORT,
                args.api_key,
                start_rig._default_env_file(),
            )
        rc = _run_pytest(args.evidence_dir, args.rig_root, args.death_observe_s)
        print(f"[t8] pytest exit={rc}")

    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else None
    )
    serve_url = state["serve_url"] if state else f"http://127.0.0.1:{SERVE_PORT}"
    _cleanup_engine_sessions(args.evidence_dir, args.rig_root, serve_url)

    if state is not None:
        for name in ("serve.log", "gateway.log", "serve-cleanup.log"):
            src = args.rig_root / name
            if src.exists():
                shutil.copy2(src, args.evidence_dir / f"t8-{name}")
        print("[t8] stopping the rig (engine-session sweep already done above)")
        sys.argv = ["stop_rig.py", "--rig-root", str(args.rig_root)]
        stop_rig.main()
        if not args.no_purge:
            shutil.rmtree(args.rig_root, ignore_errors=True)
            print(f"[t8] purged {args.rig_root}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
