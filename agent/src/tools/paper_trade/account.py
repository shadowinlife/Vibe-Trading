"""Incremental paper-trade account model (P0 cn_a 5m, T5).

The engine (``engine.py`` / ``tool.py``) is stateless by contract: it receives a
ledger snapshot and returns the next one.  This module owns the *persistent*
half — the account that carries that snapshot from day to day — and nothing
about matching.

Design rules:

* **No clock.**  ``today``, ``next_trading_day`` and ``now`` are injected by the
  caller (``runner.py`` threads them from its own injected arguments), so the
  same creation call always produces the same account document.
* **Start date is the only temporal input that matters.**  Per planbook §0.2 /
  the execution plan, a paper account starts TODAY or on the NEXT trading day;
  a past date is a backtest, not a paper trade, and returns the stable code
  ``start_date_in_past``.
* **The watermark only moves forward** (``with_advance`` / ``with_non_trading_day``
  refuse a backwards write), which is what makes a replayed day a no-op instead
  of a double debit.
* **The on-disk shape is versioned independently of the wire contract.**  A
  ``pt-account-v1`` document describes the account envelope; ``step.json``
  echoes that envelope version as ``account_schema_version`` so a reader can
  tell which layout produced the step.  The wire contract version is a property
  of the wire document itself (``schema_version``) and is never written to the
  store: coupling the two would force an account migration every time the tool
  contract gains an additive field.

``initial_cash`` / ``initial_positions`` are frozen at creation for audit; the
mutable ledger is ``cash`` / ``positions``.  ``positions`` is an ordered tuple,
never a set: the engine treats element order as semantic (AM-4-1).

pt-v1.3 (P1 intraday) adds six optional fields to the same document —
``open_orders`` / ``order_seq`` / ``watermark_bar`` / ``slippage_model`` /
``session_phase`` / ``bought_today``.  They are emitted ONLY when at least one
is non-default, or the caller explicitly opts in (the same D8 selection rule
the wire contract uses), so a v1.2 account's ``account.json`` stays
byte-identical and a v1.2 document without the keys loads with the empty
defaults.  ``watermark_bar`` is the LAST APPLIED bar label of the current
session (``HH:MM``, ``""`` when no bar has been applied) — it is never the
trade date: ``watermark`` remains the last processed ``trade_date``.

``bought_today`` is the cn_a T+1 counter — ``{symbol: whole shares bought on
the session day}`` — the one piece of the per-bar engine's ledger that a
``bar_step`` boundary would otherwise lose: it is NOT derivable from cash /
positions, because a holding carries no acquisition date.  The 15:00 session
settlement clears it, so the next session starts with an empty counter.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping

from .schema import (
    FILL_MODELS,
    INTERVALS,
    MARKETS,
    PATH_MODELS,
    SESSION_PHASES,
    SLIPPAGE_MODELS,
    AccountState,
    OrderState,
    PaperTradeSchemaError,
    Position,
    parse_account_state,
    parse_order_state,
)

__all__ = [
    "ACCOUNT_ID_MAX_LEN",
    "ACCOUNT_SCHEMA_VERSION",
    "INVALID_ACCOUNT",
    "INVALID_INITIAL_POSITIONS",
    "START_DATE_IN_PAST",
    "START_DATE_NOT_ADMISSIBLE",
    "V13_ACCOUNT_KEYS",
    "AccountError",
    "PaperTradeAccount",
    "account_state_payload",
    "create_account",
    "require_date",
    "validate_account_id",
]

#: Version of the ``account.json`` envelope (independent of the
#: ``paper_trade_step`` wire contract, which travels on the wire document).
#: A reader refuses anything else.
ACCOUNT_SCHEMA_VERSION = "pt-account-v1"

#: Stable creation error codes (contract surface — callers switch on these).
START_DATE_IN_PAST = "start_date_in_past"
START_DATE_NOT_ADMISSIBLE = "start_date_not_admissible"
INVALID_ACCOUNT = "invalid_account"
INVALID_INITIAL_POSITIONS = "invalid_initial_positions"

ACCOUNT_ID_MAX_LEN = 64
_ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BAR_LABEL_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

#: The six pt-v1.3-only ``account.json`` keys, in document order.  A document
#: that carries any of them is a v1.3 document; a v1.2 document carries none.
#: ``bought_today`` is emitted only while the counter is non-empty (an absent
#: key is the total default "no buys today"), so a fresh v1.3 account's key set
#: is the five always-present keys below.
V13_ACCOUNT_KEYS: tuple[str, ...] = (
    "open_orders",
    "order_seq",
    "watermark_bar",
    "slippage_model",
    "session_phase",
    "bought_today",
)


class AccountError(ValueError):
    """A creation/parsing violation carrying a stable ``code``.

    ``code`` is the machine-readable surface; ``message`` is deterministic
    English (VD-7 discipline) and never contains a timestamp.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_account_id(account_id: str) -> str:
    """Return ``account_id`` if it is a safe single path segment.

    The id becomes a directory name under ``accounts/``, so a separator, a
    ``..`` segment or a leading dot is a path-traversal attempt and is refused
    here rather than being normalised into a surprise location.
    """
    if not isinstance(account_id, str) or not account_id:
        raise AccountError(INVALID_ACCOUNT, "account_id must be a non-empty string")
    if len(account_id) > ACCOUNT_ID_MAX_LEN:
        raise AccountError(
            INVALID_ACCOUNT,
            f"account_id must be at most {ACCOUNT_ID_MAX_LEN} characters, got {len(account_id)}",
        )
    if not _ACCOUNT_ID_RE.match(account_id):
        raise AccountError(
            INVALID_ACCOUNT,
            "account_id must match [A-Za-z0-9][A-Za-z0-9._-]* "
            f"(no path separators, no leading dot), got {account_id!r}",
        )
    return account_id


