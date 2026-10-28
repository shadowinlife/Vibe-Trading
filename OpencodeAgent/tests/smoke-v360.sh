#!/usr/bin/env bash
# v3.6.0-mymain image smoke gate (2026-10-13 version-bump delivery:
# opencode-ai 1.18.18 -> 1.19.0, oh-my-openagent 5.1.0 -> 5.1.30;
# VT baseline unchanged since the 2026-10-10 alignment, b1f6ce71).
#
# Stage 1: targeted in-container checks via bash override (fast):
#   render / timeout+tool_output landing / deny trio / MCP count 85 +
#   read_run_artifact present / AGENTS.md counts / pinned opencode version /
#   pre-baked OMO plugin cache at the pinned spec / functional exercise of
#   the FE-1 tools on a synthetic run (amd64 runtime proof).
# Stage 1b: LLM proxy render modes (contract A.7):
#   proxy mode (LLM_BASE_URL set) renders the alibaba-cn provider block with
#   baseURL/apiKey/x-sandbox-id; direct mode (no LLM_* env) has no provider
#   key and stays byte-identical to v3.3.0 output.
# Stage 2: real entrypoint startup — proves the pinned opencode-ai@1.19.0
#   parses the config keys (mcp.vibe-trading.timeout, tool_output) at boot
#   and serve reaches /health.
#
# Usage: ./smoke-v360.sh [IMAGE]   (default opencode-serve:v3.6.0-mymain)
set -euo pipefail

IMAGE="${1:-opencode-serve:v3.6.0-mymain}"
PLATFORM="${PLATFORM:-linux/amd64}"
SMOKE_PASSWORD="smoketest-password"

echo "=== Stage 1: targeted checks ($IMAGE, $PLATFORM) ==="
docker run --rm --platform "$PLATFORM" --entrypoint bash "$IMAGE" -c '
set -e
/opt/venv/bin/python3 /workspace/.opencode/render_config.py \
  --template /workspace/.opencode/opencode.json.tmpl \
  --manifest /workspace/.opencode/vibe-trading-tools.json \
  --target /tmp/opencode.json
echo "RENDER_OK"

grep -q "\"timeout\": 600000" /tmp/opencode.json && echo "TIMEOUT_OK"
grep -q "\"max_bytes\": 262144" /tmp/opencode.json \
  && grep -q "\"max_lines\": 8000" /tmp/opencode.json && echo "TOOLOUTPUT_OK"

grep -q "vibe-trading_trading_\*" /tmp/opencode.json \
  && grep -q "vibe-trading_qveris_\*" /tmp/opencode.json \
  && grep -q "vibe-trading_iwencai_search" /tmp/opencode.json && echo "DENY_OK"

grep -q "oh-my-openagent@5.1.30" /tmp/opencode.json && echo "PLUGIN_PIN_OK"

opencode --version 2>/dev/null | grep -q "1.19.0" && echo "OPENCODE_VERSION_OK"

test -d "/home/opencode/.cache/opencode/packages/oh-my-openagent@5.1.30/node_modules/oh-my-openagent" \
  && echo "OMO_CACHE_OK"

