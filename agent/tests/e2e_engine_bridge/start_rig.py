#!/usr/bin/env python3
"""Bring up the T7 single-tenant Web-E2E rig (local, NO docker).

Production-isomorphic per ``OpencodeAgent/docs/spike_report.md`` §2/§11:

* ``opencode serve`` (local 1.18.30 binary) on 127.0.0.1:14096
  - scratch ``XDG_CONFIG_HOME`` with the config rendered by the UNMODIFIED
    production renderer (``OpencodeAgent/config/render_config.py``) over a
    local-path copy of ``opencode.json.tmpl`` (MCP = this worktree's
    ``agent/mcp_server.py`` via the current interpreter, OmO plugin ref
    untouched);
  - default data dir (``~/.local/share/opencode``) so provider auth is
    picked up as-is — engine sessions created by the rig are DELETEd by
    ``stop_rig.py`` (spike hygiene);
  - scratch workspace with a trimmed AGENTS.md.
* vt gateway (``agent/api_server.py``) on 127.0.0.1:18080
  - ``VIBE_TRADING_ENGINE=opencode`` + ``OPENCODE_BASE_URL`` -> serve;
  - scratch ``VIBE_TRADING_HOME`` (the user's real ``~/.vibe-trading`` is
    never written; ``ENV_PATH`` still READS ``~/.vibe-trading/.env`` —
    read-only, by design);
  - scratch ``API_AUTH_KEY``;
  - serves ``frontend/dist`` through SPAStaticFiles (build it first:
    ``cd frontend && npm ci && npm run build``).

Usage::

    python3 start_rig.py           # render config, start serve+gateway, wait healthy
    python3 start_rig.py --status  # print the rig state and process liveness

State (PIDs, URLs, key, log paths) lands in ``<rig-root>/rig_state.json``;
``stop_rig.py`` consumes it. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO_ROOT / "agent"
OPENCODE_AGENT_DIR = REPO_ROOT / "OpencodeAgent"

DEFAULT_RIG_ROOT = Path("/tmp/vt-e2e-rig")
DEFAULT_SERVE_PORT = 14096
DEFAULT_GATEWAY_PORT = 18080

SERVE_READY_TIMEOUT_S = 90.0
GATEWAY_READY_TIMEOUT_S = 120.0

AGENTS_MD = """# E2E rig workspace

