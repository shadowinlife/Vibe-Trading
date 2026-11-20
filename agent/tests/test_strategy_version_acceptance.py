"""Direct acceptance tests for frozen VT-0003 gaps A8, A16, A17 and A19.

Every test uses the real registry, publisher, reader and files.  Crashes are
simulated at durable-state boundaries instead of killing the pytest process.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.strategy_version_source_routes import register_strategy_version_source_routes
from src.strategy_library import paths
from src.strategy_store._version_registry_schema import _TABLE
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions import publisher as publisher_module
from src.strategy_versions.errors import (
    VersionConflictError,
    VersionDigestMismatchError,
    VersionNotFoundError,
    VersionTombstonedError,
)
from src.strategy_versions.models import RegistrationKey, VersionRegistration, VersionState
from src.strategy_versions.publisher import PublicationPayload, StrategyVersionPublisher
from src.strategy_versions.reader import StrategyVersionSourceReader
from tests._strategy_version_source_support import (
    AUTH,
    CONFIG_BYTES,
    SOURCE_BYTES,
    SPEC_BYTES,
    URL,
    Harness,
    body,
)
from tests._strategy_version_support import make_key

_TAMPERED_SHA256 = "0" * 64


def _payload(source: bytes = SOURCE_BYTES) -> PublicationPayload:
    return PublicationPayload(SPEC_BYTES, CONFIG_BYTES, source)


def _publisher(tmp_path: Path) -> tuple[SqliteVersionRegistry, StrategyVersionPublisher]:
    registry = SqliteVersionRegistry(tmp_path / "state" / "strategy_store.db")
    return registry, StrategyVersionPublisher(registry, tmp_path)


def _reader(tmp_path: Path) -> StrategyVersionSourceReader:
    registry = SqliteVersionRegistry(tmp_path / "state" / "strategy_store.db", read_only=True)
    return StrategyVersionSourceReader(registry, tmp_path)


def _version_root(tmp_path: Path, key: RegistrationKey) -> Path:
    return paths.version_dir(key.strategy_id, key.strategy_version, runtime_root=tmp_path)


def _files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _publish_active(
    tmp_path: Path,
) -> tuple[SqliteVersionRegistry, StrategyVersionPublisher, RegistrationKey, VersionRegistration]:
    registry, publisher = _publisher(tmp_path)
    key = make_key(strategy_id="momentum", version="v7")
    return registry, publisher, key, publisher.publish(key, _payload())


def _assert_version_not_found(tmp_path: Path, key: RegistrationKey) -> None:
    with pytest.raises(VersionNotFoundError):
        _reader(tmp_path).read(key)


def _assert_single_active(registry: SqliteVersionRegistry, key: RegistrationKey, version_id: str) -> None:
    records = [record for record in registry.all_records() if record.key == key]
    assert len(records) == 1
    assert records[0].version_id == version_id
    assert records[0].state is VersionState.ACTIVE


@pytest.mark.parametrize("draft_state", ["overwritten", "removed"])
def test_a8_read_never_infers_from_git_paths_cwd_or_the_strategy_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, draft_state: str
) -> None:
    # Given an active version and a mutable strategy-root draft source file.
    _, _, key, active = _publish_active(tmp_path)
    draft = paths.strategy_dir(key.strategy_id, runtime_root=tmp_path) / "code" / "signal_engine.py"
    draft.parent.mkdir(parents=True)
    draft.write_bytes(b"# stale strategy-root draft\n")
    if draft_state == "overwritten":
        draft.write_bytes(b"# different strategy-root draft\n")
    else:
        draft.unlink()

    # And a decoy cwd whose fake Git HEAD points at another repository.
    decoy = tmp_path / "decoy-cwd"
    elsewhere = tmp_path / "elsewhere-git"
    (decoy / "code").mkdir(parents=True)
    (decoy / "code" / "signal_engine.py").write_bytes(b"# decoy cwd source\n")
    (decoy / ".git").mkdir()
    elsewhere.mkdir()
    (elsewhere / "HEAD").write_text("ref: refs/heads/elsewhere\n", encoding="utf-8")
    (decoy / ".git" / "HEAD").write_text(f"ref: {elsewhere}\n", encoding="utf-8")
    monkeypatch.chdir(decoy)

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("source read must not invoke Git or any subprocess")

    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _forbidden)
    for name in ("system", "popen"):
        monkeypatch.setattr(os, name, _forbidden)
    monkeypatch.setattr(shutil, "which", _forbidden)

    # When the exact key is read through a fresh read-only reader.
    result = _reader(tmp_path).read(key)

    # Then only the frozen version bytes are returned.
    assert result.source_bytes == SOURCE_BYTES
    assert result.source_sha256 == hashlib.sha256(SOURCE_BYTES).hexdigest()
    assert result.registration.version_id == active.version_id
    assert (_version_root(tmp_path, key) / "code" / "signal_engine.py").read_bytes() == SOURCE_BYTES
    assert draft.exists() is (draft_state == "overwritten")
    if draft_state == "overwritten":
        assert draft.read_bytes() == b"# different strategy-root draft\n"

    # Then an unknown version of the same strategy still fails closed.
    unknown = make_key(owner=key.owner_user_id, strategy_id=key.strategy_id, version="v8")
    with pytest.raises(VersionNotFoundError):
        _reader(tmp_path).read(unknown)


def test_a16_tombstone_survives_restart_and_a_new_label_starts_fresh(tmp_path: Path) -> None:
    # Given an active version that is then tombstoned.
    _, publisher, key, active = _publish_active(tmp_path)
    tombstoned = publisher.tombstone(key)
    assert tombstoned.state is VersionState.TOMBSTONED

    # When fresh registry, reader and publisher instances start over the DB.
    restarted_registry = SqliteVersionRegistry(tmp_path / "state" / "strategy_store.db")
    restarted_reader = StrategyVersionSourceReader(restarted_registry, tmp_path)
    restarted_publisher = StrategyVersionPublisher(restarted_registry, tmp_path)

    # Then the exact version is still unreadable and cannot be revived.
    with pytest.raises(VersionNotFoundError):
        restarted_reader.read(key)
    assert restarted_registry.get(key) == tombstoned
    with pytest.raises(VersionTombstonedError):
        restarted_publisher.publish(key, _payload())

    # When a new version label publishes the same bytes.
    new_key = make_key(owner=key.owner_user_id, strategy_id=key.strategy_id, version="v8")
    new_active = restarted_publisher.publish(new_key, _payload())

    # Then it receives a distinct identity and reads successfully.
    assert new_active.state is VersionState.ACTIVE
    assert new_active.version_id != active.version_id
    result = restarted_reader.read(new_key)
    assert result.registration.version_id == new_active.version_id
    assert result.source_bytes == SOURCE_BYTES


def test_a17_crash_after_pending_before_snapshot_recovers_the_same_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a publisher that crashes after pending registration, before staging.
    registry, publisher = _publisher(tmp_path)
    key = make_key(strategy_id="momentum", version="v1")
    original_write_tree = publisher._write_tree

    def _crash(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("crash before snapshot")

    monkeypatch.setattr(publisher, "_write_tree", _crash)
    with pytest.raises(RuntimeError, match="crash before snapshot"):
        publisher.publish(key, _payload())

    # Then the durable state is a lone pending row with no snapshot or read.
    pending = registry.get(key)
    assert pending is not None and pending.state is VersionState.PENDING
    assert not paths.versions_dir(key.strategy_id, runtime_root=tmp_path).exists()
    _assert_version_not_found(tmp_path, key)

    # When the identical payload is retried through a process-like publisher.
    monkeypatch.setattr(publisher, "_write_tree", original_write_tree)
    active = publisher.publish(key, _payload())

    # Then the original identity activates exactly once and reads its bytes.
    assert active.version_id == pending.version_id
    _assert_single_active(registry, key, pending.version_id)
    assert _reader(tmp_path).read(key).source_bytes == SOURCE_BYTES


def test_a17_partial_staging_write_leaves_no_final_or_readable_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a publisher whose third staging write fails after a partial write.
    registry, publisher = _publisher(tmp_path)
    key = make_key(strategy_id="momentum", version="v1")
    original_write_bytes = publisher_module._write_bytes
    calls = 0

    def _partial_write(path: Path, data: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            original_write_bytes(path, data[: max(1, len(data) // 2)])
            raise OSError("simulated partial staging write")
        original_write_bytes(path, data)

    monkeypatch.setattr(publisher_module, "_write_bytes", _partial_write)
    with pytest.raises(OSError, match="partial staging write"):
        publisher.publish(key, _payload())

    # Then no staging leftover, final tree, or readable version exists.
    pending = registry.get(key)
    assert pending is not None and pending.state is VersionState.PENDING
    versions = paths.versions_dir(key.strategy_id, runtime_root=tmp_path)
    assert versions.is_dir()
    assert list(versions.iterdir()) == []
    assert not _version_root(tmp_path, key).exists()
    _assert_version_not_found(tmp_path, key)

    # When the identical payload is retried through a fresh publisher instance.
    monkeypatch.setattr(publisher_module, "_write_bytes", original_write_bytes)
    fresh_registry = SqliteVersionRegistry(tmp_path / "state" / "strategy_store.db")
    restarted = StrategyVersionPublisher(fresh_registry, tmp_path)
    active = restarted.publish(key, _payload())

    # Then the same identity activates once and the exact bytes are readable.
    assert active.version_id == pending.version_id
    _assert_single_active(fresh_registry, key, pending.version_id)
    assert _reader(tmp_path).read(key).source_bytes == SOURCE_BYTES


def test_a17_complete_tree_before_activation_recovers_and_refuses_changed_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a publisher that crashes after the complete final tree is written.
    registry, publisher = _publisher(tmp_path)
    key = make_key(strategy_id="momentum", version="v1")
    original_activate = registry.activate

    def _crash(_key: RegistrationKey) -> object:
        raise RuntimeError("crash before activation")

    monkeypatch.setattr(registry, "activate", _crash)
    with pytest.raises(RuntimeError, match="crash before activation"):
        publisher.publish(key, _payload())

    # Then the complete tree is not readable while the registration is pending.
    pending = registry.get(key)
    assert pending is not None and pending.state is VersionState.PENDING
    root = _version_root(tmp_path, key)
    before = _files(root)
    assert before["code/signal_engine.py"] == SOURCE_BYTES
    assert "manifest.json" in before
    _assert_version_not_found(tmp_path, key)

    # When a changed source is retried after restart.
    monkeypatch.setattr(registry, "activate", original_activate)
    with pytest.raises(VersionConflictError):
        publisher.publish(key, _payload(source=b"# changed on resume\n"))

    # Then the pending identity and complete tree stay untouched and unreadable.
    assert registry.get(key) == pending
    assert _files(root) == before
    _assert_version_not_found(tmp_path, key)

    # When the identical payload is retried.
    active = publisher.publish(key, _payload())

    # Then the same identity activates once, reusing the exact tree.
    assert active.version_id == pending.version_id
    _assert_single_active(registry, key, pending.version_id)
    assert _files(root) == before
    assert _reader(tmp_path).read(key).source_bytes == SOURCE_BYTES


def _tamper_registry_source_sha256(db_path: Path, key: RegistrationKey) -> None:
    parameters = (key.owner_user_id, key.strategy_id, key.strategy_version)
    statement = (
        f"UPDATE {_TABLE} SET source_sha256 = ? " "WHERE owner_user_id = ? AND strategy_id = ? AND strategy_version = ?"
    )
    connection = sqlite3.connect(db_path)
    try:
        # The production trigger must refuse the write before the test drops it.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement, (_TAMPERED_SHA256, *parameters))
        connection.execute(f"DROP TRIGGER {_TABLE}_identity_immutable")
        cursor = connection.execute(statement, (_TAMPERED_SHA256, *parameters))
        assert cursor.rowcount == 1
        connection.commit()
    finally:
        connection.close()


def test_a19_registry_digest_drift_is_detected_by_reader_and_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an active version whose registry digest is tampered test-only.
    harness = Harness(tmp_path, monkeypatch)
    key, active = harness.publish()
    _tamper_registry_source_sha256(harness.db_path, key)
    tampered = SqliteVersionRegistry(harness.db_path).get(key)
    assert tampered is not None and tampered.source.sha256 == _TAMPERED_SHA256

    # When the exact version is read through a fresh read-only reader.
    reader = StrategyVersionSourceReader(SqliteVersionRegistry(harness.db_path, read_only=True), tmp_path)
    with pytest.raises(VersionDigestMismatchError):
        reader.read(key)

    # And when the real HTTP route reads it.
    app = FastAPI()
    register_strategy_version_source_routes(app, harness.provider)
    with TestClient(app) as client:
        response = client.post(URL, json=body(), headers=AUTH)

    # Then the route returns the typed 409 without source, bytes, digest or length.
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "input_digest_mismatch"}}
    for leaked in (
        SOURCE_BYTES,
        base64.b64encode(SOURCE_BYTES),
        hashlib.sha256(SOURCE_BYTES).hexdigest().encode(),
        _TAMPERED_SHA256.encode(),
        str(len(SOURCE_BYTES)).encode(),
        active.version_id.encode(),
    ):
        assert leaked not in response.content
