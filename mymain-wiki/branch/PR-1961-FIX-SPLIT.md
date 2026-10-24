# PR #1961 — Fix & Split Memory (local)

Status: **EXECUTING** (2026-10-23)
Full task board and session log: `.omo/plans/pr-1961-fix-and-split.md`

## Identity

- PR: https://github.com/HKUDS/Vibe-Trading/pull/1961
- Reviewed head: `62bed6f3`
- PR branch (fork `shadowinlife`): `feat/paper-trading-v13`
- Local fix branch: `fix/pr1961-review-fixes`
- Current fix head: `69a030e9` (T1–T6)
- Review worktree: `/Users/mgong/LegoNanoBot/Vibe-Trading-pt-v13-upstream`

## Adopted decisions

1. **G1**: revert the hk/us HIL1 zero-calendar relaxation entirely; restore v1.1 `PAPER_DATA_NOT_READY`.
2. **G2**: split the unwired persistence layer to a **stacked follow-up**.
3. **G3**: keep the 09:30 opening-print drop; document the 4/5-day nuance.
4. **G4**: gate zero-volume / off-grid (1m-as-5m) / all-NaN 5m payloads.
5. **G5**: fix the 5m pure-valuation readiness gap now (blocker).

## Split contract

Main PR #1961 keeps only the **stateless** 5m step, readiness/payload fixes, test split, docs.

Follow-up branch `paper-trade-v12-persistence` (local, snapshot at `9fec2ac5`; do NOT open against `main` until #1961 merges) carries:

- `agent/src/tools/paper_trade/account.py`
- `engine_adapter.py`
- `store.py`
- `storage_io.py`
- `runner.py`
- `recovery.py`
- `trading_days.py`
- `agent/tests/test_paper_trade_account.py`
- `test_paper_trade_runner.py`
- `test_paper_trade_store.py`
- runner/adapter tests from `test_paper_trade_5m_golden.py`

It inherits the fee-lock fix (`effective_fee_model`), strict `from_dict`, lock-test timing fix, header-only bars, `step.json` docs, and container `accounts/` symlink note. Rebase onto `origin/main` after #1961 merges.

## Do not touch

- C1's `124.25` wording; no order-generation changes; keep in-loop `order_provider`.
- C5's `_validate_tracking_choices` (already confirmed by he-yufeng at `62bed6f`).
- Clean-split policy; `corporate_actions.py`.
- v1.1 contract/fixtures bytes.
- Settlement arithmetic (2dp notional ±2dp fee, 4dp HALF_UP avg_cost, slot-10 insufficient_cash).
- pt-v1.3 / intraday lifecycle.
- No new connector/loader/calendar dependency.
- No tree-wide default-88 black.

## Current evidence

- Final paper-trade suite after T5/T6: **603 passed** (151.04s).
- `data.py` 740 lines; `data_readiness.py` 146; 5m tool test 359 / scope 329 / strategy 198.
- Imports + ruff clean.
- T7 docs, T8 targeted format, T9 full baseline + PR process remain.