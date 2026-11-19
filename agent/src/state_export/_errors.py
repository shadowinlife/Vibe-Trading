"""Stable error taxonomy for ``vt-state-v1`` export and import.

Each public exception carries the frozen ``code`` the routes map onto an HTTP
status; callers never parse a message. ``_SelfCheckFailed`` is internal because
the import converts it to :class:`ImportFailed` after rolling back.
"""

from __future__ import annotations

from src.state_export.manifest import StateExportError

__all__ = [
    "BlobCorrupt",
    "ExportUnstable",
    "ImportFailed",
    "ImportInProgress",
    "UnknownRunId",
]


class ExportUnstable(StateExportError):
    """The strategies tree changed while it was being snapshotted."""

    code = "export_unstable"


class BlobCorrupt(StateExportError):
    """The blob is not a readable, self-consistent ``vt-state-v1`` archive."""

    code = "blob_corrupt"


class ImportInProgress(StateExportError):
    """Another import holds the process-wide import lock."""

    code = "import_in_progress"


class ImportFailed(StateExportError):
    """Installing the validated blob failed; the previous state was restored."""

    code = "import_failed"


class UnknownRunId(StateExportError):
    """No run ledger row exists for the requested ``vt_run_id``."""

    code = "unknown_vt_run_id"


class _SelfCheckFailed(Exception):
    """The imported state failed a post-install invariant."""
