# T10 — Pin Conversion Record (B1 / D10) + Build Provenance

> Plan `opencode-engine-bridge-v2` T10. This edit = ENFORCEMENT of the harness
> freeze (Oracle二轮 B1: the `@latest` specs were fictitious pins), NOT a violation.
> Flagged prominently for USER VETO per the orchestrator recommendation.

## Pin conversions (all 4 freeze-compat loci, T2 baseline_memo §3)

| Locus | File:line | WAS (fictitious) | NOW (pinned) | Rationale |
|---|---|---|---|---|
| opencode CLI (app) | `Dockerfile` (npm install) | `opencode-ai@latest` | **`opencode-ai@1.18.30`** | 1.18.30 is the version ALL bridge validation ran on: T1 golden traces, T7 web E2E (67 checks), T8 IM parity (s2 8.03s engine-death), T8-1 liveness fix. |
| opencode CLI (base) | `Dockerfile.base:29` | `opencode-ai@latest` | **`opencode-ai@1.18.30`** | Same pin; app layer re-installs on top of the kept local base's baked 1.18.18. |
| OmO plugin (tmpl) | `config/opencode.json.tmpl` (plugin[]) | `oh-my-openagent@latest` | **`oh-my-openagent@4.19.4`** | T2 §1.3: de-facto production since 2026-08-01; 5.0.0-beta.53 shipping every 1-2 days = live major-flip risk. Pinning IS the point. |
| OmO plugin (entrypoint fallback) | `entrypoint.sh` (minimal-fallback block) | `oh-my-openagent@latest` | **`oh-my-openagent@4.19.4`** | SECOND @latest site (T2 freeze-compat row 4) — pinning only the tmpl would leave the fallback live. |

### ⚠️ USER VETO FLAG — opencode CLI pin = 1.18.30 (orchestrator recommendation)

1.18.30 chosen because it is the version every bridge validation artifact was
recorded on. The alternatives would require the drift-alarm replay FIRST:
- **1.18.18** (in-image de-facto, all 4 legacy images): T1 golden traces were
  NOT recorded on it; spike §8 marks `permission.asked` payload + new event-type
  presence as MED-sensitive on 1.18.18 → would need `record_traces.py` replay + diff.
- **1.18.23** (ECS host-direct production): same — not the trace-recording version.

If the user vetoes 1.18.30 → run the drift-alarm replay (below) against the
chosen target before shipping.

## Base image pin (B1 / freeze-compat row 7 / T2 §1.1 split-brain)

`opencode-serve-base:latest` is TWO different images (local `ea738ee663d1` WITH
the `useradd -m` home-ownership fix vs registry `437144c60370` WITHOUT it).

- **Kept the LOCAL base** `ea738ee663d1` (contents suffice: Ubuntu 22.04 + py3.12
  + node20 + playwright + pip pkgs + useradd -m opencode + mkdir /workspace).
  supervisord, opencode-ai@1.18.30, and the home state-dir pre-creation are all
  added in the APP layer (no 3GB base rebuild — disk/time awareness per task).
- Tagged it `opencode-serve-base:v3.0.0-tenant` (versioned, unambiguous) and the
  app Dockerfile `FROM opencode-serve-base:v3.0.0-tenant` (default ARG, overridable).
- **Pinned digest (image config sha256)**:
  `sha256:ea738ee663d189224d7f5e3025cabb944bf219a67918f55a5e2bc6653ae2fef7`
- A literal `FROM …@sha256:<manifest-digest>` requires a registry push, which is
  user-gated (NOT done — task forbids registry push). The versioned tag + recorded
  config digest is the local-immutable equivalent and removes the `:latest` split-brain.
- NOTE: the kept local base has opencode-ai **1.18.18** baked (from its build-time
  `@latest`); the app layer's `npm install -g opencode-ai@1.18.30` overrides it
  (verified: `opencode --version` in the final image = **1.18.30**).

## Drift-alarm replay procedure (MANDATORY before ANY future pin change — spike §8 / D10)

```bash
# 1. Start a serve at the pin TARGET (same rig as T1), then re-record the corpus:
python agent/tests/fixtures/opencode_bridge/record_traces.py     # 8 golden traces
# 2. Diff vocabulary/shapes vs the committed 1.18.30 traces. Focus (spike §8 MED):
#    - permission.asked payload fields (patterns/always/tool.callID)
#    - new event-type presence (todo.updated, file.edited, catalog.updated, plugin.added)
#    - message.part.delta existence + single shape {sessionID,messageID,partID,field,delta}
python agent/tests/fixtures/opencode_bridge/analyze_traces.py
# 3. Bridge golden suite must stay green (translator is allowlist-based → unknown
#    event types are harmless, but SHAPE drift fails the golden assertions):
pytest agent/tests/opencode_bridge -q
```
OmO pin changes: same replay (the 6.4s continuation re-prompt gap is OmO-version
sensitive, spike §8 — HIGH for OmO).

