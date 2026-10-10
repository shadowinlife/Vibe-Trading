"""opencode-serve preflight check: drivability probe + container lifecycle.

Starts an ephemeral opencode-serve container when no service is running,
runs the single-task round-trip from ``opencode_probe`` (the protocol todo
4's session bridge reuses), and always tears the container down with a
written receipt. A pre-existing service is probed in place and left as found.

Todo-4 additive extension (used by ``opencode_bridge.py``): the public
``start_ephemeral_container()`` / ``teardown_container()`` pair at the bottom
exposes the same container lifecycle to the session bridge without touching
``check_opencode_serve()`` above. They reuse the private helpers and mirror
its docker-run arguments exactly; ``check_opencode_serve`` is unchanged.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import socket
import tempfile
import time
from pathlib import Path

from src.evals.harness_bench import opencode_probe

PKG_DIR = Path(__file__).resolve().parent

DEFAULT_OPENCODE_URL = "http://localhost:4096"
DEFAULT_OPENCODE_IMAGE_PREFIX = "opencode-serve:"
COMMAND_TIMEOUT = 20.0
CONTAINER_HEALTH_WAIT = 180.0

REMEDIATION = [
    "fix the session bridge (todo 4) and re-run this probe",
    "degrade the baseline side of affected benchmarks: record 'baseline "
    "missing, PoC single-side measurement' at the decision gate",
]


def _run(cmd: list[str], timeout: float) -> tuple[int, str]:
    import subprocess

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or proc.stderr).strip()[:800]
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(cmd)}"


def resolve_dashscope_key(env: dict) -> tuple[str | None, str]:
    """Resolve the DashScope key by env-var name or local opencode auth store.

    Returns (key_or_None, source_description). The key value itself is never
    logged or written anywhere by this package.
    """
    if env.get("DASHSCOPE_API_KEY"):
        return env["DASHSCOPE_API_KEY"], "env:DASHSCOPE_API_KEY"
    auth_path = Path.home() / ".local" / "share" / "opencode" / "auth.json"
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
        key = data.get("alibaba-cn", {}).get("key")
        if isinstance(key, str) and key:
            return key, "opencode auth store (~/.local/share/opencode/auth.json)"
    except (OSError, json.JSONDecodeError):
        pass
    return None, "none"


def find_opencode_image(env: dict) -> str | None:
    explicit = env.get("HARNESS_BENCH_OPENCODE_IMAGE", "").strip()
    if explicit:
        return explicit
    code, out = _run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], COMMAND_TIMEOUT
    )
    if code != 0:
        return None
    candidates = [
        line
        for line in out.splitlines()
        if line.startswith(DEFAULT_OPENCODE_IMAGE_PREFIX) and "-base" not in line
    ]
    return candidates[0] if candidates else None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_health(url: str, password: str, budget_s: float) -> bool:
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        status, _, _ = opencode_probe.http_request(
            f"{url}/health", opencode_probe.BasicAuth(password), 5.0
        )
        if status == 200:
            return True
        time.sleep(5.0)
    return False


def _probe_against(target: str, secret: str, origin: str) -> dict:
    result = opencode_probe.probe_session_round_trip(target, secret)
    ok = result["ok"]
    return {
        "status": "ok" if ok else "failed",
        "measured": {"origin": origin, "url": target, **result},
        "decision": (
            None
            if ok
            else (
                "opencode-serve is reachable but the single-task round-trip "
                "(session -> multi-turn dialog -> tool call -> result retrieval) "
                "failed; baseline-side benchmarks are blocked pending HIL decision."
            )
        ),
        "remediation": [] if ok else list(REMEDIATION),
    }


def _teardown_container(name: str) -> dict:
    rm_code, rm_out = _run(["docker", "rm", "-f", name], COMMAND_TIMEOUT)
    verify_code, verify_out = _run(
        ["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
        COMMAND_TIMEOUT,
    )
    receipt = {
        "container": name,
        "torn_down_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "docker_rm_exit_code": rm_code,
        "docker_rm_output": rm_out[:200],
        "container_still_listed": name in verify_out,
        "verify_exit_code": verify_code,
    }
    (PKG_DIR / "artifacts").mkdir(parents=True, exist_ok=True)
    (PKG_DIR / "artifacts" / "opencode_probe_teardown.json").write_text(
        json.dumps(receipt, indent=2), encoding="utf-8"
    )
    return receipt


def check_opencode_serve(env: dict, docker_ok: bool) -> dict:
    """Preflight check entry point. See module docstring for the protocol."""
    url = env.get("HARNESS_BENCH_OPENCODE_URL", "").strip()
    password = env.get("HARNESS_BENCH_OPENCODE_PASSWORD", "")

    # Case A: a service is already reachable — probe it, leave it as found.
    for candidate in filter(None, [url, DEFAULT_OPENCODE_URL]):
        status, _, _ = opencode_probe.http_request(
            f"{candidate.rstrip('/')}/health",
            opencode_probe.BasicAuth(password) if password else None,
            opencode_probe.DEFAULT_TIMEOUTS["connect"],
        )
        if status in (200, 401):
            if not password:
                return {
                    "status": "degraded",
                    "measured": {"url": candidate, "health_status": status},
                    "decision": (
                        "opencode-serve is running but no password is configured; "
                        "round-trip probe skipped."
                    ),
                    "remediation": [
                        "set HARNESS_BENCH_OPENCODE_PASSWORD and re-run preflight"
                    ],
                }
            found = _probe_against(candidate, password, "pre-existing service")
            found["measured"]["note"] = "service was already running; left as found"
            return found

    if env.get("HARNESS_BENCH_SKIP_OPENCODE") == "1":
        return {
            "status": "degraded",
            "measured": {"skipped": "HARNESS_BENCH_SKIP_OPENCODE=1"},
            "decision": "opencode-serve probe skipped by operator; drivability unknown.",
            "remediation": ["re-run without HARNESS_BENCH_SKIP_OPENCODE"],
        }

    # Case B: start an ephemeral container, probe it, tear it down.
    key, key_source = resolve_dashscope_key(env)
    image = find_opencode_image(env) if docker_ok else None
    if not docker_ok or image is None or key is None:
        reason = (
            "docker unavailable"
            if not docker_ok
            else (
                "no opencode-serve image found locally"
                if image is None
                else "no DashScope API key (env DASHSCOPE_API_KEY or opencode auth store)"
            )
        )
        return {
            "status": "degraded",
            "measured": {
                "cannot_start_because": reason,
                "image": image,
                "key_source": key_source,
            },
            "decision": (
                f"opencode-serve probe could not start a container ({reason}); "
                "baseline drivability is UNKNOWN, not assumed."
            ),
            "remediation": REMEDIATION + ["provide the missing prerequisite above"],
        }

    port = _free_port()
    name = f"hb-preflight-{os.getpid()}"
    probe_password = f"probe-{int(time.time())}"
    started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    # The entrypoint requires writable mounts at /workspace/.vt-memory and the
    # cron state/log dirs (mirrors OpencodeAgent/docker-compose.yml volumes);
    # without them it aborts with a permission error before serving.
    scratch = tempfile.mkdtemp(prefix="hb-preflight-")
    for sub in ("vt-memory", "cron-state", "cron-logs"):
        os.makedirs(os.path.join(scratch, sub), exist_ok=True)
    code, container_id = _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{port}:4096",
            "-v",
            f"{scratch}/vt-memory:/workspace/.vt-memory",
            "-v",
            f"{scratch}/cron-state:/workspace/cron_jobs/state",
            "-v",
            f"{scratch}/cron-logs:/workspace/cron_jobs/logs",
            "-e",
            f"DASHSCOPE_API_KEY={key}",
            "-e",
            f"OPENCODE_SERVER_PASSWORD={probe_password}",
            "-e",
            "TZ=Asia/Shanghai",
            image,
        ],
        COMMAND_TIMEOUT * 3,
    )
    if code != 0:
        return {
            "status": "failed",
            "measured": {"docker_run_exit_code": code, "detail": container_id},
            "decision": "ephemeral opencode-serve container failed to start.",
            "remediation": list(REMEDIATION),
        }
    target = f"http://127.0.0.1:{port}"
    try:
        if not _wait_health(target, probe_password, CONTAINER_HEALTH_WAIT):
            return {
                "status": "failed",
                "measured": {"container": name, "health": "not healthy within budget"},
                "decision": "opencode-serve container started but never became healthy.",
                "remediation": list(REMEDIATION),
            }
        found = _probe_against(target, probe_password, f"ephemeral container {image}")
        return found
    finally:
        receipt = _teardown_container(name)
        receipt["started_at"] = started_at


# --------------------------------------------------------------------------- #
# Todo-4 additive public lifecycle API (consumed by opencode_bridge.py).
# ``check_opencode_serve`` above is intentionally left untouched; these
# functions reuse the same private helpers and identical docker-run arguments.
# --------------------------------------------------------------------------- #


def teardown_container(name: str) -> dict:
    """Public wrapper over the probe-container teardown with written receipt."""
    return _teardown_container(name)


def start_ephemeral_container(env: dict) -> dict:
    """Start an ephemeral opencode-serve container for the session bridge.

    Same prerequisites and docker-run arguments as ``check_opencode_serve``
    (writable vt-memory/cron mounts, DashScope key, generated server
    password). Returns, on success::

        {"ok": True, "container": name, "url": target, "password": str,
         "image": image, "started_at": iso}

    and on failure ``{"ok": False, "reason": str, ...}`` — never raises.
    The CALLER owns teardown via :func:`teardown_container`.
    """
    key, key_source = resolve_dashscope_key(env)
    image = env.get("HARNESS_BENCH_OPENCODE_IMAGE", "").strip() or find_opencode_image(
        env
    )
    if image is None or key is None:
        reason = (
            "no opencode-serve image found locally"
            if image is None
            else "no DashScope API key (env DASHSCOPE_API_KEY or opencode auth store)"
        )
        return {"ok": False, "reason": reason, "key_source": key_source}
    port = _free_port()
    name = f"hb-bridge-{os.getpid()}"
    password = f"bridge-{int(time.time())}"
    started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    scratch = tempfile.mkdtemp(prefix="hb-bridge-")
    for sub in ("vt-memory", "cron-state", "cron-logs"):
        os.makedirs(os.path.join(scratch, sub), exist_ok=True)
    code, detail = _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{port}:4096",
            "-v",
            f"{scratch}/vt-memory:/workspace/.vt-memory",
            "-v",
            f"{scratch}/cron-state:/workspace/cron_jobs/state",
            "-v",
            f"{scratch}/cron-logs:/workspace/cron_jobs/logs",
            "-e",
            f"DASHSCOPE_API_KEY={key}",
            "-e",
            f"OPENCODE_SERVER_PASSWORD={password}",
            "-e",
            "TZ=Asia/Shanghai",
            image,
        ],
        COMMAND_TIMEOUT * 3,
    )
    if code != 0:
        return {"ok": False, "reason": f"docker run failed: {detail[:200]}"}
    target = f"http://127.0.0.1:{port}"
    if not _wait_health(target, password, CONTAINER_HEALTH_WAIT):
        teardown_container(name)
        return {"ok": False, "reason": "container started but never became healthy"}
    return {
        "ok": True,
        "container": name,
        "url": target,
        "password": password,
        "image": image,
        "started_at": started_at,
    }
