"""Unit tests for the ClickHouse unit/caliber registry (``src.clickhouse_units``).

Covers comment-string parsing, yaml loading (fixture, malformed, missing),
fail-soft degradation to the embedded fallback, and the transition-period
conversion contracts (moneyflow ×10⁴ 万元→元, northbound ×1 万元→万元).
"""

from __future__ import annotations

import logging

import pytest

from src import clickhouse_units as cu

# ---------------------------------------------------------------------------
# Comment-string parsing
# ---------------------------------------------------------------------------


def test_parse_comment_full_convention() -> None:
    """A full convention string parses every field."""
    meta = cu.parse_comment(
        "unit=万元 (ten-thousand CNY); adjust=raw; caliber=工具层×10000换算为元输出; "
        "source=tushare moneyflow; desc=小单买入金额; ambiguous_with=buy_sm_vol"
    )
    assert meta.unit == "万元 (ten-thousand CNY)"
    assert meta.adjust == "raw"
    assert meta.caliber == "工具层×10000换算为元输出"
    assert meta.source == "tushare moneyflow"
    assert meta.desc == "小单买入金额"
    assert meta.ambiguous_with == "buy_sm_vol"
    assert meta.needs_review is False


def test_parse_comment_partial_and_needs_review() -> None:
    """Absent fields are None; needs_review=1 is captured; never raises."""
    meta = cu.parse_comment(
        "source=tushare fina_indicator; desc=待补充; needs_review=1"
    )
    assert meta.unit is None
    assert meta.adjust is None
    assert meta.caliber is None
    assert meta.needs_review is True

    # Garbage input still yields an (empty) meta instead of raising.
    empty = cu.parse_comment("not a convention string at all")
    assert empty.unit is None
    assert empty.raw == "not a convention string at all"


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("万元 (ten-thousand CNY)", 10_000.0),
        ("万元 (10k CNY)", 10_000.0),
        ("千元 (thousand CNY)", 1_000.0),
        ("yuan (CNY)", 1.0),
        ("元", 1.0),
        ("百万元 (million CNY)", 1_000_000.0),
        ("percent (%)", None),
        ("ratio", None),
        ("lots", None),
        ("yuan/share (元/股)", None),
        (None, None),
    ],
)
def test_money_unit_to_yuan_factor(unit: str | None, expected: float | None) -> None:
    """Money units map to their CNY-yuan multiplier; non-money units to None."""
    assert cu.money_unit_to_yuan_factor(unit) == expected


# ---------------------------------------------------------------------------
# YAML loading (fixture files)
# ---------------------------------------------------------------------------

_FIXTURE_YAML = """
version: 1
convention: unit=; adjust=; caliber=; source=; desc=; ambiguous_with=
tables:
  demo_table:
    api: demo
    columns:
      price: unit=yuan (CNY); adjust=raw; desc=价格
      flow: unit=万元 (ten-thousand CNY); caliber=换算为元输出
      plain: desc=无单位列
"""


def test_load_registry_fixture(tmp_path) -> None:
    """A well-formed fixture yaml loads with correct per-column lookups."""
    yaml_file = tmp_path / "comments.yaml"
    yaml_file.write_text(_FIXTURE_YAML, encoding="utf-8")
    registry = cu.load_registry(yaml_file)

    assert registry.source == str(yaml_file)
    assert registry.unit("demo_table", "price") == "yuan (CNY)"
    assert registry.adjust("demo_table", "price") == "raw"
    assert registry.caliber("demo_table", "flow") == "换算为元输出"
    assert registry.unit("demo_table", "plain") is None
    assert registry.get("demo_table", "missing") is None
    assert registry.get("missing_table", "price") is None
    assert registry.money_factor_to_yuan("demo_table", "flow") == 10_000.0


def test_load_registry_missing_file_falls_back(tmp_path, caplog) -> None:
    """A missing yaml degrades to the embedded fallback with a warning."""
    with caplog.at_level(logging.WARNING):
        registry = cu.load_registry(tmp_path / "does_not_exist.yaml")
    assert registry.source == "embedded-fallback"
    assert any("embedded unit fallback" in m for m in caplog.messages)
    # The two conversion facts survive the degradation.
    assert registry.money_factor_to_yuan("stk_moneyflow", "net_mf_amount") == 10_000.0
    assert (
        registry.money_factor_to_yuan("stk_moneyflow_hsgt", "north_money") == 10_000.0
    )


