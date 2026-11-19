"""Resolve one idempotent stop tuple into monotonic durable evidence.

The service only delegates to the supervisor whose ``stop`` persists the
request before any signal and whose ``expire_stop`` resolves an overdue
unobservable request to ``unknown``. It never retries, upgrades ``unknown`` or
downgrades ``confirmed``; it projects the durable row as-is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, TypeAlias

from src.strategy_runs.control_contract import StopRequest
from src.strategy_runs.control_store import (
    ControlStore,
    ControlStoreError,
    DispatchConflict,
    DispatchNotFound,
    StopClaim,
    StopConflict,
    StopRow,
)
from src.strategy_runs.native_supervisor import SupervisorError

__all__ = [
    "ExecutionStopError",
    "ExecutionStopResult",
    "ExecutionStopService",
    "StopSupervisor",
]

ExecutionStopErrorKind: TypeAlias = Literal[
    "not_found",
    "idempotency_conflict",
    "storage_unavailable",
]


class StopSupervisor(Protocol):
    """The two native-supervisor operations used by the stop service."""

    def stop(
        self,
        dispatch_key: str,
        remote_id: str,
        stop_key: str,
        deadline_at: int,
    ) -> StopClaim:
        """Persist, signal and confirm one stop tuple."""

    def expire_stop(self, stop_key: str, now_ms: int | None = None) -> StopRow:
        """Resolve an overdue requested stop to ``unknown``."""


class ExecutionStopError(Exception):
    """A stable stop failure safe for projection onto the wire."""

    __slots__ = ("kind", "original_remote_id")

    def __init__(
        self,
        kind: ExecutionStopErrorKind,
        original_remote_id: str | None = None,
    ) -> None:
        self.kind = kind
        self.original_remote_id = original_remote_id
        super().__init__(kind)


@dataclass(frozen=True, slots=True)
class ExecutionStopResult:
    """The exact accepted-stop projection."""

    remote_id: str
    dispatch_key: str
    stop_key: str
    stop_state: Literal["requested", "confirmed", "unknown"]
    evidence_kind: str | None
    resolved_at: str | None
    replayed: bool
    contract_version: str


class ExecutionStopService:
    """Convert one wire stop request into its durable monotonic resolution."""

    def __init__(
        self,
        control_store: ControlStore,
        supervisor: StopSupervisor,
    ) -> None:
        self._control_store = control_store
        self._supervisor = supervisor

    def stop(self, request: StopRequest) -> ExecutionStopResult:
        """Persist and resolve one stop without retrying or rewriting evidence."""
        deadline_ms = int(request.deadline_at.timestamp() * 1000)
        try:
            claim = self._supervisor.stop(
                request.dispatch_key,
                request.remote_id,
                request.stop_key,
                deadline_ms,
            )
            row = self._control_store.get_stop(request.stop_key)
            if row is None:
                row = claim.row
            else:
                row = self._supervisor.expire_stop(request.stop_key)
        except DispatchNotFound as exc:
            raise ExecutionStopError("not_found") from exc
        except (StopConflict, DispatchConflict) as exc:
            raise ExecutionStopError(
                "idempotency_conflict", exc.original_remote_id
            ) from exc
        except (ControlStoreError, SupervisorError) as exc:
            raise ExecutionStopError("storage_unavailable") from exc
        return ExecutionStopResult(
            remote_id=row.remote_id,
            dispatch_key=row.dispatch_key,
            stop_key=request.stop_key,
            stop_state=row.state,
            evidence_kind=row.evidence_kind,
            resolved_at=_iso(row.resolved_at),
            replayed=claim.replayed,
            contract_version=row.contract_version,
        )


def _iso(value: int | None) -> str | None:
    """Render one epoch-millisecond stamp as a UTC ISO-8601 string."""
    if value is None:
        return None
    seconds, milliseconds = divmod(value, 1000)
    stamp = datetime.fromtimestamp(seconds, UTC).replace(
        microsecond=milliseconds * 1000
    )
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")
