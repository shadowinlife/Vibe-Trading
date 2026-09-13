#!/usr/bin/env bash
set -euo pipefail

# ── Cleanup trap ──────────────────────────────────────────────────────────────
cleanup() {
    local exit_code=$?
    rm -f /tmp/entrypoint_render_err 2>/dev/null || true
    exit $exit_code
}
trap cleanup EXIT

# ── Activate Python virtual environment ───────────────────────────────────────
source /opt/venv/bin/activate 2>/dev/null || true

# ── VT Memory: enable full lifecycle + MCP tools (mymain branch feature) ──────
# VT_MEMORY_BASE_DIR is consolidated INSIDE the persisted home volume (B5) so it
# survives container recreation alongside Settings/.env and the runtime root.
export VT_MEMORY="${VT_MEMORY:-full}"
export VT_MEMORY_MCP_TOOLS="${VT_MEMORY_MCP_TOOLS:-1}"
export VT_MEMORY_BASE_DIR="${VT_MEMORY_BASE_DIR:-/home/opencode/.vibe-trading/.vt-memory}"
mkdir -p "$VT_MEMORY_BASE_DIR"

# ── Fix broken venv symlinks (base image uv Python at /root/ is inaccessible) ──
if [ -f /usr/bin/python3 ] && [ ! -x /opt/venv/bin/python3 ]; then
    ln -sf /usr/bin/python3 /opt/venv/bin/python
    ln -sf /usr/bin/python3 /opt/venv/bin/python3
    ln -sf /usr/bin/python3.12 /opt/venv/bin/python3.11 2>/dev/null || true
    ln -sf /usr/bin/python3.12 /opt/venv/bin/python3.12 2>/dev/null || true
    echo "[entrypoint] Fixed venv python symlinks → /usr/bin/python3"
fi

# ── Read environment variables with defaults ──────────────────────────────────
CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-}"
CLICKHOUSE_PORT="${CLICKHOUSE_PORT:-8123}"
CLICKHOUSE_DATABASE="${CLICKHOUSE_DATABASE:-ashare}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-default}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-}"

# ── Ensure target directory exists ────────────────────────────────────────────
mkdir -p /home/opencode/.opencode

# ── Render opencode.json from Jinja2 template ─────────────────────────────────
TEMPLATE="/workspace/.opencode/opencode.json.tmpl"
TARGET="/home/opencode/.opencode/opencode.json"
FALLBACK="/workspace/.opencode/opencode.json.fallback"

# Rendering lives in config/render_config.py (single source of truth, covered
# by OpencodeAgent/tests/test_config_render.py). It also compiles the tool
# governance manifest (vibe-trading-tools.json) into opencode permission
# denies, so disabled VT tools never reach the model's tool list.
render_config() {
    /opt/venv/bin/python3 /workspace/.opencode/render_config.py \
        --template "$TEMPLATE" \
        --manifest /workspace/.opencode/vibe-trading-tools.json \
        --target "$TARGET"
}

if render_config 2>/tmp/entrypoint_render_err; then
    echo "[entrypoint] opencode.json rendered from template → $TARGET"
    rm -f /tmp/entrypoint_render_err
else
    echo "[entrypoint] WARNING: Jinja2 render failed: $(tr '\n' ' ' < /tmp/entrypoint_render_err 2>/dev/null)"
    rm -f /tmp/entrypoint_render_err
    if [ -f "$FALLBACK" ]; then
        cp "$FALLBACK" "$TARGET"
        echo "[entrypoint] Using fallback config: $FALLBACK"
    else
        echo "[entrypoint] ERROR: No fallback config at $FALLBACK, writing minimal config"
        cat > "$TARGET" << 'EOFMIN'
{
  "model": "alibaba-cn/qwen3.8-max",
  "plugin": ["oh-my-openagent@4.19.4"]
}
EOFMIN
    fi
fi

