"""Verified read projection for lineage-backed F9 strategy versions.

A version directory is trusted only when four independent facts agree:

* the registration's immutable source facts (path, encoding, sha256, length),
* the sibling ``version-lineage.json`` identity and digest facts,
* the ``manifest.json`` strategy/version identity and file entries,
* the actual bytes of ``strategy.json``, ``config.json``,
  ``code/signal_engine.py`` and ``version-lineage.json``.

Each file is opened once with ``O_NOFOLLOW``, required to be a regular file,
and hashed from the bytes that were read.  Missing or unprovable lineage maps
to :class:`VersionNotFoundError`; a semantic contradiction between recorded,
manifest and actual digests or lengths maps to
:class:`VersionDigestMismatchError`.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Final

from src.strategy_library.paths import (
    SIGNAL_ENGINE_RELPATH,
    STRATEGY_SPEC_FILENAME,
    VERSION_CONFIG_FILENAME,
    VERSION_MANIFEST_FILENAME,
    version_dir,
)
from src.strategy_versions.errors import (
    VersionDigestMismatchError,
    VersionNotFoundError,
)
from src.strategy_versions.models import VersionRegistration, VersionState

LINEAGE_FILENAME: Final = "version-lineage.json"
LINEAGE_SCHEMA: Final = "strategy-version-lineage-v1"

_TREE_FILES: Final = (
    STRATEGY_SPEC_FILENAME,
    VERSION_CONFIG_FILENAME,
    SIGNAL_ENGINE_RELPATH,
    LINEAGE_FILENAME,
)
_LINEAGE_FIELDS: Final = frozenset(
    {
        "schema",
        "owner_user_id",
        "strategy_id",
        "strategy_version",
        "version_id",
        "source_path",
        "source_encoding",
        "source_sha256",
        "source_byte_length",
    }
)
_HEX_DIGITS: Final = frozenset("0123456789abcdef")
_O_NOFOLLOW: Final = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC: Final = getattr(os, "O_CLOEXEC", 0)
_O_NONBLOCK: Final = getattr(os, "O_NONBLOCK", 0)
_READ_CHUNK: Final = 1024 * 1024


def _lineage_payload(registration: VersionRegistration) -> dict[str, Any]:
    """Return the closed lineage document for *registration*."""
    key = registration.key
    source = registration.source
    return {
        "schema": LINEAGE_SCHEMA,
        "owner_user_id": key.owner_user_id,
        "strategy_id": key.strategy_id,
        "strategy_version": key.strategy_version,
        "version_id": registration.version_id,
        "source_path": source.source_path,
        "source_encoding": source.source_encoding,
        "source_sha256": source.sha256,
        "source_byte_length": source.byte_length,
    }


def build_lineage(registration: VersionRegistration) -> bytes:
    """Return canonical lineage bytes binding one registration.

    The document contains no lifecycle state: state lives only in the durable
    registry, so a file copy can never claim a registration is active.
    """
    return json.dumps(
        _lineage_payload(registration),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def verify_source(registration: VersionRegistration, runtime_root: Path | str) -> bytes:
    """Return exact source bytes for an active, fully consistent version.

    Args:
        registration: Persisted registration to verify.
        runtime_root: Runtime root holding ``strategies/``.

    Returns:
        The exact bytes stored at ``code/signal_engine.py``.

    Raises:
        VersionNotFoundError: If the registration is not active, the version
            path is missing or unsafe, lineage is missing/malformed, or the
            lineage/manifest identity does not match the registration.
        VersionDigestMismatchError: If recorded, manifest and actual source
            digests or lengths contradict each other.
    """
    if registration.state is not VersionState.ACTIVE:
        raise VersionNotFoundError(f"registration {registration.version_id} is not active")
    return _verified_tree_bytes(registration, runtime_root)


def _verified_tree_bytes(registration: VersionRegistration, runtime_root: Path | str) -> bytes:
    """Verify the full F9 tree regardless of lifecycle state."""
    root = _version_root(registration, runtime_root)
    files = {
        relative: _read_regular(root.joinpath(*relative.split("/")))
        for relative in (*_TREE_FILES, VERSION_MANIFEST_FILENAME)
    }
    lineage = _parse_lineage(files[LINEAGE_FILENAME])
    _require_lineage_identity(lineage, registration)
    entries = _parse_manifest(files[VERSION_MANIFEST_FILENAME], registration)
    _verify_manifest_entries(entries, files)
    source = files[SIGNAL_ENGINE_RELPATH]
    _require_source_agreement(registration, lineage, source)
    return source


def _version_root(registration: VersionRegistration, runtime_root: Path | str) -> Path:
    """Resolve and validate the fenced version directory without creating it."""
    try:
        root = version_dir(
            registration.key.strategy_id,
            registration.key.strategy_version,
            runtime_root=Path(runtime_root),
        )
    except (OSError, ValueError) as exc:
        raise VersionNotFoundError("version path is missing or unsafe") from exc
    if root.is_symlink() or not root.is_dir():
        raise VersionNotFoundError("version directory is not a regular directory")
    return root


def _read_regular(path: Path) -> bytes:
    """Read one regular, non-symlinked file exactly once."""
    if path.is_symlink():
        raise VersionNotFoundError(f"refusing symlinked version file: {path}")
    try:
        descriptor = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC | _O_NONBLOCK)
    except (FileNotFoundError, NotADirectoryError):
        raise VersionNotFoundError(f"missing version file: {path}") from None
    except OSError as exc:
        raise VersionNotFoundError(f"unreadable version file: {path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise VersionNotFoundError(f"version path is not a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, _READ_CHUNK)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise VersionNotFoundError(f"unreadable version file: {path}") from exc
    finally:
        os.close(descriptor)


def _parse_lineage(data: bytes) -> dict[str, Any]:
    """Parse and structurally validate the closed lineage document."""
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise VersionNotFoundError("version lineage is malformed") from None
    if not isinstance(payload, dict) or set(payload) != _LINEAGE_FIELDS:
        raise VersionNotFoundError("version lineage fields are not closed")
    if payload.get("schema") != LINEAGE_SCHEMA:
        raise VersionNotFoundError("version lineage schema is unknown")
    for field in (
        "owner_user_id",
        "strategy_id",
        "strategy_version",
        "version_id",
        "source_path",
        "source_encoding",
        "source_sha256",
    ):
        if not isinstance(payload.get(field), str):
            raise VersionNotFoundError("version lineage field types are malformed")
    length = payload.get("source_byte_length")
    if isinstance(length, bool) or not isinstance(length, int):
        raise VersionNotFoundError("version lineage length is malformed")
    if _canonical(payload) != data:
        raise VersionNotFoundError("version lineage is not canonical")
    return payload


def _canonical(payload: dict[str, Any]) -> bytes:
    """Return the canonical JSON encoding used for lineage files."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _require_lineage_identity(lineage: dict[str, Any], registration: VersionRegistration) -> None:
    """Require lineage identity fields to equal the registration."""
    key = registration.key
    expected = {
        "owner_user_id": key.owner_user_id,
        "strategy_id": key.strategy_id,
        "strategy_version": key.strategy_version,
        "version_id": registration.version_id,
        "source_path": registration.source.source_path,
        "source_encoding": registration.source.source_encoding,
    }
    if any(lineage[field] != value for field, value in expected.items()):
        raise VersionNotFoundError("version lineage identity does not match")


