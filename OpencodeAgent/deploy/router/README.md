# Tenant router + provisioning (plan T11 / D2 / D9-F6)

Thin reverse proxy in front of the T10 per-tenant containers, plus the script
that provisions a tenant into existence. **Routing is the whole job**: the
router resolves `token → tenant → upstream` (or `Host → tenant`) and forwards.
Every authorization decision stays in the tenant gateway, which re-validates
the same Bearer key against its own `API_AUTH_KEY`.

```
                       ┌──────────────────────────── router (this package) ─────────────────────────┐
 client ──Host: a.example──►  resolve(token|Host) ─► fence.admit ─► forward (Host preserved) ───────┼──► tenant A container (T10)
        Authorization: Bearer …        │                    │                  │                    │      gateway :8080 ── serve :4096 (internal)
                                       │                    │                  └─ SSE relay (no buffer)│      home volume /home/opencode
                                 404/401/403            410 disposed                                 │
                                       │                    │                                        │
                                 registry hot-reload   wake-on-inbound ──► docker start ──► /health ──┼──► tenant B container
                                                       (timeout ⇒ fallback page 503 + Retry-After)    │
                                                       idle reclaim ──► ASK the engine ──► docker stop┘
```

## Layout

| Module | Role | File lines | Code lines |
|---|---|---|---|
| `router/config.py` | the single env boundary (`RouterSettings.from_env`) | 94 | 58 |
| `router/registry.py` | tenant table, resolution union, atomic write | 239 | 138 |
| `router/headers.py` | header hygiene, Host preservation, SSE headers, synthetic error frame | 135 | 89 |
| `router/proxy.py` | the forwarding hop + SSE relay + wake retry | 229 | 166 |
| `router/app.py` | ASGI wiring, resolution, registry hot-reload, admin surface | 335 | 272 |
| `router/fence.py` | per-tenant admission fence (openwork directory-fence) | 156 | 92 |
| `router/wake.py` | wake-on-inbound + the timeout fallback page | 151 | 101 |
| `router/reclaim.py` | idle reclaim with the engine truth check (opencode-router) | 240 | 178 |
| `router/backend.py` | container control: docker CLI (local), ECS (documented) | 158 | 86 |
| `router/cli.py` | `serve` / `show` / `reclaim [--dry-run]` | 140 | 113 |
| `router/spec.py` + `router/templates.py` + `router/provision.py` | provisioning: spec, rendered artifacts, orchestration | 91 / 206 / 217 | 64 / 161 / 156 |
| `deploy/provision_tenant.py` | the operator CLI (thin launcher) | 132 | 103 |
| `deploy/e2e_*.py` (7 modules) | the two-tenant E2E rig, check groups, evidence bundle | 91–367 | 71–290 |

*Code lines* = statements only (docstrings, comments and blanks excluded).

**On the plan's "≤300 行" budget** (measured, not estimated): the forwarding
core — `proxy.py` + `headers.py` — is **255 code lines**, inside the budget;
adding tenant resolution (`registry.py`'s union + `resolve` + host
normalization, ~60 of its 138) brings the routing-and-forwarding path to
**~315**. The same T11 plan row also mandates four lifecycle recipes (SSE
passthrough, wake-on-inbound with a timeout page, the idle-reclaim truth check,
the tenant-delete fence): those are `wake.py` + `reclaim.py` + `fence.py` +
`backend.py` = **457 code lines**, kept in their own modules precisely so the
proxy core stays thin and each recipe is reviewable alone. Wiring
(`config.py` + `app.py` + `cli.py`, including the admin surface and registry
hot-reload) is 443 and provisioning 484. No file exceeds the repo's 400-line
guardrail (largest: `e2e_rig.py` 367, `app.py` 335).

## Routing rules

Resolution order (pinned by `OpencodeAgent/tests/test_router_registry.py`):

| # | Condition | Outcome | HTTP |
|---|---|---|---|
| 1 | Bearer token is in the registry | that tenant | forwarded |
| 1a | …and a *registered* Host names a **different** tenant | `TenantMismatch` | **403** (never guess) |
| 2 | token absent **or unknown**, Host registered | the Host's tenant | forwarded (the gateway returns the 401) |
| 3 | token presented but unknown, no routable Host | `UnknownToken` | **401** |
| 4 | nothing resolves | `UnknownHost` | **404** (never misrouted) |

Rule 2 is what keeps business logic out of the router: it does not judge
credentials, it routes. A wrong key on a registered Host reaches the tenant
gateway and comes back as *the gateway's* 401.

The registry stores **`token_sha256`, never the plaintext key** — the client's
`Authorization` header is forwarded verbatim, so a registry leak is not a
credential leak. The plaintext exists only in the tenant's `0600 tenant.env`.

