"""Failure diagnosability for native execution workers.

A failed child must leave a bounded, redacted failure reason in the durable
dispatch row, classifiable as network timeout versus logic error, readable
through the status and result projections after the process is gone. Success
paths keep their exact prior bodies.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import strategy_run_server
from src.strategy_runs import native_supervisor as native
from src.strategy_runs.control_contract import CONTRACT_VERSION, ContractValidationError
from src.strategy_runs.control_service import ExecutionControlService
from src.strategy_runs.control_store import ControlStore, DispatchTransitionError
from src.strategy_runs.execution_worker import WorkerSpec

AGENT_ROOT = Path(__file__).resolve().parents[1]
TERMINAL_KEYS = {
    "remote_id",
    "dispatch_key",
    "status",
    "exit_code",
    "error_kind",
    "finished_at",
    "contract_version",
}
TOKEN = "dummy-service-token-failure-diagnosis-9c"
TOKEN_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def allowed_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = (tmp_path / "runs").resolve()
    root.mkdir()
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(root))
    return root


def _store(tmp_path: Path) -> ControlStore:
    return ControlStore(tmp_path / "strategy_runs.sqlite3")


def _claim(store: ControlStore, run_dir: Path, key: str = "dispatch-a") -> None:
    store.claim_dispatch(
        key,
        "backtest",
        {"run_dir": str(run_dir)},
        f"remote-{key}",
        CONTRACT_VERSION,
    )


def _spec(run_dir: Path, source: str, timeout_s: int = 10) -> WorkerSpec:
    run_dir.mkdir(parents=True, exist_ok=True)
    script = run_dir / "worker.py"
    script.write_text(source, encoding="utf-8")
    return WorkerSpec(
        kind="backtest",
        argv=(sys.executable, str(script)),
        cwd=AGENT_ROOT,
        timeout_s=timeout_s,
        run_dir=run_dir,
        result_path=run_dir / "worker_result.json",
        log_path=run_dir / "worker.log",
    )


def _threads() -> tuple[list[threading.Thread], Callable[..., threading.Thread]]:
    created: list[threading.Thread] = []

    def factory(**kwargs: Any) -> threading.Thread:
        thread = threading.Thread(**kwargs)
        created.append(thread)
        return thread

    return created, factory


def _run_to_terminal(store: ControlStore, spec: WorkerSpec) -> None:
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    supervisor.start("dispatch-a", spec)
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    supervisor.shutdown()


def _row(store: ControlStore):
    row = store.get_dispatch("dispatch-a")
    assert row is not None
    return row


def _terminal(spec: WorkerSpec) -> dict[str, Any]:
    payload = json.loads(spec.result_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _state_writer(reason: str) -> str:
    """A worker that reports a runner-style failure reason, then exits 1."""
    return (
        "import json, pathlib\n"
        "state = pathlib.Path(__file__).parent / 'state.json'\n"
        f"state.write_text(json.dumps({{'status': 'failed', 'reason': {reason!r}}}))\n"
        "raise SystemExit(1)\n"
    )


def test_failed_child_persists_failure_reason_readable_after_exit(
    allowed_runs: Path, tmp_path: Path
) -> None:
    # Given a child that fails with a concrete reason and exits non-zero.
    run_dir = allowed_runs / "failed-child"
    store = _store(tmp_path)
    _claim(store, run_dir)
    spec = _spec(run_dir, _state_writer("ValueError: signal window is empty"))

    # When the worker terminates and the supervisor settles the dispatch.
    _run_to_terminal(store, spec)

    # Then the durable row, terminal JSON and read projections carry the reason.
    row = _row(store)
    assert row.status == "failed"
    assert row.failure_reason is not None
    assert "signal window is empty" in row.failure_reason
    assert len(row.failure_reason) <= 500
    assert _terminal(spec)["failure_reason"] == row.failure_reason
    service = ExecutionControlService(store, legacy_store=store)
    status = service.status("remote-dispatch-a")
    assert status.failure_reason == row.failure_reason
    result = service.result("remote-dispatch-a")
    assert result.result_status == "failed"
    assert result.result["failure_reason"] == row.failure_reason
    assert result.result["failure_kind"] == status.error_kind


def test_network_timeout_signature_classifies_from_state_reason(
    allowed_runs: Path, tmp_path: Path
) -> None:
    # Given the tencent fetch retry-exhaustion signature in the failure reason.
    run_dir = allowed_runs / "network-state"
    store = _store(tmp_path)
    _claim(store, run_dir)
    reason = (
        "ValueError: incomplete tencent history for 000001: failed after 3 attempts"
    )
    spec = _spec(run_dir, _state_writer(reason))

    # When the worker terminates.
    _run_to_terminal(store, spec)

    # Then the failure is machine-classified as a network timeout.
    row = _row(store)
    assert row.error_kind == "network_timeout"
    assert "failed after 3 attempts" in row.failure_reason
    assert _terminal(spec)["error_kind"] == "network_timeout"


def test_network_timeout_signature_classifies_from_log_tail(
    allowed_runs: Path, tmp_path: Path
) -> None:
    # Given a child that dies with a timeout traceback and no state.json.
    run_dir = allowed_runs / "network-log"
    store = _store(tmp_path)
    _claim(store, run_dir)
    spec = _spec(
        run_dir,
        "import sys\n"
        "print('requests.exceptions.ConnectTimeout: HTTPSConnectionPool: Read timed out.', file=sys.stderr)\n"
        "raise SystemExit(1)\n",
    )

    # When the worker terminates.
    _run_to_terminal(store, spec)

    # Then the log tail still classifies and explains the failure.
    row = _row(store)
    assert row.error_kind == "network_timeout"
    assert "timed out" in row.failure_reason


def test_logic_error_is_distinguished_from_network_timeout(
    allowed_runs: Path, tmp_path: Path
) -> None:
    # Given a strategy-code exception without any network signature.
    run_dir = allowed_runs / "logic-error"
    store = _store(tmp_path)
    _claim(store, run_dir)
    spec = _spec(run_dir, _state_writer("ZeroDivisionError: division by zero"))

    # When the worker terminates.
    _run_to_terminal(store, spec)

    # Then the classification separates it from the network class.
    row = _row(store)
    assert row.error_kind == "logic_error"
    assert "division by zero" in row.failure_reason


def test_unknown_failure_keeps_runner_error_with_a_reason(
    allowed_runs: Path, tmp_path: Path
) -> None:
    # Given a child that exits non-zero without any diagnostic output.
    run_dir = allowed_runs / "silent-exit"
    store = _store(tmp_path)
    _claim(store, run_dir)
    spec = _spec(run_dir, "raise SystemExit(3)\n")

    # When the worker terminates.
    _run_to_terminal(store, spec)

    # Then the fallback kind stays honest and a reason still exists.
    row = _row(store)
    assert row.error_kind == "runner_error"
    assert row.failure_reason
    assert "3" in row.failure_reason
    assert _terminal(spec)["exit_code"] == 3


def test_success_path_is_unchanged(allowed_runs: Path, tmp_path: Path) -> None:
    # Given a cleanly completing child.
    run_dir = allowed_runs / "clean"
    store = _store(tmp_path)
    _claim(store, run_dir)
    spec = _spec(run_dir, "print('done', flush=True)\n")

    # When the worker terminates.
    _run_to_terminal(store, spec)

    # Then no failure field appears anywhere on the success path.
    row = _row(store)
    assert (row.status, row.error_kind, row.failure_reason) == ("completed", None, None)
    assert set(_terminal(spec)) == TERMINAL_KEYS
    service = ExecutionControlService(store, legacy_store=store)
    assert service.status("remote-dispatch-a").failure_reason is None


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "import sys\n"
            "for _ in range(6000):\n"
            "    print('x' * 60, file=sys.stderr)\n"
            "raise SystemExit(1)\n",
            id="huge-log-tail",
        ),
        pytest.param(
            _state_writer("ValueError: " + "y" * 2000), id="huge-state-reason"
        ),
    ],
)
def test_failure_reason_is_bounded(
    allowed_runs: Path, tmp_path: Path, source: str
) -> None:
    # Given a failure whose diagnostic text far exceeds the persisted cap.
    run_dir = allowed_runs / "bounded"
    store = _store(tmp_path)
    _claim(store, run_dir)
    spec = _spec(run_dir, source)

    # When the worker terminates.
    _run_to_terminal(store, spec)

    # Then the persisted reason stays within the 500-character bound.
    row = _row(store)
    assert row.failure_reason is not None
    assert 0 < len(row.failure_reason) <= 500


def test_failure_reason_is_redacted(allowed_runs: Path, tmp_path: Path) -> None:
    # Given a failure log carrying credential-shaped text.
    run_dir = allowed_runs / "redacted"
    store = _store(tmp_path)
    _claim(store, run_dir)
    spec = _spec(
        run_dir,
        "import sys\n"
        "print('Authorization: Bearer supersecrettoken123456', file=sys.stderr)\n"
        "print('password=hunter2', file=sys.stderr)\n"
        "raise SystemExit(1)\n",
    )

    # When the worker terminates.
    _run_to_terminal(store, spec)

    # Then the persisted reason keeps the diagnosis but never the credentials.
    row = _row(store)
    assert row.failure_reason is not None
    assert "supersecrettoken123456" not in row.failure_reason
    assert "hunter2" not in row.failure_reason
    assert "[redacted]" in row.failure_reason
    assert "supersecrettoken123456" not in spec.result_path.read_text(encoding="utf-8")


def test_wire_bodies_carry_failure_fields_additively(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a ledger with one failed and one completed dispatch, served over HTTP.
    token = tmp_path / "service-token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    token.chmod(0o600)
    monkeypatch.setenv(TOKEN_ENV, str(token))
    store = _store(tmp_path)
    failed_dir = allowed_runs / "wire-failed"
    _claim(store, failed_dir)
    failed_spec = _spec(failed_dir, _state_writer("ValueError: bad window"))
    _run_to_terminal(store, failed_spec)
    clean_dir = allowed_runs / "wire-clean"
    store.claim_dispatch(
        "dispatch-b",
        "backtest",
        {"run_dir": str(clean_dir)},
        "remote-dispatch-b",
        CONTRACT_VERSION,
    )
    clean_spec = _spec(clean_dir, "pass\n")
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    supervisor.start("dispatch-b", clean_spec)
    for thread in threads:
        thread.join(timeout=10)
    supervisor.shutdown()
    service = ExecutionControlService(store, legacy_store=store)
    app = strategy_run_server.create_app(
        lambda: store, control_service_provider=lambda: service
    )

    # When the status and result bodies are read after settlement.
    with TestClient(app) as client:
        failed_status = client.get(
            "/execution-instances/remote-dispatch-a", headers=AUTH
        )
        failed_result = client.get(
            "/execution-instances/remote-dispatch-a/result", headers=AUTH
        )
        clean_status = client.get(
            "/execution-instances/remote-dispatch-b", headers=AUTH
        )

    # Then the failed bodies carry the reason and the success body is unchanged.
    assert failed_status.status_code == 200
    body = failed_status.json()
    assert body["status"] == "failed"
    assert 0 < len(body["failure_reason"]) <= 500
    assert body["error_kind"] == "logic_error"
    result_body = failed_result.json()
    assert result_body["result_status"] == "failed"
    assert result_body["result"]["failure_reason"] == body["failure_reason"]
    assert clean_status.status_code == 200
    assert "failure_reason" not in clean_status.json()


def test_store_adds_failure_reason_column_to_an_existing_ledger(tmp_path: Path) -> None:
    # Given a ledger created before the failure-reason column existed.
    path = tmp_path / "ledger.sqlite3"
    ControlStore(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "ALTER TABLE execution_dispatches DROP COLUMN failure_reason"
        )
        connection.commit()
    finally:
        connection.close()

    # When the store reopens and settles a failed dispatch with a reason.
    reopened = ControlStore(path)
    reopened.claim_dispatch("dispatch-a", "backtest", {}, "remote-a", CONTRACT_VERSION)
    reopened.mark_dispatch_terminal(
        "dispatch-a", "failed", "runner_error", failure_reason="boom"
    )

    # Then the additive column is back and the reason is durable.
    row = reopened.get_dispatch("dispatch-a")
    assert row is not None
    assert row.failure_reason == "boom"


def test_mark_dispatch_terminal_bounds_and_gates_failure_reason(tmp_path: Path) -> None:
    # Given a committed dispatch.
    store = _store(tmp_path)
    store.claim_dispatch("dispatch-a", "backtest", {}, "remote-a", CONTRACT_VERSION)

    # Then oversized or blank reasons are refused at the store boundary.
    with pytest.raises(ContractValidationError):
        store.mark_dispatch_terminal(
            "dispatch-a", "failed", "runner_error", failure_reason="z" * 501
        )
    with pytest.raises(ContractValidationError):
        store.mark_dispatch_terminal(
            "dispatch-a", "failed", "runner_error", failure_reason="   "
        )

    # And a non-failed terminal state never accepts a reason.
    with pytest.raises(DispatchTransitionError):
        store.mark_dispatch_terminal("dispatch-a", "completed", failure_reason="boom")
