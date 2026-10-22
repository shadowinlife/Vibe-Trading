"""Wire documents and identity hashes for the bar session runner (P1-24).

Split out of :mod:`session_runner` so the runner stays an orchestration module
inside the repository's line cap.  This module owns exactly three things:

* :class:`SessionRunnerError` — the runner's refusal, carrying the stable
  internal ``code`` AND the in-band AM-5 error envelope an MCP caller returns
  on the same ``result.content[0].text`` channel as a success document;
* :func:`request_hash` — the JCS hash of the canonical request fields, the
  preimage an orphaned bar step is adopted against;
* :func:`bar_document` — the pt-v1.3 success document text plus its JCS hash.

Both hashes use :mod:`state_hash` (RFC 8785 JCS + sha256); no second
serializer exists.  Nothing here reads a clock, the environment or the network.
"""

from __future__ import annotations

import dataclasses

from . import state_hash, wire
from .bar_engine import BarOutcome
from .schema import BarStepRequest, DateRange, ErrorEnvelope

__all__ = [
    "CODE_ACCOUNT_LOCKED",
    "CODE_ACCOUNT_NOT_FOUND",
    "CODE_BAR_ALREADY_APPLIED",
    "CODE_BAR_OUT_OF_ORDER",
    "CODE_BAR_STEP_CONFLICT",
    "CODE_INVALID_REQUEST",
    "CODE_SKIPPED_NON_TRADING_DAY",
    "BarDocument",
    "SessionRunnerError",
    "bar_document",
    "request_hash",
]

CODE_ACCOUNT_NOT_FOUND = "account_not_found"
CODE_ACCOUNT_LOCKED = "account_locked"
CODE_BAR_ALREADY_APPLIED = "bar_already_applied"
CODE_BAR_OUT_OF_ORDER = "bar_out_of_order"
CODE_BAR_STEP_CONFLICT = "bar_step_conflict"
CODE_SKIPPED_NON_TRADING_DAY = "skipped_non_trading_day"
CODE_INVALID_REQUEST = "invalid_request"

_LOCKED = "PAPER_DATA_NOT_READY"
_INVALID = "INVALID_REQUEST"

#: Internal refusal code -> the frozen AM-5 wire code.  Only the accounts
#: refusal is retryable: another writer holds the lock right now and the very
#: same request may succeed once it exits.  Everything else fails closed.
_WIRE_CODES: dict[str, str] = {CODE_ACCOUNT_LOCKED: _LOCKED}


class SessionRunnerError(RuntimeError):
    """A refused bar step, carrying a stable :attr:`code` and the wire document.

    ``document`` is the in-band pt-v1.3 error envelope (AM-5) — never a
    transport error.  The identity fields of the refused request are echoed
    "where possible" exactly as ``tool.py`` does; the v1.3 coverage hash is
    omitted rather than claimed with a v1.2 preimage (AM-4-4).
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        account_id: str = "",
        market: str = "",
        from_date: str = "",
        to_date: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.account_id = account_id
        self.market = market
        self.from_date = from_date
        self.to_date = to_date

    @property
    def document(self) -> str:
        """The in-band AM-5 error envelope as JSON text."""
        envelope = ErrorEnvelope(
            code=_WIRE_CODES.get(self.code, _INVALID),
            account_id=self.account_id,
            market=self.market,
            range=DateRange(from_=self.from_date, to=self.to_date),
            message=self.message,
        )
        return wire.dumps_payload(wire.build_error_payload(envelope))


def request_hash(request: BarStepRequest, bar_time: str) -> str:
    """JCS hash of the canonical request fields (the adoption key).

    The carried ``account_state`` / ``open_orders`` / ``order_seq`` are the
    wire CARRIER, not the ledger authority (``state`` wins in the engine), so
    they are deliberately outside this preimage.  The result hash covers
    everything the bar produced, so a request field this preimage omits still
    cannot be adopted as the same bar.
    """
    canonical = {
        "account_id": request.account_id,
        "market": request.market,
        "trade_date": request.trade_date,
        "bar_time": bar_time,
        "path_model": request.path_model,
        "slippage": request.slippage.as_wire(),
        "order_actions": [dataclasses.asdict(action) for action in request.order_actions],
        "cancel_order_ids": list(request.cancel_order_ids),
        "replace_orders": [dataclasses.asdict(action) for action in request.replace_orders],
        "fee_model": dict(request.fee_model),
    }
    if request.prev_closes:
        # Emitted only when supplied (D4/D8 spirit): a bar without a caller band
        # table keeps the pre-existing preimage, and a supplied table is part of
        # the identity of the bar it priced.
        canonical["prev_closes"] = dict(request.prev_closes)
    return state_hash.state_hash(canonical)


@dataclasses.dataclass(frozen=True, slots=True)
class BarDocument:
    """One pt-v1.3 success document: its exact text and its JCS hash."""

    text: str
    result_hash: str


def bar_document(outcome: BarOutcome) -> BarDocument:
    """Serialize the engine outcome through the ONE wire builder and hash it."""
    payload = wire.build_success_payload(outcome.bar_result.result, outcome.bar_result)
    return BarDocument(
        text=wire.dumps_payload(payload),
        result_hash=state_hash.state_hash(payload),
    )
