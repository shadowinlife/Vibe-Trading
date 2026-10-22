"""JCS canonicalization + state-integrity hashing for ``paper_trade_step``.

Implements R7 / AM-4 of the pt-v1.1 contract (``received_state_hash`` and the
``effective_fee_model`` echo).  Per VD-4 the canonical form is hand-rolled —
zero new runtime dependencies — and the frozen test vectors in
``agent/tests/fixtures/paper_trade/jcs_vectors.json`` are the SOLE
interpretation authority (AM-4-2): both the VT engine and the RM observer
assert their own implementations against the same vectors.

Canonicalization rules (RFC 8785 JCS as frozen by VD-4):

* Object keys are sorted by Unicode code point — the semantics of
  ``json.dumps(sort_keys=True)``.  RFC 8785 itself sorts by UTF-16 code
  unit; the two orders agree for BMP-only keys, which is all the frozen
  vectors and the wire contract use.
* Numbers use the ECMAScript shortest round-trip representation, so
  ``100000.0`` and ``100000`` canonicalize identically (``100000``),
  ``-0.0`` canonicalizes as ``0``, and the exponential thresholds match
  ``JSON.stringify`` (``1e21`` -> ``1e+21`` but ``1e16`` ->
  ``10000000000000000``).  Integers are serialized exactly (Python
  semantics); the frozen vectors stay within +/-2^53 where every
  double-based implementation agrees.
* Strings are escaped by ``json.dumps(..., ensure_ascii=False)`` — raw
  UTF-8 for non-ASCII, ``\\uXXXX`` (lowercase hex) for control characters,
  matching ``JSON.stringify`` for BMP text.
* Arrays keep their order: element order is semantic (positions ordered by
  the RM ledger) and is never sorted or shuffled.

Determinism: this module reads no clock, no environment, no network and
writes nothing — the same input always produces the same bytes.

pt-v1.2 (D3/D4): :func:`compute_received_state_hash` accepts the four new
request fields and adds each to the coverage object ONLY when it is
non-default, so every pt-v1.1 call hashes byte-identically; and
:func:`engine_fingerprint` hashes the engine-parameter tuple with the same
JCS helper.  Neither function changes matching.

pt-v1.3 (D8/GAP-3/GAP-6): :func:`compute_bar_state_hash` covers the carried
per-bar order state, and :func:`engine_fingerprint` gains two optional
v1.3 parameters.  Both follow the same D8 rule as pt-v1.2 — a field enters
the preimage ONLY when it is present — so a v1.1/v1.2 call keeps its exact
pre-edit digest and the additional state can never be silently dropped.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .schema import (
    DEFAULT_FILL_MODEL,
    DEFAULT_INTERVAL,
    DEFAULT_PATH_MODEL,
    DEFAULT_SESSION,
)

if TYPE_CHECKING:  # annotation-only: importing the v1.3 shapes at runtime is unnecessary
    from .schema import AccountState, SlippageSpec

__all__ = [
    "ORDER_MODEL_VERSION",
    "canonicalize",
    "compute_bar_state_hash",
    "compute_received_state_hash",
    "effective_fee_model",
    "engine_fingerprint",
    "state_hash",
]

#: Version of the pt-v1.3 order-book semantics (order identity, lifecycle
#: transitions, TIF handling).  It is hashed as part of any non-trivial v1.3
#: bar state and can be passed to :func:`engine_fingerprint`, so bumping it is
#: a fingerprint-visible change: a stored hash can never claim an order model
#: the engine no longer implements.
ORDER_MODEL_VERSION = "pt-v1.3-orderbook-1"

# FROZEN terminal defaults (G1: RM written approval 2026-10-19; APPROVAL note 5
# + REQUIREMENTS §4 fee matrix).  Key names are the pt-v1.1 final set — cn_a
# verbatim from the AMENDMENT §2.2 out-param example, hk/us mapped item-by-item
# to the §4 matrix; they are registered in the delivery note (#7) so the RM
# dispatch injection (AM-4-3: fee_model is always injected explicitly) aligns.
# Any key/value change = major schema bump with RM written confirmation
# (REQUIREMENTS §9).  VD-9 numeric choices registered in the delivery note:
# hk stamp duty quantizes to whole HKD ROUND_HALF_UP (HKEX itself ceils) and
# the us SEC fee is simplified to 0.
_DEFAULT_FEE_MODELS: dict[str, dict[str, float]] = {
    "cn_a": {
        "commission_rate": 0.00025,  # 万2.5 bilateral
        "commission_min": 5.0,  # ¥5 minimum per order
        "stamp_tax_sell": 0.0005,  # 万5 sell-side only
        "transfer_fee": 0.00001,  # 万0.1 bilateral
    },
    "hk": {
        "commission_rate": 0.0003,  # 0.03%
        "commission_min": 3.0,  # HK$3 minimum
        "stamp_tax_rate": 0.001,  # 0.1% per side, whole HKD ROUND_HALF_UP (VD-9)
        "regulatory_levy_rate": 0.00003,  # ≈0.003% combined levies
    },
    "us": {
        "commission_per_share": 0.0049,  # $0.0049/share
        "commission_min": 0.99,  # $0.99 minimum
        "sec_fee_rate": 0.0,  # simplified to zero (§4, VD-9 registered)
    },
}

#: Markets whose fee defaults are known.  Mirrors the pt-v1.1 ``market`` enum.
FEE_MODEL_MARKETS: tuple[str, ...] = ("cn_a", "hk", "us")


def _ecmascript_number(value: Decimal) -> str:
    """Serialize a finite Decimal per ECMAScript ``Number::toString``.

    This is the number form RFC 8785 mandates: the shortest digit string that
    round-trips, rendered by the ECMA-262 §7.1.12.1 branch rules (plain
    integer/decimal forms inside their thresholds, ``e±X`` outside).
    """
    if value == 0:
        # Covers -0.0 too: JCS/ECMAScript render negative zero as "0".
        return "0"
    sign, digits, exponent = value.normalize().as_tuple()
    # normalize() strips trailing zeros; guard the "0E+2" style residue.
    digits = tuple(int(d) for d in digits)
    k = len(digits)
    n = exponent + k  # value = m * 10^(n-k), m has k digits
    prefix = "-" if sign else ""
    body = "".join(str(d) for d in digits)
    if k <= n <= 21:
        return prefix + body + "0" * (n - k)
    if 0 < n <= 21:
        return prefix + body[:n] + "." + body[n:]
    if -6 < n <= 0:
        return prefix + "0." + "0" * (-n) + body
    exp = n - 1
    exp_part = f"e{'+' if exp >= 0 else '-'}{abs(exp)}"
    if k == 1:
        return prefix + body + exp_part
    return prefix + body[0] + "." + body[1:] + exp_part


def _serialize_number(value: int | float) -> str:
    """Canonical number token for an int or float (bools never reach here)."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float is not JSON-serializable: {value!r}")
        # repr() is the shortest round-trip decimal for IEEE-754 doubles.
        return _ecmascript_number(Decimal(repr(value)))
    return _ecmascript_number(Decimal(value))


