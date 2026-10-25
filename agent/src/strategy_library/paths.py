"""Path layout for the strategy library.

The library lives under the runtime root (``~/.vibe-trading`` by default)::

    strategies/<strategy_id>/strategy.json
    strategies/<strategy_id>/versions/<version>/strategy.json
    strategies/<strategy_id>/versions/<version>/config.json
    strategies/<strategy_id>/versions/<version>/code/signal_engine.py
    strategies/<strategy_id>/versions/<version>/manifest.json

``strategy.json`` is the only canonical spec filename; the editable copy at the
strategy root and each immutable version copy share that name and format.
Versions additionally carry the executable ``config.json`` and
``code/signal_engine.py``. ``manifest.json`` pins the sha256 of every file in a
version directory so a run can prove exactly which bytes it executed.

Identifiers become path segments, so every public helper validates its id
before a path is built: lowercase slug only, no ``..``, no separators, no
colon. Each result is then resolved and required to stay under the strategies
root, and symlinked ancestors below the runtime root are rejected, so neither a
crafted id nor a pre-planted symlink can escape the library.

Atomic writes use a temp file in the destination directory (the only
filesystem ``os.replace`` is atomic within), fsync the file, replace, then
fsync the parent directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Sequence

from src.config.paths import get_runtime_root

_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
_ID_RE = re.compile(_ID_PATTERN)

STRATEGY_SPEC_FILENAME = "strategy.json"
VERSION_CONFIG_FILENAME = "config.json"
VERSION_MANIFEST_FILENAME = "manifest.json"
SIGNAL_ENGINE_RELPATH = "code/signal_engine.py"

_STRATEGIES_DIRNAME = "strategies"
_VERSIONS_DIRNAME = "versions"


def validate_id(value: str, *, what: str = "id") -> str:
    """Return *value* unchanged when it is a safe single path segment.

    Args:
        value: Candidate identifier.
        what: Field name used in the ``ValueError`` message.

    Returns:
        The identifier, unchanged.

    Raises:
        ValueError: When the identifier is not a string, is empty, contains
            ``..``, or does not match ``^[a-z0-9][a-z0-9._-]{0,63}$`` (which
            already excludes absolute paths, separators, colons and spaces).
    """
    if not isinstance(value, str):
        raise ValueError(f"{what} must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{what} must not be empty")
    if ".." in value:
        raise ValueError(f"{what} {value!r} must not contain '..'")
    if not _ID_RE.fullmatch(value):
        raise ValueError(
            f"{what} {value!r} must match {_ID_PATTERN}: lowercase alphanumerics, "
            "'-', '_' or '.', starting with an alphanumeric"
        )
    return value


def resolve_runtime_root(runtime_root: Path | None = None) -> Path:
    """Return the runtime root used by the strategy library.

    Args:
        runtime_root: Explicit override (tests); defaults to
            :func:`src.config.paths.get_runtime_root`.

    Returns:
        The runtime root directory, not created.
    """
    if runtime_root is None:
        return get_runtime_root()
    return Path(runtime_root).expanduser()


def strategies_root(*, runtime_root: Path | None = None) -> Path:
    """Return ``<runtime_root>/strategies``, the library root."""
    return resolve_runtime_root(runtime_root) / _STRATEGIES_DIRNAME


def _checked(root: Path, segments: Sequence[str]) -> Path:
    """Join *segments* under *root*, rejecting symlink ancestors and escapes."""
    candidate = root.joinpath(*segments)
    resolved_root = root.resolve()
    for ancestor in (candidate, *candidate.parents):
        if ancestor == root:
            break
        if ancestor.is_symlink():
            raise ValueError(f"refusing symlinked path component: {ancestor}")
    if not candidate.resolve().is_relative_to(resolved_root):
        raise ValueError(f"path {candidate} escapes the strategies root {resolved_root}")
    return candidate


def _strategy_path(
    strategy_id: str,
    *,
    version: str | None = None,
    leaf: Sequence[str] = (),
    runtime_root: Path | None = None,
) -> Path:
    ident = validate_id(strategy_id, what="strategy_id")
    segments: list[str] = [ident]
    if version is not None:
        segments.extend((_VERSIONS_DIRNAME, validate_id(version, what="version")))
    segments.extend(leaf)
    return _checked(strategies_root(runtime_root=runtime_root), segments)


def strategy_dir(strategy_id: str, *, runtime_root: Path | None = None) -> Path:
    """Return ``<strategies>/<strategy_id>`` without creating it."""
    return _strategy_path(strategy_id, runtime_root=runtime_root)


def spec_path(strategy_id: str, *, runtime_root: Path | None = None) -> Path:
    """Return the canonical draft spec path ``<strategy_id>/strategy.json``."""
    return _strategy_path(strategy_id, leaf=(STRATEGY_SPEC_FILENAME,), runtime_root=runtime_root)


def versions_dir(strategy_id: str, *, runtime_root: Path | None = None) -> Path:
    """Return ``<strategy_id>/versions`` without creating it."""
    return _strategy_path(strategy_id, leaf=(_VERSIONS_DIRNAME,), runtime_root=runtime_root)


def version_dir(strategy_id: str, version: str, *, runtime_root: Path | None = None) -> Path:
    """Return ``<strategy_id>/versions/<version>`` without creating it."""
    return _strategy_path(strategy_id, version=version, runtime_root=runtime_root)


def version_spec_path(strategy_id: str, version: str, *, runtime_root: Path | None = None) -> Path:
    """Return the immutable spec copy for one version."""
    return _strategy_path(
        strategy_id,
        version=version,
        leaf=(STRATEGY_SPEC_FILENAME,),
        runtime_root=runtime_root,
    )


def version_config_path(strategy_id: str, version: str, *, runtime_root: Path | None = None) -> Path:
    """Return the backtest config copy for one version."""
    return _strategy_path(
        strategy_id,
        version=version,
        leaf=(VERSION_CONFIG_FILENAME,),
        runtime_root=runtime_root,
    )


def version_code_path(strategy_id: str, version: str, *, runtime_root: Path | None = None) -> Path:
    """Return the signal-engine copy for one code-backed version."""
    return _strategy_path(
        strategy_id,
        version=version,
        leaf=tuple(SIGNAL_ENGINE_RELPATH.split("/")),
        runtime_root=runtime_root,
    )


def manifest_path(strategy_id: str, version: str, *, runtime_root: Path | None = None) -> Path:
    """Return the manifest path for one version."""
    return _strategy_path(
        strategy_id,
        version=version,
        leaf=(VERSION_MANIFEST_FILENAME,),
        runtime_root=runtime_root,
    )


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace *path* with *text*.

    The temp file is created in the destination directory, written with a
    short-write loop, fsynced, then swapped in with ``os.replace`` followed by
    a parent-directory fsync. A crash at any step leaves either the old file or
    the new one, never a partial write.

    Args:
        path: Destination file path; parent directories are created.
        text: Full new contents.
        encoding: Text encoding (default UTF-8).

    Raises:
        OSError: When the directory or write fails. The previous file is kept.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode(encoding)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        tmp.unlink(missing_ok=True)
        raise
    os.close(fd)
    try:
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(target.parent)


def atomic_write_json(path: Path, payload: Any) -> None:
    """Atomically write *payload* as deterministic, UTF-8, indented JSON."""
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, text)


def file_sha256(path: Path) -> str:
    """Return the hex sha256 of *path*, streamed in 1 MiB chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(directory: Path) -> dict[str, Any]:
    """Return ``{"files": [...]}`` for every regular file under *directory*.

    Entries are sorted by relative POSIX path. ``manifest.json`` itself and
    ``*.tmp`` write leftovers are excluded so the manifest never describes its
    own previous content.

    Args:
        directory: Version directory to scan.

    Returns:
        A dict with a ``files`` list of ``{path, sha256, size}`` entries.
    """
    base = Path(directory)
    entries: list[dict[str, Any]] = []
    for entry in sorted(base.rglob("*")):
        if not entry.is_file() or entry.is_symlink():
            continue
        relative = entry.relative_to(base).as_posix()
        if relative == VERSION_MANIFEST_FILENAME or entry.name.endswith(".tmp"):
            continue
        entries.append(
            {
                "path": relative,
                "sha256": file_sha256(entry),
                "size": entry.stat().st_size,
            }
        )
    return {"files": entries}


def write_version_manifest(strategy_id: str, version: str, *, runtime_root: Path | None = None) -> dict[str, Any]:
    """Write ``manifest.json`` for an existing version directory.

    Args:
        strategy_id: Strategy identifier.
        version: Version identifier.
        runtime_root: Explicit runtime root override (tests).

    Returns:
        The manifest that was written.

    Raises:
        FileNotFoundError: When the version directory does not exist.
    """
    directory = version_dir(strategy_id, version, runtime_root=runtime_root)
    if not directory.is_dir():
        raise FileNotFoundError(f"version directory does not exist: {directory}")
    manifest = build_manifest(directory)
    manifest["strategy_id"] = validate_id(strategy_id, what="strategy_id")
    manifest["version"] = validate_id(version, what="version")
    manifest["schema_version"] = 1
    atomic_write_json(manifest_path(strategy_id, version, runtime_root=runtime_root), manifest)
    return manifest


def _fsync_dir(directory: Path) -> None:
    """Best-effort parent-directory fsync after a replace."""
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)
