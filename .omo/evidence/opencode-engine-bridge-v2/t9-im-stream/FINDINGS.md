# T9 IM streaming (`_stream_delta` producer) — FINDINGS

Date: 2026-09-13 · Branch `mymain-engine-bridge` (worktree
`/Users/mgong/LegoNanoBot/vibe-trading-engine-bridge`, base HEAD `225ba2f3` = T7 gate)
Plan refs: T9 row · D5/D7 · §7.3 (grinev) · §7.5 (IM = 渐进编辑节流)

## Verdict

**Mock-platform live evidence: PASS (3/3 scenarios).** Real-platform
(Telegram) evidence: **PENDING** — no explicitly-test bot credentials
available in this environment; a parameterized ready-to-run smoke script is
delivered (`telegram_smoke.py`, fail-closed without
`TELEGRAM_TEST_BOT_TOKEN`/`TELEGRAM_TEST_CHAT_ID`/`TELEGRAM_TEST_SENDER_ID`;
production credentials/real channels are never used — plan T9 constraint).

Delivered code (committed): `agent/src/opencode_bridge/im_stream.py` (391
lines) + minimal tap (`service_persistence.py` +24/−3: optional async
`vt_event_observer` awaited in `_handle_vt_event` after the `attempt_id`
stamp / before terminal resolution, exception-contained) + wiring attach
(`wiring.py` +6/−1 at the composition root) + package export. Tests:
`agent/tests/test_opencode_bridge_im_stream.py` (29) + wiring-test extension.

## Architecture findings (read before judging the design)

