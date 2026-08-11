# T8 IM-parity findings — 16 adapters + ChannelRuntime UNCHANGED on the opencode engine

Date: 2026-09-13 · rig: opencode 1.18.30 + OmO 4.19.4 (T7 `start_rig.py`, local,
no docker) · serve 127.0.0.1:**14096**, gateway 127.0.0.1:**18080**, scratch
`/tmp/vt-t8-rig` (`VIBE_TRADING_HOME=/tmp/vt-t8-rig/vt-home`, scratch XDG +
`API_AUTH_KEY`). T8-owned ports/root only — the parallel T9 rig
(14097/18081, `/tmp/vt-t9-rig`) was never touched; the serve pid is
command-line + listen-port verified before any kill (`imlib.verified_serve_pid`).

**Verdict: the architecture claim holds.** The 16 IM adapters + `ChannelRuntime`
+ `ChannelManager` + `BaseChannel` consume the `SessionService` seam WITHOUT
knowing the engine changed (`VIBE_TRADING_ENGINE=opencode` →
`RecoverableOpencodeSessionService`). Four scenario groups are evidenced on
disk; the adapters/runtime **zero-diff assertion is green** (committed +
working tree, against mymain merge-base `5eda88d1`). One live-death timing bug
(**T8-1**) is documented below — it is in the bridge (T9's edit lane this
wave), NOT in the adapters/runtime, and is marked as a scenario FAIL via
`pytest.xfail` with the measured numbers, per the task's "document, don't fix"
directive.

Official run: `run_t8_im_parity.py --death-observe-s 660` →
**5 passed, 1 xfailed in 903s** (`pytest-t8.log`). Per-scenario assertion
detail in `s*-results.json`; transcripts/attempt records in `s*-store/`.

## How the parity is exercised (in-process IM stack on the live rig serve)

`imlib.build_im_stack` assembles the PRODUCTION wiring in the test process
against the rig's live `opencode serve`: the real unchanged
`ChannelRuntime`/`ChannelManager`/`BaseChannel` ingress (`_handle_message` →
bus → runtime → outbound dispatch), a test-defined `MockChannel` adapter (the
bus-level mock of `tests/test_channels_runtime.py` promoted to a real
`BaseChannel` subclass so the manager's consumption contract is exercised too
— it lives in `tests/`, never under `src/channels/`), and the real bridge
service started through the production sequence
`opencode_bridge.wiring.start_session_service` (tool map → subscribe-first
pumps → reconcile). In-process (rather than inside the gateway) because the
parity claim is that the runtime/adapters consume the seam unchanged, and an
in-process stack observes that seam with per-poll timing precision the
engine-death scenario needs; the gateway host itself gets its own live
preamble (s0). Rationale in `imlib.py` module docstring.

## Scenario results (all four groups evidenced)

### s0 — gateway seam preamble (5 checks, no LLM)
The production gateway host (`api_server`, `VIBE_TRADING_ENGINE=opencode`)
serves the seam: `GET /health` 200; `POST /sessions` creates a session on the
opencode engine; `GET /channels/status` returns the runtime status shape
(`running`/`inbound_queue`/`outbound_queue`/`session_count`/`channels`);
`DELETE /sessions/{id}` (cascade path; engine session is lazy = none yet).

### s1 — mock-channel scripted long-turn round trip (11 checks, ~3 min)
A **193.3 s** turn (bash `sleep 170 && echo T8_LONG_DONE`) through the mock
adapter. `runtime._wait_for_reply` polling contract satisfied:
* **D4 proven** — a 2 s poller observed the session transcript **94 times,
  every poll empty** (`all_empty: True`): the assistant Message is persisted
  ONLY at terminal, so IM polling sees nothing during the long silent turn,
  then the final reply is delivered inside the 600 s budget.
* Final reply `T8_LONG_DONE` delivered as the OutboundMessage a real adapter
  renders into a markdown card (`dingtalk.py:672-680` `send` →
  `_send_markdown_text` `sampleMarkdown`); metadata
  `{_channel_runtime, attempt_id, session_id, message_id}` exactly.