cd /opt/vibe-trading/agent
VT_MEMORY_MCP_TOOLS=1 /opt/venv/bin/python3 -c "
import asyncio, mcp_server
tools = asyncio.run(mcp_server.mcp.list_tools())
names = [t.name for t in tools]
print(\"COUNT:\", len(tools))
assert len(tools) == 85, len(tools)
assert \"read_run_artifact\" in names
print(\"COUNT_OK\")
"

grep -q "80 个" /workspace/AGENTS.md && grep -q "85 个" /workspace/AGENTS.md && echo "AGENTS_OK"

VIBE_TRADING_ALLOWED_RUN_ROOTS=/tmp/smoke-runs /opt/venv/bin/python3 - << "PYEOF"
import json, sys
from pathlib import Path
sys.path.insert(0, "/opt/vibe-trading/agent")
run = Path("/tmp/smoke-runs") / "run1"
(run / "artifacts").mkdir(parents=True, exist_ok=True)
(run / "run_card.json").write_text(json.dumps({
     "schema_version": "1.0",
     "backtest": {"codes": ["BTC-USDT"], "start_date": "2025-01-01",
                  "end_date": "2025-06-30", "interval": "1D", "initial_cash": 1000000},
     "metrics": {"sharpe": 1.25, "max_drawdown": -0.08},
     "warnings": []}))
rows = ["timestamp,ret,equity,drawdown,benchmark_equity,active_ret"]
for i in range(120):
    rows.append(f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d},0.001,"
                f"{1000000 + i * 100}.0,-0.01,{1000000 + i * 50}.0,0.0")
(run / "artifacts" / "equity.csv").write_text("\n".join(rows) + "\n")
(run / "artifacts" / "ohlcv_BTC-USDT.csv").write_text(
    "trade_date,open,high,low,close,volume\n2025-01-01,1,2,0.5,1.5,100\n")

from src.tools.run_artifact_tool import read_run_artifact
from src.tools.backtest_summary import collect_ohlcv_paths, try_build_backtest_summary

env = json.loads(read_run_artifact(str(run), "equity", format="downsample", max_rows=50))
assert len(env["rows"]) <= 50 and env["downsample"]["pinned_last"] is True
assert env["rows"][0][2] == 1000000.0 and env["rows"][-1][2] == 1011900.0
meta = json.loads(read_run_artifact(str(run), "equity", format="meta"))
assert meta["total_rows"] == 120
bad = json.loads(read_run_artifact(str(run), "../../etc/passwd"))
assert bad.get("ok") is False
summ = try_build_backtest_summary(run, collect_ohlcv_paths(run))
assert summ and summ["schema_version"] == "1.0" and len(summ["equity_preview"]) <= 50
assert summ["metrics"]["sharpe"] == 1.25
assert "BTC-USDT" in summ["artifact_paths"]["ohlcv"]
assert summ["equity_preview"][0]["equity"] == 1000000.0
assert summ["equity_preview"][-1]["equity"] == 1011900.0
print("FUNC_OK")
PYEOF
'
echo "=== Stage 1 PASS ==="

echo "=== Stage 1b: LLM proxy render modes (contract A.7) ==="
docker run --rm --platform "$PLATFORM" --entrypoint bash "$IMAGE" -c '
set -e
env -u LLM_BASE_URL -u LLM_API_KEY \
  /opt/venv/bin/python3 /workspace/.opencode/render_config.py \
  --template /workspace/.opencode/opencode.json.tmpl \
  --manifest /workspace/.opencode/vibe-trading-tools.json \
  --target /tmp/direct.json
if grep -q "\"provider\"" /tmp/direct.json; then
  echo "FAIL: provider block present in direct mode"; exit 1
fi
echo "DIRECT_OK"

LLM_BASE_URL="http://resource-manager.default.svc.cluster.local:8900/llm/v1" \
LLM_API_KEY="pool-fake-token" HOSTNAME="smoke-pod-1" \
  /opt/venv/bin/python3 /workspace/.opencode/render_config.py \
  --template /workspace/.opencode/opencode.json.tmpl \
  --manifest /workspace/.opencode/vibe-trading-tools.json \
  --target /tmp/proxy.json
grep -q "\"x-sandbox-id\": \"smoke-pod-1\"" /tmp/proxy.json || {
  echo "FAIL: x-sandbox-id missing in proxy mode"; exit 1; }
/opt/venv/bin/python3 - << "PYEOF"
import json
config = json.load(open("/tmp/proxy.json"))
options = config["provider"]["alibaba-cn"]["options"]
assert options["baseURL"] == "http://resource-manager.default.svc.cluster.local:8900/llm/v1", options
assert options["apiKey"] == "pool-fake-token", options
assert options["headers"] == {"x-sandbox-id": "smoke-pod-1"}, options
assert config["model"] == "alibaba-cn/qwen3.8-max"
direct = json.load(open("/tmp/direct.json"))
assert "provider" not in direct
print("PROXY_OK")
PYEOF
'
echo "=== Stage 1b PASS ==="

echo "=== Stage 2: real entrypoint startup ==="
CID=$(docker run -d --platform "$PLATFORM" \
  -e DASHSCOPE_API_KEY=dummy-key-not-used \
  -e OPENCODE_SERVER_PASSWORD="$SMOKE_PASSWORD" \
  -e CLICKHOUSE_HOST= \
  "$IMAGE")
trap 'docker rm -f "$CID" >/dev/null 2>&1 || true' EXIT
for i in $(seq 1 45); do
  sleep 10
  if docker exec "$CID" curl -sf -u "opencode:$SMOKE_PASSWORD" \
      http://localhost:4096/health >/dev/null 2>&1; then
    echo "HEALTH_OK after ~$((i * 10))s"
    docker logs "$CID" 2>&1 | grep -E "rendered from template|warmup|OMO plugin cache" | head -4
    echo "=== Stage 2 PASS ==="
    exit 0
  fi
done
echo "=== Stage 2 FAIL: health not reached within 450s ==="
docker logs --tail 50 "$CID" || true
exit 1
