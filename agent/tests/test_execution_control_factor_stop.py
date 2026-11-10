"""Factor starts and stops bind one declaration to one native worker."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import strategy_run_server
from src.config.paths import get_runtime_root
from src.factors.registry import get_default_registry
from src.factors.registry_freeze import freeze_registry, rows_from_registry
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    FactorPrototypeSpec,
    FactorStartRequest,
)
from src.strategy_runs.control_store import (
    ControlStore,
    ControlStoreError,
    StopClaim,
    StopRow,
)
from src.strategy_runs.execution_start_service import ExecutionStartService
from src.strategy_runs.execution_stop_service import ExecutionStopService
from tests._execution_control_support import (
    _AUTH,
    _DISPATCH_KEY,
    _FACTOR_CANDIDATES,
    _FACTOR_DISPATCH_KEY,
    _RUN_ID,
    _FakeSupervisor,
    _prepare,
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


def _factor_spec(
    *,
    candidate_ids: tuple[str, ...] = _FACTOR_CANDIDATES,
    registry_digest: str | None = None,
    symbols: tuple[str, ...] = ("600000.SH",),
) -> FactorPrototypeSpec:
    digest = registry_digest
    if digest is None:
        rows = rows_from_registry(get_default_registry(), candidate_ids)
        digest = freeze_registry(rows).registry_digest
    return FactorPrototypeSpec(
        registry_digest=digest,
        candidate_ids=candidate_ids,
        symbols=symbols,
        start_date=date(2024, 1, 2),
        split_date=date(2024, 6, 3),
        end_date=date(2024, 12, 31),
        source="alpha_zoo",
        as_of=date(2025, 1, 2),
        forward_return_horizon_days=5,
        n_groups=5,
    )


def _factor_request(
    spec: FactorPrototypeSpec | None = None,
) -> FactorStartRequest:
    return FactorStartRequest(
        dispatch_key=_FACTOR_DISPATCH_KEY,
        kind="factor",
        spec=spec or _factor_spec(),
        contract_version=CONTRACT_VERSION,
    )


def _post_factor(client: TestClient, **changes: object):
    body = _factor_request().model_dump(mode="json")
    body.update(changes)
    return client.post("/execution-instances", json=body, headers=_AUTH)


def _expected_factor_remote_id(request: FactorStartRequest) -> str:
    document = {
        "dispatch_key": request.dispatch_key,
        "spec": request.spec.model_dump(mode="json", by_alias=True),
    }
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return "factor-" + hashlib.sha256(encoded).hexdigest()[:32]


def test_factor_start_returns_deterministic_remote_id_and_spawns_once(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
) -> None:
    # Given one valid frozen factor declaration
    request = _factor_request()
    expected_remote_id = _expected_factor_remote_id(request)
    expected_run_dir = (get_runtime_root() / "runs" / expected_remote_id).resolve()

    # When the factor start is accepted
    response = _post_factor(client)

    # Then the derived identity is returned and exactly one factor worker starts
    assert response.status_code == 202
    assert response.json() == {
        "remote_id": expected_remote_id,
        "dispatch_key": _FACTOR_DISPATCH_KEY,
        "kind": "factor",
        "status": "running",
        "replayed": False,
        "contract_version": CONTRACT_VERSION,
    }
    assert expected_remote_id.startswith("factor-")
    assert len(expected_remote_id) == len("factor-") + 32
    assert len(supervisor.calls) == 1
    assert supervisor.calls[0].kind == "factor"
    assert supervisor.calls[0].run_dir == expected_run_dir
    assert supervisor.calls[0].timeout_s == 3600
    row = store.get_dispatch(_FACTOR_DISPATCH_KEY)
    assert row is not None
    assert row.kind == "factor"
    assert row.remote_id == expected_remote_id
    assert row.run_dir == str(expected_run_dir)
    assert row.status == "dispatched"


def test_factor_start_replays_same_remote_id_without_respawning(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
) -> None:
    # Given one accepted factor start
    first = _post_factor(client)
    started = store.get_dispatch(_FACTOR_DISPATCH_KEY)
    assert first.status_code == 202

    # When the identical declaration is replayed
    replay = _post_factor(client)

    # Then only the replay bit changes, with no second spawn or ledger write
    assert replay.status_code == 202
    assert replay.json() == {**first.json(), "replayed": True}
    assert len(supervisor.calls) == 1
    assert store.get_dispatch(_FACTOR_DISPATCH_KEY) == started


def test_sixteen_concurrent_factor_starts_spawn_once(
    store: ControlStore,
    start_service: ExecutionStartService,
    supervisor: _FakeSupervisor,
) -> None:
    # Given sixteen callers sharing one factor declaration
    request = _factor_request()
    expected_remote_id = _expected_factor_remote_id(request)
    barrier = threading.Barrier(16, timeout=30)

    def attempt(index: int):
        barrier.wait()
        return index, start_service.start(request)

    # When all callers race through the atomic dispatch claim
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(attempt, range(16)))

    # Then one claim is new, fifteen replay, and one worker was requested
    assert {result.remote_id for _, result in results} == {expected_remote_id}
    assert len([index for index, result in results if not result.replayed]) == 1
    assert sum(result.replayed for _, result in results) == 15
    assert len(supervisor.calls) == 1


def test_factor_registry_digest_mismatch_is_409_without_claim_or_spawn(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
) -> None:
    # Given a spec whose client-supplied registry digest is not the recomputed one
    spec = _factor_spec(registry_digest="0" * 64)

    # When the start arrives
    response = _post_factor(client, spec=spec.model_dump(mode="json"))

    # Then the frozen registry wins and nothing is claimed or spawned
    assert response.status_code == 409
    assert response.json() == {"error": "input_digest_mismatch"}
    assert store.get_dispatch(_FACTOR_DISPATCH_KEY) is None
    assert supervisor.calls == []


def test_factor_unknown_candidate_is_400_without_claim_or_spawn(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
) -> None:
    # Given a spec naming a candidate the registry does not contain
    spec = _factor_spec(
        candidate_ids=("academic_bab", "unknown_candidate"),
        registry_digest="a" * 64,
    )

    # When the start arrives
    response = _post_factor(client, spec=spec.model_dump(mode="json"))

    # Then the request is rejected as invalid with no side effects
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request"}
    assert store.get_dispatch(_FACTOR_DISPATCH_KEY) is None
    assert supervisor.calls == []


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"run_dir": "/tmp/caller-owned"}, id="caller-run-dir"),
        pytest.param({"worker": {"argv": ["sh"]}}, id="caller-worker"),
        pytest.param({"kind": "unknown"}, id="unknown-kind"),
    ],
)
def test_factor_invalid_wire_body_is_400_without_side_effects(
    changes: dict[str, object],
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
) -> None:
    # Given a factor body carrying caller-owned execution fields
    response = _post_factor(client, **changes)

    # Then strict validation never claims a dispatch or spawns a worker
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request"}
    assert store.get_dispatch(_FACTOR_DISPATCH_KEY) is None
    assert supervisor.calls == []


def test_changed_factor_spec_conflicts_with_original_remote_id(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
) -> None:
    # Given one accepted factor declaration
    first = _post_factor(client)
    assert first.status_code == 202
    started = store.get_dispatch(_FACTOR_DISPATCH_KEY)

    # When a different valid spec reuses the same dispatch key
    changed = _factor_request(_factor_spec(symbols=("000001.SZ",)))
    response = _post_factor(client, spec=changed.spec.model_dump(mode="json"))

    # Then the original remote id is authoritative and no second spawn happens
    assert response.status_code == 409
    assert response.json() == {
        "error": "idempotency_conflict",
        "original_remote_id": first.json()["remote_id"],
    }
    assert len(supervisor.calls) == 1
    assert store.get_dispatch(_FACTOR_DISPATCH_KEY) == started


def test_factor_spawn_failure_is_503_and_leaves_dispatched_row(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
) -> None:
    # Given a valid new factor claim whose native spawn fails with secret detail
    supervisor.error = RuntimeError("secret factor executable path")

    # When the supervisor cannot start the worker
    response = _post_factor(client)

    # Then the fixed 503 leaks nothing and the row stays dispatched/unknown
    assert response.status_code == 503
    assert response.json() == {"error": "worker_start_failed"}
    assert "secret factor executable path" not in response.text
    row = store.get_dispatch(_FACTOR_DISPATCH_KEY)
    assert row is not None
    assert row.kind == "factor"
    assert row.status == "dispatched"
    assert row.worker_pid is None and row.worker_pgid is None
    assert len(supervisor.calls) == 1


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        pytest.param({}, 401, id="missing"),
        pytest.param({"Authorization": "Bearer wrong"}, 403, id="wrong"),
    ],
)
def test_factor_start_requires_service_token(
    headers: dict[str, str],
    expected: int,
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
) -> None:
    # Given a valid factor declaration but absent or incorrect authentication
    body = _factor_request().model_dump(mode="json")

    # When the protected route is called
    response = client.post("/execution-instances", json=body, headers=headers)

    # Then authentication rejects it before any claim or spawn
    assert response.status_code == expected
    assert store.get_dispatch(_FACTOR_DISPATCH_KEY) is None
    assert supervisor.calls == []


_STOP_KEY = "stop-a"
_SECOND_DISPATCH_KEY = "dispatch-backtest-b"
_SECOND_RUN_ID = "mcp-long-start-test-2"
_STOP_DEADLINE = datetime(2100, 1, 1, tzinfo=UTC)
_PAST_DEADLINE = datetime(2000, 1, 1, tzinfo=UTC)
_STOP_DEADLINE_MS = 4_102_444_800_000


class _FakeStopSupervisor:
    """Store-backed native stop seam that records, but never sends, signals."""

    def __init__(self, store: ControlStore) -> None:
        self._store = store
        self.stop_calls: list[tuple[str, str, str, int]] = []
        self.expire_calls: list[str] = []
        self.signals: list[tuple[str, str]] = []
        self.confirm_with: tuple[str, dict[str, object]] | None = None
        self.failure: Exception | None = None
        self.now_ms: int | None = None

    def stop(
        self, dispatch_key: str, remote_id: str, stop_key: str, deadline_at: int
    ) -> StopClaim:
        self.stop_calls.append((dispatch_key, remote_id, stop_key, deadline_at))
        if self.failure is not None:
            raise self.failure
        claim = self._store.begin_stop(
            dispatch_key, remote_id, stop_key, deadline_at, CONTRACT_VERSION
        )
        if claim.replayed:
            return claim
        row = self._store.get_stop(stop_key)
        assert row is not None
        self.signals.append((stop_key, row.state))
        if self.confirm_with is not None:
            evidence_kind, evidence_payload = self.confirm_with
            confirmed = self._store.resolve_stop(
                stop_key, "confirmed", evidence_kind, evidence_payload
            )
            return StopClaim(replayed=claim.replayed, row=confirmed)
        return claim

    def expire_stop(self, stop_key: str) -> StopRow:
        self.expire_calls.append(stop_key)
        row = self._store.get_stop(stop_key)
        assert row is not None
        stamp = self.now_ms if self.now_ms is not None else int(time.time() * 1000)
        if row.state != "requested" or stamp < row.deadline_at:
            return row
        return self._store.resolve_stop(stop_key, "unknown", resolved_at=stamp)


@pytest.fixture
def stop_supervisor(store: ControlStore) -> _FakeStopSupervisor:
    return _FakeStopSupervisor(store)


@pytest.fixture
def stop_service(
    store: ControlStore, stop_supervisor: _FakeStopSupervisor
) -> ExecutionStopService:
    return ExecutionStopService(store, stop_supervisor)


@pytest.fixture
def stop_client(stop_service: ExecutionStopService) -> Iterator[TestClient]:
    app = strategy_run_server.create_app(
        lambda: object(),
        execution_stop_service_provider=lambda: stop_service,
    )
    with TestClient(app) as test_client:
        yield test_client


def _stop_body(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "dispatch_key": _DISPATCH_KEY,
        "remote_id": _RUN_ID,
        "stop_key": _STOP_KEY,
        "deadline_at": _STOP_DEADLINE.isoformat(),
        "contract_version": CONTRACT_VERSION,
    }
    body.update(changes)
    return body


def _post_stop(
    client: TestClient,
    remote_id: str = _RUN_ID,
    headers: dict[str, str] | None = None,
    body_remote_id: str | None = None,
    **changes: object,
):
    if body_remote_id is not None:
        changes["remote_id"] = body_remote_id
    return client.post(
        f"/execution-instances/{remote_id}/stop",
        json=_stop_body(**changes),
        headers=_AUTH if headers is None else headers,
    )


def _prepare_second(store: ControlStore, run_dir: Path) -> None:
    store.claim_dispatch(
        _SECOND_DISPATCH_KEY,
        "backtest",
        {"run_dir": str(run_dir)},
        _SECOND_RUN_ID,
        CONTRACT_VERSION,
    )


def test_stop_persists_requested_before_signal(
    stop_client: TestClient,
    store: ControlStore,
    stop_supervisor: _FakeStopSupervisor,
    run_dir: Path,
) -> None:
    # Given one prepared dispatch with no terminal evidence yet
    _prepare(store, run_dir)

    # When an unobservable stop is accepted through the stop route
    response = _post_stop(stop_client)

    # Then the row was already requested when the signal boundary was reached
    assert response.status_code == 202
    assert stop_supervisor.signals == [(_STOP_KEY, "requested")]
    assert stop_supervisor.stop_calls[0][3] == _STOP_DEADLINE_MS
    row = store.get_stop(_STOP_KEY)
    assert row is not None and row.state == "requested"
    assert response.json() == {
        "remote_id": _RUN_ID,
        "dispatch_key": _DISPATCH_KEY,
        "stop_key": _STOP_KEY,
        "stop_state": "requested",
        "evidence_kind": None,
        "resolved_at": None,
        "replayed": False,
        "contract_version": CONTRACT_VERSION,
    }


@pytest.mark.parametrize(
    "evidence",
    [
        pytest.param(
            (
                "terminal_record",
                {"status": "cancelled", "finished_at": "2026-11-09T00:00:00Z"},
            ),
            id="terminal_record",
        ),
        pytest.param(
            ("process_absent", {"pid": 4321, "pgid": 4321}), id="process_absent"
        ),
    ],
)
def test_live_stop_confirms_with_evidence(
    evidence: tuple[str, dict[str, object]],
    stop_client: TestClient,
    store: ControlStore,
    stop_supervisor: _FakeStopSupervisor,
    run_dir: Path,
) -> None:
    # Given a live identity whose supervisor observes terminal evidence
    _prepare(store, run_dir)
    stop_supervisor.confirm_with = evidence

    # When the stop is accepted
    response = _post_stop(stop_client)

    # Then the confirmed state carries the evidence class and resolution time
    body = response.json()
    assert response.status_code == 202
    assert body["stop_state"] == "confirmed"
    assert body["evidence_kind"] == evidence[0]
    assert body["resolved_at"] is not None
    assert body["replayed"] is False
    assert store.get_stop(_STOP_KEY).state == "confirmed"


def test_same_stop_tuple_replays_without_a_second_signal(
    stop_client: TestClient,
    store: ControlStore,
    stop_supervisor: _FakeStopSupervisor,
    run_dir: Path,
) -> None:
    # Given one accepted unobservable stop
    _prepare(store, run_dir)
    first = _post_stop(stop_client)

    # When the identical full tuple and stop key arrive again
    replay = _post_stop(stop_client)

    # Then only the replay bit changes and no second signal is recorded
    assert first.status_code == 202 and first.json()["replayed"] is False
    assert replay.status_code == 202
    assert replay.json() == {**first.json(), "replayed": True}
    assert stop_supervisor.signals == [(_STOP_KEY, "requested")]
    assert len(stop_supervisor.stop_calls) == 2


def test_stop_key_reuse_on_another_remote_conflicts_with_original_id(
    stop_client: TestClient,
    store: ControlStore,
    stop_supervisor: _FakeStopSupervisor,
    run_dir: Path,
) -> None:
    # Given a stop key already bound to the first remote identity
    _prepare(store, run_dir)
    _prepare_second(store, run_dir / "second")
    assert _post_stop(stop_client).status_code == 202

    # When another dispatch tries to reuse that stop key
    response = _post_stop(
        stop_client,
        remote_id=_SECOND_RUN_ID,
        body_remote_id=_SECOND_RUN_ID,
        dispatch_key=_SECOND_DISPATCH_KEY,
    )

    # Then the original remote id is named and no second signal is sent
    assert response.status_code == 409
    assert response.json() == {
        "error": "idempotency_conflict",
        "original_remote_id": _RUN_ID,
    }
    assert stop_supervisor.signals == [(_STOP_KEY, "requested")]


def test_stop_path_and_body_remote_mismatch_is_400(
    stop_client: TestClient,
    store: ControlStore,
    stop_supervisor: _FakeStopSupervisor,
    run_dir: Path,
) -> None:
    # Given a prepared dispatch and a body naming another remote
    _prepare(store, run_dir)

    # When the path remote id disagrees with the body
    response = _post_stop(stop_client, body_remote_id="another-remote")

    # Then validation stops before any stop write or signal
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request"}
    assert stop_supervisor.stop_calls == []
    assert store.get_stop(_STOP_KEY) is None


def test_stop_rejects_extra_body_fields(
    stop_client: TestClient,
    store: ControlStore,
    stop_supervisor: _FakeStopSupervisor,
    run_dir: Path,
) -> None:
    # Given a prepared dispatch and a body with an unexpected field
    _prepare(store, run_dir)

    # When the stop body carries an extra field
    response = _post_stop(stop_client, unexpected="not accepted")

    # Then the strict body contract rejects it before any side effect
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request"}
    assert stop_supervisor.stop_calls == []


def test_stop_unknown_remote_is_404(
    stop_client: TestClient,
    stop_supervisor: _FakeStopSupervisor,
) -> None:
    # Given no dispatch owns the requested remote identity
    # When a stop names it
    response = _post_stop(
        stop_client, remote_id="unknown-remote", body_remote_id="unknown-remote"
    )

    # Then the route answers not found and never signals
    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}
    assert stop_supervisor.signals == []


def test_past_deadline_unobservable_stop_is_unknown_without_upgrade(
    stop_client: TestClient,
    store: ControlStore,
    stop_supervisor: _FakeStopSupervisor,
    run_dir: Path,
) -> None:
    # Given no observable evidence and an already elapsed deadline
    _prepare(store, run_dir)
    body = {"deadline_at": _PAST_DEADLINE.isoformat()}

    # When the stop is accepted
    response = _post_stop(stop_client, **body)

    # Then it resolves to unknown without evidence and without a retry
    first = response.json()
    assert response.status_code == 202
    assert first["stop_state"] == "unknown"
    assert first["evidence_kind"] is None
    assert first["resolved_at"] is not None
    assert first["replayed"] is False

    # And an identical replay stays unknown with the same resolution
    replay = _post_stop(stop_client, **body)
    assert replay.json() == {**first, "replayed": True}
    assert stop_supervisor.signals == [(_STOP_KEY, "requested")]
    assert store.get_stop(_STOP_KEY).state == "unknown"


def test_confirmed_stop_never_downgrades(
    stop_client: TestClient,
    store: ControlStore,
    stop_supervisor: _FakeStopSupervisor,
    run_dir: Path,
) -> None:
    # Given a stop already confirmed by terminal evidence
    _prepare(store, run_dir)
    stop_supervisor.confirm_with = (
        "terminal_record",
        {"status": "cancelled", "finished_at": "2026-11-09T00:00:00Z"},
    )
    first = _post_stop(stop_client).json()

    # When the same tuple and then a fresh stop key are replayed
    replay = _post_stop(stop_client).json()
    other_key = _post_stop(stop_client, stop_key="stop-b").json()

    # Then confirmed evidence survives every replay and no signal is repeated
    for body in (replay, other_key):
        assert body["stop_state"] == "confirmed"
        assert body["evidence_kind"] == "terminal_record"
        assert body["resolved_at"] == first["resolved_at"]
    assert replay["replayed"] is True and other_key["replayed"] is True
    assert stop_supervisor.signals == [(_STOP_KEY, "requested")]
    assert store.get_stop(_STOP_KEY).state == "confirmed"


def test_stop_storage_fault_is_fixed_503(
    stop_client: TestClient,
    stop_supervisor: _FakeStopSupervisor,
) -> None:
    # Given a storage boundary that fails with sensitive detail
    stop_supervisor.failure = ControlStoreError("begin_stop", "secret SQL and path")

    # When the stop is accepted
    response = _post_stop(stop_client)

    # Then only the fixed body escapes
    assert response.status_code == 503
    assert response.json() == {"error": "storage_unavailable"}
    assert "secret SQL" not in response.text


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        pytest.param({}, 401, id="missing"),
        pytest.param({"Authorization": "Bearer wrong"}, 403, id="wrong"),
    ],
)
def test_stop_requires_service_token(
    headers: dict[str, str],
    expected: int,
    stop_client: TestClient,
    stop_supervisor: _FakeStopSupervisor,
) -> None:
    # Given a stop request with absent or incorrect authentication
    # When the protected route is called
    response = _post_stop(stop_client, headers=headers)

    # Then authentication rejects it before any stop write or signal
    assert response.status_code == expected
    assert stop_supervisor.stop_calls == []


def test_stop_route_set_includes_all_previous_paths(
    stop_client: TestClient,
) -> None:
    # Given the dedicated entry mounted with the stop provider
    paths = set(stop_client.app.openapi()["paths"])

    # Then the stop path joins exactly the previously frozen route set
    assert paths == {
        "/backtest/execute",
        "/backtest/prepare",
        "/execution-instances",
        "/execution-instances/{remote_id}",
        "/execution-instances/{remote_id}/result",
        "/execution-instances/{remote_id}/stop",
        "/factor/prototype",
        "/strategy-runs",
        "/strategy-runs/{vt_run_id}",
        "/strategy-runs/{vt_run_id}/cancel",
        "/state/export",
        "/state/import",
    }
