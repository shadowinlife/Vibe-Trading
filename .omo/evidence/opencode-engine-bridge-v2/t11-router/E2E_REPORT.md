# T11 — Tenant Router + Provisioning: two-tenant E2E Report

> Plan `opencode-engine-bridge-v2` T11 (Wave 4). Host: arm64 macOS, colima/Docker
> with **Rosetta** amd64 emulation. Tenant image: T10's `opencode-serve:v3.0.0-tenant`
> (`baf5aab39002`, vendored commit `02731637`) — **not rebuilt** (T13 is editing
> `agent/mcp_server.py` in parallel; a rebuild would vendor a moving tree).
>
> **Rig isolation**: router host port **28080**, tenant A **28081**, tenant B
> **28082**; scratch `/tmp/vt-t11-rig/`; every container named `vt-t11-*`
> (`vt-t11-a`, `vt-t11-b`, plus the throwaway `vt-t11-d` / `vt-t11-router-*`,
> all removed). T13's 14098/18082 and `/tmp/vt-t13-rig` were never touched; the
> only other containers on the host (`litellm-*`) were left alone. No new host
> port was bound beyond the three assigned (the fallback-page tenant points at
> dead port 28099 as an *outbound* target only).

## Final artifacts

| Artifact | Value |
|---|---|
| Router package | `OpencodeAgent/deploy/router/` — 12 modules, largest file 340 lines (repo guardrail ≤400) |
| Proxy core (routing + header hygiene + forwarding) | `registry.py` + `headers.py` + `proxy.py` ≈ **280 code lines** (statements only) — inside the plan's ≤300 budget |
| Mandated lifecycle recipes | `wake.py` / `reclaim.py` / `fence.py` / `backend.py` ≈ 330 code lines (separate modules, see README §Layout for the accounting) |
| Provisioning | `deploy/provision_tenant.py` (CLI) → `router/provision.py` + `router/templates.py` + `router/spec.py` |
| Router image | `vt-tenant-router:1.0.0` = `f30132174ee6`, 246 MB (python:3.12-slim + pinned static docker CLI 27.3.1 + httpx/starlette/uvicorn) |
| E2E driver | `deploy/e2e_multi_tenant.py` (+ `e2e_rig` / `e2e_checks` / `e2e_turn_checks` / `e2e_wake_checks` / `e2e_provisioning` / `e2e_evidence`) |
| Unit tests | 110 new, docker-free: registry 29, proxy 24, streaming 4, fence 5, reclaim 16, wake 13, provisioning 17, + 2 E2E-wrapper tests |
| Tenants provisioned | `a` (28081, `a.t11.tenant.local`, `vt-t11-a`, volume `vt-t11-a-home`), `b` (28082, `b.t11.tenant.local`, `vt-t11-b`, `vt-t11-b-home`) |

## ACCEPTANCE (plan T11): 开通两租户后 registry/路由/唤醒全链路脚本通过 — **MET**

`python OpencodeAgent/deploy/e2e_multi_tenant.py … --measure-resources` →
**59/59 checks passed, exit 0** (`e2e_results.json`, `router.log`,
`e2e-console.log`). The authoritative run is the one against the FINAL code
(after the `ReclaimContext` / `wake_tenant(settings)` signature refactors and
the test-file splits); an earlier identical 59/59 run validated the pre-refactor
code, and the two `--skip-model` runs (51/51) validated the harness in between.

QA scenarios from the plan row:

* **happy** — 新租户开通 → 首条入站消息冷启动唤醒 → 回复: **PASS**. Provisioning
  is re-run live inside the E2E (idempotent, key reused), tenant B is then
  `docker stop`ped, and **one inbound POST through the router wakes it and is
  answered by the gateway**: `cold_start_wake_s = 40.791` → `HTTP 201` with a
  real `session_id`, container `running`, router log `wake begin`/`wake ok`.
  Three runs of the same scenario measured **40.791 / 19.659 / 19.8 s** (warm
  volume, Rosetta) — the spread is the gateway's post-start preflight, not the
  router; the fallback budget (`WAKE_TIMEOUT_S`, default 300) covers it.
* **failure** — 未注册 Host → 404 而非误路由: **PASS**. `404 {"detail":"unknown
  tenant host"}`, no `x-vt-tenant` header, and zero requests reached either
  tenant gateway (asserted in the unit suite; the E2E asserts the observable
  404 + absent tenant header).

## E2E results by group (59 checks)

