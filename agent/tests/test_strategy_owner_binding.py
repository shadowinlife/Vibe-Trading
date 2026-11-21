"""Deployment owner-binding contracts for authenticated strategy APIs."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api import strategy_owner_binding
from src.api.strategy_owner_binding import require_deployment_owner

TOKEN = "synthetic-service-token-binding-5e2a"
OTHER_TOKEN = "synthetic-other-service-token-91bc"
OWNER = "synthetic-owner-17"
TOKEN_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"
BINDING_ENV = "RM_VT_STRATEGY_OWNER_BINDING_FILE"
JsonField = str | int | bool | None


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _payload(owner: str = OWNER, extra: dict[str, JsonField] | None = None) -> dict[str, JsonField]:
    payload: dict[str, JsonField] = {
        "schema_version": "strategy-owner-binding-v1",
        "owner_user_id": owner,
        "token_sha256": _digest(TOKEN),
    }
    if extra:
        payload.update(extra)
    return payload


def _write_binding(
    path: Path,
    payload: dict[str, JsonField] | None = None,
    content: bytes | None = None,
) -> Path:
    raw = content if content is not None else json.dumps(payload or _payload()).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


@pytest.fixture
def token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "service-token"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv(TOKEN_ENV, str(path))
    return path


@pytest.fixture
def binding_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "owner-binding.json"
    monkeypatch.setenv(BINDING_ENV, str(path))
    return path


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/bound-owner")
    def bound_owner(
        owner: Annotated[str, Depends(require_deployment_owner)],
    ) -> dict[str, str]:
        return {"owner_user_id": owner}

    with TestClient(app) as test_client:
        yield test_client


def _get(client: TestClient, token: str = TOKEN):
    return client.get("/bound-owner", headers={"Authorization": f"Bearer {token}"})


def _assert_forbidden(response, case: str = "") -> None:
    assert response.status_code == 403, case
    assert response.json() == {"detail": "forbidden"}


def test_valid_normalized_credential_ignores_owner_header(
    client: TestClient, token_file: Path, binding_path: Path
) -> None:
    # Given
    _write_binding(binding_path)
    # When
    response = client.get(
        "/bound-owner",
        headers={
            "Authorization": f"Bearer   {TOKEN}   ",
            "X-Owner-User-Id": "attacker",
        },
    )
    # Then
    assert response.status_code == 200
    assert response.json() == {"owner_user_id": OWNER}


def test_every_resolution_reloads_binding_without_last_known_good(
    client: TestClient, token_file: Path, binding_path: Path
) -> None:
    # Given
    _write_binding(binding_path)
    first = _get(client)
    _write_binding(binding_path, _payload(owner="synthetic-owner-18"))
    second = _get(client)
    _write_binding(binding_path, content=b"{")
    # When
    refused = _get(client)
    # Then
    assert [first.json(), second.json()] == [
        {"owner_user_id": OWNER},
        {"owner_user_id": "synthetic-owner-18"},
    ]
    _assert_forbidden(refused)


@pytest.mark.parametrize(
    "missing_environment",
    [
        pytest.param(True, id="missing-environment"),
        pytest.param(False, id="missing-file"),
    ],
)
def test_missing_binding_configuration_is_forbidden(
    client: TestClient,
    token_file: Path,
    binding_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_environment: bool,
) -> None:
    # Given
    if missing_environment:
        monkeypatch.delenv(BINDING_ENV, raising=False)
    # When / Then
    _assert_forbidden(_get(client))


@pytest.mark.parametrize(
    ("case", "content"),
    [
        ("empty", b""),
        ("whitespace", b" \r\n"),
        ("malformed", b"{"),
        ("undecodable", b"\xff"),
        ("non-object", b"[]"),
        ("unknown-field", json.dumps(_payload(extra={"unexpected": True})).encode()),
        (
            "wrong-schema-version",
            json.dumps(_payload(extra={"schema_version": "strategy-owner-binding-v2"})).encode(),
        ),
    ],
)
def test_invalid_binding_json_is_forbidden(
    client: TestClient, token_file: Path, binding_path: Path, case: str, content: bytes
) -> None:
    # Given
    _write_binding(binding_path, content=content)
    # When / Then
    _assert_forbidden(_get(client), case)


@pytest.mark.parametrize("owner", ["", "   ", "\towner", "owner\n", "owner\x00", "owner\u200b"])
def test_blank_or_control_owner_is_forbidden(
    client: TestClient, token_file: Path, binding_path: Path, owner: str
) -> None:
    # Given
    _write_binding(binding_path, _payload(owner=owner))
    # When / Then
    _assert_forbidden(_get(client))


@pytest.mark.parametrize("digest", ["", "0" * 63, "g" * 64, "A" * 64, 42, None, False])
def test_invalid_token_digest_is_forbidden(
    client: TestClient, token_file: Path, binding_path: Path, digest: JsonField
) -> None:
    # Given
    _write_binding(binding_path, _payload(extra={"token_sha256": digest}))
    # When / Then
    _assert_forbidden(_get(client))


def test_resolve_deployment_owner_uses_the_presented_credential(binding_path: Path) -> None:
    # Given a binding for the synthetic owner.
    _write_binding(binding_path)

    # When an already-authenticated credential is resolved.
    owner = strategy_owner_binding.resolve_deployment_owner(TOKEN)

    # Then the bound owner is returned and a different credential is refused.
    assert owner == OWNER
    with pytest.raises(HTTPException) as caught:
        strategy_owner_binding.resolve_deployment_owner(OTHER_TOKEN)
    assert (caught.value.status_code, caught.value.detail) == (403, "forbidden")


def test_mismatched_token_digest_is_forbidden(client: TestClient, token_file: Path, binding_path: Path) -> None:
    # Given
    _write_binding(binding_path, _payload(extra={"token_sha256": _digest(OTHER_TOKEN)}))
    # When / Then
    _assert_forbidden(_get(client))


def test_oversized_otherwise_valid_json_is_forbidden(client: TestClient, token_file: Path, binding_path: Path) -> None:
    # Given
    content = json.dumps(_payload()).encode() + b" " * 32_768
    _write_binding(binding_path, content=content)
    # When / Then
    _assert_forbidden(_get(client))


def test_non_regular_binding_file_is_forbidden(client: TestClient, token_file: Path, binding_path: Path) -> None:
    # Given
    binding_path.mkdir()
    # When / Then
    _assert_forbidden(_get(client))


def test_symlink_binding_file_is_forbidden(
    client: TestClient, token_file: Path, binding_path: Path, tmp_path: Path
) -> None:
    # Given
    target = _write_binding(tmp_path / "actual-binding.json")
    binding_path.symlink_to(target)
    # When / Then
    _assert_forbidden(_get(client))


def test_binding_file_owned_by_another_uid_is_forbidden(
    client: TestClient,
    token_file: Path,
    binding_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _write_binding(binding_path)
    monkeypatch.setattr(strategy_owner_binding.os, "getuid", lambda: binding_path.stat().st_uid + 1)
    # When / Then
    _assert_forbidden(_get(client))


@pytest.mark.parametrize("mode", [0o000, 0o400, 0o640, 0o644, 0o700])
def test_binding_file_mode_must_be_exactly_0600(
    client: TestClient, token_file: Path, binding_path: Path, mode: int
) -> None:
    # Given
    _write_binding(binding_path).chmod(mode)
    # When / Then
    _assert_forbidden(_get(client))


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [({}, 401), ({"Authorization": f"Bearer {OTHER_TOKEN}"}, 403)],
)
def test_authentication_failures_keep_0001_response_semantics(
    client: TestClient,
    token_file: Path,
    binding_path: Path,
    headers: dict[str, str],
    expected_status: int,
) -> None:
    # Given
    _write_binding(binding_path)
    # When
    response = client.get("/bound-owner", headers=headers)
    # Then
    assert response.status_code == expected_status
    assert response.json() == {"detail": "unauthorized" if expected_status == 401 else "forbidden"}


def test_invalid_binding_precedes_registry_construction_and_lookup(token_file: Path, binding_path: Path) -> None:
    # Given
    constructions = 0
    lookups = 0

    class RegistrySpy:
        def lookup(self) -> None:
            nonlocal lookups
            lookups += 1

    def provider() -> RegistrySpy:
        nonlocal constructions
        constructions += 1
        return RegistrySpy()

    app = FastAPI()

    @app.get("/lookup", dependencies=[Depends(require_deployment_owner)])
    def lookup() -> dict[str, bool]:
        provider().lookup()
        return {"found": True}

    # When
    with TestClient(app) as test_client:
        response = test_client.get("/lookup", headers={"Authorization": f"Bearer {TOKEN}"})
    # Then
    assert response.status_code == 403
    assert (constructions, lookups) == (0, 0)


def test_request_failures_do_not_leak_secrets_to_logs_or_responses(
    client: TestClient,
    token_file: Path,
    binding_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given
    caplog.set_level(logging.DEBUG)
    _write_binding(
        binding_path,
        _payload(extra={"unexpected": TOKEN}),
    )
    # When
    response = _get(client)
    # Then
    assert response.status_code == 403
    exposed = caplog.text + response.text + repr(response)
    for sensitive in (TOKEN, _digest(TOKEN), str(token_file), str(binding_path)):
        assert sensitive not in exposed
