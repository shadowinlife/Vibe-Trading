"""Behavior tests for original-instance execution-control read projections.

# noqa: SIZE_OK - one focused matrix; splitting is out of scope.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from unittest import mock

import pytest

from src.strategy_runs.control_contract import CONTRACT_VERSION
from src.strategy_runs.control_service import (
    ControlProjectionError,
    ExecutionControlService,
)
from src.strategy_runs.control_store import ControlStore, DispatchRow
from src.strategy_runs.store import RunRow


@pytest.fixture(autouse=True)
def _allowed_run_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))


@pytest.fixture
def store(tmp_path: Path) -> ControlStore:
    return ControlStore(tmp_path / "ledger.sqlite3")


def _dispatch(
    store: ControlStore,
    key: str = "dispatch-1",
    remote_id: str = "remote-1",
    run_dir: Path | None = None,
) -> DispatchRow:
    declaration = {"run_dir": str(run_dir)} if run_dir is not None else {}
    return store.claim_dispatch(
        key,
        "backtest",
        declaration,
        remote_id,
        CONTRACT_VERSION,
        now_ms=1_000,
    ).row


def _legacy(store: ControlStore, key: str = "legacy-1") -> RunRow:
    return store.claim(
        key,
        "strategy",
        "v1",
        None,
        None,
        60,
        now_ms=1_000,
    ).row


def _finish_dispatch(
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


def test_find_not_dispatched_has_exact_json_ready_shape(
    store: ControlStore,
) -> None:
    projection = ExecutionControlService(store).find("missing")

    assert asdict(projection) == {
        "dispatch_key": "missing",
        "state": "not_dispatched",
        "remote_id": None,
        "status": None,
        "run_dir": None,
        "started_at": None,
        "finished_at": None,
        "error_kind": None,
        "last_status": None,
        "absence_evidence": None,
        "observed_at": None,
        "contract_version": CONTRACT_VERSION,
    }
    json.dumps(asdict(projection), allow_nan=False)


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_find_native_terminal_without_absence_is_dispatched(
    store: ControlStore, status: str
) -> None:
    row = _dispatch(store, run_dir=Path("/server/run"))
    _finish_dispatch(
        store, row, status, error_kind="unclassified" if status == "failed" else None
    )

    projection = ExecutionControlService(store).find(row.dispatch_key)

    assert asdict(projection) == {
        "dispatch_key": row.dispatch_key,
        "state": "dispatched",
        "remote_id": row.remote_id,
        "status": status,
        "run_dir": "/server/run",
        "started_at": "1970-01-01T00:00:02.000Z",
        "finished_at": "1970-01-01T00:00:03.000Z",
        "error_kind": "runner_error" if status == "failed" else None,
        "last_status": None,
        "absence_evidence": None,
        "observed_at": None,
        "contract_version": CONTRACT_VERSION,
    }


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_find_legacy_run_key_projects_legacy_identity(
    store: ControlStore, status: str
) -> None:
    row = _legacy(store)
    store.mark_terminal(
        row.vt_run_id,
        status,
        error="failed" if status == "failed" else None,
        error_kind="unclassified" if status == "failed" else None,
        now_ms=3_000,
    )

    projection = ExecutionControlService(store, legacy_store=store).find(row.run_key)

    assert asdict(projection) == {
        "dispatch_key": row.run_key,
        "state": "dispatched",
        "remote_id": row.vt_run_id,
        "status": status,
        "run_dir": None,
        "started_at": "1970-01-01T00:00:01.000Z",
        "finished_at": "1970-01-01T00:00:03.000Z",
        "error_kind": "runner_error" if status == "failed" else None,
        "last_status": None,
        "absence_evidence": None,
        "observed_at": None,
        "contract_version": CONTRACT_VERSION,
    }


@pytest.mark.parametrize(("alive", "expected"), [(True, "running"), (False, "unknown")])
def test_find_native_dispatched_uses_live_handle_observation(
    store: ControlStore, alive: bool, expected: str
) -> None:
    row = _dispatch(store)
    service = ExecutionControlService(
        store, handle_provider=lambda remote_id: alive and remote_id == row.remote_id
    )

    projection = service.find(row.dispatch_key)

    assert projection.state == "dispatched"
    assert projection.status == expected
    assert projection.last_status is None


def test_find_legacy_running_with_live_handle_is_running(store: ControlStore) -> None:
    row = _legacy(store)
    service = ExecutionControlService(
        store, legacy_store=store, handle_provider=lambda _: True
    )

    projection = service.find(row.run_key)

    assert projection.state == "dispatched"
    assert projection.status == "running"
    assert projection.finished_at is None


def test_find_matching_cross_table_identity_prefers_dispatch(
    store: ControlStore,
) -> None:
    legacy = _legacy(store, "shared-key")
    dispatch = _dispatch(store, "shared-key", legacy.vt_run_id)
    _finish_dispatch(store, dispatch, "completed")
    calls = 0
    real_connect = sqlite3.connect

    def counted_connect(database: str) -> sqlite3.Connection:
        nonlocal calls
        calls += 1
        return real_connect(database)

    with mock.patch("sqlite3.connect", new=counted_connect):
        projection = ExecutionControlService(store).find("shared-key")

    assert calls == 1
    assert projection.remote_id == legacy.vt_run_id
    assert projection.status == "completed"
    assert projection.last_status is None


@pytest.mark.parametrize(
    "evidence",
    ["terminal_record", "process_absent", "ttl_expired", "explicit_absence"],
)
def test_find_confirmed_terminal_absence_includes_evidence(
    store: ControlStore, evidence: str
) -> None:
    row = _dispatch(store)
    _finish_dispatch(store, row, "cancelled")
    store.begin_stop(
        row.dispatch_key,
        row.remote_id,
        "stop-1",
        4_000,
        CONTRACT_VERSION,
        now_ms=3_500,
    )
    store.resolve_stop("stop-1", "confirmed", evidence, {"verified": True}, 5_000)

    projection = ExecutionControlService(store).find(row.dispatch_key)

    assert projection.state == "absent"
    assert projection.last_status == "cancelled"
    assert projection.absence_evidence == evidence
    assert projection.observed_at == "1970-01-01T00:00:05.000Z"
    assert projection.status is None
    assert projection.run_dir is None
    assert projection.started_at is None
    assert projection.finished_at is None
    assert projection.error_kind is None


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_status_native_terminal_states_are_stable(
    store: ControlStore, status: str
) -> None:
    row = _dispatch(store, run_dir=Path("/server/run"))
    _finish_dispatch(
        store, row, status, error_kind="timeout" if status == "failed" else None
    )

    projection = ExecutionControlService(store, handle_provider=lambda _: True).status(
        row.remote_id
    )

    assert asdict(projection) == {
        "remote_id": row.remote_id,
        "dispatch_key": row.dispatch_key,
        "status": status,
        "started_at": "1970-01-01T00:00:02.000Z",
        "finished_at": "1970-01-01T00:00:03.000Z",
        "error_kind": "timeout" if status == "failed" else None,
        "run_dir": "/server/run",
        "contract_version": CONTRACT_VERSION,
    }


@pytest.mark.parametrize(("alive", "expected"), [(True, "running"), (False, "unknown")])
def test_status_dispatched_uses_live_handle_observation(
    store: ControlStore, alive: bool, expected: str
) -> None:
    row = _dispatch(store)
    service = ExecutionControlService(
        store, handle_provider=lambda remote_id: alive and remote_id == row.remote_id
    )

    assert service.status(row.remote_id).status == expected


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_status_legacy_terminal_is_stable(store: ControlStore, status: str) -> None:
    row = _legacy(store)
    store.mark_terminal(
        row.vt_run_id,
        status,
        error="failed" if status == "failed" else None,
        error_kind="timeout" if status == "failed" else None,
        now_ms=3_000,
    )

    projection = ExecutionControlService(store, legacy_store=store).status(
        row.vt_run_id
    )

    assert projection.dispatch_key == row.run_key
    assert projection.status == status
    assert projection.started_at == "1970-01-01T00:00:01.000Z"
    assert projection.finished_at == "1970-01-01T00:00:03.000Z"
    assert projection.run_dir is None


@pytest.mark.parametrize(("alive", "expected"), [(True, "running"), (False, "unknown")])
def test_status_legacy_running_uses_live_handle_observation(
    store: ControlStore, alive: bool, expected: str
) -> None:
    row = _legacy(store)
    service = ExecutionControlService(
        store, legacy_store=store, handle_provider=lambda _: alive
    )

    assert service.status(row.vt_run_id).status == expected


def test_result_available_returns_bounded_json_and_progress(
    store: ControlStore, tmp_path: Path
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
    _finish_dispatch(store, row, "completed", result_path)

    projection = ExecutionControlService(store).result(row.remote_id)

    assert asdict(projection) == {
        "remote_id": row.remote_id,
        "status": "completed",
        "result_status": "available",
        "result": {"summary": {"metrics": {"sharpe": 1.5}}},
        "progress": {"completed": 1, "total": 1},
        "has_more": False,
        "contract_version": CONTRACT_VERSION,
    }


def test_result_without_persisted_path_is_unavailable(store: ControlStore) -> None:
    row = _dispatch(store)

    projection = ExecutionControlService(store).result(row.remote_id)

    assert projection.status == "unknown"
    assert projection.result_status == "unavailable"
    assert projection.result is None
    assert projection.progress is None
    assert projection.has_more is False


def test_failure_find_storage_error_is_not_absence(
    store: ControlStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _dispatch(store)
    monkeypatch.setattr(store, "path", tmp_path)

    with pytest.raises(ControlProjectionError) as caught:
        ExecutionControlService(store).find(row.dispatch_key)

    assert caught.value.kind == "storage_unavailable"
    assert caught.value.detail


def test_failure_find_cross_table_identity_conflict(store: ControlStore) -> None:
    legacy = _legacy(store, "shared-key")
    dispatch = _dispatch(store, "shared-key", "different-remote")

    with pytest.raises(ControlProjectionError) as caught:
        ExecutionControlService(store).find(dispatch.dispatch_key)

    assert caught.value.kind == "identity_conflict"
    assert legacy.vt_run_id not in caught.value.detail
    assert dispatch.remote_id not in caught.value.detail


def test_failure_unobservable_legacy_running_is_unknown(store: ControlStore) -> None:
    row = _legacy(store)

    projection = ExecutionControlService(store, legacy_store=store).find(row.run_key)

    assert asdict(projection) == {
        "dispatch_key": row.run_key,
        "state": "dispatched",
        "remote_id": row.vt_run_id,
        "status": "unknown",
        "run_dir": None,
        "started_at": "1970-01-01T00:00:01.000Z",
        "finished_at": None,
        "error_kind": None,
        "last_status": None,
        "absence_evidence": None,
        "observed_at": None,
        "contract_version": CONTRACT_VERSION,
    }


@pytest.mark.parametrize("method_name", ["status", "result"])
def test_failure_unknown_remote_id_is_not_found(
    store: ControlStore, method_name: str
) -> None:
    service = ExecutionControlService(store)

    with pytest.raises(ControlProjectionError) as caught:
        getattr(service, method_name)("unknown")

    assert caught.value.kind == "not_found"


def test_failure_terminal_missing_result_file_is_unavailable(
    store: ControlStore, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    row = _dispatch(store, run_dir=run_dir)
    _finish_dispatch(store, row, "completed", run_dir / "missing.json")

    projection = ExecutionControlService(store).result(row.remote_id)

    assert projection.result_status == "unavailable"
    assert projection.result is None
    assert projection.progress is None


def test_failure_foreign_result_path_is_rejected_without_disclosure(
    store: ControlStore, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    foreign = tmp_path / "foreign.json"
    foreign.write_text('{"secret": "do-not-read"}', encoding="utf-8")
    traversal = run_dir / ".." / foreign.name
    row = _dispatch(store, run_dir=run_dir)
    _finish_dispatch(store, row, "completed", traversal)

    with pytest.raises(ControlProjectionError) as caught:
        ExecutionControlService(store).result(row.remote_id)

    assert caught.value.kind == "invalid_request"
    assert str(foreign) not in caught.value.detail
    assert "do-not-read" not in caught.value.detail


@pytest.mark.parametrize(
    ("error_kind", "expected"),
    [("", "runner_error"), ("unclassified", "runner_error"), (" timeout ", "timeout")],
)
def test_failure_failed_status_always_has_known_error_kind(
    store: ControlStore, error_kind: str, expected: str
) -> None:
    row = _legacy(store)
    store.mark_terminal(
        row.vt_run_id,
        "failed",
        error="unclassified failure",
        error_kind=error_kind,
        now_ms=3_000,
    )

    projection = ExecutionControlService(store).status(row.vt_run_id)

    assert projection.status == "failed"
    assert projection.error_kind == expected


@pytest.mark.parametrize("method_name", ["find", "status", "result"])
def test_failure_invalid_identifier_is_rejected(
    store: ControlStore, method_name: str
) -> None:
    with pytest.raises(ControlProjectionError) as caught:
        getattr(ExecutionControlService(store), method_name)("../escape")

    assert caught.value.kind == "invalid_request"
