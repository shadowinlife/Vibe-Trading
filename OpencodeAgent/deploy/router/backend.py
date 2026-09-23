"""Container control backends for wake-on-inbound and the reclaim truth check.

Local backend = the **docker CLI** via ``asyncio.create_subprocess_exec``. The
``docker`` Python SDK is deliberately NOT used: it is not in ``pyproject.toml``
and the plan forbids new dependencies for the router.

ACS backend = the **``e2b-sandbox-ctl`` CLI** via the same
``asyncio.create_subprocess_exec`` shape: the e2b + kruise-agents SDK lives
entirely in that standalone package (``deploy/sandbox-ctl``, its own venv), so
the router stays zero-new-dependency (decision D7).

Production backend = ECS, **documented but not implemented** (the plan forbids
ECS API calls against real infrastructure). :class:`EcsBackend` carries the
recipe in its docstring and fails loudly, so a mis-set ``VT_ROUTER_BACKEND``
can never silently pretend to wake anything.

Secrets: the truth check reads opencode serve through ``docker exec`` (or, on
ACS, through the CLI's envd channel) and lets the *container's own*
``OPENCODE_SERVER_PASSWORD`` env supply Basic auth, so the router never holds
a tenant credential.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Protocol

from .config import DEFAULT_SERVE_URL


class BackendUnavailable(RuntimeError):
    """The container backend cannot be used (no docker CLI, no such container)."""


class ContainerBackend(Protocol):
    """What the router needs from a container orchestrator."""

    async def inspect_state(self, container: str) -> str | None:
        """Return the container state (``running``/``exited``/...) or ``None``."""

    async def start(self, container: str) -> None:
        """Start a stopped container."""

    async def stop(self, container: str) -> None:
        """Stop a running container (idle reclaim)."""

    async def engine_get(self, container: str, path: str) -> str | None:
        """GET *path* on the container-internal opencode serve; body or ``None``."""


class DockerCliBackend:
    """Local backend shelling out to the docker CLI."""

    __slots__ = ("_docker", "_serve_url", "_timeout_s")

    def __init__(
        self,
        docker_bin: str = "docker",
        serve_url: str = DEFAULT_SERVE_URL,
        timeout_s: float = 15.0,
    ):
        resolved = shutil.which(docker_bin) or docker_bin
        self._docker = resolved
        self._serve_url = serve_url.rstrip("/")
        self._timeout_s = timeout_s

    async def _run(self, *args: str) -> tuple[int, str, str]:
        try:
            process = await asyncio.create_subprocess_exec(
                self._docker,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise BackendUnavailable(f"cannot exec {self._docker}: {exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_s
            )
        except TimeoutError as exc:
            process.kill()
            raise BackendUnavailable(
                f"docker {args[0]} timed out after {self._timeout_s}s"
            ) from exc
        return (
            process.returncode or 0,
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
        )

    async def inspect_state(self, container: str) -> str | None:
        code, out, _ = await self._run("inspect", "-f", "{{.State.Status}}", container)
        return out.strip() if code == 0 else None

    async def start(self, container: str) -> None:
        code, _, err = await self._run("start", container)
        if code != 0:
            raise BackendUnavailable(
                f"docker start {container} failed: {err.strip()[:200]}"
            )

    async def stop(self, container: str) -> None:
        code, _, err = await self._run("stop", container)
        if code != 0:
            raise BackendUnavailable(
                f"docker stop {container} failed: {err.strip()[:200]}"
            )

    async def engine_get(self, container: str, path: str) -> str | None:
        # The container's own env supplies serve's Basic auth; the router never
        # sees the password. `-sf` fails silently on HTTP >= 400 -> empty body.
        script = (
            'curl -sf --max-time 8 ${OPENCODE_SERVER_PASSWORD:+-u "opencode:$OPENCODE_SERVER_PASSWORD"} '
            f'"{self._serve_url}{path}"'
        )
        code, out, _ = await self._run("exec", container, "/bin/sh", "-c", script)
        return out if code == 0 and out.strip() else None


class E2bSandboxBackend:
    """ACS backend shelling out to the standalone ``e2b-sandbox-ctl`` CLI.

    Pure shell-out (decision D7): the e2b/kruise-agents SDK dependencies live
    in the CLI package (``deploy/sandbox-ctl``, installed into its own venv);
    this class only execs the binary and parses its strict-JSON stdout, in
    exactly the :class:`DockerCliBackend` shape. Gate D11 shape (a): runner +
    pause-only lifecycle — ``stop`` = beta_pause (idempotent), ``start`` =
    one-step connect + route-poll inside the CLI. The Protocol has no
    create/kill: provisioning is the caller's job, never the router's.

    Exit-code contract (sandbox-ctl README): ``0`` ok (``state`` answers
    ``absent`` and ``engine-get`` on a paused sandbox are DATA, exit 0);
    ``3`` backend unavailable → :class:`BackendUnavailable`; ``4`` sandbox
    absent; ``5`` operation failed; ``6`` auth failure. Non-zero exits that
    are not ``3`` map onto the same conventions :class:`DockerCliBackend`
    uses for a non-zero docker exit, so no router consumer changes.

    The env read below is deliberately localised here: T16 changes only this
    module (config.py stays the boundary for every *other* knob), and
    ``os.environ`` is stdlib — no new dependency is added to the router.
    """

    __slots__ = (
        "_ctl",
        "_state_timeout_s",
        "_start_timeout_s",
        "_stop_timeout_s",
        "_engine_get_timeout_s",
    )

    #: Stable-venv install path from plan T15 (pip install -e deploy/sandbox-ctl).
    _DEFAULT_CTL = "~/.acs-sandbox/sandbox-ctl-venv/bin/e2b-sandbox-ctl"

    def __init__(
        self,
        ctl_bin: str | None = None,
        *,
        state_timeout_s: float = 30.0,
        start_timeout_s: float = 200.0,
        stop_timeout_s: float = 60.0,
        engine_get_timeout_s: float = 60.0,
    ):
        if ctl_bin is None:
            ctl_bin = os.environ.get("VT_ROUTER_E2B_CTL_BIN", "") or self._DEFAULT_CTL
        self._ctl = os.path.expanduser(ctl_bin)
        # Every budget is bounded (never-timeout is a STATE.md lesson): the
        # outer kill must stay ABOVE the CLI's own internal budget so the
        # CLI's JSON verdict — not a truncated pipe — explains a failure.
        # state: a non-mutating probe, measured ~0.5 s live (T15).
        self._state_timeout_s = state_timeout_s
        # start: the CLI polls the route with its own 180 s budget (fresh-claim
        # propagation band, T8/T9) after a ~0.6 s connect; 200 s leaves margin.
        self._start_timeout_s = start_timeout_s
        # stop: beta_pause measured 3.0 s live (T15); checkpoint latency varies.
        self._stop_timeout_s = stop_timeout_s
        # engine-get: an envd command round-trip, measured 0.9 s live (T15).
        self._engine_get_timeout_s = engine_get_timeout_s

    async def _run(
        self, args: tuple[str, ...], timeout_s: float
    ) -> tuple[int, str, str]:
        try:
            process = await asyncio.create_subprocess_exec(
                self._ctl,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:  # includes FileNotFoundError: no CLI installed
            raise BackendUnavailable(f"cannot exec {self._ctl}: {exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout_s
            )
        except TimeoutError as exc:
            process.kill()
            raise BackendUnavailable(
                f"e2b-sandbox-ctl {args[0]} timed out after {timeout_s}s"
            ) from exc
        return (
            process.returncode or 0,
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
        )

    @staticmethod
    def _parse_json(op: str, out: str, err: str) -> dict:
        """Parse the CLI's stdout; the contract guarantees strict JSON."""
        try:
            doc = json.loads(out)
        except ValueError as exc:
            raise BackendUnavailable(
                f"e2b-sandbox-ctl {op} broke its JSON contract: {err.strip()[:200]}"
            ) from exc
        if not isinstance(doc, dict):
            raise BackendUnavailable(
                f"e2b-sandbox-ctl {op} returned a non-object JSON document"
            )
        return doc

    @staticmethod
    def _describe(op: str, code: int, out: str, err: str) -> str:
        """BackendUnavailable message: prefer the CLI's JSON error envelope."""
        detail = ""
        try:
            doc = json.loads(out)
            detail = str(doc["error"]["message"])
        except (ValueError, KeyError, TypeError):
            detail = (err or out).strip()
        return f"e2b-sandbox-ctl {op} failed (exit {code}): {detail[:200]}"

    async def inspect_state(self, container: str) -> str | None:
        # ``state <sid>`` — exit 0 always carries the truth: running/paused,
        # or {"state": "absent"} for a sandbox the control plane lacks.
        code, out, err = await self._run(("state", container), self._state_timeout_s)
        if code == 3:  # backend unavailable (SDK/gateway dead)
            raise BackendUnavailable(self._describe("state", code, out, err))
        if code != 0:
            # usage/absent/op-failed/auth: mirrors docker's failed-inspect →
            # None; wake answers the fallback page, reclaim skips. Never act
            # on a guessed state.
            return None
        state = self._parse_json("state", out, err).get("state")
        if state == "running":
            return "running"
        if state == "paused":
            # D11 pause-only: a paused sandbox IS the stopped-equivalent —
            # docker reports stopped containers as "exited", and wake treats
            # any non-"running" state as "needs start" while reclaim skips
            # non-running tenants. Map, don't invent a fifth state.
            return "exited"
        return None  # "absent" (and anything outside the CLI contract)

    async def start(self, container: str) -> None:
        # One step: connect (implicit resume) + route poll inside the CLI.
        code, out, err = await self._run(("start", container), self._start_timeout_s)
        if code == 0:
            return
        # DockerCliBackend.start raises BackendUnavailable on ANY non-zero and
        # wake.py turns that into WakeUnavailable → 503 + Retry-After page:
        # exit 4 (sandbox gone) and exit 5 (route budget exhausted) both land
        # on the same client-retry path, so a lost race never 200s emptily.
        raise BackendUnavailable(self._describe("start", code, out, err))

    async def stop(self, container: str) -> None:
        # beta_pause (keep_memory=False, filesystem checkpoint). An
        # already-paused sandbox exits 0 (idempotent), so reclaim retries are
        # safe; auto-pause is the pool's idle policy (gate ⓑ), not our concern.
        code, out, err = await self._run(("stop", container), self._stop_timeout_s)
        if code == 0:
            return
        raise BackendUnavailable(self._describe("stop", code, out, err))

    async def engine_get(self, container: str, path: str) -> str | None:
        code, out, err = await self._run(
            ("engine-get", container, path), self._engine_get_timeout_s
        )
        if code == 3:
            raise BackendUnavailable(self._describe("engine-get", code, out, err))
        if code != 0:
            return None  # mirrors docker's `curl -sf` fail-silent → None
        doc = self._parse_json("engine-get", out, err)
        # A paused sandbox answers exit 0 with {"status": null,
        # "unavailable": "paused"}: reading truth never resumes a sandbox, so
        # the reclaim truth check keeps its fail-closed semantics.
        body = doc.get("body") if doc.get("status") == 200 else None
        return body if isinstance(body, str) and body.strip() else None


