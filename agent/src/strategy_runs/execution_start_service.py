"""Bind a prepared declaration to exactly one native worker start.

# noqa: SIZE_OK - backtest/factor start ordering and CAS races share one seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol, TypeAlias

from src.config.paths import get_runtime_root
from src.factors.registry import get_default_registry
from src.factors.prototype_contract import factor_remote_id, write_prototype_json
from src.factors.registry_freeze import (
    RegistryFreeze,
    RegistryFreezeError,
    freeze_registry,
    rows_from_registry,
)
from src.strategy_runs.backtest_materialize import (
    BacktestPrepareError,
    recheck_backtest_inputs,
)
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    BacktestStartRequest,
    ExecutionStartRequest,
    FactorStartRequest,
    JsonValue,
    backtest_primitive_prompt,
)
from src.strategy_runs.control_store import (
    ControlStore,
    ControlStoreError,
    DispatchConflict,
    DispatchNotFound,
    DispatchRow,
    DispatchTransitionError,
    StartClaim,
)
from src.strategy_runs.execution_worker import (
    WorkerSpec,
    backtest_worker_spec,
    factor_worker_spec,
)
from src.tools.path_utils import safe_run_dir

__all__ = [
    "ExecutionStartError",
    "ExecutionStartResult",
    "ExecutionStartService",
    "ExecutionSupervisor",
]

_BACKTEST_TIMEOUT_S: Final = 3600
_FACTOR_TIMEOUT_S: Final = 3600
ExecutionStartErrorKind: TypeAlias = Literal[
    "prepare_required",
    "idempotency_conflict",
    "input_digest_mismatch",
    "invalid_request",
    "storage_unavailable",
    "worker_start_failed",
]


class ExecutionSupervisor(Protocol):
    """The one native-supervisor operation used by the start service."""

    def start(self, dispatch_key: str, spec: WorkerSpec) -> DispatchRow:
        """Start one newly claimed dispatch."""


class ExecutionStartError(Exception):
    """A stable start failure safe for projection onto the wire."""

    __slots__ = ("kind", "original_remote_id")

    def __init__(
        self,
        kind: ExecutionStartErrorKind,
        original_remote_id: str | None = None,
    ) -> None:
        self.kind = kind
        self.original_remote_id = original_remote_id
        super().__init__(kind)


@dataclass(frozen=True, slots=True)
class ExecutionStartResult:
    """The exact accepted-start projection."""

    remote_id: str
    dispatch_key: str
    kind: Literal["backtest", "factor"]
    status: Literal["running"]
    replayed: bool
    contract_version: str


class ExecutionStartService:
    """Validate prepared bindings, claim the declaration, then spawn once."""

    def __init__(
        self,
        control_store: ControlStore,
        supervisor: ExecutionSupervisor,
    ) -> None:
        self._control_store = control_store
        self._supervisor = supervisor

    def start(
        self,
        request: ExecutionStartRequest,
        *,
        require_frozen_backtest: bool = False,
    ) -> ExecutionStartResult:
        """Start or replay one declaration of either kind."""
        if isinstance(request, FactorStartRequest):
            return self._start_factor(request)
        return self._start_backtest(request, require_frozen=require_frozen_backtest)

    def _start_backtest(
        self, request: BacktestStartRequest, *, require_frozen: bool
    ) -> ExecutionStartResult:
        """Start or replay one backtest without modifying its frozen inputs."""
        try:
            row = self._control_store.get_dispatch(request.dispatch_key)
        except ControlStoreError as exc:
            raise ExecutionStartError("storage_unavailable") from exc
        if row is None or row.kind != "backtest":
            raise ExecutionStartError("prepare_required")
        if not _matches_prepared(row, request):
            raise ExecutionStartError("idempotency_conflict", row.remote_id)
        if request.prompt != backtest_primitive_prompt(request.run_dir):
            raise ExecutionStartError("invalid_request")
        if row.status == "failed" and row.error_kind == "input_digest_mismatch":
            raise ExecutionStartError("input_digest_mismatch", row.remote_id)
        self._recheck_backtest(row, require_frozen=require_frozen)

        try:
            claim = self._control_store.begin_start(
                request.dispatch_key,
                request.run_id,
                request.model_dump(mode="json"),
                CONTRACT_VERSION,
            )
        except DispatchConflict as exc:
            raise ExecutionStartError(
                "idempotency_conflict", exc.original_remote_id
            ) from exc
        except DispatchNotFound as exc:
            raise ExecutionStartError("prepare_required") from exc
        except ControlStoreError as exc:
            raise ExecutionStartError("storage_unavailable") from exc

        if not claim.replayed:
            try:
                spec = backtest_worker_spec(
                    Path(request.run_dir), timeout_s=_BACKTEST_TIMEOUT_S
                )
                self._supervisor.start(request.dispatch_key, spec)
            except Exception as exc:  # noqa: BLE001 — fixed spawn-failure boundary
                raise ExecutionStartError("worker_start_failed") from exc
        return _accepted(claim, kind="backtest")

    def _recheck_backtest(self, row: DispatchRow, *, require_frozen: bool) -> None:
        """Reject drift only for a new claim; existing starts remain replays."""
        if row.status != "dispatched" or row.start_digest is not None:
            return
        try:
            prepared = recheck_backtest_inputs(row.declaration_json)
            if prepared is not None and (
                prepared.run_dir != row.run_dir
                or prepared.input_digest != row.input_digest
                or prepared.config_sha256 != row.config_sha256
                or prepared.source_sha256 != row.source_sha256
                or prepared.contract_version != row.contract_version
            ):
                raise BacktestPrepareError("input_digest_mismatch")
        except BacktestPrepareError as exc:
            if exc.kind != "input_digest_mismatch":
                raise ExecutionStartError("storage_unavailable") from exc
            try:
                self._control_store.mark_dispatch_terminal(
                    row.dispatch_key,
                    "failed",
                    error_kind="input_digest_mismatch",
                )
            except DispatchTransitionError as store_exc:
                try:
                    current = self._control_store.get_dispatch(row.dispatch_key)
                except ControlStoreError as current_exc:
                    raise ExecutionStartError("storage_unavailable") from current_exc
                if current is None or current.status == "dispatched":
                    raise ExecutionStartError("storage_unavailable") from store_exc
                if (
                    current.status == "failed"
                    and current.error_kind == "input_digest_mismatch"
                ):
                    raise ExecutionStartError(
                        "input_digest_mismatch", row.remote_id
                    ) from store_exc
                return
            except ControlStoreError as store_exc:
                raise ExecutionStartError("storage_unavailable") from store_exc
            raise ExecutionStartError("input_digest_mismatch", row.remote_id) from exc
        if prepared is None and require_frozen:
            raise ExecutionStartError("prepare_required")

    def _start_factor(self, request: FactorStartRequest) -> ExecutionStartResult:
        """Verify the frozen registry, claim the declaration, then spawn once."""
        frozen = _verify_factor_registry(request)
        remote_id = factor_remote_id(request.dispatch_key, request.spec)
        try:
            run_dir = safe_run_dir(str(get_runtime_root() / "runs" / remote_id))
        except ValueError as exc:
            raise ExecutionStartError("invalid_request") from exc
        declaration: dict[str, JsonValue] = {
            "start": request.model_dump(mode="json"),
            "remote_id": remote_id,
            "run_dir": str(run_dir),
        }
        try:
            claim = self._control_store.claim_dispatch(
                request.dispatch_key,
                "factor",
                declaration,
                remote_id,
                CONTRACT_VERSION,
            )
        except DispatchConflict as exc:
            raise ExecutionStartError(
                "idempotency_conflict", exc.original_remote_id
            ) from exc
        except ControlStoreError as exc:
            raise ExecutionStartError("storage_unavailable") from exc

        if not claim.replayed:
            try:
                run_dir.mkdir(parents=True, exist_ok=True)
                _write_factor_prototype(request, run_dir, remote_id, frozen)
                worker_spec = factor_worker_spec(run_dir, timeout_s=_FACTOR_TIMEOUT_S)
                self._supervisor.start(request.dispatch_key, worker_spec)
            except Exception as exc:  # noqa: BLE001 — fixed spawn-failure boundary
                raise ExecutionStartError("worker_start_failed") from exc
        return _accepted(claim, kind="factor")


def _verify_factor_registry(request: FactorStartRequest) -> RegistryFreeze:
    """Recompute the selected registry and reject unknown or changed specs."""
    spec = request.spec
    try:
        frozen = freeze_registry(
            rows_from_registry(get_default_registry(), spec.candidate_ids)
        )
    except RegistryFreezeError as exc:
        raise ExecutionStartError("invalid_request") from exc
    if frozen.registry_digest != spec.registry_digest:
        raise ExecutionStartError("input_digest_mismatch")
    return frozen


def _write_factor_prototype(
    request: FactorStartRequest,
    run_dir: Path,
    remote_id: str,
    frozen: RegistryFreeze,
) -> None:
    """Publish the worker's canonical declaration before any process starts."""
    write_prototype_json(
        run_dir / "spec.json",
        {
            "remote_id": remote_id,
            "dispatch_key": request.dispatch_key,
            "registry_digest": frozen.registry_digest,
            "tool_schema_version": frozen.tool_schema_version,
            "contract_version": CONTRACT_VERSION,
            "spec": request.spec.model_dump(mode="json", by_alias=True),
        },
    )


def _matches_prepared(row: DispatchRow, request: BacktestStartRequest) -> bool:
    return (
        row.remote_id == request.run_id
        and row.run_dir == request.run_dir
        and row.input_digest == request.input_digest
        and row.config_sha256 == request.config_sha256
        and row.source_sha256 == request.source_sha256
    )


def _accepted(
    claim: StartClaim, *, kind: Literal["backtest", "factor"]
) -> ExecutionStartResult:
    return ExecutionStartResult(
        remote_id=claim.row.remote_id,
        dispatch_key=claim.row.dispatch_key,
        kind=kind,
        status="running",
        replayed=claim.replayed,
        contract_version=CONTRACT_VERSION,
    )