* **B3 defense live** — **57 `tool_heartbeat`s, median gap 3.0 s** across the
  170 s silence (translator alive; the IM pipeline shares it).
* **D6 reply metadata** on the assistant Message: `status=completed`,
  `elapsed_ms=188858`, `provider=alibaba-cn`, `model=qwen3.8-max`; attempt
  terminal `completed`; exactly one assistant message for the attempt.
* Event vocabulary census (`s1-long-turn-results.json`): `attempt.created/
  started/completed`, `message.received`, `reasoning_delta`, `text_delta`,
  `llm_usage`, `session.created`, `tool_call`, `tool_heartbeat`, `tool_result`.
* `session.config` carries `{channel, channel_chat_id}` (runtime.py:329 — T9's
  `_stream_delta` producer input).

### s3 — command regression + D3 cascade (18 checks)
`/pairing`, `/new`, `/reset`, `/newsession` through the runtime on the opencode
engine:
* `/pairing list` — non-operator rejected (`Not authorized`,
  `unauthorized: True`), operator answered, **engine never touched** (no
  engine session created — the command path is model-free).
* Turn 1 → vt session `s1` with engine session `e1`. `/new` → `Session reset`
  (`session_reset: True`), mapping dropped from the session-map file.
* **D3 cascade** — the NEXT turn starts a **fresh vt session `s2` AND a fresh
  engine session `e2 ≠ e1`**; the OLD engine session is deliberately KEPT
  (`GET /session/{e1}/message` 200 — browsable transcript, engine keeps
  context; `recovery.py` module-docstring ownership rule). Exactly **2** engine
  sessions created total.
* `/reset` + `/newsession` aliases reset; fresh `session.config` again carries
  `{channel, channel_chat_id}`.

### s4 — scheduled briefing delivery path (9 checks)
The production cron+IM briefing path (T2 memo: live in host-direct production)
runs UNCHANGED on the opencode engine, via the real `scheduled_routes`
functions (host module resolved through `sys.modules`):
* `_dispatch_scheduled_research_job` → **positional** `send_message`
  (scheduled_routes.py:88, D6 dual call shape) enqueues a real turn.
* `_read_scheduled_briefing` reads `metadata["status"]`
  (scheduled_routes.py:109-115) → `status=completed`, text `T8_BRIEF_OK`
  (the same text the session shows — no app/channel divergence).
* `_send_scheduled_briefing` (scheduled_routes.py:129-143) →
  `adapter.send_with_receipt` returns a `DeliveryReceipt(status="accepted")`;
  the brief reaches the mock channel **verbatim**.
* Failure contract: unknown channel / missing target → `RuntimeError` (the
  outbox records a retryable failure, never a phantom delivery).

### s2 — engine-death recovery timing (XFAIL = scenario FAIL, bug T8-1)
SIGKILL the serve mid-turn (bash `sleep 120`, killed at tool+5 s, ownership-
verified pid). Measured against the **<30 s** acceptance:

| metric | measured | acceptance | verdict |
|---|---|---|---|
| attempt lands terminal (live) | **never** (`t_attempt_terminal_s: None`, 658-point/660 s timeline) | failed, <30 s | **FAIL** |
| IM explicit failure reply | **590.28 s** — `Channel runtime error: TimeoutError: timed out waiting for assistant reply`, metadata `{_channel_runtime, error: True, message_id}` | <30 s | **FAIL** on time; **PASS** on "explicit text, non-silent" (plan-QA failure scenario, at the 600 s budget boundary) |
| T6 restart reconcile (Phase B) | `interrupted: ['3a7c6add41cc']`; `_wait_for_reply` returns it in **0.0 s**, `status=interrupted`, `recovery_reason=service_restart` | — | **PASS** |
| startup vs dead engine | `load_tool_mapping` raised `OpencodeConnectionError` (loud, per `wiring.py` contract) | — | **PASS** |

