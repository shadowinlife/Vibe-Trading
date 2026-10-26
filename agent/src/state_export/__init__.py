"""State export/import for the strategy library (``vt-state-v1``).

This package owns the layout-versioned state blob that lets the strategy
library survive a sandbox rebuild: the manifest contract, per-file digests, the
content digest used for idempotency, the path fence, and the error taxonomy the
``/state/export`` and ``/state/import`` routes map onto HTTP statuses.

Public surface:

* :data:`LAYOUT_VERSION`, :data:`MANIFEST_JSON_NAME`, :data:`DATA_DIRNAME` —
  blob layout anchors.
* :data:`MAX_FILE_COUNT`, :data:`MAX_BLOB_BYTES`, :data:`EXCLUDED_PATHS` —
  budgets and the user-asset fence.
* :class:`StateManifest`, :class:`StateFile` — the frozen manifest contract.
* :func:`build_manifest`, :func:`validate_manifest` — produce and verify it.
* :func:`sha256_file`, :func:`validate_relpath`,
  :func:`compute_content_sha256` — the primitives both sides share.
* :class:`StateExportError` and its subclasses — stable ``code`` values.
"""

from __future__ import annotations

from src.state_export.manifest import (
    DATA_DIRNAME,
    EXCLUDED_PATHS,
    LAYOUT_VERSION,
    MANIFEST_JSON_NAME,
    MAX_BLOB_BYTES,
    MAX_FILE_COUNT,
    BlobTooLarge,
    LayoutUnsupported,
    ManifestInvalid,
    PathEscape,
    StateExportError,
    StateFile,
    StateManifest,
    build_manifest,
    compute_content_sha256,
    sha256_file,
    validate_manifest,
    validate_relpath,
)

__all__ = [
    "DATA_DIRNAME",
    "EXCLUDED_PATHS",
    "LAYOUT_VERSION",
    "MANIFEST_JSON_NAME",
    "MAX_BLOB_BYTES",
    "MAX_FILE_COUNT",
    "BlobTooLarge",
    "LayoutUnsupported",
    "ManifestInvalid",
    "PathEscape",
    "StateExportError",
    "StateFile",
    "StateManifest",
    "build_manifest",
    "compute_content_sha256",
    "sha256_file",
    "validate_manifest",
    "validate_relpath",
]
