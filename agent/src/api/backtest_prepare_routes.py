"""Authenticated HTTP surface for frozen backtest materialization."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import assert_never

from fastapi import APIRouter, Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.api.strategy_run_auth import require_strategy_run_token
from src.strategy_runs.backtest_config import BacktestConfigValidationError
from src.strategy_runs.backtest_materialize import (
    BacktestMaterializer,
    BacktestPrepareError,
    BacktestPrepareRequest,
)
from src.strategy_runs.control_contract import ContractValidationError
from src.strategy_runs.control_store import ControlStore

logger = logging.getLogger(__name__)
router = APIRouter(tags=["backtest-prepare"])
PrepareProvider = Callable[[], BacktestMaterializer]
_PROVIDER_ATTR = "backtest_prepare_provider"

__all__ = [
    "PrepareProvider",
    "default_prepare_provider",
    "register_backtest_prepare_routes",
    "router",
]


def _json(status_code: int, error: str, remote_id: str | None = None) -> JSONResponse:
    body = {"error": error}
    if remote_id is not None:
        body["remote_id"] = remote_id
    return JSONResponse(status_code=status_code, content=body)


def _unavailable() -> JSONResponse:
    return _json(status.HTTP_503_SERVICE_UNAVAILABLE, "storage_unavailable")


def _error_response(error: BacktestPrepareError) -> JSONResponse:
    """Map typed domain failures to fixed, detail-free wire bodies."""
    match error.kind:
        case "invalid_input":
            return _json(status.HTTP_400_BAD_REQUEST, error.kind)
        case "strategy_not_found" | "version_not_found":
            return _json(status.HTTP_404_NOT_FOUND, error.kind)
        case "input_digest_mismatch":
            return _json(status.HTTP_409_CONFLICT, error.kind)
        case "idempotency_conflict":
            return _json(status.HTTP_409_CONFLICT, error.kind, error.remote_id)
        case "storage_unavailable":
            return _unavailable()
        case unreachable:
            assert_never(unreachable)


def default_prepare_provider() -> PrepareProvider:
    """Return a lazy production provider; importing the app opens no ledger."""

    def provider() -> BacktestMaterializer:
        return BacktestMaterializer(ControlStore())

    return provider


def _resolve_materializer(request: Request) -> BacktestMaterializer:
    provider = getattr(request.app.state, _PROVIDER_ATTR, None)
    if not callable(provider):
        raise BacktestPrepareError("storage_unavailable")
    try:
        return provider()
    except Exception as exc:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        logger.error("backtest prepare provider unavailable (%s)", type(exc).__name__)
        raise BacktestPrepareError("storage_unavailable") from exc


@router.post(
    "/backtest/prepare",
    dependencies=[Depends(require_strategy_run_token)],
    response_model=None,
)
async def prepare_backtest(request: Request) -> JSONResponse:
    """Validate and freeze declared inputs without fetching data or starting compute."""
    try:
        parsed = BacktestPrepareRequest.model_validate(await request.json())
        response = _resolve_materializer(request).prepare(parsed)
    except BacktestPrepareError as exc:
        return _error_response(exc)
    except (
        json.JSONDecodeError,
        ValidationError,
        BacktestConfigValidationError,
        ContractValidationError,
        UnicodeError,
    ):
        return _json(status.HTTP_400_BAD_REQUEST, "invalid_input")
    except Exception as exc:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        logger.error("backtest prepare failed (%s)", type(exc).__name__)
        return _unavailable()
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=response.model_dump(mode="json"),
    )


def register_backtest_prepare_routes(
    app: FastAPI, provider: PrepareProvider | None = None
) -> None:
    """Mount the additive prepare route and its per-request provider."""
    setattr(
        app.state,
        _PROVIDER_ATTR,
        provider if provider is not None else default_prepare_provider(),
    )
    app.include_router(router)
