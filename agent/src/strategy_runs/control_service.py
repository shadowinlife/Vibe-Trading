"""Read-only projections for original execution instances.

Audit exception: one service module is required; helper modules are out of scope.
# noqa: SIZE_OK
The service never starts, stops, writes, or signals work. ``find`` reads native
and legacy identities in one SQLite snapshot; artifact reads are confined to
server-declared run directories and bounded JSON files.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, assert_never

from pydantic import ValidationError

from src.factors.prototype_result import (
    PrototypeResultIdentity,
    valid_prototype_progress,
    valid_prototype_result,
)
from src.strategy_runs.backtest_materialize import BacktestPrepareResponse
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    ERROR_KINDS,
    ContractValidationError,
    JsonValue,
    canonical_json_bytes,
)
from src.strategy_runs.control_store import (
    ControlStore,
    ControlStoreError,
    DispatchRow,
    StopRow,
)
from src.strategy_runs.store import RunRow, StrategyRunStore
from src.tools.backtest_summary import try_build_backtest_summary
from src.tools.path_utils import safe_run_dir

__all__ = [
    "ControlProjectionError",
    "ExecutionControlService",
    "FindProjection",
    "ResultProjection",
    "StatusProjection",
]

_IDENTITY_RE: Final = re.compile(r"^[A-Za-z0-9._~-]+$")
_RESULT_BYTE_CAP: Final = 50 * 1024
_PROTOTYPE_RESULT_BYTE_CAP: Final = 8 * 1024 * 1024
_BACKTEST_RUN_CARD_BYTE_CAP: Final = 8 * 1024 * 1024
_ERROR_KINDS: Final = frozenset(ERROR_KINDS)
_TERMINAL_STATUSES: Final = frozenset({"completed", "failed", "cancelled"})
_BATCH_STATUSES: Final = frozenset({"completed", "partial", "cancelled", "failed"})
_BATCH_TO_STATUS: Final = {
    "completed": "completed",
    "partial": "unknown",
    "cancelled": "cancelled",
    "failed": "failed",
}


class ControlProjectionError(ValueError):
    """A stable projection failure safe for an HTTP boundary to classify."""

    __slots__ = ("kind", "detail")
    kind: Literal[
        "identity_conflict", "storage_unavailable", "invalid_request", "not_found"
    ]
    detail: str

    def __init__(
        self,
        kind: Literal[
            "identity_conflict", "storage_unavailable", "invalid_request", "not_found"
        ],
        detail: str,
    ) -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}")


@dataclass(frozen=True, slots=True)
class FindProjection:
    """Recovery state for one client dispatch key."""

    dispatch_key: str
    state: str
    remote_id: str | None = None
    status: str | None = None
    run_dir: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error_kind: str | None = None
    last_status: str | None = None
    absence_evidence: str | None = None
    observed_at: str | None = None
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class StatusProjection:
    """Five-state execution status with ISO-8601 observation timestamps."""

    remote_id: str
    dispatch_key: str
    status: str
    started_at: str | None
    finished_at: str | None
    error_kind: str | None
    run_dir: str | None
    batch_status: str | None = None
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class ResultProjection:
    """Bounded result JSON, or an explicit unavailable projection."""

    remote_id: str
    status: str
    result_status: str
    result: JsonValue
    progress: JsonValue
    has_more: bool
    batch_status: str | None = None
    contract_version: str = CONTRACT_VERSION


class ExecutionControlService:
    """Project native and legacy rows without changing execution state."""

    def __init__(
        self,
        control_store: ControlStore,
        legacy_store: StrategyRunStore | None = None,
        handle_provider: Callable[[str], bool] | None = None,
    ) -> None:
        self._control_store = control_store
        self._legacy_store = legacy_store or control_store
        self._handle_provider = handle_provider

    def find(self, dispatch_key: str) -> FindProjection:
        """Resolve one key across both tables in a single read transaction."""
        key = _identifier(dispatch_key, 200, "dispatch_key")
        try:
            with closing(sqlite3.connect(str(self._control_store.path))) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN")
                dispatch_row = conn.execute(
                    "SELECT * FROM execution_dispatches WHERE dispatch_key = ?", (key,)
                ).fetchone()
                legacy_row = conn.execute(
                    "SELECT * FROM strategy_runs WHERE run_key = ?", (key,)
                ).fetchone()
                stop_row = None
                if dispatch_row is not None:
                    stop_row = conn.execute(
                        "SELECT * FROM execution_stops WHERE dispatch_key = ? "
                        "AND state = 'confirmed' AND evidence_kind != 'process_exit' "
                        "ORDER BY resolved_at DESC, stop_key DESC LIMIT 1",
                        (key,),
                    ).fetchone()
                conn.execute("COMMIT")
        except sqlite3.Error as exc:
            raise ControlProjectionError(
                "storage_unavailable", "find snapshot could not be read"
            ) from exc
        dispatch = DispatchRow.from_row(dispatch_row) if dispatch_row else None
        legacy = RunRow.from_row(legacy_row) if legacy_row else None
        stop = StopRow.from_row(stop_row) if stop_row else None
        return self._find_projection(key, dispatch, legacy, stop)

    def status(self, remote_id: str) -> StatusProjection:
        """Return stable terminal state or handle-observed nonterminal state."""
        return _status_projection(self._remote_row(remote_id), self._is_live)

    def result(self, remote_id: str) -> ResultProjection:
        """Read only the bounded JSON artifact persisted for this dispatch."""
        row = self._remote_row(remote_id)
        status = _status_projection(row, self._is_live)
        match row:
            case DispatchRow():
                return self._native_result(row, status)
            case RunRow():
                return _unavailable_result(status)
            case unreachable:
                assert_never(unreachable)

    def _remote_row(self, remote_id: str) -> DispatchRow | RunRow:
        key = _identifier(remote_id, 128, "remote_id")
        try:
            dispatch = self._control_store.get_dispatch_by_remote_id(key)
            legacy = None if dispatch is not None else self._legacy_store.get(key)
        except (ControlStoreError, sqlite3.Error) as exc:
            raise ControlProjectionError(
                "storage_unavailable", "execution status could not be read"
            ) from exc
        row = dispatch or legacy
        if row is not None:
            return row
        raise ControlProjectionError("not_found", "execution identity does not exist")

    def _is_live(self, remote_id: str) -> bool:
        return self._handle_provider is not None and bool(
            self._handle_provider(remote_id)
        )

    def _find_projection(
        self,
        key: str,
        dispatch: DispatchRow | None,
        legacy: RunRow | None,
        stop: StopRow | None,
    ) -> FindProjection:
        if dispatch is None:
            if legacy is None:
                return FindProjection(key, "not_dispatched")
            status = _status_projection(legacy, self._is_live)
            return _dispatched_find(key, status)
        if legacy is not None and dispatch.remote_id != legacy.vt_run_id:
            raise ControlProjectionError(
                "identity_conflict", "dispatch key has conflicting remote identities"
            )
        evidence = stop if dispatch.status != "dispatched" else None
        if evidence is not None:
            return FindProjection(
                dispatch_key=key,
                state="absent",
                remote_id=dispatch.remote_id,
                last_status=dispatch.status,
                absence_evidence=evidence.evidence_kind,
                observed_at=_iso(evidence.resolved_at),
            )
        return _dispatched_find(key, _status_projection(dispatch, self._is_live))

    def _native_result(
        self, row: DispatchRow, status: StatusProjection
    ) -> ResultProjection:
        if row.run_dir is None:
            return _unavailable_result(status)
        try:
            run_dir = safe_run_dir(row.run_dir)
            worker_path = (run_dir / "result.json").resolve()
            progress_path = (run_dir / "progress.json").resolve()
            if not worker_path.is_relative_to(
                run_dir
            ) or not progress_path.is_relative_to(run_dir):
                raise _invalid_result()
        except (OSError, ValueError) as exc:
            raise _invalid_result() from exc
        if _has_backtest_provenance(row):
            return _backtest_result(row, status, run_dir)
        identity = _factor_result_identity(row)
        worker_payload = _read_bounded_json(worker_path, _PROTOTYPE_RESULT_BYTE_CAP)
        if identity is not None and valid_prototype_result(worker_payload, identity):
            progress = _read_bounded_json(progress_path)
            if not valid_prototype_progress(progress, worker_payload):
                progress = None
            return _available_result(row, status, worker_payload, progress)
        if row.result_path is None:
            return _unavailable_result(status)
        try:
            candidate = Path(row.result_path).expanduser()
            if not candidate.is_absolute():
                raise _invalid_result()
            result_path = candidate.resolve()
        except (OSError, ValueError) as exc:
            raise _invalid_result() from exc
        if not result_path.is_relative_to(run_dir):
            raise _invalid_result()
        payload = _read_bounded_json(result_path)
        if payload is None:
            return _unavailable_result(status)
        return _available_result(row, status, payload)


def _has_backtest_provenance(row: DispatchRow) -> bool:
    return row.kind == "backtest" and any(
        value is not None
        for value in (row.input_digest, row.config_sha256, row.source_sha256)
    )


def _run_card_artifacts(
    card: dict[str, JsonValue], run_dir: Path
) -> list[JsonValue] | None:
    artifacts = card.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            return None
        relative = artifact.get("path")
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
        ):
            return None
        try:
            contained = (run_dir / relative).resolve()
        except (OSError, ValueError):
            return None
        if not contained.is_relative_to(run_dir):
            return None
    return artifacts


def _backtest_result(
    row: DispatchRow, status: StatusProjection, run_dir: Path
) -> ResultProjection:
    """Project one completed native run without trimming its 1.0 summary."""
    if status.status != "completed":
        return _unavailable_result(status)
    try:
        prepared = BacktestPrepareResponse.model_validate_json(row.declaration_json)
    except ValidationError:
        return _unavailable_result(status)
    if (
        prepared.run_dir != row.run_dir
        or prepared.input_digest != row.input_digest
        or prepared.config_sha256 != row.config_sha256
        or prepared.source_sha256 != row.source_sha256
    ):
        return _unavailable_result(status)

    summary = try_build_backtest_summary(run_dir)
    if summary is None:
        return _unavailable_result(status)
    card_path = (run_dir / "run_card.json").resolve()
    if not card_path.is_relative_to(run_dir):
        raise _invalid_result()
    card = _read_bounded_json(card_path, _BACKTEST_RUN_CARD_BYTE_CAP)
    if not isinstance(card, dict):
        return _unavailable_result(status)
    artifacts = _run_card_artifacts(card, run_dir)
    if artifacts is None:
        return _unavailable_result(status)
    envelope: dict[str, JsonValue] = {
        "remote_id": row.remote_id,
        "dispatch_key": row.dispatch_key,
        "contract_version": prepared.contract_version,
        "input_digest": prepared.input_digest,
        "config_sha256": prepared.config_sha256,
        "source_sha256": prepared.source_sha256,
        "data_digest": prepared.data_digest,
        "data_digest_status": prepared.data_digest_status,
        "summary": summary,
        "artifacts": artifacts,
    }
    return _available_result(row, status, envelope)


def _read_bounded_json(path: Path, cap: int = _RESULT_BYTE_CAP) -> JsonValue | None:
    try:
        with path.open("rb") as stream:
            raw = stream.read(cap + 1)
        if len(raw) > cap:
            return None
        payload: JsonValue = json.loads(raw)
        canonical_json_bytes(payload)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ContractValidationError,
    ):
        return None
    return payload


def _factor_result_identity(row: DispatchRow) -> PrototypeResultIdentity | None:
    if row.kind != "factor":
        return None
    try:
        declaration = json.loads(row.declaration_json)
        digest = declaration["start"]["spec"]["registry_digest"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not isinstance(digest, str):
        return None
    return PrototypeResultIdentity(row.remote_id, row.dispatch_key, digest)


def _available_result(
    row: DispatchRow,
    status: StatusProjection,
    payload: JsonValue,
    progress: JsonValue = None,
) -> ResultProjection:
    result = payload
    if isinstance(payload, dict) and "result" in payload:
        result = payload["result"]
        progress = payload.get("progress")
    return ResultProjection(
        row.remote_id,
        status.status,
        "available",
        result,
        progress,
        False,
        status.batch_status,
    )


def _dispatched_find(key: str, status: StatusProjection) -> FindProjection:
    return FindProjection(
        dispatch_key=key,
        state="dispatched",
        remote_id=status.remote_id,
        status=status.status,
        run_dir=status.run_dir,
        started_at=status.started_at,
        finished_at=status.finished_at,
        error_kind=status.error_kind,
    )


def _status_projection(
    row: DispatchRow | RunRow, is_live: Callable[[str], bool]
) -> StatusProjection:
    match row:
        case DispatchRow():
            status, error_kind = _observed_status(row, is_live)
            batch_status = (
                _factor_batch_status(row) if status in _TERMINAL_STATUSES else None
            )
            status, error_kind = _batch_outcome(status, error_kind, batch_status)
            return StatusProjection(
                row.remote_id,
                row.dispatch_key,
                status,
                _iso(row.started_at),
                _iso(row.terminal_at),
                error_kind,
                row.run_dir,
                batch_status,
            )
        case RunRow():
            status, error_kind = _observed_status(row, is_live)
            finished = row.updated_at if row.status != "running" else None
            return StatusProjection(
                row.vt_run_id,
                row.run_key,
                status,
                _iso(row.created_at),
                _iso(finished),
                error_kind,
                None,
            )
        case unreachable:
            assert_never(unreachable)


def _factor_batch_status(row: DispatchRow) -> str | None:
    """Return the batch outcome the worker recorded in its own result document."""
    identity = _factor_result_identity(row)
    if identity is None or row.run_dir is None:
        return None
    try:
        run_dir = safe_run_dir(row.run_dir)
        result_path = (run_dir / "result.json").resolve()
        if not result_path.is_relative_to(run_dir):
            return None
    except (OSError, ValueError):
        return None
    payload = _read_bounded_json(result_path, _PROTOTYPE_RESULT_BYTE_CAP)
    if not valid_prototype_result(payload, identity):
        return None
    status = payload["status"]
    return status if isinstance(status, str) and status in _BATCH_STATUSES else None


def _batch_outcome(
    status: str, error_kind: str | None, batch_status: str | None
) -> tuple[str, str | None]:
    """Fold a recorded batch outcome into the five-state outer status."""
    if batch_status is None:
        return status, error_kind
    projected = _BATCH_TO_STATUS[batch_status]
    if projected == "completed" and status != "completed":
        return status, error_kind
    if projected == "failed":
        return "failed", error_kind or "runner_error"
    return projected, error_kind


def _observed_status(
    row: DispatchRow | RunRow, is_live: Callable[[str], bool]
) -> tuple[str, str | None]:
    match row:
        case DispatchRow(remote_id=remote_id, status=stored, error_kind=error_kind):
            pass
        case RunRow(vt_run_id=remote_id, status=stored, error_kind=error_kind):
            pass
        case unreachable:
            assert_never(unreachable)
    match stored:
        case "dispatched" | "running":
            return ("running" if is_live(remote_id) else "unknown"), None
        case "failed":
            return "failed", _failure_kind(error_kind)
        case "completed" | "cancelled":
            return stored, None
        case unreachable:
            assert_never(unreachable)


def _unavailable_result(s: StatusProjection) -> ResultProjection:
    return ResultProjection(
        s.remote_id, s.status, "unavailable", None, None, False, s.batch_status
    )


def _invalid_result() -> ControlProjectionError:
    return ControlProjectionError(
        "invalid_request", "result path is outside the server run boundary"
    )


def _failure_kind(value: str | None) -> str:
    kind = value.strip() if value is not None else ""
    return kind if kind in _ERROR_KINDS else "runner_error"


def _identifier(value: str, limit: int, field: str) -> str:
    valid_type = isinstance(value, str) and 1 <= len(value) <= limit
    if not valid_type or _IDENTITY_RE.fullmatch(value) is None:
        raise ControlProjectionError("invalid_request", f"invalid {field}")
    return value


def _iso(value: int | None) -> str | None:
    if value is None:
        return None
    seconds, milliseconds = divmod(value, 1000)
    stamp = datetime.fromtimestamp(seconds, UTC).replace(
        microsecond=milliseconds * 1000
    )
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")
