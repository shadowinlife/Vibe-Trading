#!/usr/bin/env bash
set -euo pipefail

# ── Process supervision ───────────────────────────────────────────────────────
# The container serves two long-lived processes: opencode on :4096 and the
# dedicated strategy-run entry on ${VT_STRATEGY_RUN_PORT:-8000}. Both are
# load-bearing, so this script supervises them instead of waiting on opencode
# alone ("only MCP is up" must be a container failure, never a silent state).
SERVER_PID=""
ENTRY_PID=""
STOP_GRACE_S="${VT_ENTRYPOINT_STOP_GRACE_S:-10}"
case "$STOP_GRACE_S" in
    '' | *[!0-9]*) STOP_GRACE_S=10 ;;
esac

# Liveness from bash's job table: `kill -0` still answers for an exited child
# that has not been reaped yet (zombie), which would hide a dead service.
pid_running() {
    local pid="$1"
    [ -n "$pid" ] || return 1
    local running
    running=" $(jobs -pr 2>/dev/null | tr '\n' ' ') "
    case "$running" in
        *" $pid "*) return 0 ;;
    esac
    return 1
}

# TERM both children, wait a bounded grace period, KILL whatever ignored it,
# and reap the job table so no orphan/zombie survives this process.
stop_children() {
    local pids=""
    [ -n "$SERVER_PID" ] && pids="$SERVER_PID"
    [ -n "$ENTRY_PID" ] && pids="${pids:+$pids }$ENTRY_PID"
    [ -n "$pids" ] || return 0

    local pid
    # shellcheck disable=SC2086  # pids is a deliberate space-separated list
    kill -TERM $pids 2>/dev/null || true

    local deadline=$((SECONDS + STOP_GRACE_S))
    while [ "$SECONDS" -lt "$deadline" ]; do
        local alive=0
        for pid in $pids; do
            if pid_running "$pid"; then
                alive=1
            fi
        done
        [ "$alive" -eq 0 ] && break
        sleep 0.2
    done

    for pid in $pids; do
        if pid_running "$pid"; then
            printf '[entrypoint] process %s ignored TERM for %ss — sending KILL\n' \
                "$pid" "$STOP_GRACE_S"
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
    # shellcheck disable=SC2086
    wait $pids 2>/dev/null || true
    SERVER_PID=""
    ENTRY_PID=""
}

shutdown_and_exit() {
    local code="$1"
    trap - TERM INT
    printf '[entrypoint] received shutdown signal — stopping supervised processes\n'
    stop_children
    exit "$code"
}
# TERM/INT traps are installed once both children exist (see "Supervise both");
# before that there is nothing to stop.

# ── Cleanup trap ──────────────────────────────────────────────────────────────
cleanup() {
    local exit_code=$?
    trap - EXIT
    stop_children
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

# ── RM agent token directory ──────────────────────────────────────────────────
# RM writes ~/.config/rm/agent-token (0600) at provision time. Only the
# directory is created here — owner-only, explicitly, so it cannot inherit an
# ambient umask — and the entrypoint never creates or writes the token file
# itself (no static credential ships in the image). The directory is required
# for RM's token write, so failing to create it must fail the container.
RM_AGENT_CONFIG_DIR="${HOME:-/home/opencode}/.config/rm"
mkdir -p "$RM_AGENT_CONFIG_DIR"
if ! chmod 700 "$RM_AGENT_CONFIG_DIR" 2>/dev/null; then
    echo "[entrypoint] WARNING: cannot chmod 700 $RM_AGENT_CONFIG_DIR (owned by another uid); continuing"
fi
echo "[entrypoint] RM agent token dir ready: $RM_AGENT_CONFIG_DIR"

# ── Read environment variables with defaults ──────────────────────────────────
CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-}"
CLICKHOUSE_PORT="${CLICKHOUSE_PORT:-8123}"
CLICKHOUSE_DATABASE="${CLICKHOUSE_DATABASE:-ashare}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-default}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-}"

