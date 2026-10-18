"""Trading-calendar seam for ``paper_trade_step`` (pt-v1.1 R3 / AM-6, VD-5).

Market strategy matrix (AM-6):

* **cn_a** — ClickHouse ``ashare.trade_calendar`` is the truth
  (exchange / cal_date / is_open / pretrade_date).  The client is
  INJECTABLE: this module never constructs a connector, never touches the
  network, and is offline-testable with a fake client.
  ``ClickHouseConnector.get_trade_calendar`` (agent/src/clickhouse_connector.py:501)
  satisfies :class:`TradeCalendarClient` structurally — no wrapper needed.
* **hk / us** — TODO-G1: pending RM written approval of pt-v1.1.  AM-6
  gives hk/us a ZERO-CALENDAR fail-closed strategy: the non-trading-day
  verdict is inferred from bar availability in the data layer (Step 2+) —
  target-day all-market zero bar → ``PAPER_DATA_NOT_READY``; mid-range
  all-market zero bar → skipped reason ``market_closed_inferred`` +
  warning.  The seams below raise ``NotImplementedError`` until G1 fixes
  the strategy; if AM-6 is rejected, pt-v1.0 R3 would demand real holiday
  detection that this repo cannot serve dependency-free (plan R-V1).

Determinism: no clock, no environment, no network reads here; the same
client rows always produce the same verdicts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "CN_A_MARKET",
    "HK_MARKET",
    "US_MARKET",
    "TradeCalendarClient",
    "is_trading_day",
    "trading_days_in_range",
]

CN_A_MARKET = "cn_a"
HK_MARKET = "hk"
US_MARKET = "us"

_CALENDAR_MARKETS = (CN_A_MARKET, HK_MARKET, US_MARKET)

# TODO-G1: pending RM written approval of pt-v1.1 — the hk/us zero-calendar
# fail-closed strategy (AM-6) is a DRAFT amendment; until approval these
# markets raise instead of guessing.  If AM-6 is rejected the fallback is
# an escalation under REQUIREMENTS §9 (no dependency-free hk/us calendar
# exists in-repo — plan E8/R-V1).
_ZERO_CALENDAR_MARKETS = (HK_MARKET, US_MARKET)

_HK_US_NOT_IMPLEMENTED = (
    "paper_trade calendar: market {market!r} has no in-engine calendar by design "
    "(AM-6 zero-calendar fail-closed: non-trading days are inferred from bar "
    "availability in the data layer — target-day zero bar => PAPER_DATA_NOT_READY, "
    "mid-range zero bar => market_closed_inferred)"
)


class TradeCalendarClient(Protocol):
    """Structural interface for calendar lookups.

    ``ClickHouseConnector`` satisfies it as-is: ``get_trade_calendar``
    returns a DataFrame with columns ``exchange, cal_date, is_open,
    pretrade_date`` for the inclusive ``YYYY-MM-DD`` range.  Tests inject
    a fake returning recorded rows — never the network.
    """

    def get_trade_calendar(self, start_date: str, end_date: str) -> "pd.DataFrame":
        ...


def _normalize_date(value: str) -> str:
    """Normalize a calendar date to ``YYYY-MM-DD``.

    The connector documents ``YYYY-MM-DD`` parameters, but the stored
    ``cal_date`` spelling is unverified while ClickHouse is unreachable
    (Step 0 DD-5 live column recheck pending); tushare-lineage exports
    commonly use ``YYYYMMDD``.  Both spellings normalize; anything else
    fails closed with a deterministic message.
    """
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    raise ValueError(f"unrecognized calendar date format: {value!r}")


def _cn_a_open_map(client: TradeCalendarClient, start_date: str, end_date: str) -> dict[str, bool]:
    """Query the CH calendar once and reduce rows to {date: is_open}.

    The table carries one row per exchange per date; SSE and SZSE share the
    cn_a holiday calendar, so a date aggregates as open when ANY row says
    open — robust to single-exchange exports and deterministic either way.
    A date with no row at all is NOT a confirmed trading day (the table is
    the truth source and expected complete; fail closed).
    """
    frame = client.get_trade_calendar(start_date, end_date)
    open_map: dict[str, bool] = {}
    for record in frame.to_dict("records"):
        date = _normalize_date(record["cal_date"])
        is_open = int(record["is_open"]) == 1
        open_map[date] = open_map.get(date, False) or is_open
    return open_map


def _reject_market(market: str) -> None:
    if market not in _CALENDAR_MARKETS:
        raise ValueError(f"unknown market for calendar lookup: {market!r}")
    if market in _ZERO_CALENDAR_MARKETS:
        raise NotImplementedError(_HK_US_NOT_IMPLEMENTED.format(market=market))


def is_trading_day(client: TradeCalendarClient, market: str, date: str) -> bool:
    """Whether ``date`` (YYYY-MM-DD) is a trading day of ``market``.

    cn_a: CH ``trade_calendar`` truth.  hk/us: raises NotImplementedError
    (AM-6 zero-calendar — see module docstring).  Unknown market: ValueError.
    """
    _reject_market(market)
    day = _normalize_date(date)
    return _cn_a_open_map(client, day, day).get(day, False)


def trading_days_in_range(
    client: TradeCalendarClient, market: str, from_date: str, to_date: str
) -> tuple[str, ...]:
    """Open trading days within the inclusive range, ascending (R3 driver).

    The engine's day loop (Step 3) consumes this to emit ``skipped_days``
    rows with reason ``non_trading_day`` for the closed dates in between.
    """
    _reject_market(market)
    start = _normalize_date(from_date)
    end = _normalize_date(to_date)
    open_map = _cn_a_open_map(client, start, end)
    # Clamp to the requested range: the real connector filters in SQL, but
    # the contract this function guarantees is "open days within [start, end]"
    # regardless of what the client hands back.
    return tuple(sorted(date for date, is_open in open_map.items() if is_open and start <= date <= end))