# ── Probe ClickHouse connectivity ─────────────────────────────────────────────
if [ -n "$CLICKHOUSE_HOST" ]; then
    if command -v clickhouse-client &>/dev/null; then
        echo "[entrypoint] Probing ClickHouse at $CLICKHOUSE_HOST:$CLICKHOUSE_PORT ..."
        if clickhouse-client \
            --host "$CLICKHOUSE_HOST" \
            --port "$CLICKHOUSE_PORT" \
            --user "$CLICKHOUSE_USER" \
            ${CLICKHOUSE_PASSWORD:+--password "$CLICKHOUSE_PASSWORD"} \
            --query "SELECT 1" \
            --connect_timeout 5 \
            --max_execution_time 5 \
            2>/dev/null; then
            echo "[entrypoint] ClickHouse OK — warming schema cache"
            clickhouse-client \
                --host "$CLICKHOUSE_HOST" \
                --port "$CLICKHOUSE_PORT" \
                --user "$CLICKHOUSE_USER" \
                ${CLICKHOUSE_PASSWORD:+--password "$CLICKHOUSE_PASSWORD"} \
                --query "SELECT count() FROM system.tables WHERE database='$CLICKHOUSE_DATABASE'" \
                --connect_timeout 5 \
                2>/dev/null || true
        else
            echo "[entrypoint] WARNING: ClickHouse unreachable at $CLICKHOUSE_HOST:$CLICKHOUSE_PORT"
        fi
    else
        echo "[entrypoint] WARNING: clickhouse-client not found, skipping ClickHouse probe"
    fi
else
    echo "[entrypoint] INFO: CLICKHOUSE_HOST not set, skipping ClickHouse probe"
fi

# ── Symlink pre-built plugin cache to runtime config location ──────────────────
# The OMO plugin is installed during build at /workspace/.opencode/node_modules/
# but opencode reads config from /home/opencode/.opencode/ at runtime.
# Without this symlink, opencode re-downloads the plugin on first startup (~30s).
if [ -d /workspace/.opencode/node_modules ] && [ ! -e /home/opencode/.opencode/node_modules ]; then
    ln -sf /workspace/.opencode/node_modules /home/opencode/.opencode/node_modules
    echo "[entrypoint] Plugin cache symlinked: /workspace/.opencode/node_modules → /home/opencode/.opencode/node_modules"
fi

# ── Verify VT MCP server is importable ────────────────────────────────────────
# FastMCP >=2 no longer exposes the private ``_tool_manager`` attribute, so try
# the public ``list_tools()`` API first and fall back to import-only reporting.
VERIFY_VT=$(/opt/venv/bin/python3 -c "
import sys
try:
    sys.path.insert(0, '/opt/vibe-trading/agent')
    from mcp_server import mcp
    try:
        import asyncio
        print('OK:' + str(len(asyncio.run(mcp.list_tools()))))
    except Exception:
        print('OK:import-only')
except Exception as e:
    print('FAIL:' + str(e))
" 2>/dev/null || echo "FAIL:import_error")
if echo "$VERIFY_VT" | grep -q "^OK:"; then
    TOOL_COUNT=$(echo "$VERIFY_VT" | cut -d: -f2)
    case "$TOOL_COUNT" in
        ''|*[!0-9]*) echo "[entrypoint] VT MCP server OK (tool count unavailable in this FastMCP version)" ;;
        *) echo "[entrypoint] VT MCP server OK — $TOOL_COUNT tools registered" ;;
    esac
    echo "[entrypoint] VT_MEMORY=full, VT_MEMORY_MCP_TOOLS=1 → memory tools enabled"
    echo "[entrypoint] VT_MEMORY_BASE_DIR=$VT_MEMORY_BASE_DIR"
else
    echo "[entrypoint] WARNING: VT MCP server import failed: $VERIFY_VT"
fi

