"""Typed contracts shared by factor prototype orchestration and native evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Final, Literal, Protocol, TypedDict

from src.factors.registry_freeze import CandidateRow
from src.strategy_library.paths import atomic_write_json
from src.strategy_runs.control_contract import (
    FactorPrototypeSpec,
    JsonValue,
    canonical_json_bytes,
    sha256_hex,
)

Phase = Literal["in_sample", "out_of_sample"]
ARTIFACT_FILENAMES: Final = {
    "summary": "summary.json",
    "factor_csv": "factor.csv",
    "returns_csv": "returns.csv",
    "ic_series": "ic_series.csv",
    "group_equity": "group_equity.csv",
    "ic_summary": "ic_summary.json",
}


def factor_remote_id(dispatch_key: str, spec: FactorPrototypeSpec) -> str:
    """Derive execution identity from the complete canonical factor declaration.

    Args:
        dispatch_key: Client idempotency identity.
        spec: Fully validated frozen factor specification.

    Returns:
        The deterministic ``factor-`` prefixed 32-hex execution identity.
    """
    identity = canonical_json_bytes(
        {
            "dispatch_key": dispatch_key,
            "spec": spec.model_dump(mode="json", by_alias=True),
        }
    )
    return "factor-" + sha256_hex(identity)[:32]


class ProgressDocument(TypedDict):
    completed: int
    total: int
    skipped: int
    insufficient: int
    processed: int


@dataclass(frozen=True, slots=True)
class PrototypeProgress:
    """Outcome counts; ``completed`` never includes skipped/insufficient rows."""

    completed: int
    total: int
    skipped: int
    insufficient: int
    processed: int

    def document(self) -> ProgressDocument:
        """Return JSON-ready outcome counts.

        Returns:
            Completed, total, skipped, insufficient, and processed counts.
        """
        return {
            "completed": self.completed,
            "total": self.total,
            "skipped": self.skipped,
            "insufficient": self.insufficient,
            "processed": self.processed,
        }


@dataclass(frozen=True, slots=True)
class PrototypeResult:
    document: dict[str, JsonValue]
    progress: PrototypeProgress


@dataclass(frozen=True, slots=True)
class PrototypeRunRequest:
    spec: FactorPrototypeSpec
    run_dir: Path
    remote_id: str
    dispatch_key: str


@dataclass(frozen=True, slots=True)
class PrototypeValidationError(Exception):
    kind: Literal["invalid_request", "input_digest_mismatch"]

    def __str__(self) -> str:
        return self.kind


@dataclass(frozen=True, slots=True)
class PrototypeWorkError(Exception):
    digest: str

    def __str__(self) -> str:
        return f"identical tool input rejected: {self.digest}"


class PrototypeWorkLedger:
    """Mutable per-run set that rejects a repeated canonical tool input."""

    def __init__(self) -> None:
        self._admitted: set[str] = set()

    def admit(self, tool: str, payload: Mapping[str, JsonValue]) -> None:
        """Admit one new canonical tool input.

        Args:
            tool: Native tool name included in the work identity.
            payload: Canonical JSON-compatible tool arguments.

        Returns:
            None. The input is recorded exactly once.

        Raises:
            PrototypeWorkError: If the same tool/input digest was already admitted.
        """
        digest = sha256_hex(canonical_json_bytes(dict(payload)))
        key = f"{tool}:{digest}"
        if key in self._admitted:
            raise PrototypeWorkError(key)
        self._admitted.add(key)


@dataclass(frozen=True, slots=True)
class PhaseRequest:
    spec: FactorPrototypeSpec
    candidate: CandidateRow
    phase: Phase
    output_dir: Path
    ledger: PrototypeWorkLedger

    @property
    def start_date(self) -> date:
        """Return the first factor date in this logical phase.

        Returns:
            The spec start for IS or split date for OOS.
        """
        return (
            self.spec.start_date if self.phase == "in_sample" else self.spec.split_date
        )

    @property
    def end_date(self) -> date:
        """Return the last factor date in this logical phase.

        Returns:
            The day before split for IS or the spec end date for OOS.
        """
        return (
            self.spec.split_date - timedelta(days=1)
            if self.phase == "in_sample"
            else self.spec.end_date
        )


@dataclass(frozen=True, slots=True)
class PhaseOutcome:
    status: Literal["ok", "insufficient_data", "error"]
    reasons: tuple[str, ...]
    metrics: dict[str, JsonValue]
    artifacts: dict[str, str]


class PhaseEvaluator(Protocol):
    def evaluate(self, request: PhaseRequest) -> PhaseOutcome:
        """Evaluate exactly one candidate/phase request.

        Args:
            request: Frozen candidate/phase declaration and work ledger.

        Returns:
            The measured, insufficient, or error outcome and its artifacts.
        """


@dataclass(frozen=True, slots=True)
class _PhaseRecord:
    candidate_id: str
    phase: Phase
    outcome: PhaseOutcome


def write_prototype_json(path: Path, payload: JsonValue) -> None:
    """Validate strict finite JSON before atomically publishing it.

    Args:
        path: Destination inside the server-owned run directory.
        payload: JSON-compatible result, progress, spec, or merged summary.

    Returns:
        None. The destination is replaced atomically after validation.

    Raises:
        ContractValidationError: If the payload is not finite canonical JSON.
        OSError: If the atomic write or rename fails.
    """
    canonical_json_bytes(payload)
    atomic_write_json(path, payload)
