"""Resolve a deployment owner from its service-credential binding."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, NewType

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.api.strategy_run_auth import (
    expected_strategy_run_token_sha256,
    require_strategy_run_token,
)

OwnerUserId = NewType("OwnerUserId", str)
BINDING_FILE_ENV: Final = "RM_VT_STRATEGY_OWNER_BINDING_FILE"
_MAX_BINDING_BYTES: Final = 16_384
_FORBIDDEN: Final = "forbidden"

__all__ = [
    "BINDING_FILE_ENV",
    "OwnerBindingError",
    "OwnerUserId",
    "optional_deployment_owner",
    "require_deployment_owner",
    "resolve_deployment_owner",
]


@dataclass(frozen=True, slots=True)
class OwnerBindingError(Exception):
    """A configured owner binding cannot be trusted."""

    def __str__(self) -> str:
        return "invalid deployment owner binding"


class _OwnerBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["strategy-owner-binding-v1"]
    owner_user_id: OwnerUserId
    token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("owner_user_id")
    @classmethod
    def usable_owner(cls, value: OwnerUserId) -> OwnerUserId:
        if not value.strip() or any(unicodedata.category(character).startswith("C") for character in value):
            raise OwnerBindingError()
        return value


def _read_binding(path: Path) -> _OwnerBinding:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "getuid"):
        raise OwnerBindingError()
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise OwnerBindingError()
            content = handle.read(_MAX_BINDING_BYTES + 1)
    except OwnerBindingError:
        raise
    except (OSError, ValueError):
        raise OwnerBindingError() from None
    if len(content) > _MAX_BINDING_BYTES:
        raise OwnerBindingError()
    try:
        return _OwnerBinding.model_validate_json(content)
    except ValidationError:
        raise OwnerBindingError() from None


def _configured_binding() -> _OwnerBinding:
    configured_path = os.environ.get(BINDING_FILE_ENV)
    if configured_path is None or not configured_path.strip():
        raise OwnerBindingError()
    return _read_binding(Path(configured_path.strip()))


def _bound_owner(binding: _OwnerBinding, credential_sha256: str) -> OwnerUserId:
    if not hmac.compare_digest(binding.token_sha256, credential_sha256):
        raise OwnerBindingError()
    return binding.owner_user_id


def resolve_deployment_owner(presented: str) -> OwnerUserId:
    """Resolve the owner bound to an already authenticated credential.

    Args:
        presented: Normalized service credential accepted by 0001 auth.

    Returns:
        The deployment-bound owner user identifier.

    Raises:
        HTTPException: Fixed 403 when the binding is missing or untrusted.
    """
    try:
        binding = _configured_binding()
        presented_sha256 = hashlib.sha256(presented.encode("utf-8")).hexdigest()
        return _bound_owner(binding, presented_sha256)
    except OwnerBindingError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN) from None


def require_deployment_owner(request: Request) -> OwnerUserId:
    """Authenticate a request once, then resolve its bound owner.

    Args:
        request: Incoming HTTP request carrying the 0001 service credential.

    Returns:
        The deployment-bound owner user identifier.

    Raises:
        HTTPException: 401/403 from service auth, or fixed 403 for an untrusted
            owner binding.
    """
    return resolve_deployment_owner(require_strategy_run_token(request))


def optional_deployment_owner() -> OwnerUserId | None:
    """Select legacy writes only when the binding environment is absent."""
    if BINDING_FILE_ENV not in os.environ:
        return None
    binding = _configured_binding()
    expected_sha256 = expected_strategy_run_token_sha256()
    if expected_sha256 is None:
        raise OwnerBindingError()
    return _bound_owner(binding, expected_sha256)