See **Finding T8-1** below for root cause. Phase B proves the recovery half of
the story works: a process restart reconciles the hung attempt to `interrupted`
and the IM polling contract is then satisfied immediately. In the 660 s window
runtime1's own handler had already timed out at ~590 s (before Phase B), so it
did not pick the interrupted reply up live (`runtime1_picked_up: None`); the
short-window committed default (`T8_DEATH_OBSERVE_S=90`) exercises that live
pickup instead.

## Finding T8-1 — live engine-death mid-turn is not detected within budget (bridge bug)

- **Symptom**: killing `opencode serve` mid-turn does NOT land the attempt
  `failed`. It hangs until the IM `_wait_for_reply` 600 s polling budget
  exhausts, then the runtime's generic `except` publishes an explicit
  `TimeoutError` failure reply (590.28 s). The task's <30 s acceptance FAILS.
- **Root cause (code read, confirmed empirically)**: `OpencodeDriver.events()`
  (`driver.py:230-278`) reconnects the `/event` SSE stream FOREVER with
  exponential backoff (0.5 → 30 s, `client.py:44-46`). On serve death the
  stream drops, the driver logs + reconnects (connection-refused), and the
  async generator never terminates → the service's `_pump_driver_events`
  (`service_persistence.py:359-373`) never raises → `_fail_all_pending(...)`
  (the no-hang guarantee) never fires → `_await_terminal`'s guard tasks
  (pump/dispatch) stay alive → the attempt's `done` future never resolves.
  The translator has no attempt-level watchdog (only post-`idle` quiescence),
  and no `idle`/`session.error` ever arrives from a dead serve.
- **Why it is NOT an adapter/runtime defect**: `ChannelRuntime._wait_for_reply`
  behaves exactly per contract — it polls `get_messages` and, on budget
  exhaustion with no terminal message, raises `TimeoutError`, which
  `_handle_inbound`'s `except Exception` turns into an explicit user-visible
  failure reply (non-silent). The 16 adapters and the runtime are zero-diff
  and correct; the gap is purely bridge-side live-death detection.
- **What DOES recover it**: T6's restart reconciliation
  (`RecoverableOpencodeSessionService.reconcile`, branch 3 — engine
  unreachable → `interrupted`) lands the attempt and satisfies IM polling
  immediately (Phase B, 0.0 s). So a gateway restart heals it; a live gateway
  waiting on a permanently-dead engine does not, until the 600 s budget.
- **Disposition**: scenario marked FAIL via `pytest.xfail(strict=False)` with
  the measured numbers in the reason. **Not fixed here** — the task forbids
  editing `agent/src/opencode_bridge/**` this wave (T9 owns bridge src; a
  fix would corrupt both commits). A follow-up task should add live-death
  detection: e.g. a bounded reconnect budget / consecutive-failure threshold in
  `OpencodeDriver.events()` that raises after N failed reconnects (so the pump
  dies and `_fail_all_pending` fires), or an attempt-level engine-liveness
  watchdog in the service. Suggested acceptance: attempt lands `failed` and IM
  receives an explicit failure reply in **<30 s** of serve death.

## Non-findings (verified working live, zero-diff)

- D3 transcript ownership: user messages persisted RAW (no D8 injection block)
  across all turns; the injection goes only to the engine prompt.
- D6 reply-metadata enumeration on completed replies (status/elapsed_ms/
  provider/model) — s1; and on the interrupted reply (status/partial/
  recovery_reason) — s2 Phase B.
- `session.last_attempt_id` maintained (attempt chaining); `session.config`
  `{channel, channel_chat_id}` present (runtime.py:329) for T9's producer.
- Manager consumption contract: outbound dispatch → `MockChannel.send` (and
  `send_with_receipt` for the briefing) with retry/coalescing paths intact.
- Production model in use: `provider=alibaba-cn model=qwen3.8-max` (the rig's
  auth, read-as-is), matching T7.

## Real-platform smoke (钉钉/飞书) — PENDING, user-gated

