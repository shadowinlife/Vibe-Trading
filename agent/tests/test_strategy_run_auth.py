"""Service-token contracts for the RM strategy-run entry.

Every case drives the real routes on the dedicated app so the assertion covers
the dependency wiring, not just the helper. The service is a fake: a request
that should be refused must never reach it.
"""

from __future__ import annotations

import logging
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import strategy_run_server
from src.api import strategy_run_auth

TOKEN = "dummy-service-token-auth-4c1d"
TOKEN_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"


class _FakeService:
    """Service stub that records whether any refused request reached it."""

    def __init__(self) -> None:
        self.submit_calls: list[object] = []
        self.status_calls: list[str] = []

    async def submit(self, command):  # noqa: ANN001, ANN201 — duck-typed seam
        self.submit_calls.append(command)
        return SimpleNamespace(vt_run_id="vt-auth-0001")

    def status(self, vt_run_id: str):
        self.status_calls.append(vt_run_id)
        if vt_run_id != "vt-auth-0001":
            return None
        return SimpleNamespace(status="running", error=None, error_kind=None)


def _payload() -> dict[str, object]:
    return {
        "strategy_id": "momentum-1",
        "strategy_version": "v1-aaaaaaaa",
        "run_key": "user-1:schedule-1:2026-10-26T00:00:00Z:0",
        "sandbox_id": "default--box",
    }


def _write_token(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture
def service() -> _FakeService:
    return _FakeService()


@pytest.fixture
def client(service: _FakeService):
    """Dedicated entry app with the fake service injected."""
    app = strategy_run_server.create_app(lambda: service)
    with TestClient(app) as test_client:
        yield test_client


def _use_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str = TOKEN + "\n"
) -> Path:
    path = _write_token(tmp_path / "rm_vt_strategy_run_token", content)
    monkeypatch.setenv(TOKEN_ENV, str(path))
    return path


def _post(client: TestClient, token: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post("/strategy-runs", json=_payload(), headers=headers)


def test_token_file_is_created_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _use_token(monkeypatch, tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_missing_header_is_401_and_never_reaches_service(
    client: TestClient,
    service: _FakeService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_token(monkeypatch, tmp_path)
    response = client.post("/strategy-runs", json=_payload())
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}
    assert service.submit_calls == []


def test_non_bearer_scheme_is_401(
    client: TestClient,
    service: _FakeService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_token(monkeypatch, tmp_path)
    response = client.post(
        "/strategy-runs", json=_payload(), headers={"Authorization": f"Basic {TOKEN}"}
    )
    assert response.status_code == 401
    assert service.submit_calls == []


def test_empty_bearer_credential_is_401(
    client: TestClient,
    service: _FakeService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_token(monkeypatch, tmp_path)
    response = client.post(
        "/strategy-runs", json=_payload(), headers={"Authorization": "Bearer   "}
    )
    assert response.status_code == 401
    assert service.submit_calls == []


def test_wrong_token_is_403_and_never_reaches_service(
    client: TestClient,
    service: _FakeService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_token(monkeypatch, tmp_path)
    response = _post(client, "definitely-not-the-token")
    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden"}
    assert service.submit_calls == []


def test_correct_token_passes(
    client: TestClient,
    service: _FakeService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_token(monkeypatch, tmp_path)
    response = _post(client, TOKEN)
    assert response.status_code == 202
    assert response.json() == {"vt_run_id": "vt-auth-0001"}
    assert len(service.submit_calls) == 1
    submitted = service.submit_calls[0]
    expected = _payload()
    assert submitted.strategy_id == expected["strategy_id"]
    assert submitted.strategy_version == expected["strategy_version"]
    assert submitted.run_key == expected["run_key"]
    assert submitted.sandbox_id == expected["sandbox_id"]
    assert submitted.timeout_s == 3600


def test_unset_env_is_403(
    client: TestClient, service: _FakeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    response = _post(client, TOKEN)
    assert response.status_code == 403
    assert service.submit_calls == []


def test_missing_token_file_is_403(
    client: TestClient,
    service: _FakeService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, str(tmp_path / "does-not-exist"))
    response = _post(client, TOKEN)
    assert response.status_code == 403
    assert service.submit_calls == []


def test_empty_token_file_is_403(
    client: TestClient,
    service: _FakeService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_token(monkeypatch, tmp_path, "\r\n")
    response = _post(client, TOKEN)
    assert response.status_code == 403
    assert service.submit_calls == []


def test_unreadable_token_path_is_403(
    client: TestClient,
    service: _FakeService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "token-is-a-directory"
    directory.mkdir()
    monkeypatch.setenv(TOKEN_ENV, str(directory))
    response = _post(client, TOKEN)
    assert response.status_code == 403
    assert service.submit_calls == []


def test_trailing_crlf_is_stripped(
    client: TestClient,
    service: _FakeService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_token(monkeypatch, tmp_path, TOKEN + "\r\n")
    response = _post(client, TOKEN)
    assert response.status_code == 202
    assert len(service.submit_calls) == 1


def test_get_route_shares_the_same_auth_rule(
    client: TestClient,
    service: _FakeService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_token(monkeypatch, tmp_path)
    assert client.get("/strategy-runs/vt-missing").status_code == 401
    assert (
        client.get(
            "/strategy-runs/vt-missing", headers={"Authorization": "Bearer nope"}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/strategy-runs/vt-missing", headers={"Authorization": f"Bearer {TOKEN}"}
        ).status_code
        == 404
    )
    assert service.status_calls == ["vt-missing"]


def test_token_never_appears_in_logs_or_response_bodies(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = _use_token(monkeypatch, tmp_path)
    caplog.set_level(logging.DEBUG)
    responses = [
        client.post("/strategy-runs", json=_payload()),
        _post(client, "wrong-token-value"),
        _post(client, TOKEN),
        client.get("/strategy-runs/vt-auth-0001"),
        client.get(
            "/strategy-runs/vt-auth-0001", headers={"Authorization": f"Bearer {TOKEN}"}
        ),
    ]
    strategy_run_auth.log_token_configuration()
    assert TOKEN not in caplog.text
    assert str(path) not in caplog.text
    for response in responses:
        assert TOKEN not in response.text
        assert str(path) not in response.text
