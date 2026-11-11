"""Authenticated HTTP surface for one frozen factor prototype start.

``POST /factor/prototype`` accepts only the strict :class:`FactorStartRequest`
wire contract (``dispatch_key``, ``kind``, ``spec`` and ``contract_version``).
Every invalid declaration — unknown candidate, caller-supplied run directory or
worker fields, malformed chronology, out-of-range bounds — is rejected before
the start service can claim a dispatch or spawn a worker.

The accepted body is the same 202 projection as the generic start seam. Results
are read through ``GET /execution-instances/{remote_id}/result`` so the worker's
bounded ``result.json`` remains the single result authority; this module never
serves HTML or discloses a run directory.

The service provider is shared with the execution-control routes through
``app.state.execution_start_service_provider``, so both entrances dispatch
through one validated seam. Error bodies are fixed text: a path, token or
internal detail never reaches a response or a log line.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter

from src.api.execution_control_routes import (
    ExecutionStartProvider,
    default_execution_start_service_provider,
)
from src.api.strategy_run_auth import require_strategy_run_token
from src.strategy_runs.control_contract import CONTRACT_VERSION, FactorStartRequest
from src.strategy_runs.execution_start_service import (
    ExecutionStartError,
    ExecutionStartService,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["execution-control"])

#: Shared with ``execution_control_routes``; one start provider per app.
_START_PROVIDER_ATTR = "execution_start_service_provider"
_STORAGE_UNAVAILABLE = "storage_unavailable"

#: Fixed status for each start failure kind. Unknown kinds fail closed.
_ERROR_STATUS = {
    "prepare_required": status.HTTP_409_CONFLICT,
    "idempotency_conflict": status.HTTP_409_CONFLICT,
    "input_digest_mismatch": status.HTTP_409_CONFLICT,
    "invalid_request": status.HTTP_400_BAD_REQUEST,
    "storage_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "worker_start_failed": status.HTTP_503_SERVICE_UNAVAILABLE,
}

_FACTOR_START_ADAPTER = TypeAdapter(FactorStartRequest)


def _json(status_code: int, payload: dict[str, Any]) -> JSONResponse:
    """Return an exact JSON body, bypassing any response-model wrapping."""
    return JSONResponse(status_code=status_code, content=payload)


def _error_response(error: ExecutionStartError) -> JSONResponse:
    """Map a typed start failure onto its fixed body and status."""
    kind = error.kind if error.kind in _ERROR_STATUS else _STORAGE_UNAVAILABLE
    body: dict[str, Any] = {"error": kind, "contract_version": CONTRACT_VERSION}
    original = getattr(error, "original_remote_id", None)
    if original is not None:
        body["original_remote_id"] = original
    return _json(_ERROR_STATUS[kind], body)


def _invalid_request() -> JSONResponse:
    """Return the fixed 400 for a malformed or inconsistent declaration."""
    return _json(
        status.HTTP_400_BAD_REQUEST,
        {"error": "invalid_request", "contract_version": CONTRACT_VERSION},
    )


def _unavailable() -> JSONResponse:
    """Return the fixed 503 used when the provider cannot be constructed."""
    return _json(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        {"error": _STORAGE_UNAVAILABLE, "contract_version": CONTRACT_VERSION},
    )


def _resolve_start_service(request: Request) -> ExecutionStartService:
    """Resolve the shared provider once per request, failing closed."""
    provider = getattr(request.app.state, _START_PROVIDER_ATTR, None)
    if not callable(provider):
        raise ExecutionStartError(_STORAGE_UNAVAILABLE)
    try:
        return provider()
    except ExecutionStartError:
        raise
    except Exception as exc:  # noqa: BLE001 — construction failure is entry fault
        logger.error("factor-prototype start unavailable (%s)", type(exc).__name__)
        raise ExecutionStartError(_STORAGE_UNAVAILABLE) from exc


@router.post(
    "/factor/prototype",
    dependencies=[Depends(require_strategy_run_token)],
    response_model=None,
)
async def start_factor_prototype(request: Request) -> JSONResponse:
    """Start or replay exactly one frozen factor prototype declaration."""
    try:
        start_request = _FACTOR_START_ADAPTER.validate_python(await request.json())
    except (TypeError, ValueError):
        return _invalid_request()
    try:
        result = _resolve_start_service(request).start(start_request)
    except ExecutionStartError as exc:
        return _error_response(exc)
    except Exception as exc:  # noqa: BLE001 — an entry fault has a fixed body
        logger.error("factor prototype start failed (%s)", type(exc).__name__)
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


def register_factor_prototype_routes(
    app: FastAPI,
    provider: ExecutionStartProvider | None = None,
) -> None:
    """Mount the factor prototype start on the app's shared start provider."""
    if provider is not None:
        setattr(app.state, _START_PROVIDER_ATTR, provider)
    elif not callable(getattr(app.state, _START_PROVIDER_ATTR, None)):
        setattr(
            app.state,
            _START_PROVIDER_ATTR,
            default_execution_start_service_provider(),
        )
    app.include_router(router)


__all__ = [
    "register_factor_prototype_routes",
    "router",
]
