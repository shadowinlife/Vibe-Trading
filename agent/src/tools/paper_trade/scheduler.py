"""Live scheduling seam for the pt-v1.3 per-bar engine (P1-26, GAP-4).

The scheduler answers one question for an external cron / agent loop: *which
bar may I apply at this instant, and has it already been applied?*  It is a
thin cadence layer over :func:`~.session_runner.bar_step` — the ONLY writer of
a bar — and owns no clock, no timer, no thread and no network.  The caller
injects ``as_of`` and drives the cadence; this package never blocks, polls,
fetches data or reads a wall clock.

Two pure layers:

* :func:`closed_label` maps an aware ``+08:00`` instant onto the bar lattice:
  the LAST :data:`~.bar_clock.BAR_LABELS` entry closed at that instant, or
  ``None`` before the ``09:35`` open.  A cron uses it to turn wall time into
  the bar label it should ask for.
* :class:`SessionScheduler` is a one-tick state machine over the stored
  account.  One :meth:`SessionScheduler.tick` call applies AT MOST ONE bar
  and never applies a label after ``as_of``.

Tick rule (pinned; ``as_of`` must name a bar label exactly)::

    account.watermark == trade_date        -> session_closed
    label <= last applied label            -> already_applied
    label != the next unapplied label      -> nothing_due
    label == the next unapplied label      -> bar_step(...)

``next unapplied`` is ``09:35`` for a new session (no progress, or progress
carried from an earlier ledger day — the same roll ``bar_step`` performs) and
``next_bar(watermark_bar)`` inside a session.  A label AHEAD of the next
unapplied bar is a MISSED BEAT: ``nothing_due``, never a gap fill and never a
skip, because catching up is ``replay_session``'s job.  A label BEHIND it is
``already_applied`` and the store changes not one byte.

``bar_in_progress`` belongs to the stable error vocabulary but is unreachable
from :meth:`SessionScheduler.tick`: labels are bar ENDS, so every on-grid
label is closed at that instant and every off-grid instant is refused as
``not_a_bar_boundary`` before a bar is selected.

Accepted ``as_of`` spellings: an ``"HH:MM"`` session label (``"09:35"``) or a
full ISO-8601 timestamp carrying the Asia/Shanghai ``+08:00`` offset with zero
seconds and the scheduler's ``trade_date``
(``"2026-10-20T09:35:00+08:00"``).  Every other spelling — off-grid, naive, a
foreign offset, a sub-minute stamp, another day — raises
:class:`SchedulerError` with code ``not_a_bar_boundary``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .bar_clock import BAR_LABELS, bar_time, next_bar
from .feeds import FiveMinuteFeed
from .limits_cn_a import CnALimitsClient
from .rules import MarketRules
from .schema_v13 import BarStepRequest, PaperTradeSchemaError, parse_bar_time
from .session_runner import (
    BAR_STEP_STATUSES,
    CODE_BAR_ALREADY_APPLIED,
    STATUS_ADOPTED,
    STATUS_ALREADY_APPLIED,
    STATUS_APPLIED,
    STATUS_DRY_RUN,
    BarStepRun,
    SessionRunnerError,
    bar_step,
)
from .store import AccountStore

__all__ = [
    "SCHEDULER_ERROR_CODES",
    "SCHEDULER_STATUSES",
    "STATUS_NOTHING_DUE",
    "STATUS_SESSION_CLOSED",
    "SchedulerError",
    "SchedulerTick",
    "SessionScheduler",
    "closed_label",
]

# ── stable error / status vocabulary ──

#: ``SchedulerError.code`` vocabulary.  ``bar_in_progress`` is reserved for a
#: future sub-bar ``as_of`` form (see the module docstring); the grid contract
#: makes it unreachable in this contract, so no accepted tick can raise it.
SCHEDULER_ERROR_CODES: tuple[str, ...] = (
    "not_a_bar_boundary",
    "bar_in_progress",
    "nothing_due",
    "session_closed",
)

ERROR_NOT_A_BAR_BOUNDARY = "not_a_bar_boundary"

STATUS_NOTHING_DUE = "nothing_due"
STATUS_SESSION_CLOSED = "session_closed"

#: Every ``SchedulerTick.status``: the four ``bar_step`` outcomes plus the two
#: cadence answers this layer adds.
SCHEDULER_STATUSES: tuple[str, ...] = BAR_STEP_STATUSES + (
    STATUS_NOTHING_DUE,
    STATUS_SESSION_CLOSED,
)

_RUN_STATUSES = frozenset({STATUS_APPLIED, STATUS_ADOPTED, STATUS_DRY_RUN})
_NO_RUN_STATUSES = frozenset(
    {STATUS_ALREADY_APPLIED, STATUS_NOTHING_DUE, STATUS_SESSION_CLOSED}
)

_BAR_LABELS = frozenset(BAR_LABELS)
_DIGITS = frozenset("0123456789")


class SchedulerError(RuntimeError):
    """A refused scheduler tick, carrying a stable machine-readable ``code``.

    The code vocabulary is :data:`SCHEDULER_ERROR_CODES`; an unknown code is
    refused at construction so a typo cannot become a second, silently
    accepted refusal class.  ``message`` is safe to log — it never carries a
    credential or a provider payload.
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in SCHEDULER_ERROR_CODES:
            raise ValueError(f"unknown scheduler error code: {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True, slots=True)
class SchedulerTick:
    """The answer to one :meth:`SessionScheduler.tick` call.

    ``bar_label`` is the label the tick acted on (the applied/adopted bar, or
    the already-applied bar it was asked for) and is ``None`` when nothing was
    acted on (``nothing_due`` / ``session_closed``).  ``run`` is the
    :class:`~.session_runner.BarStepRun` for an apply/adopt/dry run and
    ``None`` otherwise; ``document`` is exactly that run's pt-v1.3 document,
    or the empty string when no document was produced.
    """

    status: str
    bar_label: str | None
    run: BarStepRun | None
    document: str

    def __post_init__(self) -> None:
        if self.status not in SCHEDULER_STATUSES:
            raise ValueError(
                f"status must be one of {SCHEDULER_STATUSES}, got {self.status!r}"
            )
        if not isinstance(self.document, str):
            raise ValueError("document must be a string")
        if self.status in _RUN_STATUSES:
            if self.run is None:
                raise ValueError(f"status {self.status!r} requires a run")
            if not self.bar_label:
                raise ValueError(f"status {self.status!r} requires a bar_label")
            if self.document != self.run.document:
                raise ValueError("document must be the run's document")
            return
        if self.status in _NO_RUN_STATUSES:
            if self.run is not None:
                raise ValueError(f"status {self.status!r} must not carry a run")
            if self.document:
                raise ValueError(f"status {self.status!r} must not carry a document")
        if self.status == STATUS_ALREADY_APPLIED:
            if not self.bar_label:
                raise ValueError("already_applied requires the bar_label it was asked for")
        elif self.bar_label is not None:
            raise ValueError(f"status {self.status!r} must not carry a bar_label")


# ── the pure lattice lookup ──


def closed_label(as_of: str) -> str | None:
    """The LAST session label closed at the aware instant ``as_of``.

    ``as_of`` is a full ISO-8601 timestamp carrying the Asia/Shanghai
    ``+08:00`` offset (a naive or foreign-offset value is refused).  The
    lookup is pure: no clock, no I/O, no cached state.  ``None`` means the
    session has not produced a closed bar yet (anything before ``09:35``);
    ``"11:30"`` covers the whole lunch break and ``"15:00"`` covers every
    instant from the close onwards.
    """
    try:
        canonical = parse_bar_time(as_of, "as_of")
    except PaperTradeSchemaError as exc:
        raise SchedulerError(ERROR_NOT_A_BAR_BOUNDARY, str(exc)) from exc
    label = canonical[11:16]
    closed = [bar for bar in BAR_LABELS if bar <= label]
    return closed[-1] if closed else None


# ── the scheduler ──


class SessionScheduler:
    """A pure one-bar-per-tick cadence layer over :func:`bar_step`.

    The caller owns the clock: it passes ``as_of`` to :meth:`tick` and calls
    again on its own cadence.  The scheduler reads the stored account, decides
    whether exactly the next unapplied bar is due, and delegates the write to
    :func:`~.session_runner.bar_step` with the same optional day inputs
    (``limits`` / ``prev_closes`` / ``calendar_client`` / ``rules``).
    """

    def __init__(
        self,
        store: AccountStore,
        account_id: str,
        trade_date: str,
        feed: FiveMinuteFeed,
        *,
        limits: CnALimitsClient | None = None,
        prev_closes: Mapping[str, float] | None = None,
        calendar_client: Any | None = None,
        rules: MarketRules | None = None,
    ) -> None:
        # Pure date validation: bar_time refuses a malformed day without
        # reading a clock (and without building a session state yet).
        bar_time(trade_date, BAR_LABELS[0])
        self._store = store
        self._account_id = account_id
        self._trade_date = trade_date
        self._feed = feed
        self._limits = limits
        self._prev_closes = dict(prev_closes) if prev_closes is not None else None
        self._calendar_client = calendar_client
        self._rules = rules

    def tick(self, as_of: str, *, dry_run: bool = False) -> SchedulerTick:
        """Apply at most ONE closed bar, exactly the next unapplied one.

        ``as_of`` must name one of the 48 session labels (see the module
        docstring for the accepted spellings).  A duplicate tick is
        ``already_applied``; a beat the caller missed is ``nothing_due``; a
        closed day is ``session_closed``.  Only the due label reaches
        :func:`bar_step`, so no bar after ``as_of`` can ever be applied and a
        tick never loops to catch up.
        """
        label = self._grid_label(as_of)
        account = self._store.load(self._account_id)
        if account.watermark == self._trade_date:
            return SchedulerTick(
                status=STATUS_SESSION_CLOSED, bar_label=None, run=None, document=""
            )
        last_label = self._last_applied_label(account)
        if last_label and label <= last_label:
            return SchedulerTick(
                status=STATUS_ALREADY_APPLIED, bar_label=label, run=None, document=""
            )
        next_label = next_bar(last_label) if last_label else BAR_LABELS[0]
        if next_label is None or label != next_label:
            return SchedulerTick(
                status=STATUS_NOTHING_DUE, bar_label=None, run=None, document=""
            )
        request = self._request(account, next_label)
        try:
            run = bar_step(
                self._store,
                request,
                self._feed,
                limits=self._limits,
                prev_closes=self._prev_closes,
                calendar_client=self._calendar_client,
                rules=self._rules,
                dry_run=dry_run,
            )
        except SessionRunnerError as exc:
            if exc.code != CODE_BAR_ALREADY_APPLIED:
                raise
            return SchedulerTick(
                status=STATUS_ALREADY_APPLIED,
                bar_label=next_label,
                run=None,
                document="",
            )
        return SchedulerTick(
            status=run.status,
            bar_label=next_label,
            run=run,
            document=run.document,
        )

    # ── internals ──

    def _grid_label(self, as_of: str) -> str:
        """The session label named by ``as_of`` (exactly on the 48-label grid)."""
        if not isinstance(as_of, str):
            raise SchedulerError(
                ERROR_NOT_A_BAR_BOUNDARY,
                f"as_of must be a string, got {type(as_of).__name__}",
            )
        if _is_hhmm(as_of):
            label = as_of
        else:
            try:
                canonical = parse_bar_time(as_of, "as_of")
            except PaperTradeSchemaError as exc:
                raise SchedulerError(ERROR_NOT_A_BAR_BOUNDARY, str(exc)) from exc
            if canonical[:10] != self._trade_date:
                raise SchedulerError(
                    ERROR_NOT_A_BAR_BOUNDARY,
                    f"as_of {as_of!r} names {canonical[:10]}, not the scheduler's "
                    f"trade_date {self._trade_date}",
                )
            label = canonical[11:16]
        if label not in _BAR_LABELS:
            raise SchedulerError(
                ERROR_NOT_A_BAR_BOUNDARY,
                f"as_of {as_of!r} names {label}, which is not one of the 48 "
                "continuous session bars",
            )
        return label

    def _last_applied_label(self, account: Any) -> str:
        """The current session's last applied label (``""`` for a new session).

        A watermark naming another day means the stored ``watermark_bar``
        belongs to that earlier session and must not gate this one; this
        mirrors ``bar_step``'s session roll exactly.  A watermark AFTER the
        requested day leaves the label empty on purpose — the runner then
        refuses the past day and the tick maps that refusal to
        ``already_applied``.
        """
        if account.watermark is not None and account.watermark != self._trade_date:
            return ""
        return account.watermark_bar

    def _request(self, account: Any, label: str) -> BarStepRequest:
        """The typed bar request for ``label``, aligned with the stored ledger."""
        return BarStepRequest(
            account_id=account.account_id,
            market=account.market,
            account_state=account.state(),
            from_date=self._trade_date,
            to_date=self._trade_date,
            bar_time=f"{self._trade_date}T{label}:00+08:00",
            interval="5m",
            session="cn_a",
            open_orders=tuple(account.open_orders),
            order_seq=account.order_seq,
        )


def _is_hhmm(text: str) -> bool:
    """Whether ``text`` is an ASCII ``HH:MM`` string (grid checked separately)."""
    return (
        len(text) == 5
        and text[2] == ":"
        and all(character in _DIGITS for character in text[:2] + text[3:])
    )
