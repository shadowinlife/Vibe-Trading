"""Backtest starts bind one declaration to one native worker."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    backtest_primitive_prompt,
)
from src.strategy_runs.control_store import ControlStore, ControlStoreError
from src.strategy_runs.execution_start_service import ExecutionStartService
from tests._execution_control_support import (
    _DISPATCH_KEY,
    _RUN_ID,
    _FakeSupervisor,
    _assert_no_side_effects,
    _post,
    _prepare,
    _request,
)

from tests._execution_control_support import (
    allow_temporary_run_root as allow_temporary_run_root,
    client as client,
    run_dir as run_dir,
    start_service as start_service,
    store as store,
    supervisor as supervisor,
    token_env as token_env,
)

pytestmark = pytest.mark.usefixtures("token_env", "allow_temporary_run_root")


def test_primitive_prompt_is_exact_and_uses_python_repr() -> None:
    # Given a frozen path containing a quote
    run_dir = "/tmp/mcp-long-quoted-'-1"

    # When the fixed primitive prompt is built
    prompt = backtest_primitive_prompt(run_dir)

    # Then it contains only the in-box invocation and no edit instruction
    assert prompt == (
        "Run the in-box backtest on this frozen directory:\n"
        f"backtest(run_dir={run_dir!r})\n"
        "Do not modify, rewrite, or regenerate config.json or code/signal_engine.py."
    )


def test_missing_prepare_returns_409_without_spawning(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    run_dir: Path,
) -> None:
    # Given no prepared dispatch
    response = _post(client, run_dir)

    # When the start is rejected Then no dispatch or worker is invented
    assert response.status_code == 409
    assert response.json() == {
        "error": "prepare_required",
        "contract_version": CONTRACT_VERSION,
    }
    assert store.get_dispatch(_DISPATCH_KEY) is None
    assert supervisor.calls == []


def test_non_backtest_dispatch_requires_a_new_prepare(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    run_dir: Path,
) -> None:
    # Given a prepared dispatch of another kind
    before = _prepare(store, run_dir, kind="factor")

    # When a backtest start names it
    response = _post(client, run_dir)

    # Then the original row is untouched and no worker starts
    assert response.status_code == 409
    assert response.json() == {
        "error": "prepare_required",
        "contract_version": CONTRACT_VERSION,
    }
    _assert_no_side_effects(store, before, supervisor, run_dir)


@pytest.mark.parametrize(
    "field",
    ["run_id", "run_dir", "input_digest", "config_sha256", "source_sha256"],
)
def test_prepared_binding_mismatch_conflicts_without_side_effects(
    field: str,
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    run_dir: Path,
) -> None:
    # Given a prepared row and one changed bound field
    before = _prepare(store, run_dir)
    changed = {
        "run_id": "mcp-long-other-2",
        "run_dir": str(run_dir.with_name("mcp-long-other-2")),
        "input_digest": "d" * 64,
        "config_sha256": "e" * 64,
        "source_sha256": "f" * 64,
    }[field]

    # When the mismatched start arrives
    response = _post(client, run_dir, **{field: changed})

    # Then it names the original id and cannot claim or spawn anything
    assert response.status_code == 409
    assert response.json() == {
        "error": "idempotency_conflict",
        "original_remote_id": _RUN_ID,
        "contract_version": CONTRACT_VERSION,
    }
    _assert_no_side_effects(store, before, supervisor, run_dir)


def test_wrong_prompt_is_400_without_side_effects(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    run_dir: Path,
) -> None:
    # Given a prepared row and a prompt that could regenerate frozen files
    before = _prepare(store, run_dir)

    # When the start uses any prompt other than the fixed primitive
    response = _post(client, run_dir, prompt="rewrite config.json and run anything")

    # Then validation stops before the start declaration is stored
    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_request",
        "contract_version": CONTRACT_VERSION,
    }
    _assert_no_side_effects(store, before, supervisor, run_dir)


@pytest.mark.parametrize("violation", ["fingerprint", "extra", "kind"])
def test_invalid_start_wire_body_is_400_without_side_effects(
    violation: str,
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    run_dir: Path,
) -> None:
    # Given a prepared row and one malformed wire declaration
    before = _prepare(store, run_dir)
    changes = {
        "fingerprint": {"build_fingerprint": "not-a-sha256"},
        "extra": {"unexpected": "not accepted"},
        "kind": {"kind": "factor"},
    }[violation]

    # When the route parses the body strictly as BacktestStartRequest
    response = _post(client, run_dir, **changes)

    # Then framework details never leak and execution has no side effects
    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_request",
        "contract_version": CONTRACT_VERSION,
    }
    _assert_no_side_effects(store, before, supervisor, run_dir)


def test_identical_start_replays_and_spawns_exactly_once(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    run_dir: Path,
) -> None:
    # Given one accepted start
    _prepare(store, run_dir)
    first = _post(client, run_dir)
    started = store.get_dispatch(_DISPATCH_KEY)
    expected = {
        "remote_id": _RUN_ID,
        "dispatch_key": _DISPATCH_KEY,
        "kind": "backtest",
        "status": "running",
        "replayed": False,
        "contract_version": CONTRACT_VERSION,
    }
    assert first.status_code == 202
    assert first.json() == expected

    # When the identical declaration is replayed
    replay = _post(client, run_dir)

    # Then only the replay bit changes and no second worker or write occurs
    assert replay.status_code == 202
    assert replay.json() == {**expected, "replayed": True}
    assert len(supervisor.calls) == 1
    assert supervisor.calls[0].run_dir == run_dir.resolve()
    assert supervisor.calls[0].timeout_s == 3600
    assert store.get_dispatch(_DISPATCH_KEY) == started


def test_changed_start_declaration_conflicts_without_respawning(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    run_dir: Path,
) -> None:
    # Given an accepted start declaration
    before = _prepare(store, run_dir)
    assert _post(client, run_dir).status_code == 202
    started = store.get_dispatch(_DISPATCH_KEY)
    assert started is not None and started != before

    # When a valid but different build fingerprint reuses the dispatch key
    response = _post(client, run_dir, build_fingerprint="e" * 64)

    # Then the original start remains authoritative and only one spawn happened
    assert response.status_code == 409
    assert response.json() == {
        "error": "idempotency_conflict",
        "original_remote_id": _RUN_ID,
        "contract_version": CONTRACT_VERSION,
    }
    assert store.get_dispatch(_DISPATCH_KEY) == started
    assert len(supervisor.calls) == 1


def test_sixteen_concurrent_identical_starts_spawn_once(
    store: ControlStore,
    start_service: ExecutionStartService,
    supervisor: _FakeSupervisor,
    run_dir: Path,
) -> None:
    # Given sixteen callers sharing one prepared dispatch and start declaration
    _prepare(store, run_dir)
    request = _request(run_dir)
    barrier = threading.Barrier(16, timeout=10)

    def attempt(index: int):
        barrier.wait()
        return index, start_service.start(request)

    # When all callers race through the atomic start claim
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(attempt, range(16)))

    # Then one result is new, fifteen replay, and exactly one worker was requested
    new_claims = [index for index, result in results if not result.replayed]
    assert len(new_claims) == 1
    assert sum(result.replayed for _, result in results) == 15
    assert len(supervisor.calls) == 1


def test_spawn_failure_is_fixed_503_and_leaves_dispatched_row(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    run_dir: Path,
) -> None:
    # Given a valid new claim whose native spawn fails with sensitive detail
    _prepare(store, run_dir)
    supervisor.error = RuntimeError("secret executable path")

    # When the supervisor cannot start the worker
    response = _post(client, run_dir)

    # Then the durable row stays dispatched/unknown, never terminal or absent
    assert response.status_code == 503
    assert response.json() == {
        "error": "worker_start_failed",
        "contract_version": CONTRACT_VERSION,
    }
    assert "secret executable path" not in response.text
    row = store.get_dispatch(_DISPATCH_KEY)
    assert row is not None
    assert row.status == "dispatched"
    assert row.start_digest is not None
    assert row.worker_pid is None and row.worker_pgid is None
    assert len(supervisor.calls) == 1


def test_storage_fault_is_fixed_503_without_detail_leakage(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    run_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a prepared row whose subsequent lookup raises a storage fault
    before = _prepare(store, run_dir)

    def unavailable(dispatch_key: str) -> None:
        raise ControlStoreError("get_dispatch", "secret SQL and database path")

    monkeypatch.setattr(store, "get_dispatch", unavailable)

    # When the service translates the storage boundary
    response = _post(client, run_dir)

    # Then no internal text or spawn escapes
    assert response.status_code == 503
    assert response.json() == {
        "error": "storage_unavailable",
        "contract_version": CONTRACT_VERSION,
    }
    assert "secret SQL" not in response.text
    assert supervisor.calls == []
    assert before.start_digest is None


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        pytest.param({}, 401, id="missing"),
        pytest.param({"Authorization": "Bearer wrong"}, 403, id="wrong"),
    ],
)
def test_start_requires_service_token(
    headers: dict[str, str],
    expected: int,
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    run_dir: Path,
) -> None:
    # Given a valid prepared start but absent or incorrect authentication
    before = _prepare(store, run_dir)
    body = _request(run_dir).model_dump(mode="json")

    # When the protected route is called
    response = client.post("/execution-instances", json=body, headers=headers)

    # Then authentication rejects it before any start side effect
    assert response.status_code == expected
    _assert_no_side_effects(store, before, supervisor, run_dir)
