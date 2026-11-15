"""Shared environment hygiene for generated-code subprocesses."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

__all__ = ["build_scrubbed_runtime_env"]

_PROXY_ENV_KEYS = frozenset(
    {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
)

_RUNTIME_ENV_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "USERNAME",
        "USERPROFILE",
        "SHELL",
        "TMPDIR",
        "TEMP",
        "TMP",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "LANG",
        "TZ",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONNOUSERSITE",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "CURL_CA_BUNDLE",
        "TUSHARE_TOKEN",
        "FINNHUB_API_KEY",
        "ALPHAVANTAGE_API_KEY",
        "TIINGO_API_KEY",
        "FMP_API_KEY",
        "FRED_API_KEY",
        "VIBE_TRADING_IWENCAI_KEY",
        "VIBE_TRADING_SEC_UA",
        "VIBE_TRADING_DATA_CACHE",
        "VIBE_TRADING_ALLOWED_RUN_ROOTS",
        # Anonymous ClickHouse target for the A-share loaders. Credentials stay
        # in the parent: generated strategy code never sees them (VT-001).
        "CLICKHOUSE_HOST",
        "CLICKHOUSE_PORT",
        "CLICKHOUSE_DATABASE",
        "CCXT_EXCHANGE",
        "CCXT_TIMEOUT_MS",
        "CCXT_FETCH_BUDGET_S",
        "OKX_TIMEOUT_S",
        "OKX_FETCH_BUDGET_S",
        "RSSHUB_BASE_URL",
        "RSSHUB_TIMEOUT_S",
        "RSSHUB_FETCH_BUDGET_S",
        "FUTU_HOST",
        "FUTU_PORT",
        "VIBE_TRADING_EASTMONEY_MIN_INTERVAL",
        "VIBE_TRADING_SINA_MIN_INTERVAL",
        "VIBE_TRADING_STOOQ_MIN_INTERVAL",
        "VIBE_TRADING_YAHOO_MIN_INTERVAL",
        "VIBE_TRADING_SEC_MIN_INTERVAL",
        "VIBE_TRADING_FINNHUB_MIN_INTERVAL",
        "VIBE_TRADING_ALPHAVANTAGE_MIN_INTERVAL",
        "VIBE_TRADING_TIINGO_MIN_INTERVAL",
        "VIBE_TRADING_FMP_MIN_INTERVAL",
        "VIBE_TRADING_FRED_MIN_INTERVAL",
        "VIBE_TRADING_IWENCAI_MIN_INTERVAL",
        "VIBE_TRADING_THS_MIN_INTERVAL",
    }
    | _PROXY_ENV_KEYS
)

_RUNTIME_ENV_PREFIXES = ("LC_",)
_CLICKHOUSE_CREDENTIAL_ENV_KEYS = frozenset(
    {
        "CLICKHOUSE_USER",
        "CLICKHOUSE_PASSWORD",
        "CLICKHOUSE_LLM_USER",
        "CLICKHOUSE_LLM_PASSWORD",
    }
)


def _is_runtime_env_key_allowed(key: str) -> bool:
    """Return whether an environment key is safe for generated backtest code."""

    return key in _RUNTIME_ENV_KEYS or key.startswith(_RUNTIME_ENV_PREFIXES)


def _copy_runtime_env() -> dict[str, str]:
    """Copy the narrow environment needed by the backtest subprocess.

    Generated strategy code is executed in this subprocess, so avoid inheriting
    LLM, API server, broker, live-trading, or advisory credentials by default.
    The allowlist keeps OS/Python basics, proxy/cert settings, and read-only
    market-data configuration needed by the built-in loaders.
    """

    return {
        key: value
        for key, value in os.environ.items()
        if _is_runtime_env_key_allowed(key)
    }


def build_scrubbed_runtime_env(
    run_dir: Path,
    *,
    pythonpath_extra: str | Path | None = None,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the shared scrubbed environment for a generated-code subprocess.

    Parent values pass through the established runtime allowlist. Server-owned
    overrides are then applied, except ClickHouse user/password pairs, which
    are stripped from both sources. Required runtime guards are written last so
    overrides cannot weaken them.

    Args:
        run_dir: Current run directory, added as a resolved allowed run root.
        pythonpath_extra: Optional working path prepended to ``PYTHONPATH``.
        overrides: Optional server-owned environment values.

    Returns:
        A new environment mapping; neither the parent nor overrides are mutated.
    """

    env = _copy_runtime_env()
    for key in _CLICKHOUSE_CREDENTIAL_ENV_KEYS:
        env.pop(key, None)
    if overrides:
        env.update(
            {
                key: value
                for key, value in overrides.items()
                if key not in _CLICKHOUSE_CREDENTIAL_ENV_KEYS
            }
        )

    if pythonpath_extra:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(pythonpath_extra) + (
            os.pathsep + existing if existing else ""
        )

    allowed_run_roots = list(
        dict.fromkeys(
            item.strip()
            for item in env.get("VIBE_TRADING_ALLOWED_RUN_ROOTS", "").split(",")
            if item.strip()
        )
    )
    current_run_root = str(run_dir.resolve())
    if current_run_root not in allowed_run_roots:
        allowed_run_roots.append(current_run_root)
    env["VIBE_TRADING_ALLOWED_RUN_ROOTS"] = ",".join(allowed_run_roots)
    env["PATH"] = env.get("PATH") or os.defpath
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "VIBE_TRADING_BACKTEST_SANDBOX": "1",
        }
    )

    # Runner subsequently replaces HOME/USERPROFILE with its ephemeral sandbox
    # home. This shared layer deliberately does not add that Runner-only layer.
    return env
