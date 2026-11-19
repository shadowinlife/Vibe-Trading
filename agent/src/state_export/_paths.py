"""Path resolution and filesystem primitives shared by state export/import.

The runtime root, the two store database paths and every temporary directory
are resolved here so export and import cannot drift into two layouts. Every
helper below is deliberately small and side-effect-scoped: temp directories are
created directly under the runtime root (same filesystem), and removals report
failures instead of raising so a rollback can finish its remaining steps.
"""

from __future__ import annotations

import math
import os
import shutil
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.accessor import get_env_config
from src.config.paths import get_runtime_root
from src.strategy_library.paths import resolve_runtime_root

__all__ = ["MAX_RUN_PAYLOAD_BYTES", "default_db_paths"]

#: Hard cap on the ``scope=run`` JSON payload (2 MiB, per change request 0004).
MAX_RUN_PAYLOAD_BYTES = 2 * 1024 * 1024

#: Directory holding the irreversible strategy snapshots.
_STRATEGY_DIRNAME = "strategies"
#: SQLite sidecars that must never survive a replace: ``-journal`` is the
#: rollback journal of the same stale-generated-file class as ``-wal``/``-shm``.
_DB_SIDECARS = ("-wal", "-shm", "-journal")
#: Temp directories are created under the runtime root so every rename stays
#: on one filesystem.
_EXPORT_TMP_PREFIX = ".state-export-"
_IMPORT_TMP_PREFIX = ".state-import-"
_IMPORT_BACKUP_PREFIX = ".state-import-backup-"
#: Deterministic tar member metadata.
_TAR_MEMBER_MODE = 0o644
_IO_CHUNK_BYTES = 1024 * 1024


def default_db_paths(runtime_root: Path | None = None) -> dict[str, Path]:
    """Return the database name → path mapping for one runtime root.

    A non-empty ``VIBE_TRADING_STRATEGY_STORE_DB_PATH`` /
    ``VIBE_TRADING_STRATEGY_DISCOVERY_DB_PATH`` override wins, matching the
    stores' own resolution; otherwise the file sits directly under
    ``runtime_root``.

    Args:
        runtime_root: Explicit runtime root (tests); defaults to the configured
            runtime root.

    Returns:
        ``{"strategy_store.db": ..., "strategy_discovery.db": ...}``.
    """
    root = _resolved_root(runtime_root)
    overrides = get_env_config().paths
    raw_paths = {
        "strategy_store.db": overrides.vibe_trading_strategy_store_db_path,
        "strategy_discovery.db": overrides.vibe_trading_strategy_discovery_db_path,
    }
    resolved: dict[str, Path] = {}
    for name, raw in raw_paths.items():
        value = raw.strip()
        resolved[name] = Path(value).expanduser() if value else root / name
    return resolved


def _resolved_root(runtime_root: Path | None) -> Path:
    """Return the explicit runtime root or the configured one."""
    return resolve_runtime_root(runtime_root) if runtime_root is not None else get_runtime_root()


def _resolved_db_paths(
    runtime_root: Path | None,
    db_paths: Mapping[str, Path] | None,
) -> dict[str, Path]:
    """Validate caller-supplied database names or fall back to the defaults."""
    if db_paths is None:
        return default_db_paths(runtime_root)
    resolved: dict[str, Path] = {}
    for name, path in db_paths.items():
        if not isinstance(name, str) or not _is_safe_segment(name) or name == _STRATEGY_DIRNAME:
            raise ValueError(f"unsafe database name: {name!r}")
        resolved[name] = Path(path).expanduser()
    return resolved


def _is_safe_segment(value: str) -> bool:
    """Return whether *value* is one safe path segment."""
    if not isinstance(value, str) or not value or value in (".", ".."):
        return False
    return not any(character in value for character in ("/", "\\", "\x00"))


def _read_only_uri(path: Path) -> str:
    """Return a percent-encoded ``file:`` URI that opens *path* read-only."""
    return f"{path.resolve().as_uri()}?mode=ro"


def _make_temp_dir(root: Path, prefix: str) -> Path:
    """Create a uniquely named directory directly under *root*."""
    path = root / f"{prefix}{uuid.uuid4().hex}"
    path.mkdir()
    return path


def _move(source: Path, destination: Path, moves: list[tuple[Path, Path]]) -> None:
    """Rename *source* to *destination* and record the reversible move."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)
    moves.append((source, destination))


def _unlink(path: Path) -> list[OSError]:
    """Delete a file if present, returning (not raising) the failure."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return [exc]
    return []


def _remove_path(path: Path) -> list[OSError]:
    """Remove a file or directory tree, returning (not raising) failures."""
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
    except OSError as exc:
        return [exc]
    return []


def _remove_tree(path: Path | None) -> None:
    """Best-effort removal of a private export/import directory."""
    if path is None:
        return
    _remove_path(path)


def _json_safe(value: Any) -> Any:
    """Recursively replace non-finite floats with ``None`` for strict JSON."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _epoch_ms_to_iso(value: int) -> str:
    """Render an epoch-millisecond stamp as a UTC ISO-8601 string."""
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
