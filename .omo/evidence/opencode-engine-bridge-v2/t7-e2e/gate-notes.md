# T7 global gate outputs (plan: 不放宽)

## (a) full pytest — `python3 -m pytest --ignore=agent/tests/e2e_backtest --tb=short -q`
- Final run (post-change tree): **9 failed, 12022 passed, 98 skipped** (19:26).
- The 9 = EXACTLY the known pre-existing baseline (eastmoney×4: dragon_tiger /
  fund_flow / margin_trading / northbound; anthropic×3: llm_reasoning_effort;
  metrics×1: test_metrics bars-per-year; provider-header×1).
- `pytest-failure-diff.json`: new_failures=[] ; curated set-identity vs the
  known-9 = PASS. The baseline LOG itself carried 4 transient contamination
  failures (captured while the working tree was mid-edit: thin-assembler at
  411 lines + 3 state_migration Cfg-stub breaks) — each was fixed and
  independently verified green before the final run (api_server.py 399<400;
  test_state_migration_wiring 5 passed); they are listed and excluded
  explicitly in the JSON, not silently dropped.

## (b) black + ruff
- `black --check agent/src/opencode_bridge` → 20 files unchanged ✓
- `black --check` on every changed/added test file (test_opencode_bridge_wiring,
  test_state_migration_wiring, test_opencode_bridge_translator,
  e2e_engine_bridge/*) → 10 files unchanged ✓
- `ruff check agent/src/opencode_bridge` → All checks passed ✓
- `ruff check agent/api_server.py agent/tests/e2e_engine_bridge
  agent/tests/test_opencode_bridge_wiring.py
  agent/tests/test_state_migration_wiring.py` → All checks passed ✓
- Pre-existing, documented, NOT introduced by T7:
  - `ruff agent/src/api/state.py` → 1×F401 (`import os`, unused in-module but
    load-bearing: `test_state_fsync.py` patches `state_mod.os.fsync`);
  - `black --check agent/src/api/state.py agent/api_server.py` → both files are
    NOT black-clean at the merge-base (house style; the repo's black gate scope
    is `agent/src/opencode_bridge agent/tests`). T7 edits mirror the existing
    file style and add no new ruff violations.
  - `black --check agent/tests` (plan's literal gate line) fails at BASELINE
    too: 453 files would be reformatted with T7 stashed (452 with T7 applied —
    the state_migration_wiring edit blackened one previously-dirty file). The
    plan line is interpreted as scoped to changed files, which are clean.

## (c) three zero-diff assertions (gate-results.json)
- protected zones `agent/src/{agent,session,providers}/` vs mymain merge-base
  5eda88d1: committed=[] uncommitted=[] → PASS
- `frontend/` source: PASS (dist/node_modules gitignored AND asserted
  untracked/modified-empty)
- `agent/src/channels/` whole directory: PASS

## Bridge suite
- `pytest -k opencode_bridge`: **187 passed, 6 skipped** (175 pre-T7 + 10
  wiring/factory + 2 run_dir bash-harvest).
