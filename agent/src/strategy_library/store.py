"""Strategy library facade: drafts, immutable versions, CAS verdicts, snapshots.

The library reuses the SDM strategy store as its single registry — there is no
second artifact table. Two invariants make it safe to schedule from:

* A verdict is written only in the same conditional UPDATE that verifies the
  ``artifact_version`` and ``spec_sha256`` it was produced against, so a
  concurrent edit turns the write into a stale no-op instead of attaching an
  old verdict to new bytes.
* :func:`update_draft` bumps the version in the same statement that installs
  the new spec bytes, and only then materialises ``strategy.json``. The
  filesystem therefore never holds a spec newer than the row describing it.

Canonical encoding and version publishing live in
:mod:`src.strategy_library.store_io`.
"""

from __future__ import annotations

import copy
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.strategy_library.history import DEFAULT_RETENTION, read_runs_for_strategy
from src.strategy_library.paths import version_dir
from src.strategy_library.store_io import (
    canonical_spec_bytes,
    code_sources,
    ensure_spec_file,
    materialise_spec,
    publish_version,
    sha256_bytes,
    spec_sha256,
    validated_payload,
    version_base,
)
from src.strategy_library.spec import StrategySpec
from src.strategy_store._shared import get_store
from src.strategy_store.models import (
    Artifact,
    ArtifactType,
    ValidationStatus,
    validate_artifact_kind,
    validate_artifact_readiness,
)
from src.strategy_store.sqlite_store import SqliteStrategyStore

__all__ = [
    "DEFAULT_LIST_LIMIT",
    "DuplicateNameUniverseError",
    "EditRequiredError",
    "StaleVerdictError",
    "canonical_spec_bytes",
    "create_draft",
    "get",
    "list_runs",
    "list_strategies",
    "record_verdict",
    "set_readiness",
    "snapshot",
    "spec_sha256",
    "update_draft",
]

DEFAULT_LIST_LIMIT = 50

# ``list_artifacts`` has no OFFSET and no demo/name predicate, so the facade
# scans a bounded newest-first window and filters/slices in Python. A single
# operator's library is orders of magnitude smaller than this bound.
_LIST_SCAN_CAP = 5000

#: Gate statuses a verdict may assert. ``APPROVED`` belongs to SDM governance.
VERDICT_STATUSES = (ValidationStatus.VALIDATED, ValidationStatus.REJECTED,
                    ValidationStatus.UNVALIDATED)

# ``register_artifact`` reports the ``UNIQUE(name, universe)`` violation as a
# plain ValueError; these fragments let the facade re-type it without catching
# unrelated write failures.
_DUPLICATE_MARKERS = ("already exists in universe", "artifacts.name")


class StaleVerdictError(RuntimeError):
    """A compare-and-swap matched no row, so nothing was written."""


class EditRequiredError(RuntimeError):
    """A legacy row has no version/spec hash and must be re-promoted (409)."""


class DuplicateNameUniverseError(ValueError):
    """The library already holds a strategy with this ``(name, universe)`` pair."""


def _resolve_store(store: SqliteStrategyStore | None) -> SqliteStrategyStore:
    """Return the explicit store or the process-wide singleton."""
    return get_store() if store is None else store


def get(strategy_id: str, *, store: SqliteStrategyStore | None = None) -> Artifact | None:
    """Return one strategy row, or ``None`` when the id does not exist."""
    return _resolve_store(store).get_artifact(strategy_id)


def list_strategies(
    *,
    kind: str | None = None,
    readiness: str | None = None,
    q: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
    include_demo: bool = True,
    store: SqliteStrategyStore | None = None,
) -> list[Artifact]:
    """Return strategy rows newest first, filtered and paginated.

    ``kind`` and ``readiness`` are pushed into SQL; the case-insensitive name
    substring (``q``) and ``include_demo`` are applied here. At most
    :data:`_LIST_SCAN_CAP` rows are scanned, bounding the work per request.

    Raises:
        ValueError: On an unknown kind/readiness or a negative limit/offset.
    """
    if kind is not None:
        validate_artifact_kind(kind)
    if readiness is not None:
        validate_artifact_readiness(readiness)
    if limit < 0:
        raise ValueError("limit must not be negative")
    if offset < 0:
        raise ValueError("offset must not be negative")

    rows = _resolve_store(store).list_artifacts(
        kind=kind, readiness=readiness, limit=_LIST_SCAN_CAP
    )
    needle = (q or "").strip().casefold()
    matched = [
        artifact
        for artifact in rows
        if (include_demo or not artifact.demo)
        and (not needle or needle in artifact.name.casefold())
    ]
    return matched[offset : offset + limit]