| Group | Checks | Result | Highlights |
|---|---|---|---|
| `provision` | 3 | PASS | live CLI re-run of tenant B: exit 0, `reused_key=true`, registry still resolves the reused key |
| `routing` | 11 | PASS | healthz tenant count; unregistered Host → 404 (not misrouted); each tenant's own token+Host → 200 with `x-vt-tenant`; **token alone routes** (Host unregistered); A's token + B's Host → **403**; **no token + registered Host → forwarded, gateway answers 401** (router routes, gateway authorizes); wrong key → gateway 401 |
| `sse` | 8 | PASS | headers on an **idle** stream in **0.009 s** (a buffering proxy would hold them); `text/event-stream`; `x-accel-buffering: no`; `cache-control: no-cache, no-transform`; **no content-length**; during a real turn 7 events arrived **spread over 11.17 s** (first event at 0.029 s) — not one terminal burst |
| `fence` | 4 | PASS | an open stream holds exactly **1 admission**; proxy activity recorded; client disconnect releases it; the router logs `sse client disconnect tenant=a` (cancel propagated, not a silent close) |
| `model` | 5 | PASS | one minimal real turn through the router: `POST /sessions` 201 → `POST /messages` 200 → `text_delta` + `reasoning_delta` + `llm_usage` + `attempt.created/started/completed` relayed, **no synthetic `router.error`**, transcript persisted (`user`+`assistant`) |
| `reclaim` | 5 | PASS | live `docker exec` probe `GET /session?limit=1&roots=true` answered with the documented list shape (1 root session); the router parsed `time.updated = 1789264643677`; admin reclaim resolved a truth for every tenant (`a: engine`, `b: proxy`); with a long idle TTL **nothing was reclaimed** (fails safe). Raw payload: `engine_session_probe_a.json` |
| `hot-reload` | 5 | PASS | a ghost tenant provisioned **while the router ran** went 404 → routable (503 fallback, `wake unavailable tenant=c` logged) **without a restart** → 404 again after de-registration |
| `wake` | 7 | PASS | `docker stop` → exited → one inbound request → started by the router → gateway answered 201 → container running → stayed healthy |
| `fallback` | 8 | PASS | a container that **starts but never serves** (`vt-t11-d`, `sleep 600`) → router waited the configured 6 s budget → **503 + `Retry-After: 30` + HTML page naming the tenant + `Cache-Control: no-store`**, `wake timed out` logged |

## Measurements (T12 baseline)

| Measurement | Value | Note |
|---|---|---|
| `cold_start_wake_s` (stopped → 201) | **40.791 s** | warm volume, amd64 under Rosetta; earlier runs 19.659 s / 19.8 s |
| `wake_timeout_s` (fallback path) | 6.009 s | equals the configured budget (6 s) |
| `sse_time_to_headers_s` (idle stream) | **0.008 s** | unbuffered proof #1 |
| `sse_first_event_s` (real turn) | 0.030 s | |
| `sse_event_spread_s` (real turn) | **14.593 s** over 7 events | unbuffered proof #2 |
| `model_turn_wall_s` | 14.664 s | |
| `rss_mb[idle]` | A **1237.0 MiB**, B **1242.1 MiB** | both freshly bootstrapped, no turn yet; consistent with the plan's 0.7–1.3 GB/tenant estimate |
| `rss_mb[before_wake]` | A **1459.2**, B 1238.0 | A after its model turn (+222 MiB) |
| `rss_mb[after_wake]` | A 1480.7, B **1027.1** | B freshly woken (lower than its idle sample: restarted process tree) |
| Fresh-volume cold boot (provisioning → healthy) | **~7 min** per tenant | OmO plugin auto-install under Rosetta (T10 documented the same); both tenants bootstrapped in parallel |

## Containerized router (production shape) — validated separately

`containerized-router-smoke.log`: the router image booted with the registry
copied in, attached to the tenant's compose network, upstream
`http://vt-t11-a:8080` (container name):

* `GET /router-healthz` → `{"status":"ok","tenants":2}`
* `GET /sessions` with tenant A's key + `Host: a.t11.tenant.local` → **200**,
  `x-vt-tenant: a`, the gateway's own `x-content-type-options: nosniff` passed through
* unregistered Host → **404** `unknown tenant host`
* SSE → `text/event-stream` + `x-accel-buffering: no` + `cache-control:
  no-cache, no-transform`, no content-length; on client close the container
  logged `sse client disconnect tenant=a`

Two operational traps found and documented (README + DEPLOY-GUIDE §T11.2):

