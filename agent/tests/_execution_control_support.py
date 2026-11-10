"""Shared fixtures and helpers for execution start/stop contract tests.

Both test modules import these fixtures so the start and stop suites keep one
definition of the auth token, run directory, ledger store, and fake supervisor.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import strategy_run_server
from src.strategy_runs import execution_worker
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    BacktestStartRequest,
    backtest_primitive_prompt,
)
from src.strategy_runs.control_store import ControlStore, DispatchRow
from src.strategy_runs.execution_start_service import ExecutionStartService
from src.strategy_runs.execution_worker import WorkerSpec
from src.strategy_runs.store import DEFAULT_DB_FILENAME

_TOKEN = "execution-start-test-token-7f3a"
_TOKEN_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}
_DISPATCH_KEY = "dispatch-backtest-a"
_FACTOR_DISPATCH_KEY = "dispatch-factor-a"
_FACTOR_CANDIDATES = ("academic_bab",)
_RUN_ID = "mcp-long-start-test-1"
_CONFIG_BYTES = b'{"frozen":true}'
_SOURCE_BYTES = b"# frozen signal engine\n"


class _FakeSupervisor:
    """Thread-safe spawn recorder with the native supervisor's public seam."""

    def __init__(self, store: ControlStore) -> None:
        self._store = store
        self._lock = threading.Lock()
        self.calls: list[WorkerSpec] = []
        self.error: Exception | None = None

    def start(self, dispatch_key: str, spec: WorkerSpec) -> DispatchRow:
        with self._lock:
            self.calls.append(spec)
            error = self.error
        if error is not None:
            raise error
        row = self._store.get_dispatch(dispatch_key)
        assert row is not None
        return row


@pytest.fixture(autouse=True)
def token_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "strategy-run-token"
    path.write_text(_TOKEN + "\n", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv(_TOKEN_ENV, str(path))


@pytest.fixture(autouse=True)
def allow_temporary_run_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        execution_worker,
        "safe_run_dir",
        lambda value: Path(value).expanduser().resolve(),
    )


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    directory = tmp_path / _RUN_ID
    (directory / "code").mkdir(parents=True)
    config = directory / "config.json"
    source = directory / "code" / "signal_engine.py"
    config.write_bytes(_CONFIG_BYTES)
    source.write_bytes(_SOURCE_BYTES)
    config.chmod(0o444)
    source.chmod(0o444)
    return directory


@pytest.fixture
def store(tmp_path: Path) -> ControlStore:
    return ControlStore(tmp_path / DEFAULT_DB_FILENAME)


@pytest.fixture
def supervisor(store: ControlStore) -> _FakeSupervisor:
    return _FakeSupervisor(store)


@pytest.fixture
def start_service(
    store: ControlStore, supervisor: _FakeSupervisor
) -> ExecutionStartService:
    return ExecutionStartService(store, supervisor)


@pytest.fixture
def client(start_service: ExecutionStartService) -> Iterator[TestClient]:
    app = strategy_run_server.create_app(
        lambda: object(),
        execution_start_service_provider=lambda: start_service,
    )
    with TestClient(app) as test_client:
        yield test_client


def _request(run_dir: Path) -> BacktestStartRequest:
    return BacktestStartRequest(
        dispatch_key=_DISPATCH_KEY,
        kind="backtest",
        run_id=_RUN_ID,
        run_dir=str(run_dir),
        input_digest="a" * 64,
        config_sha256="b" * 64,
        source_sha256="c" * 64,
        build_fingerprint="d" * 64,
        prompt=backtest_primitive_prompt(str(run_dir)),
        contract_version=CONTRACT_VERSION,
    )


def _prepare(
    store: ControlStore, run_dir: Path, *, kind: str = "backtest"
) -> DispatchRow:
    return store.claim_dispatch(
        _DISPATCH_KEY,
        kind,
        {
            "run_dir": str(run_dir),
            "input_digest": "a" * 64,
            "config_sha256": "b" * 64,
            "source_sha256": "c" * 64,
        },
        _RUN_ID,
        CONTRACT_VERSION,
    ).row


def _post(client: TestClient, directory: Path, **changes: object):
    body = _request(directory).model_dump(mode="json")
    body.update(changes)
    return client.post("/execution-instances", json=body, headers=_AUTH)


def _assert_no_side_effects(
    store: ControlStore,
    before: DispatchRow,
    supervisor: _FakeSupervisor,
    run_dir: Path,
) -> None:
    assert store.get_dispatch(_DISPATCH_KEY) == before
    assert supervisor.calls == []
    assert (run_dir / "config.json").read_bytes() == _CONFIG_BYTES
    assert (run_dir / "code" / "signal_engine.py").read_bytes() == _SOURCE_BYTES
