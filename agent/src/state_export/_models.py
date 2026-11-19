"""Value objects returned by the state export and import operations."""

from __future__ import annotations

from dataclasses import dataclass

from src.state_export.manifest import StateManifest

__all__ = ["ExportBundle", "ImportResult"]


@dataclass(frozen=True, slots=True)
class ExportBundle:
    """One complete ``scope=full`` state blob.

    ``body`` is the gzip tar; ``manifest`` describes its content and is also
    embedded as ``manifest.json``. ``content_sha256`` is the manifest's
    order-independent digest, used by the caller for idempotency.
    """

    body: bytes
    manifest: StateManifest
    layout_version: str
    content_sha256: str

    @property
    def blob(self) -> bytes:
        """Alias of :attr:`body` for callers that name the payload ``blob``."""
        return self.body


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Outcome of one successful import.

    ``strategies`` and ``versions`` are the self-checked counts of strategy and
    version directories left on disk; ``reopened`` is ``True`` because the
    import always drops the process store handle.
    """

    layout_version: str
    content_sha256: str
    strategies: int
    versions: int
    reopened: bool
