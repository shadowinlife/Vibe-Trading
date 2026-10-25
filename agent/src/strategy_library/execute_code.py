"""Start and poll one scheduled code-strategy run in an isolated child process.

A run executes a frozen copy of a version snapshot, never the live strategy
directory: ``config.json`` and ``code/signal_engine.py`` are copied from
``strategies/<id>/versions/<version>/`` into
``<runs>/scheduled/<job_id>/<run_id>/`` before anything starts. Putting the
run directory under ``get_runs_dir()`` is load-bearing -- the backtest runner
re-validates the directory against its allowed run roots in the child process.

The parent never imports the strategy code. ``start_code_run`` writes
``state.json`` as ``running`` and spawns ``python -m
src.strategy_library.run_code_entry`` with an argv list (never a shell string)
and the child's stdout/stderr redirected under the run directory's ``logs/``.
The child pid lives only in this module's in-memory registry, so a restarted
process can neither signal a recycled pid nor see one in persisted state.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from src.config.paths import get_runs_dir
from src.strategy_library.history import RunOutcome
from src.strategy_library.paths import (
    SIGNAL_ENGINE_RELPATH,
    atomic_write_json,
    resolve_runtime_root,
    validate_id,
    version_code_path,
    version_config_path,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.scheduled_research.models import ScheduledResearchJob

__all__ = [
    "CodeHandle",
    "PID_REGISTRY",
    "poll_code_run",
    "start_code_run",
]

#: Agent package root; the child is launched from here so ``src.*`` imports
#: resolve without relying on the caller's working directory.
AGENT_ROOT = Path(__file__).resolve().parents[2]

ENTRY_MODULE = "src.strategy_library.run_code_entry"
RUNS_SUBDIRNAME = "scheduled"
STATE_FILENAME = "state.json"
RUNNER_STDERR_RELPATH = Path("logs") / "runner_stderr.txt"
ENTRY_STDOUT_RELPATH = Path("logs") / "entry_stdout.txt"
ENTRY_STDERR_RELPATH = Path("logs") / "entry_stderr.txt"
METRICS_RELPATH = Path("artifacts") / "metrics.csv"
EQUITY_RELPATH = Path("artifacts") / "equity.csv"

_LIVE_STATES = frozenset({"running", "pending"})
_SUCCESS_STATES = frozenset({"success", "completed"})
_SUMMARY_KEYS = ("total_return", "annual_return", "max_drawdown", "sharpe", "trade_count")
_ERROR_TAIL_LINES = 40
_MAX_ERROR_CHARS = 1000
_MAX_SUMMARY_CHARS = 500

#: Live child pids keyed by run id. In memory only: a pid is meaningless
#: after a restart (the process is gone and the number may be recycled), so
#: it must never reach a handle or persisted state.
PID_REGISTRY: dict[str, int] = {}


@dataclass(frozen=True)
class CodeHandle:
    """Handle to one in-flight code-strategy run.

    Exactly the five values the scheduled-job model persists. The child pid is
    deliberately absent; see :data:`PID_REGISTRY`.
    """

    run_id: str
    kind: str
    session_id: str | None
    run_dir: str | None
    started_at: int

    def to_in_flight(self) -> dict[str, Any]:
        """Return the five-key dict persisted as ``in_flight``."""
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "session_id": self.session_id,
            "run_dir": self.run_dir,
            "started_at": self.started_at,
        }


def _runs_root(runtime_root: Path | None) -> Path:
    """Return ``<runtime_root>/runs`` without creating it."""
    if runtime_root is None:
        return get_runs_dir()
    return resolve_runtime_root(runtime_root) / "runs"


def _checked_dir(root: Path, segments: tuple[str, ...]) -> Path:
    """Join *segments* under *root*, rejecting symlinked ancestors and escapes."""
    candidate = Path(root).joinpath(*segments)
    resolved_root = Path(root).resolve()
    for ancestor in (candidate, *candidate.parents):
        if ancestor == Path(root):
            break
        if ancestor.is_symlink():
            raise ValueError(f"refusing symlinked path component: {ancestor}")
    if not candidate.resolve().is_relative_to(resolved_root):
        raise ValueError(f"path {candidate} escapes the runs root {resolved_root}")
    return candidate


def _snapshot_sources(
    strategy_id: str, version: str, runtime_root: Path | None
) -> tuple[Path, Path]:
    """Return the snapshot ``config.json`` and ``signal_engine.py`` paths.

    Raises:
        FileNotFoundError: When either snapshot file is missing. Callers rely
            on this firing before any run directory exists.
    """
    config_source = version_config_path(strategy_id, version, runtime_root=runtime_root)
    code_source = version_code_path(strategy_id, version, runtime_root=runtime_root)
    missing = [
        label
        for label, path in (
            ("config.json", config_source),
            (SIGNAL_ENGINE_RELPATH, code_source),
        )
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"strategy snapshot {strategy_id}/{version} is missing {', '.join(missing)}"
        )
    return config_source, code_source


def _spawn_entry(run_dir: Path) -> int:
    """Launch the child entry for *run_dir* and return its pid."""
    with (
        (run_dir / ENTRY_STDOUT_RELPATH).open("w", encoding="utf-8") as stdout_handle,
        (run_dir / ENTRY_STDERR_RELPATH).open("w", encoding="utf-8") as stderr_handle,
    ):
        process = subprocess.Popen(
            [sys.executable, "-m", ENTRY_MODULE, str(run_dir)],
            cwd=str(AGENT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            close_fds=True,
        )
    return process.pid


def start_code_run(
    job: "ScheduledResearchJob", *, runtime_root: Path | None = None
) -> CodeHandle:
    """Snapshot *job*'s strategy version and start it without waiting.

    Args:
        job: Strategy job whose ``strategy_id``/``strategy_version`` pin the
            exact snapshot to execute and whose ``id`` names the run parent.
        runtime_root: Explicit runtime root override (tests); defaults to the
            process runtime root.

    Returns:
        A :class:`CodeHandle` carrying the five persisted values.

    Raises:
        FileNotFoundError: When the version snapshot is incomplete. Nothing is
            created in that case.
        ValueError: When a job/strategy/version identifier is not a safe path
            segment.
    """
    job_id = validate_id(job.id, what="job_id")
    strategy_id = validate_id(job.strategy_id, what="strategy_id")
    version = validate_id(job.strategy_version, what="strategy_version")
    config_source, code_source = _snapshot_sources(strategy_id, version, runtime_root)

    run_id = uuid.uuid4().hex
    run_dir = _checked_dir(_runs_root(runtime_root), (RUNS_SUBDIRNAME, job_id, run_id))
    (run_dir / "code").mkdir(parents=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config_source, run_dir / "config.json")
    shutil.copyfile(code_source, run_dir / "code" / "signal_engine.py")
    atomic_write_json(run_dir / STATE_FILENAME, {"status": "running"})

    try:
        pid = _spawn_entry(run_dir)
    except OSError as exc:
        atomic_write_json(
            run_dir / STATE_FILENAME,
            {"status": "failed", "reason": f"could not start the run process: {exc}"},
        )
        raise
    PID_REGISTRY[run_id] = pid
    return CodeHandle(
        run_id=run_id,
        kind="code",
        session_id=None,
        run_dir=str(run_dir),
        started_at=int(time.time() * 1000),
    )


def _read_state(path: Path) -> dict[str, Any] | None:
    """Return the parsed state object, or ``None`` when absent or torn.

    ``state.json`` is replaced atomically, but a reader can still observe a
    partially written file on a filesystem that does not honour the rename or
    while a writer truncates; such a read is a live run, not an error.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _reap(run_id: str) -> None:
    """Reap the child when it has already exited; never block."""
    pid = PID_REGISTRY.get(run_id)
    if pid is None:
        return
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        PID_REGISTRY.pop(run_id, None)
        return
    if reaped == pid:
        PID_REGISTRY.pop(run_id, None)


