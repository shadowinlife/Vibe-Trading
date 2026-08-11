# T7 E2E findings — production-governance reality vs plan assumptions

Date: 2026-09-12/13 · rig: opencode 1.18.30 + OmO 4.19.4 (spike §11 recipe, local, no docker)
· gateway: worktree `api_server` with `VIBE_TRADING_ENGINE=opencode`, scratch
`VIBE_TRADING_HOME=/tmp/vt-e2e-rig/vt-home`, scratch `API_AUTH_KEY`, port 18080;
serve port 14096. Config rendered by the UNMODIFIED production renderer over a
local-path tmpl copy (MCP = worktree `agent/mcp_server.py` via legonanobot python,
`VT_MEMORY_MCP_TOOLS=1`).

The live rig falsified two plan/task assumptions — both rooted in the FROZEN
tool-governance manifest (`OpencodeAgent/config/vibe-trading-tools.json`,
15 disabled tools), which the spike never exercised (no backtest in spike
scenarios; §9 flagged "run_dir source untested"). Neither is a frontend-contract
change; both were fixed bridge-side with the frontend kept as the contract.

## Finding 1 — `backtest` MCP tool is DISABLED by governance (affects D5 run_dir)

- Empirical: `/experimental/tool/ids` + the G2 turn show the agent runs
  backtests through the **bash** tool as `python -m backtest.runner <run_dir>`
  (the strategy-generate skill path), NOT via a `vibe-trading_backtest` MCP call.
- Consequence: T4's run_dir harvest (gated on `"backtest" in tool` over tool
  OUTPUT) never fires — and the runner's stdout carries metrics only (no
  run_dir in any form), so output scanning cannot work on this path.
- `attempt.completed.run_dir` was `null` while the summary text carried the
  path → no run card (frontend derives runId solely from `d.run_dir`,
  Agent.tsx:876-877). E2E group 2 FAILED on run2 (evidence: run2-debug/).
- **Fix (bridge-side, translator/tools.py)**: harvest run_dir from the bash
  tool's INPUT command via `_RUNNER_CMD_PATTERN`
  (`backtest.runner <path>` / `vibe-trading backtest <path>`), keeping the
  MCP-tool-output patterns for a governance re-enable. Unit tests:
  `test_run_dir_harvested_from_bash_runner_command`,
  `test_run_dir_bash_harvest_ignores_unrelated_commands`; the existing
  "non-backtest tool OUTPUT ignored" test stays green (bash output is still
  never scanned — only the runner CLI argument).
- T10 note: the tenant container inherits this behavior; if the governance
  manifest ever re-enables `backtest`, both harvest paths coexist (last wins).

## Finding 2 — `read_file` (vt MCP) is DISABLED by governance (affects D8②)

- Empirical: G8 turn — the engine read the uploaded CSV with its NATIVE `read`
  tool at the injected ABSOLUTE path
  (`/tmp/vt-e2e-rig/vt-home/uploads/<shadow>.csv`) and answered correctly
  (ROWS=10 COLS=symbol,close). The vt MCP `read_file` named in D8's injection
  text is not on the model's tool surface (disabled list).
- B6's actual defense — relative→absolute path RESOLUTION — works exactly as
  designed; only the tool named in the instruction is unavailable. The agent
  adapted, but the instruction was a dead letter.
- **Fix (bridge-side, service.py `_build_prompt_injection`)**: the D8② block
  keeps the absolute-path resolution and becomes tool-agnostic ("read it via
  that ABSOLUTE path with a file-reading tool (the built-in read tool works)").
  T5's injection unit test (asserts uploads dir + `uploads/<name>` present,
  transcript raw) stays green.
- Note: `/upload` returns a SHADOW filename (`uploads/<hash>.csv`); the
  envelope's `filename` field keeps the original. The injection resolves
  whatever relative path appears in the content — shadow names included.

## Non-findings (verified working live)

- Subscribe-first pump connected at startup (serve log "event connected"
  before any traffic); reconcile ran against an empty scratch store.
- D3 transcript ownership: user messages persisted RAW (no injection block)
  across all groups; D6 metadata enumeration (status/elapsed_ms/provider/
  model) on completed AND cancelled replies.
- B3 defense (group 7): `sleep 105` turn — **35 tool_heartbeats, median gap
  3.00s, max gap 3.00s, max elapsed_s 105.0** (sse-g7.json); the frontend
  watchdog (Agent.tsx:1247-1269, 90s default) never archived the turn;
  post-gap events delivered the terminal and the final answer bubble.
- G1 vocabulary census (sse-g1.json): message.received, attempt.created,
  attempt.started, reasoning_delta, text_delta, llm_usage,
  attempt.completed{status,summary,run_dir,elapsed_ms,provider,model} —
  provider=alibaba-cn model=qwen3.8-max (production model).
- Auto-title (D11) stayed on the ChatLLM route (gateway preflight showed
  dashscope provider from the injected read-only .env; zero changes there).

## Final run disposition (run3 + g5 rerun)

- run3 (full pass after the two bridge fixes): **7/8 green in 387s** —
  g2 (run_dir via bash-runner harvest → /runs/{id} 200 → run card), g7
  (watchdog), g8 (absolute-path upload read) all PASS live.
- g5's run3 failure was a TEST-SCRIPT bug (asserted the literal "backtest"
  in the expanded activity; the UI renders LOCALIZED tool labels — en.json
  tools.bash = "Run command"). Fixed the assertion to derive the expected
  label from the persisted trail via en.json, and added a REST fallback so
  g5 reruns standalone. Rerun: **PASS in 3.7s, zero LLM steps** (asserts
  against run3's real g2 session).
- assertion-results.json: all 8 groups passed, 67 programmatic checks.

## Cost accounting

- run2 (first full pass, 2 groups failed): $0.4535 serve-side (cost.json in
  run2-debug/), ~35 model steps incl. the backtest turn.
- smoke round-trip + run1 (collection-error abort): ~$0.02.
- run3 (final full pass, 7/8 + free g5 rerun): $0.3644 serve-side (cost.json
  in this directory), ~25 model steps.
- Disclosed total: ~$0.84 serve-side across smoke + run2 + run3 (+ a few
  cents of gateway auto-title ChatLLM calls, D11 route). Per-pass budget
  (~60 model steps) held for each individual pass; the debugging rerun
  (run2) is disclosed rather than hidden — same posture as spike §10.
