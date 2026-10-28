"""Consistent full-state export: SQLite snapshots plus a verified tree archive.

The live databases are never read as bytes: SQLite's backup API produces one
consistent single-file copy, and a ``VACUUM INTO`` pass canonicalizes the copy
so an export → import → export cycle yields the same ``content_sha256``. The
strategy tree is scanned, then read with read-before/read-after stat checks and
a fresh rescan; any mutation aborts the export instead of returning a torn
blob. The archive itself is deterministic: ``manifest.json`` first, then every
data file sorted by path, with mtime pinned to zero.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sqlite3
import stat as statlib
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from src.state_export._errors import ExportUnstable
from src.state_export._models import ExportBundle
from src.state_export._paths import (
    _DB_SIDECARS,
    _EXPORT_TMP_PREFIX,
    _TAR_MEMBER_MODE,
    _STRATEGY_DIRNAME,
    _make_temp_dir,
    _read_only_uri,
    _remove_tree,
    _resolved_db_paths,
    _resolved_root,
)
from src.state_export.manifest import (
    DATA_DIRNAME,
    EXCLUDED_PATHS,
    MANIFEST_JSON_NAME,
    MAX_BLOB_BYTES,
    MAX_FILE_COUNT,
    BlobTooLarge,
    ManifestInvalid,
    PathEscape,
    StateManifest,
    build_manifest,
    sha256_file,
)

__all__ = ["export_full"]


@dataclass(slots=True)
class _ScanEntry:
    """One strategy file recorded by the first export pass."""

    relpath: str
    path: Path
    size: int
    mtime_ns: int
    sha256: str


def export_full(
    runtime_root: Path | None = None,
    *,
    db_paths: Mapping[str, Path] | None = None,
) -> ExportBundle:
    """Snapshot the user-owned state into one ``vt-state-v1`` gzip tar.

    Databases are copied with SQLite's backup API into single-file snapshots
    without ``-wal``/``-shm``; the strategy tree is scanned twice and read once,
    so a concurrent writer surfaces as :class:`ExportUnstable` instead of a
    torn blob. Only ``strategies/`` and the databases are bundled: run
    artifacts, sessions, uploads, the run ledger, legacy scheduled jobs and the
    RM agent token are excluded by contract.

    Args:
        runtime_root: Explicit runtime root (tests); defaults to the configured
            runtime root.
        db_paths: Database name → path overrides; defaults to the configured
            store paths.

    Returns:
        The blob plus its manifest and content digest.

    Raises:
        ExportUnstable: When the strategies tree changes during the export.
        BlobTooLarge: When the file count or total bytes exceed the budget.
        PathEscape: When a strategy path is a symlink or leaves the tree.
        ManifestInvalid: When a scan source is not a regular file.
    """
    root = _resolved_root(runtime_root)
    databases = _resolved_db_paths(root, db_paths)
    root.mkdir(parents=True, exist_ok=True)
    staging = _make_temp_dir(root, _EXPORT_TMP_PREFIX)
    try:
        data_root = staging / DATA_DIRNAME
        data_root.mkdir()
        scanned = _scan_strategy_tree(root / _STRATEGY_DIRNAME, root)
        database_pairs = _snapshot_databases(databases, data_root)
        staged_files = _read_verify_and_stage(scanned, root, data_root)
        _enforce_raw_budget(staged_files, database_pairs)
        manifest = build_manifest(
            [(relpath, staged) for relpath, staged, _, _ in staged_files],
            database_pairs,
            excluded=EXCLUDED_PATHS,
        )
        _assert_manifest_matches(manifest, staged_files)
        body = _build_blob(manifest, data_root)
        if len(body) > MAX_BLOB_BYTES:
            raise BlobTooLarge(f"compressed state blob is {len(body)} bytes; limit is {MAX_BLOB_BYTES}")
        return ExportBundle(
            body=body,
            manifest=manifest,
            layout_version=manifest.layout_version,
            content_sha256=manifest.content_sha256,
        )
    finally:
        _remove_tree(staging)


def _scan_strategy_tree(strategies_root: Path, root: Path) -> list[_ScanEntry]:
    """Return the first-pass record of every regular file under *strategies_root*.

    Hashing happens here; the size and modification time are compared against a
    second read after the database snapshots, so a change in either direction
    invalidates the whole export.
    """
    entries: list[_ScanEntry] = []
    total_bytes = 0
    for path in _walk_regular_files(strategies_root):
        if len(entries) >= MAX_FILE_COUNT:
            raise BlobTooLarge(f"strategies tree has more than {MAX_FILE_COUNT} files")
        try:
            info = path.lstat()
            if statlib.S_ISLNK(info.st_mode):
                raise PathEscape(f"strategy path is a symlink: {path}")
            if not statlib.S_ISREG(info.st_mode):
                raise ManifestInvalid(f"strategy path is not a regular file: {path}")
            total_bytes += info.st_size
            if total_bytes > MAX_BLOB_BYTES:
                raise BlobTooLarge(f"strategies tree exceeds {MAX_BLOB_BYTES} bytes")
            digest = sha256_file(path)
        except OSError as exc:
            raise ExportUnstable(f"strategy tree changed during scan: {path}") from exc
        entries.append(
            _ScanEntry(
                relpath=path.relative_to(root).as_posix(),
                path=path,
                size=info.st_size,
                mtime_ns=info.st_mtime_ns,
                sha256=digest,
            )
        )
    entries.sort(key=lambda entry: entry.relpath)
    return entries


def _walk_regular_files(strategies_root: Path) -> list[Path]:
    """Return every regular file below *strategies_root*, refusing symlinks.

    A missing tree is a valid fresh box and returns an empty list. Anything
    that is neither a directory nor a regular file (device, socket, FIFO) is
    refused rather than silently skipped.
    """
    if strategies_root.is_symlink():
        raise PathEscape(f"strategies root is a symlink: {strategies_root}")
    if not strategies_root.exists():
        return []
    if not strategies_root.is_dir():
        raise ManifestInvalid(f"strategies root is not a directory: {strategies_root}")
    files: list[Path] = []
    pending = [strategies_root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as iterator:
                children = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            raise ExportUnstable(f"strategy tree changed during scan: {current}") from exc
        for child in children:
            if child.is_symlink():
                raise PathEscape(f"strategy path is a symlink: {child.path}")
            if child.is_dir(follow_symlinks=False):
                pending.append(Path(child.path))
            elif child.is_file(follow_symlinks=False):
                files.append(Path(child.path))
            else:
                raise ManifestInvalid(f"strategy path is not a regular file: {child.path}")
    return sorted(files, key=lambda path: path.relative_to(strategies_root).as_posix())


def _snapshot_databases(databases: Mapping[str, Path], data_root: Path) -> list[tuple[str, Path]]:
    """Copy every existing database into *data_root* as one WAL-free file.

    A missing database is omitted (a fresh box legitimately has none) rather
    than replaced by an invented empty file.
    """
    pairs: list[tuple[str, Path]] = []
    for name, source in databases.items():
        if source.is_symlink():
            raise PathEscape(f"database path is a symlink: {source}")
        if not source.exists():
            continue
        if not source.is_file():
            raise ManifestInvalid(f"database path is not a regular file: {source}")
        destination = data_root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        _sqlite_snapshot(source, destination)
        pairs.append((name, destination))
    return pairs


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    """Write one canonical single-file copy of *source*.

    The live file is never read as bytes: SQLite's backup API computes the
    snapshot itself, including every committed transaction that is still only
    in the WAL. The ``VACUUM INTO`` pass rebuilds the copy so its bytes are a
    pure function of the logical content — the backup header carries a change
    counter that depends on the source's journal mode, which would otherwise
    make an export → import → export cycle produce a different
    ``content_sha256``.
    """
    source_conn = sqlite3.connect(_read_only_uri(source), uri=True)
    try:
        destination_conn = sqlite3.connect(str(destination))
        try:
            source_conn.backup(destination_conn)
            destination_conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            destination_conn.close()
    finally:
        source_conn.close()

    canonical = destination.with_name(f".{destination.name}.canonical")
    canonical.unlink(missing_ok=True)
    destination_conn = sqlite3.connect(str(destination))
    try:
        destination_conn.execute("VACUUM INTO ?", (str(canonical),))
    finally:
        destination_conn.close()
    os.replace(canonical, destination)
    for path in (destination, canonical):
        for suffix in _DB_SIDECARS:
            path.with_name(f"{path.name}{suffix}").unlink(missing_ok=True)
    os.chmod(destination, 0o600)


def _read_verify_and_stage(
    entries: list[_ScanEntry],
    root: Path,
    data_root: Path,
) -> list[tuple[str, Path, str, int]]:
    """Read each scanned file once more and stage exactly those bytes.

    Each file is stat'ed before and after the read; a size or mtime move, a
    digest mismatch, or a changed file set in the fresh rescan aborts the
    export. Staged copies are what :func:`build_manifest` hashes and what the
    archive carries, so the manifest can never describe different bytes from
    the ones shipped.
    """
    staged: list[tuple[str, Path, str, int]] = []
    for entry in entries:
        try:
            before = entry.path.lstat()
            if statlib.S_ISLNK(before.st_mode) or not statlib.S_ISREG(before.st_mode):
                raise ExportUnstable(f"strategy path changed type during export: {entry.path}")
            if before.st_size != entry.size or before.st_mtime_ns != entry.mtime_ns:
                raise ExportUnstable(f"strategy file changed during export: {entry.path}")
            data = entry.path.read_bytes()
            after = entry.path.lstat()
        except OSError as exc:
            raise ExportUnstable(f"strategy file changed during export: {entry.path}") from exc
        digest = hashlib.sha256(data).hexdigest()
        if (
            after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or len(data) != before.st_size
            or digest != entry.sha256
        ):
            raise ExportUnstable(f"strategy file changed during export: {entry.path}")
        destination = data_root / entry.relpath
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        staged.append((entry.relpath, destination, digest, len(data)))

    current: list[tuple[str, int, int]] = []
    for path in _walk_regular_files(root / _STRATEGY_DIRNAME):
        info = path.lstat()
        current.append((path.relative_to(root).as_posix(), info.st_size, info.st_mtime_ns))
    expected = [(entry.relpath, entry.size, entry.mtime_ns) for entry in entries]
    if current != expected:
        raise ExportUnstable("strategies tree changed during export")
    return staged


def _enforce_raw_budget(
    staged_files: list[tuple[str, Path, str, int]],
    database_pairs: list[tuple[str, Path]],
) -> None:
    """Enforce the count and byte budget over the raw entries before archiving."""
    if len(staged_files) + len(database_pairs) > MAX_FILE_COUNT:
        raise BlobTooLarge(
            f"state blob has {len(staged_files) + len(database_pairs)} files; limit is {MAX_FILE_COUNT}"
        )
    total_bytes = sum(size for _, _, _, size in staged_files)
    total_bytes += sum(path.stat().st_size for _, path in database_pairs)
    if total_bytes > MAX_BLOB_BYTES:
        raise BlobTooLarge(f"state blob is {total_bytes} bytes; limit is {MAX_BLOB_BYTES}")


def _assert_manifest_matches(
    manifest: StateManifest,
    staged_files: list[tuple[str, Path, str, int]],
) -> None:
    """Prove the manifest describes exactly the second-pass bytes."""
    recorded = {entry.path: entry for entry in manifest.files}
    if len(recorded) != len(staged_files):
        raise ExportUnstable("manifest file count differs from the staged snapshot")
    for relpath, _, digest, size in staged_files:
        entry = recorded.get(relpath)
        if entry is None or entry.sha256 != digest or entry.size != size:
            raise ExportUnstable("manifest digest differs from the staged snapshot")


def _build_blob(manifest: StateManifest, data_root: Path) -> bytes:
    """Pack the manifest and staged data into a deterministic gzip tar.

    Member order is the manifest first, then every data file sorted by path;
    mtime is pinned to zero and mode to ``0o644``.
    """
    manifest_bytes = (
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gzip_handle:
        with tarfile.open(fileobj=gzip_handle, mode="w") as archive:
            _add_tar_member(archive, MANIFEST_JSON_NAME, manifest_bytes)
            entries = sorted((*manifest.files, *manifest.databases), key=lambda entry: entry.path)
            for entry in entries:
                data = (data_root / entry.path).read_bytes()
                if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
                    raise ExportUnstable(f"staged file changed while packaging: {entry.path}")
                _add_tar_member(archive, f"{DATA_DIRNAME}/{entry.path}", data)
    return buffer.getvalue()


def _add_tar_member(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    """Append one regular file with deterministic metadata."""
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mtime = 0
    info.mode = _TAR_MEMBER_MODE
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    archive.addfile(info, io.BytesIO(data))