1. With the router **in a container**, `127.0.0.1:<host-port>` upstreams point at
   the router's own loopback → connect failure → a pointless wake attempt
   (observed: `wake begin tenant=a` for an already-running container). Upstreams
   must be container names on a shared network (`--upstream`).
2. On this host (colima) **`/tmp` is not shared with the VM**: a host-directory
   mount comes up empty and a single-file mount materializes as a directory
   (`IsADirectoryError`). Hence the registry is mounted as a **directory** that
   is a VM-shared path — and provisioning seeds `agent.json` into the volume
   over **stdin** instead of a file mount.

## Bugs found and fixed while proving the chain

| # | Symptom | Root cause | Fix | Caught by |
|---|---|---|---|---|
| 1 | POST/PUT/DELETE through the router → **405** | Starlette 1.0 defaults a *function* endpoint to `methods=["GET"]` when `methods=None` | explicit `PROXY_METHODS` tuple on the catch-all route | unit test `test_every_method_is_proxied` |
| 2 | Cold-start wake **never fired**: instant 502 with an empty error string | right after `docker stop`, Docker's published-port proxy **accepts then resets**, surfacing as `ReadError`/`RemoteProtocolError` — not `ConnectError`, which was the only trigger | widen the wake trigger to any `httpx.TransportError` (the backend still decides whether a start is needed); log/return the exception **type** (an empty message is useless for ops) | E2E `wake/*` (5 failures) → regression unit test `test_a_reset_connection_still_triggers_the_wake` |
| 3 | Router INFO log lines (`wake begin/ok`, `sse client disconnect`) missing from the log | uvicorn configures only its own loggers; `vt.router` had no handler, so INFO was dropped (Python's last-resort handler starts at WARNING) | `router.cli serve` installs a root handler at `VT_ROUTER_LOG_LEVEL`; the E2E starts the router through the CLI (which also exercises `cli.py`) and sets `PYTHONUNBUFFERED=1` | E2E log assertions |
| 4 | Re-provisioning **clobbered** an operator-edited `agent.json` | the host-side render was unconditional (only the volume copy was create-if-absent) | `agent.json` is written **once**; the volume seed keeps the same rule | unit test `test_rerun_is_byte_identical_and_preserves_operator_edits` |
| 5 | Volume seeding failed: `cp: omitting directory '/seed/agent.json'` | single-file bind mount under an unshared `/tmp` (see trap 2 above) | seed over **stdin** (`docker run -i … cat > …`), no host mount | provisioning run |

## Cost accounting

**3 real model turns total** (one per full E2E run: two on the pre-refactor
code, one on the final code; the two `--skip-model` validation runs spent none,
and the manual repro only created sessions). Per-turn shape on this image is
T10's measured ~64.2k input + ~36 output tokens (input dominated by the 82-tool
+ OmO + subagent context), so ≈193k input + ≈108 output ⇒ **≈ $0.13** at the
rates T10 recorded (¥0.0024/1k in, ¥0.0096/1k out). Budget was < $0.20. Every
other assertion is HTTP-level (no LLM).

## IM / real-bot smoke — USER-GATED (unchanged posture)

No bot credentials exist, so the T8/T9/T10 fail-closed convention stands. What
is delivered instead:

* the **runnable pattern**: the E2E's inbound wake is an HTTP webhook-style
  `POST` through the router (`POST /sessions`, and the same path works for
  `POST /channels/...`), which is exactly the shape a channel webhook needs;
* provisioning renders the `channels` section with **placeholders** and
  `enabled: false` + `VIBE_TRADING_CHANNELS_AUTO_START=false`, so a tenant
  flips one file and one env var to go live;
* to smoke a real bot: fill `<tenant>/agent.json` (never re-rendered over),
  set `VIBE_TRADING_CHANNELS_AUTO_START=true`, restart the tenant container,
  then reuse T8's `agent/tests/e2e_engine_bridge/real_platform_smoke.py`
  against the router's public host instead of the gateway port.

## Honest gaps / not built

* The 59-check suite runs against a **host-run** router (same code path); the
  container shape is validated by build + boot + forward + SSE + disconnect,
  not by the full suite.
* **ECS wake path: documented, not implemented** (`EcsBackend` raises with the
  recipe in its docstring) — the plan forbids ECS API calls against real
  infrastructure, and an untested wake path is worse than a loud one.
* **No permission relay** (the plan defers it). If added, every reply must first
  verify session → tenant-directory ownership (CodeNomad `ownsDirectory`).
