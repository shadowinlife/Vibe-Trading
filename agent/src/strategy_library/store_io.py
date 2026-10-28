"""Byte-level mechanics behind the strategy library facade.

Canonical spec encoding and immutable version publishing live here so
:mod:`src.strategy_library.store` stays a readable orchestration layer.

The canonical encoding (sorted keys, no insignificant whitespace, no trailing
newline) is written verbatim to ``strategy.json``. One definition therefore
serves the database hash, the draft file and every version copy, so
``sha256(file) == spec_sha256`` holds without a second serialization path.

A version directory is built in a hidden sibling and renamed into place:
``os.replace`` of a directory is atomic within one filesystem, so a failure or
a crash can never leave a half-built version that a scheduler might pick up.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from src.strategy_library.paths import (
    SIGNAL_ENGINE_RELPATH,
    STRATEGY_SPEC_FILENAME,
    VERSION_CONFIG_FILENAME,
    VERSION_MANIFEST_FILENAME,
    atomic_write_json,
    atomic_write_text,
    build_manifest,
    file_sha256,
    spec_path,
    strategy_dir,
    version_dir,
    versions_dir,
)
from src.strategy_library.spec import StrategySpec
from src.strategy_store.models import Artifact, validate_artifact_kind

_VERSION_PREFIX = re.compile(r"^v(\d+)-")


def spec_mapping(spec: StrategySpec | Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-shaped, independently owned dict for *spec*.

    Raises:
        TypeError: When *spec* is neither a mapping nor a ``StrategySpec``.
    """
    if isinstance(spec, StrategySpec):
        return spec.to_dict()
    if isinstance(spec, Mapping):
        return copy.deepcopy(dict(spec))
    raise TypeError(f"spec must be a mapping or StrategySpec, got {type(spec).__name__}")


def validated_payload(spec: StrategySpec | Mapping[str, Any]) -> dict[str, Any]:
    """Return the payload a row may carry, or raise on an unusable identity.

    A draft is explicitly allowed to be incomplete: review gaps are the gates'
    business. Only the two facts the row itself encodes are required — a
    non-blank name and a known kind.

    Raises:
        ValueError: On a blank name, a non-string universe, or an unknown kind.
    """
    payload = spec_mapping(spec)
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("a strategy draft needs a non-blank spec name")
    kind = payload.get("kind")
    if not isinstance(kind, str):
        raise ValueError("a strategy draft needs a spec kind ('code' or 'workflow')")
    validate_artifact_kind(kind)
    universe = payload.get("universe", "")
    if universe is not None and not isinstance(universe, str):
        raise ValueError("spec universe must be a string")
    return payload


def canonical_spec_bytes(spec: StrategySpec | Mapping[str, Any]) -> bytes:
    """Return the canonical JSON bytes of *spec* (UTF-8, no trailing newline).

    Raises:
        TypeError: When *spec* is not a mapping/spec, or a value is not
            JSON-encodable.
    """
    return json.dumps(
        spec_mapping(spec), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    """Return the hex sha256 of *data*."""
    return hashlib.sha256(data).hexdigest()


def spec_sha256(spec: StrategySpec | Mapping[str, Any]) -> str:
    """Return the hex sha256 of :func:`canonical_spec_bytes`."""
    return sha256_bytes(canonical_spec_bytes(spec))


def version_base(version: str | None) -> int:
    """Return ``N`` from ``v<N>-...``; an unrecognised version counts as 0."""
    match = _VERSION_PREFIX.match(version or "")
    return int(match.group(1)) if match else 0


def materialise_spec(
    strategy_id: str, payload: Mapping[str, Any], *, runtime_root: Path | None
) -> bytes:
    """Write the canonical spec to ``strategies/<id>/strategy.json``.

    ``bytes.decode('utf-8')`` followed by the writer's ``str.encode('utf-8')``
    is byte-exact for valid UTF-8, so the file digest equals the row digest.
    """
    data = canonical_spec_bytes(payload)
    atomic_write_text(spec_path(strategy_id, runtime_root=runtime_root), data.decode("utf-8"))
    return data


def ensure_spec_file(artifact: Artifact, *, runtime_root: Path | None) -> bytes:
    """Return canonical spec bytes, repairing a drifted draft file first.

    The row is the source of truth: a missing file or a digest that no longer
    matches ``spec_sha256`` is re-materialised from ``artifact.spec``, so a
    snapshot never copies bytes the verdict was not issued against.
    """
    if artifact.spec is None:
        raise ValueError(f"strategy {artifact.id!r} carries no spec; it cannot be snapshotted")
    data = canonical_spec_bytes(artifact.spec)
    path = spec_path(artifact.id, runtime_root=runtime_root)
    if not path.is_file() or file_sha256(path) != artifact.spec_sha256:
        atomic_write_text(path, data.decode("utf-8"))
    return data


def code_sources(
    strategy_id: str, *, runtime_root: Path | None
) -> list[tuple[str, Path]]:
    """Return ``(relative path, source)`` pairs a code snapshot must copy.

    Both files are required and non-empty: a code version without its config or
    engine could be scheduled but not executed. Missing input is reported before
    any directory is created.

    Raises:
        FileNotFoundError: When either source is absent or empty.
    """
    base = strategy_dir(strategy_id, runtime_root=runtime_root)
    sources: list[tuple[str, Path]] = []
    for relpath in (VERSION_CONFIG_FILENAME, SIGNAL_ENGINE_RELPATH):
        source = base.joinpath(*relpath.split("/"))
        if not source.is_file() or source.stat().st_size == 0:
            raise FileNotFoundError(
                f"code strategy {strategy_id!r} has no usable {relpath} at {source}; "
                "re-promote it before scheduling"
            )
        sources.append((relpath, source))
    return sources


def publish_version(
    strategy_id: str,
    version: str,
    *,
    spec_bytes: bytes,
    sources: Sequence[tuple[str, Path]],
    runtime_root: Path | None,
) -> Path:
    """Create ``versions/<version>/`` atomically and return its path.

    The directory is assembled under a hidden staging name, its manifest is
    computed over the copied bytes and written last, then the whole tree is
    renamed into place. An existing destination is treated as already published
    and left byte-identical — versions are immutable.

    Raises:
        OSError: When a copy or the rename fails; the staging tree is removed.
    """
    parent = versions_dir(strategy_id, runtime_root=runtime_root)
    final = version_dir(strategy_id, version, runtime_root=runtime_root)
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".tmp-{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        (staging / STRATEGY_SPEC_FILENAME).write_bytes(spec_bytes)
        for relpath, source in sources:
            destination = staging.joinpath(*relpath.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        atomic_write_json(
            staging / VERSION_MANIFEST_FILENAME, _manifest(staging, strategy_id, version)
        )
        try:
            os.replace(staging, final)
        except OSError:
            # A concurrent snapshot may have published the same version first;
            # its bytes are as good as ours, so treat that as success.
            if not final.exists():
                raise
            shutil.rmtree(staging, ignore_errors=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _fsync_dir(parent)
    return final


def _manifest(directory: Path, strategy_id: str, version: str) -> dict[str, Any]:
    """Return the version manifest for a not-yet-published directory."""
    manifest = build_manifest(directory)
    manifest.update(strategy_id=strategy_id, version=version, schema_version=1)
    return manifest


def _fsync_dir(directory: Path) -> None:
    """Best-effort directory fsync so a finished snapshot survives a crash."""
    try:
        handle = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)
