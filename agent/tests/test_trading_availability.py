"""Tests for the trading connector availability probe (PLAN-B2).

``has_configured_connector()`` is the five-branch union probe that gates the
trading_* tools out of the exposure surface when the user has configured no
connector. These tests pin: the clean-environment False, each branch flipping
the probe True in isolation, the tool-class ``check_available()`` following
the probe, the fail-toward-visibility guarantee, and the per-connector
``is_configured()`` config-only contract.

Connector credentials in this codebase are file-based (``<runtime root>/*.json``
written by ``connector configure``); eToro additionally honours
``ETORO_API_KEY`` / ``ETORO_USER_KEY`` env fallbacks, which is the env path
exercised below. All runs are network-free.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.trading import availability
from src.trading.availability import has_configured_connector, reset_availability_cache

pytestmark = pytest.mark.unit

#: Credential env vars that could satisfy a connector completeness probe.
#: Pinned to empty strings (not deleted) so tap_forward's ``.env`` scan and
#: eToro's env fallback cannot repopulate them from a developer machine.
_CONNECTOR_ENV_VARS = (
    "ETORO_API_KEY",
    "ETORO_USER_KEY",
    "TAP_PROXY_URL",
    "TAP_AGENT_KEY",
    "TAP_ALPACA_CREDENTIAL",
)


@pytest.fixture
def clean_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the runtime root: empty home, no connector credentials."""
    from src.config.accessor import reset_env_config

    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path))
    for name in _CONNECTOR_ENV_VARS:
        monkeypatch.setenv(name, "")
    reset_env_config()
    reset_availability_cache()
    yield tmp_path
    reset_availability_cache()
    reset_env_config()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _flip_selection_marker(home: Path) -> None:
    _write_json(home / "trading-connections.json", {"selected_profile": "ibkr-paper-local"})


def _flip_ibkr_local_config(home: Path) -> None:
    _write_json(home / "ibkr-local.json", {"host": "127.0.0.1", "port": 7497})


def _flip_okx_credentials(home: Path) -> None:
    _write_json(
        home / "okx.json",
        {"api_key": "fake-key", "api_secret": "fake-secret", "passphrase": "fake-pass"},
    )


def _flip_robinhood_oauth_cache(home: Path) -> None:
    token_dir = home / "live" / "robinhood" / "oauth" / "mcp-oauth-token"
    token_dir.mkdir(parents=True, exist_ok=True)
    (token_dir / "token-entry.json").write_text("{}", encoding="utf-8")


def _flip_ibkr_official_mcp_entry(home: Path) -> None:
    _write_json(
        home / "agent.json",
        {
            "mcpServers": {
                "ibkr": {
                    "type": "streamableHttp",
                    "url": "https://api.ibkr.com/v1/api/mcp-public",
                    "auth": {
                        "type": "oauth",
                        "scopes": ["mcp.read"],
                        "client_name": "Vibe-Trading",
                        "cache_dir": str(home / "live" / "ibkr" / "oauth"),
                    },
                    "enabledTools": ["*"],
                }
            }
        },
    )


def _flip_local_plugin(home: Path) -> None:
    plugin_dir = home / "connectors" / "mybroker"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "adapter.py").write_text(
        "def check_status(config=None):\n    return {'status': 'ok'}\n", encoding="utf-8"
    )
    _write_json(
        plugin_dir / "connector.json",
        {
            "schema_version": 1,
            "profile": {
                "id": "mybroker-readonly",
                "connector": "mybroker",
                "label": "My Broker",
                "environment": "live",
                "capabilities": ["account.read", "positions.read"],
                "readonly": True,
            },
            "entrypoint": "adapter.py",
        },
    )


def test_clean_environment_reports_no_connector(clean_home: Path) -> None:
    """Empty runtime root + cleared credential envs → not configured."""
    assert has_configured_connector() is False


@pytest.mark.parametrize(
    ("branch_id", "flip"),
    [
        ("a_selection_marker", _flip_selection_marker),
        ("b_ibkr_local_config", _flip_ibkr_local_config),
        ("c_okx_credentials", _flip_okx_credentials),
        ("d_robinhood_oauth_cache", _flip_robinhood_oauth_cache),
        ("d_ibkr_official_mcp_entry", _flip_ibkr_official_mcp_entry),
        ("e_local_plugin", _flip_local_plugin),
    ],
)
def test_each_branch_flips_the_probe_true_in_isolation(clean_home: Path, branch_id: str, flip) -> None:
    """Every branch alone is sufficient — the probe is a union."""
    assert has_configured_connector() is False, f"precondition failed for {branch_id}"
    flip(clean_home)
    reset_availability_cache()
    assert has_configured_connector() is True, f"branch {branch_id} did not flip the probe"


def test_branch_c_etoro_env_credentials_flips_the_probe(clean_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """eToro's env credential fallback alone satisfies branch (c)."""
    from src.config.accessor import reset_env_config

    assert has_configured_connector() is False
    monkeypatch.setenv("ETORO_API_KEY", "fake-api-key")
    monkeypatch.setenv("ETORO_USER_KEY", "fake-user-key")
    reset_env_config()
    reset_availability_cache()
    assert has_configured_connector() is True


def test_probe_result_is_cached_until_reset(clean_home: Path) -> None:
    """The memo holds across config changes until explicitly reset."""
    assert has_configured_connector() is False
    _flip_selection_marker(clean_home)
    assert has_configured_connector() is False
    reset_availability_cache()
    assert has_configured_connector() is True


def test_tool_check_available_follows_the_probe(clean_home: Path) -> None:
    """Representative trading_* classes gate exactly on the probe."""
    from src.tools.trading_connector_tool import TradingAccountTool, TradingConnectionsTool

    assert TradingConnectionsTool.check_available() is False
    assert TradingAccountTool.check_available() is False
    _flip_selection_marker(clean_home)
    reset_availability_cache()
    assert TradingConnectionsTool.check_available() is True
    assert TradingAccountTool.check_available() is True


def test_probe_never_raises_and_fails_toward_visibility(clean_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising branch helper must not raise nor hide the tools."""

    def boom() -> bool:
        raise RuntimeError("simulated probe bug")

    monkeypatch.setattr(availability, "_BRANCHES", (boom, *availability._BRANCHES[1:]))
    assert has_configured_connector() is True


def test_okx_is_configured_is_config_only(clean_home: Path) -> None:
    """okx.is_configured(): False without credentials, True when complete."""
    from src.trading.connectors.okx import sdk as okx_sdk

    assert okx_sdk.is_configured() is False
    _flip_okx_credentials(clean_home)
    assert okx_sdk.is_configured() is True


def test_is_configured_never_raises_on_corrupt_config(clean_home: Path) -> None:
    """An unreadable config file reports False, never an exception."""
    from src.trading.connectors.okx import sdk as okx_sdk

    (clean_home / "okx.json").write_text("{not valid json", encoding="utf-8")
    assert okx_sdk.is_configured() is False


def test_etoro_is_configured_honours_env_fallback(clean_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """eToro completeness resolves file config plus env fallbacks."""
    from src.config.accessor import reset_env_config
    from src.trading.connectors.etoro import sdk as etoro_sdk

    assert etoro_sdk.is_configured() is False
    monkeypatch.setenv("ETORO_API_KEY", "fake-api-key")
    monkeypatch.setenv("ETORO_USER_KEY", "fake-user-key")
    reset_env_config()
    assert etoro_sdk.is_configured() is True