* The reclaim **interval policy** is T12's; `RECLAIM_INTERVAL_S` defaults to 0.
* `GET /session?limit=1&roots=true` was verified against the pinned
  **opencode 1.18.30** only (payload in `engine_session_probe_a.json`); a pin
  change should re-check the shape (the parser fails closed on drift).
* The router buffers **request bodies** so the post-wake retry can replay them.
  Bounded in practice by the gateway's own upload limits; a multi-GB upload
  would need a spool-to-disk variant (documented in `proxy.py`).

## T12 handoff

* reuse `deploy/e2e_rig.py` (`Rig`, `RouterProcess`, `Recorder`, `docker_rss_mb`,
  `docker_state`) and add matrix groups as new `check_*` functions — the driver
  runs them in order and `--tenants` is parameterized;
* RSS sampling hooks already exist (`--measure-resources`: idle / before-wake /
  after-wake) with the first numbers above;
* `POST /router-admin/state` exposes per-tenant `inflight` / `disposed` /
  `activity_ms` (admin token required) for isolation and leak assertions;
* reclaim policy knobs: `VT_ROUTER_IDLE_TTL_S`, `VT_ROUTER_RECLAIM_INTERVAL_S`,
  `python -m router.cli reclaim --dry-run` for verdicts without stopping;
* the rig was **torn down** after this run (`compose down` + `docker volume rm`
  of the six `vt-t11-*` volumes, scratch `/tmp/vt-t11-rig` removed, ports
  28080/28081/28082 released). T12 rebuilds it with the two commands in
  §Reproduction — provisioning is idempotent and re-creates+re-seeds the
  volumes, but a **fresh volume costs ~7 min per tenant** under Rosetta (OmO
  plugin install), and the tenants bootstrap in parallel. The router image
  `vt-tenant-router:1.0.0` (`f30132174ee6`) was left in place, as was T10's
  `opencode-serve:v3.0.0-tenant`.

## Reproduction

```bash
# 1. provision two tenants on the T10 image (creates + seeds the volumes)
for t in a:28081 b:28082; do id=${t%%:*}; port=${t##*:}; \
  python OpencodeAgent/deploy/provision_tenant.py --tenant $id \
    --public-host $id.t11.tenant.local --host-port $port \
    --out-dir /tmp/vt-t11-rig/tenants --container-prefix vt-t11 --volume-prefix vt-t11 \
    --base-env OpencodeAgent/.env; done
# 2. start them (fresh volume: ~7 min under Rosetta for the OmO install)
for id in a b; do docker compose -f /tmp/vt-t11-rig/tenants/$id/docker-compose.yml up -d; done
# 3. run the 59-check E2E (spawns/stops its own router on 28080)
python OpencodeAgent/deploy/e2e_multi_tenant.py \
  --registry /tmp/vt-t11-rig/tenants/tenant_registry.json \
  --tenants-dir /tmp/vt-t11-rig/tenants --tenants a,b \
  --base-env OpencodeAgent/.env --measure-resources \
  --out .omo/evidence/opencode-engine-bridge-v2/t11-router
# 4. docker-free unit suite
pytest OpencodeAgent/tests/ -q          # 157 passed, 1 skipped (47 T10 config-render + 110 T11)
```

## Evidence index

| File | Content |
|---|---|
| `e2e_results.json` | all 59 checks + 12 measurements + notes (sanitized) |
| `e2e-console.log` | the full console trail of the final run |
| `router-image-build.log` | the `vt-tenant-router:1.0.0` image build |
| `router.log` | the router's own trail: route rejections, `wake begin/ok`, `wake timed out`, `wake unavailable`, `sse client disconnect`, registry reloads |
| `registry_snapshot.json` | the two-tenant table with `token_sha256` **stripped** (hints only) |
| `wake_timings.json` | the wake/SSE measurement subset |
| `engine_session_probe_a.json` | the raw `GET /session?limit=1&roots=true` payload from the pinned serve (truth-check shape proof) |
| `containerized-router-smoke.log` | the production-shape validation above |
| `E2E_REPORT.md` | this file |

Secrets: the generated `API_AUTH_KEY` / `OPENCODE_SERVER_PASSWORD` are scratch
values that live only in `/tmp/vt-t11-rig/tenants/*/tenant.env` (mode `0600`)
and were **not** copied into this directory; the registry snapshot drops the
digests as well. `--base-env` passed the host dev `DASHSCOPE_API_KEY` into the
tenant env files (same key T7/T8/T10 rigs used) — also outside this directory.
