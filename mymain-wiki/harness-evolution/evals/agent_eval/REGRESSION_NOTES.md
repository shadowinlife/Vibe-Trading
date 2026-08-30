# agent_eval restore — regression notes (revert 035673b0, upstream #433)

This package is the deterministic agent-eval harness from commit `b9eaa316`
("feat: add deterministic agent eval harness"), restored after the IRR-AGL
reliability/governance stack was backed out by revert `035673b0` (sibling
`310183b0` on the integration branch): "revert: back out IRR-AGL
reliability/governance stack (#405, #416)".

## (a) Why the stack was reverted — the exact #433 mechanism

Revert message (`git show 035673b0`): "the stack wrapped the tool registry in
every session path (even governance mode=off) without exposing the registry
surface AgentContext reads, breaking all session-runtime chats within a day
(#433, #431)."

Concrete mechanism, with references into the revert diff and the pre-revert
tree (`035673b0^`):

1. **Unconditional wrapping in the session chat path** —
   `agent/src/session/service.py` (pre-revert lines 224–226) imported
   `get_governance_mode`, `RuntimeContext`, and `govern_registry` inside the
   session-run builder, then (pre-revert lines 258–267) replaced the live
   registry with `registry = govern_registry(registry, surface=..., context=
   RuntimeContext(...))` for **every** session execution, regardless of
   governance mode. Removed by the revert's `@@ -221,9 +218,6 @@` and
   `@@ -255,17 +249,6 @@` hunks (plus the `governance_surface` parameter
   hunks `@@ -89,7 +89,6 @@` / `@@ -299,18 +282,6 @@`).
2. **The wrapper hid the registry surface** — `GovernedToolRegistry`
   (`agent/src/governance/runtime.py`, pre-revert lines 19–97, deleted
   entirely by the revert) only delegated a narrow method set: `tool_names`,
   `get`, `register`, `get_definitions`, `set_trace_writer`, `execute`,
   `__contains__`, `__len__` (lines 38–97). Any other attribute the session
   runtime / AgentContext read from the real `ToolRegistry` raised
   `AttributeError`, so session chats failed even with mode `off`
   (`execute` short-circuits at `if self.context.mode == "off"` — the
   wrapping itself, not the policy, was the breakage). The factory
   `govern_registry` sat at pre-revert line 100.
3. **Coupled changes in the agent loop** — `agent/src/agent/loop.py`
   (pre-revert): `PolicyDenied` import at line 40 (revert hunk
   `@@ -37,7 +37,6 @@`), `_policy_denied_payload` helper at line 446
   (`@@ -443,17 +442,6 @@`), `set_trace_writer` hook on the registry at
   lines 574–576 (`@@ -571,9 +559,6 @@`), `PolicyDenied` catches around
   `registry.execute` at lines 1328/1344 (`@@ -1323,10 +1308,7 @@`,
   `@@ -1341,8 +1323,6 @@`), and `policy_denied` trace writes at line 1418
   (`@@ -1415,8 +1395,7 @@`, `@@ -1433,19 +1412,6 @@`).

The revert also deleted the eval package itself (all of
`agent/src/evals/agent_eval/` and `agent/tests/agent_eval/`) only because its
imports referenced the removed stack — the eval harness is offline,
deterministic test infrastructure and was not part of the #433 breakage. This
restore brings it back decoupled.

## (b) What is restored from b9eaa316, and every compatibility deviation

**Restored byte-identical from `b9eaa316`** (verified by SHA-256 comparison of
`git show b9eaa316:<path>` vs worktree):

- `agent/src/evals/__init__.py`, `agent/src/evals/agent_eval/__init__.py`,
  `case_schema.py`, `golden_trace.py`, `record.py`, `scorer.py`, `stub_llm.py`
- `agent/tests/agent_eval/`: all 18 golden YAML cases under `cases/`
  (`agent_self_authorize_live_order_denied.yaml`,
  `all_sources_open_not_empty_success.yaml`,
  `ashare_t1_violation_warn_or_reject.yaml`,
  `best_trial_without_trial_count_rejected.yaml`,
  `deny_future_data_backtest.yaml`,
  `financial_data_missing_available_at_warn.yaml`,
  `hard_failures_not_hidden.yaml`, `limit_up_buy_fake_fill_rejected.yaml`,
  `local_source_fallback_denied.yaml`, `mcp_external_tool_injection_denied.yaml`,
  `no_benchmark_claim_alpha_rejected.yaml`, `no_cost_model_but_live_advice.yaml`,
  `policy_deny_must_enter_trace.yaml`, `random_control_missing_caps_scorecard.yaml`,
  `remote_api_shell_denied.yaml`, `scheduler_live_write_denied.yaml`,
  `scorecard_not_llm_overridden.yaml`, `unknown_connector_fail_closed.yaml`),
  `stubs/README.md`, and the 6 test modules (`test_case_schema.py`,
  `test_policy_cases.py`, `test_prompt_hash.py`, `test_runner.py`,
  `test_scorer.py`, `test_stub_llm.py`).

**Compatibility deviations** (all forced by the revert deleting the import
targets; each is minimal and none changes eval semantics):

1. `prompt_hash.py` — 1-line import edit:
   `src.reliability.artifacts.hashing` → `src.evals.agent_eval._hashing`
   (original module deleted by the revert).
2. `runner.py` — import block rewritten to the eval-local stand-ins below
   (`src.governance.{decisions,manifest,policy_engine}` → `_policy`,
   `src.reliability.quant.scorecard` → `_scorecard`). The `QuantIssue` import
   from the original block was dropped because the runner body never
   references it (verified by grep), and the `case_schema` import moved below
   the new imports to keep grouping sorted.
3. `_hashing.py` (new, 89 lines) — verbatim port of `sha256_json` from
   `035673b0^:agent/src/reliability/artifacts/hashing.py`, so prompt hashes
   stay bit-identical to the original harness. `CanonicalJsonError` is defined
   locally (it lived in the deleted `src.reliability.errors`); `sha256_bytes`
   and `sha256_file` were not ported (never used by the eval package). Pure
   computation: reads no config, touches no runtime path.
4. `_policy.py` (new, 349 lines) — port of `ToolSurface` / `RiskLevel` /
   `ToolManifest` (from `035673b0^:agent/src/governance/manifest.py`),
   `RuntimeContext` / `PolicyDecision` (from `.../decisions.py`), and
   `PolicyRule` / `PolicyEngine` with all 9 builtin rules P10, P20, P30, P35,
   P40, P50, P100, P900, P999 (from `.../policy_engine.py`). One documented
   simplification: `build_param_audit` (secret redaction + params hash) was
   part of the reverted reliability stack and is not ported — `params_hash` /
   `params_preview` stay `None`, which the runner never reads. This module is
   offline eval infrastructure: it never wraps a live tool registry and never
   gates a real tool execution, so #433/#420 do not apply.
5. `_scorecard.py` (new, 372 lines) — port of the `build_scorecard` gate from
   `035673b0^:agent/src/reliability/quant/scorecard.py`. The result model is
   slimmed to exactly the fields the runner reads (`scorecard_id`, `score`,
   `score_breakdown`, `conclusion_cap`, `warnings`, `hard_failures`); the
   analytics sections (crowding, regime IC, walk-forward, capacity, ...) were
   artifact-rendering concerns the runner never consumes, and the
   `reliability_enabled` runtime-config gate was dropped so the eval stays
   deterministic and config-independent.

Behavioral fidelity of the ports is proven by the restored golden suite: all
18 YAML policy/scorecard cases plus the unit tests pass unchanged
(`pytest tests/ -k agent_eval` → 39 passed, 5 pre-existing unrelated skips).

Note on style tooling: `ruff check` (E/F/W, line-length 120) passes on every
file in `agent/src/evals/` and `agent/tests/agent_eval/`. The six files this
restore materially changed (`_hashing.py`, `_policy.py`, `_scorecard.py`,
`prompt_hash.py`, `runner.py`, `tests/agent_eval/test_regression_guard_433.py`)
are formatted with `black` (26.1.0). The remaining restored `b9eaa316` files
were left byte-identical on purpose; they predate the current black style
generation and fail `black --check`, but reformatting them was deliberately
skipped to keep the restore auditable as a byte-exact revert-undo.

## (c) The #433 regression guard

`agent/tests/agent_eval/test_regression_guard_433.py` pins the scenario with
four checks:

1. `test_agent_eval_has_no_imports_from_reverted_runtime_stacks` — AST-scans
   every `*.py` under `agent/src/evals/` and fails on any `src.governance` /
   `src.reliability` import (re-coupling to the reverted runtime stacks).
2. `test_session_chat_path_is_not_registry_wrapped` — fails if
   `govern_registry`, `GovernedToolRegistry`, or `src.governance.runtime`
   reappear in `agent/src/session/service.py`: the exact #433 wrapping.
3. `test_wrap_detector_flags_the_reverted_433_snippet` — self-test proving the
   detector flags the verbatim lines removed by `035673b0` from
   `session/service.py`.
4. `test_clean_room_import_pulls_no_reverted_stack` — imports the eval package
   in a fresh subprocess and asserts neither reverted stack lands in
   `sys.modules`.

Red→green proof is recorded in
`.omo/evidence/task-1-harness-evolution-fail.txt`: a scratch re-coupling file
drove check 1 to FAILED (1 failed, 3 passed), removal restored 4 passed; a
poisoned copy of `session/service.py` with the reverted lines re-added is
flagged by the detector; and the guard passed 3 consecutive runs.
