"""Persistent execution ledger for strategy runs.

The store in this package answers exactly one question: *has this ``run_key``
already been claimed?* It is an execution ledger only — it is never a schedule
authority and never a run-history authority. F9 scheduled research keeps owning
``scheduled_research_jobs.json``, and each run's directory keeps owning its
artifacts; this ledger holds only the claim fact and the run's terminal state.

Public surface:

* :class:`~src.strategy_runs.store.StrategyRunStore` — the SQLite-backed store.
* :class:`~src.strategy_runs.store.RunRow` — one immutable ledger row.
* :class:`~src.strategy_runs.store.ClaimResult` — winner flag plus the row.
"""

from __future__ import annotations

from src.strategy_runs.store import ClaimResult, RunRow, StrategyRunStore

__all__ = ["ClaimResult", "RunRow", "StrategyRunStore"]
