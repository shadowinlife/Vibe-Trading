"""Authenticated HTTP surface for original execution instances.

Recovery reads expose ``find``, ``status`` and bounded ``result`` projections;
write routes start prepared declarations, execute frozen backtests, and stop
original instances. Handlers perform
service-token auth via :mod:`src.api.strategy_run_auth`, delegate through an
injected service, and project exact wire bodies.

The service provider is a zero-argument callable resolved inside each request;
construction failures are an entry fault (fixed ``503``) rather than a
per-instance state. Error bodies are fixed text: a projection detail, SQL
fragment, filesystem path or token never reaches a response or a log line.

Response shapes are exact: find is one of ``not_dispatched``,
``dispatched`` or ``absent``; status carries the five-state view; result is
either ``available`` or ``unavailable``. Unknown identities answer
``404 {"error": "not_found"}``.

# noqa: SIZE_OK - one authenticated execution-control router and its fixed wire map.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter

from src.api.strategy_run_auth import require_strategy_run_token
from src.strategy_runs.control_contract import (
    BacktestStartRequest,
    ExecutionStartRequest,
    StopRequest,
)
from src.strategy_runs.control_service import (
    ControlProjectionError,
    ExecutionControlService,
    FindProjection,
    ResultProjection,
    StatusProjection,
)
from src.strategy_runs.execution_start_service import (
    ExecutionStartError,
    ExecutionStartService,
)
from src.strategy_runs.execution_stop_service import (
    ExecutionStopError,
    ExecutionStopService,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["execution-control"])

#: ``app.state`` attributes holding the zero-argument service providers.
_PROVIDER_ATTR = "execution_control_service_provider"
_START_PROVIDER_ATTR = "execution_start_service_provider"
_STOP_PROVIDER_ATTR = "execution_stop_service_provider"

#: Fixed status for each projection failure kind.
_ERROR_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "identity_conflict": status.HTTP_503_SERVICE_UNAVAILABLE,
    "prepare_required": status.HTTP_409_CONFLICT,
    "idempotency_conflict": status.HTTP_409_CONFLICT,
    "input_digest_mismatch": status.HTTP_409_CONFLICT,
    "storage_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "worker_start_failed": status.HTTP_503_SERVICE_UNAVAILABLE,
    "invalid_request": status.HTTP_400_BAD_REQUEST,
}
_STORAGE_UNAVAILABLE = "storage_unavailable"
_START_ADAPTER = TypeAdapter(ExecutionStartRequest)
_BACKTEST_START_ADAPTER = TypeAdapter(BacktestStartRequest)
_STOP_ADAPTER = TypeAdapter(StopRequest)

ControlProvider = Callable[[], ExecutionControlService]
ExecutionStartProvider = Callable[[], ExecutionStartService]
ExecutionStopProvider = Callable[[], ExecutionStopService]


def _json(status_code: int, payload: dict[str, Any]) -> JSONResponse:
    """Return an exact JSON body, bypassing any response-model wrapping."""
    return JSONResponse(status_code=status_code, content=payload)


def _error_response(
    error: ControlProjectionError | ExecutionStartError | ExecutionStopError,
) -> JSONResponse:
    """Map a typed projection failure onto its fixed body and status."""
    kind = error.kind if error.kind in _ERROR_STATUS else _STORAGE_UNAVAILABLE
    body: dict[str, Any] = {"error": kind}
    original = getattr(error, "original_remote_id", None)
    if original is not None:
        body["original_remote_id"] = original
    return _json(_ERROR_STATUS[kind], body)


def _unavailable() -> JSONResponse:
    """Return the fixed 503 used when the provider cannot be constructed."""
    return _json(status.HTTP_503_SERVICE_UNAVAILABLE, {"error": _STORAGE_UNAVAILABLE})


def default_control_service_provider(app: FastAPI) -> ControlProvider:
    """Build the production provider, closed over the host app.

    ``ControlStore`` is imported lazily so importing this module neither opens
    the ledger nor resolves its path; a request that cannot build the service
    answers the fixed ``503`` instead of serving a half-built control plane.

    Args:
        app: Host application carrying optional execution handle state.

    Returns:
        A zero-argument callable returning an ``ExecutionControlService``.
    """

    def provider() -> ExecutionControlService:
        from src.strategy_runs.control_service import ExecutionControlService
        from src.strategy_runs.control_store import ControlStore

        handle_provider = getattr(app.state, "execution_handle_provider", None)
        return ExecutionControlService(
            ControlStore(),
            legacy_store=ControlStore(),
            handle_provider=handle_provider or None,
        )

    return provider


def default_execution_start_service_provider() -> ExecutionStartProvider:
    """Build the lazy production start provider with native supervision."""

    def provider() -> ExecutionStartService:
        from src.strategy_runs.control_store import ControlStore
        from src.strategy_runs.native_supervisor import NativeSupervisor

        return ExecutionStartService(ControlStore(), NativeSupervisor(ControlStore()))

    return provider


def default_execution_stop_service_provider() -> ExecutionStopProvider:
    """Build the lazy production stop provider with native supervision."""

    def provider() -> ExecutionStopService:
        from src.strategy_runs.control_store import ControlStore
        from src.strategy_runs.native_supervisor import NativeSupervisor

        store = ControlStore()
        return ExecutionStopService(store, NativeSupervisor(store))

    return provider


def _resolve_service(request: Request) -> ExecutionControlService:
    """Resolve the registered provider once per request, failing closed."""
    provider = getattr(request.app.state, _PROVIDER_ATTR, None)
    if not callable(provider):
        raise ControlProjectionError(_STORAGE_UNAVAILABLE, "provider not configured")
    try:
        return provider()
    except ControlProjectionError:
        raise
    except Exception as exc:  # noqa: BLE001 — construction failure is entry fault
        # Only the exception type is logged: a message may carry a path.
        logger.error("execution-control service unavailable (%s)", type(exc).__name__)
        raise ControlProjectionError(
            _STORAGE_UNAVAILABLE, "provider construction failed"
        ) from exc


def _resolve_start_service(request: Request) -> ExecutionStartService:
    provider = getattr(request.app.state, _START_PROVIDER_ATTR, None)
    if not callable(provider):
        raise ExecutionStartError(_STORAGE_UNAVAILABLE)
    try:
        return provider()
    except ExecutionStartError:
        raise
    except Exception as exc:  # noqa: BLE001 — construction failure is entry fault
        logger.error("execution-start service unavailable (%s)", type(exc).__name__)
        raise ExecutionStartError(_STORAGE_UNAVAILABLE) from exc


def _resolve_stop_service(request: Request) -> ExecutionStopService:
    provider = getattr(request.app.state, _STOP_PROVIDER_ATTR, None)
    if not callable(provider):
        raise ExecutionStopError(_STORAGE_UNAVAILABLE)
    try:
        return provider()
    except ExecutionStopError:
        raise
    except Exception as exc:  # noqa: BLE001 — construction failure is entry fault
        logger.error("execution-stop service unavailable (%s)", type(exc).__name__)
        raise ExecutionStopError(_STORAGE_UNAVAILABLE) from exc


def _find_payload(projection: FindProjection) -> dict[str, Any]:
    """Project a find result onto exactly one of the three recovery states."""
    if projection.state == "dispatched":
        return {
            "state": projection.state,
            "dispatch_key": projection.dispatch_key,
            "remote_id": projection.remote_id,
            "status": projection.status,
            "run_dir": projection.run_dir,
            "started_at": projection.started_at,
            "finished_at": projection.finished_at,
            "error_kind": projection.error_kind,
        }
    if projection.state == "absent":
        return {
            "state": projection.state,
            "dispatch_key": projection.dispatch_key,
            "remote_id": projection.remote_id,
            "last_status": projection.last_status,
            "absence_evidence": projection.absence_evidence,
            "observed_at": projection.observed_at,
        }
    return {"state": projection.state, "dispatch_key": projection.dispatch_key}


def _status_payload(projection: StatusProjection) -> dict[str, Any]:
    """Project one status read onto its exact eight-field body."""
    return {
        "remote_id": projection.remote_id,
        "dispatch_key": projection.dispatch_key,
        "status": projection.status,
        "started_at": projection.started_at,
        "finished_at": projection.finished_at,
        "error_kind": projection.error_kind,
        "run_dir": projection.run_dir,
        "contract_version": projection.contract_version,
    }


def _result_payload(projection: ResultProjection) -> dict[str, Any]:
    """Project one bounded artifact read onto its exact seven-field body."""
    return {
        "remote_id": projection.remote_id,
        "status": projection.status,
        "result_status": projection.result_status,
        "result": projection.result,
        "progress": projection.progress,
        "has_more": projection.has_more,
        "contract_version": projection.contract_version,
    }


async def _start_with_adapter(
    request: Request,
    adapter: TypeAdapter[BacktestStartRequest] | TypeAdapter[ExecutionStartRequest],
    *,
    require_frozen_backtest: bool,
) -> JSONResponse:
    try:
        start_request = adapter.validate_python(await request.json())
    except (TypeError, ValueError):
        return _json(status.HTTP_400_BAD_REQUEST, {"error": "invalid_request"})
    try:
        result = _resolve_start_service(request).start(
            start_request, require_frozen_backtest=require_frozen_backtest
        )
    except ExecutionStartError as exc:
        return _error_response(exc)
    except Exception as exc:  # noqa: BLE001 — an entry fault has a fixed body
        logger.error("execution start failed (%s)", type(exc).__name__)
        return _unavailable()
    return _json(
        status.HTTP_202_ACCEPTED,
        {
            "remote_id": result.remote_id,
            "dispatch_key": result.dispatch_key,
            "kind": result.kind,
            "status": result.status,
            "replayed": result.replayed,
            "contract_version": result.contract_version,
        },
    )


@router.post(
    "/backtest/execute",
    dependencies=[Depends(require_strategy_run_token)],
    response_model=None,
)
async def execute_backtest(request: Request) -> JSONResponse:
    """Execute only a prepared backtest through the generic start seam."""
    return await _start_with_adapter(
        request, _BACKTEST_START_ADAPTER, require_frozen_backtest=True
    )


@router.post(
    "/execution-instances",
    dependencies=[Depends(require_strategy_run_token)],
    response_model=None,
)
async def start_execution_instance(request: Request) -> JSONResponse:
    """Start one prepared backtest or frozen factor declaration by kind."""
    return await _start_with_adapter(
        request, _START_ADAPTER, require_frozen_backtest=False
    )


@router.post(
    "/execution-instances/{remote_id}/stop",
    dependencies=[Depends(require_strategy_run_token)],
    response_model=None,
)
async def stop_execution_instance(remote_id: str, request: Request) -> JSONResponse:
    """Resolve exactly one idempotent stop tuple for an original remote id."""
    try:
        stop_request = _STOP_ADAPTER.validate_python(await request.json())
    except (TypeError, ValueError):
        return _json(status.HTTP_400_BAD_REQUEST, {"error": "invalid_request"})
    if stop_request.remote_id != remote_id:
        return _json(status.HTTP_400_BAD_REQUEST, {"error": "invalid_request"})
    try:
        result = _resolve_stop_service(request).stop(stop_request)
    except ExecutionStopError as exc:
        return _error_response(exc)
    except Exception as exc:  # noqa: BLE001 — an entry fault has a fixed body
        logger.error("execution stop failed (%s)", type(exc).__name__)
        return _unavailable()
    return _json(
        status.HTTP_202_ACCEPTED,
        {
            "remote_id": result.remote_id,
            "dispatch_key": result.dispatch_key,
            "stop_key": result.stop_key,
            "stop_state": result.stop_state,
            "evidence_kind": result.evidence_kind,
            "resolved_at": result.resolved_at,
            "replayed": result.replayed,
            "contract_version": result.contract_version,
        },
    )


@router.get(
    "/execution-instances",
    dependencies=[Depends(require_strategy_run_token)],
    response_model=None,
)
def find_execution_instance(
    request: Request,
    dispatch_key: str = Query(...),
) -> JSONResponse:
    """Resolve one client dispatch key without starting or stopping work."""
    try:
        projection = _resolve_service(request).find(dispatch_key)
    except ControlProjectionError as exc:
        return _error_response(exc)
    except Exception as exc:  # noqa: BLE001 — a read fault is an entry fault
        logger.error("execution find failed (%s)", type(exc).__name__)
        return _unavailable()
    return _json(status.HTTP_200_OK, _find_payload(projection))


@router.get(
    "/execution-instances/{remote_id}",
    dependencies=[Depends(require_strategy_run_token)],
    response_model=None,
)
def get_execution_instance(remote_id: str, request: Request) -> JSONResponse:
    """Read the five-state status of one original remote id."""
    try:
        projection = _resolve_service(request).status(remote_id)
    except ControlProjectionError as exc:
        return _error_response(exc)
    except Exception as exc:  # noqa: BLE001 — a read fault is an entry fault
        logger.error("execution status read failed (%s)", type(exc).__name__)
        return _unavailable()
    return _json(status.HTTP_200_OK, _status_payload(projection))


@router.get(
    "/execution-instances/{remote_id}/result",
    dependencies=[Depends(require_strategy_run_token)],
    response_model=None,
)
def get_execution_instance_result(remote_id: str, request: Request) -> JSONResponse:
    """Read the bounded result artifact of one original remote id."""
    try:
        projection = _resolve_service(request).result(remote_id)
    except ControlProjectionError as exc:
        return _error_response(exc)
    except Exception as exc:  # noqa: BLE001 — a read fault is an entry fault
        logger.error("execution result read failed (%s)", type(exc).__name__)
        return _unavailable()
    return _json(status.HTTP_200_OK, _result_payload(projection))


def register_execution_control_routes(
    app: FastAPI,
    provider: ControlProvider | None = None,
    start_provider: ExecutionStartProvider | None = None,
    stop_provider: ExecutionStopProvider | None = None,
) -> None:
    """Mount execution-instance reads, starts and stops with lazy providers."""
    setattr(
        app.state,
        _PROVIDER_ATTR,
        provider or default_control_service_provider(app),
    )
    setattr(
        app.state,
        _START_PROVIDER_ATTR,
        start_provider or default_execution_start_service_provider(),
    )
    setattr(
        app.state,
        _STOP_PROVIDER_ATTR,
        stop_provider or default_execution_stop_service_provider(),
    )
    app.include_router(router)


__all__ = [
    "ControlProvider",
    "ExecutionStartProvider",
    "ExecutionStopProvider",
    "default_control_service_provider",
    "default_execution_start_service_provider",
    "default_execution_stop_service_provider",
    "register_execution_control_routes",
    "router",
]
