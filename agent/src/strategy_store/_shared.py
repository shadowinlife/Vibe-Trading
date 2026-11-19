"""Shared store singleton for SDM tools.

All SDM tools (sdm_register, sdm_status, sdm_decay_scan) import
``get_store()`` from this module to share a single ``SqliteStrategyStore``
instance.  The SQLite backend provides WAL-mode persistence with FK
constraints and schema migrations via ``PRAGMA user_version``.

The singleton also tracks the ``state-generation`` marker written by a state
import.  An import replaces the SQLite file behind a live process, so a plain
cached connection would keep reading the old inode.  ``get_store()`` therefore
compares the current marker on every call and closes/reopens the store when it
changed; an unchanged marker returns the same instance without rebuilding the
connection.
"""

from __future__ import annotations

import threading
from pathlib import Path

from src.config.paths import get_runtime_root
from src.strategy_store.sqlite_store import SqliteStrategyStore

#: Name of the marker file written after a state import.  Its contents are the
#: imported snapshot's content hash — never a credential.
STATE_GENERATION_FILENAME = "state-generation"

_lock = threading.Lock()
_store: SqliteStrategyStore | None = None
#: Marker seen when ``_store`` was opened; ``None`` means "not bound yet",
#: which is also how an externally injected store is recognised.
_store_generation: str | None = None
#: The instance this module itself bound.  Tests replace ``_store`` directly;
#: comparing identities keeps ``get_store()`` from closing a store it did not
#: open (and did not stamp with a generation).
_bound_store: SqliteStrategyStore | None = None


def state_generation_path(runtime_root: Path | None = None) -> Path:
    """Return the path of the ``state-generation`` marker.

    Args:
        runtime_root: Runtime root to read from.  Defaults to the configured
            runtime root (``VIBE_TRADING_HOME`` or ``~/.vibe-trading``).

    Returns:
        ``<runtime_root>/state-generation``.
    """
    root = runtime_root if runtime_root is not None else get_runtime_root()
    return root / STATE_GENERATION_FILENAME


def read_state_generation(runtime_root: Path | None = None) -> str:
    """Return the current state-generation marker text.

    Args:
        runtime_root: Runtime root to read from.  Defaults to the configured
            runtime root.

    Returns:
        The marker text with surrounding whitespace removed.  A missing,
        unreadable, or blank marker reads as ``""``.
    """
    try:
        return state_generation_path(runtime_root).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def write_state_generation(value: str, runtime_root: Path | None = None) -> None:
    """Atomically replace the ``state-generation`` marker.

    The caller supplies an opaque generation value: the imported snapshot's
    content hash plus a per-import nonce, so importing the same blob twice still
    rotates the marker.  This helper never logs the value and never accepts a
    service token by design; the marker lives under the runtime root and is read
    by every process holding a store handle.

    Args:
        value: New marker text.
        runtime_root: Runtime root to write to.  Defaults to the configured
            runtime root; it is created when missing.

    Raises:
        OSError: When the runtime root or the replacement fails.  The previous
            marker (if any) is kept.
    """
    # Imported lazily: ``strategy_library.store`` imports this module, so a
    # module-level import of the library layer would invert the dependency.
    from src.strategy_library.paths import atomic_write_text

    atomic_write_text(state_generation_path(runtime_root), value)


def get_store() -> SqliteStrategyStore:
    """Return the process-wide strategy store singleton.

    When the ``state-generation`` marker changed since this module opened the
    store (an import replaced the database), the old connection is closed and
    a fresh store is opened.  An unchanged marker returns the same instance.

    Returns:
        The shared ``SqliteStrategyStore``.
    """
    global _store, _store_generation, _bound_store
    with _lock:
        current = read_state_generation()
        if _store is None:
            _store = SqliteStrategyStore()
            _bound_store = _store
            _store_generation = current
        elif _store_generation is None or _bound_store is not _store:
            # An externally supplied store (tests, tooling). Adopt it as-is:
            # this module did not open it and must not close it.
            _bound_store = _store
            _store_generation = current
        elif _store_generation != current:
            _store.close()
            _store = SqliteStrategyStore()
            _bound_store = _store
            _store_generation = current
        return _store


def reopen_store() -> bool:
    """Close and drop the shared store so the next access opens it afresh.

    Returns:
        ``True`` when a store was open (and therefore closed), ``False`` when
        there was nothing to close.
    """
    global _store, _store_generation, _bound_store
    with _lock:
        existed = _store is not None
        if _store is not None:
            _store.close()
        _store = None
        _store_generation = None
        _bound_store = None
        return existed