# ── LLM egress proxy env (contract A.7; empty LLM_BASE_URL = direct mode) ────
LLM_BASE_URL="${LLM_BASE_URL:-}"
LLM_API_KEY="${LLM_API_KEY:-}"
SANDBOX_ID="${HOSTNAME:-$(uname -n)}"

# ── Render opencode.json from the Jinja2 template ─────────────────────────────
# The build-time copies live under /workspace, which a deployment may shadow
# with a volume mount; /opt/opencode-config is the immutable copy baked into
# the image and backs the template/manifest/renderer when that happens.
CONFIG_DIR="/workspace/.opencode"
CONFIG_FALLBACK_DIR="/opt/opencode-config"
TEMPLATE="$CONFIG_DIR/opencode.json.tmpl"
MANIFEST="$CONFIG_DIR/vibe-trading-tools.json"
RENDERER="$CONFIG_DIR/render_config.py"
FALLBACK="$CONFIG_DIR/opencode.json.fallback"

# opencode resolves config from $HOME/.opencode and from the session
# directory's .opencode/. Write both: a pod whose HOME is not the image user
# must not boot without the rendered config (and therefore without the MCP).
TARGET="${HOME:-/home/opencode}/.opencode/opencode.json"
PROJECT_TARGET="/workspace/.opencode/opencode.json"
mkdir -p "$(dirname "$TARGET")"

# Rendering lives in config/render_config.py (single source of truth, covered
# by OpencodeAgent/tests/test_config_render.py). It also compiles the tool
# governance manifest (vibe-trading-tools.json) into opencode permission
# denies, so disabled VT tools never reach the model's tool list.
render_config() {
    local template="$TEMPLATE" manifest="$MANIFEST" renderer="$RENDERER"
    [ -f "$template" ] || template="$CONFIG_FALLBACK_DIR/opencode.json.tmpl"
    [ -f "$manifest" ] || manifest="$CONFIG_FALLBACK_DIR/vibe-trading-tools.json"
    [ -f "$renderer" ] || renderer="$CONFIG_FALLBACK_DIR/render_config.py"
    if [ ! -f "$template" ] || [ ! -f "$renderer" ] || [ ! -f "$manifest" ]; then
        echo "ERROR: no opencode.json.tmpl/render_config.py/vibe-trading-tools.json in $CONFIG_DIR or $CONFIG_FALLBACK_DIR" >&2
        return 1
    fi
    /opt/venv/bin/python3 "$renderer" \
        --template "$template" \
        --manifest "$manifest" \
        --target "$TARGET"
}

publish_project_config() {
    if [ "$TARGET" = "$PROJECT_TARGET" ]; then
        return 0
    fi
    if cp "$TARGET" "$PROJECT_TARGET" 2>/dev/null; then
        echo "[entrypoint] opencode.json project-scope copy → $PROJECT_TARGET"
    else
        echo "[entrypoint] WARNING: could not write project-scope config → $PROJECT_TARGET"
    fi
}

