"""Env-gated fixtures for the T7 single-tenant Web E2E (live local rig).

Isolation convention (mirrors ``e2e_backtest`` WITHOUT touching the gate
command): unless ``ENGINE_BRIDGE_E2E=1`` is set, every test module in this
directory is ignored at COLLECTION time (``pytest_ignore_collect``) — the
default suite stays green without the rig and without playwright installed.

Run shape::

    python3 agent/tests/e2e_engine_bridge/start_rig.py
    ENGINE_BRIDGE_E2E=1 /tmp/vt-e2e-venv/bin/python -m pytest \
        agent/tests/e2e_engine_bridge -q
    python3 agent/tests/e2e_engine_bridge/stop_rig.py --purge

Env knobs: ``ENGINE_BRIDGE_E2E_RIG_STATE`` (default
``/tmp/vt-e2e-rig/rig_state.json``), ``ENGINE_BRIDGE_E2E_EVIDENCE``
(default ``.omo/evidence/opencode-engine-bridge-v2/t7-e2e``).

Assertions are programmatic (DOM / network / SSE level) per the plan QA
rule (以断言而非肉眼为准); screenshots are evidence, never the verdict.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import pytest

from tests.e2e_engine_bridge.riglib import GatewayApi, GroupRecorder, sse_logger_script

REPO_ROOT = Path(__file__).resolve().parents[3]

E2E_ENABLED = os.environ.get("ENGINE_BRIDGE_E2E") == "1"


def pytest_ignore_collect(collection_path, config):
    """Ignore the test modules at collection unless the rig is enabled."""
    if not E2E_ENABLED and Path(collection_path).name.startswith("test_"):
        return True
    return None


@pytest.fixture(scope="session")
def rig_state() -> Dict[str, Any]:
    state_path = Path(
        os.environ.get("ENGINE_BRIDGE_E2E_RIG_STATE", "/tmp/vt-e2e-rig/rig_state.json")
    )
    if not state_path.exists():
        pytest.fail(f"rig state {state_path} missing — run start_rig.py first")
    return json.loads(state_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def evidence_dir() -> Path:
    path = Path(
        os.environ.get(
            "ENGINE_BRIDGE_E2E_EVIDENCE",
            str(
                REPO_ROOT / ".omo" / "evidence" / "opencode-engine-bridge-v2" / "t7-e2e"
            ),
        )
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="session")
def api(rig_state) -> GatewayApi:
    return GatewayApi(rig_state["gateway_url"], rig_state["api_key"])


@pytest.fixture(scope="session")
def i18n_en() -> Dict[str, Any]:
    return json.loads(
        (REPO_ROOT / "frontend" / "src" / "i18n" / "locales" / "en.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture(scope="session")
def browser_context(rig_state):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            base_url=rig_state["gateway_url"],
        )
        context.add_init_script(sse_logger_script(rig_state["api_key"]))
        yield context
        context.close()
        browser.close()


@pytest.fixture()
def page(browser_context):
    """One pre-authenticated page per test (auth key set by the init script)."""
    page = browser_context.new_page()
    page.goto("/agent", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => window.localStorage.getItem('vibe_trading_api_auth_key') !== null",
        timeout=15_000,
    )
    yield page
    page.close()


@pytest.fixture()
def recorder(request, evidence_dir):
    """Per-test assertion recorder; writes ``<test>-results.json`` on teardown."""
    rec = GroupRecorder(evidence_dir, request.node.name)
    yield rec
    rep = getattr(request.node, "rep_call", None)
    rec.write(rep.passed if rep is not None else False)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Expose the call-phase outcome to the recorder fixture teardown."""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)
