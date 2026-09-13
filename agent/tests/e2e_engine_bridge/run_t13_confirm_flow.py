#!/usr/bin/env python3
"""T13 confirm-flow live runner: rig up -> E2E suite -> hygiene -> rig down.

One command for the committed acceptance run (plan T13). Wraps the T7 rig
scripts WITHOUT editing them (imports their internals), on the T13-owned
ports/scratch root (14098/18082, /tmp/vt-t13-rig — T8's 14096/18080, T9's
14097/18081 and the parallel T11 rig on 28080-28082 are never touched)::

    python3 agent/tests/e2e_engine_bridge/run_t13_confirm_flow.py
    python3 agent/tests/e2e_engine_bridge/run_t13_confirm_flow.py --cleanup-only

Steps:

1. rig up via ``start_rig.start`` (serve 14098 + gateway 18082, scratch
   VIBE_TRADING_HOME/XDG, production config renderer — the MCP server it
   spawns is THIS worktree's agent/mcp_server.py, so the new
   scheduled_research wrapper is live on the engine's tool surface);
2. ``pytest agent/tests/test_opencode_bridge_im_confirm_flow.py`` with
   ``ENGINE_BRIDGE_E2E=1`` (output tee'd into the evidence dir);
3. engine-session hygiene + cost: every opencode session the suite created is
   read from ``engine-sessions.jsonl``, priced and DELETEd (spike §7h: the
   user's opencode data dir is shared read-as-is), then ``t13-cost.json`` is
   written;
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

DEFAULT_RIG_ROOT = Path("/tmp/vt-t13-rig")
DEFAULT_EVIDENCE = (
    REPO_ROOT / ".omo" / "evidence" / "opencode-engine-bridge-v2" / "t13-wrapper"
)
SERVE_PORT = 14098
GATEWAY_PORT = 18082
TEST_PATH = "agent/tests/test_opencode_bridge_im_confirm_flow.py"


def _run_pytest(evidence: Path, rig_root: Path) -> int:
    env = {
        **os.environ,
        "ENGINE_BRIDGE_E2E": "1",
        "ENGINE_BRIDGE_E2E_RIG_STATE": str(rig_root / "rig_state.json"),
        "ENGINE_BRIDGE_E2E_EVIDENCE": str(evidence),
    }
    cmd = [sys.executable, "-m", "pytest", TEST_PATH, "-q", "-rxXs"]
    log_path = evidence / "pytest-t13-confirm.log"
    print(f"[t13] pytest -> {log_path}")
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


def _cleanup_engine_sessions(
    evidence: Path, rig_root: Path, serve_url: str | None
) -> None:
    rows = EngineSessionRegistry.read(evidence / "engine-sessions.jsonl")
    if not rows:
        print("[t13] no engine sessions recorded — nothing to clean")
        return
    costs: dict[str, float] = {}
    seen: set[str] = set()
    for row in rows:
        engine_id = row["engine_session_id"]
        if engine_id in seen:
            continue
        seen.add(engine_id)
        costs[engine_id] = stop_rig._session_cost(serve_url, engine_id)
        status, _ = stop_rig._req(serve_url, "DELETE", f"/session/{engine_id}")
        print(
            f"[t13] DELETE engine session {engine_id} ({row['scenario']}) -> {status}"
        )
    report = {
        "engine_sessions": sorted(costs),
        "cost_usd_per_session": costs,
        "cost_usd_total": round(sum(costs.values()), 6),
        "scenarios": sorted({row["scenario"] for row in rows}),
        "note": (
            "serve-side model cost of the T13 confirm-flow engine sessions "
            "(assistant message cost fields); sessions DELETEd after costing"
        ),
        "finished_at": time.time(),
    }
    (evidence / "t13-cost.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(
        f"[t13] cost report: total ${report['cost_usd_total']} -> {evidence / 't13-cost.json'}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rig-root", type=Path, default=DEFAULT_RIG_ROOT)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument("--no-purge", action="store_true")
    parser.add_argument("--api-key", default="vt-t13-scratch-key")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="dotenv injected into the gateway env (LANGCHAIN_* for the D11 "
        "auto-title route); default: start_rig auto-detect",
    )
    args = parser.parse_args()

    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    state_path = args.rig_root / "rig_state.json"

    rc = 0
    if not args.cleanup_only:
        if state_path.exists():
            print(f"[t13] reusing running rig at {args.rig_root}")
        else:
            args.rig_root.mkdir(parents=True, exist_ok=True)
            env_file = (
                args.env_file
                if args.env_file is not None
                else start_rig._default_env_file()
            )
            start_rig.start(
                args.rig_root,
                SERVE_PORT,
                GATEWAY_PORT,
                args.api_key,
                env_file,
            )
        rc = _run_pytest(args.evidence_dir, args.rig_root)
        print(f"[t13] pytest exit={rc}")

    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else None
    )
    serve_url = state["serve_url"] if state else f"http://127.0.0.1:{SERVE_PORT}"
    _cleanup_engine_sessions(args.evidence_dir, args.rig_root, serve_url)

    if state is not None:
        for name in ("serve.log", "gateway.log"):
            src = args.rig_root / name
            if src.exists():
                shutil.copy2(src, args.evidence_dir / f"t13-{name}")
        print("[t13] stopping the rig (engine-session sweep already done above)")
        sys.argv = ["stop_rig.py", "--rig-root", str(args.rig_root)]
        stop_rig.main()
        if not args.no_purge:
            shutil.rmtree(args.rig_root, ignore_errors=True)
            print(f"[t13] purged {args.rig_root}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
