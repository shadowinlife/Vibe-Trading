"""Bounded, crash-safe run history for scheduled research jobs.

One JSONL file per job under
``<runtime_root>/scheduled_research/runs/<job_id>.jsonl`` holds one record per
execution attempt; byte-level serialization, the 8192-byte line cap and the
atomic file mechanics live in :mod:`src.strategy_library.history_io`.

History is diagnostic state, never a source of truth: corrupt or torn lines are
skipped on read instead of raising, and a missing file reads as an empty list.
A single process-local writer per job is assumed; cross-process writers are not
merged.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Mapping

from src.strategy_library import history_io
from src.strategy_library.history_io import MAX_LINE_BYTES
from src.strategy_library.paths import resolve_runtime_root, validate_id

DEFAULT_RETENTION = 20
MIN_RETENTION = 5
MAX_RETENTION = 100

RUN_STATUSES = frozenset({"completed", "failed", "skipped", "missed"})

_JSONL_SUFFIX = ".jsonl"
_REQUIRED_TEXT_FIELDS = ("run_id", "job_id", "strategy_id", "strategy_version", "status")

__all__ = [
    "DEFAULT_RETENTION",
    "MAX_LINE_BYTES",
    "MAX_RETENTION",
    "MIN_RETENTION",
    "RUN_STATUSES",
    "RunOutcome",
    "RunRecord",
    "append_run",
    "clamp_retention",
    "delete_runs",
    "history_path",
    "read_runs",
    "read_runs_for_strategy",
    "runs_dir",
    "serialize_run_line",
]


def _validate_status(status: str) -> str:
    if status not in RUN_STATUSES:
        raise ValueError(f"status {status!r} must be one of {sorted(RUN_STATUSES)}")
    return status


def _validate_timestamp(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{what} must be a non-negative epoch-millisecond integer")
    return value


@dataclass(frozen=True)
class RunOutcome:
    """Terminal result of one scheduled-strategy execution.

    Shared by the code runner, the workflow runner and the executor sweep so
    all of them speak one result type.

    Attributes:
        status: One of ``completed``, ``failed``, ``skipped``, ``missed``.
        summary: Short human-readable outcome summary.
        error: Error message when the run failed.
        error_kind: Machine-readable failure class (e.g. ``timeout``).
        run_dir: Run artifact directory reference, never its contents.
        session_id: Chat session that carried a workflow run.
    """

    status: str
    summary: str = ""
    error: str = ""
    error_kind: str = ""
    run_dir: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        _validate_status(self.status)

    def to_dict(self) -> dict[str, Any]:
        """Return the outcome as a plain JSON-safe dict."""
        return {field.name: getattr(self, field.name) for field in fields(self)}

    @classmethod
    def from_record(cls, record: RunRecord) -> RunOutcome:
        """Build an outcome from a persisted run record."""
        return cls(
            status=record.status,
            summary=record.summary,
            error=record.error,
            error_kind=record.error_kind,
            run_dir=record.run_dir,
            session_id=record.session_id,
        )


@dataclass(frozen=True)
class RunRecord:
    """One execution attempt persisted in a job's JSONL history.

    Attributes:
        run_id: Unique run identifier.
        job_id: Owning scheduled job identifier.
        strategy_id: Strategy the run executed.
        strategy_version: Immutable version snapshot the run executed.
        started_at: Start time in epoch milliseconds.
        finished_at: Finish time in epoch milliseconds, ``None`` while running.
        status: One of ``completed``, ``failed``, ``skipped``, ``missed``.
        session_id: Chat session that carried a workflow run.
        run_dir: Run artifact directory reference, never its contents.
        summary: Short human-readable outcome summary.
        error: Error message when the run failed.
        error_kind: Machine-readable failure class.
    """

    run_id: str
    job_id: str
    strategy_id: str
    strategy_version: str
    started_at: int
    finished_at: int | None
    status: str
    session_id: str | None = None
    run_dir: str | None = None
    summary: str = ""
    error: str = ""
    error_kind: str = ""

    def __post_init__(self) -> None:
        validate_id(self.run_id, what="run_id")
        validate_id(self.job_id, what="job_id")
        validate_id(self.strategy_id, what="strategy_id")
        validate_id(self.strategy_version, what="strategy_version")
        _validate_status(self.status)
        _validate_timestamp(self.started_at, "started_at")
        if self.finished_at is not None:
            _validate_timestamp(self.finished_at, "finished_at")
        for name in ("summary", "error", "error_kind"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be a string")
        for name in ("session_id", "run_dir"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or None")

    def to_dict(self) -> dict[str, Any]:
        """Return the record as a plain JSON-safe dict in field order."""
        return {field.name: getattr(self, field.name) for field in fields(self)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RunRecord:
        """Build a record from parsed JSON.

        Raises:
            ValueError: When a required field is missing or mistyped.
        """
        if not isinstance(payload, Mapping):
            raise ValueError("run record must be a JSON object")
        for name in _REQUIRED_TEXT_FIELDS:
            if name not in payload:
                raise ValueError(f"run record is missing {name!r}")
            if not isinstance(payload[name], str) or not payload[name]:
                raise ValueError(f"run record field {name!r} must be a non-empty string")
        if "started_at" not in payload:
            raise ValueError("run record is missing 'started_at'")
        finished_at = payload.get("finished_at")
        if finished_at is not None and (
            isinstance(finished_at, bool) or not isinstance(finished_at, int)
        ):
            raise ValueError("run record field 'finished_at' must be an integer or null")

        def _text(name: str) -> str:
            value = payload.get(name, "")
            if value is None:
                return ""
            if not isinstance(value, str):
                raise ValueError(f"run record field {name!r} must be a string")
            return value

        def _optional_text(name: str) -> str | None:
            value = payload.get(name)
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError(f"run record field {name!r} must be a string or null")
            return value

        return cls(
            run_id=payload["run_id"],
            job_id=payload["job_id"],
            strategy_id=payload["strategy_id"],
            strategy_version=payload["strategy_version"],
            started_at=payload["started_at"],
            finished_at=finished_at,
            status=payload["status"],
            session_id=_optional_text("session_id"),
            run_dir=_optional_text("run_dir"),
            summary=_text("summary"),
            error=_text("error"),
            error_kind=_text("error_kind"),
        )

    def outcome(self) -> RunOutcome:
        """Return the terminal outcome carried by this record."""
        return RunOutcome.from_record(self)


def clamp_retention(value: int | None = None) -> int:
    """Clamp *value* into ``[MIN_RETENTION, MAX_RETENTION]``; ``None`` -> default.

    Raises:
        ValueError: When *value* is not an integer.
    """
    if value is None:
        return DEFAULT_RETENTION
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"retention must be an integer, got {type(value).__name__}")
    return max(MIN_RETENTION, min(MAX_RETENTION, value))


def runs_dir(*, runtime_root: Path | None = None) -> Path:
    """Return ``<runtime_root>/scheduled_research/runs`` without creating it."""
    return resolve_runtime_root(runtime_root) / "scheduled_research" / "runs"


def history_path(job_id: str, *, runtime_root: Path | None = None) -> Path:
    """Return ``<runs>/<job_id>.jsonl`` for a validated job id."""
    return history_io.history_file(runs_dir(runtime_root=runtime_root), job_id)


def serialize_run_line(record: RunRecord) -> bytes:
    """Serialize *record* to one newline-terminated JSONL line.

    The JSON payload (excluding the trailing newline) is guaranteed to be at
    most ``MAX_LINE_BYTES`` bytes; only ``summary``/``error`` (and, as a last
    resort, the optional reference fields) are truncated, never the structure.
    """
    return history_io.serialize_payload(record.to_dict())


def _read_file(path: Path) -> list[RunRecord]:
    """Read one history file, skipping corrupt or unrecognized lines."""
    records: list[RunRecord] = []
    for payload in history_io.read_raw_objects(path):
        try:
            records.append(RunRecord.from_dict(payload))
        except (ValueError, TypeError):
            continue
    return records


def _newest_first(records: list[RunRecord]) -> list[RunRecord]:
    return sorted(records, key=lambda record: (record.started_at, record.run_id), reverse=True)


def read_runs(
    job_id: str,
    *,
    limit: int | None = None,
    runtime_root: Path | None = None,
) -> list[RunRecord]:
    """Return a job's records, newest first.

    Records sort by ``started_at`` descending, tie-broken by ``run_id``. A
    missing file yields an empty list and corrupt lines are skipped.

    Raises:
        ValueError: When the job id is unsafe or *limit* is negative.
    """
    if limit is not None and limit < 0:
        raise ValueError("limit must not be negative")
    records = _newest_first(_read_file(history_path(job_id, runtime_root=runtime_root)))
    return records if limit is None else records[:limit]


def read_runs_for_strategy(
    strategy_id: str,
    limit: int = DEFAULT_RETENTION,
    *,
    runtime_root: Path | None = None,
) -> list[RunRecord]:
    """Merge every job's history for one strategy, newest first.

    Globs all job JSONL files, keeps records whose ``strategy_id`` matches,
    sorts by ``started_at`` descending (tie-broken by ``run_id``) and trims to
    *limit*. Per-job retention bounds the snapshot, so this stays request-cheap.

    Raises:
        ValueError: When the strategy id is unsafe or *limit* is negative.
    """
    if limit < 0:
        raise ValueError("limit must not be negative")
    ident = validate_id(strategy_id, what="strategy_id")
    merged: list[RunRecord] = []
    for path in sorted(runs_dir(runtime_root=runtime_root).glob(f"*{_JSONL_SUFFIX}")):
        merged.extend(record for record in _read_file(path) if record.strategy_id == ident)
    return _newest_first(merged)[:limit]


def append_run(
    record: RunRecord,
    *,
    retention: int | None = None,
    runtime_root: Path | None = None,
) -> RunRecord:
    """Append one record and trim the file to the clamped retention.

    The append is one ``O_APPEND`` write under the process-local lock; a trim
    rewrites atomically via temp + fsync + ``os.replace``.

    Args:
        record: The run record to persist.
        retention: Keep at most this many records (clamped to 5..100, default
            20).
        runtime_root: Explicit runtime root override (tests).

    Returns:
        The appended record.

    Raises:
        OSError: When the file cannot be written.
    """
    limit = clamp_retention(retention)
    path = history_path(record.job_id, runtime_root=runtime_root)
    line = serialize_run_line(record)
    with history_io.WRITE_LOCK:
        history_io.append_line(path, line)
        records = _newest_first(_read_file(path))
        if len(records) > limit:
            history_io.rewrite_payloads(
                path, [item.to_dict() for item in reversed(records[:limit])]
            )
    return record


def delete_runs(
    job_id: str,
    *,
    runtime_root: Path | None = None,
    delete_run_dirs: bool = True,
) -> bool:
    """Delete a job's history file and cascade to its run directories.

    Args:
        job_id: Job identifier.
        runtime_root: Explicit runtime root override (tests).
        delete_run_dirs: When true (default), also remove
            ``<runtime_root>/runs/scheduled/<job_id>`` and any referenced
            ``run_dir`` that resolves inside ``<runtime_root>/runs``.

    Returns:
        ``True`` when the history file existed and was removed, else ``False``.
    """
    ident = validate_id(job_id, what="job_id")
    root = resolve_runtime_root(runtime_root)
    path = history_path(ident, runtime_root=root)
    with history_io.WRITE_LOCK:
        records = _read_file(path)
        removed = False
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            pass
        if delete_run_dirs:
            history_io.cascade_run_dirs(ident, [item.run_dir for item in records], root)
    return removed
