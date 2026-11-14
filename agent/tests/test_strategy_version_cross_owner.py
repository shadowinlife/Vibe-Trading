"""A13: foreign and never-existing strategies are indistinguishable at the route.

The two owners are seeded in one database with distinct strategy ids, so this
test is independent of the concurrent cross-owner collision fix.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.strategy_version_source_routes import register_strategy_version_source_routes
from tests._strategy_version_source_support import (
    AUTH,
    SOURCE_BYTES,
    URL,
    Harness,
    body,
)

OWNER_B = "synthetic-owner-0003-cross-owner-b"


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(tmp_path, monkeypatch)


@pytest.fixture
def client(harness: Harness):
    app = FastAPI()
    register_strategy_version_source_routes(app, harness.provider)
    with TestClient(app) as test_client:
        yield test_client


def test_a13_foreign_and_absent_strategy_are_byte_identical(client: TestClient, harness: Harness) -> None:
    # Given owner A and owner B with distinct strategies in one registry.
    key_a, active_a = harness.publish(strategy_id="momentum", version="v7")
    key_b, active_b = harness.publish(strategy_id="value", version="v7", owner=OWNER_B)
    assert active_a.version_id != active_b.version_id

    # When owner A asks for owner B's strategy and for a never-existing one.
    foreign = client.post(URL, json=body(strategy_id=key_b.strategy_id, version="v7"), headers=AUTH)
    absent = client.post(URL, json=body(strategy_id="never-exists", version="v7"), headers=AUTH)

    # Then both refusals are the same bytes with only the fixed typed envelope.
    assert foreign.status_code == absent.status_code == 404
    assert foreign.content == absent.content
    assert foreign.json() == {"error": {"code": "strategy_not_found"}}
    assert set(foreign.json()) == {"error"}

    # Then no source, digest, length, version or strategy identity leaked.
    for field in (
        b"source_bytes_base64",
        b"source_sha256",
        b"source_byte_length",
        b"source_path",
        b"version_id",
        b"strategy_version",
    ):
        assert field not in foreign.content
    for leaked in (
        SOURCE_BYTES,
        base64.b64encode(SOURCE_BYTES),
        hashlib.sha256(SOURCE_BYTES).hexdigest().encode(),
        active_b.version_id.encode(),
        key_b.strategy_id.encode(),
        OWNER_B.encode(),
    ):
        assert leaked not in foreign.content

    # Then both requests reached the owner-exact lookup seam and refused there.
    assert (harness.provider_calls, harness.lookups) == (2, 2)