## Host preservation (plan D9 / F6, Oracle 二轮 B2)

The public `Host` header is forwarded **unchanged**. Each tenant gateway sets
`API_ALLOWED_HOSTS=<public host>` (`env_schema.py:313`, consumed by
`security.py:_get_extra_loopback_hosts` → `_reject_untrusted_loopback_host`).
Rewriting Host to the upstream would make every forwarded request a 403.

> ⚠️ The env var is `API_ALLOWED_HOSTS`. `EXTRA_LOOPBACK_HOSTS` is an internal
> monkeypatch registration key, **not** an env var — writing it instead means
> every proxied request 403s.

Because the router's peer IP is loopback/docker-gateway, the gateway would
treat it as a local (zero-auth) client — which is exactly why `API_AUTH_KEY`
is fail-closed in the T10 entrypoint (no key ⇒ the container refuses to boot).

Hop-by-hop headers are stripped both ways (RFC 9110), inbound
`X-Forwarded-*` is replaced with the real peer, and `accept-encoding` is forced
to `identity` so the passthrough is byte-identical on the streamed and the
buffered path.

## SSE passthrough (portal `events.ts` recipe)

| Portal behaviour | Router implementation |
|---|---|
| `text/event-stream` + `X-Accel-Buffering: no` | `_sse_response_headers` sets both, plus `Cache-Control: no-cache, no-transform`, and drops `content-length` |
| no buffering | `upstream.aiter_raw()` relayed straight into `StreamingResponse`; the router adds no middleware that could buffer |
| retry owned by the proxy (`sseMaxRetryAttempts: 0`) | an established stream is **never** silently reconnected (that would duplicate events); the only retry is the connection-establishment retry after a wake |
| `cancel()` → `abort.abort()` | client disconnect ⇒ uvicorn cancels/closes the generator ⇒ the `finally` closes the upstream response, tearing the upstream connection down |
| synthetic error event instead of silent death | an upstream failure mid-stream yields one `event: router.error` frame (`{type, tenant, reason, detail}`) before the stream ends |

The admission (fence) is released by the stream's `finally`, not by the
handler — an SSE response outlives its handler. Cleanup is `asyncio.shield`ed
so a cancelled generator still releases the fence and closes the upstream.

## Wake-on-inbound

An upstream that produces **no response at all** triggers the wake path. The
trigger is any `httpx.TransportError`, not just a connection refusal: Docker's
published-port proxy accepts-then-resets for a while after `docker stop`, which
surfaces as `ReadError`/`RemoteProtocolError` with an empty message (this was
found by the E2E, not by reasoning — see `E2E_REPORT.md`).

1. `inspect_state(container)`;
2. `start(container)` unless it already runs;
3. poll the gateway's unauthenticated `/health` — T10 made it answer only after
   the lifespan preflight (bridge → serve tool-map + reconcile) succeeds, so
   green means the whole stack is wired;
4. replay the **original request, body included** (the body is buffered for
   exactly this);
5. on timeout or an unusable backend: **503 + `Retry-After` + a fallback page**
   naming the tenant, never a bare 502 and never a silent hang.

Measured on this rig (warm volume, amd64 under Rosetta): **19.7–40.9 s** from
`docker stop` to a `201` with a real `session_id`.

**Production (ECS) wake path — documented, not implemented.** The plan forbids
ECS API calls against real infrastructure, and an untested wake path is worse
than a loud one: `EcsBackend.__init__` raises with the recipe in its docstring
(`DescribeTasks`/`UpdateService desiredCount=1` + ALB target-group health, or a
Lambda wake authorizer; `ExecuteCommand`/service-connect for the truth check).
Set `VT_ROUTER_BACKEND=docker-cli` locally.

## Idle reclaim: ask the engine, fail closed

Proxy-side activity records are **not sufficient** — WS/SSE traffic does not
hit the access-log path, so a tenant streaming a long turn looks idle to the
router. Before reclaiming, the router asks the engine
(`GET /session?limit=1&roots=true`, read `time.updated`), exactly as
opencode-router does. Locally that runs through `docker exec` + the
container's own `OPENCODE_SERVER_PASSWORD`, so serve's 4096 stays internal and
the router never holds a tenant credential.

* engine truth available ⇒ newest of (engine, proxy ledger) decides idleness;
* engine silent **and** no proxy traffic ⇒ **keep** (openwork's fail-closed
  posture — never reclaim on a guess);
* container not running ⇒ nothing to reclaim;
* the stop runs **through the fence**, so reclaim cannot race an in-flight
  request; a drain that exceeds its budget is recorded (`drained: false`), not
  hidden;
* after a reclaim the disposal mark is cleared — a reclaimed tenant is stopped,
  not deleted, so its next inbound request is admitted and wakes it.

