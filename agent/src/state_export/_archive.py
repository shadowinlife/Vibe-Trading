"""Safe extraction and fence checks for a ``vt-state-v1`` gzip tar.

Members are consumed one at a time rather than through ``extractall``: links
and devices are refused outright, names go through the shared relpath fence,
only ``manifest.json`` and declared regular files under ``data/`` are written,
and the declared count/byte budget is enforced while writing. The manifest is
parsed structurally before extraction and content-validated afterwards, so a
caller can distinguish a malformed manifest from a digest mismatch.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.state_export._errors import BlobCorrupt
from src.state_export._paths import _IO_CHUNK_BYTES, _STRATEGY_DIRNAME
from src.state_export.manifest import (
    DATA_DIRNAME,
    LAYOUT_VERSION,
    MANIFEST_JSON_NAME,
    MAX_BLOB_BYTES,
    MAX_FILE_COUNT,
    BlobTooLarge,
    LayoutUnsupported,
    ManifestInvalid,
    PathEscape,
    StateExportError,
    StateManifest,
    validate_manifest,
    validate_relpath,
)

__all__ = ["_extract_blob"]


def _extract_blob(blob: bytes, staging: Path, database_names: set[str]) -> StateManifest:
    """Parse, fence and extract *blob* into ``<staging>/data``."""
    data_root = staging / DATA_DIRNAME
    data_root.mkdir()
    try:
        archive = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
    except (tarfile.TarError, gzip.BadGzipFile, EOFError, OSError) as exc:
        raise BlobCorrupt(f"state blob is not a readable gzip tar: {type(exc).__name__}") from exc
    try:
        with archive:
            members, payload = _read_members(archive)
            declared = StateManifest.from_dict(payload)
            _check_declared_layout(declared, database_names)
            _extract_declared_files(archive, members, declared, data_root)
    except StateExportError:
        raise
    except (tarfile.TarError, gzip.BadGzipFile, EOFError, OSError) as exc:
        raise BlobCorrupt(f"state blob could not be read: {type(exc).__name__}") from exc
    try:
        return validate_manifest(payload, data_root, expected_layout=LAYOUT_VERSION)
    except (LayoutUnsupported, PathEscape, BlobTooLarge):
        raise
    except ManifestInvalid as exc:
        raise BlobCorrupt(f"state blob failed content validation: {exc}") from exc
    except OSError as exc:
        raise BlobCorrupt(f"state blob content is unreadable: {type(exc).__name__}") from exc


def _read_members(
    archive: tarfile.TarFile,
) -> tuple[dict[str, tarfile.TarInfo], Mapping[str, Any]]:
    """Validate every member name/type and return them plus the parsed manifest.

    No file is written here. Duplicate names, links, devices, absolute paths,
    traversal and anything outside ``manifest.json``/``data/**`` are refused
    before extraction starts.
    """
    members: dict[str, tarfile.TarInfo] = {}
    total_declared_bytes = 0
    file_count = 0
    for member in archive:
        relpath = _member_relpath(member)
        if relpath in members:
            raise ManifestInvalid(f"state blob contains duplicate member {relpath!r}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise PathEscape(f"state blob member is not a regular file: {relpath!r}")
        if member.isdir():
            if relpath != DATA_DIRNAME and not relpath.startswith(f"{DATA_DIRNAME}/"):
                raise ManifestInvalid(f"state blob directory is outside data/: {relpath!r}")
            members[relpath] = member
            continue
        if not member.isreg():
            raise ManifestInvalid(f"state blob member has an unsupported type: {relpath!r}")
        if relpath != MANIFEST_JSON_NAME and not relpath.startswith(f"{DATA_DIRNAME}/"):
            raise ManifestInvalid(f"state blob member is outside data/: {relpath!r}")
        total_declared_bytes += member.size
        if total_declared_bytes > MAX_BLOB_BYTES:
            raise BlobTooLarge(f"state blob content exceeds {MAX_BLOB_BYTES} bytes")
        if relpath != MANIFEST_JSON_NAME:
            file_count += 1
            if file_count > MAX_FILE_COUNT:
                raise BlobTooLarge(f"state blob has more than {MAX_FILE_COUNT} files")
        members[relpath] = member
    manifest_member = members.get(MANIFEST_JSON_NAME)
    if manifest_member is None:
        raise ManifestInvalid("state blob has no manifest.json")
    handle = archive.extractfile(manifest_member)
    if handle is None:
        raise BlobCorrupt("state blob manifest.json is unreadable")
    with handle:
        raw = handle.read(MAX_BLOB_BYTES + 1)
    if len(raw) > MAX_BLOB_BYTES:
        raise BlobTooLarge(f"state manifest exceeds {MAX_BLOB_BYTES} bytes")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestInvalid("manifest.json is not valid UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise ManifestInvalid(f"manifest.json must contain an object, got {type(payload).__name__}")
    return members, payload


def _member_relpath(member: tarfile.TarInfo) -> str:
    """Return *member*'s safe relpath, refusing traversal and link-style names."""
    raw = member.name
    if not isinstance(raw, str) or not raw:
        raise ManifestInvalid("state blob member has no name")
    if raw.endswith("/"):
        if not member.isdir():
            raise ManifestInvalid(f"state blob file member ends with '/': {raw!r}")
        raw = raw.rstrip("/")
    if not raw:
        raise ManifestInvalid("state blob member has an empty name")
    return validate_relpath(raw)


def _check_declared_layout(declared: StateManifest, database_names: set[str]) -> None:
    """Reject a manifest that names state this layout cannot install."""
    unknown_databases = [entry.path for entry in declared.databases if entry.path not in database_names]
    if unknown_databases:
        raise ManifestInvalid(f"manifest declares unknown databases: {sorted(unknown_databases)}")
    for entry in declared.files:
        if not entry.path.startswith(f"{_STRATEGY_DIRNAME}/"):
            raise ManifestInvalid(f"manifest declares a file outside strategies/: {entry.path!r}")


def _extract_declared_files(
    archive: tarfile.TarFile,
    members: Mapping[str, tarfile.TarInfo],
    manifest: StateManifest,
    data_root: Path,
) -> None:
    """Write only the files the manifest declares, enforcing the budget again."""
    declared = {entry.path for entry in (*manifest.files, *manifest.databases)}
    extracted_bytes = 0
    extracted_count = 0
    for relpath, member in sorted(members.items()):
        if not member.isreg() or relpath == MANIFEST_JSON_NAME:
            continue
        data_relpath = relpath[len(f"{DATA_DIRNAME}/") :]
        if data_relpath not in declared:
            raise ManifestInvalid(f"state blob carries an undeclared file: {data_relpath!r}")
        destination = data_root.joinpath(*data_relpath.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle = archive.extractfile(member)
        if handle is None:
            raise BlobCorrupt(f"state blob member is unreadable: {relpath!r}")
        written = 0
        with handle, destination.open("wb") as output:
            while chunk := handle.read(_IO_CHUNK_BYTES):
                written += len(chunk)
                if written > member.size:
                    raise BlobCorrupt(f"state blob member exceeds its declared size: {relpath!r}")
                output.write(chunk)
        if written != member.size:
            raise BlobCorrupt(f"state blob member is truncated: {relpath!r}")
        extracted_count += 1
        extracted_bytes += written
        if extracted_count > MAX_FILE_COUNT:
            raise BlobTooLarge(f"state blob has more than {MAX_FILE_COUNT} files")
        if extracted_bytes > MAX_BLOB_BYTES:
            raise BlobTooLarge(f"state blob content exceeds {MAX_BLOB_BYTES} bytes")
