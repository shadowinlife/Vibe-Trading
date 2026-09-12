#!/usr/bin/env python3
"""T7 global-gate runner helpers: three zero-diff assertions + failure-set diff.

The plan's global gate (不放宽):

1. protected zones ``agent/src/{agent,session,providers}/`` — zero diff vs
   the mymain merge-base;
2. ``frontend/`` source — zero diff (build artifacts ``frontend/dist`` and
   ``node_modules`` are gitignored and additionally asserted untracked);
3. ``agent/src/channels/`` — zero diff (whole directory, Oracle note 2).

Plus the pytest failure-set comparison: the full-suite run must produce the
IDENTICAL failure set as the pre-T7 baseline (9 known pre-existing
failures: eastmoney×4, anthropic×3, metrics×1, provider-header×1).

Usage::

    python3 gate_checks.py zero-diff [--base REF]
    python3 gate_checks.py failure-diff --baseline LOG --run LOG
    python3 gate_checks.py all --baseline LOG --run LOG   # both, non-zero exit on any FAIL
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

ZONES = {
    "protected": ["agent/src/agent/", "agent/src/session/", "agent/src/providers/"],
    "frontend": ["frontend/"],
    "channels": ["agent/src/channels/"],
}

FAILED_RE = re.compile(r"^FAILED (\S+?)(?: - .*)?$")


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def merge_base(base_ref: str) -> str:
    try:
        return _git("merge-base", base_ref, "HEAD")
    except subprocess.CalledProcessError:
        return _git("merge-base", f"origin/{base_ref}", "HEAD")


def zero_diff_checks(base_ref: str) -> list[dict]:
    base = merge_base(base_ref)
    checks: list[dict] = []
    for zone_name, paths in ZONES.items():
        committed = _git("diff", "--name-only", f"{base}...HEAD", "--", *paths)
        dirty = _git("status", "--porcelain", "--", *paths)
        checks.append(
            {
                "check": f"zero-diff:{zone_name}",
                "base": base,
                "paths": paths,
                "committed_changes": committed.splitlines(),
                "uncommitted_changes": dirty.splitlines(),
                "pass": not committed and not dirty,
            }
        )
    untracked_dist = _git(
        "status",
        "--porcelain",
        "--ignored=no",
        "--",
        "frontend/dist",
        "frontend/node_modules",
    )
    checks.append(
        {
            "check": "frontend-build-artifacts-untracked",
            "untracked_or_modified": untracked_dist.splitlines(),
            "pass": not untracked_dist,
        }
    )
    return checks


def _failed_set(log_path: Path) -> set[str]:
    failures: set[str] = set()
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = FAILED_RE.match(line.strip())
        if match:
            failures.add(match.group(1))
    return failures


def failure_diff(baseline_log: Path, run_log: Path) -> dict:
    baseline = _failed_set(baseline_log)
    run = _failed_set(run_log)
    return {
        "check": "pytest-failure-set-identical",
        "baseline_failures": sorted(baseline),
        "run_failures": sorted(run),
        "new_failures": sorted(run - baseline),
        "fixed_failures": sorted(baseline - run),
        "pass": baseline == run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    zd = sub.add_parser("zero-diff")
    zd.add_argument("--base", default="mymain")
    zd.add_argument("--json-out", type=Path, default=None)
    fd = sub.add_parser("failure-diff")
    fd.add_argument("--baseline", type=Path, required=True)
    fd.add_argument("--run", type=Path, required=True)
    fd.add_argument("--json-out", type=Path, default=None)
    both = sub.add_parser("all")
    both.add_argument("--base", default="mymain")
    both.add_argument("--baseline", type=Path, required=True)
    both.add_argument("--run", type=Path, required=True)
    both.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    results: list[dict] = []
    if args.command in ("zero-diff", "all"):
        results.extend(zero_diff_checks(args.base))
    if args.command in ("failure-diff", "all"):
        results.append(failure_diff(args.baseline, args.run))

    ok = True
    for entry in results:
        status = "PASS" if entry["pass"] else "FAIL"
        ok = ok and entry["pass"]
        print(f"[{status}] {entry['check']}")
        if not entry["pass"]:
            print(json.dumps(entry, indent=2))
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"[gate] results written to {args.json_out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
