"""Public facade for the ``vt-state-v1`` export, import and run payload.

The implementation is split by responsibility so each module stays reviewable:

* :mod:`._errors` — stable error codes.
* :mod:`._models` — ``ExportBundle`` / ``ImportResult``.
* :mod:`._paths` — runtime-root, database-path and filesystem primitives.
* :mod:`._export` — consistent database snapshots plus the verified tree archive.
* :mod:`._archive` — tar member fencing and extraction.
* :mod:`._install` — backup, atomic install, self-check and rollback.
* :mod:`._run_report` — the bounded ``scope=run`` payload.

This facade keeps the process-wide import lock and the frozen public names.
Callers should import from here, not from the private modules.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path

from src.state_export._errors import (
    BlobCorrupt,
    ExportUnstable,
    ImportFailed,
    ImportInProgress,
    UnknownRunId,
)
from src.state_export._export import export_full
from src.state_export._install import import_locked
from src.state_export._models import ExportBundle, ImportResult
from src.state_export._paths import MAX_RUN_PAYLOAD_BYTES, default_db_paths
from src.state_export._run_report import export_run
from src.state_export.manifest import LAYOUT_VERSION, MAX_BLOB_BYTES, BlobTooLarge, LayoutUnsupported

__all__ = [
    "MAX_RUN_PAYLOAD_BYTES",
    "BlobCorrupt",
    "ExportBundle",
    "ExportUnstable",
    "ImportFailed",
    "ImportInProgress",
    "ImportResult",
    "UnknownRunId",
    "default_db_paths",
    "export_full",
    "export_run",
    "import_full",
]

#: Serialises imports for the whole process; the contract is single-flight.
_IMPORT_LOCK = threading.Lock()


def import_full(
    blob: bytes,
    layout: str,
    *,
    runtime_root: Path | None = None,
    db_paths: Mapping[str, Path] | None = None,
) -> ImportResult:
    """Replace the live user state with the validated content of *blob*.

    The blob is extracted under the runtime root, every declared digest is
    checked, and only then is the live state moved aside. Installation is a
    sequence of same-filesystem renames: strategies first, then each database
    without stale sidecars, then the generation marker. A failure anywhere
    after the backup restores the previous strategies, databases, sidecars and
    marker, so the caller never observes a half-imported library. If the
    rollback itself cannot finish, the backup directory is retained on disk and
    named in a warning.

    Args:
        blob: Complete ``scope=full`` gzip tar.
        layout: Caller-declared layout version; must equal ``vt-state-v1``.
        runtime_root: Explicit runtime root (tests); defaults to the configured
            runtime root.
        db_paths: Database name → path overrides; defaults to the configured
            store paths.

    Returns:
        The imported layout, content digest and self-checked counts.

    Raises:
        LayoutUnsupported: When *layout* is not the supported version.
        BlobCorrupt: When the archive, manifest or a digest is unreadable.
        BlobTooLarge: When the blob or its content exceeds the budget.
        PathEscape: When a member or a live path is a symlink/escape.
        ManifestInvalid: When the manifest is structurally wrong.
        ImportInProgress: When another import holds the lock.
        ImportFailed: When installation or the post-install check failed.
    """
    if layout != LAYOUT_VERSION:
        raise LayoutUnsupported(f"unsupported layout {layout!r}; expected {LAYOUT_VERSION!r}")
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise BlobCorrupt("state blob must be bytes")
    data = bytes(blob)
    if not data:
        raise BlobCorrupt("state blob is empty")
    if len(data) > MAX_BLOB_BYTES:
        raise BlobTooLarge(f"state blob is {len(data)} bytes; limit is {MAX_BLOB_BYTES}")
    if not _IMPORT_LOCK.acquire(blocking=False):
        raise ImportInProgress("another state import is already running")
    try:
        return import_locked(data, runtime_root, db_paths)
    finally:
        _IMPORT_LOCK.release()
