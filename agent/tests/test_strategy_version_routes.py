"""Frozen HTTP contract for the authenticated exact strategy-version read.

Reads use the real publisher/registry/reader; refusals assert the provider is
never constructed or consulted.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import strategy_run_server
from src.api import strategy_owner_binding, strategy_surfaces
from src.api.strategy_version_source_routes import (
    register_strategy_version_source_routes,
)
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import VersionLockUnavailable
from src.strategy_versions.models import RegistrationKey, SourceFacts
from tests._strategy_version_source_support import (
    AUTH,
    BINDING_ENV,
    FIELDS,
    OTHER_TOKEN,
    OWNER,
    SOURCE_BYTES,
    TOKEN,
    URL,
    Harness,
    binding_bytes,
    body,
)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(tmp_path, monkeypatch)


@pytest.fixture
def client(harness: Harness):
    app = FastAPI()
    register_strategy_version_source_routes(app, harness.provider)
    with TestClient(app) as test_client:
        yield test_client


def test_owner_read_returns_exact_bytes_and_only_the_frozen_fields(client: TestClient, harness: Harness) -> None:
    # Given an active version with non-ASCII, BOM, mixed newlines and a trailer.
    key, active = harness.publish()

    # When the owner reads it with a matching expected digest and a spoofed
    # owner-like header that must be ignored.
    response = client.post(
        URL,
        json=body(expected_source_sha256=active.source.sha256),
        headers={**AUTH, "X-Owner-User-Id": "attacker"},
    )

    # Then the exact bytes and closed field set are returned.
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == FIELDS
    assert result["contract_version"] == "strategy-version-source-v1"
    assert result["strategy_id"] == key.strategy_id
    assert result["strategy_version"] == key.strategy_version
    assert result["version_id"] == active.version_id
    assert result["owner_user_id"] == OWNER
    assert result["caller_scope"] == "owner"
    assert result["source_path"] == "code/signal_engine.py"
    assert result["source_encoding"] == "utf-8"
    assert result["source_byte_length"] == len(SOURCE_BYTES)
    assert result["source_sha256"] == hashlib.sha256(SOURCE_BYTES).hexdigest()
    assert "\n" not in result["source_bytes_base64"]
    assert base64.b64decode(result["source_bytes_base64"], validate=True) == SOURCE_BYTES
    assert (harness.provider_calls, harness.lookups) == (1, 1)


@pytest.mark.parametrize(
    ("headers", "expected_status", "code"),
    [
        ({}, 401, "unauthorized"),
        ({"Authorization": f"Basic {TOKEN}"}, 401, "unauthorized"),
        ({"Authorization": f"Bearer {OTHER_TOKEN}"}, 403, "forbidden"),
    ],
)
def test_authentication_failures_are_typed_and_never_resolve_the_provider(
    client: TestClient,
    harness: Harness,
    headers: dict[str, str],
    expected_status: int,
    code: str,
) -> None:
    response = client.post(URL, json=body(), headers=headers)
    assert response.status_code == expected_status
    assert response.json() == {"error": {"code": code}}
    assert (harness.provider_calls, harness.lookups) == (0, 0)


def test_duplicate_authorization_headers_are_refused(client: TestClient, harness: Harness) -> None:
    duplicate = [
        ("Authorization", f"Bearer {TOKEN}"),
        ("Authorization", f"Bearer {TOKEN}"),
    ]
    response = client.post(URL, json=body(), headers=duplicate)
    assert response.status_code in {401, 403}
    assert response.json()["error"]["code"] in {"unauthorized", "forbidden"}
    assert (harness.provider_calls, harness.lookups) == (0, 0)


def test_authentication_precedes_body_parsing(client: TestClient, harness: Harness) -> None:
    response = client.post(URL, content=b"{")
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "unauthorized"}}
    assert (harness.provider_calls, harness.lookups) == (0, 0)


@pytest.mark.parametrize("case", ["missing", "malformed", "unbound-token"])
def test_untrusted_binding_keeps_the_fixed_403_and_never_resolves_the_provider(
    client: TestClient,
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    if case == "missing":
        monkeypatch.delenv(BINDING_ENV, raising=False)
    elif case == "malformed":
        harness.bind(OWNER, content=b"{")
    else:
        harness.bind(OWNER, content=binding_bytes(OWNER, OTHER_TOKEN))

    # A malformed body proves binding is resolved before body parsing.
    response = client.post(URL, content=b"{", headers=AUTH)
    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden"}
    assert (harness.provider_calls, harness.lookups) == (0, 0)


def test_route_authenticates_once_and_resolves_the_presented_credential(
    client: TestClient, harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an active version and a binding module that must not re-authenticate.
    harness.publish()

    def _second_authentication(_request: object) -> str:
        raise AssertionError("route authenticated twice")

    monkeypatch.setattr(strategy_owner_binding, "require_strategy_run_token", _second_authentication)

    # When the owner reads it.
    response = client.post(URL, json=body(), headers=AUTH)

    # Then the route resolves the owner from the credential it already checked.
    assert response.status_code == 200, response.text
    assert response.json()["owner_user_id"] == OWNER


def test_malformed_json_is_400_invalid_input(client: TestClient, harness: Harness) -> None:
    response = client.post(URL, content=b"{", headers=AUTH)
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "invalid_input"}}
    assert (harness.provider_calls, harness.lookups) == (0, 0)


_INVALID_BODIES = [
    pytest.param({"strategy_version": "v7"}, id="missing-strategy-id"),
    pytest.param({"strategy_id": "momentum"}, id="missing-version"),
    pytest.param(body(strategy_id=""), id="empty-strategy-id"),
    pytest.param(body(strategy_id="Momentum"), id="uppercase-strategy-id"),
    pytest.param(body(version="v7/escape"), id="slash-version"),
    pytest.param(body(strategy_id=123), id="non-string-id"),
    pytest.param(body(owner_user_id="attacker"), id="owner-field"),
    pytest.param(body(user_id="attacker"), id="user-field"),
    pytest.param(body(expected_source_sha256="A" * 64), id="uppercase-digest"),
    pytest.param(body(expected_source_sha256="0" * 63), id="short-digest"),
    pytest.param(body(expected_source_sha256="g" * 64), id="non-hex-digest"),
    pytest.param(body(expected_source_sha256=42), id="non-string-digest"),
]


@pytest.mark.parametrize("payload", _INVALID_BODIES)
def test_schema_violations_are_422(client, harness, payload) -> None:
    response = client.post(URL, json=payload, headers=AUTH)
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "invalid_input"}}
    assert (harness.provider_calls, harness.lookups) == (0, 0)


def test_non_object_json_is_422(client, harness) -> None:
    response = client.post(URL, json=["momentum"], headers=AUTH)
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "invalid_input"}}
    assert (harness.provider_calls, harness.lookups) == (0, 0)


def test_foreign_owner_and_absent_strategy_are_byte_identical(client: TestClient, harness: Harness) -> None:
    harness.publish()
    harness.bind("owner-b")

    foreign = client.post(URL, json=body(), headers=AUTH)
    absent = client.post(URL, json=body("absent", "v7"), headers=AUTH)

    assert foreign.status_code == absent.status_code == 404
    assert foreign.content == absent.content
    assert foreign.json() == {"error": {"code": "strategy_not_found"}}
    for leaked in (SOURCE_BYTES, base64.b64encode(SOURCE_BYTES)):
        assert leaked not in foreign.content


@pytest.mark.parametrize("state", ["missing", "pending", "tombstoned"])
def test_unreadable_versions_are_version_not_found_without_fallback(
    client: TestClient, harness: Harness, state: str
) -> None:
    if state == "missing":
        harness.publish(version="v6")
    elif state == "pending":
        harness.publish(version="v6")
        SqliteVersionRegistry(harness.db_path).register_pending(
            RegistrationKey.parse(OWNER, "momentum", "v7"),
            SourceFacts.from_bytes(SOURCE_BYTES),
        )
    else:
        key, _ = harness.publish(version="v7")
        harness.publisher.tombstone(key)

    response = client.post(URL, json=body(), headers=AUTH)
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "version_not_found"}}
    assert base64.b64encode(SOURCE_BYTES) not in response.content


def test_expected_digest_mismatch_is_409_without_payload(client: TestClient, harness: Harness) -> None:
    harness.publish()
    response = client.post(URL, json=body(expected_source_sha256="0" * 64), headers=AUTH)
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "input_digest_mismatch"}}
    assert base64.b64encode(SOURCE_BYTES) not in response.content


def test_actual_byte_drift_is_409_without_bytes_or_digests(client: TestClient, harness: Harness) -> None:
    key, _ = harness.publish()
    source_path = (
        harness.root / "strategies" / key.strategy_id / "versions" / key.strategy_version / "code" / "signal_engine.py"
    )
    source_path.write_bytes(SOURCE_BYTES + b"# drift\n")

    response = client.post(URL, json=body(), headers=AUTH)
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "input_digest_mismatch"}}
    for leaked in (
        base64.b64encode(SOURCE_BYTES),
        hashlib.sha256(SOURCE_BYTES).hexdigest().encode(),
        str(len(SOURCE_BYTES)).encode(),
    ):
        assert leaked not in response.content


@pytest.mark.parametrize(
    "failure",
    [
        OSError("cannot open /secret/strategy_store.db"),
        sqlite3.OperationalError("unable to open /secret/strategy_store.db"),
        VersionLockUnavailable("lock at /secret/strategy_store.db"),
    ],
)
def test_storage_and_lock_failures_are_typed_503_without_leakage(harness: Harness, failure: Exception) -> None:
    class _FailingReader:
        def read(self, key: RegistrationKey, expected_source_sha256: str | None = None):
            raise failure

    app = FastAPI()
    register_strategy_version_source_routes(app, lambda: _FailingReader())
    with TestClient(app) as test_client:
        response = test_client.post(URL, json=body(), headers=AUTH)

    assert response.status_code == 503
    assert response.json() == {"error": {"code": "storage_unavailable"}}
    assert "/secret" not in response.text
    assert TOKEN not in response.text


def test_provider_construction_failure_is_typed_503(harness: Harness) -> None:
    def _broken() -> object:
        raise OSError("cannot open /secret/strategy_store.db")

    app = FastAPI()
    register_strategy_version_source_routes(app, _broken)
    with TestClient(app) as test_client:
        response = test_client.post(URL, json=body(), headers=AUTH)

    assert response.status_code == 503
    assert response.json() == {"error": {"code": "storage_unavailable"}}
    assert "/secret" not in response.text


def test_route_mounts_only_post_and_rejects_other_methods(client: TestClient, harness: Harness) -> None:
    paths = client.app.openapi()["paths"]
    assert set(paths) == {URL}
    assert set(paths[URL]) == {"post"}
    assert client.get(URL, headers=AUTH).status_code == 405
    assert client.delete(URL, headers=AUTH).status_code == 405
    assert (harness.provider_calls, harness.lookups) == (0, 0)


def test_dedicated_entry_exposes_the_route_with_an_injected_provider(
    harness: Harness,
) -> None:
    _, active = harness.publish()
    app = strategy_run_server.create_app(lambda: object(), version_source_provider=harness.provider)
    with TestClient(app) as test_client:
        response = test_client.post(URL, json=body(), headers=AUTH)

    assert response.status_code == 200
    assert response.json()["version_id"] == active.version_id


def test_full_api_mounts_the_route_only_with_the_run_entry_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(strategy_surfaces, "STRATEGY_SCHEDULE_ROUTES_ENABLED", False)
    monkeypatch.setattr(strategy_surfaces, "STRATEGY_RUN_ENABLED", False)
    off = FastAPI()
    strategy_surfaces.register_strategy_surfaces(off)

    monkeypatch.setattr(strategy_surfaces, "STRATEGY_RUN_ENABLED", True)
    on = FastAPI()
    strategy_surfaces.register_strategy_surfaces(on)

    assert URL not in off.openapi()["paths"]
    assert URL in on.openapi()["paths"]


def test_default_provider_is_resolved_only_after_auth_and_binding(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api import strategy_version_source_routes as routes

    calls: list[bool] = []

    def _never() -> object:
        calls.append(True)
        raise AssertionError("provider resolved before auth/binding")

    monkeypatch.setattr(routes, "default_version_source_provider", _never)
    app = FastAPI()
    routes.register_strategy_version_source_routes(app)
    monkeypatch.delenv(BINDING_ENV, raising=False)

    with TestClient(app) as test_client:
        unauthenticated = test_client.post(URL, json=body())
        unbound = test_client.post(URL, json=body(), headers=AUTH)

    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {"error": {"code": "unauthorized"}}
    assert unbound.status_code == 403
    assert unbound.json() == {"detail": "forbidden"}
    assert calls == []


def test_failures_do_not_log_request_bodies_secrets_or_paths(
    client: TestClient, harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    responses = [
        client.post(URL, json=body("absent", "v7"), headers=AUTH),
        client.post(URL, content=b"{", headers=AUTH),
        client.post(URL, json=body(), headers={"Authorization": f"Bearer {OTHER_TOKEN}"}),
    ]
    exposed = caplog.text + "".join(response.text for response in responses)
    for sensitive in (
        TOKEN,
        OTHER_TOKEN,
        str(harness.token_path),
        str(harness.binding_path),
        str(harness.db_path),
    ):
        assert sensitive not in exposed