## Build provenance (recorded in image OCI labels)

| Field | Value |
|---|---|
| Image | `opencode-serve:v3.0.0-tenant` |
| **FINAL image ID** | `sha256:baf5aab39002…` (build #3, with the gateway-start `--max-time` cold-boot fix) |
| Arch / OS | amd64 / linux |
| Size | 4.7 GB |
| `dev.vibe-trading.source-branch` | `mymain-engine-bridge` |
| `dev.vibe-trading.source-commit` | `027316377377eb2dc8cfb62f3f42086bdbe0f7c4` |
| `dev.vibe-trading.opencode-pin` | `opencode-ai@1.18.30` (verified `opencode --version` in-image) |
| `dev.vibe-trading.omo-pin` | `oh-my-openagent@4.19.4` |
| Build wall time | #1 ~12 min, #2 ~7 min, #3 ~9 min (Rosetta-emulated npm/pip; base pre-built, app layer only) |

### Build history (iterative hardening)

1. **#1 `0fbba3957ab2`** — full E2E passed (web chat 9/9 ×2, persistence, heal, channels,
   SPA, auth, B2, no-key). Found: cold-boot race (gateway-start waited on serve `/health`
   which answers before the MCP layer/OmO install → one failed-preflight gateway restart,
   self-healed per the documented restart-until-reachable posture).
2. **#2 `01530ef80436`** — gateway-start switched to wait on serve `/mcp` (the actual
   preflight dependency). Found: the unbounded curl stuck on one stale hung `/mcp` request
   during the slow Rosetta fresh-volume bootstrap (serve `/mcp` blocks until OmO installs).
3. **#3 `baf5aab39002` (FINAL)** — added `--max-time 10` to the gateway-start `/mcp` probe
   so it bounded-retries through the slow bootstrap. Clean cold boot (0 "Application startup
   failed", no manual restart) + full E2E re-passed.

### Vendored-snapshot currency (T14 parallel-agent rule)

The build's `git archive` ran at ~22:32Z and recorded source-commit `02731637`.
T14 landed `02731637` ("goal session binding via gateway context injection",
verification-only — `_build_prompt_injection` UNCHANGED) at 22:16Z, BEFORE the
archive. So the vendored snapshot **already includes T14** (recorded commit ==
worktree HEAD == `02731637`). No rebuild needed. Re-check `git log` before the
final commit; rebuild if another commit lands.

## In-image verification (Rosetta-emulated, measured)

| Check | Result |
|---|---|
| `opencode --version` | **1.18.30** ✓ (Rosetta runs the amd64 binary — NOT QEMU SIGILL) |
| `supervisord --version` | 4.3.0 ✓ |
| frontend dist | `/opt/vibe-trading/frontend/dist/index.html` PRESENT + 111 asset files ✓ |
| bridge module | `/opt/vibe-trading/agent/src/opencode_bridge/` PRESENT (service.py etc.) ✓ |
| tmpl OmO pin | `oh-my-openagent@4.19.4` ✓ |
| supervisord.conf / gateway-start.sh | PRESENT / executable ✓ |
| pre-created home state dirs | 3/3 (.vibe-trading, .local/share/opencode, .opencode) ✓ |
| **MCP tool count ON** (`VT_MEMORY_MCP_TOOLS=1`) | **82** ✓ (freeze baseline) |
| **MCP tool count OFF** | **77** ✓ (freeze baseline) |

## Local runtime reality (QEMU/Rosetta chain, evaluated IN ORDER per task)

1. **Rosetta TEST FIRST**: `docker run --rm --platform linux/amd64 --entrypoint
   opencode opencode-serve:v2.1.1-mymain --version` → printed **1.18.18** (exit 0).
   Rosetta emulation WORKS on this arm64 Mac (Docker Desktop Rosetta, not QEMU).
   → Path (1) taken: **full compose E2E on amd64**. No arm64 variant needed;
   the production artifact stays amd64. (The T2 memo's "opencode SIGILLs under
   QEMU" caveat does not apply — Rosetta is enabled and runs the binary.)
