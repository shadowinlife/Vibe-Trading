"""Authenticated HTTP contracts for original execution-instance reads.

# noqa: SIZE_OK - one focused route matrix; splitting would obscure the wire contract.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import strategy_run_server
from src.factors.registry_freeze import TOOL_SCHEMA_VERSION
from src.strategy_runs.control_contract import CONTRACT_VERSION, ERROR_KINDS
from src.strategy_runs.control_service import (
    ControlProjectionError,
    ExecutionControlService,
    FindProjection,
    ResultProjection,
    StatusProjection,
)
from src.strategy_runs.control_store import ControlStore, DispatchRow
from src.strategy_runs.store import RunRow

TOKEN = "dummy-service-token-execution-routes-3b"
TOKEN_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
READ_PATHS = (
    "/execution-instances?dispatch_key=dispatch-1",
    "/execution-instances/remote-1",
    "/execution-instances/remote-1/result",
)


class _LegacyService:
    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}

    def status(self, vt_run_id: str) -> SimpleNamespace | None:
        return self.rows.get(vt_run_id)


class _ErrorService:
    def __init__(self, kind: str) -> None:
        self._error = ControlProjectionError(kind, "secret SQL /token/path detail")

    def find(self, dispatch_key: str) -> FindProjection:
        raise self._error

    def status(self, remote_id: str) -> StatusProjection:
        raise self._error

    def result(self, remote_id: str) -> ResultProjection:
        raise self._error


@pytest.fixture(autouse=True)
def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = tmp_path / "rm_vt_strategy_run_token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    token.chmod(0o600)
    monkeypatch.setenv(TOKEN_ENV, str(token))
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))


@pytest.fixture
def store(tmp_path: Path) -> ControlStore:
    return ControlStore(tmp_path / "ledger.sqlite3")


@pytest.fixture
def live_handles() -> set[str]:
    return set()


@pytest.fixture
def control_service(
    store: ControlStore, live_handles: set[str]
) -> ExecutionControlService:
    return ExecutionControlService(
        store,
        legacy_store=store,
        handle_provider=lambda remote_id: remote_id in live_handles,
    )


@pytest.fixture
def legacy_service() -> _LegacyService:
    return _LegacyService()


@pytest.fixture
def client(
    control_service: ExecutionControlService, legacy_service: _LegacyService
) -> Iterator[TestClient]:
    app = strategy_run_server.create_app(
        lambda: legacy_service, control_service_provider=lambda: control_service
    )
    with TestClient(app) as test_client:
        yield test_client


def _dispatch(
    store: ControlStore,
    key: str = "dispatch-1",
    remote_id: str = "remote-1",
    run_dir: Path | None = None,
) -> DispatchRow:
    declaration = {"run_dir": str(run_dir)} if run_dir is not None else {}
    return store.claim_dispatch(
        key, "backtest", declaration, remote_id, CONTRACT_VERSION, now_ms=1_000
    ).row


def _legacy(store: ControlStore, key: str = "legacy-1") -> RunRow:
    return store.claim(key, "strategy", "v1", None, None, 60, now_ms=1_000).row


def _finish(
    store: ControlStore,
    row: DispatchRow,
    status: str,
    result_path: Path | None = None,
    error_kind: str | None = None,
) -> None:
    store.record_worker(row.dispatch_key, 101, 101, {"pid": 101}, now_ms=2_000)
    store.mark_dispatch_terminal(
        row.dispatch_key,
        status,
        error_kind=error_kind,
        result_path=str(result_path) if result_path is not None else None,
        now_ms=3_000,
    )


def test_find_not_dispatched_has_exact_body(client: TestClient) -> None:
    response = client.get(
        "/execution-instances", params={"dispatch_key": "missing"}, headers=AUTH
    )
    assert response.status_code == 200
    assert response.json() == {
        "state": "not_dispatched",
        "dispatch_key": "missing",
        "contract_version": CONTRACT_VERSION,
    }


def test_find_dispatched_has_exact_body(
    client: TestClient, store: ControlStore, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    row = _dispatch(store, run_dir=run_dir)
    _finish(store, row, "completed")
    response = client.get(
        "/execution-instances", params={"dispatch_key": row.dispatch_key}, headers=AUTH
    )
    assert response.status_code == 200
    assert response.json() == {
        "state": "dispatched",
        "dispatch_key": row.dispatch_key,
        "remote_id": row.remote_id,
        "status": "completed",
        "run_dir": str(run_dir),
        "started_at": "1970-01-01T00:00:02.000Z",
        "finished_at": "1970-01-01T00:00:03.000Z",
        "error_kind": None,
        "contract_version": CONTRACT_VERSION,
    }


def test_find_absent_has_exact_body(client: TestClient, store: ControlStore) -> None:
    row = _dispatch(store)
    _finish(store, row, "cancelled")
    store.begin_stop(
        row.dispatch_key, row.remote_id, "stop-1", 4_000, CONTRACT_VERSION, now_ms=3_500
    )
    store.resolve_stop("stop-1", "confirmed", "terminal_record", {}, 5_000)
    response = client.get(
        "/execution-instances", params={"dispatch_key": row.dispatch_key}, headers=AUTH
    )
    assert response.status_code == 200
    assert response.json() == {
        "state": "absent",
        "dispatch_key": row.dispatch_key,
        "remote_id": row.remote_id,
        "last_status": "cancelled",
        "absence_evidence": "terminal_record",
        "observed_at": "1970-01-01T00:00:05.000Z",
        "contract_version": CONTRACT_VERSION,
    }


def test_find_legacy_run_key_is_dispatched(
    client: TestClient, store: ControlStore
) -> None:
    row = _legacy(store)
    store.mark_terminal(row.vt_run_id, "completed", now_ms=3_000)
    response = client.get(
        "/execution-instances", params={"dispatch_key": row.run_key}, headers=AUTH
    )
    assert response.status_code == 200
    assert response.json() == {
        "state": "dispatched",
        "dispatch_key": row.run_key,
        "remote_id": row.vt_run_id,
        "status": "completed",
        "run_dir": None,
        "started_at": "1970-01-01T00:00:01.000Z",
        "finished_at": "1970-01-01T00:00:03.000Z",
        "error_kind": None,
        "contract_version": CONTRACT_VERSION,
    }


@pytest.mark.parametrize(
    ("scenario", "error_kind"),
    [("running", None), ("completed", None), ("cancelled", None), ("unknown", None)]
    + [("failed", kind) for kind in ERROR_KINDS],
)
def test_status_covers_all_states_and_failure_kinds(
    client: TestClient,
    store: ControlStore,
    live_handles: set[str],
    scenario: str,
    error_kind: str | None,
) -> None:
    row = _dispatch(store, run_dir=Path("/server/run"))
    if scenario == "running":
        store.record_worker(row.dispatch_key, 101, 101, {}, now_ms=2_000)
        live_handles.add(row.remote_id)
    elif scenario != "unknown":
        _finish(store, row, scenario, error_kind=error_kind)
    terminal = scenario in {"completed", "failed", "cancelled"}
    response = client.get(f"/execution-instances/{row.remote_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "remote_id": row.remote_id,
        "dispatch_key": row.dispatch_key,
        "status": scenario,
        "started_at": None if scenario == "unknown" else "1970-01-01T00:00:02.000Z",
        "finished_at": "1970-01-01T00:00:03.000Z" if terminal else None,
        "error_kind": error_kind,
        "run_dir": "/server/run",
        "contract_version": CONTRACT_VERSION,
    }


def test_unobservable_legacy_running_is_unknown(
    client: TestClient, store: ControlStore
) -> None:
    row = _legacy(store)
    response = client.get(f"/execution-instances/{row.vt_run_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
    assert response.json()["remote_id"] == row.vt_run_id


@pytest.mark.parametrize("suffix", ["", "/result"])
def test_unknown_remote_id_is_fixed_404(client: TestClient, suffix: str) -> None:
    response = client.get(f"/execution-instances/unknown{suffix}", headers=AUTH)
    assert response.status_code == 404
    assert response.json() == {
        "error": "not_found",
        "contract_version": CONTRACT_VERSION,
    }


def test_result_available_has_exact_body(
    client: TestClient, store: ControlStore, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result_path = run_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "result": {"summary": {"metrics": {"sharpe": 1.5}}},
                "progress": {"completed": 1, "total": 1},
                "has_more": True,
            }
        ),
        encoding="utf-8",
    )
    row = _dispatch(store, run_dir=run_dir)
    _finish(store, row, "completed", result_path)
    response = client.get(f"/execution-instances/{row.remote_id}/result", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "remote_id": row.remote_id,
        "status": "completed",
        "result_status": "available",
        "result": {"summary": {"metrics": {"sharpe": 1.5}}},
        "progress": {"completed": 1, "total": 1},
        "has_more": False,
        "contract_version": CONTRACT_VERSION,
    }


@pytest.mark.parametrize("content", [None, "{", " " * (50 * 1024 + 1)])
def test_result_unavailable_has_exact_body(
    client: TestClient, store: ControlStore, tmp_path: Path, content: str | None
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result_path = run_dir / "result.json"
    if content is not None:
        result_path.write_text(content, encoding="utf-8")
    row = _dispatch(store, run_dir=run_dir)
    _finish(store, row, "completed", result_path)
    response = client.get(f"/execution-instances/{row.remote_id}/result", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "remote_id": row.remote_id,
        "status": "completed",
        "result_status": "unavailable",
        "result": None,
        "progress": None,
        "has_more": False,
        "contract_version": CONTRACT_VERSION,
    }


@pytest.mark.parametrize(
    ("batch_status", "expected"), [("cancelled", "cancelled"), ("partial", "unknown")]
)
def test_factor_reads_expose_batch_status(
    client: TestClient,
    store: ControlStore,
    tmp_path: Path,
    batch_status: str,
    expected: str,
) -> None:
    registry_digest = "a" * 64
    run_dir = tmp_path / f"factor-{batch_status}"
    run_dir.mkdir()
    remote_id = f"factor-{batch_status}"
    row = store.claim_dispatch(
        f"factor-{batch_status}-key",
        "factor",
        {
            "run_dir": str(run_dir),
            "start": {"spec": {"registry_digest": registry_digest}},
        },
        remote_id,
        CONTRACT_VERSION,
        now_ms=1_000,
    ).row
    completed = 1 if batch_status == "partial" else 0
    document = {
        "remote_id": row.remote_id,
        "dispatch_key": row.dispatch_key,
        "registry_digest": registry_digest,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "status": batch_status,
        "completed": completed,
        "total": 2,
        "candidates": [
            {
                "candidate_id": f"candidate_{index}",
                "status": "completed",
                "reasons": [],
                "metrics": {},
                "artifacts": {},
            }
            for index in range(completed)
        ],
        "quality_bounds": {},
        "artifacts": {"ic_series": None, "ic_summary": None, "group_equity": None},
        "artifact_paths": [],
    }
    result_path = run_dir / "result.json"
    result_path.write_text(json.dumps(document), encoding="utf-8")
    _finish(store, row, "completed", result_path)

    status_body = client.get(f"/execution-instances/{remote_id}", headers=AUTH).json()
    result_body = client.get(
        f"/execution-instances/{remote_id}/result", headers=AUTH
    ).json()

    assert set(status_body) == {
        "remote_id",
        "dispatch_key",
        "status",
        "started_at",
        "finished_at",
        "error_kind",
        "run_dir",
        "batch_status",
        "contract_version",
    }
    assert status_body["status"] == expected
    assert status_body["batch_status"] == batch_status
    assert set(result_body) == {
        "remote_id",
        "status",
        "result_status",
        "result",
        "progress",
        "has_more",
        "batch_status",
        "contract_version",
    }
    assert result_body["status"] == expected
    assert result_body["batch_status"] == batch_status
    assert result_body["result"] == document


def test_result_path_escape_is_rejected_without_disclosure(
    client: TestClient, store: ControlStore, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    foreign = tmp_path / "foreign.json"
    foreign.write_text('{"secret":"do-not-read"}', encoding="utf-8")
    row = _dispatch(store, run_dir=run_dir)
    _finish(store, row, "completed", run_dir / ".." / foreign.name)
    response = client.get(f"/execution-instances/{row.remote_id}/result", headers=AUTH)
    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_request",
        "contract_version": CONTRACT_VERSION,
    }
    assert str(foreign) not in response.text
    assert "do-not-read" not in response.text


def test_invalid_dispatch_key_is_fixed_400(client: TestClient) -> None:
    response = client.get(
        "/execution-instances", params={"dispatch_key": "../escape"}, headers=AUTH
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_request",
        "contract_version": CONTRACT_VERSION,
    }


@pytest.mark.parametrize("path", READ_PATHS)
@pytest.mark.parametrize(
    ("kind", "expected_status"),
    [
        ("not_found", 404),
        ("identity_conflict", 503),
        ("storage_unavailable", 503),
        ("invalid_request", 400),
    ],
)
def test_typed_errors_have_fixed_bodies_without_disclosure(
    legacy_service: _LegacyService, path: str, kind: str, expected_status: int
) -> None:
    service = _ErrorService(kind)
    app = strategy_run_server.create_app(
        lambda: legacy_service, control_service_provider=lambda: service
    )
    with TestClient(app) as client:
        response = client.get(path, headers=AUTH)
    assert response.status_code == expected_status
    assert response.json() == {"error": kind, "contract_version": CONTRACT_VERSION}
    assert "secret" not in response.text


@pytest.mark.parametrize("path", READ_PATHS)
def test_provider_failure_is_per_request_503(
    legacy_service: _LegacyService, path: str
) -> None:
    def _unavailable() -> ExecutionControlService:
        raise RuntimeError("secret database path")

    app = strategy_run_server.create_app(
        lambda: legacy_service, control_service_provider=_unavailable
    )
    with TestClient(app) as client:
        response = client.get(path, headers=AUTH)
        second = client.get(path, headers=AUTH)
    assert response.status_code == second.status_code == 503
    assert (
        response.json()
        == second.json()
        == {"error": "storage_unavailable", "contract_version": CONTRACT_VERSION}
    )
    assert "secret" not in response.text


@pytest.mark.parametrize("path", READ_PATHS)
@pytest.mark.parametrize(
    ("headers", "expected_status", "body"),
    [
        ({}, 401, {"detail": "unauthorized"}),
        ({"Authorization": f"Basic {TOKEN}"}, 401, {"detail": "unauthorized"}),
        ({"Authorization": "Bearer wrong"}, 403, {"detail": "forbidden"}),
    ],
)
def test_every_route_uses_strategy_run_auth(
    client: TestClient,
    path: str,
    headers: dict[str, str],
    expected_status: int,
    body: dict[str, str],
) -> None:
    response = client.get(path, headers=headers)
    assert response.status_code == expected_status
    assert response.json() == body


@pytest.mark.parametrize("path", READ_PATHS)
def test_unusable_token_configuration_is_403(
    client: TestClient, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(TOKEN_ENV)
    response = client.get(path, headers=AUTH)
    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden"}


def test_legacy_status_body_is_unchanged(
    client: TestClient, legacy_service: _LegacyService
) -> None:
    legacy_service.rows["vt-legacy"] = SimpleNamespace(
        status="failed", error="legacy failure", error_kind="runner_error"
    )
    response = client.get("/strategy-runs/vt-legacy", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "status": "failed",
        "error": "legacy failure",
        "error_kind": "runner_error",
    }


def test_default_start_and_stop_providers_share_one_supervisor(
    legacy_service: _LegacyService, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.strategy_runs.control_store as control_store_module
    import src.strategy_runs.native_supervisor as native_module

    created: list[object] = []

    class _Store:
        pass

    class _Supervisor:
        def __init__(self, store: object) -> None:
            self.store = store
            created.append(self)

    monkeypatch.setattr(control_store_module, "ControlStore", _Store)
    monkeypatch.setattr(native_module, "NativeSupervisor", _Supervisor)
    app = strategy_run_server.create_app(lambda: legacy_service)

    start = app.state.execution_start_service_provider()
    stop = app.state.execution_stop_service_provider()
    again = app.state.execution_start_service_provider()

    assert start._supervisor is stop._supervisor is again._supervisor
    assert len(created) == 1
