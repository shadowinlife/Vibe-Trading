"""pt-v1.3 bar-input parsers: the injected ``bars`` array and ``prev_closes``.

Split out of :mod:`schema_v13` for the community line cap.  The module depends
only on the frozen base helpers in :mod:`schema` (never on ``schema_v13``), so
the request parser can import it without a cycle.

Two shapes live here, both ADDITIVE to the pt-v1.1/pt-v1.2 request:

* ``bars`` — one ``{symbol, bars: [{bar_time, OHLCV}]}`` entry per symbol.
  Only the JSON shape is checked; the OHLCV frame contract stays with the feed.
* ``prev_closes`` — the previous session's daily close per symbol, the only
  input from which a cn_a price band can be derived when no limits table is
  injected.  The canonical representation is INTEGER CENTS: an integer value is
  read as cents (``100000`` is ``1000.00``) while a float or ``Decimal`` is
  read as a currency amount in yuan and normalized onto the 0.01 tick
  (``1000.0`` and ``Decimal("1000.00")`` both become ``100000`` cents).  The
  mixed spelling mirrors how a caller can hold either the wire integer or a
  price, and the normalization is the ONE place it is interpreted; every
  downstream layer receives the cent map.

A band that cannot be derived stays UNKNOWN: this module never invents a close,
and a non-positive, non-finite, boolean or non-numeric value is refused rather
than coerced.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType
from typing import Any

from .schema import PaperTradeSchemaError, _require_number, _require_str

__all__ = ["parse_bars", "parse_prev_closes", "require_non_empty_str"]

_CENT = Decimal("0.01")
_HUNDRED = Decimal("100")


def require_non_empty_str(value: Any, path: str) -> str:
    """A string with at least one character, refused (never coerced) otherwise."""
    text = _require_str(value, path)
    if not text:
        raise PaperTradeSchemaError(f"{path} must be a non-empty string")
    return text


def parse_bars(raw: Any) -> tuple[Mapping[str, Any], ...]:
    """Parse ``bars``: one non-empty ``{symbol, bars: [{bar_time, OHLCV}]}`` per symbol.

    Only the JSON shape is checked; the OHLCV frame contract stays with the feed.
    """
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise PaperTradeSchemaError(f"bars must be an array, got {type(raw).__name__}")
    entries: list[Mapping[str, Any]] = []
    for index, entry in enumerate(raw):
        entry_path = f"bars[{index}]"
        if not isinstance(entry, Mapping):
            raise PaperTradeSchemaError(f"{entry_path} must be an object, got {type(entry).__name__}")
        require_non_empty_str(entry.get("symbol"), f"{entry_path}.symbol")
        rows = entry.get("bars")
        if not isinstance(rows, (list, tuple)):
            raise PaperTradeSchemaError(f"{entry_path}.bars must be an array, got {type(rows).__name__}")
        if not rows:
            raise PaperTradeSchemaError(f"{entry_path}.bars must not be empty")
        for row_index, row in enumerate(rows):
            row_path = f"{entry_path}.bars[{row_index}]"
            if not isinstance(row, Mapping):
                raise PaperTradeSchemaError(f"{row_path} must be an object, got {type(row).__name__}")
            require_non_empty_str(row.get("bar_time"), f"{row_path}.bar_time")
            for column in ("open", "high", "low", "close", "volume"):
                _require_number(row.get(column), f"{row_path}.{column}")
        entries.append(entry)
    symbols = [str(entry["symbol"]) for entry in entries]
    duplicates = sorted({symbol for symbol in symbols if symbols.count(symbol) > 1})
    if duplicates:
        raise PaperTradeSchemaError(f"bars contains duplicate symbol(s) {duplicates}")
    return tuple(entries)


def parse_prev_closes(raw: Any, path: str = "prev_closes") -> Mapping[str, int]:
    """Parse ``symbol -> previous session close`` into canonical integer CENTS.

    An integer is cents; a float or ``Decimal`` is yuan and is quantized onto
    the package money tick (2dp ``ROUND_HALF_UP``) before conversion.  A
    boolean, a string, a non-finite value or a non-positive close is refused:
    a band derived from a bad close would be a fabricated limit.
    """
    if raw is None:
        return MappingProxyType({})
    if not isinstance(raw, Mapping):
        raise PaperTradeSchemaError(f"{path} must be an object, got {type(raw).__name__}")
    closes: dict[str, int] = {}
    for symbol, value in raw.items():
        symbol_path = f"{path}[{symbol!r}]"
        if not isinstance(symbol, str) or not symbol:
            raise PaperTradeSchemaError(
                f"{path} keys must be non-empty symbols, got {symbol!r}"
            )
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise PaperTradeSchemaError(
                f"{symbol_path} must be an integer (cents), float or Decimal, "
                f"got {type(value).__name__}"
            )
        cents = value if isinstance(value, int) else _cents(value, symbol_path)
        if cents <= 0:
            raise PaperTradeSchemaError(f"{symbol_path} must be > 0, got {value!r}")
        closes[symbol] = cents
    return MappingProxyType(closes)


def _cents(value: float | Decimal, path: str) -> int:
    """Normalize a yuan amount (float/Decimal) onto the cent grid, in cents."""
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise PaperTradeSchemaError(f"{path} must be finite, got {value!r}")
    tick = parsed.quantize(_CENT, rounding=ROUND_HALF_UP)
    return int(tick * _HUNDRED)
