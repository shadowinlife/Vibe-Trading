"""Pure wire contracts for ``mcp-execution-control-v1``.

D-3 deliberately hashes only config/source/data identity. RM's current
``declared_digest`` and ``build_fingerprint`` are not in the 0013 prepare
payload, so RM must align with this formula or recompute it before E2E.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Final, Literal, TypeAlias

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)

CONTRACT_VERSION: Final = "mcp-execution-control-v1"
FIND_STATES: Final = ("not_dispatched", "dispatched", "absent")
RUN_STATES: Final = ("running", "completed", "failed", "cancelled", "unknown")
STOP_STATES: Final = ("requested", "confirmed", "unknown")
STOP_EVIDENCE: Final = (
    "process_exit",
    "terminal_record",
    "process_absent",
    "ttl_expired",
    "explicit_absence",
)
ERROR_KINDS: Final = (
    "timeout",
    "runner_error",
    "strategy_not_found",
    "version_not_found",
    "input_digest_mismatch",
)
DATA_DIGEST_STATUSES: Final = ("verified", "unavailable")

_URL_SAFE_PATTERN: Final = r"^[A-Za-z0-9._~-]+$"
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_DispatchKey = Annotated[
    str, Field(min_length=1, max_length=200, pattern=_URL_SAFE_PATTERN)
]
_RemoteToken = Annotated[
    str, Field(min_length=1, max_length=128, pattern=_URL_SAFE_PATTERN)
]
_Sha256Hex = Annotated[str, Field(pattern=_SHA256_RE.pattern)]
_NonEmptyString = Annotated[str, Field(min_length=1)]

__all__ = [
    "CONTRACT_VERSION",
    "DATA_DIGEST_STATUSES",
    "ERROR_KINDS",
    "FIND_STATES",
    "RUN_STATES",
    "STOP_EVIDENCE",
    "STOP_STATES",
    "BacktestStartRequest",
    "ContractValidationError",
    "ExecutionStartRequest",
    "FactorPrototypeSpec",
    "FactorStartRequest",
    "JsonValue",
    "StopRequest",
    "backtest_input_digest",
    "backtest_primitive_prompt",
    "canonical_json_bytes",
    "ensure_sha256_hex",
    "sha256_hex",
]


@dataclass(frozen=True, slots=True)
class ContractValidationError(ValueError):
    """A fielded validation reason; pydantic wraps it at model boundaries."""

    field: str
    reason: str

    def __str__(self) -> str:
        return f"{self.field}: {self.reason}"


def backtest_primitive_prompt(run_dir: str) -> str:
    """Return the only prompt allowed to execute a frozen backtest directory."""
    return (
        "Run the in-box backtest on this frozen directory:\n"
        f"backtest(run_dir={run_dir!r})\n"
        "Do not modify, rewrite, or regenerate config.json or code/signal_engine.py."
    )


def canonical_json_bytes(payload: JsonValue) -> bytes:
    """Encode one JSON value deterministically, rejecting non-finite numbers.

    Args:
        payload: JSON-compatible value to encode.

    Returns:
        Sorted, compact, ASCII-escaped UTF-8 bytes.

    Raises:
        ContractValidationError: If the value is not finite canonical JSON.
    """
    try:
        text = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(
            field="payload", reason=f"not canonical JSON: {exc}"
        ) from exc
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Return the SHA-256 digest of bytes as 64 lowercase hex characters.

    Args:
        data: Bytes to hash.

    Returns:
        The 64-character lowercase hexadecimal digest.
    """
    return hashlib.sha256(data).hexdigest()


def ensure_sha256_hex(value: str, field: str) -> str:
    """Return *value* only when it is exactly one lowercase SHA-256 digest.

    Args:
        value: Candidate digest text.
        field: Field name included in a validation failure.

    Returns:
        The unchanged validated digest.

    Raises:
        ContractValidationError: If the value is not 64 lowercase hex characters.
    """
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ContractValidationError(
            field=field, reason="must be 64 lowercase hexadecimal characters"
        )
    return value


