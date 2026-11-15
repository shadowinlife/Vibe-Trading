"""Tests for the shared scrubbed subprocess environment builder."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

_CLICKHOUSE_CREDENTIALS = (
    "CLICKHOUSE_USER",
    "CLICKHOUSE_PASSWORD",
    "CLICKHOUSE_LLM_USER",
    "CLICKHOUSE_LLM_PASSWORD",
)


def _build(run_dir: Path, **kwargs):
    from src.core.runtime_env import build_scrubbed_runtime_env

    return build_scrubbed_runtime_env(run_dir, **kwargs)


def test_builder_allowlists_parent_and_strips_credentials_from_both_sources(
    tmp_path: Path,
) -> None:
    parent = {
        "PATH": "parent-path",
        "TUSHARE_TOKEN": "tushare-sentinel",
        "HTTPS_PROXY": "http://proxy.example.test:8080",
        "LC_ALL": "C.UTF-8",
        "CLICKHOUSE_HOST": "clickhouse.sentinel.invalid",
        "CLICKHOUSE_PORT": "8123",
        "CLICKHOUSE_DATABASE": "sentinel_db",
        "OPENAI_API_KEY": "parent-llm-sentinel",
        "VT_SUPERVISOR_TEST": "parent-value-must-not-leak",
        **{key: f"parent-{key.lower()}-sentinel" for key in _CLICKHOUSE_CREDENTIALS},
    }
    overrides = {
        "VT_SUPERVISOR_TEST": "server-sentinel",
        **{key: f"override-{key.lower()}-sentinel" for key in _CLICKHOUSE_CREDENTIALS},
    }

    with patch.dict(os.environ, parent, clear=True):
        env = _build(tmp_path, overrides=overrides)

    assert env["PATH"] == "parent-path"
    assert env["TUSHARE_TOKEN"] == "tushare-sentinel"
    assert env["HTTPS_PROXY"] == parent["HTTPS_PROXY"]
    assert env["LC_ALL"] == "C.UTF-8"
    assert env["CLICKHOUSE_HOST"] == "clickhouse.sentinel.invalid"
    assert env["CLICKHOUSE_PORT"] == "8123"
    assert env["CLICKHOUSE_DATABASE"] == "sentinel_db"
    assert env["VT_SUPERVISOR_TEST"] == "server-sentinel"
    assert "OPENAI_API_KEY" not in env
    assert all(key not in env for key in _CLICKHOUSE_CREDENTIALS)


def test_builder_writes_runtime_guards_after_overrides(tmp_path: Path) -> None:
    configured_root = tmp_path / "configured-runs"
    run_dir = tmp_path / "state-root" / "runs" / "run-1"
    inherited_roots = f"{configured_root},{configured_root}"
    override_roots = f"{configured_root}, ,{run_dir.resolve()},{configured_root}"
    parent = {
        "PATH": "parent-path",
        "VIBE_TRADING_ALLOWED_RUN_ROOTS": inherited_roots,
    }
    overrides = {
        "PATH": "",
        "PYTHONUNBUFFERED": "0",
        "PYTHONIOENCODING": "ascii",
        "PYTHONUTF8": "0",
        "VIBE_TRADING_BACKTEST_SANDBOX": "0",
        "VIBE_TRADING_ALLOWED_RUN_ROOTS": override_roots,
    }

    with patch.dict(os.environ, parent, clear=True):
        env = _build(run_dir, overrides=overrides)

    assert env["PATH"] == os.defpath
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert env["VIBE_TRADING_BACKTEST_SANDBOX"] == "1"
    assert env["VIBE_TRADING_ALLOWED_RUN_ROOTS"].split(",") == [
        str(configured_root),
        str(run_dir.resolve()),
    ]


def test_builder_prepends_optional_pythonpath(tmp_path: Path) -> None:
    pythonpath_extra = tmp_path / "agent"
    parent = {"PATH": "parent-path", "PYTHONPATH": "existing-path"}

    with patch.dict(os.environ, parent, clear=True):
        env = _build(tmp_path, pythonpath_extra=pythonpath_extra)

    assert env["PYTHONPATH"] == f"{pythonpath_extra}{os.pathsep}existing-path"
