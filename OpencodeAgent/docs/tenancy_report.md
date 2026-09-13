# T12 — Tenant Isolation Matrix + Resource Measurements (Phase-3 Exit Gate)

> Plan `opencode-engine-bridge-v2` T12 (Wave 4 汇合). Verdict: **PASS —
> 93/93 matrix checks green, zero cross-tenant reachability at any probed
> layer.** F1's architecture claim (full-stack per-tenant containers + thin
> router = safe multi-tenancy, D2) **survives its falsification instrument**:
> the plan's QA failure scenario (任一跨租户可达 → Phase 3 FAIL) never fired.
>
> Host: arm64 macOS, colima/Docker 27.4.0 with **Rosetta** amd64 emulation.
> Image: T10's `opencode-serve:v3.0.0-tenant` (`baf5aab39002`, vendored
> `02731637`) — not rebuilt. Router: T11's `OpencodeAgent/deploy/router/`
> (host-run uvicorn, the code path the containerized router smoke also
> validated in T11). Rig: tenants `a` (28081, `a.t12.tenant.local`,
> `vt-t12-a`) / `b` (28082, `b.t12.tenant.local`, `vt-t12-b`), router 28080,
> scratch `/tmp/vt-t12-rig/`, torn down after capture.

## 1. Isolation matrix — every item measured (plan Must-NOT: 不跳过任何矩阵项)

Authoritative run: `matrix_results.json` (93/93, 2026-09-13 12:23 +0800).
Group scores: m1 10/10 · m2 4/4 · m3 12/12 · m4 10/10 · m5-config 10/10 ·
m5-im 16/16 · m6 9/9 · turn 3/3 · reclaim 10/10 · wake 9/9.

