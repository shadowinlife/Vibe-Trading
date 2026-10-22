"""Deterministic full-day replay over the idempotent bar runner (P1-25).

:func:`replay_session` drives one trading day end to end: for each of the 48
canonical labels, in session order, it builds a request from the CURRENT
account, calls :func:`~.session_runner.bar_step` and collects the result.  It
is a thin orchestrator — the bar engine, the progress gate, the crash contract
and the writes stay in :mod:`~.session_runner`; nothing here re-implements
them, reads a wall clock, sleeps or touches the network.

One mechanism for the order surface
-----------------------------------
The caller supplies a **per-bar request factory** ``(label, account) ->
BarStepRequest``.  It is the ONLY mechanism: the account changes with every
bar (cash, positions, the resting book, ``order_seq``), so a static request
cannot describe bar 2.  The factory receives the account as of the moment
before the bar — the stored account for a real run, the in-memory advanced
account for a dry run — and decides what to do at that label.  The replay then
stamps the request's ledger carrier (``account_state`` / ``open_orders`` /
``order_seq``) from that same account, exactly as
:func:`~.session_runner.bar_request` does for the MCP surface, so a stale
carrier is structurally impossible and ``received_state_hash`` always audits
the ledger the bar actually ran on.

Dispositions and refusals
-------------------------
Every bar ends in exactly one disposition: ``applied`` (written by this call),
``adopted`` (this call met its own orphaned step and adopted it — the
``bar_step`` crash path), ``already applied`` (``bar_already_applied`` — a
rerun or a resume; a NO-OP that writes nothing and is never an error), or
``dry run`` (computed only).  Any OTHER :class:`SessionRunnerError` stops the
replay: it returns ``status="partial"`` with the bars processed so far and the
refusal on :attr:`SessionReplay.error`, so a partial day is never reported as
completed and the error is never swallowed.  A partial day's stored
``session.json`` is still ``open``, agreeing with the status and bar count.

:class:`ReplayError` is separate from ``SessionRunnerError`` on purpose.  A bar
refusal is a *wire* refusal with a stable code and an AM-5 envelope; a replay
fault — a factory naming another day, an absent stored step behind an applied
bar, a dry run that wrote a byte — is a driver/programming error that must
never be dressed up as a client-visible ``INVALID_REQUEST``.

The document hash
-----------------
:attr:`SessionReplay.document_sha256` is the sha256 of the "\\n"-joined
per-bar **canonical document hashes**, in label order.  A computed bar
contributes ``state_hash(document)`` (the value ``bar_step`` stores as the
step's ``result_hash``); a no-op contributes the stored step's ``result_hash``,
which is the same number.  The document TEXT of a stored bar is not kept in
the ledger (only its hash and rows are), so hashing texts would make the field
unreproducible after the fact; hashing canonical document hashes keeps it
stable across a rerun, a resume and a dry run.

Dry-run guarantee
-----------------
``dry_run=True`` takes a before/after snapshot of every file under the account
root and refuses with :class:`ReplayError` if a path or a byte changed: the
zero-write promise is checked, not asserted.  The in-memory account is advanced
with the runner's own bridge (``BarEngineState.to_account`` + the closing-bar
watermark), so a dry run computes the documents a real replay would write.

That advance needs one seam: ``bar_step`` re-reads the account from the store
under its lock (that read IS its ledger authority), and a dry run may not
persist an advance.  The replay therefore hands ``bar_step`` a
:class:`_DryRunLedger` — an :class:`~.store.AccountStore` view whose ``load``
serves the simulated ledger, whose ``lock`` is a no-op and whose
``save_account_atomic`` refuses.  The directory still resolves to the real
account, so the step reader sees exactly the history a real run would; only
the ledger view advances without a byte on disk.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator

from . import bar_clock, bar_store, session_summary, state_hash
from .account import AccountError, PaperTradeAccount, require_date
from .bar_layout import BarStoreError, SessionNotFound
from .bar_store import BarStep
from .feeds import FiveMinuteFeed
from .limits_cn_a import CnALimitsClient
from .rules import MarketRules
from .schema import BarStepRequest, PaperTradeSchemaError, parse_bar_time
from .session_documents import CODE_BAR_ALREADY_APPLIED, SessionRunnerError
from .session_runner import (
    STATUS_ADOPTED,
    STATUS_ALREADY_APPLIED,
    STATUS_APPLIED,
    STATUS_DRY_RUN,
    BarStepRun,
    bar_step,
)
from .store import AccountNotFound, AccountStore

__all__ = [
    "SESSION_REPLAY_STATUSES",
    "SESSION_STATUS_COMPLETED",
    "SESSION_STATUS_DRY_RUN",
    "SESSION_STATUS_PARTIAL",
    "BarRequestFactory",
    "ReplayError",
    "SessionReplay",
    "replay_session",
]

SESSION_STATUS_COMPLETED = "completed"
SESSION_STATUS_PARTIAL = "partial"
SESSION_STATUS_DRY_RUN = "dry_run"

#: The :attr:`SessionReplay.status` vocabulary.
SESSION_REPLAY_STATUSES: tuple[str, ...] = (
    SESSION_STATUS_COMPLETED,
    SESSION_STATUS_PARTIAL,
    SESSION_STATUS_DRY_RUN,
)

#: ``(label, current account) -> the bar request to run at that label``.
BarRequestFactory = Callable[[str, PaperTradeAccount], BarStepRequest]

_HEX = frozenset("0123456789abcdef")


class ReplayError(RuntimeError):
    """The replay driver refuses to start, continue or accept its own result."""


class _DryRunLedger(AccountStore):
    """A store view whose ledger advances in memory and whose writes refuse.

    ``load`` is the only method ``bar_step`` treats as authoritative, ``lock``
    never contends and ``save_account_atomic`` raises: a dry run that reaches a
    write is a bug, not a preview.
    """

    def __init__(self, store: AccountStore, account: PaperTradeAccount) -> None:
        super().__init__(store.base_dir)
        self._account = account

    def load(self, account_id: str) -> PaperTradeAccount:
        return self._account

    @contextmanager
    def lock(self, account_id: str) -> Iterator[None]:
        yield

    def save_account_atomic(self, account: PaperTradeAccount) -> Path:
        raise ReplayError("a dry run reached an account save")

    def advance(self, account: PaperTradeAccount) -> None:
        self._account = account


@dataclass(frozen=True, slots=True)
class SessionReplay:
    """The outcome of replaying one trading day.

    ``bars`` holds one :class:`~.session_runner.BarStepRun` per processed label
    in session order — exactly 48 for ``completed``/``dry_run``, the processed
    prefix for ``partial``.  ``session`` is the parsed ``session.json`` (the
    in-memory equivalent for a dry run, empty when a partial day got no bar),
    ``error`` is the refusal that stopped a partial day, and ``computed`` is
    the derived count of dry-run bars.
    """

    trade_date: str
    account_id: str
    status: str
    bars: tuple[BarStepRun, ...]
    applied: int
    adopted: int
    noop: int
    session: Mapping[str, Any]
    document_sha256: str
    dry_run: bool = False
    error: SessionRunnerError | None = None

    def __post_init__(self) -> None:
        if self.status not in SESSION_REPLAY_STATUSES:
            raise ValueError(
                f"status must be one of {SESSION_REPLAY_STATUSES}, got {self.status!r}"
            )
        for name in ("applied", "adopted", "noop"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer, got {value!r}")
        if len(self.document_sha256) != 64 or set(self.document_sha256) - _HEX:
            raise ValueError("document_sha256 must be a 64-character lowercase hex digest")
        if not isinstance(self.session, Mapping):
            raise ValueError(f"session must be a mapping, got {type(self.session).__name__}")
        if len(self.bars) > len(bar_clock.BAR_LABELS):
            raise ValueError("bars cannot hold more than the 48 session labels")
        if self.applied + self.adopted + self.noop + self.computed != len(self.bars):
            raise ValueError("every bar must be exactly one of applied/adopted/noop/dry_run")
        if self.status == SESSION_STATUS_COMPLETED and (
            len(self.bars) != len(bar_clock.BAR_LABELS) or self.error is not None or self.computed
        ):
            raise ValueError("a completed replay holds 48 non-dry bars and no error")
        if self.status == SESSION_STATUS_DRY_RUN and (
            not self.dry_run or self.error is not None or self.applied or self.adopted
        ):
            raise ValueError("a dry-run replay wrote nothing and carries no error")
        if self.status == SESSION_STATUS_PARTIAL and self.error is None:
            raise ValueError("a partial replay names the refusal that stopped it")

    @property
    def computed(self) -> int:
        """How many bars were computed without writing (a dry run)."""
        return sum(1 for bar in self.bars if bar.status == STATUS_DRY_RUN)


def replay_session(
    store: AccountStore,
    request_factory: BarRequestFactory,
    account_id: str,
    trade_date: str,
    feed: FiveMinuteFeed,
    *,
    limits: CnALimitsClient | None = None,
    prev_closes: Mapping[str, float] | None = None,
    calendar_client: Any | None = None,
    rules: MarketRules | None = None,
    dry_run: bool = False,
) -> SessionReplay:
    """Replay the 48 bars of ``trade_date`` for ``account_id``.

    Threads the caller's band table / calendar / rules through to every
    ``bar_step`` call.  Never reads a clock and never fetches data itself: the
    feed is the only data door and ``request_factory`` the only order surface.
    """
    if not callable(request_factory):
        raise ReplayError(f"request_factory must be callable, got {type(request_factory).__name__}")
    account = _open_account(store, account_id, trade_date)
    root = store.account_dir(account_id)
    before = _tree(root) if dry_run else None
    simulated = _DryRunLedger(store, account) if dry_run else None
    ledger = store if simulated is None else simulated
    bars: list[BarStepRun] = []
    hashes: list[str] = []
    applied = adopted = noop = 0
    error: SessionRunnerError | None = None

    for label in bar_clock.BAR_LABELS:
        request = _request_for(request_factory, label, account, account_id, trade_date)
        try:
            run = bar_step(
                ledger,
                request,
                feed,
                limits=limits,
                prev_closes=prev_closes,
                calendar_client=calendar_client,
                rules=rules,
                dry_run=dry_run,
            )
        except SessionRunnerError as exc:
            if exc.code != CODE_BAR_ALREADY_APPLIED:
                error = exc
                break
            step = _stored_step(root, trade_date, label)
            run = BarStepRun(
                status=STATUS_ALREADY_APPLIED,
                outcome=None,
                account=account,
                step_path=step.directory,
                document=exc.document,
            )
            noop += 1
            hashes.append(step.result_hash)
        else:
            if run.status == STATUS_APPLIED:
                applied += 1
            elif run.status == STATUS_ADOPTED:
                adopted += 1
            elif run.status != STATUS_DRY_RUN:
                raise ReplayError(f"bar {label} returned an unexpected status {run.status!r}")
            hashes.append(_document_hash(run.document))
            if simulated is None:
                account = _carried(run, label)
            else:
                account = _advance(account, run, request, label)
                simulated.advance(account)
        bars.append(run)

    if before is not None and _tree(root) != before:
        raise ReplayError(
            f"a dry run wrote under the account root of {account_id!r}; "
            "the zero-write promise was broken"
        )
    status = _status(error, dry_run, bars)
    session = _session(
        store,
        account,
        trade_date,
        status=status,
        bars=bars,
        hashes=hashes,
        dry_run=dry_run,
        advanced=applied + adopted,
    )
    return SessionReplay(
        trade_date=trade_date,
        account_id=account_id,
        status=status,
        bars=tuple(bars),
        applied=applied,
        adopted=adopted,
        noop=noop,
        session=session,
        document_sha256=hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest(),
        dry_run=dry_run,
        error=error,
    )


# ── preconditions and the per-bar request ────────────────────────────────


def _open_account(store: AccountStore, account_id: str, trade_date: str) -> PaperTradeAccount:
    """Load the account and refuse a day it has already left behind."""
    try:
        require_date(trade_date, "trade_date")
    except AccountError as exc:
        raise ReplayError(str(exc)) from exc
    try:
        account = store.load(account_id)
    except AccountNotFound as exc:
        raise ReplayError(f"account {account_id!r} does not exist; nothing to replay") from exc
    if account.watermark is not None and trade_date < account.watermark:
        raise ReplayError(
            f"{trade_date} precedes the account watermark {account.watermark}; a session "
            "the account has left behind has no pre-bar ledger to replay"
        )
    return account


def _request_for(
    request_factory: BarRequestFactory,
    label: str,
    account: PaperTradeAccount,
    account_id: str,
    trade_date: str,
) -> BarStepRequest:
    """Build one request and align its carrier with the authoritative account."""
    request = request_factory(label, account)
    if not isinstance(request, BarStepRequest):
        raise ReplayError(
            f"request_factory returned {type(request).__name__} for bar {label}, "
            "not a BarStepRequest"
        )
    if request.account_id != account_id or request.trade_date != trade_date:
        raise ReplayError(
            f"request_factory named {request.account_id!r}/{request.trade_date} for bar "
            f"{label}; this replay is {account_id!r}/{trade_date}"
        )
    try:
        canonical = parse_bar_time(request.bar_time, "bar_time", trade_date=trade_date)
    except PaperTradeSchemaError as exc:
        raise ReplayError(
            f"request_factory returned an unusable bar_time for {label}: {exc}"
        ) from exc
    if canonical[11:16] != label:
        raise ReplayError(
            f"request_factory returned bar_time {request.bar_time!r} for label {label}; "
            "the replay owns the label sequence"
        )
    return replace(
        request,
        account_state=account.state(),
        open_orders=tuple(account.open_orders),
        order_seq=account.order_seq,
    )


def _carried(run: BarStepRun, label: str) -> PaperTradeAccount:
    if run.account is None:
        raise ReplayError(f"bar {label} was written but returned no account")
    return run.account


def _advance(
    account: PaperTradeAccount, run: BarStepRun, request: BarStepRequest, label: str
) -> PaperTradeAccount:
    """Advance the in-memory account across a dry-run bar (the runner's bridge)."""
    outcome = run.outcome
    if outcome is None:
        raise ReplayError(f"dry-run bar {label} produced no engine outcome")
    advanced = outcome.state.to_account(
        account,
        session_phase=bar_clock.classify(label),
        slippage_model=outcome.bar_result.slippage["model"],
        engine_fingerprint=outcome.bar_result.result.engine_fingerprint,
    )
    return (
        replace(advanced, watermark=request.trade_date)
        if bar_clock.is_last_bar(label)
        else advanced
    )


# ── the session document ─────────────────────────────────────────────────


def _status(error: SessionRunnerError | None, dry_run: bool, bars: list[BarStepRun]) -> str:
    if error is not None:
        return SESSION_STATUS_PARTIAL
    if dry_run:
        return SESSION_STATUS_DRY_RUN
    return SESSION_STATUS_COMPLETED if len(bars) == len(bar_clock.BAR_LABELS) else SESSION_STATUS_PARTIAL


def _session(
    store: AccountStore,
    account: PaperTradeAccount,
    trade_date: str,
    *,
    status: str,
    bars: list[BarStepRun],
    hashes: list[str],
    dry_run: bool,
    advanced: int,
) -> Mapping[str, Any]:
    """The session.json view: refreshed on a close, in memory for a dry run."""
    root = store.account_dir(account.account_id)
    if dry_run and status == SESSION_STATUS_DRY_RUN and any(bar.outcome is not None for bar in bars):
        return MappingProxyType(_memory_summary(account, trade_date, bars, hashes))
    if status == SESSION_STATUS_COMPLETED and advanced and not dry_run:
        _refresh_summary(root, trade_date, account)
    return MappingProxyType(_read_summary(root, trade_date))


def _refresh_summary(root: Path, trade_date: str, account: PaperTradeAccount) -> None:
    """Add the closing-ledger block to the closed summary the closing bar wrote.

    Status, progress and the chain head are read back verbatim — they are the
    runner's evidence.  Only the additive ``final_state`` block is new.
    """
    current = _read_summary(root, trade_date)
    if current.get("status") != "closed" or current.get("applied_bar_count") != len(
        bar_clock.BAR_LABELS
    ):
        raise ReplayError(
            f"the closing bar left a summary that is not a closed "
            f"{len(bar_clock.BAR_LABELS)}-bar session: {current}"
        )
    session_summary.write_session_summary(
        root,
        account_id=account.account_id,
        trade_date=trade_date,
        status="closed",
        applied_bars=bar_clock.BAR_LABELS,
        counts=current["counts"],
        result_hash_chain_head=current["result_hash_chain_head"],
        final_state=_final_state(account),
    )


def _read_summary(root: Path, trade_date: str) -> dict[str, Any]:
    try:
        return dict(session_summary.read_session_summary(root, trade_date))
    except SessionNotFound:
        return {}
    except BarStoreError as exc:
        raise ReplayError(f"the session summary for {trade_date} cannot be read: {exc}") from exc


def _final_state(account: PaperTradeAccount) -> dict[str, Any]:
    """The closing ledger block (cash, positions, resting orders)."""
    return {
        "cash": account.cash,
        "positions": [
            {"symbol": row.symbol, "qty": row.qty, "avg_cost": row.avg_cost}
            for row in account.positions
        ],
        "open_orders": account.v13_state_payload()["open_orders"],
    }


def _memory_summary(
    account: PaperTradeAccount, trade_date: str, bars: list[BarStepRun], hashes: list[str]
) -> dict[str, Any]:
    """The dry-run summary: the shape the file would carry, built in memory.

    Mirrors the runner's per-bar counters (``bar_*``) because the closing step
    owns only that bar's rows; ``bars`` counts the whole session.
    """
    last = bars[-1].outcome
    if last is None:
        raise ReplayError("a dry run reached the close without an engine outcome")
    labels = bar_clock.BAR_LABELS
    counts = {
        "bars": len(labels),
        "bar_fills": len(last.fills),
        "bar_rejections": len(last.rejected),
        "bar_canceled": len(last.bar_result.result.canceled_orders),
        "open_orders": len(last.state.book.open_orders()),
        "positions": len(last.state.positions),
        "cash_cents": int(last.state.cash * 100),
    }
    return {
        "format": session_summary.SESSION_FORMAT,
        "account_id": account.account_id,
        "trade_date": trade_date,
        "status": "closed",
        "first_bar": labels[0],
        "last_bar": labels[-1],
        "watermark_bar": labels[-1],
        "applied_bar_count": len(labels),
        "counts": counts,
        "result_hash_chain_head": hashes[-1],
        "final_state": _final_state(account),
    }


# ── evidence helpers ─────────────────────────────────────────────────────


def _stored_step(root: Path, trade_date: str, label: str) -> BarStep:
    """The verified step behind a ``bar_already_applied`` refusal."""
    try:
        return bar_store.read_bar_step(root, trade_date, label)
    except BarStoreError as exc:
        raise ReplayError(
            f"bar {label} on {trade_date} is reported as applied but its step cannot be "
            f"read: {exc}"
        ) from exc


def _document_hash(document: str) -> str:
    """The canonical hash of one computed bar document."""
    try:
        payload = json.loads(document)
    except json.JSONDecodeError as exc:
        raise ReplayError(f"bar_step returned a non-JSON document: {exc.msg}") from exc
    return state_hash.state_hash(payload)


def _tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` with the sha256 of its bytes (a dry-run guard)."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
