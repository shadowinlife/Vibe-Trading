"""Verified read projection: lineage, manifest and exact source bytes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.strategy_library import paths
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import (
    VersionDigestMismatchError,
    VersionNotFoundError,
)
from src.strategy_versions.models import (
    RegistrationKey,
    SourceFacts,
    VersionRegistration,
    VersionState,
)
from src.strategy_versions.projection import (
    LINEAGE_FILENAME,
    LINEAGE_SCHEMA,
    build_lineage,
    verify_source,
)
from src.strategy_versions.publisher import (
    PublicationPayload,
    StrategyVersionPublisher,
)
from tests._strategy_version_support import make_key

SPEC_BYTES = '{"kind":"code","name":"动量 Alpha"}\n'.encode("utf-8")
CONFIG_BYTES = '{"source":"local","note":"中文"}\r\n'.encode("utf-8")
SOURCE_BYTES = ("\ufeff# 中文注释\r\n" "def generate_signals(frame):\n" "    return frame\r\n").encode("utf-8")


def _payload() -> PublicationPayload:
    return PublicationPayload(SPEC_BYTES, CONFIG_BYTES, SOURCE_BYTES)


def _publish(
    tmp_path: Path,
) -> tuple[
    SqliteVersionRegistry,
    StrategyVersionPublisher,
    RegistrationKey,
    VersionRegistration,
    Path,
]:
    registry = SqliteVersionRegistry(tmp_path / "state" / "strategy_store.db")
    publisher = StrategyVersionPublisher(registry, tmp_path)
    key = make_key(strategy_id="momentum", version="v7")
    active = publisher.publish(key, _payload())
    root = paths.version_dir(key.strategy_id, key.strategy_version, runtime_root=tmp_path)
    return registry, publisher, key, active, root


def _write_canonical(path: Path, payload: dict[str, object]) -> None:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(data)


def _refresh_manifest(root: Path, key: RegistrationKey) -> None:
    manifest = paths.build_manifest(root)
    manifest.update(
        strategy_id=key.strategy_id,
        version=key.strategy_version,
        schema_version=1,
    )
    paths.atomic_write_json(root / "manifest.json", manifest)


def test_verify_source_returns_exact_bytes_after_full_agreement(
    tmp_path: Path,
) -> None:
    # Given a published active registration.
    _, _, _, active, _ = _publish(tmp_path)

    # When the projection verifies it.
    result = verify_source(active, tmp_path)

    # Then the exact original bytes are returned.
    assert result == SOURCE_BYTES
    assert result.startswith(b"\xef\xbb\xbf")
    assert result.endswith(b"\r\n")


def test_missing_lineage_is_version_not_found(tmp_path: Path) -> None:
    # Given an active tree whose lineage file disappeared.
    _, _, _, active, root = _publish(tmp_path)
    (root / LINEAGE_FILENAME).unlink()

    # When it is verified.
    with pytest.raises(VersionNotFoundError):
        verify_source(active, tmp_path)


def test_malformed_lineage_is_version_not_found(tmp_path: Path) -> None:
    # Given an active tree with an unparsable lineage file.
    _, _, _, active, root = _publish(tmp_path)
    (root / LINEAGE_FILENAME).write_bytes(b"{not-json")

    # When it is verified.
    with pytest.raises(VersionNotFoundError):
        verify_source(active, tmp_path)


def test_extra_lineage_field_is_version_not_found(tmp_path: Path) -> None:
    # Given a lineage with a closed-schema violation and a matching manifest.
    _, _, key, active, root = _publish(tmp_path)
    lineage = json.loads((root / LINEAGE_FILENAME).read_bytes())
    lineage["state"] = "active"
    _write_canonical(root / LINEAGE_FILENAME, lineage)
    _refresh_manifest(root, key)

    # When it is verified.
    with pytest.raises(VersionNotFoundError):
        verify_source(active, tmp_path)


def test_wrong_identity_lineage_is_version_not_found(tmp_path: Path) -> None:
    # Given a lineage for another owner with a self-consistent manifest.
    _, _, key, active, root = _publish(tmp_path)
    other = VersionRegistration(
        key=RegistrationKey.parse("other-owner", key.strategy_id, key.strategy_version),
        version_id="ver_" + "ab" * 16,
        source=active.source,
        state=VersionState.ACTIVE,
    )
    (root / LINEAGE_FILENAME).write_bytes(build_lineage(other))
    _refresh_manifest(root, key)

    # When the original registration is verified.
    with pytest.raises(VersionNotFoundError):
        verify_source(active, tmp_path)


def test_lineage_digest_contradiction_is_digest_mismatch(tmp_path: Path) -> None:
    # Given a lineage whose digest and length contradict the actual bytes.
    _, _, key, active, root = _publish(tmp_path)
    lineage = json.loads((root / LINEAGE_FILENAME).read_bytes())
    lineage["source_sha256"] = "0" * 64
    lineage["source_byte_length"] = int(lineage["source_byte_length"]) + 1
    _write_canonical(root / LINEAGE_FILENAME, lineage)
    _refresh_manifest(root, key)

    # When it is verified.
    with pytest.raises(VersionDigestMismatchError):
        verify_source(active, tmp_path)


def test_manifest_source_contradiction_is_digest_mismatch(tmp_path: Path) -> None:
    # Given a manifest whose source entry contradicts the actual bytes.
    _, _, _, active, root = _publish(tmp_path)
    manifest = json.loads((root / "manifest.json").read_bytes())
    for entry in manifest["files"]:
        if entry["path"] == "code/signal_engine.py":
            entry["sha256"] = "0" * 64
            entry["size"] = int(entry["size"]) + 1
    paths.atomic_write_json(root / "manifest.json", manifest)

    # When it is verified.
    with pytest.raises(VersionDigestMismatchError):
        verify_source(active, tmp_path)


def test_actual_source_contradiction_is_digest_mismatch(tmp_path: Path) -> None:
    # Given source bytes that no longer match lineage and manifest.
    _, _, _, active, root = _publish(tmp_path)
    (root / "code/signal_engine.py").write_bytes(SOURCE_BYTES + b"# drift\n")

    # When it is verified.
    with pytest.raises(VersionDigestMismatchError):
        verify_source(active, tmp_path)


def test_manifest_identity_mismatch_is_version_not_found(tmp_path: Path) -> None:
    # Given a manifest naming a different strategy.
    _, _, _, active, root = _publish(tmp_path)
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["strategy_id"] = "other-strategy"
    paths.atomic_write_json(root / "manifest.json", manifest)

    # When it is verified.
    with pytest.raises(VersionNotFoundError):
        verify_source(active, tmp_path)


def test_manifest_missing_lineage_entry_is_version_not_found(tmp_path: Path) -> None:
    # Given a manifest that omits the lineage file.
    _, _, _, active, root = _publish(tmp_path)
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["files"] = [entry for entry in manifest["files"] if entry["path"] != LINEAGE_FILENAME]
    paths.atomic_write_json(root / "manifest.json", manifest)

    # When it is verified.
    with pytest.raises(VersionNotFoundError):
        verify_source(active, tmp_path)


def test_tombstoned_registration_blocks_read_verification(tmp_path: Path) -> None:
    # Given a tombstoned registration whose files still exist.
    _, publisher, key, _, _ = _publish(tmp_path)
    tombstoned = publisher.tombstone(key)

    # When it is verified.
    with pytest.raises(VersionNotFoundError):
        verify_source(tombstoned, tmp_path)


def test_pending_registration_blocks_read_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a complete tree left pending because activation failed.
    registry = SqliteVersionRegistry(tmp_path / "state" / "strategy_store.db")
    publisher = StrategyVersionPublisher(registry, tmp_path)
    key = make_key(strategy_id="momentum", version="v7")
    monkeypatch.setattr(
        registry,
        "activate",
        lambda _key: (_ for _ in ()).throw(RuntimeError("crash")),
    )
    with pytest.raises(RuntimeError):
        publisher.publish(key, _payload())
    pending = registry.get(key)
    assert pending is not None

    # When the pending registration is verified.
    with pytest.raises(VersionNotFoundError):
        verify_source(pending, tmp_path)


def test_symlinked_source_is_version_not_found(tmp_path: Path) -> None:
    # Given a source file replaced by a symlink to valid bytes.
    _, _, _, active, root = _publish(tmp_path)
    source_path = root / "code/signal_engine.py"
    target = tmp_path / "outside-source.py"
    target.write_bytes(SOURCE_BYTES)
    source_path.unlink()
    source_path.symlink_to(target)

    # When it is verified.
    with pytest.raises(VersionNotFoundError):
        verify_source(active, tmp_path)


def test_lineage_schema_constant_is_the_frozen_contract() -> None:
    # The lineage schema marker is part of the closed on-disk contract.
    assert LINEAGE_SCHEMA == "strategy-version-lineage-v1"
    assert SourceFacts.from_bytes(SOURCE_BYTES).source_path == "code/signal_engine.py"
