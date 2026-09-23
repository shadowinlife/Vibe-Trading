"""SDK wrapper: every e2b/kruise call the CLI makes, plus error translation.

Import discipline: ``bootstrap.ensure_patched()`` runs at module top BEFORE
the ``e2b`` import below — this ordering is pinned by tests and must not be
"tidied" away.

Subcommands map 1:1 onto the router's ContainerBackend Protocol
(T16 shell-out contract, backend.py:30-43):
    inspect_state -> state | start -> start | stop -> stop (beta_pause, D11)
    engine_get    -> engine-get
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from sandbox_ctl import bootstrap
from sandbox_ctl.output import CtlError, Exit, diag

bootstrap.ensure_patched()  # MUST precede the e2b import below (pinned test)

import httpx  # noqa: E402
from e2b import Sandbox  # noqa: E402
from e2b.exceptions import (  # noqa: E402
    AuthenticationException,
    InvalidArgumentException,
    NotFoundException,
    RateLimitException,
    SandboxException,
    SandboxNotFoundException,
    TimeoutException,
)

# Policy constants (tunable per call; never credentials/endpoints themselves).
SERVE_PORT = 4096
ROUTE_PROBE_PATH = "/session"
ROUTE_BUDGET_S = 180.0  # T8/T9: fresh-claim route floor 65-98s probabilistic
ROUTE_ATTEMPT_TIMEOUT_S = 8.0  # per-attempt max-time (curl --max-time 8 shape)
ROUTE_POLL_INTERVAL_S = 2.0
DEFAULT_TTL_S = 1800  # sandbox TTL on create / wake (T10+ pattern)
DEFAULT_REQUEST_TIMEOUT_S = 320.0  # > any wait-ready + 30s (STATE.md lesson 2)
ENVD_PROBE_BUDGET_S = 30.0

# serve basic-auth user (T8 pool / T9 matrix), password comes from env/file only.
AUTH_USER = "opencode"
DEFAULT_PASSWORD_FILE = "~/.acs-sandbox/poc-server-password"
PID1_CMD = "cat /proc/1/cmdline | tr '\\0' ' '"
SERVE_MARKER = "opencode serve"

# HTTP data-plane probe for the gateway route (in-pod loopback; the pod's own
# env / injected envs supply the secret — the password never sits in this
# module's string). Image has node 20 but NO curl/python3 (T12 PoC finding).
NODE_FETCH_SCRIPT = (
    'fetch("http://127.0.0.1:{port}__PATH__",'
    '{headers:{Authorization:"Basic "+'
    'Buffer.from("' + AUTH_USER + ':"+process.env.OPENCODE_SERVER_PASSWORD)'
    '.toString("base64")}})'
    ".then(r=>r.text().then(t=>console.log(r.status+\" \"+t)))"
)
NODE_ENV_PROBE = (
    'console.log(process.env.OPENCODE_SERVER_PASSWORD ? "visible" : "invisible")'
)
_STATUS_BODY_RE = re.compile(r"^(\d{3})(?: (.*))?$", re.DOTALL)


def _translate(exc: Exception, context: str) -> CtlError:
    """Map SDK/transport exceptions onto the exit-code contract."""
    if isinstance(exc, AuthenticationException):
        return CtlError(Exit.AUTH_FAILURE, f"{context}: API key rejected: {exc}")
    if isinstance(exc, (SandboxNotFoundException, NotFoundException)):
        return CtlError(Exit.SANDBOX_ABSENT, f"{context}: sandbox not found: {exc}")
    if isinstance(exc, (httpx.TransportError, ConnectionError, OSError)):
        return CtlError(
            Exit.BACKEND_UNAVAILABLE,
            f"{context}: gateway/SDK transport dead: {exc}",
        )
    if isinstance(exc, TimeoutException):
        return CtlError(Exit.OPERATION_FAILED, f"{context}: SDK operation timed out: {exc}")
    if isinstance(exc, RateLimitException):
        return CtlError(Exit.OPERATION_FAILED, f"{context}: rate limited: {exc}")
    if isinstance(exc, (InvalidArgumentException, SandboxException)):
        return CtlError(Exit.OPERATION_FAILED, f"{context}: {exc}")
    return CtlError(Exit.OPERATION_FAILED, f"{context}: unexpected: {type(exc).__name__}: {exc}")


def _call(context: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except CtlError:
        raise
    except Exception as exc:  # noqa: BLE001 — translation boundary
        raise _translate(exc, context) from exc


class SandboxClient:
    """Credential-owning facade over the patched e2b SDK (sync only)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        password_file: Optional[str] = None,
    ):
        try:
            self._api_key = api_key or bootstrap.resolve_api_key()
        except RuntimeError as exc:
            raise CtlError(Exit.AUTH_FAILURE, str(exc)) from exc
        self._password_file = Path(
            password_file
            or os.environ.get("OPENCODE_SERVER_PASSWORD_FILE", DEFAULT_PASSWORD_FILE)
        ).expanduser()
        env_pw = os.environ.get("OPENCODE_SERVER_PASSWORD", "").strip()
        self._env_password = env_pw or None
        self._opts = {"api_key": self._api_key}

    # -- state -------------------------------------------------------------

    def state(self, sandbox_id: str) -> dict[str, Any]:
        """Non-mutating state probe (classmethod get_info — never resumes)."""
        try:
            info = Sandbox.get_info(sandbox_id, **self._opts)
        except (SandboxNotFoundException, NotFoundException):
            return {"state": "absent", "raw": None}
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc, "state") from exc
        return {"state": str(getattr(info.state, "value", info.state)), "raw": _raw_info(info)}

    # -- start (one-step wake, T11 recommendation) --------------------------

    def start(
        self,
        sandbox_id: str,
        ttl_s: int = DEFAULT_TTL_S,
        budget_s: float = ROUTE_BUDGET_S,
    ) -> dict[str, Any]:
        """connect (implicit resume) -> defensive relaunch guard -> route poll.

        T11 verdict: serve survives pause as an in-place checkpoint (and even
        a template-recreated pod execs serve as PID1), so the relaunch branch
        is expected unreachable — kept purely as a guard.
        """
        t0 = time.monotonic()
        sbx = _call("start", Sandbox.connect, sandbox_id, **self._opts)
        connect_s = round(time.monotonic() - t0, 2)

        if ttl_s > 0:
            _call("start", sbx.set_timeout, ttl_s)

        relaunch_fired = False
        pid1 = self._pid1(sbx)
        if pid1 is None:
            diag("start: envd PID1 probe unavailable — skipping relaunch guard")
        elif SERVE_MARKER not in pid1:
            diag(f"start: PID1 is {pid1[:80]!r} — firing defensive relaunch")
            _call(
                "start relaunch",
                sbx.commands.run,
                "nohup opencode serve --hostname 0.0.0.0 --port 4096 >/tmp/serve-relaunch.log 2>&1 &",
                background=True,
                timeout=ENVD_PROBE_BUDGET_S,
            )
            relaunch_fired = True

        route_ready_s, attempts = self.poll_route(sandbox_id, budget_s=budget_s)
        return {
            "started": True,
            "connect_s": connect_s,
            "route_ready_s": route_ready_s,
            "route_attempts": attempts,
            "relaunch_fired": relaunch_fired,
        }

    def _pid1(self, sbx: Any) -> Optional[str]:
        """PID1 cmdline via envd, best-effort within a short budget."""
        deadline = time.monotonic() + ENVD_PROBE_BUDGET_S
        while time.monotonic() < deadline:
            try:
                res = sbx.commands.run(PID1_CMD, timeout=10)
                if res.exit_code == 0:
                    return res.stdout
                return res.stdout or None
            except Exception:  # noqa: BLE001 — envd may still be settling
                time.sleep(1.0)
        return None

    # -- stop (pause-only lifecycle, D11 shape a) ---------------------------

    def stop(self, sandbox_id: str) -> dict[str, Any]:
        """beta_pause(keep_memory=False): filesystem checkpoint per T11/T8 pool."""
        t0 = time.monotonic()
        try:
            paused = Sandbox.beta_pause(sandbox_id, keep_memory=False, **self._opts)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc, "stop") from exc
        pause_api_s = round(time.monotonic() - t0, 2)
        # False == already paused (SDK 409 path) — idempotent success.
        return {"paused": True, "already_paused": not paused, "pause_api_s": pause_api_s}

    # -- set-timeout (TTL keepalive ladder, T18) ------------------------------

    def set_timeout(self, sandbox_id: str, seconds: int) -> dict[str, Any]:
        """Renew the sandbox TTL through a pure control-plane call.

        Uses the SDK's class-method variant (``Sandbox.set_timeout(sid, n)``),
        the same call shape as ``beta_pause``/``kill``: no envd connect is
        performed, so a keepalive aimed at a paused sandbox never wakes it as a
        side effect (``connect`` would). The patched build treats the call as
        an only-if-longer ladder (runbook 7.2): ``now + seconds`` is applied
        only when it exceeds the current deadline — a cron cadence below
        ``seconds`` therefore always ratchets ``end_at`` forward.
        """
        _call("set-timeout", Sandbox.set_timeout, sandbox_id, seconds, **self._opts)
        return {"timeout_set": True, "seconds": seconds}

    # -- engine-get (SDK channel, independent of HTTP route, T9) ------------

    def engine_get(self, sandbox_id: str, path: str) -> dict[str, Any]:
        """GET on in-pod serve via envd commands.run + node fetch (T12: no curl).

        Dual password path with auto-detection (T12: template env NOT visible
        to the envd command shell):
          A "sandbox-env"   — pod/template env already carries the secret;
          B "envs-param"    — read the local password file and pass it via the
                              commands.run envs={} parameter (T3-E1 proven).
        """
        st = self.state(sandbox_id)["state"]
        if st == "absent":
            raise CtlError(Exit.SANDBOX_ABSENT, f"engine-get: sandbox {sandbox_id} absent")
        if st != "running":
            return {"status": None, "body": None, "unavailable": st}

        sbx = _call("engine-get", Sandbox.connect, sandbox_id, **self._opts)
        password, password_path = self._engine_get_password(sbx)
        cmd = "node -e '" + NODE_FETCH_SCRIPT.replace("{port}", str(SERVE_PORT)).replace(
            "__PATH__", path
        ) + "'"
        envs = {"OPENCODE_SERVER_PASSWORD": password} if password_path == "envs-param" else None
        try:
            res = sbx.commands.run(cmd, envs=envs, timeout=20, request_timeout=30)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc, "engine-get") from exc
        if res.exit_code != 0:
            raise CtlError(
                Exit.OPERATION_FAILED,
                "engine-get: in-pod fetch failed: "
                f"exit={res.exit_code} stderr={res.stderr.strip()[:200]!r}",
            )
        m = _STATUS_BODY_RE.match(res.stdout)
        if not m:
            raise CtlError(
                Exit.OPERATION_FAILED,
                f"engine-get: unparsable probe output: {res.stdout[:120]!r}",
            )
        body = (m.group(2) or "")
        if body.endswith("\n"):  # console.log artifact — serve bytes minus the newline
            body = body[:-1]
        return {"status": int(m.group(1)), "body": body, "password_path": password_path}

    def _engine_get_password(self, sbx: Any) -> tuple[str, str]:
        """Auto-detect which password path fires; returns (secret, path_label)."""
        try:
            probe = sbx.commands.run(f"node -e '{NODE_ENV_PROBE}'", timeout=15)
            visible = probe.exit_code == 0 and "visible" in probe.stdout and "invisible" not in probe.stdout
        except Exception:  # noqa: BLE001 — probe failure falls back to path B
            visible = False
        if visible:
            return ("", "sandbox-env")  # the pod's own env is used; we hold nothing
        pw = self._env_password
        if pw is None:
            try:
                pw = self._password_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise CtlError(
                    Exit.AUTH_FAILURE,
                    f"engine-get: serve password not visible in-sandbox and "
                    f"local file unreadable ({self._password_file}): {exc}",
                ) from exc
            if not pw:
                raise CtlError(
                    Exit.AUTH_FAILURE,
                    f"engine-get: local password file is empty: {self._password_file}",
                )
        return (pw, "envs-param")

    # -- HTTP route plane ----------------------------------------------------

    def route_url(self, sandbox_id: str, path: str = ROUTE_PROBE_PATH) -> str:
        return f"http://{bootstrap.gateway_domain()}/kruise/{sandbox_id}/{SERVE_PORT}{path}"

    def poll_route(
        self, sandbox_id: str, budget_s: float = ROUTE_BUDGET_S
    ) -> tuple[float, int]:
        """GET /session until 200 (T8: propagation floor 65-98s probabilistic).

        Raises AUTH_FAILURE immediately on 401/403 (the route is alive but the
        credential is wrong — polling longer cannot fix that).
        """
        password = self._server_password()
        t0 = time.monotonic()
        attempts = 0
        seen: dict[str, int] = {}
        url = self.route_url(sandbox_id)
        with self._route_client((AUTH_USER, password)) as client:
            while True:
                attempts += 1
                try:
                    r = client.get(url)
                    key = str(r.status_code)
                    seen[key] = seen.get(key, 0) + 1
                    if r.status_code == 200:
                        return round(time.monotonic() - t0, 2), attempts
                    if r.status_code in (401, 403):
                        raise CtlError(
                            Exit.AUTH_FAILURE,
                            f"route-wait: gateway returned {r.status_code} — "
                            "serve password rejected by the engine",
                        )
                except CtlError:
                    raise
                except (httpx.HTTPError, OSError) as exc:
                    key = type(exc).__name__
                    seen[key] = seen.get(key, 0) + 1
                elapsed = time.monotonic() - t0
                if elapsed >= budget_s:
                    raise CtlError(
                        Exit.OPERATION_FAILED,
                        f"route not ready after {elapsed:.0f}s "
                        f"({attempts} attempts, saw {seen})",
                    )
                time.sleep(min(ROUTE_POLL_INTERVAL_S, max(0.0, budget_s - elapsed)))

    def _route_client(self, auth: tuple[str, str]) -> httpx.Client:
        return httpx.Client(auth=auth, timeout=ROUTE_ATTEMPT_TIMEOUT_S)

    def _server_password(self) -> str:
        pw = self._env_password
        if pw:
            return pw
        try:
            pw = self._password_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CtlError(
                Exit.AUTH_FAILURE,
                f"no OPENCODE_SERVER_PASSWORD env and password file unreadable "
                f"({self._password_file}): {exc}",
            ) from exc
        if not pw:
            raise CtlError(Exit.AUTH_FAILURE, "server password file is empty")
        return pw

    # -- create / kill ---------------------------------------------------------

    def create(
        self,
        template: str,
        timeout_s: int = DEFAULT_TTL_S,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> dict[str, Any]:
        t0 = time.monotonic()
        sbx = _call(
            "create",
            Sandbox.create,
            template=template,
            timeout=timeout_s,
            request_timeout=request_timeout_s,
            **self._opts,
        )
        return {
            "sandbox_id": sbx.sandbox_id,
            "claim_s": round(time.monotonic() - t0, 2),
        }

    def kill(self, sandbox_id: str) -> dict[str, Any]:
        _call("kill", Sandbox.kill, sandbox_id, **self._opts)
        return {"killed": True}


def _raw_info(info: Any) -> dict[str, Any]:
    """Serialize SandboxInfo to a JSON-safe dict (no credentials inside)."""

    def conv(v: Any) -> Any:
        if hasattr(v, "value"):  # enum
            return v.value
        if hasattr(v, "isoformat"):  # datetime
            return v.isoformat()
        if isinstance(v, (str, int, float, bool, list, dict)) or v is None:
            return v
        return str(v)

    out: dict[str, Any] = {}
    for field in (
        "sandbox_id",
        "template_id",
        "name",
        "state",
        "started_at",
        "end_at",
        "cpu_count",
        "memory_mb",
        "envd_version",
        "metadata",
    ):
        if hasattr(info, field):
            out[field] = conv(getattr(info, field))
    return out
