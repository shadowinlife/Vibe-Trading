#!/usr/bin/env bash
set -euo pipefail

# Wait for opencode serve (internal 127.0.0.1:4096) to be FULLY ready, then exec
# the vt gateway. Readiness signal = serve's /mcp (200 JSON, MCP servers loaded)
# — NOT /health, which is opencode's HTML page and answers before the MCP layer
# (and the OmO plugin auto-install) is up. Waiting on /mcp is exactly what the
# gateway's lifespan preflight (bridge load_tool_mapping → GET /mcp) needs, so a
# clean cold boot has no failed-preflight gateway restart. If serve never comes
# up, the preflight fails loud and supervisord restarts this program
# (startretries) — the documented restart-until-reachable posture (wiring.py).

SERVE_URL="${OPENCODE_BASE_URL:-http://127.0.0.1:4096}"
GATEWAY_PORT="${GATEWAY_PORT:-8080}"
SERVE_AUTH=""
if [ -n "${OPENCODE_SERVER_PASSWORD:-}" ]; then
    SERVE_AUTH="-u opencode:${OPENCODE_SERVER_PASSWORD}"
fi

echo "[gateway-start] waiting for opencode serve /mcp at ${SERVE_URL} ..."
ready=false
# --max-time bounds each probe: serve lazily bootstraps on first request and
# /mcp BLOCKS until every MCP server is up (on a fresh volume that includes the
# ~minutes-long OmO plugin auto-install, much longer under cross-arch Rosetta).
# An unbounded curl would stick on one stale hung request forever; bounding it
# lets the loop retry until bootstrap completes and /mcp answers 200.
for _ in $(seq 1 180); do
    if curl -sf --max-time 10 ${SERVE_AUTH} "${SERVE_URL}/mcp" >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
done
if [ "$ready" = "true" ]; then
    echo "[gateway-start] serve /mcp ready; starting vt gateway on 0.0.0.0:${GATEWAY_PORT}"
else
    echo "[gateway-start] WARNING: serve /mcp not ready after 180s; starting gateway anyway (preflight will fail loud and supervisord will retry)" >&2
fi

# Production launch shape (start_rig.py / cli/_legacy.py:258): api_server imported
# as a MODULE so route registration resolves host symbols via sys.modules, then
# serve_main() runs uvicorn and mounts the SPA dist. cwd is set by supervisord
# (directory=/opt/vibe-trading/agent).
exec /opt/venv/bin/python -c \
    "from api_server import serve_main; import sys; sys.exit(serve_main(['--host', '0.0.0.0', '--port', '${GATEWAY_PORT}']))"
