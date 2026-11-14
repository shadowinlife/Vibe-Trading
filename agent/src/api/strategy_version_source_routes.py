"""Authenticated exact source read for one owner-bound strategy version.

One route, ``POST /strategy-version-source``, returns the exact bytes of
``code/signal_engine.py`` for one ``(owner, strategy_id, strategy_version)``.
Resolution order is frozen: service token, deployment owner binding, request
body, then the reader.  A failure in an earlier step never constructs the
reader, resolves the database or looks up a registration.

The handler parses the body by hand so a malformed JSON document is a typed
``400`` instead of FastAPI's default ``422``; schema violations are ``422``.
Both use ``{"error": {"code": "invalid_input"}}``.  Authentication failures use
typed ``unauthorized``/``forbidden`` envelopes, while a missing or untrusted
deployment binding keeps the fixed ``403 {"detail": "forbidden"}`` shape.

Storage, lock, OS and SQLite failures collapse to one typed ``503``; exception
messages may contain paths, so only the exception type is logged.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Final, Protocol

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from src.api.strategy_owner_binding import OwnerUserId, resolve_deployment_owner
from src.api.strategy_run_auth import WWW_AUTHENTICATE, require_strategy_run_token
from src.strategy_library.paths import validate_id
from src.strategy_versions.errors import (
    StrategyNotFoundError,
    VersionDigestMismatchError,
    VersionNotFoundError,
)
from src.strategy_versions.models import RegistrationKey
from src.strategy_versions.reader import (
    StrategyVersionSource,
    StrategyVersionSourceReader,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["strategy-version-source"])

CONTRACT_VERSION: Final = "strategy-version-source-v1"
PROVIDER_ATTR: Final = "strategy_version_source_provider"
_DIGEST_PATTERN: Final = r"^[0-9a-f]{64}$"


class StrategyVersionSourceProvider(Protocol):
    """Zero-argument factory for the reader, resolved per request."""

    def __call__(self) -> StrategyVersionSourceReader:
        """Return a reader bound to the production registry and runtime root."""
        ...


class StrategyVersionSourceRequest(BaseModel):
    """Closed body of ``POST /strategy-version-source``."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    strategy_id: str
    strategy_version: str
    expected_source_sha256: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @field_validator("strategy_id", "strategy_version")
    @classmethod
    def _valid_path_id(cls, value: str, info: ValidationInfo) -> str:
        """Reuse the F9 identifier rule so ids cannot become path segments."""
        return validate_id(value, what=info.field_name)


class _InvalidBody(Exception):
    """A body that failed at the JSON (400) or schema (422) boundary."""

    def __init__(self, status_code: int) -> None:
        super().__init__("invalid request body")
        self.status_code = status_code


class _ProviderUnavailable(Exception):
    """The registered provider is missing or did not return a reader."""


def _error(status_code: int, code: str, headers: dict[str, str] | None = None) -> JSONResponse:
    """Return the typed error envelope without any lookup detail."""
    return JSONResponse(status_code=status_code, content={"error": {"code": code}}, headers=headers)