def list_runs(
    strategy_id: str,
    limit: int = DEFAULT_RETENTION,
    *,
    runtime_root: Path | None = None,
) -> list:
    """Return a strategy's scheduled-run records, newest first.

    Merges every job's JSONL history by ``strategy_id`` and trims to *limit*.
    """
    return read_runs_for_strategy(strategy_id, limit, runtime_root=runtime_root)


def create_draft(
    spec: StrategySpec | Mapping[str, Any],
    *,
    run_dir: str | None = None,
    demo: bool = False,
    store: SqliteStrategyStore | None = None,
    runtime_root: Path | None = None,
) -> Artifact:
    """Register an unvalidated strategy draft and materialise its spec file.

    The row starts at ``readiness="draft"`` and ``v1-<sha[:8]>``; the spec is
    stored verbatim (after a JSON round-trip) so ``spec_sha256`` describes
    exactly what the caller can read back. An existing ``(name, universe)`` is
    never overwritten — the caller must choose a new name.

    Raises:
        DuplicateNameUniverseError: When the name/universe pair is taken; the
            existing strategy is left untouched. A ``ValueError``.
        ValueError: On a blank name or an unknown kind.
    """
    payload = validated_payload(spec)
    artifact_store = _resolve_store(store)
    digest = sha256_bytes(canonical_spec_bytes(payload))
    candidate = Artifact(
        id="",
        type=ArtifactType.STRATEGY,
        name=payload["name"].strip(),
        universe=payload.get("universe") or "",
        run_dir=run_dir,
        kind=payload["kind"],
        spec=payload,
        spec_sha256=digest,
        artifact_version=f"v1-{digest[:8]}",
        readiness="draft",
        demo=bool(demo),
    )
    try:
        strategy_id = artifact_store.register_artifact(candidate)
    except (sqlite3.IntegrityError, ValueError) as exc:
        if not any(marker in str(exc) for marker in _DUPLICATE_MARKERS):
            raise
        raise DuplicateNameUniverseError(
            f"strategy {candidate.name!r} for universe {candidate.universe!r} "
            "already exists"
        ) from exc
    materialise_spec(strategy_id, payload, runtime_root=runtime_root)
    created = artifact_store.get_artifact(strategy_id)
    assert created is not None  # just inserted on the same connection
    return created


def update_draft(
    strategy_id: str,
    spec: StrategySpec | Mapping[str, Any],
    *,
    store: SqliteStrategyStore | None = None,
    runtime_root: Path | None = None,
) -> Artifact:
    """Replace a draft's spec, bumping its version and clearing the verdict.

    One compare-and-swap on ``(artifact_version, spec_sha256)`` installs the new
    canonical bytes, the new version, ``needs_work``, a cleared current verdict,
    the displaced verdict as ``previous_verdict`` and ``UNVALIDATED``. The spec
    file is rewritten only after that statement matched, so the filesystem never
    runs ahead of the row.

    Identity columns (``name``, ``universe``) are creation-time: the spec is
    rewritten, the listing key is not.

    Raises:
        LookupError: When the strategy does not exist.
        EditRequiredError: When a legacy row has no version or spec hash.
        StaleVerdictError: When the row changed between the read and the write.
        ValueError: On a blank name or an unknown kind.
    """
    payload = validated_payload(spec)
    artifact_store = _resolve_store(store)
    existing = artifact_store.get_artifact(strategy_id)
    if existing is None:
        raise LookupError(f"strategy {strategy_id!r} does not exist")

    data = canonical_spec_bytes(payload)
    digest = sha256_bytes(data)
    old_version = existing.artifact_version
    old_digest = existing.spec_sha256
    if not old_version or not old_digest:
        # Nothing comparable to swap against: stop advertising readiness, then
        # let the caller answer 409 edit_required.
        artifact_store._update_gate_columns(strategy_id, readiness="needs_work")
        raise EditRequiredError(
            f"strategy {strategy_id!r} predates version pinning and must be "
            "re-promoted before it can be edited"
        )

    new_version = f"v{version_base(old_version) + 1}-{digest[:8]}"
    written = artifact_store._update_spec_and_version(
        strategy_id,
        spec_json=data.decode("utf-8"),
        artifact_version=new_version,
        spec_sha256=digest,
        readiness="needs_work",
        evaluator_verdict=None,
        previous_verdict=existing.evaluator_verdict,
        expected_version=old_version,
        expected_spec_sha256=old_digest,
    )
    if written != 1:
        raise StaleVerdictError(
            f"strategy {strategy_id!r} changed before the edit could be applied; "
            f"expected {old_version}/{old_digest}, nothing written"
        )

    materialise_spec(strategy_id, payload, runtime_root=runtime_root)
    updated = artifact_store.get_artifact(strategy_id)
    assert updated is not None  # the CAS above matched this row
    return updated


