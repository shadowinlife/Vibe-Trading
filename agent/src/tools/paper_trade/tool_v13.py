"""pt-v1.3 tool entry points: account create, bar step and account status.

Split out of :mod:`~.tool` for the community line cap (the pt-v1.1/v1.2 day
step stays under 800 lines) and re-exported there, so both registration
surfaces keep importing ``src.tools.paper_trade.tool`` — the MCP wrappers in
``agent/mcp_server.py`` and the agent registry.

* :func:`run_paper_trade_account_create` is the ONLY account-creation surface.
  It builds the account through :func:`~.account.create_account` — the single
  validation authority for the start-date rule, 5m-is-cn_a-only, duplicate
  initial positions, positive qty and the frozen vocabularies — then commits
  it through :meth:`~.store.AccountStore.create`, whose lock-guarded
  :class:`~.store.AccountExists` is the authoritative duplicate guard.  A
  caller-supplied ``existing_account_ids`` is only a cheap pre-check that
  preserves the pre-store contract's early error; it never replaces the store
  guard and it is never a check-then-write race.
* :func:`run_paper_trade_bar_step` is one pt-v1.3 bar.  It parses the request
  and hands it to the runtime installed at :data:`_V13_BAR_RUNTIME`; with no
  runtime installed a well-formed request is answered with an in-band
  ``INVALID_REQUEST`` naming that fact, never a success-shaped document.
* :func:`run_paper_trade_account_status` validates identity and market, then
  reads cash, positions, open orders and the applied-bar watermark through the
  same runtime seam.

The runtime installed at :data:`_V13_BAR_RUNTIME` is
:mod:`~.mcp_runtime`'s: ``live`` advances the stored account through
:mod:`~.session_runner` (one bar per call, ``applied`` / ``adopted`` /
``bar_already_applied`` idempotency, the caller-injected ``bar_time``, no wall
clock), while ``replay`` applies the carried ledger and the injected bars with
no store access.  While the seam is uninstalled, the bar and status entry
points refuse in-band instead of fabricating a success.

Determinism (R1-1): **no clock**.  ``today`` and ``next_trading_day`` are
injected strings; ``created_at`` is derived from ``today``
(``<today>T00:00:00+08:00``), so the same payload always writes the same
``account.json`` bytes.  A refusal is the same in-band ``INVALID_REQUEST``
envelope the day step uses — never a transport error, never a success-shaped
document.  The store root path is never echoed: the response carries the
account summary only.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Callable

from . import account, wire
from .schema import (
    MARKETS,
    DateRange,
    ErrorEnvelope,
    PaperTradeSchemaError,
    _V13_REQUEST_MARKERS,
    parse_bar_step_request,
)
from .session_documents import SessionRunnerError
from .store import AccountExists, AccountStore, StoreError

if TYPE_CHECKING:
    from .schema import BarStepRequest, BarStepResult

__all__ = [
    "run_paper_trade_account_create",
    "run_paper_trade_account_status",
    "run_paper_trade_bar_step",
]

#: Honest-unavailable answer for the not-yet-wired part of the v1.3 surface.
#: A well-formed request must never be answered with a success-shaped document.
_V13_RUNTIME_UNAVAILABLE = "pt-v1.3 bar runtime is not available in this build"

#: Installation point for the pt-v1.3 bar runtime.  ``None`` routes every
#: bar/status request to the refusal envelope above.
_V13_BAR_RUNTIME: Callable[[BarStepRequest], BarStepResult] | None = None

#: Installation point for the pt-v1.3 status runtime (a full document, not a
#: typed result).  ``None`` routes the status read to the same refusal.
_V13_STATUS_RUNTIME: Callable[[dict[str, Any]], str] | None = None

#: Account-create defaults, applied when the MCP wrapper omits a selector (an
#: omitted optional arrives as ``None``).  An empty string is treated as
#: omitted for every selector because no v1.3 vocabulary admits ``""`` except
#: ``slippage_model``, whose "not set" value IS the empty string.
_CREATE_DEFAULTS: dict[str, str] = {
    "interval": "5m",
    "path_model": "bar_median",
    "fill_model": "all_or_none",
    "slippage_model": "",
}

#: The duplicate message is a stable token + deterministic English (VD-7).  It
#: is the same text the pre-store create returned, so a client that already
#: matches on ``account_already_exists`` keeps working.
_ACCOUNT_EXISTS_PREFIX = "account_already_exists"


def _as_str(value: Any) -> str:
    """Echo a raw payload field as a string, or ``""`` when it is not one."""
    return value if isinstance(value, str) else ""


def _bar_error_document(
    account_id: str,
    market: str,
    from_date: str,
    to_date: str,
    message: str,
) -> str:
    """In-band pt-v1.3 error with ``received_state_hash`` omitted.

    The v1.3 coverage object uses a different preimage from the v1.2 coverage
    and this surface has none of those fields, so the narrower hash would claim
    coverage it does not have (AM-4-1).  AM-4-4 permits omission, so the key is
    absent, never null.
    """
    envelope = ErrorEnvelope(
        code="INVALID_REQUEST",
        account_id=account_id,
        market=market,
        range=DateRange(from_=from_date, to=to_date),
        message=message,
        received_state_hash=None,
    )
    return wire.dumps_payload(wire.build_error_payload(envelope))


def _already_exists_message(account_id: str) -> str:
    """The deterministic duplicate-account refusal (stable token first)."""
    return (
        f"{_ACCOUNT_EXISTS_PREFIX}: account id {account_id!r} already exists (conflict); "
        "the create path never overwrites an existing account"
    )


def _selector(raw_payload: dict[str, Any], key: str) -> Any:
    """The raw selector, defaulted only when the wrapper omitted it.

    ``None`` and ``""`` mean omitted; a non-string value is passed through
    unchanged so :func:`~.account.create_account` refuses it with its own
    vocabulary error instead of this helper coercing it into a valid model.
    """
    value = raw_payload.get(key)
    if value is None or value == "":
        return _CREATE_DEFAULTS[key]
    return value


def run_paper_trade_account_create(
    payload: Any,
    *,
    store: AccountStore | None = None,
    existing_account_ids: Iterable[str] = (),
) -> str:
    """Create a pt-v1.3 paper account; return ONE in-band JSON document.

    ``today`` and ``next_trading_day`` are REQUIRED injected strings — the
    package never reads a clock.  ``created_at`` is derived deterministically
    from ``today``, so the same payload produces the same account bytes.

    The success document is the account summary (``status="created"``) with
    the v1.3 block always present — ``account.open_orders == []`` and
    ``account.order_seq == 0`` are stated, never omitted (an absent
    ``open_orders`` would read as "unknown", not "empty").

    Every refusal is the in-band ``INVALID_REQUEST`` envelope: an
    :class:`~.account.AccountError` keeps its stable code as the message
    prefix (``start_date_in_past`` / ``start_date_not_admissible`` /
    ``invalid_account`` / ``invalid_initial_positions``), an existing account
    is ``account_already_exists``, and any other store refusal keeps its own
    code.  The store create is the duplicate authority; ``existing_account_ids``
    only preserves the earlier cheap error.
    """
    raw_payload = payload if isinstance(payload, dict) else {}
    account_id = _as_str(raw_payload.get("account_id"))
    market = _as_str(raw_payload.get("market"))
    start_date = _as_str(raw_payload.get("start_date"))

    # ``create_account`` is the single validation authority (start-date rule,
    # interval/market coherence, duplicate symbols, qty > 0, vocabularies).
    try:
        created = account.create_account(
            account_id=raw_payload.get("account_id"),
            market=raw_payload.get("market"),
            start_date=raw_payload.get("start_date"),
            initial_cash=raw_payload.get("initial_cash"),
            initial_positions=raw_payload.get("initial_positions"),
            today=raw_payload.get("today"),
            next_trading_day=raw_payload.get("next_trading_day"),
            now=f"{raw_payload.get('today')}T00:00:00+08:00",
            interval=_selector(raw_payload, "interval"),
            path_model=_selector(raw_payload, "path_model"),
            fill_model=_selector(raw_payload, "fill_model"),
            slippage_model=_selector(raw_payload, "slippage_model"),
        )
    except account.AccountError as exc:
        return _bar_error_document(
            account_id, market, start_date, start_date, f"{exc.code}: {exc.message}"
        )

    # Cheap pre-check only: the caller already knows its account ids, so the
    # conflict can be answered without touching the store.  ``target.create``
    # below is still the authority (a racing create cannot slip past it).
    if created.account_id in set(existing_account_ids):
        return _bar_error_document(
            account_id, market, start_date, start_date, _already_exists_message(created.account_id)
        )

    target = store or AccountStore()
    try:
        target.create(created)
    except AccountExists:
        return _bar_error_document(
            account_id, market, start_date, start_date, _already_exists_message(created.account_id)
        )
    except StoreError as exc:
        return _bar_error_document(
            account_id, market, start_date, start_date, f"{exc.code}: {exc.message}"
        )

    return wire.dumps_payload(
        {
            "schema_version": "pt-v1.3",
            "tool": "paper_trade_account_create",
            "status": "created",
            "account": created.to_dict(include_v13=True),
        }
    )


def _is_bar_step_payload(payload: Any) -> bool:
    """Whether a raw payload selects the pt-v1.3 bar-step surface (D8 rule)."""
    return isinstance(payload, dict) and any(
        marker in payload for marker in _V13_REQUEST_MARKERS
    )


def _run_bar_step_payload(raw_payload: dict[str, Any]) -> str:
    """Parse one pt-v1.3 payload, then hand the typed request to the bar path."""
    try:
        request = parse_bar_step_request(raw_payload)
    except PaperTradeSchemaError as exc:
        return _bar_error_document(
            _as_str(raw_payload.get("account_id")),
            _as_str(raw_payload.get("market")),
            _as_str(raw_payload.get("from_date")),
            _as_str(raw_payload.get("to_date")),
            str(exc),
        )
    return _execute_bar_step(request)


def _execute_bar_step(request: BarStepRequest) -> str:
    """Execute one parsed pt-v1.3 bar step through the installed runtime.

    The runtime receives the parsed request exactly once and its
    :class:`BarStepResult` is serialized through the v1.3 wire builder.  A
    :class:`~.session_documents.SessionRunnerError` keeps its stable code as
    the message prefix (``bar_already_applied`` / ``bar_out_of_order`` /
    ``account_locked`` / ``account_state_stale`` / ...), so a refused bar is
    never mistaken for a success document.  With no runtime installed the
    answer is a deterministic in-band ``INVALID_REQUEST`` naming that fact.
    """
    runtime = _V13_BAR_RUNTIME
    if runtime is None:
        return _bar_error_document(
            request.account_id,
            request.market,
            request.from_date,
            request.to_date,
            _V13_RUNTIME_UNAVAILABLE,
        )
    try:
        bar = runtime(request)
    except SessionRunnerError as exc:
        return _bar_error_document(
            exc.account_id or request.account_id,
            exc.market or request.market,
            exc.from_date or request.from_date,
            exc.to_date or request.to_date,
            f"{exc.code}: {exc.message}",
        )
    return wire.dumps_payload(wire.build_success_payload(bar.result, bar=bar))


def run_paper_trade_bar_step(payload: Any) -> str:
    """Run one pt-v1.3 bar step from a raw payload and return the document.

    Every shape violation — a naive or off-``+08:00`` ``bar_time``, an unknown
    action, a duplicated cancel id — maps to the in-band ``INVALID_REQUEST``
    envelope, never a traceback.
    """
    raw_payload = payload if isinstance(payload, dict) else {}
    return _run_bar_step_payload(raw_payload)


def run_paper_trade_account_status(payload: Any) -> str:
    """Read a pt-v1.3 paper account's status; return the document.

    Validates identity and market, then returns the stored account summary
    through the runtime installed at :data:`_V13_BAR_RUNTIME`; with no runtime
    installed the answer is a deterministic in-band ``INVALID_REQUEST``.
    """
    raw_payload = payload if isinstance(payload, dict) else {}
    account_id = _as_str(raw_payload.get("account_id"))
    market = _as_str(raw_payload.get("market"))
    message: str | None = None
    try:
        account.validate_account_id(raw_payload.get("account_id"))
    except account.AccountError as exc:
        message = str(exc)
    if message is None and market not in MARKETS:
        message = f"market must be one of {MARKETS}, got {raw_payload.get('market')!r}"
    if message is None:
        runtime = _V13_STATUS_RUNTIME
        if runtime is None:
            message = _V13_RUNTIME_UNAVAILABLE
        else:
            return runtime(raw_payload)
    return _bar_error_document(account_id, market, "", "", message)


def _install_runtime() -> None:
    """Install the MCP runtime on both seams at import (tests can override)."""
    from . import mcp_runtime

    mcp_runtime.install()


_install_runtime()
