"""Canonical JSON hashing for replay-only evals.

Eval-local stand-in for ``src.reliability.artifacts.hashing.sha256_json``,
which was removed by revert 035673b0 ("revert: back out IRR-AGL
reliability/governance stack"). The behavior is ported verbatim from the
pre-revert implementation (commit 035673b0^) so prompt hashes computed by
:mod:`src.evals.agent_eval.prompt_hash` stay bit-identical to the original
harness. This module is pure computation: it reads no configuration and
touches no runtime path.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class CanonicalJsonError(ValueError):
    """Raised when a value cannot be canonically hashed as JSON."""


def sha256_json(value: Mapping[str, Any]) -> str:
    """Return a canonical SHA-256 digest for a JSON object.

    Args:
        value: JSON-serializable mapping to hash canonically.

    Returns:
        Lowercase hex SHA-256 digest of the canonical JSON encoding.

    Raises:
        CanonicalJsonError: If the value is not a dict or cannot be
            canonically serialized (non-JSON types, NaN/Inf, non-string keys).
    """
    if not isinstance(value, dict):
        raise CanonicalJsonError("sha256_json requires a JSON-serializable dict")
    _validate_json_value(value, path="$")
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise CanonicalJsonError(str(exc)) from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_json_value(value: Any, *, path: str) -> None:
    if isinstance(value, BaseModel):
        raise CanonicalJsonError(
            f"{path}: Pydantic models must use model_dump(mode='json') before hashing"
        )
    if isinstance(value, (datetime, date, time)):
        raise CanonicalJsonError(
            f"{path}: datetime/date/time values must be converted before hashing"
        )
    if isinstance(value, UUID):
        raise CanonicalJsonError(
            f"{path}: UUID values must be converted before hashing"
        )
    if _looks_like_pandas(value):
        raise CanonicalJsonError(
            f"{path}: pandas objects cannot be canonically JSON hashed"
        )
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalJsonError(f"{path}: NaN/Inf values are not allowed")
        return
    if isinstance(value, list):
        for idx, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{idx}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJsonError(f"{path}: JSON object keys must be strings")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise CanonicalJsonError(
        f"{path}: unsupported JSON value type {type(value).__name__}"
    )


def _looks_like_pandas(value: Any) -> bool:
    module = type(value).__module__
    name = type(value).__name__
    return module.startswith("pandas.") and name in {"DataFrame", "Series"}
