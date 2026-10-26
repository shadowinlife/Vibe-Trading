"""Manifest contract for ``vt-state-v1`` state export/import.

A blob is a gzip-compressed tar carrying ``manifest.json`` beside a ``data/``
tree. The manifest records every file relative to ``data/`` with its sha256 and
size, never absolute paths and never credentials: the run ledger, run
artifacts, sessions, uploads, legacy scheduled jobs and the RM agent token are
fenced by :data:`EXCLUDED_PATHS`.

Each entry's ``sha256`` proves one file byte-identical before an importer
touches live state; ``content_sha256`` folds the sorted ``(path, sha256)``
pairs into one order-independent value that RM stores and compares on the next
export. Paths follow :mod:`src.strategy_library.paths`: POSIX-relative only,
``..``/NUL/backslashes/absolute prefixes rejected, and a declared entry must
resolve inside the data root with no symlinked component. This module reads no
database, spawns no process and touches no network.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LAYOUT_VERSION = "vt-state-v1"
MANIFEST_JSON_NAME = "manifest.json"
DATA_DIRNAME = "data"
MAX_BLOB_BYTES = 64 * 1024 * 1024
MAX_FILE_COUNT = 2000
EXCLUDED_PATHS = (
    "strategy_runs.sqlite3",
    "runs",
    "sessions",
    "uploads",
    "scheduled_research",
    ".config/rm/agent-token",
)

_IO_CHUNK_BYTES = 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_DRIVE_LETTER_RE = re.compile(r"[A-Za-z]:")
_MANIFEST_KEYS = frozenset({"layout_version", "created_at", "content_sha256", "files", "databases", "excluded"})
_ENTRY_KEYS = frozenset({"path", "sha256", "size"})


class StateExportError(Exception):
    """Base class for every state export/import failure.

    Subclasses fix the stable ``code`` that the routes map onto HTTP statuses,
    so callers never parse a message.
    """

    code: str = "state_export_error"


class ManifestInvalid(StateExportError):
    """The manifest is missing, structurally wrong, or contradicts its files."""

    code = "manifest_invalid"


class PathEscape(StateExportError):
    """A manifest path could leave the state data root."""

    code = "path_escape"


class BlobTooLarge(StateExportError):
    """The state blob exceeds the file-count or byte budget."""

    code = "blob_too_large"


class LayoutUnsupported(StateExportError):
    """The manifest declares a layout version this build cannot read."""

    code = "layout_version_unsupported"


@dataclass(frozen=True, slots=True)
class StateFile:
    """One file recorded in a state manifest.

    ``path`` is POSIX-relative to the blob's ``data/`` directory; ``sha256`` is
    the lowercase hex digest of its exact bytes and ``size`` their length.
    """

    path: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class StateManifest:
    """The validated description of one ``vt-state-v1`` state blob.

    ``files`` and ``databases`` are sorted by path; ``excluded`` records the
    omitted paths in caller order. ``layout_version`` is the only compatibility
    anchor and ``content_sha256`` is the idempotency digest.
    """

    layout_version: str
    created_at: str
    content_sha256: str
    files: tuple[StateFile, ...]
    databases: tuple[StateFile, ...]
    excluded: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, JSON-serializable manifest payload.

        Entries are re-sorted by path so a hand-built instance serializes like
        one produced by :func:`build_manifest`.

        Returns:
            Exactly the six manifest keys, with entries as
            ``path``/``sha256``/``size`` objects.
        """
        return {
            "layout_version": self.layout_version,
            "created_at": self.created_at,
            "content_sha256": self.content_sha256,
            "files": [{"path": e.path, "sha256": e.sha256, "size": e.size} for e in sorted(self.files, key=_path)],
            "databases": [
                {"path": e.path, "sha256": e.sha256, "size": e.size} for e in sorted(self.databases, key=_path)
            ],
            "excluded": list(self.excluded),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StateManifest:
        """Parse *payload* strictly into a manifest.

        Unknown or missing keys, wrong types, non-canonical digests, unsafe
        paths and duplicate paths are refused. ``layout_version`` is preserved
        so :func:`validate_manifest` can report an unsupported layout
        separately.

        Args:
            payload: Decoded ``manifest.json`` contents.

        Returns:
            The parsed manifest, entries sorted by path.

        Raises:
            ManifestInvalid: When the payload is not a mapping or violates the
                manifest structure.
            PathEscape: When a declared path is absolute or points upwards.
        """
        if not isinstance(payload, Mapping):
            raise ManifestInvalid(f"manifest must be an object, got {type(payload).__name__}")
        _require_keys(payload, _MANIFEST_KEYS, what="manifest")
        files = _parse_entries(payload["files"], what="files")
        databases = _parse_entries(payload["databases"], what="databases")
        excluded = _parse_excluded(payload["excluded"])
        _reject_duplicate_paths((*files, *databases))
        return cls(
            layout_version=_require_text(payload["layout_version"], "layout_version"),
            created_at=_require_text(payload["created_at"], "created_at"),
            content_sha256=_require_sha256(payload["content_sha256"], "content_sha256"),
            files=files,
            databases=databases,
            excluded=excluded,
        )


def sha256_file(path: Path) -> str:
    """Return the lowercase hex sha256 of *path*, streamed in 1 MiB chunks.

    Args:
        path: File to hash.

    Returns:
        The 64-character lowercase hex digest.

    Raises:
        OSError: When the file cannot be opened or read.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_IO_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_relpath(path: str) -> str:
    """Return *path* when it is a safe POSIX path relative to the data root.

    The path must be a non-empty string using ``/`` separators with no absolute
    prefix, drive letter, empty/``.``/``..`` segment, trailing separator, NUL or
    excluded-tree prefix. Non-canonical forms are rejected, not rewritten.

    Args:
        path: Candidate manifest path.

    Returns:
        The validated relative POSIX path.

    Raises:
        PathEscape: When absolute, drive-lettered, NUL/backslash-bearing, or
            traversing with ``..``.
        ManifestInvalid: When not a string, empty, with an empty/``.`` segment,
            a trailing ``/``, or an :data:`EXCLUDED_PATHS` prefix.
    """
    normalized = _check_path_shape(path)
    if _is_excluded(normalized, EXCLUDED_PATHS):
        raise ManifestInvalid(f"path {normalized!r} is excluded from state export")
    return normalized


def compute_content_sha256(entries: Iterable[StateFile]) -> str:
    """Fold *entries* into the manifest's order-independent content digest.

    The digest is the sha256 of the ``"path:sha256"`` lines, sorted by path and
    joined by newlines, so reordering the input never changes it.

    Args:
        entries: File entries, usually ``files + databases``.

    Returns:
        The hex digest; the empty digest for no entries.

    Raises:
        ManifestInvalid: When an entry is not a :class:`StateFile` or two
            entries share a path.
    """
    materialized = list(entries)
    for index, entry in enumerate(materialized):
        if not isinstance(entry, StateFile):
            raise ManifestInvalid(f"entry {index} must be a StateFile, got {type(entry).__name__}")
    ordered = sorted(materialized, key=_path)
    _reject_duplicate_paths(ordered)
    payload = "\n".join(f"{entry.path}:{entry.sha256}" for entry in ordered)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_manifest(
    files: Sequence[tuple[str, Path]],
    databases: Sequence[tuple[str, Path]],
    *,
    excluded: Sequence[str] = EXCLUDED_PATHS,
    created_at: str | None = None,
) -> StateManifest:
    """Hash *files* and *databases* into a ``vt-state-v1`` manifest.

    Each pair is ``(relpath, absolute_path)``; only *relpath* is recorded, so
    the manifest never leaks the runtime root. Symlinked/non-regular sources,
    duplicate paths and excluded paths are refused, and the combined count and
    size are capped.

    Args:
        files: Non-database files to record.
        databases: Database snapshot files to record.
        excluded: Paths the blob deliberately omits, recorded verbatim.
        created_at: ISO 8601 timestamp; defaults to the current UTC time.

    Returns:
        A manifest whose entries are sorted by path.

    Raises:
        ManifestInvalid: On a bad pair, unsafe/duplicate/excluded path, or a
            source that is missing, not a regular file, or a symlink.
        PathEscape: When a declared path is absolute or points upwards.
        BlobTooLarge: When the file count or total bytes exceed the budget.
    """
    stamp = datetime.now(timezone.utc).isoformat() if created_at is None else _require_text(created_at, "created_at")
    excluded_entries = _parse_excluded(excluded)
    file_entries = _build_entries(files, excluded_entries, what="files")
    database_entries = _build_entries(databases, excluded_entries, what="databases")
    combined = (*file_entries, *database_entries)
    _reject_duplicate_paths(combined)
    _enforce_limits(combined)
    return StateManifest(
        layout_version=LAYOUT_VERSION,
        created_at=stamp,
        content_sha256=compute_content_sha256(combined),
        files=tuple(sorted(file_entries, key=_path)),
        databases=tuple(sorted(database_entries, key=_path)),
        excluded=excluded_entries,
    )


def validate_manifest(
    payload: Mapping[str, Any],
    data_root: Path,
    *,
    expected_layout: str = LAYOUT_VERSION,
) -> StateManifest:
    """Validate *payload* against the files on disk under *data_root*.

    The layout is checked first so an unreadable blob reports
    :class:`LayoutUnsupported` before any file is touched. Every declared entry
    must then resolve inside *data_root* through non-symlinked components, be a
    regular file, and match its recorded size and sha256. The content digest is
    recomputed and must equal the declared one.

    Args:
        payload: Decoded ``manifest.json`` contents.
        data_root: Directory holding the blob's ``data/`` contents.
        expected_layout: Layout this build can read.

    Returns:
        The validated manifest.

    Raises:
        LayoutUnsupported: When ``layout_version`` differs from
            *expected_layout*.
        ManifestInvalid: On structural problems, a missing/non-directory data
            root, a missing or non-regular file, or a size/sha/digest mismatch.
        PathEscape: When a declared path or ancestor is a symlink or resolves
            outside *data_root*.
        BlobTooLarge: When the declared count or total bytes exceed the budget.
    """
    if not isinstance(payload, Mapping):
        raise ManifestInvalid(f"manifest must be an object, got {type(payload).__name__}")
    declared_layout = payload.get("layout_version")
    if not isinstance(declared_layout, str) or not declared_layout:
        raise ManifestInvalid("manifest.layout_version must be a non-empty string")
    if declared_layout != expected_layout:
        raise LayoutUnsupported(f"unsupported layout_version {declared_layout!r}; expected {expected_layout!r}")
    manifest = StateManifest.from_dict(payload)
    root = Path(data_root)
    if not root.is_dir():
        raise ManifestInvalid(f"data root is missing or not a directory: {root}")
    resolved_root = root.resolve()
    entries = (*manifest.files, *manifest.databases)
    _enforce_limits(entries)
    for entry in entries:
        if _is_excluded(entry.path, manifest.excluded):
            raise ManifestInvalid(f"path {entry.path!r} is excluded by the manifest")
        _verify_entry(root, resolved_root, entry)
    if compute_content_sha256(entries) != manifest.content_sha256:
        raise ManifestInvalid("content_sha256 does not match the declared file list")
    return manifest


def _path(entry: StateFile) -> str:
    """Sort key for manifest entries."""
    return entry.path


def _check_path_shape(path: object) -> str:
    """Enforce the structural path fence shared by every entry point."""
    if not isinstance(path, str):
        raise ManifestInvalid(f"path must be a string, got {type(path).__name__}")
    if not path:
        raise ManifestInvalid("path must not be empty")
    if "\x00" in path:
        raise PathEscape(f"path {path!r} must not contain NUL")
    if "\\" in path:
        raise PathEscape(f"path {path!r} must use POSIX separators")
    if path.startswith("/"):
        raise PathEscape(f"path {path!r} must be relative, not absolute")
    if _DRIVE_LETTER_RE.match(path):
        raise PathEscape(f"path {path!r} must not start with a drive letter")
    if path.endswith("/"):
        raise ManifestInvalid(f"path {path!r} must not end with '/'")
    segments = path.split("/")
    if "" in segments:
        raise ManifestInvalid(f"path {path!r} must not contain empty segments")
    if ".." in segments:
        raise PathEscape(f"path {path!r} must not traverse with '..'")
    if "." in segments:
        raise ManifestInvalid(f"path {path!r} must not contain '.' segments")
    return path


def _is_excluded(path: str, entries: Sequence[str]) -> bool:
    """Return whether *path* equals or sits under one of *entries*."""
    return any(path == entry or path.startswith(f"{entry}/") for entry in entries)


def _require_keys(mapping: Mapping[Any, Any], expected: frozenset[str], *, what: str) -> None:
    """Reject missing and unknown keys so the manifest shape stays frozen."""
    actual = set(mapping.keys())
    if missing := expected - actual:
        raise ManifestInvalid(f"{what} is missing keys: {sorted(map(str, missing))}")
    if extra := actual - expected:
        raise ManifestInvalid(f"{what} has unknown keys: {sorted(map(str, extra))}")


def _require_text(value: object, what: str) -> str:
    """Return *value* when it is a non-empty string."""
    if not isinstance(value, str) or not value:
        raise ManifestInvalid(f"{what} must be a non-empty string")
    return value


def _require_sha256(value: object, what: str) -> str:
    """Return *value* when it is a lowercase 64-character hex digest."""
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ManifestInvalid(f"{what} must be a lowercase 64-character hex sha256")
    return value


def _parse_entries(value: object, *, what: str) -> tuple[StateFile, ...]:
    """Parse a manifest ``files``/``databases`` list into sorted entries."""
    if not isinstance(value, (list, tuple)):
        raise ManifestInvalid(f"manifest.{what} must be a list, got {type(value).__name__}")
    entries: list[StateFile] = []
    for index, item in enumerate(value):
        where = f"manifest.{what}[{index}]"
        if not isinstance(item, Mapping):
            raise ManifestInvalid(f"{where} must be an object")
        _require_keys(item, _ENTRY_KEYS, what=where)
        size = item["size"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ManifestInvalid(f"{where}.size must be a non-negative integer")
        entries.append(
            StateFile(
                path=validate_relpath(item["path"]),
                sha256=_require_sha256(item["sha256"], f"{where}.sha256"),
                size=size,
            )
        )
    _reject_duplicate_paths(entries)
    return tuple(sorted(entries, key=_path))


def _parse_excluded(value: object) -> tuple[str, ...]:
    """Parse the manifest ``excluded`` list, preserving caller order."""
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ManifestInvalid("manifest.excluded must be a list of paths")
    entries: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        entry = _check_path_shape(item)
        if entry in seen:
            raise ManifestInvalid(f"manifest.excluded[{index}] duplicates {entry!r}")
        seen.add(entry)
        entries.append(entry)
    return tuple(entries)


def _reject_duplicate_paths(entries: Iterable[StateFile]) -> None:
    """Refuse two entries that claim the same manifest path."""
    seen: set[str] = set()
    for entry in entries:
        if entry.path in seen:
            raise ManifestInvalid(f"duplicate manifest path: {entry.path!r}")
        seen.add(entry.path)


def _enforce_limits(entries: Sequence[StateFile]) -> None:
    """Refuse a blob that exceeds the declared file-count or byte budget."""
    if len(entries) > MAX_FILE_COUNT:
        raise BlobTooLarge(f"state blob has {len(entries)} files; limit is {MAX_FILE_COUNT}")
    total_bytes = sum(entry.size for entry in entries)
    if total_bytes > MAX_BLOB_BYTES:
        raise BlobTooLarge(f"state blob is {total_bytes} bytes; limit is {MAX_BLOB_BYTES}")


def _build_entries(
    pairs: Sequence[tuple[str, Path]],
    excluded: Sequence[str],
    *,
    what: str,
) -> list[StateFile]:
    """Hash each ``(relpath, absolute_path)`` pair into a ``StateFile``."""
    if isinstance(pairs, (str, bytes)) or not isinstance(pairs, Sequence):
        raise ManifestInvalid(f"{what} must be a sequence of (relpath, path) pairs")
    entries: list[StateFile] = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ManifestInvalid(f"{what}[{index}] must be a (relpath, path) pair")
        relpath, source = pair
        path = validate_relpath(relpath)
        if _is_excluded(path, excluded):
            raise ManifestInvalid(f"{what}[{index}] path {path!r} is excluded")
        candidate = Path(source)
        if candidate.is_symlink():
            raise ManifestInvalid(f"{what}[{index}] source is a symlink: {candidate}")
        if not candidate.is_file():
            raise ManifestInvalid(f"{what}[{index}] source is not a regular file: {candidate}")
        entries.append(StateFile(path=path, sha256=sha256_file(candidate), size=candidate.stat().st_size))
    return entries


def _verify_entry(root: Path, resolved_root: Path, entry: StateFile) -> None:
    """Prove one declared entry resolves inside *root* and matches its hash."""
    candidate = root.joinpath(*entry.path.split("/"))
    for ancestor in candidate.parents:
        if ancestor == root:
            break
        if ancestor.is_symlink():
            raise PathEscape(f"path {entry.path!r} has symlinked component {ancestor}")
    if candidate.is_symlink():
        raise PathEscape(f"path {entry.path!r} is a symlink")
    if not candidate.resolve().is_relative_to(resolved_root):
        raise PathEscape(f"path {entry.path!r} resolves outside {resolved_root}")
    if not candidate.is_file():
        raise ManifestInvalid(f"path {entry.path!r} is missing or not a regular file")
    actual_size = candidate.stat().st_size
    if actual_size != entry.size:
        raise ManifestInvalid(f"path {entry.path!r} is {actual_size} bytes; manifest says {entry.size}")
    if sha256_file(candidate) != entry.sha256:
        raise ManifestInvalid(f"path {entry.path!r} does not match its declared sha256")