# Fallback-only counterpart of the template's conditional provider block:
# render_config.py covers the happy path, this covers the two fallbacks below.
# No-op when LLM_BASE_URL is empty → direct-mode fallbacks stay byte-identical.
inject_llm_proxy() {
    [ -n "$LLM_BASE_URL" ] || return 0
    LLM_BASE_URL="$LLM_BASE_URL" LLM_API_KEY="$LLM_API_KEY" SANDBOX_ID="$SANDBOX_ID" \
    /opt/venv/bin/python3 - "$TARGET" << 'EOFPY'
import json, os, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    config = json.load(f)
provider = config.setdefault("provider", {}).setdefault("alibaba-cn", {})
provider["options"] = {
    "baseURL": os.environ["LLM_BASE_URL"],
    "apiKey": os.environ.get("LLM_API_KEY", ""),
    "headers": {"x-sandbox-id": os.environ.get("SANDBOX_ID", "")},
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(config, f, ensure_ascii=False, indent=2)
    f.write("\n")
os.replace(tmp, path)
EOFPY
}

# Last-resort config when neither the template nor opencode.json.fallback can
# be used. It keeps the VT MCP block on purpose: a chat-only container loses
# the whole finance tool surface while still looking healthy.
write_fallback_config() {
    TARGET="$TARGET" \
    OMO_VERSION="${OMO_VERSION:-5.1.35}" \
    CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-}" \
    CLICKHOUSE_PORT="${CLICKHOUSE_PORT:-8123}" \
    CLICKHOUSE_USER="${CLICKHOUSE_USER:-default}" \
    CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-}" \
    CLICKHOUSE_DATABASE="${CLICKHOUSE_DATABASE:-ashare}" \
    CLICKHOUSE_LLM_USER="${CLICKHOUSE_LLM_USER:-}" \
    CLICKHOUSE_LLM_PASSWORD="${CLICKHOUSE_LLM_PASSWORD:-}" \
    VT_MEMORY_BASE_DIR="${VT_MEMORY_BASE_DIR:-/workspace/.vt-memory}" \
    /opt/venv/bin/python3 - << 'EOFPY'
import json, os

def env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default

config = {
    "model": "alibaba-cn/deepseek-v4.1-flash-1017",
    "provider": {
        "alibaba-cn": {
            "models": {
                "deepseek-v4.1-flash-1017": {
                    "attachment": True,
                    "limit": {"context": 1000000, "input": 500000, "output": 131072},
                    "name": "DeepSeek V4.1 Flash 1017",
                    "options": {"enable_thinking": True, "reasoning_effort": "high"},
                    "reasoning": True,
                    "tool_call": True,
                }
            }
        }
    },
    "mcp": {
        "vibe-trading": {
            "type": "local",
            "command": [
                "/opt/venv/bin/python",
                "/opt/vibe-trading/agent/mcp_server.py",
            ],
            "timeout": 600000,
            "environment": {
                "CLICKHOUSE_HOST": env("CLICKHOUSE_HOST"),
                "CLICKHOUSE_PORT": env("CLICKHOUSE_PORT", "8123"),
                "CLICKHOUSE_USER": env("CLICKHOUSE_USER", "default"),
                "CLICKHOUSE_PASSWORD": env("CLICKHOUSE_PASSWORD"),
                "CLICKHOUSE_DATABASE": env("CLICKHOUSE_DATABASE", "ashare"),
                "CLICKHOUSE_LLM_USER": env("CLICKHOUSE_LLM_USER"),
                "CLICKHOUSE_LLM_PASSWORD": env("CLICKHOUSE_LLM_PASSWORD"),
                "VT_MEMORY": "full",
                "VT_MEMORY_MCP_TOOLS": "1",
                "VT_MEMORY_BASE_DIR": env("VT_MEMORY_BASE_DIR", "/workspace/.vt-memory"),
            },
        }
    },
    "plugin": [f"oh-my-openagent@{env('OMO_VERSION', '5.1.35')}"],
}

path = os.environ["TARGET"]
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(config, f, ensure_ascii=False, indent=2)
    f.write("\n")
os.replace(tmp, path)
EOFPY
}

if render_config 2>/tmp/entrypoint_render_err; then
    echo "[entrypoint] opencode.json rendered from template → $TARGET"
    rm -f /tmp/entrypoint_render_err
    publish_project_config
else
    echo "[entrypoint] WARNING: Jinja2 render failed: $(tr '\n' ' ' < /tmp/entrypoint_render_err 2>/dev/null)"
    rm -f /tmp/entrypoint_render_err
    if [ -f "$FALLBACK" ]; then
        cp "$FALLBACK" "$TARGET"
        echo "[entrypoint] Using fallback config: $FALLBACK"
    else
        echo "[entrypoint] WARNING: No fallback config at $FALLBACK, writing minimal config (VT MCP included)"
        write_fallback_config
    fi
    if [ -n "$LLM_BASE_URL" ]; then
        if inject_llm_proxy; then
            echo "[entrypoint] LLM proxy provider block injected into fallback config"
        else
            echo "[entrypoint] WARNING: LLM proxy injection failed — fallback stays direct-mode"
        fi
    fi
    publish_project_config
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
OMO_SPEC="oh-my-openagent@${OMO_VERSION:-5.1.35}"
OMO_CACHE="/home/opencode/.cache/opencode/packages/${OMO_SPEC}/node_modules/oh-my-openagent"
if [ -d "$OMO_CACHE" ]; then
    echo "[entrypoint] OMO plugin cache present: ${OMO_CACHE%/node_modules/oh-my-openagent}"
