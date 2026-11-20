"""Typed failures for strategy-version registration and locking."""

from __future__ import annotations


class StrategyVersionError(Exception):
    """Base class for strategy-version registry failures."""


class StrategyNotFoundError(StrategyVersionError):
    """Raised when an owner has no registration for the requested strategy."""


class VersionConflictError(StrategyVersionError):
    """Raised when a key is retried with different source facts."""


class VersionTombstonedError(StrategyVersionError):
    """Raised when a tombstoned registration is reused or activated."""


class VersionNotFoundError(StrategyVersionError):
    """Raised when a transition targets an unregistered key."""


class VersionDigestMismatchError(StrategyVersionError):
    """Raised when recorded, manifest and actual version bytes disagree."""


class VersionLockUnavailable(StrategyVersionError):
    """Raised when the cross-process registry lock cannot be acquired."""


class VersionRegistryReadOnlyError(StrategyVersionError):
    """Raised when a write is attempted through a read-only registry."""
