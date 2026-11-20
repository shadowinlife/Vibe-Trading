"""Read-only exact source reads for owner-bound, active strategy versions.

Resolution is fixed: take the shared registry lock, require an owner-exact
strategy registration, require the exact key to be active, verify the full
projection, then compare the caller digest.  Nothing is inferred from paths,
Git, "latest" or a strategy root fallback, and the read never writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import (
    StrategyNotFoundError,
    VersionDigestMismatchError,
    VersionNotFoundError,
)
from src.strategy_versions.locking import shared_version_lock
from src.strategy_versions.models import (
    RegistrationKey,
    VersionRegistration,
    VersionState,
)
from src.strategy_versions.projection import verify_source


@dataclass(frozen=True, slots=True)
class StrategyVersionSource:
    """Exact source bytes and immutable facts for one active version."""

    registration: VersionRegistration
    source_bytes: bytes
    source_sha256: str
    source_byte_length: int
    source_encoding: str


class StrategyVersionSourceReader:
    """Read one exact active version without writes or fallbacks.

    Args:
        registry: Durable version registration registry.
        runtime_root: Runtime root holding ``strategies/``.
    """

    def __init__(self, registry: SqliteVersionRegistry, runtime_root: Path | str) -> None:
        self._registry = registry
        self._runtime_root = Path(runtime_root).expanduser()

    def read(
        self,
        key: RegistrationKey,
        expected_source_sha256: str | None = None,
    ) -> StrategyVersionSource:
        """Return the exact source for *key* or raise a typed refusal.

        Args:
            key: Owner-scoped registration key naming one exact version.
            expected_source_sha256: Optional lowercase digest the caller
                expects; checked only after the stored projection verifies.

        Returns:
            The verified registration and its exact source bytes.

        Raises:
            StrategyNotFoundError: If the owner has no registration for the
                strategy, including absent, legacy and foreign-owner cases.
            VersionNotFoundError: If the exact key is unregistered, not
                active, or cannot prove lineage-backed source bytes.  Also
                raised when a non-active persisted row cannot hydrate.
            VersionDigestMismatchError: If recorded, manifest and actual bytes
                disagree, the expected digest does not match, or an active
                persisted row cannot hydrate its source facts.
        """
        with shared_version_lock(self._runtime_root):
            if not self._registry.has_strategy(key.owner_user_id, key.strategy_id):
                raise StrategyNotFoundError(f"no strategy {key.strategy_id!r} " f"for owner {key.owner_user_id!r}")
            try:
                registration = self._registry.get(key)
            except (ValueError, TypeError) as exc:
                # A persisted row that cannot hydrate is either a tampered
                # active digest (409) or a non-active row that stays 404.
                if self._registry.get_state(key) is VersionState.ACTIVE:
                    raise VersionDigestMismatchError("stored source facts cannot be verified") from exc
                raise VersionNotFoundError(
                    f"no readable registration for {key.owner_user_id!r}/"
                    f"{key.strategy_id!r}/{key.strategy_version!r}"
                ) from exc
            if registration is None or registration.state is not VersionState.ACTIVE:
                raise VersionNotFoundError(
                    f"no active registration for {key.owner_user_id!r}/" f"{key.strategy_id!r}/{key.strategy_version!r}"
                )
            source_bytes = verify_source(registration, self._runtime_root)
            if expected_source_sha256 is not None and expected_source_sha256 != registration.source.sha256:
                raise VersionDigestMismatchError("expected source digest does not match stored bytes")
            return StrategyVersionSource(
                registration=registration,
                source_bytes=source_bytes,
                source_sha256=registration.source.sha256,
                source_byte_length=len(source_bytes),
                source_encoding=registration.source.source_encoding,
            )


__all__ = ["StrategyVersionSource", "StrategyVersionSourceReader"]
