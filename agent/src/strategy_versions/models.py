"""Immutable value objects for owner-scoped strategy-version registrations.

A registration is identified by ``(owner_user_id, strategy_id,
strategy_version)``.  Its ``version_id`` is opaque and random (never derived
from the source bytes), so two identical source payloads under different keys
still receive distinct identities.  Source facts capture only what the
registry needs to detect a changed payload: the canonical path, encoding,
SHA-256 digest and byte length.  The source bytes themselves are never stored
on the value object or persisted by the registry.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from dataclasses import dataclass
from enum import Enum

#: Canonical path of the Python signal engine inside a strategy package.
SOURCE_PATH = "code/signal_engine.py"
#: Canonical text encoding of the source payload.
SOURCE_ENCODING = "utf-8"
#: Prefix of every persisted registration identity.
VERSION_ID_PREFIX = "ver_"
#: Number of random bytes behind a registration identity.
VERSION_ID_BYTES = 16

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_VERSION_ID_PATTERN = re.compile(rf"{VERSION_ID_PREFIX}[0-9a-f]{{32}}")


def _require_identity_part(label: str, value: str) -> str:
    """Validate one component of a registration key.

    Args:
        label: Human-readable component name used in error messages.
        value: Candidate component value.

    Returns:
        The unchanged value when valid.

    Raises:
        TypeError: If the value is not a string.
        ValueError: If the value is blank or contains a control character.
    """
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError(f"{label} must not contain control characters")
    return value


@dataclass(frozen=True, slots=True)
class RegistrationKey:
    """Owner-scoped identity of one strategy version.

    Attributes:
        owner_user_id: Authenticated owner of the strategy.
        strategy_id: Stable strategy identifier inside the owner's account.
        strategy_version: Caller-supplied version label.
    """

    owner_user_id: str
    strategy_id: str
    strategy_version: str

    @classmethod
    def parse(cls, owner: str, strategy_id: str, strategy_version: str) -> RegistrationKey:
        """Build a validated key from request-shaped inputs.

        Args:
            owner: Authenticated owner user identifier.
            strategy_id: Stable strategy identifier.
            strategy_version: Caller-supplied version label.

        Returns:
            A frozen registration key.

        Raises:
            TypeError: If any component is not a string.
            ValueError: If any component is blank or contains control
                characters.
        """
        return cls(
            owner_user_id=_require_identity_part("owner", owner),
            strategy_id=_require_identity_part("strategy_id", strategy_id),
            strategy_version=_require_identity_part("strategy_version", strategy_version),
        )


def new_version_id() -> str:
    """Return a fresh opaque registration identity.

    The value is ``"ver_"`` followed by 32 lowercase hex characters drawn
    from :func:`secrets.token_hex`.  Callers must treat it as opaque: it is
    random, never content-derived, and persisted verbatim.

    Returns:
        A new random registration identity.
    """
    return f"{VERSION_ID_PREFIX}{secrets.token_hex(VERSION_ID_BYTES)}"


@dataclass(frozen=True, slots=True)
class SourceFacts:
    """Content facts recorded for one strategy source payload.

    The raw bytes are deliberately absent: they are hashed inside
    :meth:`from_bytes` and discarded, so neither this object nor the registry
    can return them later.

    Attributes:
        source_path: Canonical path inside the strategy package.
        source_encoding: Canonical text encoding of the payload.
        sha256: Lowercase SHA-256 digest of the exact payload bytes.
        byte_length: Exact payload length in bytes.
    """

    source_path: str
    source_encoding: str
    sha256: str
    byte_length: int

    def __post_init__(self) -> None:
        if self.source_path != SOURCE_PATH:
            raise ValueError(f"source_path must be {SOURCE_PATH!r}")
        if self.source_encoding != SOURCE_ENCODING:
            raise ValueError(f"source_encoding must be {SOURCE_ENCODING!r}")
        if _SHA256_PATTERN.fullmatch(self.sha256) is None:
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        if isinstance(self.byte_length, bool) or not isinstance(self.byte_length, int):
            raise TypeError("byte_length must be an integer")
        if self.byte_length < 0:
            raise ValueError("byte_length must be non-negative")

    @classmethod
    def from_bytes(cls, data: bytes) -> SourceFacts:
        """Derive source facts from the exact payload bytes.

        Args:
            data: Bytes of the strategy's signal-engine source file.

        Returns:
            Frozen source facts carrying the canonical path, encoding,
            lowercase SHA-256 digest and byte length.  The bytes themselves
            are not retained.

        Raises:
            TypeError: If ``data`` is not bytes-like.
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("source data must be bytes-like")
        payload = bytes(data)
        return cls(
            source_path=SOURCE_PATH,
            source_encoding=SOURCE_ENCODING,
            sha256=hashlib.sha256(payload).hexdigest(),
            byte_length=len(payload),
        )


class VersionState(str, Enum):
    """Lifecycle state of a persisted registration."""

    PENDING = "pending"
    ACTIVE = "active"
    TOMBSTONED = "tombstoned"


@dataclass(frozen=True, slots=True)
class VersionRegistration:
    """A persisted registration: identity, source facts and lifecycle state.

    Attributes:
        key: Owner-scoped registration key.
        version_id: Opaque random identity, stable for the key's lifetime.
        source: Content facts of the registered payload.
        state: Current lifecycle state.
    """

    key: RegistrationKey
    version_id: str
    source: SourceFacts
    state: VersionState

    def __post_init__(self) -> None:
        if not isinstance(self.key, RegistrationKey):
            raise TypeError("key must be a RegistrationKey")
        if not isinstance(self.source, SourceFacts):
            raise TypeError("source must be SourceFacts")
        if not isinstance(self.state, VersionState):
            raise TypeError("state must be a VersionState")
        if _VERSION_ID_PATTERN.fullmatch(self.version_id) is None:
            raise ValueError("version_id must be 'ver_' plus 32 hex characters")
