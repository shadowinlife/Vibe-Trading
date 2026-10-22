"""pt-v1.3 additive contract — per-bar order lifecycle for ``paper_trade_step``.

pt-v1.3 is the second ADDITIVE bump of the frozen pt-v1.1 contract (P1
intraday plan, GAP-2 + GAP-6).  It keeps every pt-v1.1/pt-v1.2 shape and
default untouched and adds one clock-free request/result pair for a single
5-minute bar:

* the request adds ``bar_time`` (an aware ``+08:00`` timestamp supplied by the
  caller — the package never reads a wall clock), the per-bar
  ``order_actions`` / ``cancel_order_ids`` / ``replace_orders`` channels, the
  carried ``open_orders`` + ``order_seq`` book, an explicit ``slippage`` spec,
  the ``session_phase`` reported by the clock and the optional
  ``prev_closes`` band input (integer cents per symbol);
* the result adds ``order_events``, ``open_orders``, ``order_seq``,
  ``bar_time`` and the echoed ``slippage`` on top of the existing v1.2 result
  document (composed as :attr:`BarStepResult.result`, never duplicated).

Selection follows the same D8 rule as pt-v1.2: the v1.3 surface is chosen by
the presence of a v1.3-only request field, never by a version flag the caller
sets.  :func:`~.schema.parse_request` rejects any v1.3-only key, so a
v1.1/v1.2 call can never half-parse one; :func:`parse_bar_step_request` is the
only entry that turns them into typed values.

Design choices recorded here so the later P1 modules (orders, slippage, clock,
engine, wire) share one contract:

* ``OrderAction`` is ONE flat dataclass discriminated by ``action`` rather
  than three sub-shapes: the wire carries a single ``order_actions`` array,
  and a flat shape keeps :func:`parse_order_action` exhaustive over the three
  members of :data:`ORDER_ACTIONS` without a second union layer.
* Money boundary: ``qty``/``limit`` stay ``float`` because the frozen v1.1
  wire uses JSON numbers and the engine already consumes those; slippage
  parameters are ``Decimal`` because they are added to a price and a Decimal
  is the only exact representation.  :data:`DEFAULT_SLIPPAGE_BPS` is
  therefore a string, never a float — ``Decimal(DEFAULT_SLIPPAGE_BPS)`` is
  exact and a float default could not be.
* ``BarStepResult`` composes :class:`~.schema.PaperTradeStepResult` instead of
  re-declaring its fourteen fields: the v1.2 document stays the single source
  of truth for fills/rejections/state, and the v1.3 keys are purely additive.
* ``session_phase=""`` means "the caller did not classify this bar"; the clock
  layer (``bar_clock.classify``) derives it from ``bar_time``.  It is never
  silently treated as ``continuous``: matching gates must fail closed on "".

Import policy: this module builds on the frozen base shapes in
:mod:`~.schema`.  ``schema`` re-exports these names lazily (PEP 562 module
``__getattr__``), so ``from .schema import BarStepRequest`` works and either
module can be imported first without an import cycle.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from .schema import (
    DEFAULT_FILL_MODEL,
    DEFAULT_INTERVAL,
    DEFAULT_PATH_MODEL,
    DEFAULT_SESSION,
    FILL_MODELS,
    INTERVALS,
    MARKETS,
    PATH_MODELS,
    SCHEMA_VERSION_V1_3,
    SESSIONS,
    SIDES,
    AccountState,
    PaperTradeSchemaError,
    PaperTradeStepResult,
    _parse_choice,
    _parse_date,
    _parse_fee_model,
    _require_number,
    _require_str,
    parse_account_state,
)
from .schema_bar_inputs import parse_bars, parse_prev_closes, require_non_empty_str

__all__ = [
    "BAR_STEP_MODES",
    "DEFAULT_BAR_STEP_MODE",
    "DEFAULT_SLIPPAGE_BPS",
    "DEFAULT_SLIPPAGE_MODEL",
    "DEFAULT_TIF",
    "ORDER_ACTIONS",
    "ORDER_EVENT_TYPES",
    "ORDER_STATUSES",
    "SESSION_PHASES",
    "SLIPPAGE_MODELS",
    "TIFS",
    "BarStepRequest",
    "BarStepResult",
    "OrderAction",
    "OrderState",
    "SlippageSpec",
    "parse_bar_step_request",
    "parse_bar_time",
    "parse_order_action",
    "parse_order_state",
    "parse_slippage",
]

# ── pt-v1.3 vocabularies (ADDITIVE; the frozen AM-14 tables are untouched) ──

#: ``OrderState.status`` / ``open_orders[].status`` — the order lifecycle.
#: ``new`` is the only non-terminal member; ``filled``/``canceled``/
#: ``rejected``/``expired`` are terminal and can never transition again.
ORDER_STATUSES: tuple[str, ...] = ("new", "filled", "canceled", "rejected", "expired")

#: ``mode`` — ``live`` applies the bar to the STORED account; ``replay``
#: applies it to the carried ``account_state`` with no store, network or clock.
BAR_STEP_MODES: tuple[str, ...] = ("live", "replay")
DEFAULT_BAR_STEP_MODE = "live"

#: ``order_events[].event_type`` — the per-bar lifecycle facts, in occurrence
#: order.  One shared vocabulary: the order book, the wire serializer and the
#: contract JSON (``paper_trade_step-v1.3.json``) all read this tuple, so an
#: event token can never drift between Python and the schema.
ORDER_EVENT_TYPES: tuple[str, ...] = (
    "placed",
    "canceled",
    "replaced",
    "filled",
    "rejected",
    "expired",
)

#: ``OrderState.tif`` — time in force, bounded by the session (no GTD/overnight
#: in P1).  ``day`` rests until the 15:00 expiry, ``ioc``/``fok`` terminate in
#: the bar that received them.
TIFS: tuple[str, ...] = ("day", "ioc", "fok")

#: Default TIF as a vocabulary token (a string for the same reason the
#: pt-v1.2 defaults are strings: it is compared by equality with the JSON
#: value, never coerced).
DEFAULT_TIF = "day"

#: ``OrderAction.action`` — the three per-bar order intents (the discriminated
#: union of the single flat :class:`OrderAction` shape).
ORDER_ACTIONS: tuple[str, ...] = ("place", "cancel", "replace")

#: ``SlippageSpec.model`` — adverse-only execution-price models.  ``none`` is
#: the zero-cost control, ``fixed_bps`` the P1 default (10 bps), and
#: ``fixed_amount`` an absolute money offset per share.
SLIPPAGE_MODELS: tuple[str, ...] = ("none", "fixed_bps", "fixed_amount")

DEFAULT_SLIPPAGE_MODEL = "fixed_bps"

#: Default ``fixed_bps`` value as a DECIMAL STRING.  Slippage is money math:
#: ``Decimal(DEFAULT_SLIPPAGE_BPS)`` is exactly 10, while a float default
#: (``10.0``) would reintroduce binary rounding at the first multiplication.
DEFAULT_SLIPPAGE_BPS = "10"

#: ``session_phase`` — the four phase tokens ``bar_clock.classify`` returns.
#: ``opening_auction_print`` is the excluded 09:30 print, ``auction_window``
#: the excluded 14:57-15:00 close auction, ``outside`` any label that belongs
#: to neither the continuous session nor an auction, and ``continuous`` the
#: only phase that matches orders.
SESSION_PHASES: tuple[str, ...] = (
    "continuous",
    "opening_auction_print",
    "auction_window",
    "outside",
)

#: A bar label is ``HH:MM`` inside one session day (``09:35`` .. ``15:00``).
_BAR_LABEL_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

#: Canonical Asia/Shanghai offset.  The contract has no IANA-name field, so
#: the only accepted identity is this offset; any other offset is rejected.
_SHANGHAI_OFFSET = timedelta(hours=8)

#: Plain decimal grammar for money strings — no exponents, no ``NaN``, no
#: ``Infinity``, no leading ``+``.  Deterministic in, canonical out.
_DECIMAL_TEXT_RE = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")


# ── pt-v1.3 shapes ──


@dataclass(frozen=True, slots=True)
class OrderAction:
    """One per-bar order intent, discriminated by ``action``.

    The action-specific invariants are enforced by :func:`parse_order_action`:

    * ``place`` — ``symbol`` non-empty, ``side`` in :data:`~.schema.SIDES`,
      ``qty`` > 0, optional positive ``limit``; the engine assigns the
      ``order_id`` (a caller-supplied one is rejected), while
      ``client_order_id`` is the optional dedupe key.
    * ``cancel`` — target by ``order_id`` or ``client_order_id``; every
      quantity/price field must be absent.
    * ``replace`` — target by ``order_id`` or ``client_order_id``; at least one
      of ``replace_qty``/``replace_limit``, each positive; ``qty``/``limit``/
      ``side`` must be absent so an intent can never carry two meanings.
    """

    action: str
    symbol: str
    side: str = ""
    qty: float = 0.0
    limit: float | None = None
    order_id: str = ""
    client_order_id: str = ""
    tif: str = DEFAULT_TIF
    replace_qty: float | None = None
    replace_limit: float | None = None


@dataclass(frozen=True, slots=True)
class OrderState:
    """One order-book row (open or terminal), carried across bars.

    ``filled_qty + remaining_qty`` must equal ``qty``; ``status="filled"``
    implies ``remaining_qty == 0`` and ``status="new"`` implies
    ``remaining_qty > 0`` (a ``new`` order with nothing left is a lie).
    ``seq`` is the deterministic per-account sequence embedded in ``order_id``
    (``...:<seq:08d>``); ``order_seq`` on the request is the next free value.
    """

    order_id: str
    account_id: str
    trade_date: str
    symbol: str
    side: str
    qty: float
    remaining_qty: float
    limit: float | None
    tif: str
    status: str
    bar_label: str
    client_order_id: str = ""
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    seq: int = 0


@dataclass(frozen=True, slots=True)
class SlippageSpec:
    """Explicit adverse execution-price model applied to every fill reference.

    ``bps`` and ``amount`` are ``Decimal`` (never float) because they are
    money arithmetic; :meth:`as_wire` renders them as canonical decimal
    strings for the JSON echo.  Exactly one knob may be non-zero, matching the
    selected ``model`` (see :func:`parse_slippage`).
    """

    model: str = DEFAULT_SLIPPAGE_MODEL
    bps: Decimal = Decimal(DEFAULT_SLIPPAGE_BPS)
    amount: Decimal = Decimal("0")

    def as_wire(self) -> dict[str, str]:
        """Deterministic JSON-safe echo: ``{"model", "bps", "amount"}``."""
        return {
            "model": self.model,
            "bps": _decimal_text(self.bps),
            "amount": _decimal_text(self.amount),
        }


@dataclass(frozen=True, slots=True)
class BarStepRequest:
    """Parsed pt-v1.3 in-params for one 5-minute bar.

    Mirrors the pt-v1.2 identity/session request fields (``account_id``,
    ``market``, ``account_state``, ``from_date``/``to_date``, ``fee_model``,
    ``interval``, ``session``, ``path_model``, ``fill_model``) and replaces the
    day-level ``orders``/``strategy_path`` batch with the per-bar
    ``order_actions`` channel: a bar step has exactly one order surface, and
    accepting both would allow a silent half-parse (the parser rejects the
    v1.2 order channels explicitly).

    A bar step covers exactly one session day, so ``from_date == to_date`` and
    ``bar_time`` must fall on that date.  ``schema_version`` is always
    ``"pt-v1.3"``.  ``mode`` selects the ledger authority and ``bars`` injects
    the 5m rows (``replay`` requires them).

    ``prev_closes`` carries the previous session's daily close per symbol as
    INTEGER CENTS; it is the band input from which the cn_a ``limit_up`` /
    ``limit_down`` slots are derived.  It is OPTIONAL and additive: when it is
    absent the band is UNKNOWN and the band slots are skipped (never a
    fabricated limit).  :meth:`prev_close_yuan` is the single conversion the
    band heuristic consumes.
    """

    account_id: str
    market: str
    account_state: AccountState
    from_date: str
    to_date: str
    bar_time: str
    fee_model: dict[str, float] = field(default_factory=dict)
    interval: str = DEFAULT_INTERVAL
    session: str = DEFAULT_SESSION
    path_model: str = DEFAULT_PATH_MODEL
    fill_model: str = DEFAULT_FILL_MODEL
    order_actions: tuple[OrderAction, ...] = ()
    cancel_order_ids: tuple[str, ...] = ()
    replace_orders: tuple[OrderAction, ...] = ()
    slippage: SlippageSpec = field(default_factory=SlippageSpec)
    session_phase: str = ""
    open_orders: tuple[OrderState, ...] = ()
    order_seq: int = 0
    mode: str = DEFAULT_BAR_STEP_MODE
    bars: tuple[Mapping[str, Any], ...] = ()
    prev_closes: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))

    def prev_close_yuan(self) -> dict[str, float]:
        """The canonical cent map as yuan floats for the band heuristic.

        The wire spelling is cents; :func:`~.fill_rules.band_for` speaks yuan.
        This is the single conversion, done in ``Decimal`` so no binary float
        artifact can reach the band arithmetic.
        """
        return {symbol: float(Decimal(cents) / 100) for symbol, cents in self.prev_closes.items()}

    @property
    def trade_date(self) -> str:
        """The single session day this bar belongs to."""
        return self.from_date

    @property
    def schema_version(self) -> str:
        """The wire ``schema_version`` this request's result must carry."""
        return SCHEMA_VERSION_V1_3


