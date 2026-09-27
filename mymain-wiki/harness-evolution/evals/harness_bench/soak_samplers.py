"""RSS samplers and shared types for the soak rig.

Boundary semantics (see SOAK_RIG.md): baseline side = ``docker stats``
against the opencode container (container boundary); PoC side = ``psutil``
against the Python process (process boundary). Sampling a NON-EXISTENT target
returns a structured error object — samplers never raise.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Sample:
    """One RSS observation. ``error`` is set (and ``rss_mb`` None) on failure."""

    t_seconds: float
    rss_mb: float | None = None
    error: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class RssSampler(Protocol):
    boundary: str
    target: str

    def sample(self, t_seconds: float) -> Sample:  # pragma: no cover - protocol
        ...


class WorkloadExecutor(Protocol):
    def setup(self) -> None:  # pragma: no cover - protocol
        ...

    def run_iteration(self, index: int) -> None:  # pragma: no cover - protocol
        ...

    def teardown(self) -> None:  # pragma: no cover - protocol
        ...

    @property
    def measured_pid(self) -> int | None:  # pragma: no cover - protocol
        ...


def _error(kind: str, target: str, boundary: str, message: str) -> dict[str, Any]:
    return {"kind": kind, "target": target, "boundary": boundary, "message": message}


class ProcessRssSampler:
    """Process-boundary sampler via ``psutil`` (PoC side)."""

    boundary = "process"

    def __init__(self, pid: int) -> None:
        self.pid = int(pid)
        self.target = str(self.pid)

    def sample(self, t_seconds: float) -> Sample:
        try:
            import psutil
        except ImportError as exc:  # pragma: no cover - psutil is a dev dep
            return Sample(
                t_seconds,
                error=_error(
                    "psutil_unavailable", self.target, self.boundary, str(exc)
                ),
            )
        try:
            rss_mb = psutil.Process(self.pid).memory_info().rss / (1024.0 * 1024.0)
            return Sample(t_seconds, rss_mb=round(rss_mb, 3))
        except psutil.NoSuchProcess:
            return Sample(
                t_seconds,
                error=_error(
                    "target_not_found",
                    self.target,
                    self.boundary,
                    f"no process with pid {self.pid}",
                ),
            )
        except psutil.AccessDenied as exc:
            return Sample(
                t_seconds,
                error=_error("access_denied", self.target, self.boundary, str(exc)),
            )
        except Exception as exc:  # never crash the rig
            return Sample(
                t_seconds,
                error=_error("sample_error", self.target, self.boundary, str(exc)),
            )


_UNIT_TO_MB = {
    "B": 1.0 / (1024.0 * 1024.0),
    "K": 1.0 / 1024.0,
    "KB": 1.0 / 1024.0,
    "KIB": 1.0 / 1024.0,
    "M": 1.0,
    "MB": 1.0,
    "MIB": 1.0,
    "G": 1024.0,
    "GB": 1024.0,
    "GIB": 1024.0,
    "T": 1024.0 * 1024.0,
    "TB": 1024.0 * 1024.0,
    "TIB": 1024.0 * 1024.0,
}

_MEM_RE = re.compile(r"([0-9]*\.?[0-9]+)\s*([A-Za-z]+)")


def parse_mem_usage(text: str) -> float:
    """Parse a ``docker stats`` MemUsage value (e.g. ``12.3MiB / 16GiB``) to MB.

    Only the used portion (before the ``/``) is read. Raises ``ValueError``
    on an unparseable value.
    """
    used = text.split("/", 1)[0].strip()
    match = _MEM_RE.match(used)
    if not match:
        raise ValueError(f"unparseable MemUsage value: {text!r}")
    number = float(match.group(1))
    unit = match.group(2).upper()
    if unit not in _UNIT_TO_MB:
        raise ValueError(f"unknown memory unit {unit!r} in {text!r}")
    return number * _UNIT_TO_MB[unit]


def _looks_missing(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(
        marker in lowered
        for marker in (
            "no such object",
            "could not find",
            "no such container",
            "not found",
        )
    )


class ContainerRssSampler:
    """Container-boundary sampler via ``docker stats`` (baseline side)."""

    boundary = "container"

    def __init__(
        self, container: str, docker_bin: str = "docker", timeout: float = 15.0
    ) -> None:
        self.container = container
        self.target = container
        self.docker_bin = docker_bin
        self.timeout = timeout

    def sample(self, t_seconds: float) -> Sample:
        if shutil.which(self.docker_bin) is None:
            return Sample(
                t_seconds,
                error=_error(
                    "docker_unavailable",
                    self.target,
                    self.boundary,
                    f"'{self.docker_bin}' not found on PATH",
                ),
            )
        cmd = [
            self.docker_bin,
            "stats",
            "--no-stream",
            "--format",
            "{{.MemUsage}}",
            self.container,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout, check=False
            )
        except subprocess.TimeoutExpired:
            return Sample(
                t_seconds,
                error=_error(
                    "docker_timeout",
                    self.target,
                    self.boundary,
                    f"docker stats exceeded {self.timeout}s",
                ),
            )
        except Exception as exc:  # never crash the rig
            return Sample(
                t_seconds,
                error=_error("docker_error", self.target, self.boundary, str(exc)),
            )
        stdout = (proc.stdout or "").strip()
        if proc.returncode != 0 or not stdout:
            detail = (proc.stderr or "").strip() or f"exit {proc.returncode}"
            kind = (
                "target_not_found"
                if _looks_missing(proc.stderr or "")
                else "docker_error"
            )
            return Sample(
                t_seconds, error=_error(kind, self.target, self.boundary, detail)
            )
        try:
            return Sample(t_seconds, rss_mb=round(parse_mem_usage(stdout), 3))
        except ValueError as exc:
            return Sample(
                t_seconds,
                error=_error("parse_error", self.target, self.boundary, str(exc)),
            )


class ExecutorProcessSampler:
    """Process sampler that lazily resolves the pid from a started executor.

    ``run_soak`` starts the executor before the first sample, so the pid is
    available by sampling time without a pre-flight spawn.
    """

    boundary = "process"

    def __init__(self, executor: WorkloadExecutor) -> None:
        self._executor = executor
        self.target = "executor"

    def sample(self, t_seconds: float) -> Sample:
        pid = self._executor.measured_pid
        if pid is None:
            return Sample(
                t_seconds,
                error=_error(
                    "target_not_found",
                    self.target,
                    self.boundary,
                    "executor reports no measured pid",
                ),
            )
        return ProcessRssSampler(pid).sample(t_seconds)