def _parse_manifest(data: bytes, registration: VersionRegistration) -> dict[str, dict[str, Any]]:
    """Parse a manifest and require its strategy/version identity and files."""
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise VersionNotFoundError("version manifest is malformed") from None
    if not isinstance(payload, dict):
        raise VersionNotFoundError("version manifest is malformed")
    key = registration.key
    if (
        payload.get("strategy_id") != key.strategy_id
        or payload.get("version") != key.strategy_version
        or payload.get("schema_version") != 1
    ):
        raise VersionNotFoundError("version manifest identity does not match")
    files = payload.get("files")
    if not isinstance(files, list):
        raise VersionNotFoundError("version manifest files are malformed")
    entries: dict[str, dict[str, Any]] = {}
    for entry in files:
        if not isinstance(entry, dict):
            raise VersionNotFoundError("version manifest entry is malformed")
        relative = entry.get("path")
        digest = entry.get("sha256")
        size = entry.get("size")
        if (
            not isinstance(relative, str)
            or not isinstance(digest, str)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or relative in entries
        ):
            raise VersionNotFoundError("version manifest entry is malformed")
        entries[relative] = entry
    if set(entries) != set(_TREE_FILES):
        raise VersionNotFoundError("version manifest does not describe the F9 tree")
    return entries


def _verify_manifest_entries(entries: dict[str, dict[str, Any]], files: dict[str, bytes]) -> None:
    """Require each manifest entry to match the file bytes read once."""
    for relative in _TREE_FILES:
        entry = entries[relative]
        data = files[relative]
        if entry["sha256"] != hashlib.sha256(data).hexdigest() or entry["size"] != len(data):
            raise VersionDigestMismatchError(f"manifest contradicts actual bytes for {relative}")


def _require_source_agreement(
    registration: VersionRegistration,
    lineage: dict[str, Any],
    source: bytes,
) -> None:
    """Require registration, lineage and actual source to agree exactly."""
    digest = hashlib.sha256(source).hexdigest()
    length = len(source)
    recorded = registration.source
    if recorded.sha256 != digest or recorded.byte_length != length:
        raise VersionDigestMismatchError("registration contradicts actual source bytes")
    lineage_digest = lineage["source_sha256"]
    lineage_length = lineage["source_byte_length"]
    if (
        not isinstance(lineage_digest, str)
        or len(lineage_digest) != 64
        or any(character not in _HEX_DIGITS for character in lineage_digest)
        or lineage_digest != digest
        or lineage_length != length
    ):
        raise VersionDigestMismatchError("lineage contradicts actual source bytes")
