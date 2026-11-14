"""Cross-process shared and exclusive locks for the strategy-version registry.

The lock file lives at ``<runtime_root>/.strategy-version-registry.lock``,
outside the registry's state package data.  Readers take a shared lock; the
publisher takes an exclusive lock.  Readers never create the runtime root,
its parents or the lock file, so a read against a missing runtime root stays
side-effect free.  Writers create the directory chain and a private (``0600``)
regular file and refuse a symlinked lock path.

POSIX ``fcntl.flock`` supplies the shared/exclusive semantics.  Platforms
without ``fcntl`` raise :class:`VersionLockUnavailable` rather than pretending
to hold a lock.
"""

from __future__ import annotations

import errno
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.strategy_versions.errors import VersionLockUnavailable

try:  # POSIX advisory locks (Linux/macOS).
    import fcntl
except ImportError:  # pragma: no cover - Windows lacks fcntl
    fcntl = None

LOCK_FILENAME = ".strategy-version-registry.lock"
_LOCK_MODE = 0o600
_DIRECTORY_MODE = 0o700
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


def version_lock_path(runtime_root: Path | str) -> Path:
    """Return the registry lock path for *runtime_root*.

    Args:
        runtime_root: Runtime root directory holding user-level agent state.

    Returns:
        ``<runtime_root>/.strategy-version-registry.lock``; the path is not
        created or validated.
    """
    return Path(runtime_root) / LOCK_FILENAME


def _require_fcntl() -> None:
    """Reject platforms without POSIX advisory locking."""
    if fcntl is None:  # pragma: no cover - Windows only
        raise VersionLockUnavailable("advisory file locking requires fcntl on this platform")


def _open_existing(path: Path) -> int:
    """Open the existing lock file for a shared lock without creating it."""
    try:
        fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise VersionLockUnavailable(f"cannot open the strategy-version lock at {path}") from exc
    _require_regular_file(fd, path)
    return fd


def _open_or_create(path: Path) -> int:
    """Create the private lock file (or open the existing one) for writing."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
        fd = os.open(
            path,
            os.O_RDWR | os.O_CREAT | _O_NOFOLLOW | _O_CLOEXEC,
            _LOCK_MODE,
        )
    except OSError as exc:
        raise VersionLockUnavailable(f"cannot create the strategy-version lock at {path}") from exc
    _require_regular_file(fd, path)
    try:
        if stat.S_IMODE(os.fstat(fd).st_mode) != _LOCK_MODE:
            os.fchmod(fd, _LOCK_MODE)
    except OSError as exc:
        os.close(fd)
        raise VersionLockUnavailable(f"cannot secure the strategy-version lock at {path}") from exc
    return fd


def _require_regular_file(fd: int, path: Path) -> None:
    """Reject a directory or special file at the lock path."""
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise VersionLockUnavailable(f"strategy-version lock path is not a regular file: {path}")


def _flock(fd: int, *, shared: bool, blocking: bool, path: Path) -> None:
    """Acquire the advisory lock on *fd* or raise a typed failure."""
    _require_fcntl()
    operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    if not blocking:
        operation |= fcntl.LOCK_NB
    try:
        fcntl.flock(fd, operation)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN) or isinstance(exc, BlockingIOError):
            raise VersionLockUnavailable(f"strategy-version lock is held elsewhere: {path}") from exc
        raise


def _release(fd: int) -> None:
    """Release the advisory lock and close *fd* reliably."""
    try:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                # Closing the descriptor below releases the lock as well.
                pass
    finally:
        os.close(fd)


@contextmanager
def shared_version_lock(runtime_root: Path | str, blocking: bool = True) -> Iterator[None]:
    """Hold a shared registry lock for the duration of the context.

    A missing lock file is a no-op: readers must not create the runtime root,
    its parents or the lock file.

    Args:
        runtime_root: Runtime root directory holding user-level agent state.
        blocking: When true, wait for a conflicting writer; when false, raise
            :class:`VersionLockUnavailable` immediately.

    Raises:
        VersionLockUnavailable: If a conflicting exclusive lock is held, the
            lock path is unsafe, or the platform cannot lock files.
    """
    _require_fcntl()
    path = version_lock_path(runtime_root)
    try:
        fd = _open_existing(path)
    except FileNotFoundError:
        yield
        return
    try:
        _flock(fd, shared=True, blocking=blocking, path=path)
        yield
    finally:
        _release(fd)


@contextmanager
def exclusive_version_lock(runtime_root: Path | str, blocking: bool = True) -> Iterator[None]:
    """Hold the exclusive registry lock for the duration of the context.

    The runtime root, its parents and a private lock file are created on
    demand.  Existing lock bytes are preserved.

    Args:
        runtime_root: Runtime root directory holding user-level agent state.
        blocking: When true, wait for conflicting readers or writers; when
            false, raise :class:`VersionLockUnavailable` immediately.

    Raises:
        VersionLockUnavailable: If the lock is held elsewhere, the lock path is
            a symlink or not a regular file, or the platform cannot lock files.
    """
    _require_fcntl()
    path = version_lock_path(runtime_root)
    fd = _open_or_create(path)
    try:
        _flock(fd, shared=False, blocking=blocking, path=path)
        yield
    finally:
        _release(fd)


__all__ = [
    "LOCK_FILENAME",
    "exclusive_version_lock",
    "shared_version_lock",
    "version_lock_path",
]
