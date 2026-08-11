# T14 findings — goal session binding on the opencode engine

Date: 2026-09-13 · rig: opencode 1.18.30 + OmO 4.19.4 (T7 recipe, local, no
docker) · gateway: worktree `api_server` with `VIBE_TRADING_ENGINE=opencode`,
scratch `VIBE_TRADING_HOME=/tmp/vt-t14-rig/vt-home`, scratch `API_AUTH_KEY`,
ports 14096 (serve) / 18080 (gateway) · model: qwen3.8-max (provider
alibaba-cn, production route via the rig's read-only .env).

## Verdict

The F4 mitigation HOLDS on the live engine: the D8① gateway-context
injection alone (no reminder, fresh session per turn) made the model pass
`session_id=` correctly on **13/13 sampled goal-touching turns** (12
compliance turns + the E2E kickoff turn). Passthrough rate **100% ≥ 80%** →
per plan T14, **no alias-mapping escalation required**. The frozen
`_resolve_session_id` fallback chain (mcp_server.py:350-389) was never
exercised by a compliant turn — and the GoalStore's session-ownership
validation (`store.py::_require_mutable_goal`, `goal.session_id !=
session_id` → StaleGoalError) means a wrong/omitted id CANNOT silently land
evidence on the vt session's goal: REST re-fetch is valid ground truth.

## D8① injection surface check (the D8② hazard, ruled out)

T7 Finding 2 showed D8② named a governance-DISABLED tool (`read_file`) — a
dead letter. D8① checked against the rendered governance reality, NOT
assumptions:

- Frozen manifest (`OpencodeAgent/config/vibe-trading-tools.json`, 15
  disabled): **no goal tools**. Rendered rig config
  (`/tmp/vt-t14-rig/xdg/opencode/opencode.json`) permission denies: 15
  `vibe-trading_*` entries, **zero goal-tool denies**
  (`tool-surface-check.json`).
- vt MCP surface (fastmcp `Client.list_tools` against `agent/mcp_server.py`
  with the rig env): `start_research_goal`, `get_research_goal`,
  `add_goal_evidence`, `update_research_goal_status` all registered (4 of 77
  tools).
- Prefix observation: on the model surface the tools appear as
  `vibe-trading_<name>`; the injection names the bare forms, which are exact
  suffixes. Empirically the model resolves them without help (13/13) — **no
  wording fix needed**; `_build_prompt_injection` is UNCHANGED this wave
  (T5/T7 injection tests stay green by construction).
- Live injection block captured verbatim from the engine's user message:
  `live-injection-block.txt` (`[gateway context]` + `vt_session_id=<id>` +
  the `session_id='<id>'` instruction + the D8② uploads block).

## Goal panel E2E (binding chain, all hard checks green)

`test_goal_panel_e2e_mcp_evidence_visible_via_rest` (23.9s, one model turn):

1. REST goal creation → `goal.created` SSE (gateway-emitted) → panel toggle
   renders live WITHOUT reload (the REST-originated path works as designed).
2. UI kickoff ("record exactly ONE evidence note … using add_goal_evidence",
   no session_id hint) → engine prompt carried the injection block →
   model's single `vibe-trading_add_goal_evidence` call carried
   `session_id=<vt sid>` (raw tool-part input asserted) → call completed.
3. **Degradation item 12 corroborated honestly**: during the agent turn the
   page's SSE log shows ONLY `goal.created` (from step 1's REST call) — the
   MCP-side evidence write emitted NO `goal.evidence` frame (cross-process
   write, no gateway EventBus event). The open panel stayed stale ("0/1
   met", no evidence badge; `goal-panel-stale-before-reload.png`). Recorded
   as info, never asserted as failure — silence is the EXPECTED state per
   the plan, and a future wiring that emits the events would be an
   improvement, not a regression. T15's F8 card item 12 stands as written.
4. **REST re-fetch = the plan's binding definition of panel-visible**:
   `GET /sessions/{sid}/goal` returned the snapshot with the MCP-written
   evidence (`goal-refetch.json`).
5. Reload → session-load REST fetch → panel expanded → "Recent Evidence"
   section renders the evidence text
   (`goal-panel-evidence-after-reload.png`).
6. D3 ownership live check: REST transcript user message stayed RAW (no
   `[gateway context]`).

## Compliance sampling (plan: ≥10 turns, <80% → escalate)

`test_compliance_sampling_session_id_passthrough` (211s, 12 fresh-session
minimal turns, one `add_goal_evidence` call each, no research work):

- **12/12 = 100%** first-call `session_id` correct (raw engine tool-part
  input) AND evidence landed via REST re-fetch, every turn exactly 1 tool
  call, every attempt `completed`. Full table: `compliance.md` /
  `compliance.json`.
- Fresh session per turn is the honesty control: no conversation history to
  copy the id from — the injection is the only hint, exactly the plan's
  question ("prompt-injection-only mitigation on a real model").
- Sampling caveat (stated, not hidden): n=12 on qwen3.8-max via one prompt
  shape at one point in time; a different model/prompt mix can regress —
  the rate is a gate input, not a permanent property. The store-side
  session validation is the durable backstop: a non-compliant turn fails
  loudly (StaleGoalError to the model) instead of silently mis-binding.
- Escalation NOT triggered. Had it been: the alias-mapping option would
  require touching the frozen `_resolve_session_id` surface or bridge-side
  goal-id aliasing; documented here as the pre-agreed fallback, no code
  written (plan Must-NOT respected: zero diff on mcp_server.py).

## Cost accounting

`cost.json` (stop_rig, serve-side assistant-message costs): **$0.4283 total**
across 13 engine sessions (1 E2E + 12 compliance; the fastmcp list_tools
surface check spawned no engine session). ≈27 model steps (1 tool call + 1
reply per sampled turn, + the E2E turn) — inside the task's 12-15 turn
budget, cost in line with one T7 pass ($0.36). All 13 rig engine sessions
DELETEd by stop_rig (spike §7h hygiene, all -> 200); rig root purged.

## Files

- `test_goal_panel_e2e_mcp_evidence_visible_via_rest-results.json` —
  per-assertion pass/detail (10 hard checks + 4 info records)
- `test_compliance_sampling_session_id_passthrough-results.json`
- `compliance.json` / `compliance.md` — the sampling table + rate
- `goal-refetch.json` — REST re-fetch payload with the MCP-written evidence
- `sse-goal-e2e.json` — raw page SSE frames for the E2E turn (goal.*
  silence visible: only goal.created, tickets redacted)
- `live-injection-block.txt` — verbatim engine-side injection capture
- `tool-surface-check.json` — governance/manifest/MCP-surface evidence
- `goal-panel-stale-before-reload.png` / `goal-panel-evidence-after-reload.png`
