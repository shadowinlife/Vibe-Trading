# e2b-sandbox-ctl

Standalone CLI wrapping the **e2b + kruise-agents** SDK for ACS Agent Sandbox
lifecycle operations. This package is the **D7 shell-out carrier**: the tenant
router must stay zero-new-dependency, so *all* SDK dependencies live here and
the future `E2bSandboxBackend` (plan T16) talks to sandboxes by shelling out
to `e2b-sandbox-ctl` — exactly like `DockerCliBackend` shells out to `docker`.

Per gate decision **D11** the lifecycle shape is **(a) runner, pause-only**:
`stop` = `beta_pause` (filesystem checkpoint, `keep_memory=False`),
`start` = `connect` (implicit resume), auto-pause is the idle policy.

## Install

```bash
python -m venv .venv && .venv/bin/pip install -e .
# requires the gateway port-forward to be up (default http://localhost:8888)
e2b-sandbox-ctl state <sandbox-id>
```

## Configuration (env only — nothing hardcoded)

| Variable | Default | Meaning |
|---|---|---|
| `E2B_API_KEY` | — | admin API key (takes precedence over file) |
| `E2B_API_KEY_FILE` | `~/.acs-sandbox/admin-api-key` | key file, used when env unset |
| `E2B_DOMAIN` | `localhost:8888` | kruise gateway host:port (port-forward) |
| `OPENCODE_SERVER_PASSWORD` | — | serve basic-auth password (takes precedence over file) |
| `OPENCODE_SERVER_PASSWORD_FILE` | `~/.acs-sandbox/poc-server-password` | password file |

The basic-auth *user* is the literal `opencode` (pool template, T8/T9) — it is
not a secret; only passwords/keys stay out of the code.

## Commands

All commands print **exactly one strict-JSON document to stdout**;
diagnostics go to stderr. On failure stdout carries
`{"ok": false, "error": {"code", "message"}}`.

| Command | Output (success) | Maps to (T16) |
|---|---|---|
| `state <sid>` | `{"state":"running\|paused\|absent","raw":{...}}` | `inspect_state` |
| `start <sid> [--ttl 1800]` | `{"started":true,"connect_s":…,"route_ready_s":…,"route_attempts":…,"relaunch_fired":false}` | `start` |
| `stop <sid>` | `{"paused":true,"already_paused":false,"pause_api_s":…}` | `stop` |
| `engine-get <sid> <path>` | `{"status":200,"body":"…","password_path":"sandbox-env\|envs-param"}` | `engine_get` |
| `create --template T [--timeout 1800] [--request-timeout 320]` | `{"sandbox_id":"…","claim_s":…}` | provisioning |
| `kill <sid>` | `{"killed":true}` | reclaim |
| `route-wait <sid> [--budget 180]` | `{"ready":true,"route_ready_s":…,"attempts":…}` | wake helper |

`engine-get` on a *paused* sandbox returns `{"status":null,"body":null,"unavailable":"paused"}`
with exit 0 (mirrors `DockerCliBackend.engine_get` answering `None` for a
stopped container — the truth check never resumes a sandbox as a side effect).

## Exit codes (docker-CLI-aligned, discriminable)

| Code | Name | Meaning |
|---|---|---|
| 0 | ok | success (incl. `state` answering `absent`, `engine-get` on paused) |
| 2 | usage | argparse: bad flags / missing args / invalid path |
| 3 | backend unavailable | SDK/network/gateway dead — **T16 raises `BackendUnavailable` on this** |
| 4 | sandbox absent | control plane has no such sandbox |
| 5 | operation failed | route budget exhausted, pause refused, in-pod command error |
| 6 | auth failure | API key missing/rejected, serve password rejected (401 ends route polling early) |

## Design notes

### One-step `start` (T11 verdict)

`.omo/evidence/task-11-pause-resume.json` proved pause/resume is an **in-place
checkpoint**: the pod object, PID1 `opencode serve`, and the gateway route all
survive — `connect` → poll route → ready completes in ~0.5–1.0 s. A serve
*relaunch* branch is therefore expected-unreachable; it exists purely as a
defensive guard (fires only if the envd PID1 probe reads a cmdline without
`opencode serve`). The route poll keeps the full **180 s budget** because
fresh-claim route propagation is a gateway property (65–98 s band, T8/T9), not
a wake property — any pod-recreation miss re-enters that band.

### `engine-get` dual password path (T12 finding)

The PoC image has **no python3/curl inside** (T12), so the in-pod GET runs a
`node -e` fetch against loopback `127.0.0.1:4096` through the SDK envd channel
(T9: envd is instant and independent of the HTTP route). T12 also observed
that the template/pod env is **not visible to the envd command shell**, so the
CLI probes visibility first:

1. `sandbox-env` — `process.env.OPENCODE_SERVER_PASSWORD` visible in-sandbox →
   node uses the pod's own secretRef value; nothing crosses the CLI boundary.
2. `envs-param` — invisible → the CLI reads the local password file and passes
   it via `commands.run(envs={...})` (T3-E1: create-time envs are proven
   visible to command processes).

The chosen path is reported in the output as `password_path`. The password is
never logged, never in argv (it rides the envd JSON body), and never on disk.

### Router integration contract (T16)

```
ContainerBackend          → e2b-sandbox-ctl
inspect_state()           → state <sid>            (None ⇔ {"state":"absent"}, exit 0)
start()                   → start <sid>            (exit 3 ⇒ BackendUnavailable)
stop()                    → stop <sid>
engine_get()              → engine-get <sid> <path> (None ⇔ status null / exit !=0)
```

Timeouts: T16's shell-out should use a per-command budget ≥ the operation's
own budget (`start`/`route-wait` up to 180 s + connect). Never run without a
timeout (STATE.md lesson 7). The CLI always bounds its own waits.

## Tests

```bash
pip install -e .[dev] && pytest -q
```

Unit tests mock the SDK — no live calls. `tests/test_bootstrap_order.py`
pins the patch-before-e2b import discipline (AST ordering + runtime spy).