1. **No native `_stream_delta` producer exists — anywhere.** Grep across
   `agent/src` (this branch AND upstream `main` @ `5f85c6d5`) finds the
   vocabulary ONLY in consumers: `manager.py` (coalescing 323-367, dedup
   256-281, `_streamed` skip 330-368), `base.py` (`send_delta` contract,
   `supports_streaming`, `_wants_stream` inbound stamp), and six adapter
   edit-buffer implementations (telegram/discord/feishu/matrix/weixin/
   websocket). All of it landed dormant in `101306e9` ("feat: wire IM
   channel runtime"). **T9's bridge producer is the FIRST producer**, so the
   "exact metadata contract" was mirrored from the frozen consumers, not
   from a native emitter (there is none to mirror).

2. **Coalescer-absorption text-loss hazard (design driver).** A
   `_stream_end` marker that ALSO carries `_stream_delta` is absorbed by
   `_coalesce_stream_deltas` (manager.py:401-407) into a merged delta batch;
   telegram/matrix `send_delta` `_stream_end` branches then finalize from
   their OWN buffer and DISCARD the batch content (telegram.py:926-933
   ignores the `delta` arg on end). ⇒ End markers carry `_stream_end` ONLY
   (never `_stream_delta`), so they dispatch separately after all deltas.
   Pinned by unit test (`test_stream_end_on_every_terminal`).

3. **Duplicate-final gap in the vendored consumer infra (the hard problem).**
   With streaming ON, the turn produces BOTH the finalized stream preview
   AND `ChannelRuntime`'s polled terminal message (runtime.py:218-234 —
   the T8-verified polling contract, frozen for T9). The runtime final
   carries neither `_streamed` (the manager's own skip-send flag, whose
   documented purpose is exactly this: weixin.py:1226-1231 "the final
   answer carries the `_streamed` flag and bypasses send") nor
   `origin_message_id` (the only key `_should_suppress_outbound` suppresses
   on), so on edit-capable real adapters the user would see the answer
   twice. Producer-side remedy within zero-diff-on-channels: a **one-shot
   `_streamed` tagger** — a narrow, reversible `publish_outbound` decorator
   on the shared bus instance (the only seam that sees the runtime final
   pre-dispatch) that marks ONLY messages with `_channel_runtime` + a
   recorded attempt id + whitespace-normalized content EXACTLY equal to the
   finalized stream text. Any mismatch (proposal suffix, continuation
   re-settle, sparse-snapshot summary, failure text) passes the final
   through unmarked — fail-safe direction: an extra bubble is possible, a
   lost answer is not. Manager dedup fingerprints are never touched
   (stream messages bypass dedup by manager design; the tagger adds a mark,
   never suppresses content).
   **Upstream-candidate note (divergence ledger):** the native fix is
   `ChannelRuntime` setting `_streamed` when the inbound carried
   `_wants_stream` (base.py:225-226 already stamps it!) — a channels/
   change, frozen for T9 (Must-NOT 不改 manager/适配器/runtime).

4. **Segmentation = translator `iter`** (one assistant message = one ReAct
   iteration). Intermediate closes carry `_resuming: True` — feishu's
   documented multi-tool-round semantics (feishu.py:1904-1911). This makes
   the LAST segment's text equal `attempt.completed.summary`
   (= `finalized_text(last natural completion)`, lifecycle.py:257-270) in
   the common case, which is what makes the tagger's exact-match gate
   reliable. Flush ordering (grinev): pending text ALWAYS flushes before a
   segment close or tool boundary; tool events force-flush (also the
   trailing edge during 3 s heartbeats); the terminal flushes then closes.

5. **Throttle ladder**: attempt-age bands `<15s→1s, <60s→2s, <180s→5s,
   else→10s cap` (plan T9 "1s→2s→5s→10s 封顶"; grinev §7.3), event-driven
   flushes (no background timers), first flush immediate. Layered UNDER
   manager coalescing: the producer rate-limits publications; the manager
   still merges whatever queues up back-to-back (observed live: 4
   `text_delta` SSE events → 3 adapter delta calls in scenario A).

6. **Governance reality honored** (T7 FINDINGS #1): backtest/read_file MCP
   tools are disabled, so the tool-heavy turn (scenario C) runs via bash.

## Live evidence (mock platform) — rig on MY ports 14097/18081

Rig: T7 `start_rig.py` UNMODIFIED (`--rig-root /tmp/vt-t9-rig --serve-port
14097 --gateway-port 18081`); stack: T8's `imlib.py` imported UNMODIFIED
(parallel-task file — read-only reuse) + `StreamingMockChannel` (T8's
`MockChannel` subclassed with telegram-style recorded `send_delta`:
preview create → edits → finalize; `src/channels/` zero-diff) + REAL
`ChannelRuntime`/`ChannelManager`/`MessageBus` + REAL bridge service
(production `start_session_service` sequence) + producer attached via the
documented `plumbing_resolver` seam (the gateway-populated `src.api.state`
singletons don't exist in-process; the production attach path
`wiring.build_session_service` + default resolver are covered by unit and
wiring tests, incl. `test_default_resolver_reads_state_singletons`).

Runner: `run_t9_im_stream.py` (one stack, per-channel `streaming` switch
flipped between turns — the gate is re-checked at every attempt open by
design). Results:

| Scenario | Result | Key observations |
|---|---|---|
| A `streaming-on` | **PASS** (15.6 s) | 4 `text_delta` SSE → preview_create + 2 edits + 1 finalize (edits-not-spam); finalized text == persisted reply; **0 `channel.send` records** (no duplicate final); producer log: `runtime final for attempt 0ecf925b4177 marked _streamed` |
| B `streaming-off` | **PASS** (12.3 s) | **0 stream records** (zero `_stream_delta` publications); exactly 1 terminal message, `_streamed` absent, content == persisted reply — terminal single-message fallback unchanged |
| C `tool-order` | **PASS** (16.5 s) | bash `echo t9-flush-check` turn: pre-tool text ("Checking now.") flushed in 2 edits BEFORE the tool_call boundary; 1 intermediate finalize with `_resuming`; ordered finalized text keeps pre-tool before post-tool; single terminal finalize; 0 duplicate finals |

Artifacts: `streaming-{on,off}-results.json`, `tool-order-results.json`
(per-assertion pass/detail), `*-adapter-log.json` (every mock-adapter call,
timestamped), `sse-events.json` (EventBus timeline — Web surface intact),
`producer-log.json` (the two `_streamed` marks), `turns.json`, `cost.json`
(**$0.0569 total**, 3 engine sessions priced then DELETEd → 3×200; the
in-process store is outside stop_rig's vt_home sweep, so the runner
self-cleans), `serve.log`/`gateway.log` (copied).

Gateway wiring smoke (same rig): gateway booted ENGINE=opencode →
`preflight_engine_bridge` completed (fail-loud contract: startup would have
blocked otherwise), `POST /sessions` → 200 via the bridge service, 0
ERROR/Traceback lines in `gateway.log`.

## Gates

* New unit suite: 29 passed (mapping/addressing · ladder timing on an
  injectable clock, no real sleeps · `_stream_end` on all three terminals ·
  switch-off zero-publication fallback · interleaved attempts isolated by
  `_stream_id` (QA 乱序不串话) · flush ordering + `_resuming` segmentation ·
  tagger match/mismatch/one-shot/detach · service-level tap proving the Web
  SSE vocabulary is untouched and a raising observer never breaks dispatch).
* Bridge baseline: `pytest -k opencode_bridge` → **217 passed, 11 skipped**
  (was 188/11 at HEAD `225ba2f3`); bridge files only → 216 passed,
  1 skipped (openbb import skip, pre-existing).
* Channels-adjacent suites (`test_channels_runtime`, `test_channels_api`,
  `test_session_reply_runtime_metadata`, `test_scheduled_delivery_hook`,
  `test_api_live_runtime`, `test_state_migration_wiring`,
  `test_session_restart_recovery`): 69 passed.
* `black` + `ruff` clean on every touched path; zero-diff guards:
  `agent/src/channels/**`, `agent/src/{agent,session,providers}/**`,
  `frontend/**`, `agent/src/api/state.py`, T7 rig files — all untouched
  (T8's untracked parallel files left alone).

## Rig isolation record (parallel T8)

Ports 14097 (serve) / 18081 (gateway) — verified free before start, owned
by this rig (PIDs 60257/60291 from `rig_state.json`); 14096/18080 and
`/tmp/vt-t8-rig` never touched; only my two PIDs killed at teardown
(`stop_rig.py` verifies cmdline+port ownership before kill).

## Real-platform (Telegram) smoke — how to run when a TEST bot exists

```bash
python3 agent/tests/e2e_engine_bridge/start_rig.py \
    --rig-root /tmp/vt-t9-rig --serve-port 14097 --gateway-port 18081
TELEGRAM_TEST_BOT_TOKEN=<test-bot> TELEGRAM_TEST_CHAT_ID=<tester-chat> \
TELEGRAM_TEST_SENDER_ID=<tester-id> \
    python3 .omo/evidence/opencode-engine-bridge-v2/t9-im-stream/telegram_smoke.py
```

Expected: ONE Telegram message edits itself into shape (no second final
bubble — the tagger marks the runtime final `_streamed`; telegram's
`_stream_end` path does the final `edit_message_text`), then
`telegram-smoke-results.json` lands here. The script spies on adapter
calls without altering the real `telegram.py` code path, pins `allow_from`
to the single tester (never `*`), and DELETEs its engine session.
Caveat to re-verify live: telegram flood-control on very long turns
(adapter-internal `stream_edit_interval` layers under the producer ladder).