Plan T8 asks for a real-platform smoke on the tenant's own TEST bot. **No
unambiguously-test bot credentials exist in the environment**: searched
`OpencodeAgent/.env.example` (only a placeholder `DINGTALK_WEBHOOK=...your-token`
notification hook, not a channel adapter credential), the user's
`~/.vibe-trading/.env` (absent) and the checkout `agent/.env` (only
`DASHSCOPE_*`/`LANGCHAIN_*`/`CLICKHOUSE_*`/`TUSHARE_*` — no
`DINGTALK_*`/`FEISHU_*` channel credentials). Per the task rule, production
credentials must NOT be used and no message was sent to any real
channel/group.

Delivered instead: **`real_platform_smoke.py`** — a ready-to-run,
credential-parameterized real-bot smoke. It refuses to run without explicitly
TEST-bot env vars (verified: `VT_T8_DINGTALK_CLIENT_ID/SECRET` or
`VT_T8_FEISHU_APP_ID/SECRET` + `VT_T8_ALLOW_FROM`), never reads any config
file for credentials, and reuses the same production wiring with the REAL
adapter loaded by `ChannelManager`. To execute (user step)::

    # tenant TEST bot only; rig must be up (run_t8_im_parity.py step 1)
    VT_T8_PLATFORM=dingtalk \
    VT_T8_DINGTALK_CLIENT_ID=<TEST app key> \
    VT_T8_DINGTALK_CLIENT_SECRET=<TEST app secret> \
    VT_T8_ALLOW_FROM=<your sender id> \
    python3 agent/tests/e2e_engine_bridge/real_platform_smoke.py

It waits for a DM `reply with exactly SMOKE_OK`, asserts the round trip +
`send_with_receipt`, prices + DELETEs its engine sessions, and writes
`real-smoke-results.json`. Missing adapter SDK exits with the registry install
hint (`pip install 'vibe-trading-ai[dingtalk]'`). **Mock-platform evidence
(this run) is the committed acceptance; real-platform is user-gated.**

## Cost accounting

- Official run (run 2, full suite): **$0.0716** serve-side (`t8-cost.json`),
  ~5 model steps (s1 long turn $0.029, s3 two short turns $0.028, s4 one short
  turn $0.014, s2 killed-before-completion $0.0, s0 zero). All 5 engine
  sessions DELETEd after costing (spike §7h hygiene; the rig shares the user's
  opencode data dir read-as-is).
- Disclosed dry run (run 1): **$0.115** serve-side, ~5 model steps — identical
  s0/s2/s3/s4 outcomes; its s1 hit a TEST-SIDE assertion bug (the 2 s D4
  poller cannot win the 0.25 s delivery race, so `t_first_assistant` stayed
  `None`). Fixed the assertion to prove D4 directly (all 94 in-turn polls
  empty = no assistant message before terminal) rather than via the slow
  poller catching the terminal message. Run 1 preserved as
  `../t8-im-run1-debug/`. Disclosed rather than hidden — same posture as T7
  §Cost and spike §10.
- Disclosed total: **~$0.19** serve-side across run 1 + run 2 (+ a few cents of
  gateway auto-title ChatLLM calls on the D11 route). Per-run model-step budget
  held; total ~10 steps, well under the ~40 cap.

---

## Addendum (2026-09-13): Finding T8-1 → RESOLVED — commit `90a4378a`

