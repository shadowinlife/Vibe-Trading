"""Frozen HTTP contracts for the RM strategy-run entry routes.

The fake service owns the idempotency/status semantics; these tests assert the
wire contract around it: exact 202/409/200 bodies, framework 404/405, the 422
model boundary, auth wiring, and that the dedicated app mounts nothing else.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import strategy_run_server
from src.api.strategy_run_routes import DuplicateRunKeyError

AGENT_DIR = Path(__file__).resolve().parent.parent
TOKEN = "dummy-service-token-routes-8e2a"
TOKEN_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class _FakeService:
    """In-memory stand-in for ``src.strategy_runs.service``."""

    def __init__(self) -> None:
        self.runs: dict[str, str] = {}
        self.rows: dict[str, SimpleNamespace] = {}
        self.submits: list[object] = []
        self.status_reads: list[str] = []
        self.submit_error: BaseException | None = None
        self.status_error: BaseException | None = None

    async def submit(self, command):  # noqa: ANN001, ANN201 — duck-typed seam
        if self.submit_error is not None:
            raise self.submit_error
        existing = self.runs.get(command.run_key)
        if existing is not None:
            raise DuplicateRunKeyError(existing)
        vt_run_id = f"vt-{len(self.submits) + 1:04d}"
        self.submits.append(command)
        self.runs[command.run_key] = vt_run_id
        self.rows[vt_run_id] = SimpleNamespace(
            status="running", error=None, error_kind=None
        )
        return SimpleNamespace(vt_run_id=vt_run_id)

    def status(self, vt_run_id: str):
        if self.status_error is not None:
            raise self.status_error
        self.status_reads.append(vt_run_id)
        return self.rows.get(vt_run_id)


def _base_payload() -> dict[str, object]:
    return {
        "strategy_id": "momentum-1",
        "strategy_version": "v1-aaaaaaaa",
        "run_key": "user-1:schedule-1:2026-10-26T00:00:00Z:0",
        "sandbox_id": "default--box",
        "timeout_s": 3600,
    }


def _without(key: str) -> dict[str, object]:
    body = _base_payload()
    body.pop(key)
    return body


def _with(**overrides: object) -> dict[str, object]:
    body = _base_payload()
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def token_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "rm_vt_strategy_run_token"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv(TOKEN_ENV, str(path))
    return path


@pytest.fixture
def service() -> _FakeService:
    return _FakeService()


@pytest.fixture
def client(service: _FakeService, token_env: Path):
    app = strategy_run_server.create_app(lambda: service)
    with TestClient(app) as test_client:
        yield test_client


def _create(client: TestClient, body: dict[str, object] | None = None) -> str:
    response = client.post("/strategy-runs", json=body or _base_payload(), headers=AUTH)
    assert response.status_code == 202, response.text
    return response.json()["vt_run_id"]


def test_post_returns_exact_202_body(client: TestClient) -> None:
    response = client.post("/strategy-runs", json=_base_payload(), headers=AUTH)
    assert response.status_code == 202
    assert response.json() == {"vt_run_id": "vt-0001"}
    assert set(response.json()) == {"vt_run_id"}


def test_duplicate_run_key_returns_exact_top_level_409(
    client: TestClient, service: _FakeService
) -> None:
    first = _create(client)
    changed_body = _with(timeout_s=120, upstream="https://box.example:8000")
    changed_body.pop("sandbox_id")
    second = client.post("/strategy-runs", json=changed_body, headers=AUTH)
    assert second.status_code == 409
    assert second.json() == {"error": "duplicate_run_key", "vt_run_id": first}
    assert "detail" not in second.json()
    assert len(service.submits) == 1


def test_get_running_returns_only_the_three_frozen_fields(client: TestClient) -> None:
    vt_run_id = _create(client)
    response = client.get(f"/strategy-runs/{vt_run_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"status": "running", "error": None, "error_kind": None}
    assert set(response.json()) == {"status", "error", "error_kind"}


def test_terminal_status_is_stable_across_repeated_gets(
    client: TestClient, service: _FakeService
) -> None:
    vt_run_id = _create(client)
    service.rows[vt_run_id] = SimpleNamespace(
        status="completed", error=None, error_kind=None
    )
    first = client.get(f"/strategy-runs/{vt_run_id}", headers=AUTH)
    second = client.get(f"/strategy-runs/{vt_run_id}", headers=AUTH)
    assert first.status_code == second.status_code == 200
    assert (
        first.json()
        == second.json()
        == {"status": "completed", "error": None, "error_kind": None}
    )


def test_failed_status_carries_error_and_kind(
    client: TestClient, service: _FakeService
) -> None:
    vt_run_id = _create(client)
    service.rows[vt_run_id] = SimpleNamespace(
        status="failed",
        error="strategy version missing",
        error_kind="version_not_found",
    )
    response = client.get(f"/strategy-runs/{vt_run_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "status": "failed",
        "error": "strategy version missing",
        "error_kind": "version_not_found",
    }


def test_unknown_vt_run_id_is_404(client: TestClient, service: _FakeService) -> None:
    response = client.get("/strategy-runs/vt-unknown", headers=AUTH)
    assert response.status_code == 404
    assert service.status_reads == ["vt-unknown"]


def test_unregistered_path_is_404(client: TestClient) -> None:
    assert client.get("/strategy-runs/vt-0001/cancel", headers=AUTH).status_code == 404
    assert client.get("/not-a-route", headers=AUTH).status_code == 404


def test_wrong_method_is_405(client: TestClient) -> None:
    vt_run_id = _create(client)
    assert client.get("/strategy-runs", headers=AUTH).status_code == 405
    assert client.put(f"/strategy-runs/{vt_run_id}", headers=AUTH).status_code == 405
    assert client.delete(f"/strategy-runs/{vt_run_id}", headers=AUTH).status_code == 405


_INVALID_BODIES = [
    pytest.param(lambda: _without("strategy_id"), id="missing-strategy-id"),
    pytest.param(lambda: _without("strategy_version"), id="missing-strategy-version"),
    pytest.param(lambda: _without("run_key"), id="missing-run-key"),
    pytest.param(lambda: _with(strategy_id=""), id="empty-strategy-id"),
    pytest.param(lambda: _with(strategy_id="Momentum-1"), id="uppercase-strategy-id"),
    pytest.param(lambda: _with(strategy_version="v1/escape"), id="slash-version"),
    pytest.param(lambda: _with(run_key=""), id="empty-run-key"),
    pytest.param(lambda: _with(run_key="k" * 513), id="oversized-run-key"),
    pytest.param(lambda: _with(sandbox_id=None, upstream=None), id="no-target"),
    pytest.param(lambda: _with(sandbox_id=""), id="empty-sandbox-id"),
    pytest.param(lambda: _with(sandbox_id="   "), id="blank-sandbox-id"),
    pytest.param(
        lambda: _with(sandbox_id=None, upstream="ftp://box.example"),
        id="non-http-upstream",
    ),
    pytest.param(
        lambda: _with(sandbox_id=None, upstream="http://user:secret@box.example"),
        id="upstream-userinfo",
    ),
    pytest.param(
        lambda: _with(sandbox_id=None, upstream="http://"), id="upstream-without-host"
    ),
    pytest.param(lambda: _with(timeout_s=59), id="timeout-below-range"),
    pytest.param(lambda: _with(timeout_s=86401), id="timeout-above-range"),
    pytest.param(lambda: _with(timeout_s="soon"), id="timeout-not-int"),
    pytest.param(lambda: _with(extra_field="nope"), id="extra-field"),
]


@pytest.mark.parametrize("mutator", _INVALID_BODIES)
def test_model_violation_is_422(
    client: TestClient, service: _FakeService, mutator
) -> None:
    response = client.post("/strategy-runs", json=mutator(), headers=AUTH)
    assert response.status_code == 422
    assert service.submits == []


def test_unexpected_runner_state_cannot_escape(
    client: TestClient, service: _FakeService
) -> None:
    vt_run_id = _create(client)
    service.rows[vt_run_id] = SimpleNamespace(
        status="skipped", error=None, error_kind=None
    )
    response = client.get(f"/strategy-runs/{vt_run_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "status": "failed",
        "error": "strategy run failed",
        "error_kind": "runner_error",
    }


def test_failed_row_without_text_still_has_both_fields(
    client: TestClient, service: _FakeService
) -> None:
    vt_run_id = _create(client)
    service.rows[vt_run_id] = SimpleNamespace(
        status="failed", error="", error_kind=None
    )
    response = client.get(f"/strategy-runs/{vt_run_id}", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"].strip()
    assert body["error_kind"].strip()


def test_service_construction_failure_is_503_entry_unavailable(
    service: _FakeService,
) -> None:
    def _unavailable() -> None:
        raise RuntimeError("database path not available")

    app = strategy_run_server.create_app(_unavailable)
    with TestClient(app) as test_client:
        post = test_client.post("/strategy-runs", json=_base_payload(), headers=AUTH)
        get = test_client.get("/strategy-runs/vt-0001", headers=AUTH)
    for response in (post, get):
        assert response.status_code == 503
        assert response.json() == {"detail": "entry unavailable"}
        assert "database path" not in response.text


def test_service_errors_are_entry_faults(
    client: TestClient, service: _FakeService
) -> None:
    service.submit_error = RuntimeError("boom")
    service.status_error = RuntimeError("boom")
    assert (
        client.post("/strategy-runs", json=_base_payload(), headers=AUTH).status_code
        == 503
    )
    assert client.get("/strategy-runs/vt-0001", headers=AUTH).status_code == 503


def test_dedicated_app_mounts_only_health_and_the_two_service_routes(
    service: _FakeService,
) -> None:
    app = strategy_run_server.create_app(lambda: service)
    paths = set(app.openapi()["paths"])
    assert paths == {"/strategy-runs", "/strategy-runs/{vt_run_id}"}
    assert not any(path.endswith("{path:path}") for path in paths)


def _run_python(
    script: str, workdir: Path, **extra_env: str
) -> subprocess.CompletedProcess:
    home = workdir / "home"
    cwd = workdir / "cwd"
    home.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "PYTHONPATH": str(AGENT_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_import_has_no_filesystem_side_effects(tmp_path: Path) -> None:
    result = _run_python("import src.api.strategy_run_routes", tmp_path / "hygiene")
    assert result.returncode == 0, result.stderr
    assert list((tmp_path / "hygiene" / "cwd").iterdir()) == []
    assert list((tmp_path / "hygiene" / "home").rglob("*")) == []


def test_entry_module_import_starts_no_threads_and_touches_no_files(
    tmp_path: Path,
) -> None:
    script = (
        "import threading; "
        "import strategy_run_server; "
        "print(','.join(sorted(t.name for t in threading.enumerate())))"
    )
    result = _run_python(script, tmp_path / "entry-import")
    assert result.returncode == 0, result.stderr
    assert list((tmp_path / "entry-import" / "cwd").iterdir()) == []
    assert list((tmp_path / "entry-import" / "home").rglob("*")) == []
    assert result.stdout.strip() == "MainThread"


def test_api_server_registration_is_gated(tmp_path: Path) -> None:
    script = (
        "import api_server; "
        "print('/strategy-runs' in api_server.app.openapi()['paths'])"
    )
    off = _run_python(script, tmp_path / "gated-off")
    on = _run_python(script, tmp_path / "gated-on", VT_STRATEGY_RUN_ENABLED="1")
    assert off.returncode == 0, off.stderr
    assert on.returncode == 0, on.stderr
    assert off.stdout.strip() == "False"
    assert on.stdout.strip() == "True"
    assert TOKEN not in off.stdout + off.stderr + on.stdout + on.stderr


def test_api_server_opt_in_lifespan_starts_and_stops_the_service(
    tmp_path: Path,
) -> None:
    script = (
        "import asyncio, api_server\n"
        "calls = []\n"
        "class _Service:\n"
        "    async def start(self): calls.append('start')\n"
        "    async def stop(self): calls.append('stop')\n"
        "api_server._build_strategy_run_service = lambda: _Service()\n"
        "asyncio.run(api_server._start_strategy_run_service())\n"
        "asyncio.run(api_server._stop_strategy_run_service())\n"
        "print(calls, api_server._strategy_run_service)"
    )
    on = _run_python(script, tmp_path / "lifespan-on", VT_STRATEGY_RUN_ENABLED="1")
    off = _run_python(script, tmp_path / "lifespan-off")
    assert on.returncode == 0, on.stderr
    assert off.returncode == 0, off.stderr
    assert on.stdout.strip() == "['start', 'stop'] None"
    assert off.stdout.strip() == "[] None"