@dataclass(frozen=True, slots=True)
class BarStepResult:
    """pt-v1.3 out-param: the v1.2 result document plus the per-bar state.

    Composition (``result``) keeps :class:`~.schema.PaperTradeStepResult` the
    only declaration of the frozen v1.2 fields; the additive keys below are
    what the bar engine, the wire serializer and the store consume.
    ``order_events`` rows are the ordered lifecycle facts of THIS bar; their
    key vocabulary is owned by the order book, not pinned here.
    ``slippage`` is the canonical string echo of the applied
    :class:`SlippageSpec` (money never leaves as a float).
    """

    result: PaperTradeStepResult
    bar_time: str
    order_events: tuple[dict[str, Any], ...] = ()
    open_orders: tuple[OrderState, ...] = ()
    order_seq: int = 0
    slippage: dict[str, str] = field(default_factory=lambda: SlippageSpec().as_wire())
    schema_version: str = SCHEMA_VERSION_V1_3


# ── Boundary parsers (fail closed, deterministic, path-named) ──


def _require_non_negative_number(value: Any, path: str) -> float:
    number = _require_number(value, path)
    if number < 0:
        raise PaperTradeSchemaError(f"{path} must be >= 0, got {value!r}")
    return number


def _require_positive_number(value: Any, path: str) -> float:
    number = _require_number(value, path)
    if number <= 0:
        raise PaperTradeSchemaError(f"{path} must be > 0, got {value!r}")
    return number


