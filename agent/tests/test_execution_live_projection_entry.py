"""Production-assembly live projection for the dedicated execution entry.

RM 0013 forward-fix: route registration wires the shared native supervisor's
process-liveness check into the read service, so a live-observable dispatch
projects ``running`` through find and status with no manual handle-provider
injection. ``unknown`` stays reserved for records whose process cannot be
observed, and terminal reads stay stable.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest
import yaml
from fastapi.testclient import TestClient
from httpx import Response
from pytest_socket import disable_socket, enable_socket

import strategy_run_server
from backtest.loaders import local_loader
from src.strategy_library import paths as strategy_paths
from src.strategy_runs.control_contract import CONTRACT_VERSION, JsonValue
from src.strategy_runs.control_store import ControlStore, DispatchRow
from tests.test_backtest_execution_local_integration import (
    _AGENT_ROOT,
    _AUTH,
    _FIXTURE_DIR,
    _OFFLINE_GUARD,
    _Prepared,
    _STRATEGY_ID,
    _TOKEN,
    _VERSION,
    _config,
    _start_body,
)

_READY_MARKER: Final = "VT_LIVE_PROJECTION_READY"
_ENGINE_MARKER: Final = "VT_LOCAL_ENGINE_GENERATE"
_WAIT_BUDGET_S: Final = 60.0
_POLL_S: Final = 0.05
_LIVE_IDENTITY: Final = ("mcp-long-live-wire-1", "live.wire.1")
_UNOBSERVABLE_IDENTITY: Final = ("mcp-long-live-wire-2", "live.wire.2")


@dataclass(frozen=True, slots=True)
class _EntryHarness:
    client: TestClient
    store: ControlStore
    tmp_path: Path


@pytest.fixture(autouse=True)
def _offline_parent() -> Iterator[None]:
    """Block IP networking in the parent while allowing TestClient's local wakeup."""
    disable_socket(allow_unix_socket=True)
    yield
    enable_socket()


@pytest.fixture
def entry_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_EntryHarness]:
    """Build the entry with production control/start/stop assembly, no injection."""
    home = tmp_path / "home"
    bridge = home / ".vibe-trading" / "data-bridge" / "config.yaml"
    bridge.parent.mkdir(parents=True)
    source_path = tmp_path / "sources" / "000001.SZ.csv"
    source_path.parent.mkdir()
    source_path.write_bytes((_FIXTURE_DIR / "000001.SZ.csv").read_bytes())
    bridge.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "symbol": "000001.SZ",
                        "type": "csv",
                        "path": str(source_path),
                        "date_format": "%Y-%m-%d",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    token = tmp_path / "strategy-run-token"
    token.write_text(_TOKEN + "\n", encoding="utf-8")
    token.chmod(0o600)
    monkeypatch.setenv("RM_VT_STRATEGY_RUN_TOKEN_FILE", str(token))
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path / "runs"))
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("TUSHARE_TOKEN", "")

    guard_dir = tmp_path / "offline-guard"
    guard_dir.mkdir()
    (guard_dir / "sitecustomize.py").write_text(_OFFLINE_GUARD, encoding="utf-8")
    python_path = [str(guard_dir), str(_AGENT_ROOT)]
    if existing := os.environ.get("PYTHONPATH"):
        python_path.append(existing)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(python_path))
    monkeypatch.setattr(local_loader, "_CONFIG_PATH", bridge)

    runtime = tmp_path / "runtime"
    strategy_paths.strategy_dir(_STRATEGY_ID, runtime_root=runtime).mkdir(parents=True)
    version_config = strategy_paths.version_config_path(_STRATEGY_ID, _VERSION, runtime_root=runtime)
    version_code = strategy_paths.version_code_path(_STRATEGY_ID, _VERSION, runtime_root=runtime)
    version_code.parent.mkdir(parents=True)
    version_config.write_text('{"frozen":true}\n', encoding="utf-8")
    version_code.write_bytes((_FIXTURE_DIR / "code" / "signal_engine_live_gate.py").read_bytes())

    app = strategy_run_server.create_app(lambda: object())
    with TestClient(app) as client:
        yield _EntryHarness(client, ControlStore(), tmp_path)


def _prepare(harness: _EntryHarness, identity: tuple[str, str]) -> _Prepared:
    run_id, dispatch_key = identity
    run_dir = (harness.tmp_path / "runs" / run_id).resolve()
    config = _config()
    source_bytes = (_FIXTURE_DIR / "code" / "signal_engine_live_gate.py").read_bytes()
    response = harness.client.post(
        "/backtest/prepare",
        json={
            "dispatch_key": dispatch_key,
            "run_dir": str(run_dir),
            "config": config,
            "source": {
                "text": source_bytes.decode("utf-8"),
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
            },
            "data_source": config["data_source"],
            "data_as_of": config["data_as_of"],
            "snapshot_basis": config["snapshot_basis"],
            "contract_version": CONTRACT_VERSION,
        },
        headers=_AUTH,
    )
    assert response.status_code == 200, response.text
    declaration: dict[str, JsonValue] = response.json()
    return _Prepared(run_id, dispatch_key, run_dir, declaration)


def _execute(harness: _EntryHarness, prepared: _Prepared) -> Response:
    return harness.client.post("/backtest/execute", json=_start_body(prepared), headers=_AUTH)