def canonicalize(value: Any) -> str:
    """Return the JCS canonical form of a JSON-native value.

    Accepts exactly the JSON type lattice: ``None``, ``bool``, ``int``,
    ``float``, ``str``, ``list``, ``dict`` (str keys only).  Anything else —
    ``Decimal``, ``datetime``, custom objects — raises ``TypeError``: the
    hashed surface is the JSON-parsed tool input, and a silently coerced
    non-JSON type would make the hash implementation-defined.
    """
    # bool before int: bool is an int subclass and must serialize as a token.
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _serialize_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonicalize(item) for item in value) + "]"
    if isinstance(value, dict):
        items = []
        for key in sorted(value):
            if not isinstance(key, str):
                raise TypeError(f"JCS object keys must be str, got {type(key).__name__}")
            items.append(f"{json.dumps(key, ensure_ascii=False)}:{canonicalize(value[key])}")
        return "{" + ",".join(items) + "}"
    raise TypeError(f"value of type {type(value).__name__} is not JSON-native")


def state_hash(value: Any) -> str:
    """Return ``sha256(JCS(value))`` as lowercase hex (AM-4-1)."""
    return hashlib.sha256(canonicalize(value).encode("utf-8")).hexdigest()


def _decimal_token(value: Decimal) -> str:
    """Render a ``Decimal`` exactly as ``SlippageSpec.as_wire`` does.

    Slippage parameters are money/rate values and are ``Decimal`` by contract;
    the wire echo renders them with ``format(normalize(), "f")``.  Hashing the
    same string keeps the bar-state hash and the echoed spec on one
    representation — and a ``Decimal`` never degrades to a binary float.
    """
    if not value.is_finite():
        raise ValueError(f"non-finite Decimal is not hasheable: {value!r}")
    if not value:
        return "0"
    return format(value.normalize(), "f")


