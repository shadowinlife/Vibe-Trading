"""Read-only exact source reads for owner-bound strategy versions."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from src.strategy_library import paths
from src.strategy_library.paths import (
    STRATEGY_SPEC_FILENAME,
    VERSION_CONFIG_FILENAME,
    VERSION_MANIFEST_FILENAME,
    atomic_write_json,
    build_manifest,
)
from src.strategy_store.sqlite_store import SqliteStrategyStore
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import (
    StrategyNotFoundError,
    VersionDigestMismatchError,
    VersionNotFoundError,
)
from src.strategy_versions.models import RegistrationKey, VersionRegistration
from src.strategy_versions.projection import LINEAGE_FILENAME
from src.strategy_versions.publisher import (
    PublicationPayload,
    StrategyVersionPublisher,
)
from src.strategy_versions.reader import StrategyVersionSourceReader
from tests._strategy_version_support import make_key, make_source, tree_state

SPEC_BYTES = '{"kind":"code","name":"精确读取"}\n'.encode("utf-8")
CONFIG_BYTES = '{"source":"local","note":"中文"}\r\n'.encode("utf-8")
SOURCE_BYTES = ("\ufeff# 中文注释\r\n" "def generate_signals(frame):\n" "    return frame\r\n").encode("utf-8")


def _payload(source: bytes = SOURCE_BYTES) -> PublicationPayload:
    return PublicationPayload(SPEC_BYTES, CONFIG_BYTES, source)


def _publish(
    tmp_path: Path,
    key: RegistrationKey | None = None,
    source: bytes = SOURCE_BYTES,
) -> tuple[
    SqliteVersionRegistry,
    StrategyVersionPublisher,
    RegistrationKey,
    VersionRegistration,
]:
    registry = SqliteVersionRegistry(tmp_path / "state" / "strategy_store.db")
    publisher = StrategyVersionPublisher(registry, tmp_path)
    key = key or make_key(strategy_id="momentum", version="v7")
    active = publisher.publish(key, _payload(source))
    return registry, publisher, key, active


def _reader(tmp_path: Path) -> StrategyVersionSourceReader:
    registry = SqliteVersionRegistry(tmp_path / "state" / "strategy_store.db", read_only=True)
    return StrategyVersionSourceReader(registry, tmp_path)


def _version_root(tmp_path: Path, key: RegistrationKey) -> Path:
    return paths.version_dir(key.strategy_id, key.strategy_version, runtime_root=tmp_path)


def test_active_exact_read_returns_exact_bytes_and_facts(tmp_path: Path) -> None:
    # Given a published active version with non-ASCII text and mixed newlines.
    _, _, key, active = _publish(tmp_path)

    # When the exact key is read.
    result = _reader(tmp_path).read(key)

    # Then the original bytes and every derived fact are returned verbatim.
    assert result.registration == active
    assert result.registration.version_id == active.version_id
    assert result.registration.key.owner_user_id == key.owner_user_id
    assert result.source_bytes == SOURCE_BYTES
    assert result.source_bytes.startswith(b"\xef\xbb\xbf")
    assert result.source_bytes.endswith(b"\r\n")
    assert result.source_sha256 == hashlib.sha256(SOURCE_BYTES).hexdigest()
    assert result.source_sha256 == active.source.sha256
    assert result.source_byte_length == len(SOURCE_BYTES)
    assert result.source_encoding == "utf-8"


def test_matching_expected_digest_is_accepted(tmp_path: Path) -> None:
    # Given an active version and its stored digest.
    _, _, key, active = _publish(tmp_path)

    # When the caller supplies the same expected digest.
    result = _reader(tmp_path).read(key, expected_source_sha256=active.source.sha256)

    # Then the exact bytes are still returned unchanged.
    assert result.source_bytes == SOURCE_BYTES
    assert result.source_sha256 == active.source.sha256


def test_mismatching_expected_digest_is_digest_mismatch(tmp_path: Path) -> None:
    # Given an internally consistent active version.
    _, _, key, _ = _publish(tmp_path)

    # When the caller expects a different digest.
    with pytest.raises(VersionDigestMismatchError):
        _reader(tmp_path).read(key, expected_source_sha256="0" * 64)


def test_pending_registration_is_version_not_found(tmp_path: Path) -> None:
    # Given a registered but never activated identity.
    registry = SqliteVersionRegistry(tmp_path / "state" / "strategy_store.db")
    key = make_key(strategy_id="momentum", version="v7")
    registry.register_pending(key, make_source(SOURCE_BYTES))

    # When the exact key is read.
    with pytest.raises(VersionNotFoundError):
        _reader(tmp_path).read(key)


def test_tombstoned_registration_is_version_not_found(tmp_path: Path) -> None:
    # Given a tombstoned version whose files still exist.
    _, publisher, key, _ = _publish(tmp_path)
    publisher.tombstone(key)

    # When the exact key is read.
    with pytest.raises(VersionNotFoundError):
        _reader(tmp_path).read(key)


def test_missing_version_for_registered_strategy_is_version_not_found(
    tmp_path: Path,
) -> None:
    # Given an owner-scoped strategy with one active version.
    _, _, key, _ = _publish(tmp_path)
    wrong_version = make_key(owner=key.owner_user_id, strategy_id=key.strategy_id, version="v8")

    # When another version of the same strategy is requested.
    with pytest.raises(VersionNotFoundError):
        _reader(tmp_path).read(wrong_version)


def test_absent_strategy_is_strategy_not_found(tmp_path: Path) -> None:
    # Given no registry database or strategy tree at all.
    key = make_key(strategy_id="missing", version="v1")

    # When the key is read.
    with pytest.raises(StrategyNotFoundError):
        _reader(tmp_path).read(key)

    # Then the refusal created no database, directory or lock file.
    assert list(tmp_path.iterdir()) == []


def test_foreign_owner_strategy_is_strategy_not_found(tmp_path: Path) -> None:
    # Given an active version owned by another user.
    _, _, key, active = _publish(tmp_path)
    foreign = make_key(owner="owner-b", strategy_id=key.strategy_id, version=key.strategy_version)

    # When a different owner requests the same strategy and version.
    with pytest.raises(StrategyNotFoundError) as caught:
        _reader(tmp_path).read(foreign)

    # Then the failure is the same shape as an absent strategy and exposes no
    # version state or bytes.
    assert foreign.strategy_version not in str(caught.value)
    assert active.version_id not in str(caught.value)
    assert "tombstone" not in str(caught.value)


def test_legacy_unregistered_strategy_is_strategy_not_found(tmp_path: Path) -> None:
    # Given a legacy strategy directory with manifest but no trusted owner
    # registration, plus a legacy strategy-store database without the registry
    # table.
    db_path = tmp_path / "state" / "strategy_store.db"
    db_path.parent.mkdir(parents=True)
    SqliteStrategyStore(db_path).close()
    key = make_key(strategy_id="legacy", version="v1")
    root = _version_root(tmp_path, key)
    (root / "code").mkdir(parents=True)
    (root / STRATEGY_SPEC_FILENAME).write_text('{"legacy":true}', encoding="utf-8")
    (root / VERSION_CONFIG_FILENAME).write_text('{"legacy":true}', encoding="utf-8")
    (root / "code" / "signal_engine.py").write_text("legacy\n", encoding="utf-8")
    manifest = build_manifest(root)
    manifest.update(strategy_id=key.strategy_id, version=key.strategy_version)
    manifest["schema_version"] = 1
    atomic_write_json(root / VERSION_MANIFEST_FILENAME, manifest)

    # When the legacy key is read.
    with pytest.raises(StrategyNotFoundError) as caught:
        _reader(tmp_path).read(key)

    # Then neither the legacy tree nor the manifest can confer readability.
    assert key.strategy_version not in str(caught.value)
    assert (root / "code" / "signal_engine.py").read_bytes() == b"legacy\n"


def test_missing_lineage_is_version_not_found(tmp_path: Path) -> None:
    # Given an active tree whose lineage file disappeared.
    _, _, key, _ = _publish(tmp_path)
    (_version_root(tmp_path, key) / LINEAGE_FILENAME).unlink()

    # When the exact key is read.
    with pytest.raises(VersionNotFoundError):
        _reader(tmp_path).read(key)


def test_actual_byte_drift_is_digest_mismatch(tmp_path: Path) -> None:
    # Given an active tree whose source bytes changed after publication.
    _, _, key, _ = _publish(tmp_path)
    source_path = _version_root(tmp_path, key) / "code" / "signal_engine.py"
    source_path.write_bytes(SOURCE_BYTES + b"# drift\n")

    # When the exact key is read.
    with pytest.raises(VersionDigestMismatchError):
        _reader(tmp_path).read(key)


def test_other_owner_registrations_coexist_in_one_database(tmp_path: Path) -> None:
    # Given two owners publishing different strategies into one registry.
    registry = SqliteVersionRegistry(tmp_path / "state" / "strategy_store.db")
    publisher = StrategyVersionPublisher(registry, tmp_path)
    owner_a = make_key(owner="owner-a", strategy_id="momentum", version="v1")
    owner_b = make_key(owner="owner-b", strategy_id="value", version="v1")
    source_a = b"owner a source\n"
    source_b = b"owner b source\n"
    active_a = publisher.publish(owner_a, _payload(source_a))
    active_b = publisher.publish(owner_b, _payload(source_b))
    reader = _reader(tmp_path)

    # When both owners read their own exact versions.
    result_a = reader.read(owner_a)
    result_b = reader.read(owner_b)

    # Then each owner sees only their own identity and bytes.
    assert active_a.version_id != active_b.version_id
    assert result_a.registration.version_id == active_a.version_id
    assert result_a.source_bytes == source_a
    assert result_b.registration.version_id == active_b.version_id
    assert result_b.source_bytes == source_b

    # Then a foreign owner cannot read the other owner's strategy even though
    # its files exist on the same runtime root.
    with pytest.raises(StrategyNotFoundError):
        reader.read(
            make_key(
                owner="owner-b",
                strategy_id=owner_a.strategy_id,
                version=owner_a.strategy_version,
            )
        )


def test_read_consults_only_exact_version_paths_without_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an active version.
    _, _, key, _ = _publish(tmp_path)

    # When every directory listing API is forbidden.
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("reader must not list directories")

    monkeypatch.setattr(Path, "iterdir", forbidden)
    monkeypatch.setattr(os, "listdir", forbidden)
    monkeypatch.setattr(os, "scandir", forbidden)

    # Then the read still returns the exact bytes from fixed paths.
    result = _reader(tmp_path).read(key)
    assert result.source_bytes == SOURCE_BYTES


def test_read_creates_no_files_and_changes_no_bytes(tmp_path: Path) -> None:
    # Given a published version and a complete filesystem snapshot.
    _, _, key, _ = _publish(tmp_path)
    before = tree_state(tmp_path)

    # When the version is read through a fresh read-only reader.
    result = _reader(tmp_path).read(key)

    # Then no database, tree, WAL, lock or inode byte/mtime changed.
    assert result.source_bytes == SOURCE_BYTES
    assert tree_state(tmp_path) == before


def test_restarted_reader_returns_the_same_identity_and_bytes(tmp_path: Path) -> None:
    # Given a version read through one reader instance.
    _, _, key, _ = _publish(tmp_path)
    first = _reader(tmp_path).read(key)

    # When a new reader and registry adapter read the same key after restart.
    second = _reader(tmp_path).read(key)

    # Then the persisted identity and exact bytes are unchanged.
    assert second.registration.version_id == first.registration.version_id
    assert second.registration == first.registration
    assert second.source_bytes == first.source_bytes
    assert second.source_sha256 == first.source_sha256
