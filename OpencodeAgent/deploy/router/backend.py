"""Container control backends for wake-on-inbound and the reclaim truth check.

Local backend = the **docker CLI** via ``asyncio.create_subprocess_exec``. The
``docker`` Python SDK is deliberately NOT used: it is not in ``pyproject.toml``
and the plan forbids new dependencies for the router.

Production backend = ECS, **documented but not implemented** (the plan forbids
ECS API calls against real infrastructure). :class:`EcsBackend` carries the
recipe in its docstring and fails loudly, so a mis-set ``VT_ROUTER_BACKEND``
can never silently pretend to wake anything.

Secrets: the truth check reads opencode serve through ``docker exec`` and lets
the *container's own* ``OPENCODE_SERVER_PASSWORD`` env supply Basic auth, so
the router never holds a tenant credential.
"""

from __future__ import annotations

import asyncio
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
        case "ecs":
            return EcsBackend()
        case unknown:
            raise BackendUnavailable(f"unknown VT_ROUTER_BACKEND={unknown!r}")