def require_date(value: Any, path: str) -> str:
    """Return ``value`` as a strict ``YYYY-MM-DD`` date string.

    Exact-format regex first: ``datetime.date.fromisoformat`` in 3.11 accepts
    looser spellings (``2026-1-2``), and a ledger keyed on two spellings of the
    same day is not a ledger.
    """
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise AccountError(INVALID_ACCOUNT, f"{path} must be YYYY-MM-DD, got {value!r}")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise AccountError(INVALID_ACCOUNT, f"{path} is not a calendar date: {value!r}") from exc
    return value


def _require_number(value: Any, path: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AccountError(INVALID_ACCOUNT, f"{path} must be a number, got {type(value).__name__}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise AccountError(INVALID_ACCOUNT, f"{path} must be finite, got {value!r}")
    if minimum is not None and numeric < minimum:
        raise AccountError(INVALID_ACCOUNT, f"{path} must be >= {minimum}, got {numeric}")
    return numeric


def _positions(raw: Any, path: str) -> tuple[Position, ...]:
    """Parse a position list (``Position`` objects or wire dicts) via ``schema``."""
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise AccountError(INVALID_ACCOUNT, f"{path} must be an array")
    items = [
        {"symbol": item.symbol, "qty": item.qty, "avg_cost": item.avg_cost}
        if isinstance(item, Position)
        else item
        for item in raw
    ]
    try:
        return parse_account_state({"cash": 0.0, "positions": items}, path).positions
    except PaperTradeSchemaError as exc:
        raise AccountError(INVALID_ACCOUNT, str(exc)) from exc


def _position_rows(positions: tuple[Position, ...]) -> list[dict[str, Any]]:
    return [
        {"symbol": item.symbol, "qty": item.qty, "avg_cost": item.avg_cost}
        for item in positions
    ]


def _bought_today_rows(counts: Mapping[str, int]) -> dict[str, int]:
    """The T+1 counter as its JSON row: symbol-sorted, whole shares."""
    return {symbol: int(qty) for symbol, qty in sorted(counts.items())}


def _require_int(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AccountError(
            INVALID_ACCOUNT, f"{path} must be an integer, got {type(value).__name__}"
        )
    if value < minimum:
        raise AccountError(INVALID_ACCOUNT, f"{path} must be >= {minimum}, got {value}")
    return value


def _choice_or_empty(value: Any, path: str, allowed: tuple[str, ...]) -> str:
    """Accept an empty string ("not set") or one member of ``allowed``."""
    if not isinstance(value, str):
        raise AccountError(
            INVALID_ACCOUNT, f"{path} must be a string, got {type(value).__name__}"
        )
    if value and value not in allowed:
        raise AccountError(
            INVALID_ACCOUNT, f"{path} must be one of {allowed} or empty, got {value!r}"
        )
    return value


def _bar_label_or_empty(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise AccountError(
            INVALID_ACCOUNT, f"{path} must be a string, got {type(value).__name__}"
        )
    if value and not _BAR_LABEL_RE.match(value):
        raise AccountError(
            INVALID_ACCOUNT, f"{path} must be HH:MM or empty, got {value!r}"
        )
    return value


def _order_state_row(order: OrderState) -> dict[str, Any]:
    """One ``OrderState`` as its JSON row (the full declaration order)."""
    return {
        "order_id": order.order_id,
        "account_id": order.account_id,
        "trade_date": order.trade_date,
        "symbol": order.symbol,
        "side": order.side,
        "qty": order.qty,
        "remaining_qty": order.remaining_qty,
        "limit": order.limit,
        "tif": order.tif,
        "status": order.status,
        "bar_label": order.bar_label,
        "client_order_id": order.client_order_id,
        "filled_qty": order.filled_qty,
        "avg_fill_price": order.avg_fill_price,
        "seq": order.seq,
    }


def _order_state_rows(orders: tuple[OrderState, ...]) -> list[dict[str, Any]]:
    return [_order_state_row(order) for order in orders]


def _open_orders(raw: Any, path: str, account_id: str) -> tuple[OrderState, ...]:
    """Parse carried open-order rows and enforce the book-level invariants.

    Every row is re-parsed through ``parse_order_state`` even when it is
    already an ``OrderState`` instance, so a hand-built object cannot bypass
    the quantity/status/bar-label checks.  ``trade_date`` is deliberately NOT
    compared with ``watermark``: open orders accumulate during a session whose
    day is only closed when the watermark advances.
    """
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise AccountError(INVALID_ACCOUNT, f"{path} must be an array")
    orders: list[OrderState] = []
    for index, item in enumerate(raw):
        row_path = f"{path}[{index}]"
        candidate = _order_state_row(item) if isinstance(item, OrderState) else item
        try:
            row = parse_order_state(candidate, row_path)
        except PaperTradeSchemaError as exc:
            raise AccountError(INVALID_ACCOUNT, str(exc)) from exc
        if row.account_id != account_id:
            raise AccountError(
                INVALID_ACCOUNT,
                f"{row_path}.account_id must be {account_id!r}, got {row.account_id!r}",
            )
        orders.append(row)
    order_ids = [row.order_id for row in orders]
    if len(set(order_ids)) != len(order_ids):
        raise AccountError(INVALID_ACCOUNT, f"{path} contains a duplicate order_id")
    client_ids = [row.client_order_id for row in orders if row.client_order_id]
    if len(set(client_ids)) != len(client_ids):
        raise AccountError(INVALID_ACCOUNT, f"{path} contains a duplicate client_order_id")
    return tuple(orders)


def _bought_today(raw: Any, path: str) -> Mapping[str, int]:
    """Parse the v1.3 T+1 counter: ``{symbol: whole shares bought today}``.

    ``None`` (a v1.2 document) parses as the empty counter.  Only whole,
    non-negative share counts are admitted: cn_a trades in board lots, so a
    fractional counter means the writer and this reader disagree about what a
    share is, and silently truncating it would make a holding unsellable for
    the wrong reason.
    """
    if raw is None:
        return MappingProxyType({})
    if not isinstance(raw, Mapping):
        raise AccountError(INVALID_ACCOUNT, f"{path} must be an object")
    counts: dict[str, int] = {}
    for symbol, qty in raw.items():
        if not isinstance(symbol, str) or not symbol:
            raise AccountError(
                INVALID_ACCOUNT, f"{path} keys must be non-empty symbols, got {symbol!r}"
            )
        counts[symbol] = _require_int(qty, f"{path}[{symbol!r}]")
    return MappingProxyType(dict(sorted(counts.items())))


def account_state_payload(state: AccountState) -> dict[str, Any]:
    """Canonical JSON ``account_state`` payload (the adoption-key pre-image).

    ``account.json``'s digest, ``step.json``'s ``input_state``/``end_state`` and
    the recovery chain all hash THIS shape, so a stored hash can be recomputed
    from either side of the write without a second normalizer.
    """
    return {"cash": state.cash, "positions": _position_rows(state.positions)}


@dataclass(frozen=True, slots=True)
class PaperTradeAccount:
    """One incremental paper-trade account (plan T5 data model).

    ``initial_*`` are the immutable creation snapshot; ``cash`` / ``positions``
    are the current ledger; ``watermark`` is the last trade_date the account has
    processed (``None`` before the first advance).  ``non_trading_days`` records
    every closed day the watermark walked over, so "no step" is auditable
    without a step directory.

    The pt-v1.3 fields carry the intraday session across bars:
    ``open_orders``/``order_seq`` are the resting book and its next free
    sequence, ``watermark_bar`` is the last applied ``HH:MM`` label of the
    CURRENT session, ``bought_today`` is the cn_a T+1 counter
    (``{symbol: shares bought on this session day}``), and
    ``slippage_model``/``session_phase`` are the resolved execution model and
    last classified phase (``""`` = not set/not classified).  All six default
    to their v1.2-absent values.
    """

    account_id: str
    market: str
    start_date: str
    initial_cash: float
    initial_positions: tuple[Position, ...]
    cash: float
    positions: tuple[Position, ...]
    watermark: str | None
    engine_fingerprint: str
    created_at: str
    interval: str = "5m"
    path_model: str = "bar_median"
    fill_model: str = "all_or_none"
    schema_version: str = ACCOUNT_SCHEMA_VERSION
    non_trading_days: tuple[str, ...] = ()
    open_orders: tuple[OrderState, ...] = ()
    order_seq: int = 0
    watermark_bar: str = ""
    slippage_model: str = ""
    session_phase: str = ""
    bought_today: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))

    # ── ledger views ──

    def state(self) -> AccountState:
        """The engine's input shape for the *current* ledger."""
        return AccountState(cash=self.cash, positions=self.positions)

    def state_payload(self) -> dict[str, Any]:
        """The JSON account_state shape (exactly what the wire parser reads).

        This is the value the engine hashes for ``received_state_hash``, so it
        must stay identical in shape to ``schema.parse_account_state`` input —
        which is why the runner also hashes it for step adoption.
        """
        return account_state_payload(self.state())

    def state_digest(self) -> str:
        """``sha256(JCS(state_payload()))`` — the chain/adoption key."""
        from .state_hash import state_hash

        return state_hash(self.state_payload())

    def request_fields(self) -> dict[str, Any]:
        """Wire fields this account adds to a ``paper_trade_step`` request.

        Only NON-DEFAULT values are emitted (D4 spirit): a v1.1-shaped account
        produces no extra keys, so its requests stay byte-identical to pt-v1.1.
        """
        fields: dict[str, Any] = {}
        if self.interval != "1d":
            fields["interval"] = self.interval
            fields["path_model"] = self.path_model
            fields["fill_model"] = self.fill_model
            fields["session"] = "cn_a"
        return fields

    # ── pt-v1.3 session state ──

    @property
    def has_v13_state(self) -> bool:
        """True when any v1.3-only field is non-default (D8 selection rule)."""
        return bool(
            self.open_orders
            or self.order_seq
            or self.watermark_bar
            or self.slippage_model
            or self.session_phase
            or self.bought_today
        )

    def v13_state_payload(self) -> dict[str, Any]:
        """The v1.3 keys as one block, every applicable key present.

        A v1.3 document always carries the five structural keys — an omitted
        ``open_orders`` would read as "no orders" instead of "unknown", which
        is exactly the silent-drop failure the version selection exists to
        prevent.  ``bought_today`` is the exception: its absent value is TOTAL
        (``{}`` = nothing was bought today, never "unknown"), so it is written
        only while the counter is non-empty and a default account's key set
        does not grow for a field that carries no information.
        """
        payload: dict[str, Any] = {
            "open_orders": _order_state_rows(self.open_orders),
            "order_seq": self.order_seq,
            "watermark_bar": self.watermark_bar,
            "slippage_model": self.slippage_model,
            "session_phase": self.session_phase,
        }
        if self.bought_today:
            payload["bought_today"] = _bought_today_rows(self.bought_today)
        return payload

    # ── monotone transitions ──

    def with_advance(
        self,
        *,
        end_state: AccountState,
        watermark: str,
        engine_fingerprint: str | None = None,
    ) -> "PaperTradeAccount":
        """Return the account after applying one day's ``end_state``.

        ``engine_fingerprint=None`` keeps the current fingerprint (the caller
        did not observe one); a string replaces it.  A watermark that does not
        move forward is a programming error, not a data condition — it would
        make the next replay check silently wrong.
        """
        require_date(watermark, "watermark")
        if self.watermark is not None and watermark <= self.watermark:
            raise ValueError(
                f"watermark must move forward: current {self.watermark}, requested {watermark}"
            )
        return replace(
            self,
            cash=end_state.cash,
            positions=tuple(end_state.positions),
            watermark=watermark,
            engine_fingerprint=(
                self.engine_fingerprint if engine_fingerprint is None else engine_fingerprint
            ),
        )

    def with_non_trading_day(self, trade_date: str) -> "PaperTradeAccount":
        """Advance the watermark over a closed day without touching the ledger."""
        require_date(trade_date, "trade_date")
        if self.watermark is not None and trade_date <= self.watermark:
            raise ValueError(
                f"watermark must move forward: current {self.watermark}, requested {trade_date}"
            )
        days = self.non_trading_days
        if trade_date not in days:
            days = days + (trade_date,)
        return replace(self, watermark=trade_date, non_trading_days=days)

    # ── document shape ──

    def to_dict(self, *, include_v13: bool = False) -> dict[str, Any]:
        """The ``account.json`` document (deterministic key set).

        The v1.3 keys are appended as one block when the account carries any
        v1.3 state, or when ``include_v13=True`` asks for them explicitly.
        A v1.2 account therefore serializes byte-identically to the document
        written before pt-v1.3 existed.  There is no opt-OUT: a v1.3 account
        can never write a v1.2-shaped document that silently drops its open
        orders or its T+1 counter.
        """
        document: dict[str, Any] = {
            "schema_version": self.schema_version,
            "account_id": self.account_id,
            "market": self.market,
            "start_date": self.start_date,
            "initial_cash": self.initial_cash,
            "initial_positions": _position_rows(self.initial_positions),
            "cash": self.cash,
            "positions": _position_rows(self.positions),
            "watermark": self.watermark,
            "engine_fingerprint": self.engine_fingerprint,
            "created_at": self.created_at,
            "interval": self.interval,
            "path_model": self.path_model,
            "fill_model": self.fill_model,
            "non_trading_days": list(self.non_trading_days),
        }
        if include_v13 or self.has_v13_state:
            document.update(self.v13_state_payload())
        return document

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "PaperTradeAccount":
        """Parse an ``account.json`` document.

        A v1.2 document (no v1.3 keys) loads with the empty defaults; every
        malformed v1.3 field is refused with :class:`AccountError` rather than
        coerced.  Unknown keys are ignored (a newer writer's additive field must
        not brick an older reader); a different ``schema_version`` is refused,
        because the fields this reader understands may no longer mean the same
        thing.
        """
        if not isinstance(document, Mapping):
            raise AccountError(INVALID_ACCOUNT, "account document must be an object")
        version = document.get("schema_version", ACCOUNT_SCHEMA_VERSION)
        if version != ACCOUNT_SCHEMA_VERSION:
            raise AccountError(
                INVALID_ACCOUNT,
                f"unsupported account schema_version {version!r}; "
                f"this reader understands {ACCOUNT_SCHEMA_VERSION!r}",
            )
        account_id = validate_account_id(document.get("account_id"))
        market = document.get("market")
        if market not in MARKETS:
            raise AccountError(INVALID_ACCOUNT, f"market must be one of {MARKETS}, got {market!r}")
        watermark = document.get("watermark")
        if watermark is not None:
            watermark = require_date(watermark, "watermark")
        non_trading_days = document.get("non_trading_days") or []
        if not isinstance(non_trading_days, (list, tuple)):
            raise AccountError(INVALID_ACCOUNT, "non_trading_days must be an array")
        days: list[str] = []
        for index, day in enumerate(non_trading_days):
            days.append(require_date(day, f"non_trading_days[{index}]"))
        open_orders = _open_orders(document.get("open_orders"), "open_orders", account_id)
        order_seq = _require_int(document.get("order_seq", 0), "order_seq")
        if open_orders:
            highest = max(order.seq for order in open_orders)
            if order_seq <= highest:
                raise AccountError(
                    INVALID_ACCOUNT,
                    f"order_seq must be greater than every open order seq ({highest}), "
                    f"got {order_seq}",
                )
        return cls(
            account_id=account_id,
            market=market,
            start_date=require_date(document.get("start_date"), "start_date"),
            initial_cash=_require_number(document.get("initial_cash"), "initial_cash", minimum=0.0),
            initial_positions=_positions(document.get("initial_positions"), "initial_positions"),
            cash=_require_number(document.get("cash"), "cash", minimum=0.0),
            positions=_positions(document.get("positions"), "positions"),
            watermark=watermark,
            engine_fingerprint=str(document.get("engine_fingerprint") or ""),
            created_at=str(document.get("created_at") or ""),
            interval=str(document.get("interval") or "5m"),
            path_model=str(document.get("path_model") or "bar_median"),
            fill_model=str(document.get("fill_model") or "all_or_none"),
            schema_version=version,
            non_trading_days=tuple(days),
            open_orders=open_orders,
            order_seq=order_seq,
            watermark_bar=_bar_label_or_empty(document.get("watermark_bar", ""), "watermark_bar"),
            slippage_model=_choice_or_empty(
                document.get("slippage_model", ""), "slippage_model", SLIPPAGE_MODELS
            ),
            session_phase=_choice_or_empty(
                document.get("session_phase", ""), "session_phase", SESSION_PHASES
            ),
            bought_today=_bought_today(document.get("bought_today"), "bought_today"),
        )


def create_account(
    *,
    account_id: str,
    market: str,
    start_date: str,
    initial_cash: float,
    initial_positions: Any = (),
    today: str,
    next_trading_day: str,
    now: str,
    engine_fingerprint: str = "",
    interval: str = "5m",
    path_model: str = "bar_median",
    fill_model: str = "all_or_none",
    slippage_model: str = "",
) -> PaperTradeAccount:
    """Build a fresh account, enforcing the start-date rule (planbook §0.2).

    ``today`` and ``next_trading_day`` are BOTH injected — this function never
    reads a clock, so "today" is whatever the caller says it is and the same
    call is reproducible in tests and in production.

    ``slippage_model`` is the pt-v1.3 execution model resolved at creation
    (``""`` = not set); it is checked against the frozen vocabulary here, so a
    typo can never survive until the first bar step.

    Raises :class:`AccountError` with ``code`` in
    ``{start_date_in_past, start_date_not_admissible, invalid_account,
    invalid_initial_positions}``.
    """
    account_id = validate_account_id(account_id)
    if market not in MARKETS:
        raise AccountError(INVALID_ACCOUNT, f"market must be one of {MARKETS}, got {market!r}")
    start = require_date(start_date, "start_date")
    today_ = require_date(today, "today")
    next_day = require_date(next_trading_day, "next_trading_day")
    if not isinstance(now, str) or not now:
        raise AccountError(INVALID_ACCOUNT, "now must be a non-empty ISO-8601 string")

    if start < today_:
        raise AccountError(
            START_DATE_IN_PAST,
            f"start_date {start} precedes today {today_}: a past start is a backtest, "
            "not a paper trade (planbook §0.2)",
        )
    if start > today_ and start != next_day:
        raise AccountError(
            START_DATE_NOT_ADMISSIBLE,
            f"start_date {start} must be today {today_} or the next trading day {next_day}; "
            "an arbitrary future date is not an admissible tracking start",
        )

    cash = _require_number(initial_cash, "initial_cash", minimum=0.0)
    positions = _positions(list(initial_positions or ()), "initial_positions")
    symbols = [item.symbol for item in positions]
    if len(symbols) != len(set(symbols)):
        raise AccountError(
            INVALID_INITIAL_POSITIONS,
            "initial_positions must not list the same symbol twice: "
            f"{sorted({s for s in symbols if symbols.count(s) > 1})}",
        )
    for index, item in enumerate(positions):
        if item.qty <= 0:
            raise AccountError(
                INVALID_INITIAL_POSITIONS,
                f"initial_positions[{index}].qty must be > 0, got {item.qty}",
            )
        if item.avg_cost < 0:
            raise AccountError(
                INVALID_INITIAL_POSITIONS,
                f"initial_positions[{index}].avg_cost must be >= 0, got {item.avg_cost}",
            )

    if interval not in INTERVALS:
        raise AccountError(
            INVALID_ACCOUNT, f"interval must be one of {INTERVALS}, got {interval!r}"
        )
    if interval == "5m" and market != "cn_a":
        # D5: 5m is cn_a-only in pt-v1.2; creating an incoherent account would
        # fail only at the first step, after the operator believes it exists.
        raise AccountError(
            INVALID_ACCOUNT, f"interval 5m is cn_a-only in pt-v1.2, got market {market!r}"
        )
    if path_model not in PATH_MODELS:
        raise AccountError(
            INVALID_ACCOUNT, f"path_model must be one of {PATH_MODELS}, got {path_model!r}"
        )
    if fill_model not in FILL_MODELS:
        raise AccountError(
            INVALID_ACCOUNT, f"fill_model must be one of {FILL_MODELS}, got {fill_model!r}"
        )
    slippage_model = _choice_or_empty(slippage_model, "slippage_model", SLIPPAGE_MODELS)

    return PaperTradeAccount(
        account_id=account_id,
        market=market,
        start_date=start,
        initial_cash=cash,
        initial_positions=positions,
        cash=cash,
        positions=positions,
        watermark=None,
        engine_fingerprint=str(engine_fingerprint or ""),
        created_at=now,
        interval=interval,
        path_model=path_model,
        fill_model=fill_model,
        non_trading_days=(),
        slippage_model=slippage_model,
    )