else
    echo "[entrypoint] WARNING: OMO plugin cache missing: ${OMO_CACHE} — opencode will download it on first request (needs npm registry access; see Dockerfile pre-bake)"
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

# ── Start opencode serve (unchanged: :4096) ───────────────────────────────────
opencode serve --port 4096 --hostname 0.0.0.0 &
SERVER_PID=$!

# First bootstrap of a fresh container performs one-time LOCAL init (plugin
# module load, skill index build, db setup). It is CPU-only — no downloads,
# everything is pre-baked in the image — but it blocks the first API request
# for seconds (native amd64) up to a minute (QEMU emulation). Send one
# read-only request after the server has settled so the first real client
# request hits a warm instance.
# opencode 1.19.x can stall its first bootstrap when a request arrives before
# local init finishes; let it settle before the warmup probe.
sleep 20
if curl -sf --max-time 300 -u opencode:"${OPENCODE_SERVER_PASSWORD:-}" \
    "http://127.0.0.1:4096/config?directory=/home/opencode" >/dev/null 2>&1; then
    echo "[entrypoint] warmup complete — first client request will be fast"
else
    echo "[entrypoint] WARNING: warmup request did not complete within 300s"
fi

if ! pid_running "$SERVER_PID"; then
    echo "[entrypoint] ERROR: opencode serve exited before the strategy-run entry could start"
    exit 1
fi

# ── Gate startup on a connected VT MCP server ─────────────────────────────────
# The import check above proves the server *can* start; it says nothing about
# whether opencode loaded the config and connected it. Without this gate a
# render/scope regression ships a container where chat works but the model has
# zero `vibe-trading_*` tools.
VT_MCP_READY_BUDGET_S="${VT_MCP_READY_BUDGET_S:-180}"
case "$VT_MCP_READY_BUDGET_S" in
    '' | *[!0-9]*)
        echo "[entrypoint] WARNING: invalid VT_MCP_READY_BUDGET_S — using 180"
        VT_MCP_READY_BUDGET_S=180
        ;;
esac

vt_mcp_connected() {
    local body
    body="$(curl -sf --max-time 10 -u "opencode:${OPENCODE_SERVER_PASSWORD:-}" \
        "http://127.0.0.1:4096/mcp" 2>/dev/null)" || return 1
    [ -n "$body" ] || return 1
    VT_MCP_STATUS="$body" /opt/venv/bin/python3 - << 'EOFPY'
import json, os, sys
try:
    status = json.loads(os.environ["VT_MCP_STATUS"])
except Exception:
    sys.exit(1)
entry = status.get("vibe-trading") if isinstance(status, dict) else None
sys.exit(0 if isinstance(entry, dict) and entry.get("status") == "connected" else 1)
EOFPY
}

MCP_DEADLINE=$((SECONDS + VT_MCP_READY_BUDGET_S))
until vt_mcp_connected; do
    if ! pid_running "$SERVER_PID"; then
        echo "[entrypoint] FATAL: opencode exited before the VT MCP server connected"
        exit 1
    fi
    if [ "$SECONDS" -ge "$MCP_DEADLINE" ]; then
        echo "[entrypoint] FATAL: VT MCP server 'vibe-trading' not connected after ${VT_MCP_READY_BUDGET_S}s — refusing to serve without the finance tool surface"
        curl -s -u "opencode:${OPENCODE_SERVER_PASSWORD:-}" "http://127.0.0.1:4096/mcp" 2>/dev/null || true
        echo
        exit 1
    fi
    sleep 2