`fix(engine-bridge): live engine-death detection lands attempts within IM
budget`. Mechanism: **driver-internal bounded liveness surfacing** (design
call + rejected alternatives in `stream_liveness.py` module docstring).
`OpencodeDriver.events()` now raises the typed `EnginePresumedDeadError` on
either bounded death signal — (1) `liveness_max_silent_cycles=4` consecutive
/event connection cycles delivering ZERO frames (SIGKILL on localhost: EOF +
refused reconnects → ~3.5 s at the backoff floor), or (2)
`liveness_silence_window_s=15` without ANY received frame, evaluated at
cycle end (frozen serve: `stream_read_timeout_s` 90→20 = 2× the heartbeat
cadence, so the first silent read-timeout cycle trips at ~20 s). Heartbeats
are wire bytes: pure CONTENT silence never trips either signal (T1 traces:
max observed inter-frame gap 10.01 s < 15 s window — pinned by a data-bound
test + a scenario-g real-timestamp replay through the live liveness logic;
the translator golden replay of g stays green untouched). The pump's
EXISTING `_fail_all_pending` path lands the attempt `failed` exactly once
through the frozen T5 terminal shape; the EXISTING `_ensure_pumps`
restart-on-next-send re-establishes the stream when the engine returns — no
gateway restart (T10's supervisord mode). Zero service/translator changes;
native engine untouched; subscribe-first single persistent connection
preserved.

### Before / after (s2, live rig, same recipe: serve 14096 / gateway 18080 /
### /tmp/vt-t8-rig, ownership-verified kill inside the silent bash sleep)

| metric | before (run 2, xfail) | after (run 4, PASS) | acceptance |
|---|---|---|---|
| attempt lands terminal (live) | **never** (660 s window) | **failed @ 8.03 s** | failed, <30 s ✓ |
| IM explicit failure reply | 590.28 s (generic TimeoutError) | **8.03 s** — `Execution failed: engine event stream lost: … presumed dead: 4 consecutive /event cycles delivered no frame …` via `_fail_all_pending` (no new vocabulary) | <30 s, explicit ✓ |
| T6 restart reconcile (Phase B) | interrupted (heal path) | **composes**: loud `load_tool_mapping`, idempotent EMPTY reconcile, attempt stays `failed`, exactly 1 reply, `_wait_for_reply` 0.0 s | green ✓ |
| engine returns (Phase C, new) | n/a | respawn serve → SAME stack, no gateway restart: **pump restarted (new task), `T8_RESUME_OK` round trip 12.86 s, attempt completed** | green ✓ |

Official re-run: `run_t8_im_parity.py` (default `--death-observe-s` now 120)
→ **6 passed in 272 s** (`pytest-t8.log`); s2 is a real PASS, xfail removed.
Full bridge suite: **227 passed / 6 skipped** (baseline 222 + 5 new unit
tests: count signal, window signal, heartbeats-across-reconnects,
scenario-g real-trace guard, service-level death→exactly-once→resume with
T9 observer-tap survival). black/ruff clean; protected zones zero-diff.

### Disclosed debug run (run 3) + cost

Run 3 (`t8-im-run3-phasec-debug/`) failed ONLY in Phase C, on two rig-env
traps now fixed + documented in `imlib.respawn_serve`: (a) the pytest
process runs under conftest's sandbox `HOME`, so the respawned serve got a
DIFFERENT opencode data dir than the runner-spawned original (no auth,
fresh-store `SQLiteError: disk I/O error` on session create) — fixed by
restoring the real HOME from the passwd entry; (b) `/app` answers HTML
before the API finishes initializing (~14 s; requests queue, /event delivers
no heartbeats) — fixed with a `GET /mcp` 200 readiness gate. The runner's
cleanup reachability probe also crashed JSON-decoding a LIVE serve's /app
HTML — now status-only. Run 3's Phase A/B were already green (8.05 s /
0.0 s), and its liveness log shows BOTH signals firing live (count signal
on the kill; window signal — "no frame received for 20.0s" — against the
initializing respawn), i.e. the fix worked; only the test-harness respawn
was broken. Cost: run-4 serve-side **$0.086** (s2 kill ≈ $0, resume turn
$0.014); pricing run-3's orphans at DELETE time brought the run-4 cost
report to $0.166 total — combined disclosed spend for the T8-1 fix ≈
**$0.17**, marginally over the ~$0.15 guideline because run-3's
environmental Phase-C failure required a full re-run. All 16 registry
sessions DELETEd (200) or confirmed already gone (404); zero `mockim`/`T8_`
survivors in the user's real opencode store (storage grep empty).

Known bounded residual (documented in `stream_liveness.py`): a serve that
dies AND restarts faster than the ~3.5 s count budget is never declared
dead; its zombie in-flight turn waits like a long-silent tool
(recovery.py's pre-existing residual edge). New turns are unaffected.