The **interval policy** (idle N hours) is T12's; the router ships the truth
check plus a one-shot admin/CLI pass (`POST /router-admin/reclaim`,
`python -m router.cli reclaim [--dry-run]`) and `VT_ROUTER_RECLAIM_INTERVAL_S`
defaulting to **0 = off**.

## Tenant-delete fence (openwork directory-fence)

`TenantFence` serializes disposal against prompt admission per tenant:
`enter()` refuses once disposal is requested (`TenantDisposed` ⇒ HTTP 410),
`dispose()` marks the tenant closing, waits for in-flight requests to drain
(bounded), runs the action, then marks it disposed. Admission is an object
rather than a context manager because an SSE stream outlives its handler.

## Provisioning

```bash
python OpencodeAgent/deploy/provision_tenant.py \
    --tenant acme --public-host acme.example.com --host-port 28081 \
    --out-dir /srv/vt-tenants --container-prefix vt-tenant --volume-prefix vt-tenant \
    --image opencode-serve:v3.0.0-tenant \
    --base-env OpencodeAgent/.env      # shared model/data-source creds only
# add --print-key once to hand the tenant its API_AUTH_KEY
docker compose -f /srv/vt-tenants/acme/docker-compose.yml up -d
```

Produces, per tenant:

| Artifact | Content |
|---|---|
| `<tenant>/tenant.env` (`0600`) | generated `API_AUTH_KEY` + `OPENCODE_SERVER_PASSWORD`; `API_ALLOWED_HOSTS=<public host>,localhost`; `VIBE_TRADING_SSE_TIMEOUT` (frontend watchdog input, `settings_routes.py:397`); `LANGCHAIN_*` (D11: auto-title + swarm worker stay on the Python provider stack); `VIBE_TRADING_CHANNELS_AUTO_START=false`; allowlisted operator-shared credentials |
| `<tenant>/agent.json` | the `channels` section with credential **PLACEHOLDERS** and every adapter `enabled: false`, `operators: []` (fail closed) |
| `<tenant>/docker-compose.yml` | one T10 container: `host_port:8080`, serve stays internal on 4096, B5 volume layout, external named volumes, T10's healthcheck |
| named volumes | `<prefix>-<tenant>-{home,cron-state,cron-logs}`, home seeded with the B5 skeleton + `agent.json`, `chown opencode:opencode` |
| `tenant_registry.json` (`0600`) | the routing entry (`token_sha256`, host, upstream, container, volume) |
| `fleet.yml` | `include:` of every registered tenant's compose file |

`opencode.json` is **not** rendered here: the existing `config/opencode.json.tmpl`
+ `render_config.py` pipeline owns it (`entrypoint.sh` renders at container
start, compiling the tool-governance manifest into permission denies).
Provisioning only supplies the environment that pipeline reads, so the
governance surface is never forked.

**Idempotency rules** (pinned by `test_provision_tenant.py`): the API key and
serve password are reused from the existing `tenant.env` (`--rotate-key` to
force a new pair); generated files are byte-stable; `agent.json` is written
**once** so operator-filled bot credentials survive a re-run (the same
create-if-absent rule applies inside the volume); volumes are create-if-absent.

## Registry format

```json
{
  "version": 1,
  "tenants": {
    "acme": {
      "tenant_id": "acme",
      "public_host": "acme.example.com",
      "upstream": "http://127.0.0.1:28081",
      "container": "vt-tenant-acme",
      "volume": "vt-tenant-acme-home",
      "token_sha256": "<64 hex>",
      "token_hint": "03af",
      "host_port": 28081,
      "image": "opencode-serve:v3.0.0-tenant",
      "created_at": "…", "updated_at": "…"
    }
  }
}
```

`tenants` is the single source of truth: the token and Host indexes are derived
at load time, so they cannot drift. Writes are atomic (temp file + replace) and
the router **hot-reloads on mtime change**, so provisioning a new tenant needs
no restart (proven by the E2E). A failed reload keeps the last good table.

## Configuration (`VT_ROUTER_*`)

