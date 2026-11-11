"""Real offline prepare/execute/result integration for frozen backtests.

# noqa: SIZE_OK - one mandated module covers real execution and both refusals.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest
import yaml
from fastapi.testclient import TestClient
from httpx import Response
from pytest_socket import disable_socket, enable_socket

import strategy_run_server
import backtest.runner as backtest_runner
from backtest.loaders import local_loader
from src.strategy_library import paths as strategy_paths
from src.strategy_runs.backtest_materialize import BacktestMaterializer
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    JsonValue,
    backtest_primitive_prompt,
)
from src.strategy_runs.control_service import ExecutionControlService
from src.strategy_runs.control_store import ControlStore
from src.strategy_runs.execution_start_service import ExecutionStartService
from src.strategy_runs.native_supervisor import NativeSupervisor

_AGENT_ROOT: Final = Path(__file__).resolve().parents[1]
_FIXTURE_DIR: Final = Path(__file__).parent / "fixtures" / "execution_control"
_TOKEN: Final = "offline-integration-token"
_AUTH: Final = {"Authorization": f"Bearer {_TOKEN}"}
_STRATEGY_ID: Final = "local-cn-neutral"
_VERSION: Final = "v1-abcdef12"
_ENGINE_MARKER: Final = "VT_LOCAL_ENGINE_GENERATE"
_REQUIRED_CSVS: Final = ("metrics.csv", "equity.csv", "trades.csv", "positions.csv")
_HAPPY: Final = ("mcp-long-local-happy-1", "local.happy.1")
_TAMPER: Final = ("mcp-long-local-tamper-2", "local.tamper.2")
_CHRONOLOGY: Final = ("mcp-long-local-chronology-3", "local.chronology.3")
_OFFLINE_GUARD: Final = """import socket


def _blocked(*args, **kwargs):
    raise OSError("network disabled by offline backtest integration")


class _OfflineSocket(socket.socket):
    def connect(self, *args, **kwargs):
        return _blocked()

    def connect_ex(self, *args, **kwargs):
        return _blocked()


socket.socket = _OfflineSocket
socket.create_connection = _blocked
"""


@dataclass(frozen=True, slots=True)
class _Harness:
    client: TestClient
    store: ControlStore
    supervisor: NativeSupervisor
    tmp_path: Path
    source_path: Path
    bridge_path: Path
    watchers: list[threading.Thread]


@dataclass(frozen=True, slots=True)
class _Prepared:
    run_id: str
    dispatch_key: str
    run_dir: Path
    declaration: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class _Completed:
    prepared: _Prepared
    result_body: dict[str, JsonValue]
    file_state: dict[str, tuple[str, int]]
    worker_pid: int
    worker_pgid: int


@pytest.fixture(autouse=True)
def _offline_parent() -> Iterator[None]:
    """Block IP networking in the parent while allowing TestClient's local wakeup."""
    disable_socket(allow_unix_socket=True)
    yield
    enable_socket()


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Harness]:
    """Build the real HTTP/services stack around one native supervisor."""
    home = tmp_path / "home"
    bridge = home / ".vibe-trading" / "data-bridge" / "config.yaml"
    bridge.parent.mkdir(parents=True)
    source_path = tmp_path / "sources" / "000001.SZ.csv"
    source_path.parent.mkdir()
    fixture_bytes = (_FIXTURE_DIR / "000001.SZ.csv").read_bytes()
    source_path.write_bytes(fixture_bytes)
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
    version_config = strategy_paths.version_config_path(
        _STRATEGY_ID, _VERSION, runtime_root=runtime
    )
    version_code = strategy_paths.version_code_path(
        _STRATEGY_ID, _VERSION, runtime_root=runtime
    )
    version_code.parent.mkdir(parents=True)
    version_config.write_text('{"frozen":true}\n', encoding="utf-8")
    version_code.write_bytes((_FIXTURE_DIR / "code" / "signal_engine.py").read_bytes())

    store = ControlStore(tmp_path / "ledger.sqlite3")
    watchers: list[threading.Thread] = []

    def thread_factory(
        *,
        target: Callable[..., None],
        args: tuple,
        name: str,
        daemon: bool,
    ) -> threading.Thread:
        thread = threading.Thread(target=target, args=args, name=name, daemon=daemon)
        watchers.append(thread)
        return thread

    supervisor = NativeSupervisor(store, thread_factory=thread_factory)
    reader = ExecutionControlService(store, handle_provider=supervisor.handle_is_live)
    materializer = BacktestMaterializer(store, runtime_root=runtime)
    starter = ExecutionStartService(store, supervisor)
    app = strategy_run_server.create_app(
        lambda: object(),
        control_service_provider=lambda: reader,
        prepare_provider=lambda: materializer,
        execution_start_service_provider=lambda: starter,
    )
    with TestClient(app) as client:
        yield _Harness(
            client, store, supervisor, tmp_path, source_path, bridge, watchers
        )
    supervisor.shutdown()