def _parse_body(raw: bytes) -> StrategyVersionSourceRequest:
    """Parse the body, separating malformed JSON from schema violations."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _InvalidBody(status.HTTP_400_BAD_REQUEST) from exc
    try:
        return StrategyVersionSourceRequest.model_validate(payload)
    except ValidationError as exc:
        raise _InvalidBody(status.HTTP_422_UNPROCESSABLE_ENTITY) from exc


def _authenticate(request: Request) -> str | JSONResponse:
    """Run the service-token check once and return the credential or refusal."""
    if len(request.headers.getlist("authorization")) > 1:
        return _error(status.HTTP_401_UNAUTHORIZED, "unauthorized", WWW_AUTHENTICATE)
    try:
        return require_strategy_run_token(request)
    except HTTPException as exc:
        code = "unauthorized" if exc.status_code == 401 else "forbidden"
        headers = WWW_AUTHENTICATE if exc.status_code == 401 else None
        return _error(exc.status_code, code, headers)


def _reader_for_request(request: Request) -> StrategyVersionSourceReader:
    """Resolve the provider once, only after authentication and binding."""
    provider = getattr(request.app.state, PROVIDER_ATTR, None)
    if not callable(provider):
        raise _ProviderUnavailable()
    reader = provider()
    if not callable(getattr(reader, "read", None)):
        raise _ProviderUnavailable()
    return reader


def _payload(
    request_body: StrategyVersionSourceRequest,
    owner: OwnerUserId,
    result: StrategyVersionSource,
) -> dict[str, object]:
    """Project the reader result onto exactly the frozen success fields."""
    return {
        "contract_version": CONTRACT_VERSION,
        "strategy_id": request_body.strategy_id,
        "strategy_version": request_body.strategy_version,
        "version_id": result.registration.version_id,
        "owner_user_id": owner,
        "caller_scope": "owner",
        "source_path": result.registration.source.source_path,
        "source_encoding": result.source_encoding,
        "source_byte_length": result.source_byte_length,
        "source_bytes_base64": base64.b64encode(result.source_bytes).decode("ascii"),
        "source_sha256": result.source_sha256,
    }


@router.post("/strategy-version-source", response_model=None)
async def read_strategy_version_source(request: Request) -> JSONResponse:
    """Return the exact source bytes for one owner-bound version."""
    presented = _authenticate(request)
    if isinstance(presented, JSONResponse):
        return presented

    owner = resolve_deployment_owner(presented)

    try:
        body = _parse_body(await request.body())
    except _InvalidBody as exc:
        return _error(exc.status_code, "invalid_input")

    key = RegistrationKey.parse(owner, body.strategy_id, body.strategy_version)
    try:
        result = _reader_for_request(request).read(key, body.expected_source_sha256)
    except StrategyNotFoundError:
        return _error(status.HTTP_404_NOT_FOUND, "strategy_not_found")
    except VersionNotFoundError:
        return _error(status.HTTP_404_NOT_FOUND, "version_not_found")
    except VersionDigestMismatchError:
        return _error(status.HTTP_409_CONFLICT, "input_digest_mismatch")
    except Exception as exc:  # storage/lock/OS/SQLite collapse
        logger.error("strategy-version source read failed (%s)", type(exc).__name__)
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "storage_unavailable")

    return JSONResponse(status_code=status.HTTP_200_OK, content=_payload(body, owner, result))


def default_version_source_provider() -> StrategyVersionSourceReader:
    """Resolve the production registry and runtime root lazily.

    Called only after authentication and owner binding, so a refused request
    never touches the strategy-store database or the runtime root.
    """
    from src.config.paths import get_runtime_root
    from src.strategy_store.sqlite_store import _default_db_path
    from src.strategy_store.version_registry import SqliteVersionRegistry

    registry = SqliteVersionRegistry(_default_db_path(), read_only=True)
    return StrategyVersionSourceReader(registry, get_runtime_root())


def register_strategy_version_source_routes(
    app: FastAPI,
    provider: StrategyVersionSourceProvider | None = None,
) -> None:
    """Mount the source-read route with an optional injected provider.

    Args:
        app: Host application.
        provider: Zero-argument callable returning a
            :class:`StrategyVersionSourceReader`. Defaults to the lazy
            production provider; tests inject a fake to prove it is never
            constructed on refusal.
    """
    setattr(app.state, PROVIDER_ATTR, provider or default_version_source_provider)
    app.include_router(router)


__all__ = [
    "CONTRACT_VERSION",
    "PROVIDER_ATTR",
    "StrategyVersionSourceProvider",
    "StrategyVersionSourceRequest",
    "default_version_source_provider",
    "register_strategy_version_source_routes",
    "router",
]
