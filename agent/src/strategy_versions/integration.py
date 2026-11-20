"""Bound-owner lifecycle for new code strategies and their snapshots.

A deployment binding selects the bound write path only when it is configured.
New non-demo code strategies register one pending identity and persist their
owner before evaluation; scheduling publishes through the lineage-backed
:class:`~src.strategy_versions.publisher.StrategyVersionPublisher` only when the
registry proves the bound owner already owns that exact strategy.  Without that
proof the legacy snapshot path stays authoritative, so a manually set
``Artifact.owner`` or a pre-binding artifact is never registered or backfilled.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from src.api.strategy_owner_binding import OwnerUserId, optional_deployment_owner
from src.strategy_library import store as library_store
from src.strategy_library.paths import (
    SIGNAL_ENGINE_RELPATH,
    VERSION_CONFIG_FILENAME,
    resolve_runtime_root,
    version_dir,
)
from src.strategy_library.store_io import code_sources, ensure_spec_file
from src.strategy_store._shared import get_store
from src.strategy_store.models import Artifact
from src.strategy_store.sqlite_store import SqliteStrategyStore
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import StrategyVersionError
from src.strategy_versions.locking import exclusive_version_lock
from src.strategy_versions.models import (
    RegistrationKey,
    SourceFacts,
    VersionRegistration,
)
from src.strategy_versions.publisher import (
    PublicationPayload,
    StrategyVersionPublisher,
    reject_cross_owner_claim,
)

__all__ = [
    "BoundStrategyError",
    "initialize_bound_strategy",
    "resolve_creation_owner",
    "snapshot_version",
]


class BoundStrategyError(StrategyVersionError):
    """A new bound code version cannot be initialized safely."""


def resolve_creation_owner() -> OwnerUserId | None:
    """Return the deployment owner, or ``None`` when the binding is absent.

    Returns:
        The configured bound owner, or ``None`` to select legacy creation.

    Raises:
        OwnerBindingError: When a configured binding is untrusted.
    """
    return optional_deployment_owner()


def initialize_bound_strategy(
    artifact: Artifact,
    *,
    owner: OwnerUserId,
    store: SqliteStrategyStore | None = None,
    runtime_root: Path | None = None,
) -> VersionRegistration:
    """Register a new code version as pending and persist its bound owner.

    The source/config files must already be copied into the strategy root.  The
    version must be fresh: no final F9 directory, no registry row, and no owner
    already on the artifact.  Registration happens before the owner write so a
    failure leaves an unreadable pending row rather than a readable but
    unregistered strategy.  A pending, active or tombstoned claim on the same
    strategy version by another owner is rejected before anything is written.

    Raises:
        BoundStrategyError: If the artifact is not a new unowned code version,
            or its final directory or registration already exists.
        FileNotFoundError: If the root config or engine is absent or empty.
        VersionConflictError: If the registry key already holds other facts,
            or another owner claims the same strategy version.
        LookupError: If the artifact disappears during the owner write.
    """
    if artifact.kind != "code" or artifact.demo:
        raise BoundStrategyError("bound initialization needs a non-demo code strategy")
    version = artifact.artifact_version
    if not version or artifact.owner is not None:
        raise BoundStrategyError("bound initialization needs a new unowned version")
    resolved_store = _resolve_store(store)
    registry = _registry(resolved_store)
    with exclusive_version_lock(resolve_runtime_root(runtime_root)):
        target = version_dir(artifact.id, version, runtime_root=runtime_root)
        if target.is_symlink() or target.exists():
            raise BoundStrategyError(f"version directory already exists: {version}")
        key = RegistrationKey.parse(owner, artifact.id, version)
        if registry.get(key) is not None:
            raise BoundStrategyError(f"version is already registered: {version}")
        reject_cross_owner_claim(registry, key)
        source = _root_source_bytes(artifact.id, runtime_root)
        registration = registry.register_pending(key, SourceFacts.from_bytes(source))
        if resolved_store.update_artifact(replace(artifact, owner=owner)) is None:
            raise LookupError(f"strategy {artifact.id!r} disappeared during initialization")
    return registration


def snapshot_version(
    strategy_id: str,
    *,
    store: SqliteStrategyStore | None = None,
    runtime_root: Path | None = None,
) -> str:
    """Pin one strategy version, dispatching bound snapshots to the publisher.

    Binding absent selects the legacy snapshot unchanged.  A configured binding
    publishes only when the registry proves the bound owner already owns this
    exact strategy; otherwise the legacy snapshot is used and nothing is
    registered.  ``Artifact.owner`` alone is never sufficient proof.
    """
    resolved_store = _resolve_store(store)
    owner = optional_deployment_owner()
    if owner is not None:
        artifact = resolved_store.get_artifact(strategy_id)
        if artifact is None:
            raise LookupError(f"strategy {strategy_id!r} does not exist")
        version = artifact.artifact_version
        if (
            version
            and artifact.kind == "code"
            and not artifact.demo
            and _registry(resolved_store).has_strategy(owner, strategy_id)
        ):
            _publish(resolved_store, artifact, owner, version, runtime_root)
            return version
    return library_store.snapshot(strategy_id, store=resolved_store, runtime_root=runtime_root)


def _publish(
    store: SqliteStrategyStore,
    artifact: Artifact,
    owner: OwnerUserId,
    version: str,
    runtime_root: Path | None,
) -> None:
    """Publish the root bytes through the lineage-backed publisher."""
    key = RegistrationKey.parse(owner, artifact.id, version)
    spec_bytes = ensure_spec_file(artifact, runtime_root=runtime_root)
    sources = dict(code_sources(artifact.id, runtime_root=runtime_root))
    payload = PublicationPayload(
        spec_bytes=spec_bytes,
        config_bytes=sources[VERSION_CONFIG_FILENAME].read_bytes(),
        source_bytes=sources[SIGNAL_ENGINE_RELPATH].read_bytes(),
    )
    root = resolve_runtime_root(runtime_root)
    StrategyVersionPublisher(_registry(store), root).publish(key, payload)


def _root_source_bytes(strategy_id: str, runtime_root: Path | None) -> bytes:
    """Return the exact root engine bytes, refusing a missing or empty file."""
    sources = dict(code_sources(strategy_id, runtime_root=runtime_root))
    return sources[SIGNAL_ENGINE_RELPATH].read_bytes()


def _registry(store: SqliteStrategyStore) -> SqliteVersionRegistry:
    return SqliteVersionRegistry(store.db_path)


def _resolve_store(store: SqliteStrategyStore | None) -> SqliteStrategyStore:
    return get_store() if store is None else store
