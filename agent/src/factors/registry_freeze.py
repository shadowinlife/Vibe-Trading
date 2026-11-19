"""Deterministic freezes for explicitly selected alpha registry candidates."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypedDict

if TYPE_CHECKING:
    from src.factors.registry import Registry

TOOL_SCHEMA_VERSION: Final = "alpha-zoo-v1+alpha-bench-v1+factor-analysis-v1"
_REGISTRY_SCHEMA: Final = "factor-registry-v1"
_MAX_CANDIDATES: Final = 200
_CANDIDATE_ID_RE: Final = re.compile(r"^[a-z][a-z0-9]+_[a-z0-9_]+$")
_ZOO_ID_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


@dataclass(frozen=True, slots=True)
class RegistryFreezeError(ValueError):
    """A machine-readable selected-registry validation failure."""

    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


class CandidateRowDocument(TypedDict):
    """JSON-compatible canonical candidate metadata."""

    candidate_id: str
    zoo: str
    universe: list[str]
    columns_required: list[str]
    min_warmup_bars: int


@dataclass(frozen=True, slots=True)
class CandidateRow:
    """The complete metadata vocabulary covered by a registry freeze."""

    candidate_id: str
    zoo: str
    universe: tuple[str, ...]
    columns_required: tuple[str, ...]
    min_warmup_bars: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.candidate_id, str)
            or _CANDIDATE_ID_RE.fullmatch(self.candidate_id) is None
        ):
            raise RegistryFreezeError(
                "malformed", f"invalid candidate_id {self.candidate_id!r}"
            )
        if not isinstance(self.zoo, str) or _ZOO_ID_RE.fullmatch(self.zoo) is None:
            raise RegistryFreezeError("malformed", f"invalid zoo {self.zoo!r}")
        _validate_string_tuple(self.universe, "universe")
        _validate_string_tuple(self.columns_required, "columns_required")
        if (
            isinstance(self.min_warmup_bars, bool)
            or not isinstance(self.min_warmup_bars, int)
            or self.min_warmup_bars < 0
        ):
            raise RegistryFreezeError(
                "malformed", "min_warmup_bars must be a non-negative integer"
            )

    def document(self) -> CandidateRowDocument:
        """Return canonical JSON-compatible metadata in declaration order.

        Returns:
            The five-key canonical candidate document.
        """
        return {
            "candidate_id": self.candidate_id,
            "zoo": self.zoo,
            "universe": list(self.universe),
            "columns_required": list(self.columns_required),
            "min_warmup_bars": self.min_warmup_bars,
        }


def _validate_string_tuple(values: tuple[str, ...], field: str) -> None:
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value for value in values
    ):
        raise RegistryFreezeError(
            "malformed", f"{field} must be a tuple of non-empty strings"
        )


@dataclass(frozen=True, slots=True)
class RegistryFreeze:
    """A sorted candidate set and its deterministic schema-bound digest."""

    registry_digest: str
    tool_schema_version: str
    candidates: tuple[CandidateRow, ...]


def _parse_candidate_ids(candidate_ids: Sequence[str]) -> tuple[str, ...]:
    if isinstance(candidate_ids, str) or not isinstance(candidate_ids, Sequence):
        raise RegistryFreezeError(
            "malformed", "candidate_ids must be a sequence of strings"
        )
    if not candidate_ids:
        raise RegistryFreezeError("empty", "candidate_ids must not be empty")
    if len(candidate_ids) > _MAX_CANDIDATES:
        raise RegistryFreezeError(
            "too_many", f"candidate_ids exceeds {_MAX_CANDIDATES} entries"
        )
    parsed = []
    seen = set()
    for candidate_id in candidate_ids:
        if (
            not isinstance(candidate_id, str)
            or _CANDIDATE_ID_RE.fullmatch(candidate_id) is None
        ):
            raise RegistryFreezeError(
                "malformed", f"invalid candidate_id {candidate_id!r}"
            )
        if candidate_id in seen:
            raise RegistryFreezeError(
                "duplicate", f"duplicate candidate_id {candidate_id!r}"
            )
        seen.add(candidate_id)
        parsed.append(candidate_id)
    return tuple(parsed)


def _parse_rows(rows: Sequence[CandidateRow]) -> tuple[CandidateRow, ...]:
    if isinstance(rows, str) or not isinstance(rows, Sequence):
        raise RegistryFreezeError("malformed", "rows must be CandidateRow values")
    if not rows:
        raise RegistryFreezeError("empty", "candidate rows must not be empty")
    if len(rows) > _MAX_CANDIDATES:
        raise RegistryFreezeError(
            "too_many", f"candidate rows exceed {_MAX_CANDIDATES} entries"
        )
    seen = set()
    for row in rows:
        if not isinstance(row, CandidateRow):
            raise RegistryFreezeError("malformed", "rows must be CandidateRow values")
        if row.candidate_id in seen:
            raise RegistryFreezeError(
                "duplicate", f"duplicate candidate_id {row.candidate_id!r}"
            )
        seen.add(row.candidate_id)
    return tuple(rows)


def rows_from_registry(
    registry: Registry,
    candidate_ids: Sequence[str],
) -> tuple[CandidateRow, ...]:
    """Read exactly the requested candidates without list/fallback expansion.

    Args:
        registry: Registry used as the sole source of candidate metadata.
        candidate_ids: Explicit candidate ids to read, in request order.

    Returns:
        Canonical rows in request order, preserving metadata array order.

    Raises:
        RegistryFreezeError: If an id is empty, duplicate, malformed, unknown,
            over the selection cap, or resolves to mismatched metadata.
    """
    parsed_ids = _parse_candidate_ids(candidate_ids)
    rows = []
    for candidate_id in parsed_ids:
        try:
            alpha = registry.get(candidate_id)
        except KeyError as exc:
            raise RegistryFreezeError(
                "unknown", f"candidate_id {candidate_id!r} is not registered"
            ) from exc
        if alpha.id != candidate_id:
            raise RegistryFreezeError(
                "malformed",
                f"registry returned {alpha.id!r} for requested {candidate_id!r}",
            )
        if not isinstance(alpha.meta, dict):
            raise RegistryFreezeError(
                "malformed", f"{candidate_id}: metadata must be a mapping"
            )
        try:
            universe = alpha.meta["universe"]
            columns_required = alpha.meta["columns_required"]
            min_warmup_bars = alpha.meta["min_warmup_bars"]
        except KeyError as exc:
            raise RegistryFreezeError(
                "malformed", f"{candidate_id}: missing metadata {exc.args[0]!r}"
            ) from exc
        if not isinstance(universe, (list, tuple)) or not isinstance(
            columns_required, (list, tuple)
        ):
            raise RegistryFreezeError(
                "malformed", f"{candidate_id}: metadata arrays must be sequences"
            )
        rows.append(
            CandidateRow(
                candidate_id=alpha.id,
                zoo=alpha.zoo,
                universe=tuple(universe),
                columns_required=tuple(columns_required),
                min_warmup_bars=min_warmup_bars,
            )
        )
    return tuple(rows)


def freeze_registry(
    rows: Sequence[CandidateRow],
    tool_schema_version: str = TOOL_SCHEMA_VERSION,
) -> RegistryFreeze:
    """Freeze exactly ``rows`` using the D-6 canonical JSON formula.

    Args:
        rows: Explicit candidate rows to canonicalize and hash.
        tool_schema_version: Version bound into the digest document.

    Returns:
        The candidate-id-sorted rows and their schema-bound SHA-256 digest.

    Raises:
        RegistryFreezeError: If rows are empty, duplicate, malformed, over the
            selection cap, or the schema version is blank.
    """
    parsed_rows = _parse_rows(rows)
    if not isinstance(tool_schema_version, str) or not tool_schema_version.strip():
        raise RegistryFreezeError(
            "malformed", "tool_schema_version must be a non-empty string"
        )
    ordered = tuple(sorted(parsed_rows, key=lambda row: row.candidate_id))
    document = {
        "schema": _REGISTRY_SCHEMA,
        "tool_schema_version": tool_schema_version,
        "candidates": [row.document() for row in ordered],
    }
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return RegistryFreeze(
        registry_digest=hashlib.sha256(encoded).hexdigest(),
        tool_schema_version=tool_schema_version,
        candidates=ordered,
    )
