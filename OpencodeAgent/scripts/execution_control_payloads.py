"""Sanitized local fixtures and frozen request builders for live verification."""

from __future__ import annotations

from pathlib import Path

from execution_control_common import (
    CANDIDATE_ID,
    CONTRACT,
    REGISTRY_DIGEST,
    RUN_ROOT,
    TOKEN,
    JsonValue,
    Reply,
    canonical,
    sha,
)
from execution_control_harness import Harness


def prepare_payload(
    key: str, run_id: str, config: dict[str, JsonValue], source: str
) -> dict[str, JsonValue]:
    return {
        "dispatch_key": key,
        "run_dir": str(RUN_ROOT / run_id),
        "config": config,
        "source": {"text": source, "sha256": sha(source.encode())},
        "data_source": config["data_source"],
        "data_as_of": config["data_as_of"],
        "snapshot_basis": config["snapshot_basis"],
        "contract_version": CONTRACT,
    }


def start_payload(
    key: str, run_id: str, prepared: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    run_dir = str(prepared["run_dir"])
    return {
        "dispatch_key": key,
        "kind": "backtest",
        "run_id": run_id,
        "run_dir": run_dir,
        "input_digest": prepared["input_digest"],
        "config_sha256": prepared["config_sha256"],
        "source_sha256": prepared["source_sha256"],
        "build_fingerprint": "d" * 64,
        "prompt": f"Run the in-box backtest on this frozen directory:\nbacktest(run_dir={run_dir!r})\nDo not modify, rewrite, or regenerate config.json or code/signal_engine.py.",
        "contract_version": CONTRACT,
    }


def stop_payload(
    key: str, remote_id: str, stop_key: str, deadline: str
) -> dict[str, JsonValue]:
    return {
        "dispatch_key": key,
        "remote_id": remote_id,
        "stop_key": stop_key,
        "deadline_at": deadline,
        "contract_version": CONTRACT,
    }


def factor_payload(key: str, dates: list[str]) -> dict[str, JsonValue]:
    return {
        "dispatch_key": key,
        "kind": "factor",
        "contract_version": CONTRACT,
        "spec": {
            "registry_digest": REGISTRY_DIGEST,
            "candidate_ids": [CANDIDATE_ID],
            "symbols": [f"local:VT{index:02d}" for index in range(6)],
            "start_date": dates[240],
            "split_date": dates[320],
            "end_date": dates[-1],
            "source": "alpha_zoo",
            "as_of": dates[-1],
            "forward_return_horizon_days": 1,
            "n_groups": 3,
        },
    }


def setup_state(state: Path, source: str) -> tuple[dict[str, JsonValue], str]:
    """Create only disposable auth/data/strategy state; fixtures remain read-only."""
    (state / "auth").mkdir(parents=True)
    (state / "data").mkdir()
    (state / "payload").mkdir()
    (state / "runtime" / "runs").mkdir(parents=True)
    for directory in (
        state,
        state / "auth",
        state / "data",
        state / "payload",
        state / "runtime",
        state / "runtime" / "runs",
    ):
        directory.chmod(0o777)
    (state / "auth" / "service-token").write_text(TOKEN + "\n", encoding="utf-8")
    fixture = (
        Path(__file__).resolve().parents[2]
        / "agent/tests/fixtures/execution_control/000001.SZ.csv"
    )
    data_path = state / "data" / "000001.SZ.csv"
    data_path.write_bytes(fixture.read_bytes())
    data_path.chmod(0o666)
    config: dict[str, JsonValue] = {
        "strategy_id": "fixture-main",
        "strategy_version": "v1-fixture01",
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
    long_source = source.replace(
        "import pandas as pd", "import signal\nimport time\n\nimport pandas as pd"
    ).replace(
        'print("VT_LOCAL_ENGINE_GENERATE", flush=True)',
        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n        time.sleep(120)\n        print("VT_LONG_ENGINE_START", flush=True)',
    )
    for strategy_id, version, text in (
        ("fixture-main", "v1-fixture01", source),
        ("fixture-stop", "v1-stop0001", long_source),
        ("fixture-restart", "v1-restart01", long_source),
    ):
        version_dir = (
            state / "runtime" / "strategies" / strategy_id / "versions" / version
        )
        (version_dir / "code").mkdir(parents=True)
        (version_dir / "config.json").write_bytes(
            canonical(
                config | {"strategy_id": strategy_id, "strategy_version": version}
            )
        )
        (version_dir / "code" / "signal_engine.py").write_text(text, encoding="utf-8")
    (state / "payload" / "neutral.json").write_bytes(canonical(config))
    (state / "payload" / "long.py").write_text(long_source, encoding="utf-8")
    sources = [{"symbol": "000001.SZ", "path": "/vt-state/data/000001.SZ.csv"}]
    sources += [
        {
            "symbol": f"VT{index:02d}",
            "path": f"/fixtures/factor_prototype/VT{index:02d}.csv",
        }
        for index in range(6)
    ]
    lines = ["sources:"]
    for item in sources:
        lines.extend(
            (
                f"  - symbol: {item['symbol']}",
                "    type: csv",
                f"    path: {item['path']}",
                "    date_format: '%Y-%m-%d'",
            )
        )
    (state / "loader.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config, long_source


def prepare(
    h: Harness,
    group: str,
    key: str,
    run_id: str,
    config: dict[str, JsonValue],
    source: str,
) -> dict[str, JsonValue]:
    return h.expect(
        h.request(
            group,
            f"prepare-{key}",
            "POST",
            "/backtest/prepare",
            prepare_payload(key, run_id, config, source),
        ),
        200,
    )


def execute(
    h: Harness, group: str, key: str, run_id: str, prepared: dict[str, JsonValue]
) -> Reply:
    return h.request(
        group,
        f"execute-{key}",
        "POST",
        "/backtest/execute",
        start_payload(key, run_id, prepared),
    )
