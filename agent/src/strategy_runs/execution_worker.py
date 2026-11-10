"""Server-owned ``WorkerSpec`` factories for the native execution supervisor.

No caller supplies a command, a shell string, a cwd, or an output path: every
field is derived here from the agent root and a validated run directory.
Validation is pure — this module never spawns a process.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from src.tools.path_utils import safe_run_dir

__all__ = [
    "WorkerSpec",
    "WorkerSpecError",
    "backtest_worker_spec",
    "factor_worker_spec",
]

_KINDS: Final = ("backtest", "factor")
_MAX_TIMEOUT_S: Final = 86_400
_SHELL_FLAGS: Final = frozenset({"-c", "-s", "/c", "/k"})
_SHELL_NAMES: Final = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "dash",
        "ksh",
        "csh",
        "tcsh",
        "cmd",
        "cmd.exe",
        "powershell",
        "pwsh",
    }
)
_AGENT_ROOT: Final = Path(__file__).resolve().parents[2]
_RESULT_FILENAME: Final = "worker_result.json"
_LOG_FILENAME: Final = "worker.log"


class WorkerSpecError(ValueError):
    """A rejected worker specification carrying a stable ``field`` and ``reason``."""

    __slots__ = ("field", "reason")

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    """One server-owned worker command, fully validated before any spawn."""

    kind: Literal["backtest", "factor"]
    argv: tuple[str, ...]
    cwd: Path
    timeout_s: int
    run_dir: Path
    result_path: Path
    log_path: Path
    env: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        _validate_kind(self.kind)
        _validate_argv(self.argv)
        _validate_cwd(self.cwd)
        _validate_timeout(self.timeout_s)
        run_dir = _validate_run_dir(self.run_dir)
        _validate_output(self.result_path, run_dir, "result_path")
        _validate_output(self.log_path, run_dir, "log_path")
        _validate_env(self.env)


def backtest_worker_spec(
    run_dir: Path, timeout_s: int, *, env: Mapping[str, str] | None = None
) -> WorkerSpec:
    """Build the only allowed backtest worker command.

    Args:
        run_dir: Run directory under an allowed run root.
        timeout_s: Declared worker timeout in seconds (1..86400).
        env: Optional extra environment for the worker process.

    Returns:
        A validated spec running ``backtest/runner.py`` with cwd at the agent root.

    Raises:
        WorkerSpecError: If the run directory or any derived field is invalid.
    """
    resolved = _validate_run_dir(run_dir)
    return WorkerSpec(
        kind="backtest",
        argv=(
            sys.executable,
            str(_AGENT_ROOT / "backtest" / "runner.py"),
            str(resolved),
        ),
        cwd=_AGENT_ROOT,
        timeout_s=timeout_s,
        run_dir=resolved,
        result_path=resolved / _RESULT_FILENAME,
        log_path=resolved / _LOG_FILENAME,
        env=env,
    )


def factor_worker_spec(
    run_dir: Path, timeout_s: int, *, env: Mapping[str, str] | None = None
) -> WorkerSpec:
    """Build the only allowed factor-prototype worker command.

    Args:
        run_dir: Run directory under an allowed run root.
        timeout_s: Declared worker timeout in seconds (1..86400).
        env: Optional extra environment for the worker process.

    Returns:
        A validated spec running ``src.factors.prototype_worker`` as a module.

    Raises:
        WorkerSpecError: If the run directory or any derived field is invalid.
    """
    resolved = _validate_run_dir(run_dir)
    return WorkerSpec(
        kind="factor",
        argv=(sys.executable, "-m", "src.factors.prototype_worker", str(resolved)),
        cwd=_AGENT_ROOT,
        timeout_s=timeout_s,
        run_dir=resolved,
        result_path=resolved / _RESULT_FILENAME,
        log_path=resolved / _LOG_FILENAME,
        env=env,
    )


def _validate_kind(kind: object) -> None:
    if kind not in _KINDS:
        raise WorkerSpecError("kind", "must be 'backtest' or 'factor'")


def _validate_argv(argv: object) -> None:
    if isinstance(argv, str) or not isinstance(argv, tuple):
        raise WorkerSpecError("argv", "must be a tuple of non-empty strings")
    if not argv:
        raise WorkerSpecError("argv", "must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in argv):
        raise WorkerSpecError("argv", "must be a tuple of non-empty strings")
    if argv[0] != sys.executable:
        raise WorkerSpecError("argv", "argv[0] must be the current Python interpreter")
    for item in argv[1:]:
        if item.lower() in _SHELL_FLAGS or Path(item).name.lower() in _SHELL_NAMES:
            raise WorkerSpecError("argv", "must not invoke a shell")


def _validate_cwd(cwd: object) -> None:
    if not isinstance(cwd, Path) or not cwd.is_absolute():
        raise WorkerSpecError("cwd", "must be an absolute pathlib.Path")
    if cwd.resolve() != _AGENT_ROOT:
        raise WorkerSpecError("cwd", "must be the server agent root")


def _validate_timeout(timeout_s: object) -> None:
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int):
        raise WorkerSpecError("timeout_s", "must be an int between 1 and 86400 seconds")
    if not 0 < timeout_s <= _MAX_TIMEOUT_S:
        raise WorkerSpecError("timeout_s", "must be an int between 1 and 86400 seconds")


def _validate_run_dir(run_dir: object) -> Path:
    if not isinstance(run_dir, Path) or not run_dir.is_absolute():
        raise WorkerSpecError("run_dir", "must be an absolute pathlib.Path")
    resolved = run_dir.expanduser().resolve()
    try:
        safe_run_dir(str(resolved))
    except ValueError as exc:
        raise WorkerSpecError("run_dir", "must be inside allowed run roots") from exc
    if not _is_directory_or_creatable(resolved):
        raise WorkerSpecError("run_dir", "must be a directory or creatable directory")
    return resolved


def _is_directory_or_creatable(path: Path) -> bool:
    ancestor = path
    while not ancestor.exists():
        if ancestor.parent == ancestor:
            return False
        ancestor = ancestor.parent
    return ancestor.is_dir()


def _validate_output(path: object, run_dir: Path, field: str) -> None:
    reason = "must be an absolute path inside run_dir"
    if not isinstance(path, Path) or not path.is_absolute():
        raise WorkerSpecError(field, reason)
    resolved = path.resolve()
    if resolved == run_dir or not resolved.is_relative_to(run_dir):
        raise WorkerSpecError(field, reason)


def _validate_env(env: object) -> None:
    if env is None:
        return
    if not isinstance(env, Mapping):
        raise WorkerSpecError(
            "env", "must be a mapping of string keys to string values"
        )
    for key, value in env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise WorkerSpecError(
                "env", "must be a mapping of string keys to string values"
            )