def _find(harness: _EntryHarness, dispatch_key: str) -> Response:
    return harness.client.get("/execution-instances", params={"dispatch_key": dispatch_key}, headers=_AUTH)


def _status(harness: _EntryHarness, remote_id: str) -> Response:
    return harness.client.get(f"/execution-instances/{remote_id}", headers=_AUTH)


def _await_worker_marker(prepared: _Prepared, marker: str) -> None:
    """Await the worker's exact log marker within a bounded budget, never a fixed sleep."""
    log_path = prepared.run_dir / "worker.log"
    deadline = time.monotonic() + _WAIT_BUDGET_S
    while time.monotonic() < deadline:
        if log_path.exists() and marker in log_path.read_text(encoding="utf-8"):
            return
        time.sleep(_POLL_S)
    detail = log_path.read_text(encoding="utf-8") if log_path.exists() else "<no worker.log>"
    pytest.fail(f"worker never printed {marker}:\n{detail}")


def _await_terminal_row(harness: _EntryHarness, prepared: _Prepared) -> DispatchRow:
    """Await the durable terminal transition within a bounded budget."""
    deadline = time.monotonic() + _WAIT_BUDGET_S
    while time.monotonic() < deadline:
        row = harness.store.get_dispatch(prepared.dispatch_key)
        if row is not None and row.status in ("completed", "failed", "cancelled"):
            return row
        time.sleep(_POLL_S)
    pytest.fail(f"dispatch {prepared.dispatch_key} never reached a terminal state")


def test_live_dispatch_projects_running_without_manual_provider_injection(
    entry_harness: _EntryHarness,
) -> None:
    harness = entry_harness
    # Given the production entry app and a gated synthetic worker started by the real route.
    prepared = _prepare(harness, _LIVE_IDENTITY)
    accepted = _execute(harness, prepared)
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["replayed"] is False
    _await_worker_marker(prepared, _READY_MARKER)

    # When find and status read the dispatch while its process is provably live.
    find = _find(harness, prepared.dispatch_key)
    status = _status(harness, prepared.run_id)

    # Then both project running through the wired supervisor, with no injected provider.
    assert find.status_code == 200, find.text
    find_body = find.json()
    assert find_body["state"] == "dispatched"
    assert find_body["status"] == "running"
    assert find_body["remote_id"] == prepared.run_id
    assert find_body["dispatch_key"] == prepared.dispatch_key
    assert find_body["finished_at"] is None
    assert find_body["error_kind"] is None
    assert find_body["contract_version"] == CONTRACT_VERSION
    assert status.status_code == 200, status.text
    status_body = status.json()
    assert status_body["status"] == "running"
    assert status_body["remote_id"] == prepared.run_id
    assert status_body["dispatch_key"] == prepared.dispatch_key
    assert status_body["finished_at"] is None
    assert status_body["error_kind"] is None
    assert status_body["contract_version"] == CONTRACT_VERSION

    # When the gate releases and the durable terminal transition lands.
    (prepared.run_dir / "release.marker").write_text("go\n", encoding="utf-8")
    terminal = _await_terminal_row(harness, prepared)
    assert terminal.status == "completed", (prepared.run_dir / "worker.log").read_text(encoding="utf-8")

    # Then repeated status reads return the same terminal body and find keeps dispatch-row-first.
    first = _status(harness, prepared.run_id)
    second = _status(harness, prepared.run_id)
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == "completed"
    assert second.json() == first.json()
    find_after = _find(harness, prepared.dispatch_key)
    assert find_after.status_code == 200
    assert find_after.json()["state"] == "dispatched"
    assert find_after.json()["status"] == "completed"
    worker_log = (prepared.run_dir / "worker.log").read_text(encoding="utf-8")
    assert worker_log.count(_ENGINE_MARKER) == 1


def test_unobservable_rows_project_unknown_through_production_wiring(
    entry_harness: _EntryHarness,
) -> None:
    harness = entry_harness
    # Given stored nonterminal rows the shared supervisor never started a process for.
    run_dir = harness.tmp_path / "runs" / _UNOBSERVABLE_IDENTITY[0]
    native = harness.store.claim_dispatch(
        _UNOBSERVABLE_IDENTITY[1],
        "backtest",
        {"run_dir": str(run_dir)},
        _UNOBSERVABLE_IDENTITY[0],
        CONTRACT_VERSION,
    ).row
    legacy = harness.store.claim("live.wire.legacy.1", _STRATEGY_ID, _VERSION, None, None, 60).row

    # When find and status read them through the production handle wiring.
    native_find = _find(harness, native.dispatch_key)
    native_status = _status(harness, native.remote_id)
    legacy_find = _find(harness, legacy.run_key)
    legacy_status = _status(harness, legacy.vt_run_id)

    # Then every unobservable nonterminal row stays unknown, never running or 404.
    assert native_find.status_code == 200, native_find.text
    assert native_find.json()["state"] == "dispatched"
    assert native_find.json()["status"] == "unknown"
    assert native_status.status_code == 200, native_status.text
    assert native_status.json()["status"] == "unknown"
    assert legacy_find.status_code == 200, legacy_find.text
    assert legacy_find.json()["state"] == "dispatched"
    assert legacy_find.json()["status"] == "unknown"
    assert legacy_status.status_code == 200, legacy_status.text
    assert legacy_status.json()["status"] == "unknown"