| # | Item (plan T12 row) | Mechanism exercised | Level proven | Verdict |
|---|---|---|---|---|
| 1 | A 的 key 打 B upstream → 401 | Gateway key-first auth (`security.py:_validate_api_auth`, GHSA-7wgj posture: configured key ⇒ even loopback peers need it) | **Live HTTP**, router bypassed: host → B's published port with A's key (bare Host and B's public Host), anonymous, plus the router-level token/Host conflict (403 `TenantMismatch`), plus positive controls (own key → 200) — both directions | **PASS** (10) |
| 2 | 浏览器经 router POST → 200 非 403 | Host preservation + `API_ALLOWED_HOSTS` (D9/B2 recipe) + `_reject_cross_site_browser_request` Origin/Host match | **Live HTTP** with full browser header set (Origin, Referer, Sec-Fetch-Site/Mode/Dest, Chrome UA): same-origin POST → **201** on both tenants; negative control: cross-site Origin → **403** (the guard is an allow-list, not disabled) | **PASS** (4) |
| 3 | SSE ticket 各自独立 | Per-gateway-process ticket store (`security.py:_sse_tickets`, single-use ~60s) + session-scoped EventBus subscribe | **Live HTTP**: mint via `POST /auth/sse-ticket` (key-gated; anonymous mint → 401); own ticket opens own stream (200, `text/event-stream`); replay → **401** (single-use); A's ticket on B's stream → **401** and B's on A's → **401** (both directions); Bearer path re-asserted. **Stream independence under fire**: A's open SSE connection received **0 frames / 0 bytes** during B's real model turn (`witness` check; EventBus is per-process AND per-session) | **PASS** (12) |
| 4 | A 的会话列表不含 B | Per-tenant `SessionStore` on the per-tenant volume (D2: import-time-fixed state isolated by container) | **REST + store level**: `GET /sessions` listings disjoint (each lists its own markers, zero foreign ids); in-container: volume session dirs enumerated, foreign-id `grep -r` over the whole runtime root → **0 hits** (9 foreign ids on A, 5 on B); own sessions all present on own volume (5/5, 9/9) — `store_isolation.json` | **PASS** (10) |
| 5 | IM 双 bot 互不串话 | Production IM wiring (`MessageBus`/`ChannelManager`/`ChannelRuntime` + bridge service via the production factory) with the T8 `imlib` MockChannel pattern | **Three levels** (no real bot credentials exist — fail-closed convention of T8/T9/T10/T11): **(a) runtime** — two probes run CONCURRENTLY, one inside each tenant container against that tenant's own engine (serve is loopback-bound, unreachable cross-container by T10 design), distinct `{mockim, t12-a-chat}` / `{mockim, t12-b-chat}`; overlapping windows proven by timestamps (B's [t+1.0s, t+13.4s] inside A's [t+0, t+16.4s]); each channel saw only its own chat (sent=2, foreign=0), session map holds only its own key, IM session landed in its own store AND is listed by its own gateway via the router while absent from the other's; cross-container state greps → 0 hits. **(b) config** — A's `agent.json`/env/`tenant.env` carry zero of B's placeholders/host/key (and symmetrically); channels fail-closed (`enabled:false`, `operators:[]`). **(c) real dual-bot** — USER-GATED `deploy/real_dual_bot_smoke.py` delivered (T8 `real_platform_smoke.py` pattern extended to two tenants/bots); refusal-without-credentials demonstrated (exit 2, partial creds also refused); adapter SDKs verified present in the image (`dingtalk-stream 0.24.3`, `lark-oapi 1.7.3`) so it is runnable as-shipped once TEST-bot credentials exist | **PASS** (26) + user-gated pending (c) |
| 6 | 跨租户 opencode/MCP 进程不可互访 | Separate compose networks (per-tenant `docker-compose.yml` with distinct project `name:`), serve bound `127.0.0.1:4096` (T10), PID/mount namespaces | **In-container probes + docker topology** (`network_topology.json`): A on `vt-t12-a_default` (172.19.0.2), B on `vt-t12-b_default` (172.20.0.2), **no shared network**; 4096 has **no published port** on either; from inside A: B's container name → DNS NX, B's IP :8080 and :4096 → CONN_FAIL, B's host-published 28082 via A's default gateway → **CONN_FAIL** (colima: no host-port hairpin — even the public surface is unreachable container-to-container here; where a deployment does expose it, it is the key-gated public endpoint and item 1 proves 401); PID namespace: exactly A's own 6 processes visible, one `opencode serve` (its own), zero victim cmdlines; filesystem: zero hits for B's host/placeholder/session-ids under A's `/home/opencode` | **PASS** (9) |

**Honesty notes (item 5).** The two IM probes live in separate containers, so
cross-delivery between them is structurally impossible (no shared bus,
network, or filesystem) — the assertions document that structural isolation
empirically rather than exercising a shared in-memory bus; the shared-process
crosstalk risk is exactly what D2's full-stack-per-tenant ruling removes, and
T8's parity suite pinned the single-process MockChannel semantics. The probes
run the gateway's production IM classes as a second process inside each
container, not inside the live gateway process — doing the latter would
require modifying frozen `agent/src` code or real bot credentials. What the
probe path shares with production: same `BaseChannel._handle_message` ingress
(all 16 adapters' path), same runtime/manager/bus classes, same bridge factory
(`build_session_service` + `start_session_service`: tool map → subscribe-first
pumps → reconcile), same engine, same persisted store. Bonus observation: the
`{channel,chat_id}→session` map survived container stop→start (run 2 reused
run 1's session) — IM conversations persist across reclaim/wake cycles.

## 2. Resource measurements (REAL — 估算值不得冒充实测值)

Methodology: `docker stats --no-stream` (cgroup MemUsage) sampled every 2.5 s
by a background thread over labeled windows — **not** single snapshots;
running-state samples only (stopped containers report ~30 MiB residuals;
filtered). Raw rows: `rss_samples.csv`; per-window stats: `rss_summary.json`.
Latencies are wall-clock around real HTTP requests / real injections. Every
number below traces to a raw evidence file.

### 2.1 Per-container RSS (MiB, median [min–max], n samples)

| Window | Tenant A | Tenant B | Note |
|---|---|---|---|
| **Idle** (60 s, both up, zero traffic, pre-turn) | **1047.6** [1042.4–1061.9] n=14 | **1039.4** [1039.4–1285.1] n=14 | B's first sample still settling post-wake (1285→1039) |
| **Active — web turn via router** (B's turn; A idle control) | 1048.1 (control) | **1294.3** [1290.2–1298.4] | gateway-mediated turn = the production active shape |
| **Active — IM turn** (concurrent in-container probes) | 1365.0 [1252.4–1410.0] | 1380.4 [1294.3–1455.1] | **includes the probe's own second Python stack (~250–400 MiB)** — production IM runs inside the gateway process; treat the web-turn window as the representative active figure |
| Reclaim-cycle window (stop→boot transitions) | 1022.5 [164.0–1297.4] | 952.4 [98.0–1299.5] | lifecycle evidence, not a steady state |
| Run-1 idle (archived, different uptime state) | 1249.3 | 1047.6 | A never restarted in run 1; B freshly woken — same spread T11 saw |

### 2.2 Cold-start wake latency (container stopped → router wake → first successful proxied answer: `POST /sessions` → **201 + real session_id**)

| Sample | Tenant | Trigger | Seconds |
|---|---|---|---|
| 1 | b | policy-loop idle stop | 19.696 |
| 2 | a | policy-loop idle stop | 18.687 |
| 3 | a | phase-W stop | 18.503 |
| 4 | b | phase-W stop | 18.512 |

**Distribution (n=4): min 18.503 / median 18.599 / max 19.696 s**
(`wake_timings.json`; router trail `wake begin/ok` in `router.log`).
T11 baseline was 19.7–40.9 s (its 40.8 s outlier was a first-wake-on-volume
case); run 1 of this rig measured [30.728, 18.709, 18.501] — the tight
18.5–19.7 s band is the warm-volume steady state under Rosetta.

### 2.3 IM first-response latency (inbound mock-channel message → first outbound chunk / terminal, through the tenant's own in-container IM stack)

| Run | Tenant | first outbound (kind) | terminal |
|---|---|---|---|
| authoritative | a | **6.061 s** (streaming delta) | 14.222 s |
| authoritative | b | **2.074 s** (streaming delta) | 10.143 s |
| run 1 (archived) | a | 4.318 s (delta) | 12.412 s |
| run 1 (archived) | b | 3.046 s (delta) | 11.122 s |

First outbound is always a T9 `_stream_delta` chunk (streaming gate on, like
the provisioned telegram config), i.e. the user-visible first response, not
the terminal message. Spread (2.1–6.1 s) is model time-to-first-token under
Rosetta; the manager's coalescer merged the deltas into one chunk + one
`_stream_end` per turn (sent=2), and the `_streamed` final-dedup suppressed
the duplicate terminal message — T9 semantics observed live in-container.
Web-turn wall (POST /messages → attempt.completed via router): 13.119 s
(run 2), 13.759 s (run 1).

### 2.4 Deviation vs the plan's 0.7–1.3 GB/tenant estimate

The estimate (Oracle Phase-3 analysis, carried in plan T12/R4) compares to
measurement as follows — units stated explicitly because the answer depends
on them (1 GiB = 1024 MiB; 1 GB = 1000 MB):

| State | Measured | In 0.7–1.3 GB (decimal)? | In 0.7–1.3 GiB? |
|---|---|---|---|
| Idle | 1039–1048 MiB ≈ 1.02–1.03 GiB ≈ **1.09–1.10 GB** | **yes** (mid-upper band) | yes |
| Active, gateway-mediated turn | 1290–1298 MiB ≈ 1.27 GiB ≈ **1.36 GB** | **no — ~5% over the 1.3 ceiling** | yes (1.27 GiB) |
| Active, IM-probe shape (second stack in-container) | up to 1455 MiB ≈ 1.42 GiB ≈ **1.53 GB** | no — ~17% over | no |
| T11 single-snapshot peak (post-turn) | 1459–1480 MiB ≈ **1.53–1.55 GB** | no — ~18% over | no |

**Honest deviation statement**: the estimate holds for the IDLE tenant
(1.09–1.10 GB, inside the band under either unit reading) but is **5–18% low
for active peaks** in decimal GB: a gateway-mediated turn plateaus ~1.36 GB,
and first-turn-after-boot peaks (model context warm-up; T11's 1459–1480 MiB
snapshots) reach ~1.53 GB. Why: the estimate predated measurement of (a) the
82-tool + OmO + subagent prompt context resident in the serve process during
a turn (~+220–250 MiB over idle, consistent across T11 and both T12 runs) and
(b) the MCP subprocess working set during tool turns. Practical consequence:
the provisioned compose limit (6 GiB/tenant) carries ~4× headroom over the
observed active peak — R4's mitigation ladder (瘦身 gateway 依赖面 / 提高回收
激进度 / 大租户独立主机) is **not** triggered by these measurements; a
2-tenant-per-host budget should plan ~1.4 GB/tenant active (≈2.8 GB + host
overhead), not 1.3.

## 3. Reclaim policy verification (accelerated N)

Mechanism (T11): `POST /router-admin/reclaim` → per-tenant evaluate — engine
truth `GET /session?limit=1&roots=true` → `time.updated` via `docker exec`
(serve stays internal; the router never holds a tenant credential), proxy
ledger fallback, **fail closed** when no truth — → fenced `docker stop`
(drain budget recorded, never hidden). Policy shape verified with
**accelerated N: `VT_ROUTER_IDLE_TTL_S=15` (production default 10800 s = 3 h)
+ an operator-side scheduler hitting the admin endpoint every 4 s** — the
same call a cron/systemd timer makes in production. Evidence:
`reclaim_verdicts.json` (every pass + verdict), `midturn_truth_progression.json`.

| Step | Expected | Observed |
|---|---|---|
| R1 idle stop (unattended) | both tenants (idle since Phase M, engine truth stale) stopped by the loop | A: stop verdict `idle 19440ms >= ttl 15000ms`, truth=engine, **drained=true**, container exited; B: same (idle 25129–43590 ms) |
| R2/R3 inbound wake | a stopped tenant wakes on its first inbound request | B 19.696 s → 201 + session; A 18.687 s → 201 + session (scheduler paused during wakes — see finding 2) |
| R4 **live-turn protection** | a tenant mid-turn is KEPT by the engine truth check even though the proxy ledger is stale | turn on A resumed the scheduler at the first engine event (t+0.031 s); mid-turn verdicts: **0 stops, 1 keep citing truth_source=engine (idle_ms=1245 < 15000)**; direct engine probes during the turn show `time.updated` tracking it (truth age 398–7402 ms, n=4); turn completed (`attempt.completed`, 10.961 s) and the container stayed running throughout |
| R5 idle again → stop | after the turn lands, the same loop stops A | A exited, drained=true; classification: A keeps=3 stops=2, B keeps=3 stops=2, all stops fenced+drained |

The R4 shape is the strong form of the test: an in-container turn (like IM or
cron traffic) touches **no** router path, so the proxy ledger alone would call
the tenant idle — only the engine truth check keeps it. Run 1 additionally
showed the keep verdicts holding at idle_ms 403/5122 with truth ages
163–6645 ms.

**Findings (documented, NOT fixed — frozen surfaces, auditor posture):**

1. **`VT_ROUTER_RECLAIM_INTERVAL_S` is parsed but never consumed**
   (`router/config.py:45,70`; no in-router periodic loop exists — README's
   "interval policy is T12's" deferred it and T12 is verification-scoped).
   The verified policy above runs the mechanism from an operator-side
   scheduler; production wiring is either a cron/systemd timer on
   `router.cli reclaim` / the admin endpoint, or consuming the knob in the
   router lifespan (~10-line change, belongs to a router feature commit, not
   to this audit).
2. **Wake-vs-reclaim interaction**: a wake in flight IS an admitted router
   request (fence holds it), so a concurrent dispose drains instead of
   racing — but a wake longer than `DRAIN_TIMEOUT_S` (30 s) could be stopped
   mid-health-poll (`drained=false` is recorded, not hidden). The test
   scheduler pauses during wake samples; deployments should keep
   drain ≥ wake budget or accept the recorded-drain-false path.
3. **Run 1 archived** (`run1-debug/`): 90/91 — the single FAIL was a
   check-window bug in the harness (the mid-turn verdict filter had no lower
   time bound and counted R1's pre-turn idle stop; raw verdict timestamps
   prove both in-window verdicts were engine-truth KEEPs and the turn
   completed). Fixed (`since_t` bound); run 2 is authoritative.

## 4. Cost accounting (measured, both accountings)

8 minimal real turns total across both runs (4 per run; ≤2 per tenant per
run — within the task's cost discipline). Serve-side sweep of every engine
session (`cost.json`, assistant-message `cost`/`tokens` fields):

| Accounting | Total | Basis |
|---|---|---|
| **Real DashScope spend** | **≈ ¥0.272 ≈ $0.038** | 113,174 input + 32 output tokens (A: 50,396 in; B: 62,778 in) at T10's recorded rates ¥0.0024/1k in, ¥0.0096/1k out |
| opencode serve price DB | $0.2845 (A $0.1326, B $0.1519) | serve's internal pricing catalog — ≈7× the real qwen3.8-max rate; reported for traceability, **not** the bill |

Token shape: a cold-context gateway turn costs ~60k input (82-tool + OmO +
subagent context — T11's measured shape); prefix-cache hits drop repeats to
~1.05k input (observed: run 2's B web turn at 1,056 tokens vs run 1's 60,444
on the same prompt). Budget was < $0.20 — **met under the real-spend
accounting with ~5× margin**; under the serve price-DB accounting the
two-run cumulative figure ($0.28) exceeds it, which is why both accountings
are stated. Gateway auto-title ChatLLM calls are excluded (same exclusion
T8 documented); explicit titles were passed on every session create.

## 5. User-gated pending items

| Item | Status | How to run |
|---|---|---|
| **Real dual-bot IM smoke** (matrix item 5, real-platform level) | Script delivered + fail-closed gate demonstrated (exit 2 with clear instructions when either tenant's TEST-bot credentials are missing; partial creds also refused). Adapter SDKs verified in-image (`dingtalk-stream 0.24.3`, `lark-oapi 1.7.3`, `python-telegram-bot 22.8`). NOT executed — no bot credentials exist (T8/T9/T10/T11 convention; production credentials forbidden for smokes) | Set `VT_T12_{A,B}_PLATFORM` + per-platform TEST-bot credential envs (docstring of `deploy/real_dual_bot_smoke.py`), rig up, run it; a human DMs each bot `reply with exactly SMOKE_OK_A` / `_B`; cross-assertions + `real-dual-smoke-results.json` are automatic |
| ECS wake/reclaim backend | unchanged from T11: documented recipe, deliberately unimplemented (`router/backend.py::EcsBackend`) | user decision at deployment |
| Registry push / production hosts | untouched (task Must-NOT) | user decision |

## 6. Suite + gate status

| Gate | Result |
|---|---|
| `pytest OpencodeAgent/tests/ -q` | **169 passed, 1 skipped** = pre-existing 158 (T11's recorded 157+1 baseline — 47 T10 config-render + 110 T11 router/provisioning + 1 skip) **+ 12 new** T12 helper tests (`test_tenancy_matrix.py`, docker-free). Zero failures. NOTE: the tasking's "226 baseline" is not reproducible at HEAD `05c8058b` — the highest recorded count for this directory before T12 is T11's 158; stated honestly rather than matched silently |
| Bridge suite (`agent/tests/test_opencode_bridge*.py` + `test_session_service_lifecycle.py`) | **233 passed, 7 skipped — bit-identical to T13's recorded baseline** (233/7). The tasking's "241" ≈ the 240 collected (233+7); zero failures either way |
| Global gate `pytest --ignore=agent/tests/e2e_backtest --tb=short -q` | **12070 passed, 105 skipped, 9 failed in 19:31** — the 9 failures are a **strict subset of T7's recorded 13-failure baseline** (`t7-e2e/gate-results.json`, env-dependent: tushare-fallback / anthropic-adapter / provider-header / metrics tests); **ZERO new failures** (failure-set diff), and 4 baseline failures are now absent (fixed between T7 and HEAD). Log: `/tmp/vt-t12-rig/global-gate.log` at run time |
| black + ruff on all new python | clean (all files ≤400 lines; largest new: `e2e_state_checks.py` 364) |
| Zero-diff guarantees | `agent/src/**`, `frontend/**`, protected zones, translator/golden fixtures, T13's `mcp_server`/READMEs: **untouched** (`git status` shows only new `OpencodeAgent/deploy/*`, new `OpencodeAgent/tests/test_tenancy_matrix.py`, this report). No OpencodeAgent config fix was needed — the matrix ran green against T10/T11 as landed |

## 7. Reproduction

```bash
# 1. provision two tenants (idempotent; fresh volume ≈7 min/tenant under Rosetta)
for t in a:28081 b:28082; do id=${t%%:*}; port=${t##*:}; \
  python OpencodeAgent/deploy/provision_tenant.py --tenant $id \
    --public-host $id.t12.tenant.local --host-port $port \
    --out-dir /tmp/vt-t12-rig/tenants --container-prefix vt-t12 --volume-prefix vt-t12 \
    --base-env OpencodeAgent/.env; done
# 2. start them
for id in a b; do docker compose -f /tmp/vt-t12-rig/tenants/$id/docker-compose.yml up -d; done
# 3. run the matrix (spawns/stops its own router on 28080; ~15 min; 4 real turns)
python OpencodeAgent/deploy/e2e_tenancy_matrix.py \
  --registry /tmp/vt-t12-rig/tenants/tenant_registry.json \
  --tenants-dir /tmp/vt-t12-rig/tenants --tenants a,b \
  --out .omo/evidence/opencode-engine-bridge-v2/t12-tenancy
# 4. docker-free helper tests
pytest OpencodeAgent/tests/test_tenancy_matrix.py -q     # 12 passed
# 5. teardown
for id in a b; do docker compose -f /tmp/vt-t12-rig/tenants/$id/docker-compose.yml down -v; done
docker volume rm vt-t12-{a,b}-{home,cron-state,cron-logs}; rm -rf /tmp/vt-t12-rig
```

Knobs: `--reclaim-ttl-s` (accelerated N, default 15), `--reclaim-interval-s`
(4), `--idle-window-s` (60), `--rss-interval-s` (2.5), `--im-wait-s` (300),
`--skip-model` (HTTP-only harness validation, zero cost).

## 8. Evidence index (`.omo/evidence/opencode-engine-bridge-v2/t12-tenancy/`, untracked)

| File | Content |
|---|---|
| `matrix_results.json` | all 93 checks + 19 measurements + notes (sanitized) |
| `e2e-console.log` | full console trail of the authoritative run |
| `router.log` | router trail: route rejections, `wake begin/ok` ×4, `sse client disconnect`, registry reloads, the TTL=15 restart banner |
| `rss_samples.csv` / `rss_summary.json` | raw 2.5 s docker-stats rows (tenant/label/state-filtered) + per-window stats |
| `wake_timings.json` | the 4-sample cold-start distribution |
| `reclaim_verdicts.json` | every policy-loop pass + verdict, classification per tenant, live-turn protection detail |
| `midturn_truth_progression.json` | engine `time.updated` sampled every 3 s during the live turn (truth-age evidence) |
| `im_probe_{a,b}.json` + `.stdout/.stderr.log` | in-container IM probe results (latencies, sent records, session maps, store listings, per-turn cost) |
| `web_turn_b.json` / `web_turn_llm_usage.json` | the gateway-mediated turn's full event trail + token usage |
| `cost.json` | serve-side cost/token sweep of every engine session, both tenants |
| `network_topology.json` | per-tenant networks/IPs/published ports + the in-container host-port probe result |
| `store_isolation.json` | store-level check counts (dirs, foreign greps, own-present) |
| `registry_snapshot.json` | routing table with `token_sha256` stripped |
| `scripts/` | copies of the 10 E2E/probe/smoke scripts as run |
| `run1-debug/` | archived run 1 (90/91; the harness check-window bug + its forensic fix trail) |

Secrets: generated `API_AUTH_KEY`/`OPENCODE_SERVER_PASSWORD` are scratch
values living only in `/tmp/vt-t12-rig/tenants/*/tenant.env` (0600, removed
with the scratch dir); evidence carries key HINTS/digests at most. The
`--base-env` passthrough put the host dev `DASHSCOPE_API_KEY` into tenant env
files (same key T7/T8/T10/T11 rigs used) — outside this directory.
