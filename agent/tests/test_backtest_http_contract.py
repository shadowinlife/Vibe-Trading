"""Route-level negative matrix and redaction for the frozen backtest surface.

Pins the exact wire contract for prepare/execute/find/status/result: fixed
401/403/400/404/409/503 bodies, no token or filesystem detail in responses or
logs, and honest ``unavailable`` results for oversized native metadata without
dropping a bounded summary.

# noqa: SIZE_OK - one public route matrix; splitting would hide the auth/error mapping.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
import yaml
from fastapi.testclient import TestClient

import strategy_run_server
from backtest.loaders import local_loader
from backtest.run_card import write_run_card
from src.api import strategy_run_auth
from src.strategy_library import paths as strategy_paths
from src.strategy_runs.backtest_materialize import BacktestMaterializer
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    JsonValue,
    backtest_primitive_prompt,
)
from src.strategy_runs.control_service import ExecutionControlService
from src.strategy_runs.control_store import ControlStore, DispatchRow
from src.strategy_runs.execution_start_service import ExecutionStartService
from src.strategy_runs.execution_worker import WorkerSpec

_TOKEN: Final = "backtest-http-contract-token"
_AUTH: Final = {"Authorization": f"Bearer {_TOKEN}"}
_DISPATCH_KEY: Final = "user-1.job-9.1"
_RUN_ID: Final = "mcp-long-job-9-1"
_STRATEGY_ID: Final = "momentum-alpha"
_VERSION: Final = "v1-abcdef12"
_SOURCE: Final = "class SignalEngine:\n    pass\n"
_SECRET: Final = (
    "sqlite3.OperationalError: SELECT * FROM secret_table "
    "/secret/absolute/path token=super-secret"
)
_ROUTES: Final = ("prepare", "execute", "find", "status", "result")
_READ_ROUTES: Final = ("find", "status", "result")
_PREPARE_INVALID: Final = (
    "dispatch_key_chars",
    "dispatch_key_empty",
    "run_dir_relative",
    "run_dir_outside",
    "run_dir_name",
    "source_digest",
    "source_mismatch",
    "source_empty",
    "config_missing",
    "config_extra",
    "chronology",
    "provenance",
    "contract_version",
)
_EXECUTE_INVALID: Final = (
    "dispatch_key",
    "run_id",
    "run_dir",
    "input_digest",
    "config_sha256",
    "source_sha256",
    "build_fingerprint",
    "prompt",
    "kind",
    "contract_version",
    "extra",
)


class _FakeSupervisor:
    def __init__(self, store: ControlStore) -> None:
        self._store = store
        self.calls: list[WorkerSpec] = []

    def start(self, dispatch_key: str, spec: WorkerSpec) -> DispatchRow:
        self.calls.append(spec)
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
    app = _app(store, supervisor, tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def _app(
    store: ControlStore,
    supervisor: _FakeSupervisor,
    tmp_path: Path,
) -> object:
    materializer = BacktestMaterializer(store, runtime_root=tmp_path / "runtime")
    starter = ExecutionStartService(store, supervisor)
    reader = ExecutionControlService(store)
    return strategy_run_server.create_app(
        lambda: object(),
        control_service_provider=lambda: reader,
        prepare_provider=lambda: materializer,
        execution_start_service_provider=lambda: starter,
    )


def _config() -> dict[str, JsonValue]:
    return {
        "strategy_id": _STRATEGY_ID,
        "strategy_version": _VERSION,
        "market": "us",
        "symbols": ["AAPL.US"],
        "start_date": "2026-01-01",
        "end_date": "2026-01-02",
        "bar_interval": "1d",
        "data_source": "local",
        "data_as_of": "2026-01-03",
        "snapshot_basis": "test-snapshot-v1",
        "initial_cash": "100000.00",
        "currency": "USD",
        "commission_bps": "1.5",
        "tax_bps": "5",
        "slippage_bps": "2",
    }


def _payload(tmp_path: Path) -> dict[str, JsonValue]:
    config = _config()
    return {
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
    }


def _start_body(
    prepared: dict[str, JsonValue], **changes: JsonValue
) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
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
    body.update(changes)
    return body


def _unsigned_start_body(tmp_path: Path) -> dict[str, JsonValue]:
    run_dir = str((tmp_path / "runs" / _RUN_ID).resolve())
    return {
        "dispatch_key": _DISPATCH_KEY,
        "kind": "backtest",
        "run_id": _RUN_ID,
        "run_dir": run_dir,
        "input_digest": "a" * 64,
        "config_sha256": "b" * 64,
        "source_sha256": "c" * 64,
        "build_fingerprint": "d" * 64,
        "prompt": backtest_primitive_prompt(run_dir),
        "contract_version": CONTRACT_VERSION,
    }


def _prepare(client: TestClient, tmp_path: Path) -> dict[str, JsonValue]:
    response = client.post("/backtest/prepare", json=_payload(tmp_path), headers=_AUTH)
    assert response.status_code == 200, response.text
    return response.json()


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


def _request(
    client: TestClient,
    route: str,
    tmp_path: Path,
    headers: dict[str, str],
):
    if route == "prepare":
        return client.post(
            "/backtest/prepare", json=_payload(tmp_path), headers=headers
        )
    if route == "execute":
        return client.post(
            "/backtest/execute", json=_unsigned_start_body(tmp_path), headers=headers
        )
    if route == "find":
        return client.get(
            "/execution-instances",
            params={"dispatch_key": _DISPATCH_KEY},
            headers=headers,
        )
    if route == "status":
        return client.get(f"/execution-instances/{_RUN_ID}", headers=headers)
    return client.get(f"/execution-instances/{_RUN_ID}/result", headers=headers)


def _mutate_prepare(payload: dict[str, JsonValue], case: str, tmp_path: Path) -> None:
    config = payload["config"]
    assert isinstance(config, dict)
    if case == "dispatch_key_chars":
        payload["dispatch_key"] = "bad key!"
    elif case == "dispatch_key_empty":
        payload["dispatch_key"] = ""
    elif case == "run_dir_relative":
        payload["run_dir"] = f"runs/{_RUN_ID}"
    elif case == "run_dir_outside":
        payload["run_dir"] = str((tmp_path / "outside" / _RUN_ID).resolve())
    elif case == "run_dir_name":
        payload["run_dir"] = str((tmp_path / "runs" / "arbitrary").resolve())
    elif case == "source_digest":
        payload["source"] = {"text": _SOURCE, "sha256": "z" * 64}
    elif case == "source_mismatch":
        payload["source"] = {"text": _SOURCE, "sha256": "0" * 64}
    elif case == "source_empty":
        payload["source"] = {"text": "", "sha256": "0" * 64}
    elif case == "config_missing":
        del config["market"]
    elif case == "config_extra":
        config["unexpected"] = True
    elif case == "chronology":
        config["data_as_of"] = "2026-01-01"
        payload["data_as_of"] = "2026-01-01"
    elif case == "provenance":
        payload["data_source"] = "clickhouse"
    elif case == "contract_version":
        payload["contract_version"] = "mcp-execution-control-v2"
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(case)


def _mutate_execute(body: dict[str, JsonValue], case: str) -> None:
    if case == "dispatch_key":
        body["dispatch_key"] = "bad key!"
    elif case == "run_id":
        body["run_id"] = "bad id!"
    elif case == "run_dir":
        body["run_dir"] = ""
    elif case == "input_digest":
        body["input_digest"] = "not-hex"
    elif case == "config_sha256":
        body["config_sha256"] = "A" * 64
    elif case == "source_sha256":
        body["source_sha256"] = "a" * 63
    elif case == "build_fingerprint":
        body["build_fingerprint"] = "xyz"
    elif case == "prompt":
        body["prompt"] = ""
    elif case == "kind":
        body["kind"] = "factor"
    elif case == "contract_version":
        body["contract_version"] = "mcp-execution-control-v2"
    elif case == "extra":
        body["unexpected"] = True
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(case)


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize(
    ("headers", "code", "body"),
    [
        ({}, 401, {"detail": "unauthorized"}),
        ({"Authorization": f"Basic {_TOKEN}"}, 401, {"detail": "unauthorized"}),
        ({"Authorization": "Bearer    "}, 401, {"detail": "unauthorized"}),
        ({"Authorization": "Bearer definitely-wrong"}, 403, {"detail": "forbidden"}),
    ],
)
def test_every_route_uses_fixed_service_auth(
    client: TestClient,
    route: str,
    headers: dict[str, str],
    code: int,
    body: dict[str, str],
    tmp_path: Path,
) -> None:
    # Given a valid request body and one missing, malformed or wrong credential.
    response = _request(client, route, tmp_path, headers)
    # Then the fixed auth body is returned and nothing echoes the credential.
    assert response.status_code == code, response.text
    assert response.json() == body
    assert _TOKEN not in response.text
    assert "definitely-wrong" not in response.text


@pytest.mark.parametrize("route", _ROUTES)
def test_unusable_token_configuration_is_fixed_403(
    client: TestClient,
    route: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the service token cannot be read for any of the four documented reasons.
    monkeypatch.delenv("RM_VT_STRATEGY_RUN_TOKEN_FILE", raising=False)
    response = _request(client, route, tmp_path, _AUTH)
    assert response.status_code == 403 and response.json() == {"detail": "forbidden"}
    monkeypatch.setenv("RM_VT_STRATEGY_RUN_TOKEN_FILE", str(tmp_path / "missing"))
    assert _request(client, route, tmp_path, _AUTH).status_code == 403
    empty = tmp_path / "empty-token"
    empty.write_text("\r\n", encoding="utf-8")
    monkeypatch.setenv("RM_VT_STRATEGY_RUN_TOKEN_FILE", str(empty))
    assert _request(client, route, tmp_path, _AUTH).status_code == 403
    directory = tmp_path / "token-directory"
    directory.mkdir()
    monkeypatch.setenv("RM_VT_STRATEGY_RUN_TOKEN_FILE", str(directory))
    response = _request(client, route, tmp_path, _AUTH)
    assert response.status_code == 403 and response.json() == {"detail": "forbidden"}


def test_token_never_appears_in_logs_or_response_bodies(
    client: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Given DEBUG logging and the mounted token file.
    caplog.set_level(logging.DEBUG)
    token_path = tmp_path / "strategy-run-token"
    # When refusals and a configuration announcement are observed.
    responses = [_request(client, route, tmp_path, {}) for route in _ROUTES] + [
        _request(client, route, tmp_path, {"Authorization": "Bearer wrong"})
        for route in _ROUTES
    ]
    strategy_run_auth.log_token_configuration()
    # Then neither the token nor its mount path reaches a body or a log line.
    assert _TOKEN not in caplog.text
    assert str(token_path) not in caplog.text
    assert "wrong" not in caplog.text
    for response in responses:
        assert _TOKEN not in response.text
        assert str(token_path) not in response.text


@pytest.mark.parametrize("case", _PREPARE_INVALID)
def test_malformed_prepare_is_fixed_400_before_side_effects(
    case: str,
    client: TestClient,
    store: ControlStore,
    tmp_path: Path,
) -> None:
    # Given one malformed key, path, digest, config or chronology declaration.
    payload = _payload(tmp_path)
    _mutate_prepare(payload, case, tmp_path)
    # When prepare validates it before claim or materialization.
    response = client.post("/backtest/prepare", json=payload, headers=_AUTH)
    # Then the fixed body names no detail and creates no dispatch or run directory.
    assert response.status_code == 400, response.text
    assert response.json() == {
        "error": "invalid_input",
        "contract_version": CONTRACT_VERSION,
    }
    assert store.get_dispatch(_DISPATCH_KEY) is None
    assert not (tmp_path / "runs" / _RUN_ID).exists()
    assert list((tmp_path / "runs").glob(".*.staging")) == []


def test_malformed_prepare_json_is_fixed_400(client: TestClient) -> None:
    response = client.post(
        "/backtest/prepare",
        content=b'{"dispatch_key":',
        headers={**_AUTH, "Content-Type": "application/json"},
    )
    assert response.status_code == 400 and response.json() == {
        "error": "invalid_input",
        "contract_version": CONTRACT_VERSION,
    }


@pytest.mark.parametrize("case", _EXECUTE_INVALID)
def test_malformed_execute_is_fixed_400_before_side_effects(
    case: str,
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    tmp_path: Path,
) -> None:
    # Given a verified prepare and one malformed execute-only field.
    prepared = _prepare(client, tmp_path)
    body = _start_body(prepared)
    _mutate_execute(body, case)
    # When the strict adapter rejects the declaration before the service.
    response = client.post("/backtest/execute", json=body, headers=_AUTH)
    # Then no spawn or lifecycle mutation occurs.
    assert response.status_code == 400, response.text
    assert response.json() == {
        "error": "invalid_request",
        "contract_version": CONTRACT_VERSION,
    }
    assert supervisor.calls == []
    row = store.get_dispatch(_DISPATCH_KEY)
    assert row is not None and row.status == "dispatched" and row.start_digest is None


def test_malformed_execute_json_is_fixed_400(client: TestClient) -> None:
    response = client.post(
        "/backtest/execute",
        content=b'{"dispatch_key":',
        headers={**_AUTH, "Content-Type": "application/json"},
    )
    assert response.status_code == 400 and response.json() == {
        "error": "invalid_request",
        "contract_version": CONTRACT_VERSION,
    }


def test_prepare_idempotency_conflict_names_the_original_id(
    client: TestClient, store: ControlStore, tmp_path: Path
) -> None:
    # Given one frozen declaration.
    assert (
        client.post(
            "/backtest/prepare", json=_payload(tmp_path), headers=_AUTH
        ).status_code
        == 200
    )
    run_dir = tmp_path / "runs" / _RUN_ID
    before = {
        name: (run_dir / name).read_bytes()
        for name in ("config.json", "code/signal_engine.py")
    }
    # When the same key is reused with a changed execution assumption.
    changed = _payload(tmp_path)
    config = changed["config"]
    assert isinstance(config, dict)
    config["commission_bps"] = "9"
    response = client.post("/backtest/prepare", json=changed, headers=_AUTH)
    # Then only the original remote id is disclosed and no byte is rewritten.
    assert response.status_code == 409
    assert response.json() == {
        "error": "idempotency_conflict",
        "remote_id": _RUN_ID,
        "contract_version": CONTRACT_VERSION,
    }
    assert {name: (run_dir / name).read_bytes() for name in before} == before
    assert [path.name for path in (tmp_path / "runs").iterdir()] == [_RUN_ID]
    row = store.get_dispatch(_DISPATCH_KEY)
    assert row is not None and row.remote_id == _RUN_ID


def test_execute_field_mismatch_names_the_original_id(
    client: TestClient, supervisor: _FakeSupervisor, tmp_path: Path
) -> None:
    # Given a prepared binding and a start naming another run directory/digest.
    prepared = _prepare(client, tmp_path)
    other_dir = str((tmp_path / "runs" / "mcp-long-other-2").resolve())
    for changes in (
        {"run_dir": other_dir},
        {"input_digest": "0" * 64},
        {"config_sha256": "1" * 64},
        {"source_sha256": "2" * 64},
        {"run_id": "mcp-long-other-2"},
    ):
        # When the prepared-row binding rejects the field.
        response = client.post(
            "/backtest/execute", json=_start_body(prepared, **changes), headers=_AUTH
        )
        # Then the conflict exposes only the original execution identity.
        assert response.status_code == 409, response.text
        assert response.json() == {
            "error": "idempotency_conflict",
            "original_remote_id": _RUN_ID,
            "contract_version": CONTRACT_VERSION,
        }
    assert supervisor.calls == []


def test_execute_drift_names_the_original_id_before_spawn(
    client: TestClient,
    store: ControlStore,
    supervisor: _FakeSupervisor,
    tmp_path: Path,
) -> None:
    # Given a prepared declaration whose frozen config is tampered after prepare.
    prepared = _prepare(client, tmp_path)
    config = tmp_path / "runs" / _RUN_ID / "config.json"
    config.chmod(0o600)
    config.write_bytes(b"tampered")
    # When execute rechecks the frozen inputs.
    response = client.post(
        "/backtest/execute", json=_start_body(prepared), headers=_AUTH
    )
    # Then the original id identifies the failure and no worker starts.
    assert response.status_code == 409
    assert response.json() == {
        "error": "input_digest_mismatch",
        "original_remote_id": _RUN_ID,
        "contract_version": CONTRACT_VERSION,
    }
    assert supervisor.calls == []
    row = store.get_dispatch(_DISPATCH_KEY)
    assert row is not None and row.status == "failed"
    assert row.error_kind == "input_digest_mismatch"


def test_unknown_and_cross_namespace_ids_do_not_leak_existence(
    client: TestClient, tmp_path: Path
) -> None:
    # Given one prepared dispatch whose key and remote id are distinct.
    _prepare(client, tmp_path)
    # When unknown ids or the other namespace's id is queried.
    unknown = client.get("/execution-instances/missing", headers=_AUTH)
    unknown_result = client.get("/execution-instances/missing/result", headers=_AUTH)
    key_as_remote = client.get(f"/execution-instances/{_DISPATCH_KEY}", headers=_AUTH)
    remote_as_key = client.get(
        "/execution-instances", params={"dispatch_key": _RUN_ID}, headers=_AUTH
    )
    # Then 404s are fixed and the wrong-namespace find reports not_dispatched only.
    assert unknown.status_code == unknown_result.status_code == 404
    assert (
        unknown.json()
        == unknown_result.json()
        == {"error": "not_found", "contract_version": CONTRACT_VERSION}
    )
    assert "missing" not in unknown.text
    assert key_as_remote.status_code == 404
    assert key_as_remote.json() == {
        "error": "not_found",
        "contract_version": CONTRACT_VERSION,
    }
    assert remote_as_key.status_code == 200
    assert remote_as_key.json() == {
        "state": "not_dispatched",
        "dispatch_key": _RUN_ID,
        "contract_version": CONTRACT_VERSION,
    }


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/execution-instances", {"dispatch_key": "../escape"}),
        ("/execution-instances/bad%20id", None),
        ("/execution-instances/bad%20id/result", None),
    ],
)
def test_malformed_identifiers_are_fixed_400(
    client: TestClient, path: str, params: dict[str, str] | None
) -> None:
    response = client.get(path, params=params, headers=_AUTH)
    assert response.status_code == 400 and response.json() == {
        "error": "invalid_request",
        "contract_version": CONTRACT_VERSION,
    }


def test_legacy_result_identity_remains_readable(
    client: TestClient, store: ControlStore
) -> None:
    # Given a legacy strategy run with no native result artifact.
    row = store.claim("legacy-key-1", "strategy", "v1", None, None, 60).row
    # When its status/result are read through the additive route.
    status = client.get(f"/execution-instances/{row.vt_run_id}", headers=_AUTH)
    result = client.get(f"/execution-instances/{row.vt_run_id}/result", headers=_AUTH)
    # Then legacy compatibility is preserved as an honest unavailable read.
    assert status.status_code == 200 and status.json()["status"] == "unknown"
    assert result.status_code == 200
    assert result.json()["result_status"] == "unavailable"
    assert result.json()["result"] is None


@pytest.mark.parametrize("route", _ROUTES)
def test_provider_failure_is_fixed_503_without_disclosure(
    route: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given every provider raises with a message carrying SQL, a path and a token.
    def _raise() -> object:
        raise RuntimeError(_SECRET)

    app = strategy_run_server.create_app(
        lambda: object(),
        control_service_provider=_raise,
        prepare_provider=_raise,
        execution_start_service_provider=_raise,
    )
    caplog.set_level(logging.DEBUG)
    with TestClient(app) as client:
        response = _request(client, route, tmp_path, _AUTH)
    # Then the fixed body and the log name only the exception type.
    assert response.status_code == 503, response.text
    assert response.json() == {
        "error": "storage_unavailable",
        "contract_version": CONTRACT_VERSION,
    }
    for fragment in ("secret_table", "/secret/absolute/path", "super-secret", "SELECT"):
        assert fragment not in response.text
        assert fragment not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.parametrize("route", _READ_ROUTES)
def test_service_read_failure_is_fixed_503_without_disclosure(
    route: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given a constructed service whose read raises a storage exception message.
    class _BrokenService:
        def find(self, dispatch_key: str) -> object:
            raise RuntimeError(_SECRET)

        def status(self, remote_id: str) -> object:
            raise RuntimeError(_SECRET)

        def result(self, remote_id: str) -> object:
            raise RuntimeError(_SECRET)

    app = strategy_run_server.create_app(
        lambda: object(), control_service_provider=lambda: _BrokenService()
    )
    caplog.set_level(logging.DEBUG)
    with TestClient(app) as client:
        response = _request(client, route, tmp_path, _AUTH)
    assert response.status_code == 503
    assert response.json() == {
        "error": "storage_unavailable",
        "contract_version": CONTRACT_VERSION,
    }
    assert _SECRET not in response.text and _SECRET not in caplog.text


def _completed_run(client: TestClient, store: ControlStore, tmp_path: Path) -> Path:
    prepared = _prepare(client, tmp_path)
    response = client.post(
        "/backtest/execute", json=_start_body(prepared), headers=_AUTH
    )
    assert response.status_code == 202, response.text
    run_dir = tmp_path / "runs" / _RUN_ID
    _write_native_results(run_dir)
    store.mark_dispatch_terminal(_DISPATCH_KEY, "completed")
    return run_dir


def test_oversized_run_card_is_honest_unavailable(
    client: TestClient, store: ControlStore, tmp_path: Path
) -> None:
    # Given a completed run whose run_card.json exceeds the native read cap.
    run_dir = _completed_run(client, store, tmp_path)
    card_path = run_dir / "run_card.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["padding"] = "x" * (8 * 1024 * 1024 + 1)
    card_path.write_text(json.dumps(card), encoding="utf-8")
    # When the bounded result projection reads it.
    response = client.get(f"/execution-instances/{_RUN_ID}/result", headers=_AUTH)
    # Then it answers honestly instead of truncating or raising.
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result_status"] == "unavailable"
    assert body["result"] is None and body["has_more"] is False
    assert len(response.content) < 4096
    assert "padding" not in response.text and "Traceback" not in response.text


def test_large_metrics_keep_every_required_summary_field(
    client: TestClient, store: ControlStore, tmp_path: Path
) -> None:
    # Given a completed run with a large metric and a 500-point equity curve.
    prepared = _prepare(client, tmp_path)
    assert (
        client.post(
            "/backtest/execute", json=_start_body(prepared), headers=_AUTH
        ).status_code
        == 202
    )
    run_dir = tmp_path / "runs" / _RUN_ID
    _write_native_results(run_dir)
    rows = ["timestamp,ret,equity,drawdown,benchmark_equity,active_ret"]
    rows.extend(
        f"2026-02-{index % 28 + 1:02d},{index / 1000},"
        f"{100000 + index},{index / 1000},100000,0"
        for index in range(500)
    )
    (run_dir / "artifacts" / "equity.csv").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
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
        {"sharpe": 1.5, "large": "m" * 200_000},
        data_sources=["local"],
    )
    store.mark_dispatch_terminal(_DISPATCH_KEY, "completed")
    # When the route projects the bounded result.
    response = client.get(f"/execution-instances/{_RUN_ID}/result", headers=_AUTH)
    # Then every mandatory summary block survives and the preview stays bounded.
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    summary = result["summary"]
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
    assert summary["metrics"]["large"] == "m" * 200_000
    assert 0 < len(summary["equity_preview"]) <= 50
    assert summary["equity_preview"][0]["time"] == "2026-02-01"
    assert summary["equity_preview"][-1]["time"] == f"2026-02-{499 % 28 + 1:02d}"
