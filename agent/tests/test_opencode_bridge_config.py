"""Unit tests for the opencode-bridge env schema additions (env_schema.py).

Additive-only surface (plan T3): ``VIBE_TRADING_ENGINE``,
``OPENCODE_BASE_URL``, ``OPENCODE_SERVER_PASSWORD``,
``OPENCODE_BRIDGE_QUIESCENCE_S`` (default 8.0 — the T1-measured value that
falsified the initial 3.0), and ``OPENCODE_BRIDGE_CHILD_EVENTS``.
"""

from __future__ import annotations

import pytest

from src.config.env_schema import EnvConfig, OpencodeBridgeConfig

BRIDGE_ALIASES = (
    "VIBE_TRADING_ENGINE",
    "OPENCODE_BASE_URL",
    "OPENCODE_SERVER_PASSWORD",
    "OPENCODE_BRIDGE_QUIESCENCE_S",
    "OPENCODE_BRIDGE_CHILD_EVENTS",
)


@pytest.fixture(autouse=True)
def _clean_bridge_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for alias in BRIDGE_ALIASES:
        monkeypatch.delenv(alias, raising=False)


def test_defaults_match_plan_t3() -> None:
    config = OpencodeBridgeConfig()
    assert config.vibe_trading_engine == "native"
    assert config.opencode_base_url == "http://127.0.0.1:4096"
    assert config.opencode_server_password == ""
    # T1 mandatory condition 1: OmO re-prompts 6.4 s after idle, so the
    # quiescence default must be 8.0 (the initial 3.0 was falsified).
    assert config.opencode_bridge_quiescence_s == 8.0
    assert config.opencode_bridge_child_events == "drop"


def test_env_overrides_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBE_TRADING_ENGINE", "opencode")
    monkeypatch.setenv("OPENCODE_BASE_URL", "http://127.0.0.1:14096")
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "pw")
    monkeypatch.setenv("OPENCODE_BRIDGE_QUIESCENCE_S", "10.5")
    monkeypatch.setenv("OPENCODE_BRIDGE_CHILD_EVENTS", "surface")
    config = OpencodeBridgeConfig()
    assert config.vibe_trading_engine == "opencode"
    assert config.opencode_base_url == "http://127.0.0.1:14096"
    assert config.opencode_server_password == "pw"
    assert config.opencode_bridge_quiescence_s == 10.5
    assert config.opencode_bridge_child_events == "surface"


def test_unparseable_quiescence_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCODE_BRIDGE_QUIESCENCE_S", "not-a-number")
    assert OpencodeBridgeConfig().opencode_bridge_quiescence_s == 8.0


def test_explicit_kwargs_win_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_BASE_URL", "http://from-env:1")
    config = OpencodeBridgeConfig(opencode_base_url="http://explicit:2")
    assert config.opencode_base_url == "http://explicit:2"


def test_envconfig_composes_bridge_section() -> None:
    root = EnvConfig()
    assert root.opencode_bridge.vibe_trading_engine == "native"
    assert root.opencode_bridge.opencode_bridge_quiescence_s == 8.0
