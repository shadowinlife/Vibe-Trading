"""Publication contract for lineage-backed F9 strategy versions."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from src.strategy_library import paths
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import (
    VersionConflictError,
    VersionNotFoundError,
    VersionTombstonedError,
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
from tests._strategy_version_support import make_key, tree_state

SPEC_BYTES = '{"kind":"code","name":"动量 Alpha"}\n'.encode("utf-8")
CONFIG_BYTES = '{"source":"local","note":"中文"}\r\n'.encode("utf-8")
SOURCE_BYTES = ("\ufeff# 中文注释\r\n" "def generate_signals(frame):\n" "    return frame\r\n").encode("utf-8")
F9_TREE = {
    "strategy.json",
    "config.json",
    "code/signal_engine.py",
    LINEAGE_FILENAME,
    "manifest.json",
}


def _payload(source: bytes = SOURCE_BYTES) -> PublicationPayload:
    return PublicationPayload(SPEC_BYTES, CONFIG_BYTES, source)


def _publisher(
    tmp_path: Path,
) -> tuple[SqliteVersionRegistry, StrategyVersionPublisher]:
    registry = SqliteVersionRegistry(tmp_path / "state" / "strategy_store.db")
    return registry, StrategyVersionPublisher(registry, tmp_path)


def _version_root(tmp_path: Path, key: RegistrationKey) -> Path:
    return paths.version_dir(key.strategy_id, key.strategy_version, runtime_root=tmp_path)


def _files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _manifest(root: Path) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return payload, {entry["path"]: entry for entry in payload["files"]}


def _create_pending_with_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[
    SqliteVersionRegistry,
    StrategyVersionPublisher,
    RegistrationKey,
    VersionRegistration,
    Path,
]:
    registry, publisher = _publisher(tmp_path)
    key = make_key(strategy_id="momentum", version="v1")
    original_activate = registry.activate
    monkeypatch.setattr(
        registry,
        "activate",
        lambda _key: (_ for _ in ()).throw(RuntimeError("crash before activation")),
    )
    with pytest.raises(RuntimeError, match="crash before activation"):
        publisher.publish(key, _payload())
    pending = registry.get(key)
    assert pending is not None
    assert pending.state is VersionState.PENDING
    monkeypatch.setattr(registry, "activate", original_activate)
    return registry, publisher, key, pending, _version_root(tmp_path, key)


def test_publish_creates_exact_lineage_backed_f9_tree_and_activates(
    tmp_path: Path,
) -> None:
    # Given a validated owner-scoped key and a non-ASCII payload.
    registry, publisher = _publisher(tmp_path)
    key = make_key(strategy_id="momentum", version="v1")

    # When the payload is published.
    active = publisher.publish(key, _payload())

    # Then the registration is active and the tree has exactly the F9 shape.
    assert active.state is VersionState.ACTIVE
    assert active.source == SourceFacts.from_bytes(SOURCE_BYTES)
    assert registry.get(key) == active
    root = _version_root(tmp_path, key)
    files = _files(root)
    assert set(files) == F9_TREE
    assert files["strategy.json"] == SPEC_BYTES
    assert files["config.json"] == CONFIG_BYTES
    assert files["code/signal_engine.py"] == SOURCE_BYTES

    # Then lineage binds the owner, key, identity and exact source facts.
    lineage = json.loads(files[LINEAGE_FILENAME])
    assert lineage == {
        "schema": LINEAGE_SCHEMA,
        "owner_user_id": key.owner_user_id,
        "strategy_id": key.strategy_id,
        "strategy_version": key.strategy_version,
        "version_id": active.version_id,
        "source_path": active.source.source_path,
        "source_encoding": active.source.source_encoding,
        "source_sha256": active.source.sha256,
        "source_byte_length": active.source.byte_length,
    }
    assert "state" not in lineage
    assert files[LINEAGE_FILENAME] == build_lineage(active)

    # Then the manifest names the version and includes the lineage entry.
    manifest, entries = _manifest(root)
    assert manifest["strategy_id"] == key.strategy_id
    assert manifest["version"] == key.strategy_version
    assert manifest["schema_version"] == 1
    assert set(entries) == F9_TREE - {"manifest.json"}
    assert entries[LINEAGE_FILENAME] == {
        "path": LINEAGE_FILENAME,
        "sha256": hashlib.sha256(files[LINEAGE_FILENAME]).hexdigest(),
        "size": len(files[LINEAGE_FILENAME]),
    }
    source_entry = entries["code/signal_engine.py"]
    assert source_entry["sha256"] == active.source.sha256
    assert source_entry["size"] == active.source.byte_length


def test_source_bytes_are_published_without_normalisation(tmp_path: Path) -> None:
    # Given a payload carrying a BOM, non-ASCII text, mixed CRLF/LF and a
    # trailing newline.
    _, publisher = _publisher(tmp_path)
    key = make_key(strategy_id="momentum", version="v1")

    # When it is published.
    publisher.publish(key, _payload())

    # Then every original byte survives, including the trailing CRLF.
    actual = (_version_root(tmp_path, key) / "code/signal_engine.py").read_bytes()
    assert actual == SOURCE_BYTES
    assert actual.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in actual
    assert b"\n" in actual.replace(b"\r\n", b"")
    assert actual.endswith(b"\r\n")
    assert hashlib.sha256(actual).hexdigest() == SourceFacts.from_bytes(SOURCE_BYTES).sha256


def test_active_replay_returns_the_same_registration_without_writes(
    tmp_path: Path,
) -> None:
    # Given an active registration.
    registry, publisher = _publisher(tmp_path)
    key = make_key(strategy_id="momentum", version="v1")
    active = publisher.publish(key, _payload())
    before = tree_state(tmp_path)

    # When the exact same publication is replayed.
    replay = publisher.publish(key, _payload())

    # Then identity is returned unchanged and no file byte, mtime or inode
    # changed, including the registry database.
    assert replay == active
    assert tree_state(tmp_path) == before
    assert registry.get(key) == active


def test_active_version_deleted_after_activation_never_rebuilds(tmp_path: Path) -> None:
    # Given an active version whose final tree is later deleted.
    registry, publisher = _publisher(tmp_path)
    key = make_key(strategy_id="momentum", version="v1")
    active = publisher.publish(key, _payload())
    root = _version_root(tmp_path, key)
    shutil.rmtree(root)

    # When the payload is replayed.
    with pytest.raises(VersionNotFoundError):
        publisher.publish(key, _payload())

    # Then the publisher fails closed and does not recreate from the payload.
    assert not root.exists()
    assert registry.get(key) == active


def test_pending_retry_after_deleted_unactivated_final_keeps_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a completed tree whose activation crashed, leaving it pending.
    registry, publisher, key, pending, root = _create_pending_with_tree(tmp_path, monkeypatch)
    shutil.rmtree(root)

    # When the same facts are published again after deleting the final tree.
    active = publisher.publish(key, _payload())

    # Then the original random identity is retained and the tree is rebuilt.
    assert active.version_id == pending.version_id
    assert active.state is VersionState.ACTIVE
    lineage = json.loads((root / LINEAGE_FILENAME).read_bytes())
    assert lineage["version_id"] == pending.version_id
    assert registry.get(key) == active


def test_changed_payload_for_pending_identity_conflicts_before_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a pending identity whose final tree was deleted.
    registry, publisher, key, pending, root = _create_pending_with_tree(tmp_path, monkeypatch)
    shutil.rmtree(root)

    # When a different source payload is published.
    with pytest.raises(VersionConflictError):
        publisher.publish(key, _payload(source=b"different source\n"))

    # Then the original identity stays authoritative and nothing is rebuilt.
    assert registry.get(key) == pending
    assert not root.exists()


@pytest.mark.parametrize("field", ["spec_bytes", "config_bytes"])
def test_pending_retry_with_changed_non_source_bytes_conflicts_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    # Given a pending identity whose final tree already exists.
    registry, publisher, key, pending, root = _create_pending_with_tree(tmp_path, monkeypatch)
    before = _files(root)
    values = {
        "spec_bytes": SPEC_BYTES,
        "config_bytes": CONFIG_BYTES,
        "source_bytes": SOURCE_BYTES,
    }
    values[field] = values[field] + b"changed"

    # When the retry changes only a non-source payload component.
    with pytest.raises(VersionConflictError):
        publisher.publish(key, PublicationPayload(**values))

    # Then the pending row stays pending and the existing bytes are untouched.
    assert registry.get(key) == pending
    assert _files(root) == before


def test_cross_owner_same_strategy_version_conflicts_without_writes(tmp_path: Path) -> None:
    # Given owner A's active version at the shared F9 path.
    registry, publisher = _publisher(tmp_path)
    key_a = make_key(owner="owner-a", strategy_id="momentum", version="v1")
    active = publisher.publish(key_a, _payload())
    before = tree_state(tmp_path / "strategies")

    # When owner B publishes the same strategy id and version.
    key_b = make_key(owner="owner-b", strategy_id="momentum", version="v1")
    with pytest.raises(VersionConflictError):
        publisher.publish(key_b, _payload())

    # Then owner B has no row and owner A's tree bytes are unchanged.
    assert registry.get(key_b) is None
    assert registry.get(key_a) == active
    assert tree_state(tmp_path / "strategies") == before


def test_changed_payload_for_active_identity_conflicts_without_writes(
    tmp_path: Path,
) -> None:
    # Given an active registration.
    registry, publisher = _publisher(tmp_path)
    key = make_key(strategy_id="momentum", version="v1")
    active = publisher.publish(key, _payload())
    tree_before = tree_state(tmp_path / "strategies")

    # When a different source payload is published.
    with pytest.raises(VersionConflictError):
        publisher.publish(key, _payload(source=b"different source\n"))

    # Then the original identity and tree remain unchanged.
    assert registry.get(key) == active
    assert tree_state(tmp_path / "strategies") == tree_before


def test_tombstone_is_terminal_idempotent_and_keeps_the_tree(tmp_path: Path) -> None:
    # Given an active registration.
    registry, publisher = _publisher(tmp_path)
    key = make_key(strategy_id="momentum", version="v1")
    publisher.publish(key, _payload())
    root = _version_root(tmp_path, key)
    before = _files(root)

    # When the version is tombstoned twice.
    tombstoned = publisher.tombstone(key)
    assert publisher.tombstone(key) == tombstoned

    # Then tombstone is terminal and the files are retained but unreadable.
    assert tombstoned.state is VersionState.TOMBSTONED
    with pytest.raises(VersionTombstonedError):
        publisher.publish(key, _payload())
    with pytest.raises(VersionTombstonedError):
        registry.activate(key)
    with pytest.raises(VersionNotFoundError):
        verify_source(tombstoned, tmp_path)
    assert _files(root) == before
    assert registry.get(key) == tombstoned


def test_legacy_version_tree_without_lineage_is_not_overwritten(tmp_path: Path) -> None:
    # Given a legacy F9 directory with matching filenames but no lineage.
    registry, publisher = _publisher(tmp_path)
    key = make_key(strategy_id="momentum", version="v1")
    root = _version_root(tmp_path, key)
    (root / "code").mkdir(parents=True)
    (root / "strategy.json").write_text('{"legacy":true}', encoding="utf-8")
    (root / "config.json").write_text('{"legacy":true}', encoding="utf-8")
    (root / "code/signal_engine.py").write_text("legacy\n", encoding="utf-8")
    before = _files(root)

    # When a new payload targets the same path.
    with pytest.raises(VersionNotFoundError):
        publisher.publish(key, _payload())

    # Then the legacy bytes are untouched and the new record stays pending.
    assert _files(root) == before
    record = registry.get(key)
    assert record is not None
    assert record.state is VersionState.PENDING


@pytest.mark.parametrize("field", ["spec_bytes", "config_bytes", "source_bytes"])
def test_empty_payload_component_is_rejected_before_registration(tmp_path: Path, field: str) -> None:
    # Given a payload with one empty component.
    values = {
        "spec_bytes": SPEC_BYTES,
        "config_bytes": CONFIG_BYTES,
        "source_bytes": SOURCE_BYTES,
    }
    values[field] = b""
    db_path = tmp_path / "state" / "strategy_store.db"

    # When the payload is constructed.
    with pytest.raises(ValueError):
        PublicationPayload(**values)

    # Then no registry row or database was created.
    registry = SqliteVersionRegistry(db_path)
    assert registry.get(make_key()) is None
    assert not db_path.exists()


@pytest.mark.parametrize(
    "key",
    (
        make_key(strategy_id="../escape"),
        make_key(version="V1"),
    ),
)
def test_unsafe_registration_path_fails_before_pending_row(tmp_path: Path, key: RegistrationKey) -> None:
    # Given a key that cannot become a safe F9 path segment.
    registry, publisher = _publisher(tmp_path)
    db_path = tmp_path / "state" / "strategy_store.db"

    # When publication is attempted.
    with pytest.raises(ValueError):
        publisher.publish(key, _payload())

    # Then no pending row or registry database was written.
    assert registry.get(key) is None
    assert not db_path.exists()