class EcsBackend:
    """Production wake path — DOCUMENTED, NOT IMPLEMENTED (plan constraint).

    The ECS equivalent of :class:`DockerCliBackend` for the host-direct/ECS
    deployment form:

    * ``inspect_state`` -> ``ecs:DescribeTasks`` (cluster + task arn from the
      tenant record) or ``DescribeServices`` for a service-per-tenant shape;
      ``STOPPED``/``RUNNING`` map onto ``exited``/``running``.
    * ``start`` -> ``ecs:UpdateService`` with ``desiredCount=1`` (service shape)
      or ``ecs:RunTask`` (task shape), then poll ``DescribeTasks`` until
      ``RUNNING`` + target-group health turns healthy. Scale-to-zero reclaim is
      the inverse (``desiredCount=0``); an ALB target group with a Lambda
      "wake" authorizer is the usual way to make the first inbound request
      trigger the scale-up without exposing the ECS API to the data path.
    * ``engine_get`` -> ``ecs:ExecuteCommand`` (SSM agent, ``enableExecuteCommand``)
      running the same in-container ``curl``; or publish serve on a
      service-connect endpoint and call it over the network instead.

    Deliberately unimplemented here: the plan forbids ECS API calls against real
    infrastructure, and an untested wake path is worse than a loud one.
    """

    __slots__ = ()

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise BackendUnavailable(
            "VT_ROUTER_BACKEND=ecs is documented but not implemented; "
            "use docker-cli locally (see EcsBackend docstring for the recipe)"
        )


def build_backend(
    name: str, *, docker_bin: str = "docker", serve_url: str = DEFAULT_SERVE_URL
) -> ContainerBackend:
    """Construct the configured backend (exhaustive over the known names)."""
    match name:
        case "docker-cli":
            return DockerCliBackend(docker_bin=docker_bin, serve_url=serve_url)
        case "e2b":
            return E2bSandboxBackend()
        case "ecs":
            return EcsBackend()
        case unknown:
            raise BackendUnavailable(f"unknown VT_ROUTER_BACKEND={unknown!r}")