| Variable | Default | Meaning |
|---|---|---|
| `REGISTRY` | `tenant_registry.json` | routing table path |
| `HOST` / `PORT` | `0.0.0.0` / `28080` | listen address |
| `CONNECT_TIMEOUT_S` | `5` | upstream connect budget (short, so the wake path fires fast) |
| `READ_TIMEOUT_S` | `0` (= infinite) | SSE streams and long turns have no bounded response time; liveness comes from disconnect-cancel |
| `WRITE_TIMEOUT_S` | `60` | request-body write budget |
| `WAKE_TIMEOUT_S` | `300` | health-poll budget before the fallback page |
| `WAKE_POLL_INTERVAL_S` | `1` | health poll cadence |
| `DRAIN_TIMEOUT_S` | `30` | fence drain budget during disposal/reclaim |
| `IDLE_TTL_S` | `10800` | reclaim threshold (policy owner: T12) |
| `RECLAIM_INTERVAL_S` | `0` (off) | periodic reclaim loop interval |
| `ADMIN_TOKEN_SHA256` | `""` (admin surface **disabled**) | sha256 of the admin Bearer token |
| `BACKEND` | `docker-cli` | `docker-cli` \| `ecs` (documented, unimplemented) |
| `DOCKER_BIN` / `SERVE_URL` / `HEALTH_PATH` | `docker` / `http://127.0.0.1:4096` / `/health` | backend + probe targets |
| `LOG_LEVEL` | `INFO` | the router's own logger level |

Run it:

```bash
cd OpencodeAgent/deploy
VT_ROUTER_REGISTRY=/srv/vt-tenants/tenant_registry.json python -m router.cli serve --port 28080
python -m router.cli show                 # routing table (no secrets)
python -m router.cli reclaim --dry-run    # truth-check verdicts, stops nothing
```

Containerized (`Dockerfile.router` + `docker-compose.router.yml`): the router
needs the docker CLI and `/var/run/docker.sock` for the wake backend, and the
tenant upstreams must be container names on a shared network
(`provision_tenant.py --upstream http://vt-tenant-acme:8080`).

## Testing

```bash
pytest OpencodeAgent/tests/ -q     # 157 passed, 1 skipped — docker-free
```

| File | Covers |
|---|---|
| `test_router_registry.py` | resolution precedence, Host normalization, load failures, atomic write, the disk-backed table (hot reload / missing file) |
| `test_router_proxy.py` | routing outcomes, Host preservation, header hygiene, body/query forwarding, buffered responses, admission release, the wake trigger classes |
| `test_router_streaming.py` | SSE headers, byte-for-byte relay, the synthetic error frame |
| `test_router_fence.py` | admission counting, dispose-vs-in-flight serialization, drain timeout, refusal after disposal |
| `test_router_reclaim.py` | the engine truth check (documented + wrapped + empty + unparsable payloads), fail-closed, fenced stop, multi-tenant pass |
| `test_router_wake.py` | cold start + body replay, unhealthy-but-running, timeout/missing-container/unusable-backend fallback pages, the admin surface |
| `test_provision_tenant.py` | secrets, env seeds, passthrough allowlist, placeholders, compose shape, registry entry, idempotency |
| `test_router_e2e.py` | docker-free app construction + `cli show`; the compose E2E gated on `VT_T11_E2E=1` |
| `test_config_render.py` | T10's 47 config-pipeline tests — untouched and still green |

Unit tests drive the real ASGI app through `httpx.ASGITransport` with the
upstream side on `httpx.MockTransport` — no docker, no network. Because
`ASGITransport` buffers, the *unbuffered* first-byte behaviour is asserted by
the compose E2E instead:

```bash
python OpencodeAgent/deploy/e2e_multi_tenant.py \
    --registry /tmp/vt-t11-rig/tenants/tenant_registry.json \
    --tenants-dir /tmp/vt-t11-rig/tenants --tenants a,b \
    --base-env OpencodeAgent/.env --measure-resources \
    --out .omo/evidence/opencode-engine-bridge-v2/t11-router
```

59 checks across provisioning idempotency, the routing matrix, SSE headers +
spread, fence admission/release, one real model turn, the live engine truth
check, registry hot reload, cold-start wake, and the timeout fallback page.
`--tenants` is parameterized and the recorder emits measurements, so T12's
isolation matrix extends this rig instead of redoing it.

## Handoff to T12

* reuse `deploy/e2e_rig.py` (`Rig`, `RouterProcess`, `Recorder`, `docker_rss_mb`)
  and add matrix groups as new `check_*` functions — the driver runs them in order;
* `--measure-resources` already samples idle / before-wake / after-wake RSS
  (measured here: ~1.0–1.4 GiB per container, consistent with the plan's
  0.7–1.3 GB/tenant estimate);
* `POST /router-admin/state` exposes per-tenant `inflight` / `disposed` /
  `activity_ms` for isolation and leak assertions;
* the reclaim policy knob is `VT_ROUTER_IDLE_TTL_S` + `RECLAIM_INTERVAL_S`;
  `reclaim --dry-run` gives verdicts without stopping anything.

## Not built here (deliberately)

* **permission relay** — the plan defers it; if it is ever added, every reply
  must first verify session → tenant-directory ownership (CodeNomad
  `ownsDirectory`), or a cross-tenant permission answer becomes possible.
* **shared-bot ingress routing** — excluded by the plan's Must-NOT.
* **business logic / auth verdicts** — the tenant gateway owns them.
