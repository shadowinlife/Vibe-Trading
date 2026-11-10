"""Frozen factor prototype HTTP contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import strategy_run_server
from src.factors.prototype import FactorPrototypeRunner, PrototypeRunRequest
from src.factors.registry import get_default_registry
from src.factors.registry_freeze import (
    TOOL_SCHEMA_VERSION,
    freeze_registry,
    rows_from_registry,
)
from src.strategy_runs import execution_start_service
from src.strategy_runs.control_contract import CONTRACT_VERSION, FactorPrototypeSpec
from src.strategy_runs.control_service import ExecutionControlService
from src.strategy_runs.control_store import ControlStore
from src.strategy_runs.execution_start_service import ExecutionStartService
from tests._factor_prototype_support import _Evaluator

from tests._factor_prototype_support import _allowed_run_root as _allowed_run_root

_HTTP_TOKEN = "factor-prototype-test-token-4c9e"
_HTTP_TOKEN_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"
_HTTP_AUTH = {"Authorization": f"Bearer {_HTTP_TOKEN}"}
_HTTP_DISPATCH_KEY = "dispatch-factor-http"
_HTTP_RESULT_FIELDS = {
    "remote_id",
    "dispatch_key",
    "registry_digest",
    "tool_schema_version",
    "contract_version",
    "status",
    "completed",
    "total",
    "candidates",
    "quality_bounds",
    "artifacts",
    "artifact_paths",
}

pytestmark = pytest.mark.usefixtures("_allowed_run_root")


class _HttpSupervisor:
    """Spawn recorder that can fail with a secret-bearing message."""

    def __init__(self, store: ControlStore) -> None:
        self._store = store
        self.calls: list[object] = []
        self.error: Exception | None = None

    def start(self, dispatch_key, worker_spec):  # noqa: ANN001, ANN201
        self.calls.append(worker_spec)
        if self.error is not None:
            raise self.error
        return self._store.get_dispatch(dispatch_key)


@pytest.fixture
def http_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    token_path = tmp_path / "factor-token"
    token_path.write_text(_HTTP_TOKEN + "\n", encoding="utf-8")
    token_path.chmod(0o600)
    monkeypatch.setenv(_HTTP_TOKEN_ENV, str(token_path))
    monkeypatch.setattr(
        execution_start_service, "get_runtime_root", lambda: tmp_path / "runtime"
    )
    store = ControlStore(tmp_path / "ledger.sqlite3")
    supervisor = _HttpSupervisor(store)
    service = ExecutionStartService(store, supervisor)
    app = strategy_run_server.create_app(
        lambda: object(),
        control_service_provider=lambda: ExecutionControlService(store),
        execution_start_service_provider=lambda: service,
    )
    with TestClient(app) as client:
        yield client, store, supervisor, tmp_path / "runtime"


def _http_spec(**changes: object) -> dict[str, object]:
    rows = rows_from_registry(get_default_registry(), ("alpha101_012",))
    spec: dict[str, object] = {
        "registry_digest": freeze_registry(rows).registry_digest,
        "candidate_ids": ["alpha101_012"],
        "symbols": ["600000.SH"],
        "start_date": "2024-01-02",
        "split_date": "2024-06-03",
        "end_date": "2024-12-31",
        "source": "alpha_zoo",
        "as_of": "2025-01-02",
        "forward_return_horizon_days": 5,
        "n_groups": 5,
    }
    spec.update(changes)
    return spec


def _http_body(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "dispatch_key": _HTTP_DISPATCH_KEY,
        "kind": "factor",
        "spec": _http_spec(),
        "contract_version": CONTRACT_VERSION,
    }
    body.update(changes)
    return body


def _post_http(  # noqa: ANN001, ANN201 — TestClient seam
    client: TestClient,
    body: dict[str, object] | None = None,
    headers: dict[str, str] = _HTTP_AUTH,
):
    return client.post(
        "/factor/prototype",
        json=body if body is not None else _http_body(),
        headers=headers,
    )


def test_factor_prototype_http_start_replay_and_bounded_result(
    http_stack,
) -> None:
    client, store, supervisor, runtime_root = http_stack

    response = _post_http(client)

    assert response.status_code == 202
    accepted = response.json()
    assert set(accepted) == {
        "remote_id",
        "dispatch_key",
        "kind",
        "status",
        "replayed",
        "contract_version",
    }
    assert accepted["dispatch_key"] == _HTTP_DISPATCH_KEY
    assert accepted["kind"] == "factor"
    assert accepted["status"] == "running"
    assert accepted["replayed"] is False
    assert accepted["contract_version"] == CONTRACT_VERSION
    assert accepted["remote_id"].startswith("factor-")
    row = store.get_dispatch(_HTTP_DISPATCH_KEY)
    assert row is not None
    assert row.status == "dispatched"
    assert len(supervisor.calls) == 1

    replay = _post_http(client)

    assert replay.status_code == 202
    assert replay.json() == {**accepted, "replayed": True}
    assert len(supervisor.calls) == 1

    run_dir = runtime_root / "runs" / accepted["remote_id"]
    spec = FactorPrototypeSpec.model_validate(_http_spec())
    result = FactorPrototypeRunner(
        registry=get_default_registry(), evaluator=_Evaluator()
    ).run(
        PrototypeRunRequest(
            spec=spec,
            run_dir=run_dir,
            remote_id=accepted["remote_id"],
            dispatch_key=_HTTP_DISPATCH_KEY,
        )
    )
    (run_dir / "result.json").write_text(json.dumps(result.document), encoding="utf-8")
    (run_dir / "progress.json").write_text(
        json.dumps(result.progress.document()), encoding="utf-8"
    )

    read = client.get(
        f"/execution-instances/{accepted['remote_id']}/result", headers=_HTTP_AUTH
    )

    assert read.status_code == 200
    payload = read.json()
    assert payload["remote_id"] == accepted["remote_id"]
    assert payload["result_status"] == "available"
    assert payload["has_more"] is False
    assert payload["contract_version"] == CONTRACT_VERSION
    document = payload["result"]
    assert set(document) == _HTTP_RESULT_FIELDS
    assert document["remote_id"] == accepted["remote_id"]
    assert document["dispatch_key"] == _HTTP_DISPATCH_KEY
    assert document["registry_digest"] == spec.registry_digest
    assert document["tool_schema_version"] == TOOL_SCHEMA_VERSION
    assert document["status"] == "completed"
    assert document["completed"] == 1
    assert document["total"] == 1
    assert isinstance(document["quality_bounds"], dict)
    assert {"selection_window", "oos_window"} <= set(document["quality_bounds"])
    assert set(document["artifacts"]) == {"ic_series", "ic_summary", "group_equity"}
    assert document["artifact_paths"]
    assert _HTTP_TOKEN not in read.text


def test_factor_prototype_http_changed_spec_conflicts_with_original_id(
    http_stack,
) -> None:
    client, store, supervisor, _ = http_stack
    first = _post_http(client)
    started = store.get_dispatch(_HTTP_DISPATCH_KEY)
    changed = _http_body()
    changed["spec"]["symbols"] = ["000001.SZ"]

    response = _post_http(client, changed)

    assert response.status_code == 409
    assert response.json() == {
        "error": "idempotency_conflict",
        "original_remote_id": first.json()["remote_id"],
        "contract_version": CONTRACT_VERSION,
    }
    assert len(supervisor.calls) == 1
    assert store.get_dispatch(_HTTP_DISPATCH_KEY) == started


def test_factor_prototype_http_spawn_failure_is_503_with_dispatched_row(
    http_stack,
) -> None:
    client, store, supervisor, _ = http_stack
    supervisor.error = RuntimeError("secret factor worker path")

    response = _post_http(client)

    assert response.status_code == 503
    assert response.json() == {
        "error": "worker_start_failed",
        "contract_version": CONTRACT_VERSION,
    }
    assert "secret factor worker path" not in response.text
    row = store.get_dispatch(_HTTP_DISPATCH_KEY)
    assert row is not None
    assert row.status == "dispatched"
    assert row.worker_pid is None and row.worker_pgid is None
    assert len(supervisor.calls) == 1


_TOO_MANY_CANDIDATES = [f"academic_{index}" for index in range(201)]
_TOO_MANY_SYMBOLS = [f"SYMBOL{index}" for index in range(501)]


def _spec_case(**changes: object):
    def mutate(body: dict[str, object]) -> None:
        body["spec"] = {**body["spec"], **changes}

    return mutate


_INVALID_CASES = [
    pytest.param(
        _spec_case(candidate_ids=["alpha101_012", "academic_missing"]),
        400,
        id="unknown-candidate",
    ),
    pytest.param(_spec_case(registry_digest="0" * 64), 409, id="digest-mismatch"),
    pytest.param(
        lambda body: body.update(run_dir="/tmp/caller-owned"),
        400,
        id="caller-run-dir",
    ),
    pytest.param(
        lambda body: body.update(worker={"argv": ["sh"]}), 400, id="worker-field"
    ),
    pytest.param(_spec_case(candidate_ids=[]), 400, id="empty-candidate-ids"),
    pytest.param(
        _spec_case(candidate_ids=_TOO_MANY_CANDIDATES),
        400,
        id="201-candidate-ids",
    ),
    pytest.param(_spec_case(symbols=[]), 400, id="empty-symbols"),
    pytest.param(_spec_case(symbols=_TOO_MANY_SYMBOLS), 400, id="501-symbols"),
    pytest.param(_spec_case(split_date="2024-01-02"), 400, id="bad-chronology"),
    pytest.param(_spec_case(as_of="2024-12-30"), 400, id="as-of-before-end"),
    pytest.param(
        _spec_case(forward_return_horizon_days=253), 400, id="horizon-over-252"
    ),
    pytest.param(_spec_case(n_groups=1), 400, id="groups-below-range"),
    pytest.param(_spec_case(n_groups=21), 400, id="groups-above-range"),
    pytest.param(_spec_case(universe=["equity_us"]), 400, id="caller-universe"),
]


@pytest.mark.parametrize(("mutate", "expected"), _INVALID_CASES)
def test_factor_prototype_http_invalid_input_has_no_side_effects(
    mutate,
    expected: int,
    http_stack,
) -> None:
    client, store, supervisor, runtime_root = http_stack
    body = _http_body()
    mutate(body)

    response = _post_http(client, body)

    assert response.status_code == expected
    expected_body = {
        "error": "input_digest_mismatch" if expected == 409 else "invalid_request",
        "contract_version": CONTRACT_VERSION,
    }
    assert response.json() == expected_body
    assert store.get_dispatch(_HTTP_DISPATCH_KEY) is None
    assert supervisor.calls == []
    assert not (runtime_root / "runs").exists()


@pytest.mark.parametrize(
    ("headers", "expected", "body"),
    [
        pytest.param({}, 401, {"detail": "unauthorized"}, id="missing"),
        pytest.param(
            {"Authorization": f"Basic {_HTTP_TOKEN}"},
            401,
            {"detail": "unauthorized"},
            id="malformed",
        ),
        pytest.param(
            {"Authorization": "Bearer wrong"}, 403, {"detail": "forbidden"}, id="wrong"
        ),
    ],
)
def test_factor_prototype_http_requires_service_token(
    headers: dict[str, str],
    expected: int,
    body: dict[str, str],
    http_stack,
) -> None:
    client, store, supervisor, runtime_root = http_stack

    response = _post_http(client, headers=headers)

    assert response.status_code == expected
    assert response.json() == body
    if expected == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    assert store.get_dispatch(_HTTP_DISPATCH_KEY) is None
    assert supervisor.calls == []
    assert not (runtime_root / "runs").exists()