def _canonical(payload: JsonValue) -> bytes:
    """Independently encode the digest documents without production helpers."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _config(changes: dict[str, JsonValue] | None = None) -> dict[str, JsonValue]:
    config: dict[str, JsonValue] = {
        "strategy_id": _STRATEGY_ID,
        "strategy_version": _VERSION,
        "market": "CN_A",
        "symbols": ["local:000001.SZ"],
        "start_date": "2026-01-05",
        "end_date": "2026-01-09",
        "bar_interval": "1d",
        "data_source": "local",
        "data_as_of": "2026-01-09",
        "snapshot_basis": "sanitized-local-csv-v1",
        "initial_cash": "10000.00",
        "currency": "CNY",
        "commission_bps": "2.50",
        "tax_bps": "5.00",
        "slippage_bps": "20.00",
    }
    if changes:
        config.update(changes)
    return config


def _post_prepare(
    harness: _Harness,
    identity: tuple[str, str],
    config: dict[str, JsonValue],
) -> Response:
    run_id, dispatch_key = identity
    run_dir = (harness.tmp_path / "runs" / run_id).resolve()
    source_bytes = (_FIXTURE_DIR / "code" / "signal_engine.py").read_bytes()
    return harness.client.post(
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


def _prepare(
    harness: _Harness,
    identity: tuple[str, str],
    changes: dict[str, JsonValue] | None = None,
) -> _Prepared:
    config = _config(changes)
    response = _post_prepare(harness, identity, config)
    assert response.status_code == 200, response.text
    declaration = response.json()
    run_id, dispatch_key = identity
    prepared = _Prepared(
        run_id,
        dispatch_key,
        (harness.tmp_path / "runs" / run_id).resolve(),
        declaration,
    )
    _assert_independent_prepare(harness, prepared, config)
    return prepared


def _assert_independent_prepare(
    harness: _Harness, prepared: _Prepared, config: dict[str, JsonValue]
) -> None:
    """Recompute D-3 and local-data identity from request and fixture bytes."""
    config_bytes = _canonical(config)
    source_bytes = (_FIXTURE_DIR / "code" / "signal_engine.py").read_bytes()
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    input_digest = hashlib.sha256(
        _canonical(
            {
                "schema": "backtest-input-v1",
                "config_sha256": config_sha256,
                "source_sha256": source_sha256,
                "data_source": config["data_source"],
                "data_as_of": config["data_as_of"],
                "snapshot_basis": config["snapshot_basis"],
            }
        )
    ).hexdigest()
    relative = Path(
        os.path.relpath(
            harness.source_path.resolve(), harness.bridge_path.parent.resolve()
        )
    ).as_posix()
    source_data_sha256 = hashlib.sha256(harness.source_path.read_bytes()).hexdigest()
    data_digest = hashlib.sha256(
        _canonical(
            {
                "schema": "backtest-local-data-v1",
                "files": [[relative, source_data_sha256]],
            }
        )
    ).hexdigest()
    expected = {
        "config_sha256": config_sha256,
        "source_sha256": source_sha256,
        "input_digest": input_digest,
        "data_digest": data_digest,
        "data_digest_status": "verified",
    }
    for key, value in expected.items():
        assert prepared.declaration[key] == value
    assert (prepared.run_dir / "config.json").read_bytes() == config_bytes
    assert (prepared.run_dir / "code" / "signal_engine.py").read_bytes() == source_bytes


def _start_body(prepared: _Prepared) -> dict[str, JsonValue]:
    return {
        "dispatch_key": prepared.dispatch_key,
        "kind": "backtest",
        "run_id": prepared.run_id,
        "run_dir": str(prepared.run_dir),
        "input_digest": prepared.declaration["input_digest"],
        "config_sha256": prepared.declaration["config_sha256"],
        "source_sha256": prepared.declaration["source_sha256"],
        "build_fingerprint": "d" * 64,
        "prompt": backtest_primitive_prompt(str(prepared.run_dir)),
        "contract_version": CONTRACT_VERSION,
    }


def _execute(harness: _Harness, prepared: _Prepared) -> Response:
    return harness.client.post(
        "/backtest/execute", json=_start_body(prepared), headers=_AUTH
    )


def _wait_for_worker(harness: _Harness, prepared: _Prepared) -> None:
    """Join the real watcher thread, never sleep or poll for process completion."""
    assert len(harness.watchers) == 1
    harness.watchers[0].join(timeout=60)
    assert not harness.watchers[0].is_alive(), "native engine did not finish"
    row = harness.store.get_dispatch(prepared.dispatch_key)
    assert row is not None and row.status == "completed", (
        harness.tmp_path / "runs" / prepared.run_id / "worker.log"
    ).read_text(encoding="utf-8")


def _get_result(harness: _Harness, run_id: str) -> dict[str, JsonValue]:
    response = harness.client.get(
        f"/execution-instances/{run_id}/result", headers=_AUTH
    )
    assert response.status_code == 200, response.text
    return response.json()


def _file_state(run_dir: Path) -> dict[str, tuple[str, int]]:
    return {
        path.relative_to(run_dir).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in run_dir.rglob("*")
        if path.is_file()
    }


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _assert_artifacts(harness: _Harness, completed: _Prepared) -> dict[str, str]:
    """Require native evidence rows and independently hash the manifested CSVs."""
    outer = _get_result(harness, completed.run_id)
    assert outer["status"] == "completed" and outer["result_status"] == "available"
    result = outer["result"]
    assert result["summary"]["schema_version"] == "1.0"
    assert result["data_digest_status"] == "verified"
    for key in ("input_digest", "config_sha256", "source_sha256", "data_digest"):
        assert result[key] == completed.declaration[key]

    card = json.loads((completed.run_dir / "run_card.json").read_text(encoding="utf-8"))
    assert card["data_sources"] == ["local"]
    assert result["artifacts"] == card["artifacts"]
    manifest = {entry["path"]: entry["sha256"] for entry in result["artifacts"]}
    hashes = {}
    for name in _REQUIRED_CSVS:
        path = completed.run_dir / "artifacts" / name
        assert _rows(path), f"{name} has no evidence rows"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert manifest[f"artifacts/{name}"] == digest
        hashes[name] = digest
    metrics = _rows(completed.run_dir / "artifacts" / "metrics.csv")
    assert len(metrics) == 1 and Decimal(metrics[0]["trade_count"]) == 1
    assert Decimal(metrics[0]["final_value"]) > 0
    assert Decimal(metrics[0]["final_value"]) != Decimal("10000")
    equity = _rows(completed.run_dir / "artifacts" / "equity.csv")
    assert len(equity) > 1
    assert all(Decimal(row["equity"]) > 0 for row in equity)
    assert equity[0]["equity"] != equity[-1]["equity"]
    positions = _rows(completed.run_dir / "artifacts" / "positions.csv")
    assert any(Decimal(row["000001.SZ"]) != 0 for row in positions)
    assert len(_rows(completed.run_dir / "artifacts" / "trades.csv")) == 2
    return hashes


def _assert_neutral_costs(run_dir: Path, source_path: Path) -> list[str]:
    """Recompute fixture-driven slippage and fees from declared bps only."""
    trades = _rows(run_dir / "artifacts" / "trades.csv")
    assert [(row["event"], Decimal(row["qty"])) for row in trades] == [
        ("entry", Decimal(100)),
        ("exit", Decimal(100)),
    ]
    fill_lines = (
        (run_dir / "artifacts" / "fills.jsonl").read_text(encoding="utf-8").splitlines()
    )
    fills = [json.loads(line) for line in fill_lines]
    assert [fill["action"] for fill in fills] == ["open", "close"]
    source_by_date = {row["date"]: row for row in _rows(source_path)}
    fees = []
    for fill in fills:
        direction = 1 if Decimal(str(fill["signed_quantity"])) > 0 else -1
        raw_open = Decimal(source_by_date[fill["timestamp"][:10]]["open"])
        expected_price = raw_open * (
            Decimal(1) + Decimal(direction) * Decimal("20") / Decimal(10000)
        )
        execution_price = Decimal(str(fill["execution_price"]))
        assert execution_price == expected_price
        notional = abs(Decimal(str(fill["signed_quantity"]))) * execution_price
        commission = notional * Decimal("2.50") / Decimal(10000)
        if fill["action"] == "close":
            commission += notional * Decimal("5.00") / Decimal(10000)
        actual = Decimal(str(fill["fee"]))
        assert actual == commission
        fees.append(str(actual))
    assert Decimal(fees[0]) < Decimal("5"), "native CN minimum commission leaked in"
    assert fees == ["0.2505", "0.7485"], "transfer fee or native fee defaults leaked in"
    return fees


def _run_happy(harness: _Harness, *, emit_evidence: bool = True) -> _Completed:
    """Execute one real run, verify its result, then prove same-key replay."""
    prepared = _prepare(harness, _HAPPY)
    accepted = _execute(harness, prepared)
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["replayed"] is False
    started = harness.store.get_dispatch(prepared.dispatch_key)
    assert started is not None and started.worker_pid is not None
    assert started.worker_pgid is not None
    _wait_for_worker(harness, prepared)

    assert json.loads(
        (prepared.run_dir / "state.json").read_text(encoding="utf-8")
    ) == {"status": "success"}
    terminal = json.loads((prepared.run_dir / "worker_result.json").read_text())
    assert terminal["status"] == "completed" and terminal["exit_code"] == 0
    hashes = _assert_artifacts(harness, prepared)
    fees = _assert_neutral_costs(prepared.run_dir, harness.source_path)
    before = _file_state(prepared.run_dir)
    result_before = _get_result(harness, prepared.run_id)
    row_before = harness.store.get_dispatch(prepared.dispatch_key)

    replay = _execute(harness, prepared)
    assert replay.status_code == 202 and replay.json()["replayed"] is True
    assert replay.json()["remote_id"] == prepared.run_id
    assert _file_state(prepared.run_dir) == before
    assert harness.store.get_dispatch(prepared.dispatch_key) == row_before
    assert _get_result(harness, prepared.run_id) == result_before
    assert len(harness.watchers) == 1
    worker_log = (prepared.run_dir / "worker.log").read_text(encoding="utf-8")
    assert worker_log.count(_ENGINE_MARKER) == 1

    completed = _Completed(
        prepared,
        result_before,
        before,
        started.worker_pid,
        started.worker_pgid,
    )
    if emit_evidence:
        print(
            "HAPPY_EVIDENCE",
            json.dumps(
                {
                    "worker_pid": completed.worker_pid,
                    "worker_pgid": completed.worker_pgid,
                    "worker_thread_count": len(harness.watchers),
                    "engine_marker_count": 1,
                    "replayed_remote_id": replay.json()["remote_id"],
                    "state": "success",
                    "summary_schema": "1.0",
                    "data_sources": ["local"],
                    "data_digest_status": "verified",
                    "config_sha256": prepared.declaration["config_sha256"],
                    "source_sha256": prepared.declaration["source_sha256"],
                    "input_digest": prepared.declaration["input_digest"],
                    "data_digest": prepared.declaration["data_digest"],
                    "artifact_sha256": hashes,
                    "fill_fees": fees,
                },
                sort_keys=True,
            ),
        )
    return completed


def test_real_offline_prepare_execute_result_and_same_key_replay(
    harness: _Harness,
) -> None:
    # Given sanitized CSV/strategy fixtures and the unmodified native services.
    # When a frozen declaration executes and the identical key is replayed.
    completed = _run_happy(harness)
    # Then one native engine produced all evidence and replay changed no byte.
    assert (
        completed.file_state["state.json"][0]
        == hashlib.sha256(b'{\n  "status": "success"\n}').hexdigest()
    )
    assert not list(completed.prepared.run_dir.rglob("*.html"))


def test_source_tamper_and_chronology_refuse_before_new_engine(
    harness: _Harness,
) -> None:
    # Given one intact completed result whose artifacts and remote id are known.
    completed = _run_happy(harness, emit_evidence=False)
    happy_result = completed.result_body
    happy_files = completed.file_state

    # When a second prepared declaration's source CSV changes before execute.
    tampered = _prepare(harness, _TAMPER)
    tampered_files = _file_state(tampered.run_dir)
    harness.source_path.write_bytes(
        harness.source_path.read_bytes() + b"# post-prepare tamper\n"
    )
    refusal = _execute(harness, tampered)

    # Then the durable digest refusal precedes every engine/worker side effect.
    assert refusal.status_code == 409
    assert refusal.json() == {
        "error": "input_digest_mismatch",
        "original_remote_id": tampered.run_id,
        "contract_version": CONTRACT_VERSION,
    }
    row = harness.store.get_dispatch(tampered.dispatch_key)
    assert row is not None and row.status == "failed"
    assert row.error_kind == "input_digest_mismatch"
    assert row.start_digest is None and row.worker_pid is None
    assert _file_state(tampered.run_dir) == tampered_files
    assert not (tampered.run_dir / "worker.log").exists()
    assert not (tampered.run_dir / "state.json").exists()
    assert _file_state(completed.prepared.run_dir) == happy_files
    assert _get_result(harness, completed.prepared.run_id) == happy_result

    # When a pristine fixture would expose its real 2026-01-09 row beyond as_of.
    harness.source_path.write_bytes((_FIXTURE_DIR / "000001.SZ.csv").read_bytes())
    fixture_dates = [row["date"] for row in _rows(harness.source_path)]
    assert max(fixture_dates) == "2026-01-09"
    chronology = _post_prepare(
        harness, _CHRONOLOGY, _config({"data_as_of": "2026-01-08"})
    )
    chronology_run = harness.tmp_path / "runs" / _CHRONOLOGY[0]

    # Then validation refuses before claim, loader, engine, or post-as_of consumption.
    assert chronology.status_code == 400
    assert chronology.json() == {
        "error": "invalid_input",
        "contract_version": CONTRACT_VERSION,
    }
    assert harness.store.get_dispatch(_CHRONOLOGY[1]) is None
    assert not chronology_run.exists()
    assert len(harness.watchers) == 1
    worker_logs = list((harness.tmp_path / "runs").rglob("worker.log"))
    assert len(worker_logs) == 1
    assert worker_logs[0].read_text(encoding="utf-8").count(_ENGINE_MARKER) == 1
    assert _file_state(completed.prepared.run_dir) == happy_files
    print(
        "FAILURE_EVIDENCE",
        json.dumps(
            {
                "source_tamper_http": 409,
                "source_tamper_dispatch": [row.status, row.error_kind],
                "source_tamper_original_remote_id": refusal.json()[
                    "original_remote_id"
                ],
                "source_tamper_worker_pid": row.worker_pid,
                "chronology_http": 400,
                "chronology_dispatch_absent": True,
                "chronology_source_restored": True,
                "post_as_of_fixture_date": max(fixture_dates),
                "worker_thread_count": len(harness.watchers),
                "new_engine_marker_count": 0,
                "original_result_intact": True,
            },
            sort_keys=True,
        ),
    )


def test_direct_runner_failure_persists_terminal_state(harness: _Harness) -> None:
    # Given a real direct CLI run whose neutral config fails native validation.
    run_dir = harness.tmp_path / "runs" / "mcp-long-local-invalid-4"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text("{}\n", encoding="utf-8")

    # When the unpatched runner rejects it before any loader or engine work.
    with pytest.raises(SystemExit) as exited:
        backtest_runner.main(run_dir)

    # Then the direct entrypoint leaves the same durable failure contract.
    assert exited.value.code == 1
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed" and "SystemExit" in state["reason"]
    progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["stage"] == "failed"
    assert harness.watchers == []
