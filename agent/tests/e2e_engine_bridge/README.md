# T7 engine-bridge Web E2E (live local rig, no docker)

Proves the Phase-1 claim end to end: the **unchanged** React frontend +
gateway REST/SSE surface work on the opencode engine
(`VIBE_TRADING_ENGINE=opencode`) — eight mandatory assertion groups
(`test_web_e2e.py`), none skippable (plan T7 Must-NOT).

## Isolation convention

Mirrors `e2e_backtest` WITHOUT touching the gate command: unless
`ENGINE_BRIDGE_E2E=1` is set, `conftest.py::pytest_ignore_collect` ignores
every test module here at COLLECTION time, so the default suite
(`pytest --ignore=agent/tests/e2e_backtest --tb=short -q`) stays green
without the rig and without playwright installed.

## Prerequisites

- macOS/Linux local machine; ports **14096** (opencode serve) and **18080**
  (gateway) free — the rig refuses to start otherwise (the user's live
  opencode instances on default ports are never touched).
- `opencode` 1.18.30 binary (`OPENCODE_BIN` override; default
  `~/.opencode/bin/opencode`), OmO plugin cache present (spike §2 rig),
  provider auth in the default opencode data dir (read as-is).
- This interpreter (`python3` = legonanobot conda env) with the repo
  importable; `nano-search-mcp` on PATH.
- Frontend built: `cd frontend && npm ci && npm run build` (dist is
  gitignored; the gateway serves it via SPAStaticFiles).
- A scratch venv with playwright (NOT a repo dependency; `pydantic` is
  needed because the parent `agent/tests/conftest.py` imports
  `src.config`):
  `python3 -m venv /tmp/vt-e2e-venv && /tmp/vt-e2e-venv/bin/pip install playwright pytest pydantic pydantic-settings && PLAYWRIGHT_BROWSERS_PATH=/tmp/vt-e2e-pw-browsers /tmp/vt-e2e-venv/bin/playwright install chromium`
- Optional `--env-file` dotenv for the gateway (LANGCHAIN_* for the D11
  auto-title route); auto-detects `~/.vibe-trading/.env` / `agent/.env`.

## Run

```bash
# 1. rig up (renders the production config via the UNMODIFIED render_config.py
#    into a scratch XDG dir; scratch VIBE_TRADING_HOME; scratch API key)
python3 agent/tests/e2e_engine_bridge/start_rig.py

# 2. the eight groups (evidence -> .omo/evidence/opencode-engine-bridge-v2/t7-e2e/)
ENGINE_BRIDGE_E2E=1 PLAYWRIGHT_BROWSERS_PATH=/tmp/vt-e2e-pw-browsers \
    /tmp/vt-e2e-venv/bin/python -m pytest agent/tests/e2e_engine_bridge -q

# 3. global gate helpers: zero-diff assertions + pytest failure-set diff
python3 agent/tests/e2e_engine_bridge/gate_checks.py all \
    --baseline /tmp/t7-scratch/baseline-pytest.log \
    --run /tmp/t7-scratch/final-pytest.log \
    --json-out .omo/evidence/opencode-engine-bridge-v2/t7-e2e/gate-results.json

# 4. rig down (DELETEs only the engine sessions the rig created, writes the
#    cost report, kills the process groups; --purge removes the scratch root)
python3 agent/tests/e2e_engine_bridge/stop_rig.py \
    --evidence-dir .omo/evidence/opencode-engine-bridge-v2/t7-e2e --purge
```

## Rig hygiene (standing directives)

- NO docker; scratch ports only; the user's real `~/.vibe-trading`,
  `~/.config/opencode` and global npm are never written (the opencode DATA
  dir is shared read-as-is for auth — rig engine sessions are DELETEd by
  `stop_rig.py`, spike §7h hygiene).
- `ENV_PATH` still READS `~/.vibe-trading/.env` (read-only, by design).
- Cost discipline: every prompt is single-shot with an explicit "do not
  iterate / no todo list / no subagents" clause; actual cost lands in
  `cost.json` (stop_rig).

## Evidence layout (`.omo/evidence/opencode-engine-bridge-v2/t7-e2e/`)

`<group>-results.json` (per-assertion pass/fail + detail), `sse-<group>.json`
(raw frames the page received, tickets redacted), `<group>-*.png`
(screenshots — artifacts, never the verdict), `serve.log` / `gateway.log`
(copied by the runner), `cost.json`, `gate-results.json`.