done
echo "[entrypoint] VT MCP connected — vibe-trading tools are in the model tool surface"

# ── Start the dedicated strategy-run entry ────────────────────────────────────
# `agent/strategy_run_server.py` mounts only /health and the two RM service
# routes and never runs api_server's lifespan, so the F9 scheduler stays off.
# It is started after opencode is warm on purpose: the entry's own bind failure
# must be unambiguous (a shared port conflict can only be the entry's fault).
VT_STRATEGY_RUN_HOST="${VT_STRATEGY_RUN_HOST:-0.0.0.0}"
VT_STRATEGY_RUN_PORT="${VT_STRATEGY_RUN_PORT:-8000}"
VT_STRATEGY_RUN_HEALTH_BUDGET_S="${VT_STRATEGY_RUN_HEALTH_BUDGET_S:-120}"
case "$VT_STRATEGY_RUN_HEALTH_BUDGET_S" in
    '' | *[!0-9]*)
        echo "[entrypoint] WARNING: invalid VT_STRATEGY_RUN_HEALTH_BUDGET_S — using 120"
        VT_STRATEGY_RUN_HEALTH_BUDGET_S=120
        ;;
esac

echo "[entrypoint] starting strategy-run entry on ${VT_STRATEGY_RUN_HOST}:${VT_STRATEGY_RUN_PORT}"
/opt/venv/bin/python /opt/vibe-trading/agent/strategy_run_server.py \
    --host "$VT_STRATEGY_RUN_HOST" \
    --port "$VT_STRATEGY_RUN_PORT" &
ENTRY_PID=$!

trap 'shutdown_and_exit 143' TERM
trap 'shutdown_and_exit 130' INT

# ── Gate startup on the entry's real /health ──────────────────────────────────
# /health is unauthenticated by design (the RM token only guards the service
# routes), so no token mount is needed to prove the process is serving.
ENTRY_HEALTH_URL="http://127.0.0.1:${VT_STRATEGY_RUN_PORT}/health"
ENTRY_HEALTH_DEADLINE=$((SECONDS + VT_STRATEGY_RUN_HEALTH_BUDGET_S))
ENTRY_READY=0
while :; do
    if ! pid_running "$ENTRY_PID"; then
        echo "[entrypoint] ERROR: strategy-run entry exited before ${ENTRY_HEALTH_URL} returned 200"
        break
    fi
    if curl -sf --max-time 2 "$ENTRY_HEALTH_URL" >/dev/null 2>&1; then
        ENTRY_READY=1
        echo "[entrypoint] strategy-run entry /health OK → ${ENTRY_HEALTH_URL} (${SECONDS}s after entrypoint start)"
        break
    fi
    if [ "$SECONDS" -ge "$ENTRY_HEALTH_DEADLINE" ]; then
        echo "[entrypoint] ERROR: strategy-run entry /health did not return 200 within ${VT_STRATEGY_RUN_HEALTH_BUDGET_S}s → ${ENTRY_HEALTH_URL}"
        break
    fi
    sleep 1
done
if [ "$ENTRY_READY" != "1" ]; then
    echo "[entrypoint] FATAL: refusing to serve opencode without the strategy-run entry"
    exit 1
fi

# ── Supervise both ────────────────────────────────────────────────────────────
# `wait -n` returns as soon as either process exits (bash >= 4.3; the base
# image ships 5.1). Any exit — even status 0 — tears the sibling down and
# fails the container, so a half-alive service can never look healthy.
echo "[entrypoint] supervising opencode (pid=${SERVER_PID}) and strategy-run entry (pid=${ENTRY_PID})"
set +e
wait -n
SUPERVISOR_RC=$?
set -e
echo "[entrypoint] ERROR: a supervised process exited (status ${SUPERVISOR_RC}) — stopping the sibling"
stop_children
exit 1