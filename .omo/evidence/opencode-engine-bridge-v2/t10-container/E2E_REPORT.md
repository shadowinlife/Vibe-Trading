# T10 — Single-Tenant Container compose E2E Report

> Plan `opencode-engine-bridge-v2` T10. Host: arm64 macOS, Docker Desktop with
> **Rosetta** amd64 emulation (verified: `opencode --version` runs, NOT QEMU SIGILL).
> Rig isolation: T10 used host port **24096** (gateway) + scratch `/tmp/vt-t10-build/`;
> T14's 14096/18080 were never touched. No processes/containers I didn't create were killed.

## Final artifact

| Field | Value |
|---|---|
| Image | `opencode-serve:v3.0.0-tenant` |
| **Final image ID** | `sha256:baf5aab39002…` (3rd build, with the gateway-start `--max-time` fix) |
| Arch / size | amd64 / 4.7 GB |
| Vendored source commit | `027316377377eb2dc8cfb62f3f42086bdbe0f7c4` (mymain-engine-bridge HEAD; includes T14) |
| Pins | opencode-ai@**1.18.30**, oh-my-openagent@**4.19.4** (verified in-image) |
| MCP tool count | **82 ON / 77 OFF** (freeze baseline match) |

Build history: build #1 `0fbba3957ab2` (full E2E passed; cold-boot race found) →
build #2 `01530ef80436` (`/mcp`-wait, found the no-`--max-time` stale-hang) →
**build #3 `baf5aab39002` (FINAL, `--max-time` fix; clean cold boot + full E2E).**

## ACCEPTANCE (plan T10): 单租户 compose 起来后 Web 聊天+IM 往返全通；无 key 启动被拒 — **MET**

## E2E results (FINAL image baf5aab39002)

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | **No-key boot rejected** (fail-closed D9/B2) | **PASS** — entrypoint exits 1, "Fail-closed: gateway refuses to boot without API_AUTH_KEY" | e2e-nokey-boot.log, e2e-final-checks.log |
| 2 | **Clean cold boot** (gateway-start `/mcp` bounded-retry wait) | **PASS** — gateway healthy ~35s (cached vol) / ~25s (re-boot), **0 "Application startup failed"**, no manual restart | e2e-final-boot.log |
| 3 | **Web chat round-trip** (gateway→bridge→serve→**vibe-trading MCP**→model→reply) | **PASS 9/9** — 24.7s; full vt SSE vocabulary (text_delta×5, reasoning_delta, tool_call/result, llm_usage, attempt.created/started/completed); `list_skills` MCP call returned 91 skills; attempt.completed run-card-capable (summary+run_dir+provider+model+elapsed_ms) | e2e-final-web-chat.log, e2e-web-chat-sse.json |
| 4 | **serve crash → supervisord restart → bridge heals** (T8-1 fix + composition) | **PASS** — killed serve pid 144 → supervisord restarted (pid 236) ~6s → serve /mcp ready ~3s → **gateway pid UNCHANGED (25→25, no gateway restart)** → post-crash new turn **9/9 PASS** (bridge reconnected via _ensure_pumps) | e2e-final-crash-heal.log |
| 5 | **Settings persistence across down&&up** (B5) | **PASS** — PUT /settings/data-sources tushare_token → landed in volume `/home/opencode/.vibe-trading/.env` (ENV_PATH) → `down` (no -v) && `up` → raw value SURVIVED + API `configured: True`; seeded provider config also persisted | e2e-final-persistence.log |
| 6 | **channels-status** (IM container-level check) | **PASS** — HTTP 200, 17 channels, **15 adapters available** (dingtalk/discord/email/feishu/mochat/msteams/… — channels extras installed & importable), **0 configured** (no bot creds = fail-closed posture) | e2e-final-checks.log |
| 7 | **SPA from container** (frontend dist, T2 §4.2 gap fixed) | **PASS** — `<div id="root">` present, `/assets/index-CWmPULMY.js` → HTTP 200, title "Vibe-Trading …" (curl-level; playwright venv unavailable, curl-level acceptable per task) | e2e-spa-channels.log, e2e-final-checks.log |
| 8 | **Auth** (D9) | **PASS** — /sessions with key → 200, no key → 401 | e2e-final-checks.log |
| 9 | **B2 API_ALLOWED_HOSTS** (real env var, loopback Host trust) | **PASS** — loopback peer + trusted Host `t10.tenant.local` (in API_ALLOWED_HOSTS) → 200; untrusted Host `evil.example` → **403** (`_reject_untrusted_loopback_host`) | e2e-final-checks.log, e2e-channels-auth.log |
| 10 | **B6 MCP env parity** | **PASS** — entrypoint asserts rendered MCP env `HOME=/home/opencode` + `VIBE_TRADING_HOME=/home/opencode/.vibe-trading` identical to gateway ("MCP env parity OK"); `external_directory` ALLOW covers uploads (object form, allow-last) | boot logs, test_config_render.py |

