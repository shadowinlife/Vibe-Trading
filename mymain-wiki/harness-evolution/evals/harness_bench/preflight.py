"""Environment preflight for the harness benchmark suite.

Checks (each bounded, each recorded in ``preflight_report.json``):

  docker        -- daemon availability + version capture
  huggingface   -- reachability of huggingface.co (or HF_ENDPOINT / mirror)
  disk          -- free space vs the suite's data footprint (BacktestBench
                   ~6.5M A-share daily rows plus Docker task images)
  opencode_serve-- single-task drivability round-trip, see opencode_check.py

Exit-code contract: 0 iff every check is ok or carries a recorded
degradation decision; 1 when any check is failed/degraded WITHOUT a
decision; 2 on hard script failure (cannot write the report, bad usage).

Injection seams for tests / failure-path evidence (env vars):
  HARNESS_BENCH_INJECT_FAILURE=<check>  force one check to failed w/o decision
  HARNESS_BENCH_POISON_HOSTS=h1,h2      treat hosts as unreachable (HF path)
  HARNESS_BENCH_SKIP_OPENCODE=1         skip container lifecycle (probe only)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from src.evals.harness_bench import opencode_check, opencode_probe

PKG_DIR = Path(__file__).resolve().parent
REPORT_PATH = PKG_DIR / "preflight_report.json"
SCHEMA_PATH = PKG_DIR / "preflight_report.schema.json"

TIMEOUTS: dict[str, float] = {
    "docker_command": 20.0,
    "hf_probe": 15.0,
    "container_health_wait": opencode_check.CONTAINER_HEALTH_WAIT,
    **opencode_probe.DEFAULT_TIMEOUTS,
}

DISK_REQUIRED_GB = 50.0  # BacktestBench rows (<1GB parquet) + docker images
DISK_HARD_FLOOR_GB = 10.0
HF_DIRECT = "https://huggingface.co"
HF_MIRROR = "https://hf-mirror.com"
HF_PROBE_PATH = "/api/datasets/PatronusAI/financebench"


def _run(cmd: list[str], timeout: float) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or proc.stderr).strip()[:800]
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(cmd)}"


def _http_status(url: str, timeout: float) -> tuple[int, float]:
    started = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "harness-bench-preflight"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, round((time.monotonic() - started) * 1000, 1)
    except urllib.error.HTTPError as exc:
        return exc.code, round((time.monotonic() - started) * 1000, 1)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, round((time.monotonic() - started) * 1000, 1)


def _poisoned(host: str, env: dict) -> bool:
    poisoned = {h.strip() for h in env.get("HARNESS_BENCH_POISON_HOSTS", "").split(",")}
    poisoned.discard("")
    bare = host.split("://")[-1].split("/")[0]
    return host in poisoned or bare in poisoned


def check_docker(env: dict) -> dict:
    code, version = _run(
        ["docker", "info", "--format", "{{.ServerVersion}}"], TIMEOUTS["docker_command"]
    )
    if code == 0 and version:
        return {
            "status": "ok",
            "measured": {"server_version": version, "command": "docker info"},
            "decision": None,
            "remediation": [],
        }
    return {
        "status": "failed",
        "measured": {"docker_info_exit_code": code, "detail": version},
        "decision": (
            "Docker-dependent benchmarks (swebench_verified, terminal_bench_2) are "
            "skip-marked with report-level disclosure until Docker is available."
        ),
        "remediation": [
            "start the Docker daemon (Docker Desktop / colima / systemd unit)",
            "install Docker Engine >= 20.10 and re-run preflight",
        ],
    }


def check_huggingface(env: dict) -> dict:
    candidates: list[tuple[str, str]] = [("direct", HF_DIRECT)]
    env_endpoint = env.get("HF_ENDPOINT", "").strip()
    if env_endpoint and env_endpoint not in (HF_DIRECT, HF_MIRROR):
        candidates.append(("HF_ENDPOINT", env_endpoint))
    candidates.append(("mirror", HF_MIRROR))
    probed: list[dict] = []
    for label, endpoint in candidates:
        if _poisoned(endpoint, env):
            probed.append(
                {
                    "endpoint": endpoint,
                    "label": label,
                    "status": 0,
                    "note": "poisoned by HARNESS_BENCH_POISON_HOSTS",
                }
            )
            continue
        status, latency = _http_status(endpoint + HF_PROBE_PATH, TIMEOUTS["hf_probe"])
        probed.append(
            {
                "endpoint": endpoint,
                "label": label,
                "status": status,
                "latency_ms": latency,
            }
        )
        if status != 0:  # any HTTP answer means the hub API is reachable
            if label == "direct":
                return {
                    "status": "ok",
                    "measured": {"probed": probed, "endpoint_used": endpoint},
                    "decision": None,
                    "remediation": [],
                }
            return {
                "status": "degraded",
                "measured": {"probed": probed, "endpoint_used": endpoint},
                "decision": (
                    f"huggingface.co unreachable; set HF_ENDPOINT={endpoint} for all "
                    "HuggingFace dataset downloads (FinanceBench, FinEval)."
                ),
                "remediation": [
                    f"export HF_ENDPOINT={endpoint} before running HF-backed benchmarks",
                    "restore direct network access to https://huggingface.co",
                ],
            }
    return {
        "status": "failed",
        "measured": {"probed": probed, "endpoint_used": None},
        "decision": (
            "No HuggingFace endpoint reachable (direct, HF_ENDPOINT, mirror). "
            "HF-backed benchmarks (financebench_fineval) are skip-marked with "
            "report-level disclosure."
        ),
        "remediation": [
            "set HF_ENDPOINT to a reachable mirror and re-run preflight",
            "manually download PatronusAI/financebench and SUFE-AIFLM-Lab/FinEval "
            "into the HF cache and point HF_HOME at it",
        ],
    }


def check_disk(env: dict, worktree: Path) -> dict:
    usage = shutil.disk_usage(worktree)
    free_gb = usage.free / 1e9
    measured = {
        "path": str(worktree),
        "free_gb": round(free_gb, 2),
        "required_gb": DISK_REQUIRED_GB,
        "hard_floor_gb": DISK_HARD_FLOOR_GB,
        "basis": (
            "BacktestBench ~6.5M A-share daily rows (<1GB parquet) plus Docker "
            "task images for SWE-bench/terminal-bench and HF dataset cache"
        ),
    }
    if free_gb >= DISK_REQUIRED_GB:
        return {
            "status": "ok",
            "measured": measured,
            "decision": None,
            "remediation": [],
        }
    if free_gb >= DISK_HARD_FLOOR_GB:
        return {
            "status": "degraded",
            "measured": measured,
            "decision": (
                f"Free space {free_gb:.1f}GB is below the {DISK_REQUIRED_GB}GB "
                "requirement: BacktestBench runs subset-sampled and docker image "
                "pruning is advised before the full suite."
            ),
            "remediation": [
                "docker image prune / remove unused benchmark images",
                "free disk space to reach the required threshold",
            ],
        }
    return {
        "status": "failed",
        "measured": measured,
        "decision": (
            f"Free space {free_gb:.1f}GB is below the {DISK_HARD_FLOOR_GB}GB hard "
            "floor: data-heavy benchmarks are skip-marked with disclosure."
        ),
        "remediation": ["free disk space above the hard floor and re-run preflight"],
    }


def run_checks(env: dict | None = None, worktree: Path | None = None) -> dict:
    env = dict(os.environ if env is None else env)
    root = worktree or PKG_DIR.parents[3]
    docker = check_docker(env)
    checks = {
        "docker": docker,
        "huggingface": check_huggingface(env),
        "disk": check_disk(env, root),
        "opencode_serve": opencode_check.check_opencode_serve(
            env, docker["status"] == "ok"
        ),
    }
    injected = env.get("HARNESS_BENCH_INJECT_FAILURE", "").strip()
    if injected in checks:
        checks[injected] = {
            "status": "failed",
            "measured": {"injected_failure": True},
            "decision": None,
            "remediation": [],
        }
    if all(c["status"] == "ok" for c in checks.values()):
        overall = "ok"
    elif any(c["status"] != "ok" and not c["decision"] for c in checks.values()):
        overall = "failed"
    else:
        overall = "degraded"
    return {
        "schema_version": "1.0",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "worktree": str(root),
        },
        "timeouts_seconds": TIMEOUTS,
        "checks": checks,
        "overall": overall,
    }


def exit_code_for(report: dict) -> int:
    for check in report["checks"].values():
        if check["status"] != "ok" and not check["decision"]:
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harness benchmark preflight.")
    parser.add_argument("--output", default=str(REPORT_PATH))
    parser.add_argument("--print", action="store_true", dest="print_report")
    args = parser.parse_args(argv)

    report = run_checks()
    out_path = Path(args.output)
    try:
        import jsonschema

        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(report, schema)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except json.JSONDecodeError as exc:
        print(f"preflight: cannot load schema: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"preflight: cannot write report: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - schema violation = hard failure
        print(f"preflight: report failed schema validation: {exc}", file=sys.stderr)
        return 2
    if args.print_report:
        print(json.dumps(report, indent=2))
    code = exit_code_for(report)
    print(f"preflight: overall={report['overall']} exit={code} report={out_path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
