"""Regression tests for the entrypoint's VT credential bridge (LLM_* -> DASHSCOPE_*).

Executes the real bridge block extracted from entrypoint.sh under bash with a
controlled environment, so a regression in the shipped bytes fails here.
Dummy values only; no real credentials. The harness supplies LLM_API_KEY /
LLM_BASE_URL because entrypoint.sh assigns their defaults before the block.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT_PATH = Path(__file__).resolve().parents[1] / "entrypoint.sh"
BRIDGE_MARKER = "# ── VT credential bridge"
BAKED_DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _bridge_block() -> str:
    lines = ENTRYPOINT_PATH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(BRIDGE_MARKER))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("# ──"))
    return "\n".join(lines[start:end])


def _run_bridge(env: dict[str, str]) -> tuple[str, str]:
    script = (
        "set -euo pipefail\n"
        + _bridge_block()
        + '\nprintf "%s\\n%s\\n" "${DASHSCOPE_API_KEY:-}" "${DASHSCOPE_BASE_URL:-}"\n'
    )
    proc = subprocess.run(
        ["bash", "-c", script],
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env},
        capture_output=True,
        text=True,
        check=True,
    )
    key, base = proc.stdout.splitlines()[-2:]
    return key, base


@pytest.mark.parametrize(
    "env,expected_key,expected_base",
    [
        pytest.param(
            {
                "LLM_API_KEY": "proxy-key-dummy",
                "LLM_BASE_URL": "https://proxy.example.test/v1",
                "DASHSCOPE_BASE_URL": BAKED_DASHSCOPE_BASE,
            },
            "proxy-key-dummy",
            "https://proxy.example.test/v1",
            id="proxy_mode_bridges_key_and_endpoint",
        ),
        pytest.param(
            {
                "LLM_API_KEY": "",
                "LLM_BASE_URL": "",
                "DASHSCOPE_API_KEY": "direct-key-dummy",
                "DASHSCOPE_BASE_URL": BAKED_DASHSCOPE_BASE,
            },
            "direct-key-dummy",
            BAKED_DASHSCOPE_BASE,
            id="direct_mode_untouched",
        ),
        pytest.param(
            {
                "LLM_API_KEY": "proxy-key-dummy",
                "LLM_BASE_URL": "https://proxy.example.test/v1",
                "DASHSCOPE_API_KEY": "explicit-key-dummy",
                "DASHSCOPE_BASE_URL": BAKED_DASHSCOPE_BASE,
            },
            "explicit-key-dummy",
            BAKED_DASHSCOPE_BASE,
            id="explicit_dashscope_key_wins",
        ),
        pytest.param(
            {
                "LLM_API_KEY": "proxy-key-dummy",
                "LLM_BASE_URL": "",
                "DASHSCOPE_BASE_URL": BAKED_DASHSCOPE_BASE,
            },
            "proxy-key-dummy",
            BAKED_DASHSCOPE_BASE,
            id="key_without_proxy_base_bridges_key_only",
        ),
        pytest.param(
            {"LLM_API_KEY": "", "LLM_BASE_URL": ""},
            "",
            "",
            id="no_credentials_anywhere_stays_empty",
        ),
    ],
)
def test_credential_bridge(env: dict[str, str], expected_key: str, expected_base: str) -> None:
    assert _run_bridge(env) == (expected_key, expected_base)
