"""Synthetic fixtures for the strategy-version domain tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.strategy_versions.models import (
    RegistrationKey,
    SourceFacts,
    VersionRegistration,
    VersionState,
)


def make_key(
    *,
    owner: str = "owner-a",
    strategy_id: str = "strategy-a",
    version: str = "v1",
) -> RegistrationKey:
    return RegistrationKey.parse(owner, strategy_id, version)


def make_source(data: bytes = b"print('ok')\n") -> SourceFacts:
    return SourceFacts.from_bytes(data)


def make_version_id(*parts: str) -> str:
    """Return a deterministic valid identity for synthetic records."""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"ver_{digest[:32]}"


def make_record(
    *,
    owner: str = "owner-a",
    strategy_id: str = "strategy-a",
    version: str = "v1",
    state: VersionState = VersionState.PENDING,
    source: bytes = b"print('ok')\n",
    version_id: str | None = None,
) -> VersionRegistration:
    """Return a valid synthetic registration with a deterministic identity."""
    return VersionRegistration(
        key=make_key(owner=owner, strategy_id=strategy_id, version=version),
        version_id=version_id or make_version_id(owner, strategy_id, version),
        source=make_source(source),
        state=state,
    )


def record_sort_key(record: VersionRegistration) -> tuple[str, str, str]:
    """Return the registry's deterministic owner/key ordering."""
    key = record.key
    return (key.owner_user_id, key.strategy_id, key.strategy_version)


def tree_state(root: Path) -> dict[str, tuple[bytes, int, int]]:
    return {
        path.relative_to(root).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
            path.stat().st_ino,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
