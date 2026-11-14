"""A9: 0003 read bytes are consumable by the frozen 0013 prepare flow.

One end-to-end HTTP journey on the dedicated entry app publishes an
owner-bound active version, reads its exact bytes through
``POST /strategy-version-source``, then hands the decoded text plus an
RM-owned config to ``POST /backtest/prepare``.  Expected source bytes,
canonical config bytes and the D-3 input digest are derived from the
synthetic test inputs, never from the production helpers under test.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Final

import pytest
from fastapi.testclient import TestClient

import strategy_run_server
from src.config.paths import get_runtime_root
from src.strategy_library.paths import version_code_path, version_config_path
from src.strategy_runs.backtest_materialize import BacktestMaterializer
from src.strategy_runs.control_contract import CONTRACT_VERSION, JsonValue
from src.strategy_runs.control_store import ControlStore
from tests._strategy_version_source_support import (
    AUTH,
    CONFIG_BYTES,
    OWNER,
    SOURCE_BYTES,
    URL,
    Harness,
)

STRATEGY_ID: Final = "momentum"
VERSION: Final = "v7"
RUN_ID: Final = "mcp-long-a9-1"
DISPATCH_KEY: Final = "synthetic-owner-a9:job-1:1"


def _canonical(payload: JsonValue) -> bytes:
    """Encode the documented canonical JSON shape straight from test input."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _rm_config() -> dict[str, JsonValue]:
    """Return an RM-owned config whose bytes differ from the snapshot config."""
    return {
        "strategy_id": STRATEGY_ID,
        "strategy_version": VERSION,
        "market": "cn_a",
        "symbols": ["600000.SH"],
        "start_date": "2026-02-01",
        "end_date": "2026-02-03",
        "bar_interval": "1d",
        "data_source": "clickhouse",
        "data_as_of": "2026-02-04",
        "snapshot_basis": "a9-rm-owned-config-v1",
        "initial_cash": "250000.00",
        "currency": "CNY",
        "commission_bps": "2.5",
        "tax_bps": "1",
        "slippage_bps": "3",
    }


def _expected_input_digest(config: dict[str, JsonValue], config_sha256: str, source_sha256: str) -> str:
    """Compute the six-field D-3 digest independently from the test inputs."""
    document = {
        "schema": "backtest-input-v1",
        "config_sha256": config_sha256,
        "source_sha256": source_sha256,
        "data_source": config["data_source"],
        "data_as_of": config["data_as_of"],
        "snapshot_basis": config["snapshot_basis"],
    }
    return hashlib.sha256(_canonical(document)).hexdigest()


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    """Publish into the same runtime root that production prepare resolves."""
    instance = Harness(tmp_path, monkeypatch)
    monkeypatch.setenv("VIBE_TRADING_HOME", str(instance.root))
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str((instance.root / "runs").resolve()))
    return instance


