"""Frozen backtest execute, drift refusal, and native result provenance.

# noqa: SIZE_OK - one public execute/result seam covers the required route matrix.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
import yaml
from fastapi.testclient import TestClient

import strategy_run_server
from backtest.loaders import local_loader
from backtest.run_card import write_run_card
from src.strategy_library import paths as strategy_paths
from src.strategy_runs.backtest_materialize import BacktestMaterializer
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    JsonValue,
    backtest_primitive_prompt,
)
from src.strategy_runs.control_service import ExecutionControlService
from src.strategy_runs.control_store import (
    ControlStore,
    DispatchRow,
    DispatchTransitionError,
)
from src.strategy_runs.execution_start_service import ExecutionStartService
from src.strategy_runs.execution_worker import WorkerSpec

_TOKEN: Final = "backtest-execute-test-token"
_AUTH: Final = {"Authorization": f"Bearer {_TOKEN}"}
_DISPATCH_KEY: Final = "user-1.job-9.1"
_RUN_ID: Final = "mcp-long-job-9-1"
_STRATEGY_ID: Final = "momentum-alpha"
_VERSION: Final = "v1-abcdef12"
_SOURCE: Final = "class SignalEngine:\n    pass\n"


class _FakeSupervisor:
    def __init__(self, store: ControlStore) -> None:
        self._store = store
        self.calls: list[WorkerSpec] = []
        self.error: Exception | None = None

    def start(self, dispatch_key: str, spec: WorkerSpec) -> DispatchRow:
        self.calls.append(spec)
        if self.error is not None:
            raise self.error
        row = self._store.get_dispatch(dispatch_key)
        assert row is not None
        return row


@pytest.fixture(autouse=True)
def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = tmp_path / "strategy-run-token"
    token.write_text(_TOKEN + "\n", encoding="utf-8")
    token.chmod(0o600)
    monkeypatch.setenv("RM_VT_STRATEGY_RUN_TOKEN_FILE", str(token))
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path / "runs"))
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path / "runtime"))
    runtime = tmp_path / "runtime"
    strategy_paths.strategy_dir(_STRATEGY_ID, runtime_root=runtime).mkdir(parents=True)
    config = strategy_paths.version_config_path(
        _STRATEGY_ID, _VERSION, runtime_root=runtime
    )
    code = strategy_paths.version_code_path(
        _STRATEGY_ID, _VERSION, runtime_root=runtime
    )
    code.parent.mkdir(parents=True)
    config.write_text('{"frozen":true}\n', encoding="utf-8")
    code.write_text(_SOURCE, encoding="utf-8")
    data = tmp_path / "data" / "aapl.csv"
    data.parent.mkdir()
    data.write_text(
        "Date,Open,High,Low,Close,Volume\n"
        "2026-01-01,10,11,9,10.5,1000\n"
        "2026-01-02,12,13,11,12.5,1500\n",
        encoding="utf-8",
    )
    bridge = tmp_path / "data-bridge" / "config.yaml"
    bridge.parent.mkdir()
    bridge.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "symbol": "AAPL.US",
                        "type": "csv",
                        "path": str(data),
                        "columns": {
                            "date": "Date",
                            "open": "Open",
                            "high": "High",
                            "low": "Low",
                            "close": "Close",
                            "volume": "Volume",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(local_loader, "_CONFIG_PATH", bridge)


@pytest.fixture
def store(tmp_path: Path) -> ControlStore:
    return ControlStore(tmp_path / "ledger.sqlite3")


@pytest.fixture
def supervisor(store: ControlStore) -> _FakeSupervisor:
    return _FakeSupervisor(store)


@pytest.fixture
def client(
    store: ControlStore, supervisor: _FakeSupervisor, tmp_path: Path
) -> Iterator[TestClient]:
    materializer = BacktestMaterializer(store, runtime_root=tmp_path / "runtime")
    starter = ExecutionStartService(store, supervisor)
    reader = ExecutionControlService(store)
    app = strategy_run_server.create_app(
        lambda: object(),
        control_service_provider=lambda: reader,
        prepare_provider=lambda: materializer,
        execution_start_service_provider=lambda: starter,
    )
    with TestClient(app) as test_client:
        yield test_client


def _config(source: str) -> dict[str, JsonValue]:
    local = source == "local"
    return {
        "strategy_id": _STRATEGY_ID,
        "strategy_version": _VERSION,
        "market": "us" if local else "cn_a",
        "symbols": ["AAPL.US" if local else "000001.SZ"],
        "start_date": "2026-01-01",
        "end_date": "2026-01-02",
        "bar_interval": "1d",
        "data_source": source,
        "data_as_of": "2026-01-03",
        "snapshot_basis": "test-snapshot-v1",
        "initial_cash": "100000.00",
        "currency": "USD" if local else "CNY",
        "commission_bps": "1.5",
        "tax_bps": "5",
        "slippage_bps": "2",
    }


def _prepare(
    client: TestClient, tmp_path: Path, source: str = "local"
) -> dict[str, JsonValue]:
    config = _config(source)
    response = client.post(
        "/backtest/prepare",
        json={
            "dispatch_key": _DISPATCH_KEY,
            "run_dir": str((tmp_path / "runs" / _RUN_ID).resolve()),
            "config": config,
            "source": {
                "text": _SOURCE,
                "sha256": hashlib.sha256(_SOURCE.encode()).hexdigest(),
            },
            "data_source": config["data_source"],
            "data_as_of": config["data_as_of"],
            "snapshot_basis": config["snapshot_basis"],
            "contract_version": CONTRACT_VERSION,
        },
        headers=_AUTH,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _start_body(prepared: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        "dispatch_key": _DISPATCH_KEY,
        "kind": "backtest",
        "run_id": _RUN_ID,
        "run_dir": prepared["run_dir"],
        "input_digest": prepared["input_digest"],
        "config_sha256": prepared["config_sha256"],
        "source_sha256": prepared["source_sha256"],
        "build_fingerprint": "d" * 64,
        "prompt": backtest_primitive_prompt(str(prepared["run_dir"])),
        "contract_version": CONTRACT_VERSION,
    }


def _execute(client: TestClient, body: dict[str, JsonValue], **changes: JsonValue):
    payload = dict(body)
    payload.update(changes)
    return client.post("/backtest/execute", json=payload, headers=_AUTH)


def _write_native_results(run_dir: Path) -> None:
    artifacts = run_dir / "artifacts"
    artifacts.mkdir()
    (artifacts / "equity.csv").write_text(
        "timestamp,ret,equity,drawdown,benchmark_equity,active_ret\n"
        "2026-01-01,0,100000,0,100000,0\n"
        "2026-01-02,0.1,110000,0,105000,0.05\n",
        encoding="utf-8",
    )
    (artifacts / "trades.csv").write_text(
        "symbol,quantity\nAAPL.US,10\n", encoding="utf-8"
    )
    (artifacts / "metrics.csv").write_text(
        "total_return,sharpe\n0.1,1.5\n", encoding="utf-8"
    )
    (artifacts / "positions.csv").write_text(
        "symbol,quantity\nAAPL.US,10\n", encoding="utf-8"
    )
    write_run_card(
        run_dir,
        {
            "codes": ["AAPL.US"],
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
            "interval": "1D",
            "initial_cash": 100000.0,
        },
        {"total_return": 0.1, "sharpe": 1.5},
        data_sources=["local"],
    )


def test_execute_accepts_and_replays_with_exactly_one_native_worker(
    client: TestClient, supervisor: _FakeSupervisor, tmp_path: Path
) -> None:
    # Given a fully prepared local declaration and its exact start body.
    prepared = _prepare(client, tmp_path)
    frozen = {
        name: (tmp_path / "runs" / _RUN_ID / name).read_bytes()
        for name in ("config.json", "code/signal_engine.py")
    }
    # When the same execute declaration is sent twice.
    first = _execute(client, _start_body(prepared))
    replay = _execute(client, _start_body(prepared))
    # Then only the first call starts the fixed native runner.
    expected = {
        "remote_id": _RUN_ID,
        "dispatch_key": _DISPATCH_KEY,
        "kind": "backtest",
        "status": "running",
        "replayed": False,
        "contract_version": CONTRACT_VERSION,
    }
    assert first.status_code == 202, first.text
    assert first.json() == expected
    assert replay.status_code == 202 and replay.json() == {**expected, "replayed": True}
    assert len(supervisor.calls) == 1
    spec = supervisor.calls[0]
    assert spec.kind == "backtest" and spec.timeout_s == 3600
    assert spec.run_dir == (tmp_path / "runs" / _RUN_ID).resolve()
    assert spec.argv[-2].endswith("backtest/runner.py")
    assert spec.argv[-1] == str(spec.run_dir)
    assert {
        name: (tmp_path / "runs" / _RUN_ID / name).read_bytes() for name in frozen
    } == frozen


def test_completed_dispatch_replays_without_rerunning(
    client: TestClient, store: ControlStore, supervisor: _FakeSupervisor, tmp_path: Path
) -> None:
    # Given an accepted start whose dispatch has already completed.
    prepared = _prepare(client, tmp_path)
    body = _start_body(prepared)
    assert _execute(client, body).status_code == 202
    store.mark_dispatch_terminal(_DISPATCH_KEY, "completed")
    # When the completed declaration is replayed.
    response = _execute(client, body)
    # Then the original id replays without another worker.
    assert response.status_code == 202
    assert response.json()["remote_id"] == _RUN_ID
    assert response.json()["replayed"] is True
    assert len(supervisor.calls) == 1


def test_in_flight_replay_does_not_rewrite_lifecycle_after_drift(
    client: TestClient, store: ControlStore, supervisor: _FakeSupervisor, tmp_path: Path
) -> None:
    # Given an accepted worker whose frozen config changes after its start claim.
    prepared = _prepare(client, tmp_path)
    body = _start_body(prepared)
    assert _execute(client, body).status_code == 202
    config = tmp_path / "runs" / _RUN_ID / "config.json"
    config.chmod(0o600)
    config.write_bytes(b"tampered")
    # When the existing start declaration is replayed.
    response = _execute(client, body)
    # Then replay neither spawns nor converts the live dispatch into a drift failure.
    assert response.status_code == 202 and response.json()["replayed"] is True
    assert len(supervisor.calls) == 1
    row = store.get_dispatch(_DISPATCH_KEY)
    assert row is not None and row.status == "dispatched"
    assert row.start_digest is not None and row.error_kind is None


@pytest.mark.parametrize("drift", ["config", "source", "declaration", "local-data"])
def test_pre_spawn_drift_fails_closed_and_remains_visible(
    drift: str,
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    tmp_path: Path,
) -> None:
    # Given one verified local prepare and one independent post-prepare mutation.
    prepared = _prepare(client, tmp_path)
    run_dir = tmp_path / "runs" / _RUN_ID
    if drift == "local-data":
        data = tmp_path / "data" / "aapl.csv"
        data.write_text(
            data.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8"
        )
    elif drift == "declaration":
        row = store.get_dispatch(_DISPATCH_KEY)
        assert row is not None
        declaration = json.loads(row.declaration_json)
        declaration["input_digest"] = "0" * 64
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                "UPDATE execution_dispatches SET declaration_json = ? WHERE dispatch_key = ?",
                (
                    json.dumps(declaration, sort_keys=True, separators=(",", ":")),
                    _DISPATCH_KEY,
                ),
            )
    else:
        target = run_dir / (
            "config.json" if drift == "config" else "code/signal_engine.py"
        )
        target.chmod(0o600)
        target.write_bytes(b"tampered")
    # When execute rechecks the frozen inputs before its start claim.
    response = _execute(client, _start_body(prepared))
    # Then drift is durable, visible, and cannot spawn or create another run.
    assert response.status_code == 409
    assert response.json() == {
        "error": "input_digest_mismatch",
        "original_remote_id": _RUN_ID,
    }
    row = store.get_dispatch(_DISPATCH_KEY)
    assert row is not None and row.status == "failed"
    assert row.error_kind == "input_digest_mismatch" and row.start_digest is None
    assert supervisor.calls == []
    assert [path.name for path in (tmp_path / "runs").iterdir()] == [_RUN_ID]
    found = client.get(
        "/execution-instances", params={"dispatch_key": _DISPATCH_KEY}, headers=_AUTH
    )
    status = client.get(f"/execution-instances/{_RUN_ID}", headers=_AUTH)
    assert found.status_code == status.status_code == 200
    assert found.json()["status"] == status.json()["status"] == "failed"
    assert (
        found.json()["error_kind"]
        == status.json()["error_kind"]
        == "input_digest_mismatch"
    )


def test_digest_failure_replay_is_409_not_an_accept(
    client: TestClient, supervisor: _FakeSupervisor, tmp_path: Path
) -> None:
    # Given a dispatch already failed by the frozen-input guard.
    prepared = _prepare(client, tmp_path)
    config = tmp_path / "runs" / _RUN_ID / "config.json"
    config.chmod(0o600)
    config.write_bytes(b"tampered")
    body = _start_body(prepared)
    assert _execute(client, body).status_code == 409
    # When the same declaration is retried.
    replay = _execute(client, body)
    # Then it consistently names the original failure and never accepts work.
    assert replay.status_code == 409
    assert replay.json() == {
        "error": "input_digest_mismatch",
        "original_remote_id": _RUN_ID,
    }
    assert supervisor.calls == []


def test_declaration_run_dir_swap_cannot_verify_another_run(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    tmp_path: Path,
) -> None:
    # Given a second valid run whose bytes match the declaration before drift.
    prepared = _prepare(client, tmp_path)
    original = tmp_path / "runs" / _RUN_ID
    alternate = tmp_path / "runs" / "mcp-long-other-2"
    (alternate / "code").mkdir(parents=True)
    for relative in ("config.json", "code/signal_engine.py"):
        (alternate / relative).write_bytes((original / relative).read_bytes())
    row = store.get_dispatch(_DISPATCH_KEY)
    assert row is not None
    declaration = json.loads(row.declaration_json)
    declaration["run_dir"] = str(alternate)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE execution_dispatches SET declaration_json = ? WHERE dispatch_key = ?",
            (
                json.dumps(declaration, sort_keys=True, separators=(",", ":")),
                _DISPATCH_KEY,
            ),
        )
    config = original / "config.json"
    config.chmod(0o600)
    config.write_bytes(b"tampered")
    # When recheck could otherwise validate the alternate instead of the bound row.
    response = _execute(client, _start_body(prepared))
    # Then declaration/row disagreement fails before any start claim or spawn.
    assert response.status_code == 409
    assert response.json() == {
        "error": "input_digest_mismatch",
        "original_remote_id": _RUN_ID,
    }
    assert supervisor.calls == []
    row = store.get_dispatch(_DISPATCH_KEY)
    assert row is not None and row.status == "failed" and row.start_digest is None


def test_terminal_cas_race_is_not_misreported_as_digest_drift(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given drift plus a competing terminal completion during failure CAS.
    prepared = _prepare(client, tmp_path)
    config = tmp_path / "runs" / _RUN_ID / "config.json"
    config.chmod(0o600)
    config.write_bytes(b"tampered")
    original = store.mark_dispatch_terminal

    def complete_first(
        dispatch_key: str, status: str, error_kind: str | None = None
    ) -> None:
        assert status == "failed" and error_kind == "input_digest_mismatch"
        original(dispatch_key, "completed")
        raise DispatchTransitionError(dispatch_key, "completed", status, "test race")

    monkeypatch.setattr(store, "mark_dispatch_terminal", complete_first)
    # When the terminal CAS loses to that completion.
    response = _execute(client, _start_body(prepared))
    # Then begin_start reports its real conflict instead of inventing drift provenance.
    assert response.status_code == 409
    assert response.json() == {
        "error": "idempotency_conflict",
        "original_remote_id": _RUN_ID,
    }
    assert supervisor.calls == []
    row = store.get_dispatch(_DISPATCH_KEY)
    assert row is not None and row.status == "completed" and row.error_kind is None


def test_unavailable_data_digest_does_not_block_execute(
    client: TestClient, supervisor: _FakeSupervisor, tmp_path: Path
) -> None:
    # Given a non-local declaration whose data digest is honestly unavailable.
    prepared = _prepare(client, tmp_path, source="clickhouse")
    assert prepared["data_digest"] is None
    assert prepared["data_digest_status"] == "unavailable"
    # When execute rechecks the available config/source/input proofs.
    response = _execute(client, _start_body(prepared))
    # Then unavailable data provenance alone is not a fabricated verification failure.
    assert response.status_code == 202
    assert len(supervisor.calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "factor"},
        {"unexpected": "value"},
        {"build_fingerprint": "bad"},
        {"prompt": "rewrite config.json"},
    ],
)
def test_invalid_execute_body_is_fixed_400(
    changes: dict[str, JsonValue],
    client: TestClient,
    supervisor: _FakeSupervisor,
    tmp_path: Path,
) -> None:
    # Given a prepared declaration with an invalid execute-only field.
    prepared = _prepare(client, tmp_path)
    # When the strict route/service boundary parses it.
    response = _execute(client, _start_body(prepared), **changes)
    # Then validation detail and execution side effects stay contained.
    assert response.status_code == 400 and response.json() == {
        "error": "invalid_request"
    }
    assert supervisor.calls == []


@pytest.mark.parametrize(
    ("headers", "code", "body"),
    [
        ({}, 401, {"detail": "unauthorized"}),
        ({"Authorization": "Bearer wrong"}, 403, {"detail": "forbidden"}),
    ],
)
def test_execute_requires_service_token(
    headers: dict[str, str],
    code: int,
    body: dict[str, str],
    client: TestClient,
    supervisor: _FakeSupervisor,
    tmp_path: Path,
) -> None:
    # Given a valid execute body but no valid credential.
    prepared = _prepare(client, tmp_path)
    # When authentication runs before the provider.
    response = client.post(
        "/backtest/execute", json=_start_body(prepared), headers=headers
    )
    # Then the fixed auth body contains neither the token nor worker side effects.
    assert response.status_code == code and response.json() == body
    assert _TOKEN not in response.text and supervisor.calls == []


def test_missing_prepare_is_409(client: TestClient, tmp_path: Path) -> None:
    # Given a well-formed start declaration with no dispatch row.
    run_dir = str((tmp_path / "runs" / _RUN_ID).resolve())
    body = _start_body(
        {
            "run_dir": run_dir,
            "input_digest": "a" * 64,
            "config_sha256": "b" * 64,
            "source_sha256": "c" * 64,
        }
    )
    # When execute cannot find its prepare binding.
    response = _execute(client, body)
    # Then it refuses to invent an execution.
    assert response.status_code == 409 and response.json() == {
        "error": "prepare_required"
    }


def test_execute_rejects_abbreviated_legacy_declaration(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    tmp_path: Path,
) -> None:
    # Given a pre-execute declaration without full prepare/data provenance.
    run_dir = str((tmp_path / "runs" / _RUN_ID).resolve())
    declaration = {
        "run_dir": run_dir,
        "input_digest": "a" * 64,
        "config_sha256": "b" * 64,
        "source_sha256": "c" * 64,
    }
    store.claim_dispatch(
        _DISPATCH_KEY, "backtest", declaration, _RUN_ID, CONTRACT_VERSION
    )
    # When the strict execute boundary receives its matching start body.
    response = _execute(client, _start_body(declaration))
    # Then legacy generic-start compatibility cannot bypass frozen verification.
    assert response.status_code == 409
    assert response.json() == {"error": "prepare_required"}
    assert supervisor.calls == []


def test_binding_mismatch_returns_original_id(
    client: TestClient, supervisor: _FakeSupervisor, tmp_path: Path
) -> None:
    # Given a prepared row and a start naming another remote id.
    prepared = _prepare(client, tmp_path)
    # When the prepared binding check fails.
    response = _execute(client, _start_body(prepared), run_id="mcp-long-other-2")
    # Then the conflict identifies only the original execution.
    assert response.status_code == 409
    assert response.json() == {
        "error": "idempotency_conflict",
        "original_remote_id": _RUN_ID,
    }
    assert supervisor.calls == []


def test_spawn_failure_is_fixed_503_without_secret_leakage(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    tmp_path: Path,
) -> None:
    # Given a verified declaration whose supervisor rejects the spawn.
    prepared = _prepare(client, tmp_path)
    supervisor.error = RuntimeError("secret executable path")
    # When the new start claim cannot launch its worker.
    response = _execute(client, _start_body(prepared))
    # Then the fixed failure leaves an unknown dispatched row, not a terminal success.
    assert response.status_code == 503 and response.json() == {
        "error": "worker_start_failed"
    }
    assert "secret executable" not in response.text
    row = store.get_dispatch(_DISPATCH_KEY)
    assert (
        row is not None and row.status == "dispatched" and row.start_digest is not None
    )


def test_unknown_result_identity_is_404(client: TestClient) -> None:
    # Given no execution with the requested id.
    response = client.get("/execution-instances/missing/result", headers=_AUTH)
    # When the read projection resolves it Then the fixed 404 is returned.
    assert response.status_code == 404 and response.json() == {"error": "not_found"}


def test_completed_backtest_result_preserves_native_summary_and_provenance(
    client: TestClient, store: ControlStore, tmp_path: Path
) -> None:
    # Given a completed worker with native run-card, equity/trades/metrics/positions artifacts.
    prepared = _prepare(client, tmp_path)
    assert _execute(client, _start_body(prepared)).status_code == 202
    run_dir = tmp_path / "runs" / _RUN_ID
    _write_native_results(run_dir)
    store.mark_dispatch_terminal(
        _DISPATCH_KEY, "completed", result_path=str(run_dir / "worker_result.json")
    )
    # When the native result is read through the public projection.
    response = client.get(f"/execution-instances/{_RUN_ID}/result", headers=_AUTH)
    # Then summary 1.0, run-card artifacts, and every prepare digest survive intact.
    assert response.status_code == 200, response.text
    outer = response.json()
    assert outer["status"] == "completed" and outer["result_status"] == "available"
    assert outer["has_more"] is False
    result = outer["result"]
    assert set(result) == {
        "remote_id",
        "dispatch_key",
        "contract_version",
        "input_digest",
        "config_sha256",
        "source_sha256",
        "data_digest",
        "data_digest_status",
        "summary",
        "artifacts",
    }
    for key in ("input_digest", "config_sha256", "source_sha256", "data_digest"):
        assert result[key] == prepared[key]
    assert result["remote_id"] == _RUN_ID and result["dispatch_key"] == _DISPATCH_KEY
    assert result["contract_version"] == CONTRACT_VERSION
    assert result["data_digest_status"] == "verified"
    summary = result["summary"]
    assert summary["schema_version"] == "1.0"
    assert {
        "run_id",
        "metrics",
        "equity_preview",
        "codes",
        "start_date",
        "end_date",
        "interval",
        "artifact_paths",
    } <= set(summary)
    assert summary["metrics"] == {"total_return": 0.1, "sharpe": 1.5}
    assert summary["equity_preview"] == [
        {
            "time": "2026-01-01",
            "equity": 100000.0,
            "drawdown": 0.0,
            "benchmark_equity": 100000.0,
        },
        {
            "time": "2026-01-02",
            "equity": 110000.0,
            "drawdown": 0.0,
            "benchmark_equity": 105000.0,
        },
    ]
    card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    assert result["artifacts"] == card["artifacts"]
    assert {
        "artifacts/equity.csv",
        "artifacts/trades.csv",
        "artifacts/metrics.csv",
        "artifacts/positions.csv",
    } <= {item["path"] for item in result["artifacts"]}


@pytest.mark.parametrize(
    "scenario", ["not-complete", "missing-summary", "escaped-artifact"]
)
def test_backtest_result_is_unavailable_without_complete_valid_native_output(
    scenario: str, client: TestClient, store: ControlStore, tmp_path: Path
) -> None:
    # Given an incomplete run, missing summary, or artifact path escaping its run.
    prepared = _prepare(client, tmp_path)
    assert _execute(client, _start_body(prepared)).status_code == 202
    run_dir = tmp_path / "runs" / _RUN_ID
    if scenario != "missing-summary":
        _write_native_results(run_dir)
    if scenario == "escaped-artifact":
        card_path = run_dir / "run_card.json"
        card = json.loads(card_path.read_text(encoding="utf-8"))
        card["artifacts"][0]["path"] = "../outside.json"
        card_path.write_text(json.dumps(card), encoding="utf-8")
    if scenario != "not-complete":
        store.mark_dispatch_terminal(_DISPATCH_KEY, "completed")
    # When its result is requested.
    response = client.get(f"/execution-instances/{_RUN_ID}/result", headers=_AUTH)
    # Then no fake available envelope is produced.
    assert response.status_code == 200
    assert response.json()["result_status"] == "unavailable"
    assert response.json()["result"] is None and response.json()["has_more"] is False


def test_authenticated_prepare_find_execute_result_chain_preserves_contract(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    tmp_path: Path,
) -> None:
    # Given a prepared local declaration read back through the recovery route.
    prepared = _prepare(client, tmp_path)
    found_before = client.get(
        "/execution-instances", params={"dispatch_key": _DISPATCH_KEY}, headers=_AUTH
    )
    # When the same declaration is executed exactly once and finishes natively.
    assert found_before.status_code == 200, found_before.text
    assert found_before.json()["state"] == "dispatched"
    assert found_before.json()["remote_id"] == _RUN_ID
    assert found_before.json()["status"] == "unknown"
    accepted = _execute(client, _start_body(prepared))
    run_dir = tmp_path / "runs" / _RUN_ID
    _write_native_results(run_dir)
    store.mark_dispatch_terminal(_DISPATCH_KEY, "completed")
    found_after = client.get(
        "/execution-instances", params={"dispatch_key": _DISPATCH_KEY}, headers=_AUTH
    )
    status = client.get(f"/execution-instances/{_RUN_ID}", headers=_AUTH)
    result = client.get(f"/execution-instances/{_RUN_ID}/result", headers=_AUTH)
    # Then every authenticated read agrees on identity, summary and CSV provenance.
    assert accepted.status_code == 202 and accepted.json()["remote_id"] == _RUN_ID
    assert len(supervisor.calls) == 1
    assert found_after.status_code == status.status_code == result.status_code == 200
    assert found_after.json()["status"] == status.json()["status"] == "completed"
    outer = result.json()
    assert outer["result_status"] == "available" and outer["has_more"] is False
    summary = outer["result"]["summary"]
    assert summary["schema_version"] == "1.0"
    assert {
        "run_id",
        "metrics",
        "equity_preview",
        "codes",
        "start_date",
        "end_date",
        "interval",
        "artifact_paths",
    } <= set(summary)
    assert summary["run_id"] == _RUN_ID
    assert summary["codes"] == ["AAPL.US"]
    assert summary["start_date"] == "2026-01-01"
    assert summary["end_date"] == "2026-01-02"
    assert summary["interval"] == "1D"
    expected_csvs = {
        "artifacts/metrics.csv",
        "artifacts/equity.csv",
        "artifacts/trades.csv",
        "artifacts/positions.csv",
    }
    artifacts = outer["result"]["artifacts"]
    assert expected_csvs <= {item["path"] for item in artifacts}
    for item in artifacts:
        path = item["path"]
        assert not Path(path).is_absolute() and ".." not in Path(path).parts
        payload = (run_dir / path).read_bytes()
        assert item["size_bytes"] == len(payload)
        assert item["sha256"] == hashlib.sha256(payload).hexdigest()
    for key in ("input_digest", "config_sha256", "source_sha256", "data_digest"):
        assert outer["result"][key] == prepared[key]