def backtest_input_digest(
    config_sha256: str,
    source_sha256: str,
    data_source: str,
    data_as_of: str,
    snapshot_basis: str,
) -> str:
    """Hash exactly the six-field D-3 ``backtest-input-v1`` document.

    Args:
        config_sha256: Digest of canonical config bytes.
        source_sha256: Digest of frozen source bytes.
        data_source: Declared market-data source.
        data_as_of: Declared data snapshot boundary.
        snapshot_basis: Declared snapshot provenance.

    Returns:
        The lowercase SHA-256 digest of the canonical D-3 document.

    Raises:
        ContractValidationError: If a digest or provenance string is invalid.
    """
    ensure_sha256_hex(config_sha256, "config_sha256")
    ensure_sha256_hex(source_sha256, "source_sha256")
    for field, value in (
        ("data_source", data_source),
        ("data_as_of", data_as_of),
        ("snapshot_basis", snapshot_basis),
    ):
        if not isinstance(value, str) or not value:
            raise ContractValidationError(
                field=field, reason="must be a non-empty string"
            )
    return sha256_hex(
        canonical_json_bytes(
            {
                "schema": "backtest-input-v1",
                "config_sha256": config_sha256,
                "source_sha256": source_sha256,
                "data_source": data_source,
                "data_as_of": data_as_of,
                "snapshot_basis": snapshot_basis,
            }
        )
    )


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class _VersionedModel(_ContractModel):
    contract_version: Literal[CONTRACT_VERSION]


class BacktestStartRequest(_VersionedModel):
    """A start declaration; todo 5 binds ``run_dir`` to its prepared row."""

    dispatch_key: _DispatchKey
    kind: Literal["backtest"]
    run_id: _RemoteToken
    run_dir: str = Field(min_length=1)
    input_digest: _Sha256Hex
    config_sha256: _Sha256Hex
    source_sha256: _Sha256Hex
    build_fingerprint: _Sha256Hex
    prompt: str = Field(min_length=1)


class FactorPrototypeSpec(_ContractModel):
    """A bounded factor batch over one frozen alpha-zoo registry."""

    registry_digest: _Sha256Hex
    candidate_ids: tuple[_NonEmptyString, ...] = Field(min_length=1, max_length=200)
    symbols: tuple[_NonEmptyString, ...] = Field(min_length=1, max_length=500)
    start_date: date
    split_date: date
    end_date: date
    source: Literal["alpha_zoo"]
    as_of: date
    horizon_days: int = Field(
        alias="forward_return_horizon_days", strict=True, ge=1, le=252
    )
    n_groups: int = Field(strict=True, ge=2, le=20)

    @field_validator("candidate_ids", "symbols")
    @classmethod
    def _unique_non_blank(
        cls, values: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ContractValidationError(
                field=info.field_name, reason="entries must not be blank"
            )
        if len(set(values)) != len(values):
            raise ContractValidationError(
                field=info.field_name, reason="entries must be unique"
            )
        return values

    @model_validator(mode="after")
    def _chronology(self) -> FactorPrototypeSpec:
        if not self.start_date < self.split_date <= self.end_date:
            raise ContractValidationError(
                field="split_date",
                reason="require start_date < split_date <= end_date",
            )
        if self.as_of < self.end_date:
            raise ContractValidationError(
                field="as_of", reason="must be greater than or equal to end_date"
            )
        return self


class FactorStartRequest(_VersionedModel):
    """The complete declaration accepted for one frozen factor start."""

    dispatch_key: _DispatchKey
    kind: Literal["factor"]
    spec: FactorPrototypeSpec


ExecutionStartRequest: TypeAlias = Annotated[
    BacktestStartRequest | FactorStartRequest, Field(discriminator="kind")
]


class StopRequest(_VersionedModel):
    """The complete idempotent stop tuple and its aware deadline."""

    dispatch_key: _DispatchKey
    remote_id: _RemoteToken
    stop_key: _RemoteToken
    deadline_at: AwareDatetime

    @field_validator("deadline_at", mode="before")
    @classmethod
    def _require_iso_string(cls, value: JsonValue) -> JsonValue:
        if not isinstance(value, str):
            raise ContractValidationError(
                field="deadline_at",
                reason="must be a timezone-aware ISO-8601 string",
            )
        return value
