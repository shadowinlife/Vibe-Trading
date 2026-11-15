"""Shared frozen-input and served-federation contract tests."""

from __future__ import annotations

import pytest

from backtest import source_contract as sc
from backtest.source_contract import validate_frozen_enrichment


def test_live_config_keeps_existing_enrichment_behavior() -> None:
    validate_frozen_enrichment(
        {
            "fundamental_fields": {"income": ["total_revenue"]},
            "event_feeds": [{"name": "news"}],
        }
    )


@pytest.mark.parametrize(
    ("fundamental_fields", "event_feeds"),
    [
        (None, None),
        ({}, {}),
        ([], []),
        ({"income": []}, []),
        ({"income": None, "cashflow": []}, {}),
    ],
)
def test_frozen_config_accepts_effectively_empty_enrichment(
    fundamental_fields: object, event_feeds: object
) -> None:
    validate_frozen_enrichment(
        {
            "frozen_data_manifest": "/sealed/manifest.json",
            "fundamental_fields": fundamental_fields,
            "event_feeds": event_feeds,
        }
    )


def test_frozen_config_rejects_effective_fundamental_fields() -> None:
    with pytest.raises(ValueError, match="fundamental_fields.*frozen_data_manifest"):
        validate_frozen_enrichment(
            {
                "frozen_data_manifest": "/sealed/manifest.json",
                "fundamental_fields": {
                    "income": [],
                    "fina_indicator": ["roe"],
                },
            }
        )


def test_frozen_config_rejects_effective_event_feeds() -> None:
    with pytest.raises(ValueError, match="event_feeds.*frozen_data_manifest"):
        validate_frozen_enrichment(
            {
                "frozen_data_manifest": "/sealed/manifest.json",
                "event_feeds": [{"name": "news", "route_template": "/news"}],
            }
        )


def _history_leg() -> dict[str, object]:
    return {
        "source": "clickhouse",
        "table": "stk_factor_pro",
        "start_date": "2026-11-13",
        "end_date": "2026-11-14",
        "adjustment": "raw",
        "volume_unit": "lot",
        "amount_unit": "thousand CNY",
    }


def _realtime_leg() -> dict[str, object]:
    return {
        "source": "tencent",
        "start_date": "2026-11-15",
        "end_date": "2026-11-15",
        "adjustment": "split_dividend_additive",
        "volume_unit": "lots",
        "amount_unit": None,
    }


def _federated() -> dict[str, object]:
    return {"mode": "federated", "history": _history_leg(), "realtime": _realtime_leg()}


@pytest.mark.parametrize(
    ("mode", "legs"),
    [
        ("clickhouse_only", {"history": _history_leg()}),
        ("federated", {"history": _history_leg(), "realtime": _realtime_leg()}),
        ("realtime_only", {"realtime": _realtime_leg()}),
    ],
)
def test_federation_normalizer_accepts_valid_mode_leg_pairing(
    mode: str, legs: dict[str, object]
) -> None:
    normalized = sc.normalize_federation({"mode": mode, **legs})

    assert normalized is not None
    assert normalized["mode"] == mode
    assert set(normalized).issuperset(legs)


@pytest.mark.parametrize(
    "value",
    [
        {"mode": "unknown"},
        {"mode": "clickhouse_only"},
        {"mode": "clickhouse_only", "realtime": _realtime_leg()},
        {"mode": "federated", "history": _history_leg()},
        {"mode": "realtime_only", "history": _history_leg()},
        {"mode": "realtime_only", "realtime": {"start_date": "2026-11-15"}},
    ],
)
def test_federation_normalizer_rejects_invalid_mode_or_legs(value: object) -> None:
    with pytest.raises(ValueError):
        sc.normalize_federation(value)


def test_federation_normalizer_keeps_only_approved_json_scalars() -> None:
    value = _federated()
    value["password"] = "sentinel-password"
    value["history"]["api_key"] = "sentinel-key"
    value["realtime"]["debug"] = {"raw": "exception"}

    normalized = sc.normalize_federation(value)

    assert normalized is not None
    assert "password" not in normalized
    assert "api_key" not in normalized["history"]
    assert "debug" not in normalized["realtime"]
    assert normalized["history"]["source"] == "clickhouse"
    assert normalized["realtime"]["source"] == "tencent"


def test_federation_normalizer_rejects_non_scalar_leg_values() -> None:
    value = _federated()
    value["history"]["table"] = {"secret": "sentinel"}

    with pytest.raises(ValueError):
        sc.normalize_federation(value)


def test_federation_from_attrs_reads_only_dedicated_provenance() -> None:
    federation = _federated()

    assert sc.federation_from_attrs({"_provenance": {"federation": federation}})
    assert sc.federation_from_attrs({"federation": federation}) is None
    assert sc.federation_from_attrs({"_provenance": {"other": federation}}) is None
    assert sc.federation_from_attrs(None) is None


def test_federation_source_distinguishes_three_modes() -> None:
    assert (
        sc.federation_source(
            {"mode": "clickhouse_only", "history": _history_leg()}, "fallback"
        )
        == "clickhouse"
    )
    assert sc.federation_source(_federated(), "fallback") == "federated"
    assert (
        sc.federation_source(
            {"mode": "realtime_only", "realtime": _realtime_leg()}, "clickhouse"
        )
        == "tencent"
    )


def test_federation_adjustment_marks_mixed_caliber_explicitly() -> None:
    assert sc.federation_adjustment(_federated(), "raw") == "mixed"

    same = _federated()
    same["realtime"]["adjustment"] = "raw"
    assert sc.federation_adjustment(same, "mixed") == "raw"


def test_lot_and_lots_do_not_create_false_unit_warning() -> None:
    value = _federated()
    value["realtime"]["adjustment"] = "raw"

    assert sc.federation_caliber_warning(value) is None


def test_mixed_adjustment_and_units_create_deterministic_warning() -> None:
    value = _federated()
    value["realtime"]["volume_unit"] = "shares"

    assert sc.federation_caliber_warning(value) == (
        "history adjustment=raw; realtime adjustment=split_dividend_additive; "
        "history volume_unit=lot; realtime volume_unit=shares"
    )
