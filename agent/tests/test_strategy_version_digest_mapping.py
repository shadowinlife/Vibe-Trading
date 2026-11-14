"""A19 mapping: a persisted digest that cannot hydrate is typed, never 503.

An ACTIVE row whose persisted ``source_sha256`` is non-canonical (uppercase or
non-hex) must read as ``VersionDigestMismatchError`` and answer ``409
input_digest_mismatch``.  PENDING and TOMBSTONED rows keep not-found priority
(``404 version_not_found``).  Every tamper happens in a copied database; the
production schema, trigger and ``SourceFacts`` rules are untouched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.strategy_version_source_routes import register_strategy_version_source_routes
from src.strategy_store._version_registry_schema import _TABLE
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import VersionDigestMismatchError, VersionNotFoundError
from src.strategy_versions.models import RegistrationKey, SourceFacts
from src.strategy_versions.reader import StrategyVersionSourceReader
from tests._strategy_version_source_support import (
    AUTH,
    OWNER,
    SOURCE_BYTES,
    URL,
    Harness,
    body,
)

UPPERCASE_DIGEST = "A" * 64
NON_HEX_DIGEST = "g" * 64
MALFORMED_DIGESTS = pytest.mark.parametrize(
    "digest",
    [UPPERCASE_DIGEST, NON_HEX_DIGEST],
    ids=["uppercase", "non-hex"],
)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(tmp_path, monkeypatch)


def _copy_and_tamper(source_db: Path, target_db: Path, key: RegistrationKey, digest: str) -> Path:
    """Copy *source_db* and replace one digest in the copy only.

    The immutable trigger is dropped inside the copied database (never the
    live one), then exactly one row is rewritten before the copy is read.
    """
    source = sqlite3.connect(source_db)
    target = sqlite3.connect(target_db)
    try:
        source.backup(target)
        target.execute(f"DROP TRIGGER {_TABLE}_identity_immutable")
        cursor = target.execute(
            f"UPDATE {_TABLE} SET source_sha256 = ? "
            "WHERE owner_user_id = ? AND strategy_id = ? AND strategy_version = ?",
            (digest, key.owner_user_id, key.strategy_id, key.strategy_version),
        )
        assert cursor.rowcount == 1
        target.commit()
    finally:
        target.close()
        source.close()
    return target_db


def _reader(db_path: Path, root: Path) -> StrategyVersionSourceReader:
    return StrategyVersionSourceReader(SqliteVersionRegistry(db_path, read_only=True), root)


def _client(db_path: Path, root: Path) -> TestClient:
    app = FastAPI()
    register_strategy_version_source_routes(app, lambda: _reader(db_path, root))
    return TestClient(app)


@MALFORMED_DIGESTS
def test_active_malformed_digest_reads_as_digest_mismatch(harness: Harness, tmp_path: Path, digest: str) -> None:
    # Given an ACTIVE row whose persisted digest cannot hydrate.
    key, _ = harness.publish()
    tampered = _copy_and_tamper(harness.db_path, tmp_path / "tampered-active.db", key, digest)

    # When the exact version is read through the typed reader.
    with pytest.raises(VersionDigestMismatchError) as raised:
        _reader(tampered, tmp_path).read(key)

    # Then the hydration failure is the cause, not a storage failure.
    assert isinstance(raised.value.__cause__, (ValueError, TypeError))


@MALFORMED_DIGESTS
def test_active_malformed_digest_route_is_409_without_source_fields(
    harness: Harness, tmp_path: Path, digest: str
) -> None:
    # Given an ACTIVE row whose persisted digest cannot hydrate.
    key, active = harness.publish()
    tampered = _copy_and_tamper(harness.db_path, tmp_path / "tampered-active-route.db", key, digest)

    # When the real HTTP route reads it.
    with _client(tampered, tmp_path) as client:
        response = client.post(URL, json=body(), headers=AUTH)

    # Then the typed 409 is returned, never a generic 503.
    assert response.status_code == 409, response.text
    assert response.json() == {"error": {"code": "input_digest_mismatch"}}
    for leaked in (SOURCE_BYTES, digest.encode(), active.version_id.encode()):
        assert leaked not in response.content


@pytest.mark.parametrize("state", ["pending", "tombstoned"], ids=["pending", "tombstoned"])
@MALFORMED_DIGESTS
def test_non_active_malformed_digest_keeps_not_found_priority(
    harness: Harness, tmp_path: Path, state: str, digest: str
) -> None:
    # Given a non-ACTIVE row whose persisted digest cannot hydrate.
    key = RegistrationKey.parse(OWNER, "momentum", f"v-{state}")
    registry = SqliteVersionRegistry(harness.db_path)
    registry.register_pending(key, SourceFacts.from_bytes(SOURCE_BYTES))
    if state == "tombstoned":
        registry.tombstone(key)
    tampered = _copy_and_tamper(harness.db_path, tmp_path / f"tampered-{state}.db", key, digest)

    # When the reader and the route resolve the exact key.
    with pytest.raises(VersionNotFoundError) as raised:
        _reader(tampered, tmp_path).read(key)
    assert isinstance(raised.value.__cause__, (ValueError, TypeError))
    with _client(tampered, tmp_path) as client:
        response = client.post(URL, json=body(version=key.strategy_version), headers=AUTH)

    # Then not-found keeps priority over digest mismatch.
    assert response.status_code == 404, response.text
    assert response.json() == {"error": {"code": "version_not_found"}}