def _json_native(value: Any) -> Any:
    """Normalize a hashed state object into the JSON type lattice.

    Typed v1.3 rows (``AccountState`` / ``OrderState`` / ``SlippageSpec``) are
    accepted alongside their JSON-native forms so the caller may hash the
    parsed request without a second serializer.  ``Decimal`` renders through
    :func:`_decimal_token`; everything else is returned unchanged and left for
    :func:`canonicalize` to accept or reject (it fails closed on non-JSON
    types and on non-finite floats).
    """
    if isinstance(value, Decimal):
        return _decimal_token(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_native(getattr(value, field.name)) for field in dataclass_fields(value)}
    if isinstance(value, Mapping):
        return {key: _json_native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_native(item) for item in value]
    return value


def compute_bar_state_hash(
    *,
    account_state: AccountState | dict[str, Any],
    open_orders: Sequence[Any] = (),
    order_seq: int = 0,
    bar_time: str = "",
    slippage_model: str | SlippageSpec | dict[str, Any] = "",
    order_model_version: str = ORDER_MODEL_VERSION,
) -> str:
    """D8: hash the per-bar state a pt-v1.3 ``bar_step`` consumes.

    Preimage (a JCS object; :func:`canonicalize` pins the key order):

    * ``account_state`` — always;
    * ``open_orders`` — only when non-empty;
    * ``order_seq`` — only when non-zero;
    * ``bar_time`` — only when non-empty;
    * ``slippage_model`` — only when non-empty;
    * ``order_model_version`` — only when at least one of the four v1.3
      fields above is present.

    The v1.3 additions mirror the D8 rule of
    :func:`compute_received_state_hash`: a bar with no v1.3 surface hashes
    EXACTLY as the v1.2 preimage ``{"account_state": ...}``, so a v1.1/v1.2
    caller cannot gain a different digest.  ``order_model_version`` travels
    with the order state it describes — it is hashed only when that state is
    present, which keeps a bump of :data:`ORDER_MODEL_VERSION` visible in
    every non-trivial bar hash without perturbing the v1.2 boundary.

    ``account_state`` and ``open_orders`` may be the typed dataclasses or their
    JSON rows; both normalize to the same preimage.  A ``Decimal`` slippage
    value (e.g. a ``SlippageSpec`` passed whole) renders as the canonical
    plain-decimal string used by ``SlippageSpec.as_wire``, never as a float.
    Non-finite input fails closed — floats through :func:`canonicalize`,
    Decimals through :func:`_decimal_token` — instead of hashing a constant.

    Deterministic: no clock, no environment, no I/O.
    """
    coverage: dict[str, Any] = {"account_state": _json_native(account_state)}
    rows = tuple(open_orders)
    has_v13_state = False
    if rows:
        coverage["open_orders"] = _json_native(rows)
        has_v13_state = True
    if order_seq:
        coverage["order_seq"] = order_seq
        has_v13_state = True
    if bar_time:
        coverage["bar_time"] = bar_time
        has_v13_state = True
    if slippage_model:
        coverage["slippage_model"] = _json_native(slippage_model)
        has_v13_state = True
    if has_v13_state:
        coverage["order_model_version"] = order_model_version
    return state_hash(coverage)


