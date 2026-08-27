"""Connector availability probe for conditional trading-tool exposure.

This module answers one question: has the user configured ANY trading
connector? When the answer is false, the ``trading_*`` tools are dead weight
in the tool surface — every one of them can only answer "no connector
configured", yet they occupy disclosure budget and create mis-routing surface
against ``get_market_data`` (AUDIT Q10/K21).

Contract of :func:`has_configured_connector`:

* network-free and cheap; the result is memoized at module level
  (call :func:`reset_availability_cache` to clear it, e.g. in tests);
* NEVER raises — any internal error degrades to ``True`` (fail toward
  visibility: a probe bug must not hide working tools);
* returns ``True`` iff ANY of these five branches holds:

  a. selection marker: ``trading-connections.json`` exists (the user ran
     ``connector use`` / ``trading_select_connection``);
  b. local IBKR config: ``ibkr-local.json`` exists (``connector configure``);
  c. credentials-complete broker_sdk connector: any connector module's public
     ``is_configured()`` returns ``True`` (config/credential completeness
     only — no network, no optional SDK import);
  d. remote-MCP OAuth/config presence: a Robinhood OAuth token cache entry
     exists, or the ``agent.json`` ``mcp_servers`` config carries the IBKR
     official-MCP server entry;
  e. local plugins installed: ``discover_plugins()`` returns a valid plugin.

The union is deliberately broad: we prefer keeping dead weight over hiding a
working tool (a hidden working tool is a routing regression; a disclosed
unusable tool is only a disclosure tax). Because the probe is the single
source of truth both the agent registry (``build_registry`` skips classes
whose ``check_available()`` is false) and the MCP exposure gate consult,
every new connector MUST register its availability signal here — add its
probe to ``_BRANCHES`` (and, for credential-based connectors, an
``is_configured()`` to its ``sdk.py``). An unregistered connector is
invisible to the gate and its tools would be hidden while they work.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

#: broker_sdk connector modules publishing ``is_configured()``. Every new
#: credential/config-based connector MUST be added here (module docstring).
_SDK_CONNECTOR_MODULES: tuple[str, ...] = (
    "src.trading.connectors.okx.sdk",
    "src.trading.connectors.binance.sdk",
    "src.trading.connectors.alpaca.sdk",
    "src.trading.connectors.tiger.sdk",
    "src.trading.connectors.longbridge.sdk",
    "src.trading.connectors.futu.sdk",
    "src.trading.connectors.dhan.sdk",
    "src.trading.connectors.shoonya.sdk",
    "src.trading.connectors.etoro.sdk",
    "src.trading.connectors.trading212.sdk",
    "src.trading.connectors.mt5.sdk",
)

#: Memoized probe result; ``None`` means "not computed yet".
_CACHE: bool | None = None


def reset_availability_cache() -> None:
    """Drop the memoized probe result so the next call re-probes."""
    global _CACHE
    _CACHE = None


def has_configured_connector() -> bool:
    """Report whether the user has configured any trading connector.

    The probe is a deliberately broad five-branch union (module docstring),
    is network-free, memoizes its result, and never raises: any internal
    error returns ``True`` so a probe bug cannot hide a working tool.

    Returns:
        ``True`` when any availability branch holds (or on any probe error),
        ``False`` only when every branch deterministically reports absent.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        result = any(probe() for probe in _BRANCHES)
    except Exception:  # noqa: BLE001 - fail toward visibility, never raise
        result = True
    _CACHE = result
    return result


def _selection_marker_present() -> bool:
    """Branch (a): the user selected a connector profile at least once."""
    from src.trading import profiles

    return profiles.config_path().exists()


def _ibkr_local_config_present() -> bool:
    """Branch (b): a local IBKR TWS/Gateway config file was written."""
    from src.trading.connectors.ibkr import local

    return local.config_path().exists()


def _any_sdk_connector_configured() -> bool:
    """Branch (c): any broker_sdk connector reports config completeness."""
    for module_path in _SDK_CONNECTOR_MODULES:
        module = importlib.import_module(module_path)
        if module.is_configured():
            return True
    return False


def _remote_mcp_config_present() -> bool:
    """Branch (d): remote-MCP OAuth cache or official-MCP config present.

    File-based only: an IBKR official-MCP entry anywhere in ``agent.json``
    ``mcp_servers`` counts as configured, while Robinhood counts only when an
    OAuth token cache entry actually exists (its entry alone carries no
    authorization). The Robinhood cache is looked up at every configured
    ``auth.cache_dir`` plus the canonical default under the runtime root.
    """
    from src.config.loader import load_agent_config
    from src.config.paths import get_runtime_root
    from src.config.schema import live_broker_key_for_entry
    from src.live.registry import has_cached_oauth_token

    servers = load_agent_config().mcp_servers or {}
    robinhood_cache_dirs: list[str] = []
    for server_key, server in servers.items():
        broker = live_broker_key_for_entry(server_key, server)
        if broker == "ibkr":
            return True
        if broker == "robinhood":
            auth = getattr(server, "auth", None)
            cache_dir = str(getattr(auth, "cache_dir", "") or "") if auth is not None else ""
            if cache_dir:
                robinhood_cache_dirs.append(cache_dir)
    robinhood_cache_dirs.append(str(get_runtime_root() / "live" / "robinhood" / "oauth"))
    return any(has_cached_oauth_token("", cache_dir) for cache_dir in robinhood_cache_dirs)


def _local_plugins_installed() -> bool:
    """Branch (e): the user installed at least one local connector plugin."""
    from src.trading.local_plugins import discover_plugins

    plugins, _ = discover_plugins()
    return bool(plugins)


#: The five availability branches, evaluated in order with short-circuit.
#: Registration duty for new connectors: see the module docstring.
_BRANCHES: tuple[Callable[[], bool], ...] = (
    _selection_marker_present,
    _ibkr_local_config_present,
    _any_sdk_connector_configured,
    _remote_mcp_config_present,
    _local_plugins_installed,
)