# ── Fail-closed auth gate (D9 / B2) ───────────────────────────────────────────
# The gateway treats a loopback peer as local (zero-auth). Behind the thin router
# the peer can be loopback, so a missing API_AUTH_KEY = unauthenticated public
# surface. Refuse to boot without it (security.py:_configured_api_key reads
# API_AUTH_KEY or the VIBE_TRADING_API_KEY alias).
if [ -z "${API_AUTH_KEY:-}" ] && [ -z "${VIBE_TRADING_API_KEY:-}" ]; then
    echo "[entrypoint] FATAL: API_AUTH_KEY (or VIBE_TRADING_API_KEY) is not set." >&2
    echo "[entrypoint] Fail-closed (D9/B2): the tenant gateway refuses to boot without an API auth key." >&2
    exit 1
fi

# ── B5 volume/state alignment gate ────────────────────────────────────────────
# helpers.py:31 hardcodes ENV_PATH = Path.home()/".vibe-trading"/".env" (ignores
# VIBE_TRADING_HOME), while paths.py:get_runtime_root() honors VIBE_TRADING_HOME.
# If they diverge, Settings writes land in the ephemeral layer and the runtime
# root lands elsewhere. Require VIBE_TRADING_HOME unset-or-equal to $HOME/.vibe-trading.
EXPECTED_VT_HOME="$HOME/.vibe-trading"
if [ -n "${VIBE_TRADING_HOME:-}" ] && [ "$VIBE_TRADING_HOME" != "$EXPECTED_VT_HOME" ]; then
    echo "[entrypoint] FATAL: VIBE_TRADING_HOME='$VIBE_TRADING_HOME' diverges from ENV_PATH base '$EXPECTED_VT_HOME' (B5)." >&2
    echo "[entrypoint] Settings/.env would land in the ephemeral layer. Unset it or set it exactly equal." >&2
    exit 1
fi
export VIBE_TRADING_HOME="$EXPECTED_VT_HOME"

# ── B6 MCP-subprocess env parity assertion ────────────────────────────────────
# The MCP subprocess env is fixed at spawn by the rendered opencode.json
# mcp.vibe-trading.env block. Assert it carries HOME/VIBE_TRADING_HOME identical
# to the gateway, so goal/session/run paths resolve to the same persisted root.
MCP_ENV_HOME=$(/opt/venv/bin/python3 -c "
import json
cfg = json.load(open('$TARGET'))
env = cfg.get('mcp', {}).get('vibe-trading', {}).get('env', {})
print(env.get('HOME', '') + '|' + env.get('VIBE_TRADING_HOME', ''))
" 2>/dev/null || echo "|")
MCP_HOME="${MCP_ENV_HOME%%|*}"
MCP_VT_HOME="${MCP_ENV_HOME##*|}"
if [ "$MCP_HOME" = "$HOME" ] && [ "$MCP_VT_HOME" = "$EXPECTED_VT_HOME" ]; then
    echo "[entrypoint] MCP env parity OK — HOME=$MCP_HOME VIBE_TRADING_HOME=$MCP_VT_HOME (identical to gateway)"
else
    echo "[entrypoint] WARNING: MCP env parity mismatch (B6): mcp.HOME='$MCP_HOME' mcp.VIBE_TRADING_HOME='$MCP_VT_HOME' vs gateway HOME='$HOME' VIBE_TRADING_HOME='$EXPECTED_VT_HOME'" >&2
fi

# ── Start the dual-process stack under supervisord (plan T10 process ruling) ──
# supervisord manages: (a) opencode serve on 127.0.0.1:4096 (internal-only) and
# (b) the vt gateway on 0.0.0.0:$GATEWAY_PORT (the single public port, a pure
# HTTP client of serve). serve auto-restarts on crash; the bridge heals via T6
# liveness + _ensure_pumps resume-on-next-send WITHOUT a gateway restart.
echo "[entrypoint] Starting supervisord (opencode serve 127.0.0.1:4096 + vt gateway 0.0.0.0:${GATEWAY_PORT:-8080})"
exec /opt/venv/bin/supervisord -c /etc/supervisord.conf