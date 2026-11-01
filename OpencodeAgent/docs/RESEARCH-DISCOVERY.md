# Research discovery: opt-in exploration

This harness experiment separates **finding the research goal** from **filling
tool parameters**. It is local to the OpenCode deployment branch and does not
replace the VT agent or introduce another financial research ledger.

## Try it

Invoke `research-discovery`, or explicitly ask “先探索需求 / 先选研究路线”.
The default mode is progressive; ask for deep exploration to investigate more
goal-related tradeoffs. Either mode can stop, skip, pivot or return to the
legacy route. No fixed three-question or one-round budget applies in this mode.

For “最近涨很多，还能买吗”, distinguish technical risk, testing a buy rule, and
multi-view thesis review. If the reply is “满仓 NVDA，担心财报”, pivot to event
risk; do not continue whole-market stock selection. The user chooses what to
learn, not a list of tool names. Unknown cost basis and holdings stay unknown.

The experimental skill takes precedence over conflicting v1 defaults only
while explicitly active. Existing report delivery remains Run Detail for
backtests and Markdown for research. It does not restore automatic HTML export,
the retired specialist-agent layer or the engine bridge.

## Capability mapping

[Routes](../skills/research-discovery/routes.md) and
[recipes](../skills/research-discovery/recipes.json) map research questions to
existing tools/skills. Strategy catalogue rows are not recommendations: inspect
the actual evidence and freshness before testing. Swarm is a research route,
not a replacement for backtest evidence. Technical chart structure and A-share
market-wide escape-top signals are distinct; the latter is not cross-market.

Directory presence, permissions, data availability and evidence adequacy are
separate checks. `screen_market` has no crypto universe. A crypto research
request must first constrain the instruments and use actual available data.
Investment horizon, historical sample and warm-up are independent fields.
Dates reflect each actual source, not a blanket T-1.
The brief uses `a/us/crypto`, or `cross` for allocation comparisons only.
Data-dependent routes require a known market before `ready`; strategy catalogue
inspection may leave it unknown. A screener's universe must match that scope.

## Validate a v2 brief

From the repository root:

```bash
conda run -n legonanobot python OpencodeAgent/scripts/capture_discovery_tools.py \
  --out /tmp/discovery-tools.json
conda run -n legonanobot python OpencodeAgent/brief_validation.py \
  /tmp/research-brief.json --snapshot /tmp/discovery-tools.json
```

The capture script uses the local VT server through the MCP client, calls only
`tools/list`, expands the existing deny manifest, and records the exact
`inputSchema` of recipe tools. It does not invoke finance tools or save
credentials/results. A locally captured schema does not prove a deployed
image's permissions or data availability; capture that environment separately.

The v2 validator checks route/dependency legality, unknown/default provenance,
revision references, current schemas/denies, and separate backtest config
declarations. Unsupported schema validation keywords fail explicitly rather
than being silently ignored. The v1 fixtures remain unchanged.
Cancellation clears the executable plan and its approval reference. Structural
provenance checks do not verify that a referenced user/tool event really exists.

`backtest.args` contains only `run_dir`. The configuration goes into a separate
`files` entry, not a pseudo `config` argument. Validation checks the declared
plan, **not whether files were written or strategy code is correct**. The VT
runner owns executable configuration/code validation and actual evidence.

The image copies the validator next to the global skills directory. Use its
same CLI with an operator-captured snapshot; no runtime tool hook calls it
automatically. An `approval.message_id` in a brief is an **untrusted reference**,
not proof that the user confirmed it.

## Offline evaluation and hook probe

```bash
conda run -n legonanobot python -m pytest \
  OpencodeAgent/tests/test_research_discovery.py \
  OpencodeAgent/tests/test_intake_brief.py \
  OpencodeAgent/tests/test_config_render.py -q
```

[Independent multi-turn evaluation](../tests/evals/research_discovery/README.md)
requires actual target-matching artifacts and observed source evidence. Legacy
intake's tool-call proxy is not reused as an efficacy metric. Synthetic tests
do not demonstrate useful model interviews.

The optional test-only canary uses an **existing** image and a deterministic
loopback model stub. It publishes no ports, uses no real keys and cannot reach
external services:

```bash
docker run --rm --platform linux/amd64 --network none \
  --entrypoint /opt/venv/bin/python \
  --mount "type=bind,src=$(pwd)/OpencodeAgent/tests/evals/research_discovery,dst=/probe,readonly" \
  "$IMAGE" /probe/canary.py
```

Set `IMAGE` to a locally available compatible image; the canary will not fetch
dependencies. It checks explicit before-hook rejection with zero fake MCP
side effects, and trusted OpenCode session binding overriding model-invented
arguments in two distinct sessions. Only `probe_*` fixture tools are
intercepted; the canary plugin is not packaged into production.

### Verification recorded for this tranche

- `conda run -n legonanobot python -m pytest OpencodeAgent/tests -q`:
  **242 passed, 1 skipped**. Black `--check`, Ruff and the indexed
  `tools/ci_private_files_gate.py` check passed.
- Read-only `code-review`: no significant issues found.
- OpenCode 1.19.3 loopback canary: zero blocked-tool side effects, visible
  rejection, and two trusted session injections in distinct sessions.
- An offline local overlay of `opencode-serve:v3.16.3-capability-map-20261031`
  replaced AGENTS, skills and the validator. With `STARTUP_BUDGET_S=300`,
  `REQUIRE_IMAGE=1`, `OFFLINE=1`, `scripts/check_startup_readiness.sh` passed:
  first authenticated `/config` 200 at **26.7s**, MCP connected at **32.0s**,
  strategy-run `/health` 200 verified at **38.9s**.
- The packaged CLI accepted a synthetic catalogue brief against **37 schemas**
  captured from the candidate's actual **99 tools**; the host's default capture
  had 94 tools. Neither capture invoked financial tools.

The overlay is **not a full Dockerfile rebuild or a release image**. These
checks do not establish interview quality, trustworthy approval, restart/
compaction recovery or OMO coexistence with the test hook. No real model replay,
deployment or production configuration change was performed.

## Admission and rollback

There is **no enabled hard gate** in this tranche. Before integrating one:

1. Verify native question/text confirmations against real user events and
   brief hashes/revisions; a model-written confirmation is never sufficient.
2. Reuse VT's goal store and supply the actual OpenCode session ID. Test
   concurrent sessions, stale revisions, cancellation and idempotent recovery.
3. Verify restart/compaction and OMO hook coexistence. Replaying a run must not
   double-start expensive tasks or overwrite another conversation's goal.
4. Run a separately authorized paired model evaluation and startup readiness
   checks on a built candidate image. No conclusion from underpowered trials.

Shell access is not a security sandbox; a named-tool hook cannot prevent all
equivalent shell/code paths. Failures must stay visible. Headless new/open tasks
wait for an approved plan rather than guessing an expensive workflow.

Rollback the experiment by not activating `research-discovery`; no goal data,
historical evidence, default config, broker permissions or model settings are
changed. Do not ship a runtime gate before all admission checks pass.