def _require_non_negative_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PaperTradeSchemaError(f"{path} must be an integer, got {type(value).__name__}")
    if value < 0:
        raise PaperTradeSchemaError(f"{path} must be >= 0, got {value}")
    return value


def _decimal_text(value: Decimal) -> str:
    """Canonical plain-decimal text: ``Decimal("10.0")`` -> ``"10"``."""
    if not value:
        return "0"
    return format(value.normalize(), "f")


def _decimal_from_json(value: Any, path: str) -> Decimal:
    """Parse a money value from a JSON number or a plain decimal string."""
    if isinstance(value, bool):
        raise PaperTradeSchemaError(f"{path} must be a JSON number or a decimal string, got bool")
    if isinstance(value, str):
        if not _DECIMAL_TEXT_RE.match(value):
            raise PaperTradeSchemaError(
                f"{path} must be a plain decimal string, got {value!r}"
            )
        return Decimal(value)
    if isinstance(value, (int, float)):
        return Decimal(str(_require_number(value, path)))
    raise PaperTradeSchemaError(
        f"{path} must be a JSON number or a decimal string, got {type(value).__name__}"
    )


def parse_bar_time(value: Any, path: str = "bar_time", *, trade_date: str | None = None) -> str:
    """Return the canonical aware ``+08:00`` ISO-8601 form of a bar time.

    Rejected with :class:`PaperTradeSchemaError` (never coerced):

    * a non-string, a value that is not ISO-8601, or a date-only string;
    * a NAIVE datetime — every bar time must state its offset;
    * any offset other than Asia/Shanghai's ``+08:00``;
    * a sub-minute timestamp (bars are minute labels, seconds are always 0);
    * a date that disagrees with ``trade_date`` when one is supplied.

    The 5-minute grid is NOT enforced here: ``14:57`` is a legal label (it is
    the close-auction phase, not a continuous bar) and the clock layer owns the
    grid.  Output is canonical, so two spellings of the same instant parse to
    one string: ``"2026-10-20T17:35:00+08:00"`` never leaks through.
    """
    text = _require_str(value, path)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise PaperTradeSchemaError(
            f"{path} must be an ISO-8601 datetime, got {text!r}"
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PaperTradeSchemaError(
            f"{path} must be timezone-aware with a +08:00 offset, got naive {text!r}"
        )
    if parsed.utcoffset() != _SHANGHAI_OFFSET:
        raise PaperTradeSchemaError(
            f"{path} must carry the Asia/Shanghai +08:00 offset, got {text!r}"
        )
    if parsed.second != 0 or parsed.microsecond != 0:
        raise PaperTradeSchemaError(
            f"{path} must be a minute bar label with zero seconds, got {text!r}"
        )
    if trade_date is not None and parsed.date().isoformat() != trade_date:
        raise PaperTradeSchemaError(
            f"{path} date {parsed.date().isoformat()} disagrees with trade_date {trade_date}"
        )
    return f"{parsed.replace(tzinfo=None).isoformat(timespec='seconds')}+08:00"


def parse_slippage(raw: Any, path: str = "slippage") -> SlippageSpec:
    """Parse the ``slippage`` object: ``{"model", "bps", "amount"}``.

    Absent/null returns the default spec (``fixed_bps`` 10 bps).  Values are
    parsed as Decimal money; a negative, non-finite or exponent-shaped value is
    rejected.  Exactly one knob may be non-zero:

    * ``none`` — ``bps`` and ``amount`` must both be 0;
    * ``fixed_bps`` — ``amount`` must be 0 (``bps`` defaults to 10);
    * ``fixed_amount`` — ``bps`` must be 0 (``amount`` defaults to 0).

    Unknown keys are rejected rather than ignored, so a misspelled knob cannot
    silently fall back to the default cost.
    """
    if raw is None:
        return SlippageSpec()
    if not isinstance(raw, dict):
        raise PaperTradeSchemaError(f"{path} must be an object, got {type(raw).__name__}")
    unknown = sorted(set(raw) - {"model", "bps", "amount"})
    if unknown:
        raise PaperTradeSchemaError(
            f"{path} has unknown keys {unknown}; allowed: ['amount', 'bps', 'model']"
        )
    model = _parse_choice(raw.get("model"), f"{path}.model", SLIPPAGE_MODELS, DEFAULT_SLIPPAGE_MODEL)
    bps_raw = raw.get("bps")
    if bps_raw is None:
        bps = Decimal(DEFAULT_SLIPPAGE_BPS) if model == "fixed_bps" else Decimal("0")
    else:
        bps = _decimal_from_json(bps_raw, f"{path}.bps")
    amount_raw = raw.get("amount")
    amount = Decimal("0") if amount_raw is None else _decimal_from_json(amount_raw, f"{path}.amount")
    if bps < 0:
        raise PaperTradeSchemaError(f"{path}.bps must be >= 0, got {_decimal_text(bps)}")
    if amount < 0:
        raise PaperTradeSchemaError(f"{path}.amount must be >= 0, got {_decimal_text(amount)}")
    if model == "none" and (bps != 0 or amount != 0):
        raise PaperTradeSchemaError(f"{path} model 'none' requires bps == 0 and amount == 0")
    if model == "fixed_bps" and amount != 0:
        raise PaperTradeSchemaError(f"{path}.amount must be 0 when model is 'fixed_bps'")
    if model == "fixed_amount" and bps != 0:
        raise PaperTradeSchemaError(f"{path}.bps must be 0 when model is 'fixed_amount'")
    return SlippageSpec(model=model, bps=bps, amount=amount)


def parse_order_action(raw: Any, path: str = "order_actions[]") -> OrderAction:
    """Parse one ``order_actions`` / ``replace_orders`` row.

    Validates the shared field types, then the action-specific invariants
    documented on :class:`OrderAction`.  A cancel/replace target must name an
    ``order_id`` or a ``client_order_id``; an action that carries fields of a
    different action is rejected rather than silently ignored.
    """
    if not isinstance(raw, dict):
        raise PaperTradeSchemaError(f"{path} must be an object, got {type(raw).__name__}")
    action = _require_str(raw.get("action"), f"{path}.action")
    if action not in ORDER_ACTIONS:
        raise PaperTradeSchemaError(f"{path}.action must be one of {ORDER_ACTIONS}, got {action!r}")
    symbol = _require_str(raw.get("symbol", ""), f"{path}.symbol")
    side = _require_str(raw.get("side", ""), f"{path}.side")
    qty = _require_non_negative_number(raw.get("qty", 0.0), f"{path}.qty")
    limit_raw = raw.get("limit")
    limit = None if limit_raw is None else _require_positive_number(limit_raw, f"{path}.limit")
    order_id = _require_str(raw.get("order_id", ""), f"{path}.order_id")
    client_order_id = _require_str(raw.get("client_order_id", ""), f"{path}.client_order_id")
    tif = _parse_choice(raw.get("tif"), f"{path}.tif", TIFS, DEFAULT_TIF)
    replace_qty_raw = raw.get("replace_qty")
    replace_qty = (
        None if replace_qty_raw is None else _require_positive_number(replace_qty_raw, f"{path}.replace_qty")
    )
    replace_limit_raw = raw.get("replace_limit")
    replace_limit = (
        None
        if replace_limit_raw is None
        else _require_positive_number(replace_limit_raw, f"{path}.replace_limit")
    )

    if action == "place":
        if not symbol:
            raise PaperTradeSchemaError(f"{path}.symbol must be a non-empty string for a place action")
        if side not in SIDES:
            raise PaperTradeSchemaError(f"{path}.side must be one of {SIDES}, got {side!r}")
        if qty <= 0:
            raise PaperTradeSchemaError(f"{path}.qty must be > 0 for a place action")
        if order_id:
            raise PaperTradeSchemaError(
                f"{path}.order_id must be empty for a place action; the engine assigns it"
            )
        if replace_qty is not None or replace_limit is not None:
            raise PaperTradeSchemaError(
                f"{path}.replace_qty/replace_limit are only valid for a replace action"
            )
    elif action == "cancel":
        if not order_id and not client_order_id:
            raise PaperTradeSchemaError(
                f"{path} cancel requires order_id or client_order_id"
            )
        if qty != 0 or limit is not None or replace_qty is not None or replace_limit is not None:
            raise PaperTradeSchemaError(f"{path} cancel must carry no qty/limit/replace fields")
        if side:
            raise PaperTradeSchemaError(f"{path}.side must be empty for a cancel action")
        if tif != DEFAULT_TIF:
            raise PaperTradeSchemaError(f"{path}.tif must be {DEFAULT_TIF!r} for a cancel action")
    else:  # replace — the vocabulary check above makes this exhaustive.
        if not order_id and not client_order_id:
            raise PaperTradeSchemaError(
                f"{path} replace requires order_id or client_order_id"
            )
        if replace_qty is None and replace_limit is None:
            raise PaperTradeSchemaError(
                f"{path} replace requires replace_qty and/or replace_limit"
            )
        if side or qty != 0 or limit is not None:
            raise PaperTradeSchemaError(f"{path} replace must carry no side/qty/limit fields")
        if tif != DEFAULT_TIF:
            raise PaperTradeSchemaError(f"{path}.tif must be {DEFAULT_TIF!r} for a replace action")

    return OrderAction(
        action=action,
        symbol=symbol,
        side=side,
        qty=qty,
        limit=limit,
        order_id=order_id,
        client_order_id=client_order_id,
        tif=tif,
        replace_qty=replace_qty,
        replace_limit=replace_limit,
    )


def parse_order_state(raw: Any, path: str = "open_orders[]") -> OrderState:
    """Parse one carried ``OrderState`` row (open or terminal).

    Enforces the book invariants: quantity conservation
    (``filled_qty + remaining_qty == qty``), ``remaining_qty <= qty``, a
    terminal ``filled`` row with nothing left, a ``new`` row with something
    left, a positive ``limit`` when present, and an ``HH:MM`` bar label.
    """
    if not isinstance(raw, dict):
        raise PaperTradeSchemaError(f"{path} must be an object, got {type(raw).__name__}")
    side = _require_str(raw.get("side"), f"{path}.side")
    if side not in SIDES:
        raise PaperTradeSchemaError(f"{path}.side must be one of {SIDES}, got {side!r}")
    tif = _require_str(raw.get("tif"), f"{path}.tif")
    if tif not in TIFS:
        raise PaperTradeSchemaError(f"{path}.tif must be one of {TIFS}, got {tif!r}")
    status = _require_str(raw.get("status"), f"{path}.status")
    if status not in ORDER_STATUSES:
        raise PaperTradeSchemaError(
            f"{path}.status must be one of {ORDER_STATUSES}, got {status!r}"
        )
    qty = _require_positive_number(raw.get("qty"), f"{path}.qty")
    remaining = _require_non_negative_number(raw.get("remaining_qty"), f"{path}.remaining_qty")
    filled = _require_non_negative_number(raw.get("filled_qty", 0.0), f"{path}.filled_qty")
    if remaining > qty:
        raise PaperTradeSchemaError(f"{path}.remaining_qty must be <= qty")
    if filled > qty:
        raise PaperTradeSchemaError(f"{path}.filled_qty must be <= qty")
    if not math.isclose(filled + remaining, qty, rel_tol=1e-9, abs_tol=1e-9):
        raise PaperTradeSchemaError(
            f"{path} filled_qty + remaining_qty must equal qty, "
            f"got {filled!r} + {remaining!r} != {qty!r}"
        )
    if status == "filled" and remaining > 0:
        raise PaperTradeSchemaError(f"{path}.remaining_qty must be 0 for a filled order")
    if status == "new" and remaining <= 0:
        raise PaperTradeSchemaError(f"{path}.remaining_qty must be > 0 for a new order")
    limit_raw = raw.get("limit")
    limit = None if limit_raw is None else _require_positive_number(limit_raw, f"{path}.limit")
    bar_label = _require_str(raw.get("bar_label"), f"{path}.bar_label")
    if not _BAR_LABEL_RE.match(bar_label):
        raise PaperTradeSchemaError(f"{path}.bar_label must be HH:MM, got {bar_label!r}")
    return OrderState(
        order_id=require_non_empty_str(raw.get("order_id"), f"{path}.order_id"),
        account_id=require_non_empty_str(raw.get("account_id"), f"{path}.account_id"),
        trade_date=_parse_date(raw.get("trade_date"), f"{path}.trade_date"),
        symbol=require_non_empty_str(raw.get("symbol"), f"{path}.symbol"),
        side=side,
        qty=qty,
        remaining_qty=remaining,
        limit=limit,
        tif=tif,
        status=status,
        bar_label=bar_label,
        client_order_id=_require_str(raw.get("client_order_id", ""), f"{path}.client_order_id"),
        filled_qty=filled,
        avg_fill_price=_require_non_negative_number(
            raw.get("avg_fill_price", 0.0), f"{path}.avg_fill_price"
        ),
        seq=_require_non_negative_int(raw.get("seq", 0), f"{path}.seq"),
    )


def _parse_order_actions(payload: dict[str, Any], key: str) -> tuple[OrderAction, ...]:
    raw = payload.get(key, [])
    if not isinstance(raw, list):
        raise PaperTradeSchemaError(f"{key} must be an array")
    return tuple(parse_order_action(item, f"{key}[{i}]") for i, item in enumerate(raw))


def _parse_cancel_ids(payload: dict[str, Any]) -> tuple[str, ...]:
    raw = payload.get("cancel_order_ids", [])
    if not isinstance(raw, list):
        raise PaperTradeSchemaError("cancel_order_ids must be an array")
    seen: list[str] = []
    for i, item in enumerate(raw):
        order_id = require_non_empty_str(item, f"cancel_order_ids[{i}]")
        if order_id in seen:
            raise PaperTradeSchemaError(f"cancel_order_ids[{i}] duplicates order id {order_id!r}")
        seen.append(order_id)
    return tuple(seen)


def _parse_open_orders(payload: dict[str, Any]) -> tuple[OrderState, ...]:
    raw = payload.get("open_orders", [])
    if not isinstance(raw, list):
        raise PaperTradeSchemaError("open_orders must be an array")
    orders = tuple(parse_order_state(item, f"open_orders[{i}]") for i, item in enumerate(raw))
    order_ids = [order.order_id for order in orders]
    if len(set(order_ids)) != len(order_ids):
        raise PaperTradeSchemaError("open_orders contains a duplicate order_id")
    client_ids = [order.client_order_id for order in orders if order.client_order_id]
    if len(set(client_ids)) != len(client_ids):
        raise PaperTradeSchemaError("open_orders contains a duplicate client_order_id")
    return orders


def _parse_request_slippage(payload: dict[str, Any]) -> SlippageSpec:
    if "slippage" in payload and "slippage_model" in payload:
        raise PaperTradeSchemaError("slippage and slippage_model are mutually exclusive")
    if "slippage" in payload:
        return parse_slippage(payload["slippage"], "slippage")
    if "slippage_model" in payload:
        shorthand: dict[str, Any] = {"model": payload["slippage_model"]}
        if "slippage_bps" in payload:
            shorthand["bps"] = payload["slippage_bps"]
        if "slippage_amount" in payload:
            shorthand["amount"] = payload["slippage_amount"]
        return parse_slippage(shorthand, "slippage")
    return SlippageSpec()


def parse_bar_step_request(payload: dict[str, Any]) -> BarStepRequest:
    """Parse the pt-v1.3 in-params for one bar (fail closed, path-named).

    Shape-level parse only: types, enums, date/time canonicalization, book
    and action invariants.  Semantic gates (cn_a-only 5m, marketability,
    price bands) stay with the tool/engine, exactly as pt-v1.2's
    :func:`~.schema.parse_request` leaves AM-3 to ``tool.py``.
    """
    if not isinstance(payload, dict):
        raise PaperTradeSchemaError(f"request must be an object, got {type(payload).__name__}")
    for legacy in ("orders", "strategy_path"):
        if legacy in payload:
            raise PaperTradeSchemaError(
                f"{legacy} is not part of the pt-v1.3 bar-step contract; use order_actions"
            )
    market = _require_str(payload.get("market"), "market")
    if market not in MARKETS:
        raise PaperTradeSchemaError(f"market must be one of {MARKETS}, got {market!r}")
    from_date = _parse_date(payload.get("from_date"), "from_date")
    to_date = _parse_date(payload.get("to_date"), "to_date")
    if from_date != to_date:
        raise PaperTradeSchemaError(
            f"a bar step covers one session day, got from_date {from_date} != to_date {to_date}"
        )
    if payload.get("bar_time") is None:
        raise PaperTradeSchemaError("bar_time is required for a pt-v1.3 bar step")
    bar_time = parse_bar_time(payload["bar_time"], trade_date=from_date)

    order_actions = _parse_order_actions(payload, "order_actions")
    cancel_order_ids = _parse_cancel_ids(payload)
    replace_orders = _parse_order_actions(payload, "replace_orders")
    for i, replace in enumerate(replace_orders):
        if replace.action != "replace":
            raise PaperTradeSchemaError(
                f"replace_orders[{i}].action must be 'replace', got {replace.action!r}"
            )

    targets = [action.order_id or action.client_order_id for action in order_actions if action.action != "place"]
    targets += [action.order_id or action.client_order_id for action in replace_orders]
    targets += list(cancel_order_ids)
    duplicates = sorted({target for target in targets if targets.count(target) > 1})
    if duplicates:
        raise PaperTradeSchemaError(
            "order id(s) "
            f"{duplicates} are targeted more than once across "
            "order_actions/cancel_order_ids/replace_orders"
        )

    open_orders = _parse_open_orders(payload)
    order_seq = _require_non_negative_int(payload.get("order_seq", 0), "order_seq")
    if open_orders:
        highest = max(order.seq for order in open_orders)
        if order_seq <= highest:
            raise PaperTradeSchemaError(
                f"order_seq must be greater than every open order seq ({highest}), got {order_seq}"
            )
    mode = _parse_choice(payload.get("mode"), "mode", BAR_STEP_MODES, DEFAULT_BAR_STEP_MODE)
    bars = parse_bars(payload.get("bars"))
    prev_closes = parse_prev_closes(payload.get("prev_closes"))
    if mode == "replay" and not bars:
        raise PaperTradeSchemaError("mode 'replay' requires bars: a replayed bar may not read the store")

    return BarStepRequest(
        account_id=_require_str(payload.get("account_id"), "account_id"),
        market=market,
        account_state=parse_account_state(payload.get("account_state"), "account_state"),
        from_date=from_date,
        to_date=to_date,
        bar_time=bar_time,
        fee_model=_parse_fee_model(payload),
        interval=_parse_choice(payload.get("interval"), "interval", INTERVALS, DEFAULT_INTERVAL),
        session=_parse_choice(payload.get("session"), "session", SESSIONS, DEFAULT_SESSION),
        path_model=_parse_choice(
            payload.get("path_model"), "path_model", PATH_MODELS, DEFAULT_PATH_MODEL
        ),
        fill_model=_parse_choice(
            payload.get("fill_model"), "fill_model", FILL_MODELS, DEFAULT_FILL_MODEL
        ),
        order_actions=order_actions,
        cancel_order_ids=cancel_order_ids,
        replace_orders=replace_orders,
        slippage=_parse_request_slippage(payload),
        session_phase=_parse_choice(
            payload.get("session_phase"), "session_phase", SESSION_PHASES, ""
        ),
        open_orders=open_orders,
        order_seq=order_seq,
        mode=mode,
        bars=bars,
        prev_closes=prev_closes,
    )
