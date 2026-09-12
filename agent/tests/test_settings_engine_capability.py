"""Settings-surface engine-capability contract (T15, opencode engine bridge).

Pins that the Settings HTTP surface is **engine-agnostic**: under
``VIBE_TRADING_ENGINE=opencode`` the ``/settings/llm`` and
``/settings/data-sources`` endpoints serve the *identical* response shape they
serve under the native engine, so the bundled frontend (a ZERO-diff guardrail
of the engine-bridge plan) keeps working unchanged on either engine. These are
contract pins + regression guards: the settings routes never read the engine
switch, so a future edit that accidentally makes the surface engine-dependent
(and breaks the frontend) fails here.

No live rig is required (TestClient + monkeypatched dotenv), so this suite runs
in the default gate — unlike the ``ENGINE_BRIDGE_E2E``-gated rig suites under
``agent/tests/e2e_engine_bridge/``.

T15 decisions (canonical operational rationale lives in
``mymain-wiki/features/f8-engine-bridge.md``; this docstring is the code-side
record the plan's T15 row asks for):

* ``sse_timeout_seconds`` — MUST keep being served under BOTH engines: it is
  the frontend streaming-watchdog input (``Agent.tsx:1241-1243`` arms
  ``sseTimeoutMsRef`` from it; ``Agent.tsx:1247-1269`` archives a turn that
  sees no SSE event for that long). The chosen value is the *same* env-driven
  ``VIBE_TRADING_SSE_TIMEOUT`` (default 90) under both engines — no value
  change. Under opencode the *semantics* differ, not the number: the bridge
  synthesizes ``tool_heartbeat`` every 3 s while a tool part runs and an 8.0 s
  quiescence window (``OPENCODE_BRIDGE_QUIESCENCE_S``) bounds the post-
  completion gap, so a healthy turn's silent gaps stay ≈8 s — well under 90 s.
  The watchdog therefore only trips on a genuinely hung engine (same outer
  bound as native, where it guards tool silence + provider hangs). Weakening or
  engine-forking this value is forbidden (plan T15 Must-NOT).

* Settings→LLM under opencode — the *conversation* engine is opencode-managed
  (``opencode.json`` + opencode's own auth), NOT this page. ``LANGCHAIN_*``
  here still drives auto-title (D11, ``sessions_routes.py`` ChatLLM route) and
  swarm workers, so writes stay valid and are NOT blocked. The frontend has no
  read-only affordance for this section (its disabled states are provider-auth
  driven only — ``usesManagedAuth`` / ``api_key_required``), so per plan T15
  this degrades to a DOCUMENTATION NOTE (F8 card degradation item 6): zero
  frontend change, zero response-shape change.

* Settings→data-sources (B5 adjudication) — DOCUMENTATION-ONLY (F8 card
  degradation item 13), the plan-sanctioned binary choice. A write hot-applies
  to the GATEWAY process (``os.environ`` + ``registry.refresh_source_order_
  overrides`` + ``reset_env_config``), but the agent's MCP subprocess env is
  fixed at spawn — ``opencode serve`` owns it (T2 baseline_memo §2.3: "``.env``
  hot-apply is impossible for MCP subprocesses") — so agent-side data-source
  changes take effect only after an engine/container restart. The gateway
  self-restart option is REJECTED: it cannot re-spawn the MCP subprocess (that
  is ``opencode serve``'s child, not the gateway's), it would drop every
  in-flight turn/SSE stream, and in the host-direct production form a gateway
  restart is a systemd ``Restart=`` concern, not a request-handler side effect.
  No existing response field carries the caveat (``baostock_message`` is
  baostock-specific), so the response shape is unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

import pytest
from fastapi.testclient import TestClient

import api_server
from src.api import settings_routes
from src.config.accessor import get_env_config, reset_env_config


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Settings test client (mirrors ``test_settings_api.py``; loopback, no key)."""
    env_example = tmp_path / ".env.example"
    env_path = tmp_path / ".env"
    env_example.write_text(
        "\n".join(
            [
                "LANGCHAIN_PROVIDER=openrouter",
                "LANGCHAIN_MODEL_NAME=deepseek/deepseek-v4-pro",
                "OPENROUTER_BASE_URL=https://openrouter.ai/api/v1",
                "OPENROUTER_API_KEY=sk-or-v1-your-key-here",
                "LANGCHAIN_TEMPERATURE=0.2",
                "TIMEOUT_SECONDS=90",
                "MAX_RETRIES=3",
                "LANGCHAIN_REASONING_EFFORT=max",
                "TUSHARE_TOKEN=your-tushare-token",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(api_server, "ENV_PATH", env_path)
    monkeypatch.setattr(
        api_server, "LEGACY_ENV_PATH", tmp_path / "legacy" / ".env", raising=False
    )
    monkeypatch.setattr(api_server, "ENV_EXAMPLE_PATH", env_example)
    monkeypatch.setattr(api_server, "_baostock_supported", lambda: False)
    monkeypatch.setattr(api_server, "_baostock_installed", lambda: False)
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


@pytest.fixture
def engine_state(monkeypatch: pytest.MonkeyPatch) -> Callable[[Optional[str]], None]:
    """Factory putting the process in a given ``VIBE_TRADING_ENGINE`` state.

    Sets (or unsets) the env var and flushes the cached ``EnvConfig`` so
    ``get_env_config().opencode_bridge.vibe_trading_engine`` reflects it — the
    same input the ``state.py`` factory switch reads. Teardown restores the
    pristine config cache so later tests never inherit an engine value.
    """

    def _set(value: Optional[str]) -> None:
        if value is None:
            monkeypatch.delenv("VIBE_TRADING_ENGINE", raising=False)
        else:
            monkeypatch.setenv("VIBE_TRADING_ENGINE", value)
        reset_env_config()

    yield _set
    monkeypatch.delenv("VIBE_TRADING_ENGINE", raising=False)
    reset_env_config()


def test_engine_switch_input_is_readable_for_both_values(
    engine_state: Callable[[Optional[str]], None],
) -> None:
    """The factory-switch input (``state.py:64``) resolves for native|opencode.

    Guards that the settings contract below is exercised against a real
    opencode engine state, not a silently-ignored env var.
    """
    engine_state("opencode")
    assert get_env_config().opencode_bridge.vibe_trading_engine == "opencode"
    engine_state("native")
    assert get_env_config().opencode_bridge.vibe_trading_engine == "native"
    engine_state(None)
    # Unset falls back to the schema default (native) — the rollback path.
    assert get_env_config().opencode_bridge.vibe_trading_engine == "native"


def test_llm_settings_serve_sse_timeout_under_opencode_engine(
    client: TestClient,
    engine_state: Callable[[Optional[str]], None],
) -> None:
    """``sse_timeout_seconds`` MUST keep being served under ENGINE=opencode.

    It is the frontend watchdog input (``Agent.tsx:1241-1243``); the value is
    the env-driven default (90) on both engines — the bridge's 3 s heartbeat +
    8 s quiescence keep healthy-turn gaps well under it (see module docstring).
    """
    engine_state("opencode")
    response = client.get("/settings/llm")

    assert response.status_code == 200
    body = response.json()
    assert body["sse_timeout_seconds"] == 90
    assert isinstance(body["sse_timeout_seconds"], int)


def test_sse_timeout_stays_env_driven_under_opencode(
    client: TestClient,
    engine_state: Callable[[Optional[str]], None],
    tmp_path: Path,
) -> None:
    """The watchdog value is env-driven under opencode, not hardcoded.

    T11 seeds ``VIBE_TRADING_SSE_TIMEOUT`` per tenant; this pins that the
    opencode path honors it exactly as native does (no engine-specific fork).
    """
    (tmp_path / ".env").write_text(
        "VIBE_TRADING_SSE_TIMEOUT=300\nLANGCHAIN_PROVIDER=openrouter\n",
        encoding="utf-8",
    )
    engine_state("opencode")

    body = client.get("/settings/llm").json()
    assert body["sse_timeout_seconds"] == 300


def test_llm_settings_shape_identical_under_both_engines(
    client: TestClient,
    engine_state: Callable[[Optional[str]], None],
) -> None:
    """ZERO-diff guardrail: the LLM response shape is engine-agnostic.

    The opencode-managed conversation engine is a DOCUMENTATION note (F8 item
    6), not a response-shape change — the frontend has no read-only affordance
    to consume such a signal, so the shape must stay byte-for-byte stable.
    """
    engine_state("native")
    native = client.get("/settings/llm").json()
    engine_state("opencode")
    opencode = client.get("/settings/llm").json()

    assert sorted(native) == sorted(opencode)
    assert sorted(native) == sorted(settings_routes.LLMSettingsResponse.model_fields)


def test_data_sources_shape_identical_under_both_engines(
    client: TestClient,
    engine_state: Callable[[Optional[str]], None],
) -> None:
    """B5 adjudication is documentation-only (F8 item 13): engine-agnostic shape.

    No response field carries the restart caveat, so the data-source payload is
    identical under both engines and the frontend needs no change.
    """
    engine_state("native")
    native = client.get("/settings/data-sources").json()
    engine_state("opencode")
    opencode = client.get("/settings/data-sources").json()

    assert sorted(native) == sorted(opencode)
    assert sorted(native) == sorted(
        settings_routes.DataSourceSettingsResponse.model_fields
    )


def test_data_source_write_hot_applies_gateway_side_under_opencode(
    client: TestClient,
    engine_state: Callable[[Optional[str]], None],
    tmp_path: Path,
    _reset_source_order_env: None,
) -> None:
    """Under opencode a data-source write still hot-applies to the GATEWAY.

    This is the half of B5 that DOES take effect live (``os.environ`` +
    registry refresh); the MCP-subprocess half needs an engine restart and is
    documented (F8 item 13), not signaled in the response.
    """
    from backtest.loaders import registry

    engine_state("opencode")
    response = client.put(
        "/settings/data-sources",
        json={
            "source_orders": [
                {
                    "market": "a_share",
                    # mymain divergence (F5): permutation of the clickhouse-led
                    # local default chain.
                    "order": [
                        "tushare",
                        "clickhouse",
                        "tencent",
                        "mootdx",
                        "eastmoney",
                        "baostock",
                        "akshare",
                        "local",
                    ],
                },
            ],
        },
    )

    assert response.status_code == 200
    entry = next(
        e for e in response.json()["source_orders"] if e["market"] == "a_share"
    )
    assert entry["effective_order"][0] == "tushare"
    assert os.environ.get("MARKET_DATA_ORDER_A_SHARE", "").startswith("tushare,")
    assert registry.FALLBACK_CHAINS["a_share"][0] == "tushare"


@pytest.fixture()
def _reset_source_order_env() -> object:
    """Scrub order overrides from the process env + restore default chains.

    Mirrors ``test_settings_api.py``: the PUT handler hot-applies overrides
    into ``os.environ`` and the registry, so both must return to defaults.
    """
    from backtest.loaders import registry

    yield
    for key in [k for k in list(os.environ) if k.startswith("MARKET_DATA_ORDER_")]:
        os.environ.pop(key, None)
    registry.refresh_source_order_overrides()
