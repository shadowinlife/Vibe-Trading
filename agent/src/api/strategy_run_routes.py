"""Frozen HTTP surface for RM-triggered strategy runs.

Two routes only: ``POST /strategy-runs`` starts one version-pinned run and
returns its opaque ``vt_run_id``; ``GET /strategy-runs/{vt_run_id}`` returns
the three-field status view. Handlers here do exactly four things — service
token auth (via :mod:`src.api.strategy_run_auth`), request-model validation,
one call into an injected service object, and mapping the result onto the
frozen response shape. Business logic (idempotency claim, runner dispatch,
persistence, sweeper) lives behind the service seam below.

Service seam (implemented by ``src.strategy_runs.service``)::

    async def submit(command: StrategyRunCommand) -> object  # has .vt_run_id
    def status(vt_run_id: str) -> object | None              # .status/.error/.error_kind

``submit`` raises :class:`DuplicateRunKeyError` when the ``run_key`` was
already claimed. The default provider imports ``src.strategy_runs.service``
lazily inside the request path, so this module has no import-time side effects
and the T3/T4 package may not exist yet.

Response shapes are exact: ``202 {"vt_run_id": ...}``,
``409 {"error": "duplicate_run_key", "vt_run_id": ...}`` (top level, never
wrapped in ``detail``), and ``200 {"status", "error", "error_kind"}``.

A syntactically invalid JSON body is rejected by FastAPI's body parser (422)
before the auth dependency runs; a syntactically valid body with model
violations still authenticates first (401/403).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from src.api.strategy_run_auth import require_strategy_run_token
from src.strategy_library.paths import validate_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["strategy-runs"])

#: ``app.state`` attribute holding the callable that builds the service.
_SERVICE_PROVIDER_ATTR = "strategy_run_service_provider"

DUPLICATE_RUN_KEY = "duplicate_run_key"
ENTRY_UNAVAILABLE = "entry unavailable"

_RUN_KEY_MAX_LENGTH = 512
_DEFAULT_TIMEOUT_S = 3600
_MIN_TIMEOUT_S = 60
_MAX_TIMEOUT_S = 86400
_DEFAULT_FAILURE = "strategy run failed"


class DuplicateRunKeyError(Exception):
    """Raised by the service when ``run_key`` already owns a run.

    Attributes:
        vt_run_id: The id of the run that already claimed the key; it is
            returned unchanged in the ``409`` body.
    """

    def __init__(self, vt_run_id: str) -> None:
        super().__init__("duplicate run_key")
        self.vt_run_id = vt_run_id


@dataclass(frozen=True, slots=True)
class StrategyRunCommand:
    """Validated, transport-neutral input handed to the service."""

    strategy_id: str
    strategy_version: str
    run_key: str
    sandbox_id: str | None = None
    upstream: str | None = None
    timeout_s: int = _DEFAULT_TIMEOUT_S


@runtime_checkable
class StrategyRunService(Protocol):
    """The seam the route module depends on; see the module docstring."""

    async def submit(self, command: StrategyRunCommand) -> Any:
        """Claim and start one run; return an object carrying ``vt_run_id``."""
        ...

    def status(self, vt_run_id: str) -> Any | None:
        """Return the persisted status row, or ``None`` for an unknown id."""
        ...


ServiceProvider = Callable[[], StrategyRunService]


class StrategyRunRequest(BaseModel):
    """Body of ``POST /strategy-runs`` (frozen contract §3.1)."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    strategy_version: str
    # Opaque idempotency key: never trimmed, split or normalised here.
    run_key: str = Field(min_length=1, max_length=_RUN_KEY_MAX_LENGTH)
    sandbox_id: str | None = Field(default=None, min_length=1)
    upstream: str | None = None
    timeout_s: int = Field(
        default=_DEFAULT_TIMEOUT_S, ge=_MIN_TIMEOUT_S, le=_MAX_TIMEOUT_S
    )

    @field_validator("strategy_id", "strategy_version")
    @classmethod
    def _valid_path_id(cls, value: str, info: ValidationInfo) -> str:
        """Reuse the F9 identifier rule so ids cannot become path segments."""
        return validate_id(value, what=info.field_name)

    @field_validator("sandbox_id")
    @classmethod
    def _non_blank_sandbox(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("sandbox_id must not be blank")
        return value

    @field_validator("upstream")
    @classmethod
    def _http_url_without_userinfo(cls, value: str | None) -> str | None:
        """Accept only an absolute http(s) URL with a host and no userinfo."""
        if value is None:
            return None
        if not value.strip():
            raise ValueError("upstream must not be blank")
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise ValueError("upstream must be a valid URL") from exc
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("upstream must use http or https")
        if not parsed.hostname:
            raise ValueError("upstream must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("upstream must not contain userinfo")
        return value

    @model_validator(mode="after")
    def _target_present(self) -> "StrategyRunRequest":
        """Require at least one way to locate the target box."""
        if not self.sandbox_id and not self.upstream:
            raise ValueError("one of sandbox_id or upstream is required")
        return self


def _json(status_code: int, payload: dict[str, Any]) -> JSONResponse:
    """Return an exact JSON body, bypassing any response-model wrapping."""
    return JSONResponse(status_code=status_code, content=payload)


def _entry_unavailable() -> HTTPException:
    """Return the fixed 503 used when the service cannot be constructed."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=ENTRY_UNAVAILABLE,
    )


def default_service_provider() -> StrategyRunService:
    """Build the production service, importing the T3/T4 package lazily.

    Kept out of module import scope on purpose: ``src.strategy_runs`` is owned
    by a parallel task and the route module must import cleanly (with no file
    or database access) before that package exists.
    """
    from src.strategy_runs.service import build_service

    return build_service()


def _resolve_service(request: Request) -> StrategyRunService:
    """Resolve the registered provider once per request, failing closed."""
    provider = getattr(request.app.state, _SERVICE_PROVIDER_ATTR, None)
    if not callable(provider):
        raise _entry_unavailable()
    try:
        return provider()
    except HTTPException:
        raise
    except (
        Exception
    ) as exc:  # noqa: BLE001 — any construction failure is "entry unavailable"
        # Only the exception type is logged: a message may carry request data.
        logger.error("strategy-run service unavailable (%s)", type(exc).__name__)
        raise _entry_unavailable() from exc


def _vt_run_id(result: Any) -> str:
    """Read ``vt_run_id`` off a submit result; blank is a service failure."""
    value = getattr(result, "vt_run_id", None)
    return value if isinstance(value, str) else ""


def _status_payload(row: Any) -> dict[str, Any]:
    """Project a status row onto exactly ``status``/``error``/``error_kind``.

    Defensive on read: the three frozen states are the only ones that may ever
    leave the process. An unexpected runner state collapses to ``failed`` with
    ``runner_error``, and a ``failed`` row always carries both non-empty text
    fields, so the wire shape cannot depend on a service bug.
    """
    run_status = getattr(row, "status", None)
    if run_status in {"running", "completed"}:
        return {"status": run_status, "error": None, "error_kind": None}
    if run_status != "failed":
        return {
            "status": "failed",
            "error": _DEFAULT_FAILURE,
            "error_kind": "runner_error",
        }
    error = getattr(row, "error", None)
    error_kind = getattr(row, "error_kind", None)
    return {
        "status": "failed",
        "error": (
            error if isinstance(error, str) and error.strip() else _DEFAULT_FAILURE
        ),
        "error_kind": (
            error_kind
            if isinstance(error_kind, str) and error_kind.strip()
            else "runner_error"
        ),
    }


@router.post(
    "/strategy-runs",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_strategy_run_token)],
    response_model=None,
)
async def create_strategy_run(
    body: StrategyRunRequest, request: Request
) -> JSONResponse:
    """Claim ``run_key`` and start one run; return only ``vt_run_id``."""
    service = _resolve_service(request)
    command = StrategyRunCommand(
        strategy_id=body.strategy_id,
        strategy_version=body.strategy_version,
        run_key=body.run_key,
        sandbox_id=body.sandbox_id,
        upstream=body.upstream,
        timeout_s=body.timeout_s,
    )
    try:
        result = await service.submit(command)
    except HTTPException:
        raise
    except DuplicateRunKeyError as exc:
        return _json(
            status.HTTP_409_CONFLICT,
            {"error": DUPLICATE_RUN_KEY, "vt_run_id": str(exc.vt_run_id)},
        )
    except Exception as exc:  # noqa: BLE001 — an entry fault, never a run state
        logger.error("strategy-run submit failed (%s)", type(exc).__name__)
        raise _entry_unavailable() from exc
    vt_run_id = _vt_run_id(result)
    if not vt_run_id:
        logger.error("strategy-run submit returned no vt_run_id")
        raise _entry_unavailable()
    return _json(status.HTTP_202_ACCEPTED, {"vt_run_id": vt_run_id})


@router.get(
    "/strategy-runs/{vt_run_id}",
    dependencies=[Depends(require_strategy_run_token)],
    response_model=None,
)
def get_strategy_run(vt_run_id: str, request: Request) -> JSONResponse:
    """Read one persisted status; never starts, polls or mutates a run."""
    service = _resolve_service(request)
    try:
        row = service.status(vt_run_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — a read fault is an entry fault
        logger.error("strategy-run status read failed (%s)", type(exc).__name__)
        raise _entry_unavailable() from exc
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="unknown vt_run_id"
        )
    return _json(status.HTTP_200_OK, _status_payload(row))


def register_strategy_run_routes(
    app: FastAPI,
    service_provider: ServiceProvider | None = None,
) -> None:
    """Mount the two routes and remember how to build their service.

    Args:
        app: Host application.
        service_provider: Zero-argument callable returning a
            :class:`StrategyRunService`. Defaults to the lazy production
            provider; tests inject a fake here.
    """
    setattr(
        app.state, _SERVICE_PROVIDER_ATTR, service_provider or default_service_provider
    )
    app.include_router(router)


__all__ = [
    "DUPLICATE_RUN_KEY",
    "ENTRY_UNAVAILABLE",
    "DuplicateRunKeyError",
    "StrategyRunCommand",
    "StrategyRunRequest",
    "StrategyRunService",
    "default_service_provider",
    "register_strategy_run_routes",
    "router",
]
