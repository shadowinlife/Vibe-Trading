"""T7 factory-wiring tests: the state.py engine switch + the wiring module.

Covers the D1 factory contract without a live serve:

* ``VIBE_TRADING_ENGINE`` unset/``native`` -> the untouched native
  ``SessionService`` (default-path regression guard);
* ``opencode`` -> ``RecoverableOpencodeSessionService`` with a real
  ``OpencodeDriver`` (env-driven base URL) and the REAL T4
  ``EventTranslator`` injected (quiescence/child-events from the schema);
* unknown engine value -> loud ``ValueError``;
* ``start_session_service``: tool-map load -> translator map replacement ->
  subscribe-first pumps -> reconcile, in order; a serve down at startup
  propagates (loud) and leaves the pumps unstarted;
* ``preflight_engine_bridge``: skips a disabled session runtime (None
  service), starts a built one;
* ``stop_engine_bridge``: drains the state singleton when it has an
  ``aclose`` seam, no-op for the native shape.

Live browser E2E against a real rig: ``agent/tests/e2e_engine_bridge/``
(env-gated, ``ENGINE_BRIDGE_E2E=1``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional

import pytest

import src.api.state as state_mod
from src.api import _compat
from src.config.accessor import reset_env_config
from src.opencode_bridge import (
    EventTranslator,
    OpencodeDriver,
    RecoverableOpencodeSessionService,
    build_session_service,
    preflight_engine_bridge,
    start_session_service,
    stop_engine_bridge,
)
from src.opencode_bridge.errors import OpencodeConnectionError
from src.opencode_bridge.tool_names import ToolNameMap
from src.session.events import EventBus
from src.session.service import SessionService
from src.session.store import SessionStore

from tests.test_opencode_bridge_service import FakeDriver

TMAP = ToolNameMap(
    prefixed_to_bare={"vibe-trading_read_file": "read_file"},
    server_prefixes=("vibe-trading_",),
)


class WiringDriver(FakeDriver):
    """FakeDriver + the two startup surfaces ``start_session_service`` uses."""

    def __init__(self) -> None:
        super().__init__()
        self.map_loads = 0
        self.load_error: Optional[Exception] = None

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:1"

    async def load_tool_mapping(self) -> ToolNameMap:
        self.map_loads += 1
        if self.load_error is not None:
            raise self.load_error
        return TMAP


def _reset_service_singleton() -> None:
    state_mod._session_service = None
    _compat.set_host_attr("_session_service", None)


@pytest.fixture()
def clean_singleton():
    _reset_service_singleton()
    yield
    _reset_service_singleton()


def _factory_env(monkeypatch, tmp_path: Path, engine: Optional[str]) -> None:
    monkeypatch.setattr(state_mod, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(state_mod, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setenv("ENABLE_SESSION_RUNTIME", "true")
    if engine is None:
        monkeypatch.delenv("VIBE_TRADING_ENGINE", raising=False)
    else:
        monkeypatch.setenv("VIBE_TRADING_ENGINE", engine)
    reset_env_config()


# ---------------------------------------------------------------------------
# state.py factory switch (D1)
# ---------------------------------------------------------------------------


def test_factory_default_builds_native_service(monkeypatch, tmp_path, clean_singleton):
    _factory_env(monkeypatch, tmp_path, engine=None)

    service = state_mod._get_session_service()

    assert isinstance(service, SessionService)
    assert not isinstance(service, RecoverableOpencodeSessionService)


def test_factory_native_explicit_builds_native_service(
    monkeypatch, tmp_path, clean_singleton
):
    _factory_env(monkeypatch, tmp_path, engine="native")

    assert isinstance(state_mod._get_session_service(), SessionService)


def test_factory_opencode_builds_wired_bridge_service(
    monkeypatch, tmp_path, clean_singleton
):
    _factory_env(monkeypatch, tmp_path, engine="opencode")
    monkeypatch.setenv("OPENCODE_BASE_URL", "http://127.0.0.1:14096")
    monkeypatch.setenv("OPENCODE_BRIDGE_QUIESCENCE_S", "9.5")
    monkeypatch.setenv("OPENCODE_BRIDGE_CHILD_EVENTS", "drop")
    reset_env_config()

    service = state_mod._get_session_service()

    assert isinstance(service, RecoverableOpencodeSessionService)
    driver = service._driver
    translator = service._translator
    assert isinstance(driver, OpencodeDriver)
    assert driver.base_url == "http://127.0.0.1:14096"
    assert isinstance(translator, EventTranslator)
    assert translator._quiescence_s == 9.5
    assert translator.tool_map.prefixed_to_bare == {}  # loaded at start()
    assert service.store is not None and service.event_bus is not None


def test_factory_unknown_engine_raises(monkeypatch, tmp_path, clean_singleton):
    _factory_env(monkeypatch, tmp_path, engine="pydantic-ai")

    with pytest.raises(ValueError, match="native\\|opencode"):
        state_mod._get_session_service()


# ---------------------------------------------------------------------------
# wiring.start_session_service / preflight / stop
# ---------------------------------------------------------------------------


def _build_stub_service(tmp_path: Path, driver: WiringDriver):
    store = SessionStore(base_dir=tmp_path / "sessions")
    bus = EventBus()
    translator = EventTranslator(quiescence_s=8.0)
    service = RecoverableOpencodeSessionService(
        store=store,
        event_bus=bus,
        runs_dir=tmp_path / "runs",
        driver=driver,
        translator=translator,
    )
    return service, translator


def test_start_loads_map_then_subscribes_then_reconciles(tmp_path):
    driver = WiringDriver()
    service, translator = _build_stub_service(tmp_path, driver)

    async def main():
        report = await start_session_service(service)
        pump_started = service._pump_task is not None
        await service.aclose()
        assert (report.reattached, report.backfilled, report.interrupted) == (
            (),
            (),
            (),
        )
        assert pump_started
        assert driver.map_loads == 1
        assert translator.tool_map is TMAP

    asyncio.run(main())


def test_start_propagates_dead_serve_and_leaves_pumps_down(tmp_path):
    driver = WiringDriver()
    driver.load_error = OpencodeConnectionError("serve down")
    service, translator = _build_stub_service(tmp_path, driver)

    async def main():
        with pytest.raises(OpencodeConnectionError):
            await start_session_service(service)
        assert service._pump_task is None
        assert translator.tool_map.prefixed_to_bare == {}
        await service.aclose()

    asyncio.run(main())


def test_preflight_skips_disabled_runtime_and_starts_built_service(tmp_path):
    driver = WiringDriver()
    service, _ = _build_stub_service(tmp_path, driver)

    async def main():
        await preflight_engine_bridge(lambda: None)
        assert driver.map_loads == 0
        await preflight_engine_bridge(lambda: service)
        assert driver.map_loads == 1
        assert service._pump_task is not None
        await service.aclose()

    asyncio.run(main())


def test_stop_engine_bridge_drains_state_singleton(tmp_path):
    driver = WiringDriver()
    service, _ = _build_stub_service(tmp_path, driver)

    async def main():
        state_mod._session_service = SessionStore(base_dir=tmp_path / "x")
        await stop_engine_bridge()  # native-shaped (no aclose) -> no-op

        state_mod._session_service = service
        await service.start()
        await stop_engine_bridge()
        assert driver.closed

    try:
        asyncio.run(main())
    finally:
        state_mod._session_service = None


def test_build_session_service_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_BASE_URL", "http://127.0.0.1:14096")
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "secret")
    monkeypatch.setenv("OPENCODE_BRIDGE_QUIESCENCE_S", "10")
    reset_env_config()

    service = build_session_service(
        SessionStore(base_dir=tmp_path / "sessions"),
        EventBus(),
        tmp_path / "runs",
    )

    assert isinstance(service, RecoverableOpencodeSessionService)
    assert service._driver.base_url == "http://127.0.0.1:14096"
    assert service._translator._quiescence_s == 10.0
    # T9: the IM streaming producer is attached at the composition root.
    from src.opencode_bridge.im_stream import ImStreamProducer

    observer = service.vt_event_observer
    assert observer is not None and isinstance(observer.__self__, ImStreamProducer)


def test_package_exports_public_surface():
    import src.opencode_bridge as bridge

    expected = {
        "EventTranslator",
        "ImStreamProducer",
        "OpencodeDriver",
        "OpencodeSessionService",
        "RecoverableOpencodeSessionService",
        "ReconciliationReport",
        "build_session_service",
        "preflight_engine_bridge",
        "start_session_service",
        "stop_engine_bridge",
        "delete_engine_session",
        "ENGINE_SESSION_CONFIG_KEY",
    }
    missing: List[str] = sorted(name for name in expected if not hasattr(bridge, name))
    assert missing == []
    assert set(bridge.__all__) >= expected
