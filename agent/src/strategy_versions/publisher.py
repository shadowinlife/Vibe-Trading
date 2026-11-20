"""Locked, lineage-backed publisher for immutable F9 strategy versions.

Publication order is fixed: register the random identity as ``pending``, build
a hidden staging tree named after that identity, write the exact payload and
lineage, write the manifest last, fsync and rename the tree into place, verify
the projection, then activate.  An existing final tree is never overwritten: it
must already verify against the same registration, otherwise publication fails
closed and the registration stays pending.

The legacy :func:`src.strategy_library.store_io.publish_version` is deliberately
not reused because it accepts an existing directory without proving lineage.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from src.strategy_library.paths import (
    SIGNAL_ENGINE_RELPATH,
    STRATEGY_SPEC_FILENAME,
    VERSION_CONFIG_FILENAME,
    VERSION_MANIFEST_FILENAME,
    atomic_write_json,
    build_manifest,
    strategies_root,
    version_dir,
    versions_dir,
)
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import (
    VersionConflictError,
    VersionTombstonedError,
)
from src.strategy_versions.locking import exclusive_version_lock
from src.strategy_versions.models import (
    RegistrationKey,
    SourceFacts,
    VersionRegistration,
    VersionState,
)
from src.strategy_versions.projection import (
    LINEAGE_FILENAME,
    build_lineage,
    verify_source,
)

_MANIFEST_SCHEMA_VERSION: Final = 1
_STAGING_SUFFIX: Final = ".staging"
_PAYLOAD_FIELDS: Final = ("spec_bytes", "config_bytes", "source_bytes")
_READ_CHUNK: Final = 1024 * 1024
_O_NOFOLLOW: Final = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC: Final = getattr(os, "O_CLOEXEC", 0)


@dataclass(frozen=True, slots=True)
class PublicationPayload:
    """Exact bytes a publication freezes into one version directory."""

    spec_bytes: bytes
    config_bytes: bytes
    source_bytes: bytes

    def __post_init__(self) -> None:
        """Reject non-bytes or empty components before any registration."""
        for field in _PAYLOAD_FIELDS:
            value = getattr(self, field)
            if not isinstance(value, (bytes, bytearray, memoryview)):
                raise TypeError(f"{field} must be bytes-like")
            if len(value) == 0:
                raise ValueError(f"{field} must not be empty")
            object.__setattr__(self, field, bytes(value))


class StrategyVersionPublisher:
    """Publish and retire owner-scoped strategy versions under one lock.

    Args:
        registry: Durable version registration registry.
        runtime_root: Runtime root holding ``strategies/``.
    """

    def __init__(self, registry: SqliteVersionRegistry, runtime_root: Path | str) -> None:
        self._registry = registry
        self._runtime_root = Path(runtime_root).expanduser()

    def publish(self, key: RegistrationKey, payload: PublicationPayload) -> VersionRegistration:
        """Publish *payload* under *key* and return its active registration.

        Replaying an active registration returns it without writing any file.
        A changed source payload for an existing identity raises
        :class:`VersionConflictError`; a tombstoned identity raises
        :class:`VersionTombstonedError`.

        Raises:
            ValueError: If the key cannot form a safe fenced version path.
            VersionConflictError: If the identity exists with different facts.
            VersionTombstonedError: If the identity is tombstoned.
            VersionNotFoundError: If an existing tree cannot prove lineage.
            VersionDigestMismatchError: If recorded and actual bytes disagree.
        """
        facts = SourceFacts.from_bytes(payload.source_bytes)
        self._final_path(key)
        with exclusive_version_lock(self._runtime_root):
            final = self._final_path(key)
            reject_cross_owner_claim(self._registry, key)
            registration = self._registry.get(key)
            if registration is None:
                registration = self._registry.register_pending(key, facts)
                resumed = False
            else:
                self._require_reusable(registration, facts)
                if registration.state is VersionState.ACTIVE:
                    verify_source(registration, self._runtime_root)
                    return registration
                resumed = True
            return self._complete(registration, payload, final, resumed=resumed)

    def tombstone(self, key: RegistrationKey) -> VersionRegistration:
        """Retire *key* durably without deleting any version file."""
        with exclusive_version_lock(self._runtime_root):
            return self._registry.tombstone(key)

    def _require_reusable(self, registration: VersionRegistration, facts: SourceFacts) -> None:
        """Reject tombstoned or fact-changing retries before any write."""
        if registration.state is VersionState.TOMBSTONED:
            raise VersionTombstonedError(f"registration {registration.version_id} is tombstoned")
        if registration.source != facts:
            raise VersionConflictError(f"registration {registration.version_id} stores different source facts")

    def _complete(
        self,
        registration: VersionRegistration,
        payload: PublicationPayload,
        final: Path,
        *,
        resumed: bool,
    ) -> VersionRegistration:
        """Activate an existing matching tree or build and activate a new one."""
        if os.path.lexists(final):
            self._verify_for_activation(registration)
            if resumed:
                self._require_unchanged_payload(final, payload)
            return self._registry.activate(registration.key)
        self._write_tree(registration, payload, final)
        self._verify_for_activation(registration)
        return self._registry.activate(registration.key)

    def _require_unchanged_payload(self, final: Path, payload: PublicationPayload) -> None:
        """Refuse a pending retry whose bytes differ from the existing tree."""
        expected = (
            (STRATEGY_SPEC_FILENAME, payload.spec_bytes),
            (VERSION_CONFIG_FILENAME, payload.config_bytes),
            (SIGNAL_ENGINE_RELPATH, payload.source_bytes),
        )
        for relative, data in expected:
            try:
                existing = _read_final_bytes(final.joinpath(*relative.split("/")))
            except OSError as exc:
                raise VersionConflictError(f"pending retry cannot verify existing {relative}") from exc
            if existing != data:
                raise VersionConflictError(f"pending retry payload differs from existing {relative}")

    def _verify_for_activation(self, registration: VersionRegistration) -> bytes:
        """Verify a pending tree with the same byte contract as a read."""
        return verify_source(replace(registration, state=VersionState.ACTIVE), self._runtime_root)

    def _write_tree(
        self,
        registration: VersionRegistration,
        payload: PublicationPayload,
        final: Path,
    ) -> None:
        """Build one hidden staging tree and rename it into place."""
        versions = versions_dir(registration.key.strategy_id, runtime_root=self._runtime_root)
        versions.mkdir(parents=True, exist_ok=True)
        staging = versions / f".{registration.version_id}{_STAGING_SUFFIX}"
        _discard_staging(staging)
        try:
            staging.mkdir(mode=0o700)
            _write_bytes(staging / STRATEGY_SPEC_FILENAME, payload.spec_bytes)
            _write_bytes(staging / VERSION_CONFIG_FILENAME, payload.config_bytes)
            _write_bytes(
                staging.joinpath(*SIGNAL_ENGINE_RELPATH.split("/")),
                payload.source_bytes,
            )
            _write_bytes(staging / LINEAGE_FILENAME, build_lineage(registration))
            manifest = build_manifest(staging)
            manifest.update(
                strategy_id=registration.key.strategy_id,
                version=registration.key.strategy_version,
                schema_version=_MANIFEST_SCHEMA_VERSION,
            )
            atomic_write_json(staging / VERSION_MANIFEST_FILENAME, manifest)
            _fsync_dir(staging)
            if os.path.lexists(final):
                raise VersionConflictError("version path appeared before publication completed")
            os.replace(staging, final)
            _fsync_dir(final.parent)
        except BaseException:
            _discard_staging(staging)
            raise

    def _final_path(self, key: RegistrationKey) -> Path:
        """Return a validated final path without creating any directory."""
        root = self._runtime_root
        if os.path.lexists(root) and not root.is_dir():
            raise ValueError("runtime root must be a directory")
        strategies = strategies_root(runtime_root=root)
        if strategies.is_symlink() or (os.path.lexists(strategies) and not strategies.is_dir()):
            raise ValueError("strategies root must be a regular directory")
        return version_dir(key.strategy_id, key.strategy_version, runtime_root=root)


def reject_cross_owner_claim(registry: SqliteVersionRegistry, key: RegistrationKey) -> None:
    """Refuse a version path already claimed by a different owner.

    Raises:
        VersionConflictError: If another owner holds the same
            ``(strategy_id, strategy_version)`` in any lifecycle state.
    """
    for record in registry.all_records():
        held = record.key
        if (
            held.strategy_id == key.strategy_id
            and held.strategy_version == key.strategy_version
            and held.owner_user_id != key.owner_user_id
        ):
            raise VersionConflictError(f"version {key.strategy_id!r}/{key.strategy_version!r} belongs to another owner")


def _read_final_bytes(path: Path) -> bytes:
    """Read one existing final-tree file without following symlinks."""
    descriptor = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, _READ_CHUNK)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_bytes(path: Path, data: bytes) -> None:
    """Write one new staging file, fsync it, and close it reliably."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC,
        0o600,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while publishing a version file")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _discard_staging(path: Path) -> None:
    """Remove only the exact staging path owned by one version identity."""
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _fsync_dir(directory: Path) -> None:
    """Best-effort directory fsync so a rename survives a crash."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


__all__ = ["PublicationPayload", "StrategyVersionPublisher", "reject_cross_owner_claim"]
