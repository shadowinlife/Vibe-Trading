**Versions used (local spike rig): opencode CLI 1.18.30** (`/Users/mgong/.opencode/bin/opencode`) **+ OmO (oh-my-openagent) 4.19.4** (global npm reference install; plugin ref `oh-my-openagent@latest` cache-resolved to 4.19.4 via `~/.cache/opencode/packages/oh-my-openagent@latest`, confirmed by `plugin-meta.json`). **⚠️ VERSION DELTA vs production (B1, from T2 `baseline_memo.md`): in-image CLI = 1.18.18, ECS host-direct production = 1.18.23 — this spike ran 1.18.30 (+12/+7 patches). OmO = 4.19.4 on BOTH sides (no delta). Version-sensitive measurements are flagged in §8; per user directive the spike ran `opencode serve` directly on this machine (no docker), so the CLI delta is accepted-and-flagged rather than eliminated.**

# Phase-0 Spike Report — opencode serve + OmO + Vibe-Trading MCP event rig

- Plan: `.omo/plans/opencode-engine-bridge-v2.md` T1 (Wave 1, Phase-0 go/no-go gate)
- Date: 2026-09-12, branch `mymain-engine-bridge`
- Traces: `agent/tests/fixtures/opencode_bridge/traces/` (8 JSONL files, 972 KB, sanitized)
- Driver: `agent/tests/fixtures/opencode_bridge/record_traces.py` (stdlib only, reusable)

---

## 1. VERDICT: **GO** (with 3 mandatory conditions carried into T3/T4)

No plan assumption was falsified without a mitigation. The event surface of
opencode 1.18.30 + OmO 4.19.4 supports every D4/D5 mechanism the bridge needs:

| Gate question | Result |
|---|---|
| S1: does `message.part.delta` exist (D5 main path)? | **YES** — single stable shape, 946 deltas across traces; also present in kimaki's public 1.2.15-era fixtures → version-stable (§6) |
| S2: is OmO continuation distinguishable + measurable (D4)? | **YES** — `session.idle` fires between rounds; re-prompt arrives **6.4 s** after idle (n=2, σ<0.02 s); injected message identifiable by text prefix (§5c) |
| S3: is abort terminal distinguishable from natural completion (D4/B4)? | **YES** — `MessageAbortedError` on `session.error` + assistant `message.updated` with `error` and **no** `finish`; natural completion carries `time.completed` + `finish:"stop"` (§5b) |
| B3: do long tools starve the event stream (90 s frontend watchdog)? | **YES — confirmed hazard**: 120.75 s content-silence inside `sleep 120`; only stream-level `server.heartbeat` (10 s) arrives → bridge MUST synthesize `tool_heartbeat` (~3 s) per D5 (§7g) |
| B4: natural-completion markers for text settlement? | **YES** — `message.updated info.time.completed` + `info.finish` (`stop` vs `tool-calls`) exactly as D4 assumes (§4) |
| kimaki #74: `session.error` followed by idle? | **YES** on abort (error → status idle → `session.idle`, twice). Error-without-idle was not observed, but the synthetic-idle guard remains cheap insurance — keep it in D4 |
| F7: `DELETE /session/:id` available? | **YES** — 200/`true` + `session.deleted` event + cascade-deletes child sessions (§7h) |