def compute_received_state_hash(
    *,
    account_id: str,
    market: str,
    account_state: Any,
    fee_model: Any,
    from_date: str,
    to_date: str,
    interval: str = DEFAULT_INTERVAL,
    session: str = DEFAULT_SESSION,
    path_model: str = DEFAULT_PATH_MODEL,
    fill_model: str = DEFAULT_FILL_MODEL,
) -> str:
    """Hash the AM-4-1 coverage object plus any non-default pt-v1.2 field.

    ``fee_model`` must be the EFFECTIVE value (see :func:`effective_fee_model`)
    — AM-4-3: the RM dispatch always injects fee_model explicitly, so the hash
    covers every non-order parameter.

    D4: the four pt-v1.2 fields enter the coverage object ONLY when explicitly
    non-default, so a call that omits them (or passes the default value) hashes
    exactly as pt-v1.1 did.  A v1.2 call can never hide a non-default field
    from the hash.
    """
    coverage: dict[str, Any] = {
        "account_id": account_id,
        "market": market,
        "account_state": account_state,
        "fee_model": fee_model,
        "from_date": from_date,
        "to_date": to_date,
    }
    for key, value, default in (
        ("interval", interval, DEFAULT_INTERVAL),
        ("session", session, DEFAULT_SESSION),
        ("path_model", path_model, DEFAULT_PATH_MODEL),
        ("fill_model", fill_model, DEFAULT_FILL_MODEL),
    ):
        if value != default:
            coverage[key] = value
    return state_hash(coverage)


def engine_fingerprint(
    *,
    interval: str = DEFAULT_INTERVAL,
    path_model: str = DEFAULT_PATH_MODEL,
    fill_model: str = DEFAULT_FILL_MODEL,
    bar_fingerprint: str = "",
    rules_version: str | None = None,
    slippage_model: str | None = None,
    order_model_version: str | None = None,
) -> str:
    """D3: ``sha256(JCS({interval, path_model, fill_model, rules_version,
    bar_fingerprint}))`` as lowercase hex.

    ``bar_fingerprint`` is supplied by the engine (T3/T4) as a hex digest of
    the bars actually used; ``""`` means no bar fingerprint was supplied (e.g.
    a cash-only account with no matched bar).  ``rules_version`` defaults to
    :data:`rules.RULES_VERSION` — imported lazily because ``rules`` imports
    this module — and is injectable so the fingerprint can be audited against
    a past rule set.  No clock, no paths, no environment: the same arguments
    always produce the same hex string.

    D8: ``slippage_model`` and ``order_model_version`` are appended to the
    preimage ONLY when they are not ``None``, so every pt-v1.1/pt-v1.2 call
    (which supplies neither) keeps the exact pre-edit digest.  A v1.3 engine
    passes both, which makes the slippage model and the order-book semantics
    part of the audited parameter tuple.
    """
    if rules_version is None:
        from .rules import RULES_VERSION

        rules_version = RULES_VERSION
    preimage: dict[str, Any] = {
        "interval": interval,
        "path_model": path_model,
        "fill_model": fill_model,
        "rules_version": rules_version,
        "bar_fingerprint": bar_fingerprint,
    }
    if slippage_model is not None:
        preimage["slippage_model"] = slippage_model
    if order_model_version is not None:
        preimage["order_model_version"] = order_model_version
    return state_hash(preimage)


def effective_fee_model(market: str, fee_model: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the fee parameters actually in effect for this run (R7-3 echo).

    Per-key merge of the caller-provided overrides over the market defaults:
    REQUIREMENTS §2.1 defines ``fee_model`` as "key = fee item name, value =
    rate/minimum" overrides.  Unknown markets fail closed — there is no
    silent default fee schedule.
    """
    if market not in _DEFAULT_FEE_MODELS:
        raise ValueError(f"unknown market for fee model: {market!r}")
    return {**_DEFAULT_FEE_MODELS[market], **(fee_model or {})}
