"""HTTP contract for atomic, replay-safe frozen backtest preparation.

# noqa: SIZE_OK - one public route seam covers the complete A5/A6 behavior matrix.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from pathlib import Path
from typing import Final

import pytest
import yaml
from fastapi.testclient import TestClient

import strategy_run_server
from backtest.loaders import local_loader
from src.strategy_library import paths as strategy_paths
from src.strategy_runs import backtest_materialize as materialize_module
from src.strategy_runs.backtest_materialize import (
    BacktestMaterializer,
    BacktestPrepareRequest,
    BacktestPrepareResponse,
)
from src.strategy_runs.control_contract import CONTRACT_VERSION, JsonValue
from src.strategy_runs.control_store import ControlStore
from tests.module_os_helpers import patch_module_os

STRATEGY_ID: Final = "momentum-alpha"
VERSION: Final = "v1-abcdef12"
RUN_ID: Final = "mcp-long-job-7-1"
DISPATCH_KEY: Final = "user-1:job-7:1"
TOKEN: Final = "dummy-service-token-backtest-prepare-8"
AUTH: Final = {"Authorization": f"Bearer {TOKEN}"}
SOURCE: Final = "# 中文\r\nclass SignalEngine:\r\n    pass\r\n"
SOURCE_BYTES: Final = SOURCE.encode("utf-8")


def _canonical(payload: JsonValue) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _config(**changes: JsonValue) -> dict[str, JsonValue]:
    config: dict[str, JsonValue] = {
        "strategy_id": STRATEGY_ID,
        "strategy_version": VERSION,
        "market": "cn_a",
        "symbols": ["000001.SZ"],
        "start_date": "2026-01-01",
        "end_date": "2026-01-02",
        "bar_interval": "1d",
        "data_source": "clickhouse",
        "data_as_of": "2026-01-03",
        "snapshot_basis": "test-snapshot-v1",
        "initial_cash": "100000.00",
        "currency": "CNY",
        "commission_bps": "1.5",
        "tax_bps": "5",
        "slippage_bps": "2",
    }
    config.update(changes)
    return config


def _run_dir(tmp_path: Path, name: str = RUN_ID) -> Path:
    return (tmp_path / "runs" / name).resolve()


def _payload(
    tmp_path: Path,
    config: dict[str, JsonValue] | None = None,
    dispatch_key: str = DISPATCH_KEY,
) -> dict[str, JsonValue]:
    neutral = _config() if config is None else config
    return {
        "dispatch_key": dispatch_key,
        "run_dir": str(_run_dir(tmp_path)),
        "config": neutral,
        "source": {
            "text": SOURCE,
            "sha256": hashlib.sha256(SOURCE_BYTES).hexdigest(),
        },
        "data_source": neutral["data_source"],
        "data_as_of": neutral["data_as_of"],
        "snapshot_basis": neutral["snapshot_basis"],
        "contract_version": CONTRACT_VERSION,
    }


def _post(client: TestClient, payload: dict[str, JsonValue]):
    return client.post("/backtest/prepare", json=payload, headers=AUTH)


def _expected_response(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    config_bytes = _canonical(payload["config"])
    source_sha256 = hashlib.sha256(SOURCE_BYTES).hexdigest()
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    input_digest = hashlib.sha256(
        _canonical(
            {
                "schema": "backtest-input-v1",
                "config_sha256": config_sha256,
                "source_sha256": source_sha256,
                "data_source": payload["data_source"],
                "data_as_of": payload["data_as_of"],
                "snapshot_basis": payload["snapshot_basis"],
            }
        )
    ).hexdigest()
    return {
        "run_dir": payload["run_dir"],
        "config_sha256": config_sha256,
        "source_sha256": source_sha256,
        "input_digest": input_digest,
        "data_source": payload["data_source"],
        "data_as_of": payload["data_as_of"],
        "data_digest": None,
        "data_digest_status": "unavailable",
        "contract_version": CONTRACT_VERSION,
    }


def _file_state(run_dir: Path) -> dict[str, tuple[bytes, int]]:
    return {
        relative: (
            (run_dir / relative).read_bytes(),
            (run_dir / relative).stat().st_mtime_ns,
        )
        for relative in ("config.json", "code/signal_engine.py")
    }


def _local_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    csv_path = data_dir / "aapl.csv"
    csv_path.write_text(
        "Date,Open,High,Low,Close,Volume\n"
        "2026-01-01,10,11,9,10.5,1000\n"
        "2026-01-02,12,13,11,12.5,1500\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "data-bridge" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "symbol": "AAPL.US",
                        "type": "csv",
                        "path": str(csv_path),
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
    monkeypatch.setattr(local_loader, "_CONFIG_PATH", config_path)
    return csv_path


def _local_payload(tmp_path: Path) -> dict[str, JsonValue]:
    payload = _payload(
        tmp_path,
        _config(data_source="local", symbols=["AAPL.US"]),
    )
    return payload


@pytest.fixture(autouse=True)
def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = tmp_path / "rm_vt_strategy_run_token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    token.chmod(0o600)
    monkeypatch.setenv("RM_VT_STRATEGY_RUN_TOKEN_FILE", str(token))
    monkeypatch.setenv(
        "VIBE_TRADING_ALLOWED_RUN_ROOTS", str((tmp_path / "runs").resolve())
    )
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path / "runtime"))

    runtime = tmp_path / "runtime"
    strategy_paths.strategy_dir(STRATEGY_ID, runtime_root=runtime).mkdir(parents=True)
    version_config = strategy_paths.version_config_path(
        STRATEGY_ID, VERSION, runtime_root=runtime
    )
    version_code = strategy_paths.version_code_path(
        STRATEGY_ID, VERSION, runtime_root=runtime
    )
    version_code.parent.mkdir(parents=True)
    version_config.write_text('{"frozen":true}\n', encoding="utf-8")
    version_code.write_bytes(SOURCE_BYTES)
    version_config.chmod(0o444)
    version_code.chmod(0o444)
    monkeypatch.setattr(
        local_loader,
        "_CONFIG_PATH",
        tmp_path / "data-bridge" / "config.yaml",
    )

    def fail_fetch(*args: JsonValue, **kwargs: JsonValue) -> None:
        del args, kwargs
        raise AssertionError("prepare must never fetch market data")

    monkeypatch.setattr(local_loader.DataLoader, "fetch", fail_fetch)


@pytest.fixture
def store(tmp_path: Path) -> ControlStore:
    return ControlStore(tmp_path / "ledger.sqlite3")


@pytest.fixture
def materializer(store: ControlStore, tmp_path: Path) -> BacktestMaterializer:
    return BacktestMaterializer(store, runtime_root=tmp_path / "runtime")


@pytest.fixture
def client(materializer: BacktestMaterializer):
    app = strategy_run_server.create_app(
        lambda: object(), prepare_provider=lambda: materializer
    )
    with TestClient(app) as test_client:
        yield test_client


def test_prepare_materializes_exact_bytes_and_independent_digests(
    client: TestClient, tmp_path: Path
) -> None:
    # Given a valid declaration whose source contains UTF-8 and CRLF bytes.
    payload = _payload(tmp_path)
    # When the declaration is prepared through the public route.
    response = _post(client, payload)
    # Then the exact response and independently computed file hashes agree.
    assert response.status_code == 200, response.text
    assert response.json() == _expected_response(payload)
    run_dir = _run_dir(tmp_path)
    assert (run_dir / "config.json").read_bytes() == _canonical(payload["config"])
    assert (run_dir / "code" / "signal_engine.py").read_bytes() == SOURCE_BYTES
    assert {
        path.relative_to(run_dir).as_posix()
        for path in run_dir.rglob("*")
        if path.is_file()
    } == {"config.json", "code/signal_engine.py"}
    for relative in ("config.json", "code/signal_engine.py"):
        assert stat.S_IMODE((run_dir / relative).stat().st_mode) == 0o444


def test_replay_returns_stored_response_without_rewriting(
    client: TestClient, store: ControlStore, tmp_path: Path
) -> None:
    # Given one successfully frozen run directory.
    payload = _payload(tmp_path)
    first = _post(client, payload)
    assert first.status_code == 200
    before = _file_state(_run_dir(tmp_path))
    # When the identical declaration is replayed.
    second = _post(client, payload)
    # Then stored identity/digests and every frozen byte/mtime remain unchanged.
    assert second.status_code == 200
    assert second.json() == first.json()
    assert _file_state(_run_dir(tmp_path)) == before
    row = store.get_dispatch(DISPATCH_KEY)
    assert row is not None and row.remote_id == RUN_ID
    assert row.input_digest == first.json()["input_digest"]


def test_concurrent_same_key_prepare_replays_without_false_conflict(
    materializer: BacktestMaterializer,
    store: ControlStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a first same-key prepare still inside its atomic materialization.
    payload = _payload(tmp_path)
    request = BacktestPrepareRequest.model_validate(payload)
    real_materialize = materialize_module._materialize
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def blocking_materialize(*args: JsonValue, **kwargs: JsonValue) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(timeout=10)
        assert real_materialize(*args, **kwargs) is None

    monkeypatch.setattr(materialize_module, "_materialize", blocking_materialize)
    first_result: list[object] = []
    first = threading.Thread(
        target=lambda: first_result.append(materializer.prepare(request))
    )
    first.start()
    assert entered.wait(timeout=10)
    try:
        # When a second same-key prepare arrives before the first publishes.
        second = materializer.prepare(request)
    finally:
        release.set()
        first.join(timeout=10)
    # Then both return the same response with exactly one complete run directory.
    assert len(first_result) == 1
    expected = BacktestPrepareResponse.model_validate(_expected_response(payload))
    assert first_result[0] == second == expected
    run_dir = _run_dir(tmp_path)
    assert sorted(
        path.relative_to(run_dir).as_posix()
        for path in run_dir.rglob("*")
        if path.is_file()
    ) == ["code/signal_engine.py", "config.json"]
    assert [path.name for path in (tmp_path / "runs").iterdir()] == [RUN_ID]
    row = store.get_dispatch(DISPATCH_KEY)
    assert row is not None and row.status == "dispatched" and row.remote_id == RUN_ID
    # And a later complete replay still keeps every frozen byte and mtime.
    before = _file_state(run_dir)
    assert materializer.prepare(request) == expected
    assert _file_state(run_dir) == before


@pytest.mark.parametrize("failure", ["write", "rename"])
def test_transient_materialize_failure_does_not_burn_the_dispatch_key(
    client: TestClient,
    store: ControlStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    # Given a materialization that fails once after the dispatch claim commits.
    payload = _payload(tmp_path)
    real_operation = getattr(os, failure)
    calls = 0

    def fail_once(*args: JsonValue, **kwargs: JsonValue) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("secret transient failure")
        return real_operation(*args, **kwargs)

    patch_module_os(monkeypatch, materialize_module, **{failure: fail_once})
    failed = _post(client, payload)
    # Then the failure is redacted and leaves a claim without a run directory.
    assert failed.status_code == 503
    assert failed.json() == {
        "error": "storage_unavailable",
        "contract_version": CONTRACT_VERSION,
    }
    assert "secret" not in failed.text
    row = store.get_dispatch(DISPATCH_KEY)
    assert row is not None and row.status == "dispatched"
    assert not _run_dir(tmp_path).exists()
    # When the same key and input retry after the transient fault clears.
    retried = _post(client, payload)
    # Then the stored declaration is re-materialized and replayed, never 409.
    assert retried.status_code == 200, retried.text
    assert retried.json() == _expected_response(payload)
    assert (run_dir := _run_dir(tmp_path)).is_dir()
    assert sorted(
        path.relative_to(run_dir).as_posix()
        for path in run_dir.rglob("*")
        if path.is_file()
    ) == ["code/signal_engine.py", "config.json"]
    assert [path.name for path in (tmp_path / "runs").iterdir()] == [RUN_ID]


def test_changed_input_conflicts_and_preserves_original(
    client: TestClient, tmp_path: Path
) -> None:
    # Given a claimed frozen declaration.
    first = _post(client, _payload(tmp_path))
    assert first.status_code == 200
    before = _file_state(_run_dir(tmp_path))
    # When the same key is reused with a changed execution assumption.
    changed = _payload(tmp_path, _config(commission_bps="9"))
    response = _post(client, changed)
    # Then the conflict names the original id and cannot replace frozen bytes.
    assert response.status_code == 409
    assert response.json() == {
        "error": "idempotency_conflict",
        "remote_id": RUN_ID,
        "contract_version": CONTRACT_VERSION,
    }
    assert _file_state(_run_dir(tmp_path)) == before
    assert [path.name for path in (tmp_path / "runs").iterdir()] == [RUN_ID]


@pytest.mark.parametrize("relative", ["config.json", "code/signal_engine.py"])
@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_replay_with_unreadable_frozen_input_is_honest_conflict(
    client: TestClient, tmp_path: Path, relative: str, mutation: str
) -> None:
    # Given a replay whose frozen file was removed or changed after prepare.
    payload = _payload(tmp_path)
    assert _post(client, payload).status_code == 200
    target = _run_dir(tmp_path) / relative
    if mutation == "missing":
        target.unlink()
    else:
        target.chmod(0o600)
        target.write_bytes(b"tampered")
    # When the identical declaration is replayed.
    response = _post(client, payload)
    # Then missing/drifted bytes are an integrity failure, never false success or repair.
    assert response.status_code == 409
    assert response.json() == {
        "error": "input_digest_mismatch",
        "contract_version": CONTRACT_VERSION,
    }
    if mutation == "missing":
        assert not target.exists()
    else:
        assert target.read_bytes() == b"tampered"


@pytest.mark.parametrize(
    "candidate",
    [
        "runs/mcp-long-job-7-1",
        "outside",
        "mcp-long-job-7-0",
        "arbitrary-name",
    ],
)
def test_run_dir_fence_rejects_relative_outside_and_malformed_paths(
    client: TestClient,
    store: ControlStore,
    tmp_path: Path,
    candidate: str,
) -> None:
    # Given a path that is relative, outside the allowed root, or has the wrong grammar.
    payload = _payload(tmp_path)
    if candidate == "outside":
        supplied = str((tmp_path / "outside" / RUN_ID).resolve())
    elif candidate.startswith("mcp-long") or candidate == "arbitrary-name":
        supplied = str(_run_dir(tmp_path, candidate))
    else:
        supplied = candidate
    payload["run_dir"] = supplied
    # When prepare is attempted.
    response = _post(client, payload)
    # Then no dispatch or filesystem identity is created.
    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_input",
        "contract_version": CONTRACT_VERSION,
    }
    assert store.get_dispatch(DISPATCH_KEY) is None
    assert not _run_dir(tmp_path).exists()


def test_missing_strategy_is_404_without_run_dir(
    client: TestClient, tmp_path: Path
) -> None:
    # Given a declaration for an unknown strategy.
    payload = _payload(tmp_path, _config(strategy_id="missing-strategy"))
    # When snapshot resolution fails.
    response = _post(client, payload)
    # Then the stable error is strategy_not_found and nothing is materialized.
    assert response.status_code == 404
    assert response.json() == {
        "error": "strategy_not_found",
        "contract_version": CONTRACT_VERSION,
    }
    assert not _run_dir(tmp_path).exists()


@pytest.mark.parametrize("relative", ["config.json", "code/signal_engine.py"])
def test_missing_version_snapshot_file_is_404(
    client: TestClient, tmp_path: Path, relative: str
) -> None:
    # Given an existing strategy with one incomplete version snapshot.
    version_dir = strategy_paths.version_dir(
        STRATEGY_ID, VERSION, runtime_root=tmp_path / "runtime"
    )
    (version_dir / relative).unlink()
    # When prepare resolves that version.
    response = _post(client, _payload(tmp_path))
    # Then it reports version_not_found without creating a run.
    assert response.status_code == 404
    assert response.json() == {
        "error": "version_not_found",
        "contract_version": CONTRACT_VERSION,
    }
    assert not _run_dir(tmp_path).exists()


def test_source_not_equal_to_version_is_conflict_and_never_overwrites_snapshot(
    client: TestClient, tmp_path: Path
) -> None:
    # Given internally consistent submitted bytes that differ from the pinned version.
    changed = SOURCE + "# changed\n"
    payload = _payload(tmp_path)
    payload["source"] = {
        "text": changed,
        "sha256": hashlib.sha256(changed.encode("utf-8")).hexdigest(),
    }
    # When prepare binds the source to the version snapshot.
    response = _post(client, payload)
    # Then it refuses the mismatch and preserves both version and absent run bytes.
    snapshot = strategy_paths.version_code_path(
        STRATEGY_ID, VERSION, runtime_root=tmp_path / "runtime"
    )
    assert response.status_code == 409
    assert response.json() == {
        "error": "input_digest_mismatch",
        "contract_version": CONTRACT_VERSION,
    }
    assert snapshot.read_bytes() == SOURCE_BYTES
    assert not _run_dir(tmp_path).exists()


def test_declared_source_hash_mismatch_is_invalid_input(
    client: TestClient, tmp_path: Path
) -> None:
    # Given a source digest that does not describe the submitted UTF-8 bytes.
    payload = _payload(tmp_path)
    payload["source"] = {"text": SOURCE, "sha256": "0" * 64}
    # When the declaration is checked.
    response = _post(client, payload)
    # Then it is rejected before any snapshot/run write.
    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_input",
        "contract_version": CONTRACT_VERSION,
    }
    assert not _run_dir(tmp_path).exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contract_version", "wrong"),
        ("data_as_of", "2026-01-01"),
        ("data_source", "local"),
        ("snapshot_basis", "different-basis"),
    ],
)
def test_invalid_chronology_provenance_or_contract_is_400(
    client: TestClient,
    store: ControlStore,
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    # Given one invalid duplicate-provenance/chronology/contract field.
    payload = _payload(tmp_path)
    payload[field] = value
    # When validation runs before the dispatch claim.
    response = _post(client, payload)
    # Then invalid input has no durable or filesystem side effect.
    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_input",
        "contract_version": CONTRACT_VERSION,
    }
    assert store.get_dispatch(DISPATCH_KEY) is None
    assert not _run_dir(tmp_path).exists()


def test_extra_fields_are_forbidden(
    client: TestClient, store: ControlStore, tmp_path: Path
) -> None:
    # Given undeclared top-level and neutral-config fields.
    payload = _payload(tmp_path, _config(native_alias="not-allowed"))
    payload["unexpected"] = "not-allowed"
    # When the strict boundary parses the body.
    response = _post(client, payload)
    # Then extras are rejected instead of silently dropped.
    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_input",
        "contract_version": CONTRACT_VERSION,
    }
    assert store.get_dispatch(DISPATCH_KEY) is None


def test_local_files_have_independently_verified_digest(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given one fully enumerable local CSV for the requested symbol/window.
    csv_path = _local_csv(tmp_path, monkeypatch)
    payload = _local_payload(tmp_path)
    # When prepare computes the local data proof without invoking the loader.
    response = _post(client, payload)
    # Then the digest follows the documented sorted relative-path/hash schema.
    relative = os.path.relpath(
        csv_path.resolve(), local_loader._CONFIG_PATH.parent.resolve()
    ).replace(os.sep, "/")
    expected_data_digest = hashlib.sha256(
        _canonical(
            {
                "schema": "backtest-local-data-v1",
                "files": [
                    [relative, hashlib.sha256(csv_path.read_bytes()).hexdigest()]
                ],
            }
        )
    ).hexdigest()
    assert response.status_code == 200, response.text
    assert response.json()["data_digest_status"] == "verified"
    assert response.json()["data_digest"] == expected_data_digest


def test_local_data_mutation_conflicts_without_rewriting_run(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a verified local-data declaration and frozen run files.
    csv_path = _local_csv(tmp_path, monkeypatch)
    payload = _local_payload(tmp_path)
    assert _post(client, payload).status_code == 200
    before = _file_state(_run_dir(tmp_path))
    # When the underlying local bytes change before a same-key replay.
    csv_path.write_text(
        csv_path.read_text(encoding="utf-8") + "# mutation\n", encoding="utf-8"
    )
    response = _post(client, payload)
    # Then the changed declaration conflicts and cannot rewrite the original run.
    assert response.status_code == 409
    assert response.json() == {
        "error": "idempotency_conflict",
        "remote_id": RUN_ID,
        "contract_version": CONTRACT_VERSION,
    }
    assert _file_state(_run_dir(tmp_path)) == before


def test_unenumerable_local_input_is_explicitly_unavailable(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a local mapping whose input file does not exist.
    missing = tmp_path / "missing.csv"
    config_path = tmp_path / "data-bridge" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        yaml.safe_dump(
            {"sources": [{"symbol": "AAPL.US", "type": "csv", "path": str(missing)}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(local_loader, "_CONFIG_PATH", config_path)
    # When no complete local file set can be proven.
    response = _post(client, _local_payload(tmp_path))
    # Then prepare succeeds with an honest unavailable proof, never a fabricated hash.
    assert response.status_code == 200
    assert response.json()["data_digest"] is None
    assert response.json()["data_digest_status"] == "unavailable"


@pytest.mark.parametrize("failure", ["write", "rename"])
def test_filesystem_failure_leaves_no_run_or_staging_directory(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    # Given an injected low-level write/rename failure containing sensitive detail.
    def fail_operation(*args: JsonValue, **kwargs: JsonValue) -> None:
        del args, kwargs
        raise OSError("secret /sql/path failure")

    if failure == "write":
        patch_module_os(monkeypatch, materialize_module, write=fail_operation)
    else:
        patch_module_os(monkeypatch, materialize_module, rename=fail_operation)
    # When materialization fails after its short dispatch claim.
    response = _post(client, _payload(tmp_path))
    # Then storage failure is fixed/redacted and no half run/staging tree remains.
    assert response.status_code == 503
    assert response.json() == {
        "error": "storage_unavailable",
        "contract_version": CONTRACT_VERSION,
    }
    assert "secret" not in response.text
    assert not _run_dir(tmp_path).exists()
    run_root = tmp_path / "runs"
    assert not run_root.exists() or list(run_root.iterdir()) == []


@pytest.mark.parametrize(
    ("headers", "expected_status", "expected_body"),
    [
        ({}, 401, {"detail": "unauthorized"}),
        ({"Authorization": "Bearer wrong"}, 403, {"detail": "forbidden"}),
    ],
)
def test_prepare_requires_service_token(
    client: TestClient,
    tmp_path: Path,
    headers: dict[str, str],
    expected_status: int,
    expected_body: dict[str, str],
) -> None:
    # Given a missing or incorrect credential.
    # When the route is called before any provider work.
    response = client.post(
        "/backtest/prepare", json=_payload(tmp_path), headers=headers
    )
    # Then authentication fails closed with the existing fixed body and no run.
    assert response.status_code == expected_status
    assert response.json() == expected_body
    assert not _run_dir(tmp_path).exists()


def test_provider_failure_is_fixed_storage_unavailable(tmp_path: Path) -> None:
    # Given a provider that cannot construct its storage dependency.
    def unavailable() -> BacktestMaterializer:
        raise OSError("secret SQL path and token")

    app = strategy_run_server.create_app(lambda: object(), prepare_provider=unavailable)
    # When a valid authenticated request resolves that provider.
    with TestClient(app) as test_client:
        response = _post(test_client, _payload(tmp_path))
    # Then no internal detail crosses the fixed 503 boundary.
    assert response.status_code == 503
    assert response.json() == {
        "error": "storage_unavailable",
        "contract_version": CONTRACT_VERSION,
    }
    assert "secret" not in response.text