**Mandatory conditions (feed T3/T4/T10):**
1. **`QUIESCENCE_S` default 3.0 is FALSIFIED** — OmO re-prompts 6.4 s after idle. Default must be ≥ 8 s (recommend 8–10 s: measured max 6.43 + margin). n=2 samples; T4 golden tests pin the trace-derived value.
2. **`message.part.delta` `field:"text"` does NOT distinguish text vs reasoning** — reasoning parts stream deltas with `field:"text"` too (16 of 17 deltas in scenario a were reasoning-part deltas). Translator must join `partID → part.type` (from `message.part.updated` snapshots) before routing to `text_delta` vs reasoning-tail. D5's `field=="text"` criterion alone would leak chain-of-thought into chat text.
3. **Idle/terminal handling must be sessionID-scoped** — subagent child sessions emit their own `session.idle`/`session.status` on the same `/event` stream (2 child idles arrived 14 s before the parent's idle in scenario d). A bridge that treats any `session.idle` as turn-end terminates early.

---

## 2. Rig (production-isomorphic, local — no docker per user directive)

```
opencode serve --hostname 127.0.0.1 --port 14096 --print-logs --log-level DEBUG
  cwd        = /tmp/oc-spike-workspace          (mirrors image /workspace)
  XDG_CONFIG_HOME = /tmp/oc-spike-xdg           (isolates user's live opencode config)
  config     = OpencodeAgent/config/render_config.py (UNMODIFIED, run locally)
               over a /tmp copy of opencode.json.tmpl with local paths
  MCP        = vibe-trading → legonanobot python3 + worktree agent/mcp_server.py
               (VT_MEMORY=full, VT_MEMORY_MCP_TOOLS=1 → 82 tools, matches prod)
               search mcp → legonanobot nano-search-mcp --transport stdio
  plugin     = oh-my-openagent@latest → 4.19.4 (cache-resolved, same ref as prod tmpl)
  data dir   = default ~/.local/share/opencode (auth picked up as-is; sessions
               share the user's opencode.db — all spike sessions DELETEd after)
```

Config rendering used the **production renderer** (`render_config.py`): 15
governance denies from `vibe-trading-tools.json`, 12 domain subagents from
`subagents.json`, prompts materialized next to the rendered config — verified
effective via `GET /config` (permission denies + 24 agents + OmO builtin MCPs).
Phase B restarted the same serve with `permission.bash="ask"` added to the
rendered config (verified effective via `GET /config`).

**OmO plugin load evidence** (assertion ✓):
1. serve log: `message=stream providerID=alibaba-cn modelID=qwen3.8-max … agent="Sisyphus - ultraworker" mode=primary` (OmO primary agent override active);
2. `GET /agent`: full OmO roster (Sisyphus/Prometheus/Atlas/Metis/Momus/Sisyphus-Junior/explore/librarian/oracle/plan/build/general) merged with the 12 rendered domain subagents;
3. `GET /mcp`: OmO builtin MCPs injected (`websearch`, `context7`, `grep_app`, `lsp` connected; `codegraph` disabled) — these exist only via the plugin;
4. `~/.local/state/opencode/plugin-meta.json`: `oh-my-openagent@latest → version 4.19.4`.

### Rig deviations from the production image (all recorded, none verdict-affecting)

| # | Deviation | Why | Event-shape impact |
|---|---|---|---|
| 1 | CLI 1.18.30 vs image 1.18.18 / host 1.18.23 | user directive: local serve, no docker; image binary SIGILLs under QEMU on arm64 (T2 §caveat) | see §8 sensitivity table |
| 2 | MCP python = legonanobot conda env (not `uv run`) | repo `pyproject.toml` is at root, not `agent/`; legonanobot is the plan's sanctioned env; import + 82-tool count verified pre-spike | none (same `mcp_server.py` blob lineage) |
| 3 | Model: primary agent ran **qwen3.8-max** (= production model, via user's `~/.omo/omo.jsonc` sisyphus assignment); OmO subagents (explore) ran qwen3.8-flash; top-level config model was qwen3.8-flash | cost discipline + `~/.omo/omo.jsonc` is home-hardcoded (unisolatable without touching user state) | none expected — event shapes are harness-generated; (f) gap is timer-driven (σ<0.02 s across runs) |
| 4 | `oh-my-openagent.json` (production copy, in workspace `.opencode/` + XDG config dir) is only partially effective: OmO 4.x reads `~/.omo/omo.jsonc` (global) and merges the project file per-key; the image's legacy `"build"` key maps to OmO 4.x primary `sisyphus` ("Sisyphus - ultraworker") only via OmO's config migration | observed: project file won for `prometheus`/`explore` (flash), `omo.jsonc` won for `sisyphus` (max, absent from project file) | **T2/T10 finding**: the image's `oh-my-openagent.json` `build` key relies on OmO migration semantics — worth a pin-time check |
| 5 | No `OPENCODE_SERVER_PASSWORD` (serve unsecured on loopback) | HTTP auth is orthogonal to event shapes; T3 implements Basic Auth per kimaki recipe | none |
| 6 | AGENTS.md = 6-line trimmed copy (task-allowed); no ClickHouse creds (empty Jinja defaults), no workspace scripts/cron | scenarios don't touch them | none |
| 7 | User's global skills dirs (`~/.claude/skills`, `~/.agents/skills`) leak into the skill surface (home-based discovery ignores XDG_CONFIG_HOME; one duplicate-name WARN in log) | unavoidable without touching user state | none |

---

## 3. Event vocabulary observed (1.18.30, `/event` SSE, wire format `data: {json}` only — **no `event:` lines**; type lives in the JSON payload)

`server.connected`, `server.heartbeat` (exactly 10.0 s cadence), `session.created`,
`session.updated`, `session.status` (`{type:"busy"|"idle"}`), `session.idle`,
`session.error`, `session.diff`, `session.deleted`, `message.updated`,
`message.part.updated`, `message.part.delta`, `permission.asked`,
`permission.replied`, `todo.updated`, `file.edited`, `file.watcher.updated`,
`tui.toast.show`, `catalog.updated`, `integration.updated`, `plugin.added`,
`reference.updated`.

Part types: `text`, `reasoning`, `tool`, `step-start`, `step-finish` (same five
as kimaki's 1.2.15 fixtures). **Not observed**: `session.status {type:"retry"}`
(no provider retry occurred) → `stream_reset` mapping (D5/降级#14) remains
best-effort-unverified.

---

## 4. Natural-completion & terminal markers (B4 verification)

Natural completion (per logical turn) — confirmed exactly as D4/kimaki specify:

```jsonc
// message.updated, properties.info:
{ "role":"assistant", "time":{"created":…, "completed":1789212855852},
  "finish":"stop",            // "tool-calls" on intermediate rounds
  "error":null, "modelID":"qwen3.8-max", "providerID":"alibaba-cn",
  "cost":0.01506913587, "tokens":{…}, "parentID":"<user msg id>" }
```

- Intermediate tool rounds: `finish:"tool-calls"` + `time.completed` set → NOT a turn end (D4 holds).
- Turn end: `finish:"stop"` + `time.completed`, then `session.status idle` + `session.idle` within ~10 ms.
- **Finalized-text semantics (B4)**: last `finish≠"tool-calls"` completion before terminal — supported; text parts carry `time.end` and the cumulative snapshot in `message.part.updated`.
- Post-idle bookkeeping (CRITICAL for quiescence timers): within ~12 ms after `session.idle` the server re-emits `session.updated`, `session.diff`, and a `message.updated` **for the original user message**. A naive "any event resets the timer" quiescence implementation never expires (this bug was hit live during the spike and fixed in the driver via message-novelty filtering — `ActivityScanner` in `record_traces.py`).
- Each `message.updated` is emitted multiple times per message (duplicates are normal); dedupe by message id.

## 5. Scenario results (assertions executed)

### (a) Multi-tool turn — `scenario_a_multi_tool.jsonl` (94 frames)
One prompt → `vibe-trading_list_skills` (MCP) + `bash echo` in one assistant message (2 tool parts, `finish:"tool-calls"`), then final `finish:"stop"`. **idle count per logical turn = 1** ✓. 17 deltas. Status: busy×5→idle. Tool part completed state keys: `input, metadata, output, status, time, title` — **`state.output` present** (D5 preview source ✓); **`state.title` is `""` for MCP tools** — confirms D5's "preview from output, never title" choice.

### (b) Mid-turn abort — `scenario_b_abort.jsonl` (129 frames)
`sleep 30` bash tool running → `POST /session/:id/abort` (200/`true`) at +4 s. Terminal sequence: `session.error {error:{name:"MessageAbortedError",data:{message:"Aborted"}}}` → assistant `message.updated` with `time.completed` set, **`finish:null`, `error` present** → status idle → `session.idle` **×2** (double idle). **Abort is unambiguously distinguishable** from natural completion (error+no-finish vs finish:"stop") ✓. kimaki's 1.2.15 abort fixture shows the identical shape (incl. double idle and idle-before-error ordering at ~120 ms) → version-stable. `session.error` followed by idle ✓ (kimaki #74: no deadlock observed on abort).

### (c) OmO todo-continuation — `scenario_c_continuation_run{1,2}.jsonl` (539/506 frames)
3-item todo prompt, "stop after item 1". Observed per run: round 1 (todowrite + file write) → natural completion `finish:"stop"` → `session.idle` **#1** → **6.4 s gap** → OmO injects a **new user message** → round 2 (todos 2–3) → final completion → `session.idle` **#2** → quiescence (25 s, no further activity). **idle count = 2 per continued turn; continuations = 1 per run** ✓.
Injected message shape: ordinary `role:"user"` message, **`synthetic` flag NOT set**, text begins:
`[SYSTEM DIRECTIVE: OH-MY-OPENCODE - TODO CONTINUATION]` — identify continuations by this prefix (T4 should also suppress it from the user-visible vt transcript per D3, and T5 must not persist it as user text).
**Idle DOES fire between continuation rounds** — D4's "idle drains the queue, terminal only on quiescent idle" design is directly implementable; the quiescence timer just has to exceed 6.4 s.

### (d) Subagent spawn — `scenario_d_subagent.jsonl` (324 frames)
`task` tool → explore subagent. Parent-chain evidence (3 redundant sources):
1. child `session.created`/`session.updated` carry **`info.parentID` = main session id** (available from creation — the live-tracking source);
2. task tool part `state.metadata.sessionId` = child session id — **only populated in the `completed` state** (null while pending/running — deviation from the kimaki "canonical id" assumption for live tracking; metadata keys: `agent, description, load_skills, model, prompt, requested_subagent_type, run_in_background, sessionId, spawnDepth, sync, taskId, truncated`);
3. `GET /session/:id/children` → 200 with child list (`parentID` set).
Child events flow on the **same `/event` stream** with the child's own `sessionID` (child: 1 session.created, 14 message.updated, 176 deltas, **2 session.idle**). Child message chain: assistant `info.parentID` = child's user message id; child user messages have `parentID:null`. Main idle arrived **14 s after** the child's idles → sessionID-scoped idle handling is mandatory (condition 3). `D4 CHILD_EVENTS=drop` is implementable by sessionID filter alone.

### (e) Permission ask/reply — `scenario_e_permission.jsonl` (163 frames, phase B `bash=ask`)
`permission.asked` arrived with full relay shape:
```jsonc
{"id":"per_…","sessionID":"ses_…","permission":"bash",
 "patterns":["echo permission-spike-ok"],
 "metadata":{"command":"echo permission-spike-ok"},
 "always":["echo *"],
 "tool":{"messageID":"msg_…","callID":"call_…"}}
```
→ `POST /session/:id/permissions/per_… {"response":"once"}` → 200/`true` → `permission.replied {requestID, reply:"once"}` → tool executed → natural completion → idle ✓. Blocked window observable (3.3 s by design). The `always` array gives the bridge the "allow-always" pattern list for free (IM relay UX).

## 6. Kimaki public-fixture comparison (`cli/src/session-handler/event-stream-fixtures/*.jsonl`, fetched `real-session-task-normal.jsonl` + `session-explicit-abort.jsonl`)

| Aspect | kimaki fixture (opencode 1.2.15) | this spike (1.18.30) | Match |
|---|---|---|---|
| Core vocabulary | message.updated / message.part.updated / session.updated / session.status{busy,idle} / session.idle / session.diff | same + newer types (§3) | ✓ superset |
| Natural completion | `info.time.completed` + `finish:"stop"` | identical | ✓ |
| `message.part.delta` | present, `{sessionID,messageID,partID,field:"text",delta}` | **byte-identical property set** | ✓ (strong version-stability evidence for measurement i) |
| Abort terminal | `session.error MessageAbortedError` → idle×2; assistant msg `error` + no `finish` | identical (message text "Aborted" vs "The operation was aborted.") | ✓ |
| Assistant→user msg parentID | `info.parentID` = user msg id | identical | ✓ |
| Part types | text, step-start, step-finish (+tool) | text, reasoning, tool, step-start, step-finish | ✓ |
| Envelope | kimaki wraps: `{timestamp, threadId, projectDirectory, event:{…}}` | driver wraps: `{ts, mono, type, data:<raw wire payload>}` | inner payload identical |

## 7. Measurements (f)/(g)/(h)/(i)

**(f) idle→continuation first-event gap** — n=**2** (honest n; one continuation cycle per c-run, 2 runs): **6.409 s, 6.427 s** (min/max = p50 ≈ 6.4 s; σ = 0.018 s → timer-driven, not model-driven). Next event after idle = the injected `message.updated` (user). Natural-completion→re-prompt gap (f2) is the same 6.4 s (idle follows natural completion within ~10 ms). **→ `QUIESCENCE_S` ≥ 8 s** (recommend 8–10). Caveat: measured on this rig's model mix; the consistency suggests a fixed OmO continuation delay, but n=2 — T4 golden tests should pin the trace values and the field default should carry margin.

**(g) intra-tool silence** — `sleep 120` bash call (scenario g): tool part running window **121.8 s**; session-scoped events inside: **1** (a mid-run `message.part.updated` snapshot re-emission); **max silent gap = 120.75 s**. Stream-level `server.heartbeat` continued at **10.01 s** cadence (15 beats) — the SSE connection stays alive, but **no content events** arrive. Short tools (0.25–4.7 s windows across a/b/c/d/e traces) emit 1–7 session-scoped events inside (part-snapshot re-emissions, `todo.updated`, permission events) — i.e. opencode gives NO periodic progress event for a silent long tool. **→ B3 confirmed: any tool run >90 s starves the frontend watchdog; D5's synthesized `tool_heartbeat` every ~3 s during tool-part-running is mandatory** (a 120 s backtest run needs ~40 synthesized beats). The bridge can rely on `server.heartbeat` only as connection keep-alive, never as watchdog feed.

**(h) `DELETE /session/:id`** — present in `/doc` (OpenAPI 3.1.0) and live: **200 / `true`**, emits **`session.deleted`**, and **cascades to child sessions** (child DELETE after parent → 404). F7 cascade dependency satisfied. (All 10 spike sessions + 2 smoke sessions deleted post-run; `GET /session` shows 0 spike sessions remaining.)

**(i) `message.part.delta`** — **EXISTS on 1.18.30**. Single property-set across 946 deltas: `{sessionID, messageID, partID, field:"text", delta:<str>}`. `field` was always `"text"` — **including reasoning-part deltas** (join `partID→part.type` required; condition 2). D5 main path (direct passthrough) is GO; the `message.part.updated` diff-fallback stays as insurance only.

## 8. Version sensitivity (1.18.30 spike vs 1.18.18 image / 1.18.23 host)

| Measurement / mechanism | Sensitivity to CLI delta | Rationale |
|---|---|---|
| (i) `message.part.delta` existence+shape | **LOW** | identical shape in kimaki fixtures captured on 1.2.15 → stable across the whole 1.x line |
| Natural completion (`time.completed`+`finish`), abort terminal (`MessageAbortedError`, double idle) | **LOW** | identical in 1.2.15 fixtures |
| (f) 6.4 s continuation gap | **LOW for CLI, HIGH for OmO** | gap is produced by OmO 4.19.4 stop-hook — OmO version matches production exactly |
| (g) intra-tool silence / heartbeat synthesis need | **LOW** | structural (tool execution emits no content events); `server.heartbeat` 10 s cadence is the only possibly-newer detail — irrelevant since the bridge synthesizes its own |
| (h) DELETE + cascade | **LOW-MED** | legacy surface, long-stable; re-verify with one call at pin time (T10) |
| `permission.asked` payload (`patterns`/`always`/`tool.callID`) | **MED** | richer than opencode-runtime's 1.18.x description; fields could differ on 1.18.18 — re-verify at pin time; translator should read defensively (optional fields) |
| Newer event types (`todo.updated`, `file.edited`, `catalog.updated`, `plugin.added`, …) | **MED** | presence on 1.18.18 unverified — **T4 translator must be allowlist-based** (ignore unknown types), which makes the drift harmless |
| `session.status {type:"retry"}` | **UNVERIFIED** | never triggered in the spike (no provider retry); `stream_reset`/attempt-count mapping stays best-effort (降级 #14) |

**Drift alarm (D10)**: these traces are the golden fixtures — when T10 pins the
image CLI (1.18.18 vs 1.18.23 decision pending), replay the pin-target's event
stream against `record_traces.py` and diff the vocabulary/shapes above.

## 9. Deviation table vs D4/D5 assumptions

| D4/D5 assumption | Spike finding | Status |
|---|---|---|
| `QUIESCENCE_S` 3.0 initial | re-prompt at 6.4 s > 3.0 → false terminal risk | **FALSIFIED → raise to ≥8 s** |
| delta passthrough keyed on `field=="text"` | reasoning deltas also carry `field:"text"` | **REFINED → join partID→part.type** |
| `session.idle` per turn, queue-drain semantics | confirmed; but child sessions idle on same stream; double-idle on abort; post-idle bookkeeping re-emissions | **CONFIRMED + 3 implementation guards** (sessionID scope; novelty filter; ≥2-idle tolerance) |
| `session.error` → no idle (kimaki #74) needs synthetic idle | abort: error WAS followed by idle (×2); error-without-idle not observed | **GUARD KEPT** (defensive, cheap) |
| natural completion = `time.completed && finish≠"tool-calls"` | exactly observed | **CONFIRMED** |
| terminal payload availability (`summary/run_dir/elapsed_ms/provider/model`) | provider/model/cost/tokens on every assistant `message.updated`; `run_dir` extraction depends on backtest tool output (not exercised — no ClickHouse/backtest in spike); summary = last settled text (mechanism confirmed) | **CONFIRMED except run_dir source untested** (T4 golden with synthetic backtest output) |
| child canonical id = task part `state.metadata.sessionId` | only populated at completion; live tracking needs child `session.created info.parentID` | **REFINED** |
| tool preview = `state.output` first 200 chars | `state.output` present; `state.title` empty for MCP tools | **CONFIRMED** |
| OmO continuation detectable | injected user message NOT `synthetic`-flagged; text prefix `[SYSTEM DIRECTIVE: OH-MY-OPENCODE` | **REFINED → prefix-match + transcript suppression** |
| `session.status retry` → attempt counting | not observed (no retry occurred) | **UNCOVERED** (best-effort stands) |

## 10. Cost & turn accounting (honest)

35 assistant model steps across 8 recorded sessions (+3 pre-scenario smoke/probe
turns), 10 user messages (incl. 2 OmO continuations). Session costs sum
**$0.51** (+~$0.15 smoke/probe) ≈ **$0.66 total**. The ~30-turn budget refers to
logical turns; tool-round steps and the explore subagent's internal calls put
model steps at 35 — slightly over budget, disclosed.

## 11. Reproduction

```bash
# 1. workspace + config (see §2); render with the production renderer:
python3 OpencodeAgent/config/render_config.py \
  --template /tmp/oc-spike-config-src/opencode.json.tmpl \
  --manifest OpencodeAgent/config/vibe-trading-tools.json \
  --subagents OpencodeAgent/config/subagents.json \
  --target /tmp/oc-spike-xdg/opencode/opencode.json
# 2. serve (phase A):
cd /tmp/oc-spike-workspace && XDG_CONFIG_HOME=/tmp/oc-spike-xdg \
  opencode serve --hostname 127.0.0.1 --port 14096 --print-logs --log-level DEBUG
# 3. drive + record:
cd agent/tests/fixtures/opencode_bridge
python3 record_traces.py --out traces --scenarios a,b,c,d,g,h
# 4. phase B: add "permission":{"bash":"ask"} to rendered config, restart serve:
python3 record_traces.py --out traces --scenarios e
# 5. re-derive measurements from raw traces (no LLM calls):
python3 record_traces.py --out traces --analyze-only
```

## 12. Artifacts

| File | Content |
|---|---|
| `traces/scenario_a_multi_tool.jsonl` | (a) 94 frames |
| `traces/scenario_b_abort.jsonl` | (b) 129 frames |
| `traces/scenario_c_continuation_run1.jsonl` / `run2.jsonl` | (c)+(f) 539/506 frames |
| `traces/scenario_d_subagent.jsonl` | (d) 324 frames (main+child sids) |
| `traces/scenario_e_permission.jsonl` | (e) 163 frames (phase B) |
| `traces/scenario_g_silent_tool.jsonl` | (g) 87 frames (sleep-120 window) |
| `traces/measurement_h_delete_session.jsonl` | (h) DELETE + `session.deleted` |
| `traces/measurements.json` | f/f2/g/h/i + per-scenario assertions (machine-readable) |
| `record_traces.py` | reusable driver: SSE capture + scenario orchestration (stdlib-only) |
| `analyze_traces.py` | measurement/assertion derivation from raw traces (shared `ActivityScanner` quiescence semantics — the T4 translator reuses this logic) |

Trace JSONL frame format: `{"ts":<epoch_s>,"mono":<monotonic_s>,"type":<wire event type>,"data":<raw wire payload, verbatim>}`; `_recorder.*` types are driver boundary markers. Sanitized: secret-pattern scan clean; no home-directory paths; no credentials (ClickHouse vars empty by design).