def record_verdict(
    strategy_id: str,
    verdict: Mapping[str, Any],
    *,
    expected_version: str,
    expected_spec_sha256: str,
    readiness: str,
    validation_status: ValidationStatus | str,
    store: SqliteStrategyStore | None = None,
) -> Artifact:
    """Store an evaluator verdict bound to the version it judged.

    Verdict, readiness, validation status and timestamp are one conditional
    UPDATE. A zero rowcount means the strategy was edited after the evaluator
    read it: nothing is written and the caller re-evaluates. An inconclusive
    evaluation persists ``UNVALIDATED``: the verdict is recorded without
    advancing SDM governance, and ``APPROVED`` is never a gate verdict.

    Raises:
        ValueError: On an unknown readiness or an out-of-contract status.
        StaleVerdictError: When the CAS guard no longer matches.
    """
    if not isinstance(verdict, Mapping):
        raise TypeError(f"verdict must be a mapping, got {type(verdict).__name__}")
    validate_artifact_readiness(readiness)
    status = ValidationStatus(validation_status)
    if status not in VERDICT_STATUSES:
        raise ValueError(
            f"gate verdicts are unvalidated/validated/rejected, not {status.value!r}"
        )

    artifact_store = _resolve_store(store)
    written = artifact_store._update_gate_columns(
        strategy_id,
        readiness=readiness,
        evaluator_verdict=copy.deepcopy(dict(verdict)),
        validation_status=status,
        expected_version=expected_version,
        expected_spec_sha256=expected_spec_sha256,
    )
    if written != 1:
        raise StaleVerdictError(
            f"verdict for {strategy_id!r} is stale: expected "
            f"{expected_version}/{expected_spec_sha256}; nothing written"
        )
    updated = artifact_store.get_artifact(strategy_id)
    if updated is None:  # pragma: no cover - the CAS matched a row
        raise LookupError(f"strategy {strategy_id!r} disappeared during the write")
    return updated


def set_readiness(
    strategy_id: str,
    readiness: str,
    *,
    store: SqliteStrategyStore | None = None,
) -> Artifact:
    """Flip the scheduling gate without touching the verdict.

    For state changes that carry no evaluation (disable, back to ``reviewing``).
    A verdict and the readiness it justifies must go through
    :func:`record_verdict`, so they can never be split across two writes. Never
    call this from a REST handler.

    Raises:
        ValueError: On an unknown readiness state.
        LookupError: When the strategy does not exist.
    """
    validate_artifact_readiness(readiness)
    artifact_store = _resolve_store(store)
    if artifact_store._update_gate_columns(strategy_id, readiness=readiness) != 1:
        raise LookupError(f"strategy {strategy_id!r} does not exist")
    updated = artifact_store.get_artifact(strategy_id)
    assert updated is not None
    return updated


def snapshot(
    strategy_id: str,
    *,
    store: SqliteStrategyStore | None = None,
    runtime_root: Path | None = None,
) -> str:
    """Freeze the current version into ``versions/<version>/`` and return it.

    Copies are kind-aware: a ``code`` version carries ``strategy.json``,
    ``config.json`` and ``code/signal_engine.py``; a ``workflow`` version
    carries only ``strategy.json``. ``manifest.json`` pins every copied digest,
    and the directory is published by an atomic rename, so a failure leaves no
    half-built version and an existing version is never rewritten.

    Raises:
        LookupError: When the strategy does not exist.
        EditRequiredError: When the row carries no ``artifact_version``.
        FileNotFoundError: When a code strategy lacks a required source file.
        ValueError: When the row carries no spec to copy.
    """
    artifact_store = _resolve_store(store)
    artifact = artifact_store.get_artifact(strategy_id)
    if artifact is None:
        raise LookupError(f"strategy {strategy_id!r} does not exist")
    version = artifact.artifact_version
    if not version:
        raise EditRequiredError(
            f"strategy {strategy_id!r} has no version to snapshot; edit or re-promote it first"
        )
    if version_dir(strategy_id, version, runtime_root=runtime_root).exists():
        return version

    spec_bytes = ensure_spec_file(artifact, runtime_root=runtime_root)
    sources = (
        code_sources(strategy_id, runtime_root=runtime_root)
        if artifact.kind == "code"
        else []
    )
    publish_version(
        strategy_id,
        version,
        spec_bytes=spec_bytes,
        sources=sources,
        runtime_root=runtime_root,
    )
    return version