Scratch workspace for the opencode engine-bridge E2E rig (plan T7).
Follow instructions exactly; do not explore beyond the task.
Do not create todo lists for single-step tasks.
Cost discipline: run each requested tool exactly once; never iterate.
"""


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _http_status(url: str, timeout: float = 3.0) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return None


def _wait_http(url: str, timeout_s: float, label: str, log: Path) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _http_status(url) is not None:
            return
        time.sleep(0.5)
    tail = (
        log.read_text(encoding="utf-8", errors="replace")[-2000:]
        if log.exists()
        else ""
    )
    raise SystemExit(f"{label} did not become ready at {url}\n--- log tail ---\n{tail}")


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv file into a dict (values are never printed)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.replace("export ", "").strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return values


def _default_env_file() -> Path | None:
    for candidate in (
        Path.home() / ".vibe-trading" / ".env",
        AGENT_DIR / ".env",
    ):
        if candidate.exists():
            return candidate
    return None


def _find_opencode_bin() -> str:
    override = os.environ.get("OPENCODE_BIN", "").strip()
    if override:
        return override
    found = shutil.which("opencode")
    if found:
        return found
    candidate = Path.home() / ".opencode" / "bin" / "opencode"
    if candidate.exists():
        return str(candidate)
    raise SystemExit("opencode binary not found (set OPENCODE_BIN)")


def _render_config(rig_root: Path) -> Path:
    """Render the production config with local paths via the production renderer."""
    template_src = OPENCODE_AGENT_DIR / "config" / "opencode.json.tmpl"
    template = json.loads(template_src.read_text(encoding="utf-8"))

    mcp = template["mcp"]
    mcp["search mcp"]["command"] = [
        shutil.which("nano-search-mcp") or "nano-search-mcp",
        "--transport",
        "stdio",
    ]
    vt = mcp["vibe-trading"]
    vt["command"] = [sys.executable, str(AGENT_DIR / "mcp_server.py")]
    vt["env"]["VT_MEMORY_BASE_DIR"] = str(rig_root / "workspace" / ".vt-memory")
    vt["env"]["VIBE_TRADING_HOME"] = str(rig_root / "vt-home")

    config_src = rig_root / "config-src"
    config_src.mkdir(parents=True, exist_ok=True)
    local_tmpl = config_src / "opencode.json.tmpl"
    local_tmpl.write_text(json.dumps(template, indent=2), encoding="utf-8")

    target = rig_root / "xdg" / "opencode" / "opencode.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            str(OPENCODE_AGENT_DIR / "config" / "render_config.py"),
            "--template",
            str(local_tmpl),
            "--manifest",
            str(OPENCODE_AGENT_DIR / "config" / "vibe-trading-tools.json"),
            "--subagents",
            str(OPENCODE_AGENT_DIR / "config" / "subagents.json"),
            "--target",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return target


def _spawn(
    cmd: list[str], cwd: Path, env: dict[str, str], log: Path
) -> subprocess.Popen:
    handle = log.open("wb")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"[rig] spawned pid={proc.pid}: {' '.join(cmd)} (log: {log})")
    return proc


def start(
    rig_root: Path,
    serve_port: int,
    gateway_port: int,
    api_key: str,
    env_file: Path | None,
) -> None:
    if not _port_free(serve_port):
        raise SystemExit(
            f"port {serve_port} is NOT free — refusing to start (user processes?)"
        )
    if not _port_free(gateway_port):
        raise SystemExit(f"port {gateway_port} is NOT free — refusing to start")
    if not (REPO_ROOT / "frontend" / "dist" / "index.html").exists():
        raise SystemExit(
            "frontend/dist missing — run: cd frontend && npm ci && npm run build"
        )

    workspace = rig_root / "workspace"
    vt_home = rig_root / "vt-home"
    xdg = rig_root / "xdg"
    for directory in (workspace, vt_home, xdg):
        directory.mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text(AGENTS_MD, encoding="utf-8")

    config_path = _render_config(rig_root)
    print(f"[rig] rendered config: {config_path}")

    serve_log = rig_root / "serve.log"
    serve_env = {
        **os.environ,
        "XDG_CONFIG_HOME": str(xdg),
        "VIBE_TRADING_HOME": str(vt_home),
    }
    serve = _spawn(
        [
            _find_opencode_bin(),
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(serve_port),
            "--print-logs",
            "--log-level",
            "INFO",
        ],
        cwd=workspace,
        env=serve_env,
        log=serve_log,
    )
    serve_url = f"http://127.0.0.1:{serve_port}"
    _wait_http(f"{serve_url}/app", SERVE_READY_TIMEOUT_S, "opencode serve", serve_log)
    print(f"[rig] serve ready at {serve_url}")

    gateway_log = rig_root / "gateway.log"
    dotenv = _load_env_file(env_file) if env_file is not None else {}
    if dotenv:
        print(f"[rig] gateway dotenv injected from {env_file} ({len(dotenv)} keys)")
    gateway_env = {
        **os.environ,
        **dotenv,
        "VIBE_TRADING_ENGINE": "opencode",
        "OPENCODE_BASE_URL": serve_url,
        "API_AUTH_KEY": api_key,
        "VIBE_TRADING_HOME": str(vt_home),
        "ENABLE_SESSION_RUNTIME": "true",
        "VIBE_TRADING_CHANNELS_AUTO_START": "false",
    }
    # Production launch shape (cli/_legacy.py:258): api_server imported as a
    # MODULE (route registration resolves host symbols via sys.modules), then
    # serve_main() runs uvicorn and mounts the SPA dist.
    gateway = _spawn(
        [
            sys.executable,
            "-c",
            "from api_server import serve_main; import sys as _s; "
            f"_s.exit(serve_main(['--host', '127.0.0.1', '--port', '{gateway_port}']))",
        ],
        cwd=AGENT_DIR,
        env=gateway_env,
        log=gateway_log,
    )
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    _wait_http(
        f"{gateway_url}/health", GATEWAY_READY_TIMEOUT_S, "vt gateway", gateway_log
    )
    print(f"[rig] gateway ready at {gateway_url}")

    state = {
        "serve_pid": serve.pid,
        "gateway_pid": gateway.pid,
        "serve_url": serve_url,
        "gateway_url": gateway_url,
        "api_key": api_key,
        "rig_root": str(rig_root),
        "vt_home": str(vt_home),
        "serve_log": str(serve_log),
        "gateway_log": str(gateway_log),
        "started_at": time.time(),
    }
    (rig_root / "rig_state.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )
    print(f"[rig] state written to {rig_root / 'rig_state.json'}")


def status(rig_root: Path) -> None:
    state_path = rig_root / "rig_state.json"
    if not state_path.exists():
        print("[rig] no rig_state.json — rig is not running")
        return
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for name in ("serve_pid", "gateway_pid"):
        pid = state[name]
        alive = True
        try:
            os.kill(pid, 0)
        except OSError:
            alive = False
        print(f"[rig] {name}={pid} alive={alive}")
    print(json.dumps({k: v for k, v in state.items() if k != "api_key"}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rig-root", type=Path, default=DEFAULT_RIG_ROOT)
    parser.add_argument("--serve-port", type=int, default=DEFAULT_SERVE_PORT)
    parser.add_argument("--gateway-port", type=int, default=DEFAULT_GATEWAY_PORT)
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ENGINE_BRIDGE_E2E_API_KEY", "vt-e2e-scratch-key"),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="dotenv injected into the gateway env (LANGCHAIN_* for the D11 "
        "auto-title route); default: auto-detect ~/.vibe-trading/.env or agent/.env",
    )
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.status:
        status(args.rig_root)
        return 0
    if (args.rig_root / "rig_state.json").exists():
        raise SystemExit("rig_state.json exists — run stop_rig.py first")
    args.rig_root.mkdir(parents=True, exist_ok=True)
    env_file = args.env_file if args.env_file is not None else _default_env_file()
    start(args.rig_root, args.serve_port, args.gateway_port, args.api_key, env_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
