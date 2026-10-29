"""Service-token authentication for the RM strategy-run entry.

The control plane (RM) authenticates with one shared service token that is
mounted into the box as a ``0600`` file. The file path is named by
``RM_VT_STRATEGY_RUN_TOKEN_FILE`` and read again on every request so a rotated
mount takes effect without a restart.

This module deliberately knows nothing about VT's own ``API_AUTH_KEY``,
loopback trust or ``require_auth``: the entry routes accept the service token
and nothing else. Error bodies are fixed text; a token, the header that carried
it, or the file's contents never reach a response or a log line.

Failure split (frozen contract):

* missing ``Authorization`` header, a non-``Bearer`` scheme, or an empty
  presented token -> ``401``;
* token file unset, missing, unreadable, empty, or a mismatching token ->
  ``403``, and the service keeps running.
"""

from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

#: Environment variable naming the ``0600`` file holding the RM service token.
TOKEN_FILE_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"

#: Fixed bodies: a caller must not learn *why* the token was refused.
_UNAUTHORIZED = "unauthorized"
_FORBIDDEN = "forbidden"
WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}

__all__ = [
    "TOKEN_FILE_ENV",
    "WWW_AUTHENTICATE",
    "log_token_configuration",
    "require_strategy_run_token",
    "token_configured",
]


def _unauthorized() -> HTTPException:
    """Return the fixed 401 for a missing or malformed credential."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_UNAUTHORIZED,
        headers=WWW_AUTHENTICATE,
    )


def _forbidden() -> HTTPException:
    """Return the fixed 403 for an unusable or mismatching service token."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=_FORBIDDEN,
    )


def _token_file() -> Path | None:
    """Return the configured token-file path, or ``None`` when unset/blank."""
    raw = os.environ.get(  # noqa: env-gate — runtime mount path, not app config
        TOKEN_FILE_ENV, ""
    ).strip()
    if not raw:
        return None
    return Path(raw)


def _expected_token() -> str:
    """Read the mounted token, stripping only trailing ``\\r``/``\\n``.

    Any failure — unset env, missing file, permission error, undecodable
    bytes — reads as "no token configured" so the caller fails closed with
    ``403`` instead of crashing. The path and its contents are never logged.
    """
    path = _token_file()
    if path is None:
        return ""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return handle.read().rstrip("\r\n")
    except (OSError, UnicodeError):
        return ""


def _presented_token(request: Request) -> str:
    """Return the Bearer credential, refusing a missing/malformed header.

    Raises:
        HTTPException: 401 when the header is absent, does not use the
            ``Bearer`` scheme, or carries an empty credential.
    """
    header = request.headers.get("authorization", "")
    scheme, separator, credential = header.partition(" ")
    if not separator or scheme.lower() != "bearer":
        raise _unauthorized()
    token = credential.strip()
    if not token:
        raise _unauthorized()
    return token


def token_configured() -> bool:
    """Return whether a non-empty service token can be read right now."""
    return bool(_expected_token())


def log_token_configuration() -> None:
    """Emit the only startup-safe token signal: configured or not.

    Deliberately never includes the file path, its size, or its contents.
    """
    if token_configured():
        logger.info("strategy-run entry: service token configured")
    else:
        logger.info("strategy-run entry: service token not configured")


def require_strategy_run_token(request: Request) -> None:
    """FastAPI dependency validating the RM service token.

    Raises:
        HTTPException: 401 for a missing/malformed header, 403 when the token
            cannot be read or does not match.
    """
    presented = _presented_token(request)
    expected = _expected_token()
    if not expected:
        raise _forbidden()
    if not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        raise _forbidden()