def _read_first_csv_row(path: Path) -> dict[str, str]:
    """Return the first data row of *path*, or an empty dict when unreadable."""
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = csv.DictReader(handle)
            return next(rows, {})
    except (OSError, ValueError, csv.Error):
        return {}


def _count_data_rows(path: Path) -> int:
    """Return the number of non-empty data rows in *path* (0 when unreadable)."""
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            return sum(1 for row in reader if any(cell.strip() for cell in row))
    except (OSError, ValueError, csv.Error):
        return 0


def _summary(metrics: Mapping[str, str]) -> str:
    """Return one bounded line naming the metrics that were written."""
    parts: list[str] = []
    for key in _SUMMARY_KEYS:
        value = (metrics.get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    text = " ".join(parts) if parts else "backtest completed"
    return text[:_MAX_SUMMARY_CHARS]


def _read_error_tail(path: Path) -> str:
    """Return the last :data:`_ERROR_TAIL_LINES` lines of a log, if any."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()[-_ERROR_TAIL_LINES:]
    return "\n".join(line for line in lines if line.strip()).strip()


def _bounded(text: str) -> str:
    """Keep the newest diagnostic text within the durable error budget."""
    text = text.strip()
    if len(text) <= _MAX_ERROR_CHARS:
        return text
    return "..." + text[-(_MAX_ERROR_CHARS - 3):]


def _failed(run_dir: Path, state: Mapping[str, Any]) -> RunOutcome:
    """Build the failure outcome, preferring the runner log over the reason."""
    reason = state.get("reason")
    reason_text = reason.strip() if isinstance(reason, str) else ""
    detail = _read_error_tail(run_dir / RUNNER_STDERR_RELPATH) or reason_text
    return RunOutcome(
        status="failed",
        error=_bounded(detail or "strategy run failed without a diagnostic"),
        error_kind="runner_error",
        run_dir=str(run_dir),
    )


def _completed(run_dir: Path) -> RunOutcome:
    """Build the success outcome, failing when no equity curve was produced."""
    if _count_data_rows(run_dir / EQUITY_RELPATH) <= 0:
        return RunOutcome(
            status="failed",
            error="backtest finished without an equity curve",
            error_kind="empty_equity",
            run_dir=str(run_dir),
        )
    return RunOutcome(
        status="completed",
        summary=_summary(_read_first_csv_row(run_dir / METRICS_RELPATH)),
        run_dir=str(run_dir),
    )


def poll_code_run(
    handle: Any, *, runtime_root: Path | None = None
) -> RunOutcome | None:
    """Return the run's terminal outcome, or ``None`` while it is still live.

    Never raises: a handle without a directory, a missing run directory, a
    torn ``state.json`` and an unreadable artifact all degrade to ``None`` (the
    run is still in flight from the caller's point of view) or to a failure
    outcome, so the executor's own timeout remains the only hard stop.

    Args:
        handle: A :class:`CodeHandle` or a rehydrated in-flight handle.
        runtime_root: Accepted for symmetry with :func:`start_code_run`; the
            handle carries an absolute run directory.

    Returns:
        ``None`` while running, otherwise the terminal :class:`RunOutcome`.
    """
    del runtime_root  # the persisted handle names the run directory absolutely
    run_id = getattr(handle, "run_id", "")
    if isinstance(run_id, str) and run_id:
        _reap(run_id)

    raw_dir = getattr(handle, "run_dir", None)
    if not raw_dir:
        return RunOutcome(
            status="failed",
            error="strategy handle carries no run directory",
            error_kind="runner_error",
        )
    run_dir = Path(raw_dir)
    state = _read_state(run_dir / STATE_FILENAME)
    if state is None:
        return None
    status = str(state.get("status") or "").strip().lower()
    if not status or status in _LIVE_STATES:
        return None
    if status in _SUCCESS_STATES:
        return _completed(run_dir)
    return _failed(run_dir, state)