def test_load_registry_malformed_yaml_falls_back(tmp_path) -> None:
    """Unparseable yaml never raises — it degrades to the fallback."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "tables: [unclosed\n  - this is : not valid: yaml:", encoding="utf-8"
    )
    registry = cu.load_registry(bad)
    assert registry.source == "embedded-fallback"


def test_load_registry_wrong_shape_falls_back(tmp_path) -> None:
    """Valid yaml without a usable 'tables' mapping degrades to the fallback."""
    for content in ("foo: bar\n", "tables: {}\n", "tables:\n  t1: {columns: {}}\n"):
        weird = tmp_path / "weird.yaml"
        weird.write_text(content, encoding="utf-8")
        registry = cu.load_registry(weird)
        assert registry.source == "embedded-fallback"


def test_repo_comments_yaml_parses_verified_facts() -> None:
    """The shipped comments.yaml parses and carries the verified anchors."""
    registry = cu.load_registry()  # repo schema/clickhouse/comments.yaml
    assert registry.source != "embedded-fallback"
    assert registry.unit("stk_factor_pro", "vol") == "lots"
    assert registry.unit("stk_factor_pro", "amount") == "千元 (thousand CNY)"
    assert registry.adjust("stk_factor_pro", "close") == "raw"
    assert registry.unit("stk_moneyflow_hsgt", "north_money") == "万元 (10k CNY)"
    assert registry.money_factor_to_yuan("stk_moneyflow", "buy_elg_amount") == 10_000.0


# ---------------------------------------------------------------------------
# Transition-period conversion contracts
# ---------------------------------------------------------------------------


def _registry_from(tables: dict[str, dict[str, str]]) -> cu.UnitRegistry:
    return cu.UnitRegistry(
        {
            table: {col: cu.parse_comment(comment) for col, comment in cols.items()}
            for table, cols in tables.items()
        },
        source="test-fixture",
    )


def test_moneyflow_contract_factor_is_10k(monkeypatch) -> None:
    """The moneyflow 万元→元 contract resolves to ×10⁴ from real metadata."""
    monkeypatch.setattr(cu, "_REGISTRY", cu.load_registry(), raising=False)
    assert cu.moneyflow_amount_to_yuan_factor() == 10_000.0


def test_northbound_contract_factor_is_one(monkeypatch) -> None:
    """The northbound 万元→万元 contract resolves to ×1 (legacy ×100 removed)."""
    monkeypatch.setattr(cu, "_REGISTRY", cu.load_registry(), raising=False)
    assert cu.northbound_raw_to_wan_factor() == 1.0


def test_moneyflow_contract_violation_raises(monkeypatch) -> None:
    """A COMMENT edit claiming a different raw unit fails loudly."""
    monkeypatch.setattr(
        cu,
        "_REGISTRY",
        _registry_from({"stk_moneyflow": {"net_mf_amount": "unit=yuan (CNY)"}}),
        raising=False,
    )
    with pytest.raises(cu.UnitContractError):
        cu.moneyflow_amount_to_yuan_factor()


def test_northbound_contract_violation_raises(monkeypatch) -> None:
    """A COMMENT edit claiming northbound is stored in yuan fails loudly."""
    monkeypatch.setattr(
        cu,
        "_REGISTRY",
        _registry_from({"stk_moneyflow_hsgt": {"north_money": "unit=yuan (CNY)"}}),
        raising=False,
    )
    with pytest.raises(cu.UnitContractError):
        cu.northbound_raw_to_wan_factor()


def test_contracts_degrade_to_legacy_constants_without_metadata(
    monkeypatch, caplog
) -> None:
    """Missing metadata warns and returns the verified constants (fail-soft)."""
    monkeypatch.setattr(cu, "_REGISTRY", _registry_from({}), raising=False)
    with caplog.at_level(logging.WARNING):
        assert cu.moneyflow_amount_to_yuan_factor() == 10_000.0
        assert cu.northbound_raw_to_wan_factor() == 1.0
    assert any("legacy factor" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# Provenance labels (P1.2)
# ---------------------------------------------------------------------------


def test_clickhouse_bar_provenance_complete(monkeypatch) -> None:
    """Provenance metadata is always complete, even on the embedded fallback."""
    monkeypatch.setattr(cu, "_REGISTRY", cu._embedded_registry(), raising=False)
    provenance = cu.clickhouse_bar_provenance("stk_factor_pro")
    assert provenance["volume_unit"] == "lot"
    assert provenance["amount_unit"] == "thousand CNY"
    assert provenance["price_adjust"] == "raw"
    assert "stk_factor_pro" in provenance["caliber"]


def test_short_unit_label_mapping() -> None:
    assert cu.short_unit_label("lots") == "lot"
    assert cu.short_unit_label("千元 (thousand CNY)") == "thousand CNY"
    assert cu.short_unit_label("万元 (10k CNY)") == "10k CNY"
    assert cu.short_unit_label(None) is None
    # Unknown units pass through verbatim.
    assert cu.short_unit_label("股") == "股"