## IM 往返 (acceptance) — how it is satisfied

No production bot credentials exist (T8/T9 established this; fail-closed rule stands).
Per the task, IM round-trip is satisfied by:
1. **Mock-channel parity ALREADY proven at T8** on **IDENTICAL bridge code** — verified:
   `git diff 90a4378a 02731637 -- agent/src/opencode_bridge/ agent/src/channels/` is
   **EMPTY** (T14 was verification-only, `_build_prompt_injection` unchanged). So T8's
   s0/s1/s3/s4 PASS + s2 8.03s engine-death carry directly to the container's bridge.
2. **Container-level channels-status check** (#6 above): 15 adapters available, runtime
   lazy-inits and reports status through the containerized gateway.
3. **Real-bot smoke = USER-GATED**: reuse T8's `agent/tests/e2e_engine_bridge/real_platform_smoke.py`
   against the container once the user provides test bot credentials (documented, not run here).

## Cost accounting (container LLM turns — kept minimal)

4 real model turns total (build #1: web-chat + heal; build #3 FINAL: web-chat + post-crash).
Per turn ~64.2k input + ~36 output tokens (input dominated by the 82-tool + OmO + subagent
context). Total ~257k input + ~144 output. Estimated **~$0.086** at qwen-max DashScope rates
(¥0.0024/1k in, ¥0.0096/1k out); MaaS qwen3.8-max pricing may differ but stays **< $0.30**.
Source: `llm_usage` SSE events (e2e-web-chat-sse.json).

## Known degradation (documented, NOT fixed — see KNOWN_DEGRADATION_nano-search.md)

`search mcp` (nano-search-mcp, 12 auxiliary CN-finance search tools) reports `failed`:
it imports the **mcp v1** API (`mcp.server.fastmcp`, removed in mcp 2.x) while the newer
vendored VT pulls **mcp 2.2.0** (via `fastmcp>=2.14`). The CORE `vibe-trading` MCP (82 tools)
is `connected` and the full chain works. Fix = a separate nano-search→fastmcp-4.x migration
(10 files + `streamable_http_path` constructor change, verified rejected by fastmcp 4.0.3).
Does NOT affect T10 acceptance.

## Cold-boot reality under Rosetta (documented)

On a FRESH volume, serve lazily bootstraps on first request and `/mcp` blocks until every
MCP server is up — including the OmO plugin auto-install (~6-10 min under cross-arch Rosetta;
482M cache). The gateway-start `--max-time 10` bounded-retry wait rides this out and proceeds
the moment `/mcp` answers 200 (clean boot, no gateway restart). With OmO cached in the persisted
volume, re-boot is ~25-35s. On native amd64 (production ECS) the OmO install is ~30s, so cold
boot is fast. This is why the `--max-time` bound matters: an unbounded curl sticks on one stale
hung `/mcp` request forever (observed on build #2).

## Stack disposition

Per task default: **compose DOWN** after E2E (see e2e-teardown.log). The named volume
`opencodeagent_vt-tenant-home` is removed with `down -v` to leave no scratch state.
