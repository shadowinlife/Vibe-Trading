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
export VT_MEMORY="${VT_MEMORY:-full}"
export VT_MEMORY_MCP_TOOLS="${VT_MEMORY_MCP_TOOLS:-1}"
export VT_MEMORY_BASE_DIR="${VT_MEMORY_BASE_DIR:-/workspace/.vt-memory}"
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
        cat > "$TARGET" << EOFMIN
{
  "model": "alibaba-cn/qwen3.8-max",
  "plugin": ["oh-my-openagent@${OMO_VERSION:-5.1.0}"]
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

# ── Verify the pre-baked OMO plugin cache is present ─────────────────────────
# opencode >=1.18 loads config plugins from
#   ~/.cache/opencode/packages/<spec>/node_modules/<name>
# with zero network access when that directory exists (pre-baked in the
# Dockerfile via npm install). A volume mounted over ~/.cache/opencode would
# MASK the pre-baked cache and force a ~241MB registry download on the first
# API request — warn loudly so the regression is visible in container logs.
# (The old /workspace/.opencode/node_modules symlink targeted a pre-1.18
# plugin path that opencode no longer reads — removed as dead code.)
if ls -d /home/opencode/.cache/opencode/packages/oh-my-openagent@*/node_modules/oh-my-openagent >/dev/null 2>&1; then
    echo "[entrypoint] OMO plugin cache present: $(ls -d /home/opencode/.cache/opencode/packages/oh-my-openagent@* | head -1)"
else
    echo "[entrypoint] WARNING: OMO plugin cache missing — opencode will download it on first request (needs npm registry access; see Dockerfile pre-bake)"
fi

# ── Verify VT MCP server is importable ────────────────────────────────────────
# FastMCP >=2 no longer exposes the private ``_tool_manager`` attribute, so try
# the public ``list_tools()`` API first and fall back to import-only reporting.
# The Dockerfile already gates this import at build time, so the runtime check
# is log-visibility only; skip it with SKIP_VT_VERIFY=1 to save ~1.5-3s.
if [ "${SKIP_VT_VERIFY:-0}" = "1" ]; then
    echo "[entrypoint] SKIP_VT_VERIFY=1 — skipping VT MCP server verification (build-time gate already passed)"
else
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
fi

# ── Background warmup of the app instance ─────────────────────────────────────
# First bootstrap of a fresh container performs one-time LOCAL init (plugin
# module load, skill index build, db setup). It is CPU-only — no downloads,
# everything is pre-baked in the image — but it blocks the first API request
# for seconds (native amd64) up to ~a minute (QEMU emulation). Fire a
# read-only warmup request in the background so the first real client request
# hits a warm instance. Retries until the server accepts connections.
(
    for _ in $(seq 1 90); do
        if curl -sf --max-time 240 -u opencode:"${OPENCODE_SERVER_PASSWORD:-}" \
            "http://localhost:4096/config?directory=/home/opencode" >/dev/null 2>&1; then
            echo "[entrypoint] warmup complete — first client request will be fast"
            exit 0
        fi
        sleep 2
    done
    echo "[entrypoint] WARNING: warmup did not complete within 180 retries"
) &

# ── Start opencode serve ──────────────────────────────────────────────────────
exec opencode serve --port 4096 --hostname 0.0.0.0