def test_a9_read_bytes_feed_frozen_prepare_with_rm_owned_config(harness: Harness, tmp_path: Path) -> None:
    # Given an active owner-bound version whose source has a UTF-8 BOM,
    # non-ASCII comments, mixed CRLF/LF line endings and a trailing newline.
    key, active = harness.publish(strategy_id=STRATEGY_ID, version=VERSION)
    assert key.owner_user_id == OWNER
    assert SOURCE_BYTES.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in SOURCE_BYTES
    assert b"\n" in SOURCE_BYTES.replace(b"\r\n", b"")
    assert SOURCE_BYTES.endswith(b"\r\n")
    assert "中文".encode("utf-8") in SOURCE_BYTES
    assert get_runtime_root() == harness.root
    snapshot_code = version_code_path(STRATEGY_ID, VERSION)
    snapshot_config = version_config_path(STRATEGY_ID, VERSION)
    assert snapshot_code.read_bytes() == SOURCE_BYTES
    assert snapshot_config.read_bytes() == CONFIG_BYTES

    materializer = BacktestMaterializer(ControlStore(tmp_path / "ledger.sqlite3"))
    start_calls: list[bool] = []

    def forbidden_start() -> object:
        start_calls.append(True)
        raise AssertionError("prepare must never start an execution")

    app = strategy_run_server.create_app(
        lambda: object(),
        prepare_provider=lambda: materializer,
        execution_start_service_provider=forbidden_start,
        version_source_provider=harness.provider,
    )
    run_dir = (tmp_path / "runs" / RUN_ID).resolve()
    rm_config = _rm_config()

    with TestClient(app) as client:
        # When the owner reads the exact version through the 0003 route.
        read_response = client.post(
            URL,
            json={
                "strategy_id": STRATEGY_ID,
                "strategy_version": VERSION,
                "expected_source_sha256": active.source.sha256,
            },
            headers=AUTH,
        )
        assert read_response.status_code == 200, read_response.text
        source_read = read_response.json()
        read_bytes = base64.b64decode(source_read["source_bytes_base64"], validate=True)

        # When the decoded text and the RM-owned config enter frozen prepare.
        prepare_response = client.post(
            "/backtest/prepare",
            json={
                "dispatch_key": DISPATCH_KEY,
                "run_dir": str(run_dir),
                "config": rm_config,
                "source": {
                    "text": read_bytes.decode("utf-8"),
                    "sha256": source_read["source_sha256"],
                },
                "data_source": rm_config["data_source"],
                "data_as_of": rm_config["data_as_of"],
                "snapshot_basis": rm_config["snapshot_basis"],
                "contract_version": CONTRACT_VERSION,
            },
            headers=AUTH,
        )
        assert prepare_response.status_code == 200, prepare_response.text
        prepared = prepare_response.json()

    # Then the read returned the exact stored bytes and identity.
    assert read_bytes == SOURCE_BYTES
    assert source_read["source_bytes_base64"] == base64.b64encode(SOURCE_BYTES).decode("ascii")
    assert source_read["source_sha256"] == hashlib.sha256(SOURCE_BYTES).hexdigest()
    assert source_read["source_sha256"] == active.source.sha256
    assert source_read["source_byte_length"] == len(SOURCE_BYTES)
    assert source_read["version_id"] == active.version_id
    assert source_read["owner_user_id"] == OWNER
    assert source_read["caller_scope"] == "owner"
    assert source_read["source_path"] == "code/signal_engine.py"

    # Then prepare wrote the 0003 bytes and the RM config canonically, not the
    # diagnostic snapshot config.
    prepared_source = (run_dir / "code" / "signal_engine.py").read_bytes()
    prepared_config = (run_dir / "config.json").read_bytes()
    config_bytes = _canonical(rm_config)
    source_sha256 = hashlib.sha256(SOURCE_BYTES).hexdigest()
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    assert prepared_source == read_bytes == SOURCE_BYTES
    assert prepared_config == config_bytes
    assert prepared_config != CONFIG_BYTES
    assert hashlib.sha256(CONFIG_BYTES).hexdigest() != config_sha256

    # Then the source, config and prepare input digests stay independent.
    assert prepared["source_sha256"] == source_sha256
    assert prepared["source_sha256"] == source_read["source_sha256"]
    assert prepared["config_sha256"] == config_sha256
    assert prepared["input_digest"] == _expected_input_digest(rm_config, config_sha256, source_sha256)
    assert (
        len(
            {
                prepared["input_digest"],
                prepared["config_sha256"],
                prepared["source_sha256"],
            }
        )
        == 3
    )

    # Then VT did not rewrite the published snapshot or its diagnostic config.
    assert snapshot_code.read_bytes() == SOURCE_BYTES
    assert snapshot_config.read_bytes() == CONFIG_BYTES

    # Then no execution start or executor artifact was invoked or created.
    assert start_calls == []
    assert sorted(path.relative_to(run_dir).as_posix() for path in run_dir.rglob("*") if path.is_file()) == [
        "code/signal_engine.py",
        "config.json",
    ]
