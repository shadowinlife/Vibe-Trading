"""Static wiring tests for the dual-process container entrypoint (T8).

The image must serve opencode (:4096) *and* the dedicated strategy-run entry
(``VT_STRATEGY_RUN_PORT``, default 8000), supervise both, and fail the
container when either one dies. These tests inspect the shipped files so a
regression in that contract fails without a Docker build.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

OPENCODE_AGENT_DIR = Path(__file__).resolve().parents[1]
ENTRYPOINT_PATH = OPENCODE_AGENT_DIR / "entrypoint.sh"
DOCKERFILE_PATH = OPENCODE_AGENT_DIR / "Dockerfile"
READINESS_PATH = OPENCODE_AGENT_DIR / "scripts" / "check_startup_readiness.sh"


@pytest.fixture(scope="module")
def entrypoint() -> str:
    return ENTRYPOINT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def readiness() -> str:
    return READINESS_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "path", [ENTRYPOINT_PATH, READINESS_PATH], ids=["entrypoint", "readiness"]
)
def test_shell_scripts_parse(path: Path) -> None:
    bash = shutil.which("bash")
    assert bash is not None, "bash is required for shell syntax checks"
    result = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_opencode_command_and_warmup_are_unchanged(entrypoint: str) -> None:
    assert "opencode serve --port 4096 --hostname 0.0.0.0 &" in entrypoint
    assert "http://127.0.0.1:4096/config?directory=/home/opencode" in entrypoint
    assert "sleep 20" in entrypoint


def test_entrypoint_starts_the_dedicated_service_entry(entrypoint: str) -> None:
    assert (
        "/opt/venv/bin/python /opt/vibe-trading/agent/strategy_run_server.py"
        in entrypoint
    )
    assert 'VT_STRATEGY_RUN_HOST="${VT_STRATEGY_RUN_HOST:-0.0.0.0}"' in entrypoint
    assert 'VT_STRATEGY_RUN_PORT="${VT_STRATEGY_RUN_PORT:-8000}"' in entrypoint
    assert '--host "$VT_STRATEGY_RUN_HOST"' in entrypoint
    assert '--port "$VT_STRATEGY_RUN_PORT"' in entrypoint


def test_entrypoint_supervises_both_processes(entrypoint: str) -> None:
    assert "wait -n" in entrypoint
    assert "pid_running()" in entrypoint
    assert "jobs -pr" in entrypoint
    assert "trap 'shutdown_and_exit 143' TERM" in entrypoint
    assert "trap 'shutdown_and_exit 130' INT" in entrypoint
    assert "kill -TERM" in entrypoint
    assert "kill -KILL" in entrypoint
    supervisor = entrypoint.split("wait -n", 1)[1]
    assert "stop_children" in supervisor
    assert "exit 1" in supervisor


def test_entrypoint_gates_startup_on_the_entry_health(entrypoint: str) -> None:
    assert (
        'ENTRY_HEALTH_URL="http://127.0.0.1:${VT_STRATEGY_RUN_PORT}/health"'
        in entrypoint
    )
    assert 'curl -sf --max-time 2 "$ENTRY_HEALTH_URL"' in entrypoint
    assert "VT_STRATEGY_RUN_HEALTH_BUDGET_S" in entrypoint
    assert "refusing to serve opencode without the strategy-run entry" in entrypoint


def test_entrypoint_never_starts_the_full_api_server_or_scheduler(
    entrypoint: str,
) -> None:
    assert "api_server.py --host" not in entrypoint
    assert "VT_STRATEGY_RUN_ENABLED" not in entrypoint


def test_entrypoint_never_creates_or_bakes_the_rm_token(
    entrypoint: str, dockerfile: str, readiness: str
) -> None:
    for text in (entrypoint, dockerfile, readiness):
        assert "RM_VT_STRATEGY_RUN_TOKEN_FILE" not in text
        assert "scp " not in text
        assert re.search(r"Bearer\s+[A-Za-z0-9._-]{8,}", text) is None


def test_dockerfile_exposes_both_ports_and_keeps_healthcheck(
    dockerfile: str,
) -> None:
    assert "EXPOSE 4096 8000" in dockerfile
    assert (
        "HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3"
        in dockerfile
    )
    assert "http://localhost:4096/health" in dockerfile
    assert "http://localhost:${VT_STRATEGY_RUN_PORT:-8000}/health" in dockerfile
    assert 'ENTRYPOINT ["/workspace/entrypoint.sh"]' in dockerfile


def test_readiness_script_covers_the_entry_in_offline_mode(readiness: str) -> None:
    assert 'OFFLINE="${OFFLINE:-1}"' in readiness
    assert 'REQUIRE_IMAGE="${REQUIRE_IMAGE:-0}"' in readiness
    assert "strategy-run entry listening" in readiness
    assert 'ENTRY_HEALTH_URL="http://127.0.0.1:${ENTRY_PORT}/health"' in readiness
    assert '"$ENTRY_HEALTH_URL"' in readiness
    assert "--network none" in readiness
    assert "entry /health 200" in readiness


def test_readiness_script_gates_on_the_mcp_connection(readiness: str) -> None:
    assert 'MCP_MARKER="[entrypoint] VT MCP connected"' in readiness
    assert 'MCP_URL="http://127.0.0.1:4096/mcp?directory=/workspace"' in readiness
    assert '"vibe-trading"[[:space:]]*' in readiness
    assert "VT MCP 未连接" in readiness
    assert '-e "VT_STARTUP_BUDGET_S=$STARTUP_BUDGET_S"' in readiness


def test_entrypoint_fails_without_a_connected_vt_mcp(entrypoint: str) -> None:
    assert "vt_mcp_connected()" in entrypoint
    assert (
        'OPENCODE_MCP_URL="http://127.0.0.1:4096/mcp?directory=/workspace"'
        in entrypoint
    )
    assert 'VT_STARTUP_BUDGET_S="${VT_STARTUP_BUDGET_S:-300}"' in entrypoint
    assert "refusing to serve without the finance tool surface" in entrypoint
    assert (
        "VT MCP connected — vibe-trading tools are in the model tool surface"
        in entrypoint
    )
    fatal = re.search(
        r"FATAL: VT MCP server 'vibe-trading' not connected[\s\S]{0,400}?\n(\s*)exit 1",
        entrypoint,
    )
    assert fatal, "the VT MCP gate must exit non-zero, not just log"
    exhausted = re.search(
        r"budget was spent before the VT MCP gate[\s\S]{0,200}?\n(\s*)exit 1",
        entrypoint,
    )
    assert exhausted, "an exhausted startup budget must also fail the container"


def test_entrypoint_writes_config_for_both_opencode_scopes(entrypoint: str) -> None:
    assert 'TARGET="${HOME:-/home/opencode}/.opencode/opencode.json"' in entrypoint
    assert 'PROJECT_TARGET="/workspace/.opencode/opencode.json"' in entrypoint
    assert 'cp "$TARGET" "$PROJECT_TARGET"' in entrypoint
    # Both the rendered path and the fallback path must publish the config, or a
    # render failure silently loses the project-scope copy.
    calls = re.findall(r"^\s+finalize_config$", entrypoint, flags=re.MULTILINE)
    assert len(calls) == 2, f"expected 2 finalize_config calls, found {len(calls)}"
    assert 'chmod 600 "$TARGET"' in entrypoint
    assert 'chmod 600 "$PROJECT_TARGET"' in entrypoint


def test_project_scope_copy_drops_the_plugin_list(entrypoint: str) -> None:
    # A plugin entry in the project config makes opencode install the whole
    # bundle into /workspace/.opencode/node_modules on the first request
    # (~70s); the global scope already loads it from the pre-baked cache.
    finalize = entrypoint.split("finalize_config()", 1)[1].split("\n}", 1)[0]
    assert 'config.pop("plugin", None)' in finalize
    assert 'json.dump(config' in finalize


def test_entrypoint_renders_from_a_workspace_independent_template_source(
    entrypoint: str,
) -> None:
    assert 'CONFIG_FALLBACK_DIR="/opt/opencode-config"' in entrypoint
    assert "$CONFIG_FALLBACK_DIR/opencode.json.tmpl" in entrypoint
    assert "$CONFIG_FALLBACK_DIR/render_config.py" in entrypoint


def test_entrypoint_fallback_config_keeps_the_vt_mcp_block(entrypoint: str) -> None:
    fallback = entrypoint.split("write_fallback_config()", 1)[1].split("\n}", 1)[0]
    assert '"mcp"' in fallback
    assert '"vibe-trading"' in fallback
    assert '"/opt/vibe-trading/agent/mcp_server.py"' in fallback
    assert '"environment"' in fallback
    assert '"CLICKHOUSE_HOST": env("CLICKHOUSE_HOST")' in fallback


def test_fallback_config_generator_emits_valid_mcp_json(tmp_path: Path) -> None:
    """Execute the shipped fallback generator instead of trusting its source text."""
    body = ENTRYPOINT_PATH.read_text(encoding="utf-8")
    heredoc = body.split("write_fallback_config() {", 1)[1]
    script = heredoc.split("<< 'EOFPY'\n", 1)[1].split("\nEOFPY\n", 1)[0]
    target = tmp_path / "opencode.json"
    env = {
        **os.environ,
        "TARGET": str(target),
        "CLICKHOUSE_HOST": "ch.test",
        "CLICKHOUSE_LLM_USER": "llm_role",
        "OMO_VERSION": "5.1.35",
    }
    subprocess.run([sys.executable, "-c", script], env=env, check=True)
    config = json.loads(target.read_text(encoding="utf-8"))
    server = config["mcp"]["vibe-trading"]
    assert server["type"] == "local"
    assert "env" not in server
    assert server["environment"]["CLICKHOUSE_HOST"] == "ch.test"
    assert server["environment"]["CLICKHOUSE_LLM_USER"] == "llm_role"
    assert server["environment"]["VT_MEMORY_MCP_TOOLS"] == "1"
    assert config["plugin"] == ["oh-my-openagent@5.1.35"]


def test_dockerfile_ships_the_immutable_renderer_inputs(dockerfile: str) -> None:
    assert (
        "COPY config/opencode.json.tmpl config/vibe-trading-tools.json "
        "config/render_config.py /opt/opencode-config/" in dockerfile
    )
