"""Dependency-light contracts for sealed inputs and federated bar provenance."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def validate_frozen_enrichment(config: Mapping[str, Any]) -> None:
    """Reject live enrichment that would alter a sealed frozen input.

    Empty optional values are equivalent to absence. In particular, a
    fundamental-table mapping whose tables all have empty field lists requests
    no enrichment and is valid for a frozen run.

    Args:
        config: Backtest configuration to inspect.

    Raises:
        ValueError: If a frozen manifest is set and either enrichment option
            effectively requests additional data.
    """
    if not config.get("frozen_data_manifest"):
        return

    raw_fields = config.get("fundamental_fields")
    fields_requested = (
        any(bool(fields) for fields in raw_fields.values())
        if isinstance(raw_fields, Mapping)
        else bool(raw_fields)
    )
    if fields_requested:
        raise ValueError(
            "fundamental_fields cannot be used with frozen_data_manifest; "
            "frozen inputs are sealed"
        )
    if config.get("event_feeds"):
        raise ValueError(
            "event_feeds cannot be used with frozen_data_manifest; "
            "frozen inputs are sealed"
        )


_FEDERATION_MODES = frozenset({"clickhouse_only", "federated", "realtime_only"})
_FEDERATION_LEG_FIELDS = (
    "source",
    "table",
    "start_date",
    "end_date",
    "adjustment",
    "volume_unit",
    "amount_unit",
)
_FEDERATION_REQUIRED_LEGS = {
    "clickhouse_only": ("history",),
    "federated": ("history", "realtime"),
    "realtime_only": ("realtime",),
}
_JSON_SCALARS = (str, int, float, bool)


def _normalize_federation_leg(raw_leg: object, name: str) -> dict[str, Any]:
    if not isinstance(raw_leg, Mapping):
        raise ValueError(f"federation {name} leg must be an object")
    source = raw_leg.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"federation {name} leg requires an actual source")

    leg: dict[str, Any] = {}
    for field in _FEDERATION_LEG_FIELDS:
        if field not in raw_leg:
            continue
        value = raw_leg[field]
        if value is not None and not isinstance(value, _JSON_SCALARS):
            raise ValueError(f"federation {name}.{field} must be a JSON scalar")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"federation {name}.{field} must be finite")
        leg[field] = value
    return leg


def _canonical_unit(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip().lower()
    return "lot" if normalized in {"lot", "lots"} else normalized


def _caliber_warning_for_legs(
    history: Mapping[str, Any] | None, realtime: Mapping[str, Any] | None
) -> str | None:
    if history is None or realtime is None:
        return None
    segments = []
    for field in ("adjustment", "volume_unit", "amount_unit"):
        history_value = history.get(field)
        realtime_value = realtime.get(field)
        if history_value is None or realtime_value is None:
            continue
        left = (
            history_value if field == "adjustment" else _canonical_unit(history_value)
        )
        right = (
            realtime_value if field == "adjustment" else _canonical_unit(realtime_value)
        )
        if left != right:
            segments.append(
                f"history {field}={history_value}; realtime {field}={realtime_value}"
            )
    return "; ".join(segments) or None


def normalize_federation(value: object) -> dict[str, Any] | None:
    """Validate and normalize the closed federation provenance contract."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("federation provenance must be an object")
    mode = value.get("mode")
    if mode not in _FEDERATION_MODES:
        raise ValueError(f"unsupported federation mode: {mode!r}")

    normalized: dict[str, Any] = {"mode": mode}
    required = _FEDERATION_REQUIRED_LEGS[mode]
    for name in ("history", "realtime"):
        raw_leg = value.get(name)
        if name in required:
            if raw_leg is None:
                raise ValueError(f"federation mode {mode} requires {name} leg")
            normalized[name] = _normalize_federation_leg(raw_leg, name)
        elif raw_leg is not None:
            raise ValueError(f"federation mode {mode} forbids {name} leg")

    history = normalized.get("history")
    realtime = normalized.get("realtime")
    if history is not None and history["source"] != "clickhouse":
        raise ValueError("federation history source must be clickhouse")
    if realtime is not None and realtime["source"] == "clickhouse":
        raise ValueError("federation realtime source must be the actual network source")

    warning = _caliber_warning_for_legs(history, realtime)
    if warning:
        normalized["caliber_warning"] = warning
    return normalized


def federation_from_attrs(attrs: object) -> dict[str, Any] | None:
    """Read only the dedicated provenance object from frame attributes."""
    if not isinstance(attrs, Mapping):
        return None
    provenance = attrs.get("_provenance")
    if not isinstance(provenance, Mapping):
        return None
    return normalize_federation(provenance.get("federation"))


def federation_source(value: object, default: str | None) -> str | None:
    """Return the overall source without hiding a realtime or federated leg."""
    normalized = normalize_federation(value)
    if normalized is None:
        return default
    mode = normalized["mode"]
    if mode == "federated":
        return "federated"
    leg_name = "history" if mode == "clickhouse_only" else "realtime"
    return normalized[leg_name]["source"]


def federation_adjustment(value: object, default: str | None) -> str | None:
    """Return one shared caliber, or explicitly mark mixed leg calibers."""
    normalized = normalize_federation(value)
    if normalized is None:
        return default
    adjustments = {
        normalized[leg]["adjustment"]
        for leg in ("history", "realtime")
        if leg in normalized and normalized[leg].get("adjustment") is not None
    }
    if not adjustments:
        return default
    return adjustments.pop() if len(adjustments) == 1 else "mixed"


def federation_caliber_warning(value: object) -> str | None:
    """Return a deterministic warning for mixed leg caliber or units."""
    normalized = normalize_federation(value)
    if normalized is None:
        return None
    return _caliber_warning_for_legs(
        normalized.get("history"), normalized.get("realtime")
    )
