"""Metadata-driven unit/caliber registry for the ClickHouse ``ashare`` database.

Loads the column-comment contract from ``schema/clickhouse/comments.yaml``
(CLICKHOUSE_ITERATION_PLAN.md P0.2/P1.4) and exposes unit / adjust / caliber
lookups per ``(table, column)``.  Comment strings follow the convention
``unit=<单位>; adjust=<raw|hfq|qfq|bfq>; caliber=<口径>; source=tushare <api>;
desc=<中文说明>; ambiguous_with=<列>``.

The registry is fail-soft: when the yaml file is missing or unparseable (e.g.
pip-installed contexts without the repo ``schema/`` tree) it degrades to a
small embedded fallback carrying the two verified conversion facts plus the
provenance/valuation fields, with an explicit warning.  Importing this module
never raises and never reads environment variables.

Transition contract (plan P1.4 "过渡期留断言"): while metadata takes over from
hardcoded factors, any present metadata is asserted to agree with the verified
contract, so a future COMMENT edit that breaks it fails loudly instead of
silently rescaling data.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ColumnMeta:
    """Parsed COMMENT metadata for one ``(table, column)`` pair."""

    unit: str | None = None
    adjust: str | None = None
    caliber: str | None = None
    source: str | None = None
    desc: str | None = None
    ambiguous_with: str | None = None
    needs_review: bool = False
    raw: str = ""


def parse_comment(comment: str) -> ColumnMeta:
    """Parse one ``key=value; ...`` COMMENT string into :class:`ColumnMeta`.

    Never raises: malformed segments are skipped, so a partially-formed
    comment still yields the fields that did parse.
    """
    fields: dict[str, str] = {}
    for part in str(comment).split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            fields[key] = value
    return ColumnMeta(
        unit=fields.get("unit"),
        adjust=fields.get("adjust"),
        caliber=fields.get("caliber"),
        source=fields.get("source"),
        desc=fields.get("desc"),
        ambiguous_with=fields.get("ambiguous_with"),
        needs_review=fields.get("needs_review", "") == "1",
        raw=str(comment),
    )


# Ordered patterns mapping a money-unit string to its CNY-yuan multiplier.
# Specific units first: "百万元" contains "万元", so million must precede
# ten-thousand, which must precede the anchored plain-yuan rule.
_YUAN_MULTIPLIERS: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"百万元|million CNY", re.IGNORECASE), 1_000_000.0),
    (re.compile(r"万元|10k CNY|ten-thousand CNY", re.IGNORECASE), 10_000.0),
    (re.compile(r"千元|thousand CNY", re.IGNORECASE), 1_000.0),
    (re.compile(r"^\s*(yuan \(CNY\)|元)\s*$", re.IGNORECASE), 1.0),
)


def money_unit_to_yuan_factor(unit: str | None) -> float | None:
    """Return the multiplier converting a ``unit=`` value to CNY yuan.

    ``10000.0`` for 万元-class units, ``1000.0`` for 千元-class, ``1.0`` for
    plain yuan; ``None`` when the unit is absent or not monetary.
    """
    if not unit:
        return None
    for pattern, factor in _YUAN_MULTIPLIERS:
        if pattern.search(unit):
            return factor
    return None


# Minimal comment facts that must survive even without comments.yaml: the two
# verified conversion contracts (moneyflow amounts 万元→元 ×10⁴; northbound
# raw unit 万元, output 万元, factor 1) plus the stk_factor_pro fields used by
# the provenance metadata (P1.2) and the get_valuation tool (P1.3).
_EMBEDDED_COMMENTS: dict[str, dict[str, str]] = {
    "stk_moneyflow": {
        col: "unit=万元 (ten-thousand CNY); caliber=工具层×10000换算为元输出"
        for col in (
            "buy_sm_amount",
            "sell_sm_amount",
            "buy_md_amount",
            "sell_md_amount",
            "buy_lg_amount",
            "sell_lg_amount",
            "buy_elg_amount",
            "sell_elg_amount",
            "net_mf_amount",
        )
    },
    "stk_moneyflow_hsgt": {
        col: "unit=万元 (10k CNY)"
        for col in ("ggt_ss", "ggt_sz", "hgt", "sgt", "north_money", "south_money")
    },
    "stk_factor_pro": {
        "vol": "unit=lots; caliber=tushare口径(1手=100股)",
        "amount": "unit=千元 (thousand CNY); caliber=已交叉验证amount×10/vol≈close",
        "close": "unit=yuan (CNY); adjust=raw",
        "pe_ttm": "unit=ratio; caliber=TTM(滚动12个月), 亏损的PE为空",
        "pb": "unit=ratio; caliber=总市值/净资产",
        "ps_ttm": "unit=ratio; caliber=TTM(滚动12个月)",
        "dv_ttm": "unit=percent (%); caliber=股息率(TTM)",
        "total_mv": "unit=万元 (ten-thousand CNY); caliber=tushare daily_basic口径",
        "circ_mv": "unit=万元 (ten-thousand CNY); caliber=tushare daily_basic口径",
        "turnover_rate": "unit=percent (%)",
    },
}


class UnitRegistry:
    """Immutable lookup of parsed column metadata by ``(table, column)``."""

    def __init__(
        self, tables: dict[str, dict[str, ColumnMeta]], *, source: str
    ) -> None:
        self._tables = tables
        self.source = source

    def get(self, table: str, column: str) -> ColumnMeta | None:
        """Return metadata for ``(table, column)`` or ``None`` when absent."""
        return self._tables.get(table, {}).get(column)

    def unit(self, table: str, column: str) -> str | None:
        """Return the ``unit=`` value for ``(table, column)`` or ``None``."""
        meta = self.get(table, column)
        return meta.unit if meta else None

    def adjust(self, table: str, column: str) -> str | None:
        """Return the ``adjust=`` value for ``(table, column)`` or ``None``."""
        meta = self.get(table, column)
        return meta.adjust if meta else None

    def caliber(self, table: str, column: str) -> str | None:
        """Return the ``caliber=`` value for ``(table, column)`` or ``None``."""
        meta = self.get(table, column)
        return meta.caliber if meta else None

    def money_factor_to_yuan(self, table: str, column: str) -> float | None:
        """Return the raw-value → CNY-yuan multiplier for a money column."""
        return money_unit_to_yuan_factor(self.unit(table, column))


def _default_yaml_path() -> Path:
    """Locate ``schema/clickhouse/comments.yaml`` relative to this file."""
    return (
        Path(__file__).resolve().parents[2] / "schema" / "clickhouse" / "comments.yaml"
    )


def _embedded_registry() -> UnitRegistry:
    """Build the fallback registry from :data:`_EMBEDDED_COMMENTS`."""
    tables = {
        table: {column: parse_comment(comment) for column, comment in columns.items()}
        for table, columns in _EMBEDDED_COMMENTS.items()
    }
    return UnitRegistry(tables, source="embedded-fallback")


def load_registry(path: str | Path | None = None) -> UnitRegistry:
    """Load ``comments.yaml`` into a :class:`UnitRegistry`, failing soft.

    Any problem (missing file, invalid YAML, unexpected structure) degrades to
    the embedded fallback with a warning — this function never raises.
    ``path`` overrides the yaml location (tests only).
    """
    yaml_path = Path(path) if path is not None else _default_yaml_path()
    try:
        import yaml

        doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        tables_raw = doc.get("tables") if isinstance(doc, dict) else None
        if not isinstance(tables_raw, dict) or not tables_raw:
            raise ValueError(f"{yaml_path}: missing or empty 'tables' mapping")
        tables: dict[str, dict[str, ColumnMeta]] = {}
        for table, table_def in tables_raw.items():
            columns = table_def.get("columns") if isinstance(table_def, dict) else None
            if not isinstance(columns, dict):
                continue
            tables[str(table)] = {
                str(column): parse_comment(str(comment))
                for column, comment in columns.items()
                if comment is not None
            }
        if not any(tables.values()):
            raise ValueError(f"{yaml_path}: no table defines usable columns")
        return UnitRegistry(tables, source=str(yaml_path))
    except Exception as exc:  # noqa: BLE001 — fail-soft is the contract here
        logger.warning(
            "ClickHouse comments.yaml unavailable (%s); degrading to embedded unit fallback",
            exc,
        )
        return _embedded_registry()


_REGISTRY: UnitRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def get_registry() -> UnitRegistry:
    """Return the process-wide cached registry (loaded on first use)."""
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = load_registry()
    return _REGISTRY


class UnitContractError(RuntimeError):
    """Registry metadata contradicts a verified tool-layer conversion."""


# Verified contract factors (2026-08-12 real-data anchors, see comments.yaml):
# stk_moneyflow amount columns are stored in 万元 and the tools emit 元 (×10⁴);
# stk_moneyflow_hsgt northbound columns are stored in 万元 and the tools emit
# 万元 (factor 1 — the legacy ×100 was a confirmed 100x bug, now removed).
MONEYFLOW_AMOUNT_COLUMNS: tuple[str, ...] = tuple(_EMBEDDED_COMMENTS["stk_moneyflow"])
NORTHBOUND_COLUMNS: tuple[str, ...] = ("hgt", "sgt", "north_money")
MONEYFLOW_WAN_TO_YUAN = 10_000.0
NORTHBOUND_RAW_WAN = 10_000.0


def _asserted_raw_yuan_factor(
    table: str, columns: tuple[str, ...], expected_raw_yuan: float
) -> dict[str, float]:
    """Collect registry raw→yuan factors for *columns*, asserting the contract.

    Returns the non-empty factors found. Raises :class:`UnitContractError`
    when any present factor contradicts *expected_raw_yuan*.
    """
    registry = get_registry()
    seen: dict[str, float] = {}
    for column in columns:
        factor = registry.money_factor_to_yuan(table, column)
        if factor is not None:
            seen[column] = factor
    for column, factor in seen.items():
        if factor != expected_raw_yuan:
            raise UnitContractError(
                f"{table}.{column}: registry unit implies raw→yuan factor "
                f"{factor}, contract expects {expected_raw_yuan}"
            )
    return seen


def moneyflow_amount_to_yuan_factor() -> float:
    """Factor converting raw ``stk_moneyflow`` amount columns to CNY yuan.

    Metadata-driven with a transition assertion: present comments.yaml unit
    metadata must agree with the verified ×10⁴ contract or
    :class:`UnitContractError` is raised; missing metadata degrades to the
    legacy constant with a warning. Returns ``10000.0`` (万元 → 元).
    """
    seen = _asserted_raw_yuan_factor(
        "stk_moneyflow", MONEYFLOW_AMOUNT_COLUMNS, MONEYFLOW_WAN_TO_YUAN
    )
    if not seen:
        logger.warning(
            "no unit metadata for stk_moneyflow amount columns; using legacy factor %s",
            MONEYFLOW_WAN_TO_YUAN,
        )
    return MONEYFLOW_WAN_TO_YUAN


def northbound_raw_to_wan_factor() -> float:
    """Factor converting raw ``stk_moneyflow_hsgt`` northbound values to 万元.

    The raw CH/tushare unit is 万元 and the tool output unit is 万元, so the
    correct factor is **1** — the legacy ×100 was a confirmed 100x data bug
    (verified 2026-08-12: CH north_money == tushare live value, ≈37.5亿元
    magnitude) and has been removed on both paths. Present metadata must
    confirm the raw 万元 unit or :class:`UnitContractError` is raised.
    """
    seen = _asserted_raw_yuan_factor(
        "stk_moneyflow_hsgt", NORTHBOUND_COLUMNS, NORTHBOUND_RAW_WAN
    )
    if not seen:
        logger.warning(
            "no unit metadata for stk_moneyflow_hsgt northbound columns; "
            "using corrected factor 1.0 (legacy ×100 removed)"
        )
    return 1.0


# Short, stable English labels for envelope metadata; keyed by the exact
# ``unit=`` strings used in comments.yaml / the embedded fallback.
_UNIT_SHORT_LABELS: dict[str, str] = {
    "lots": "lot",
    "千元 (thousand CNY)": "thousand CNY",
    "万元 (ten-thousand CNY)": "10k CNY",
    "万元 (10k CNY)": "10k CNY",
    "yuan (CNY)": "CNY",
    "percent (%)": "percent",
    "ratio": "dimensionless",
}


def short_unit_label(unit: str | None) -> str | None:
    """Map a raw ``unit=`` string to a short envelope label (or ``None``)."""
    if not unit:
        return None
    return _UNIT_SHORT_LABELS.get(unit.strip(), unit.strip())


def clickhouse_bar_provenance(table: str = "stk_factor_pro") -> dict[str, str]:
    """Additive ``_provenance`` unit metadata for ClickHouse-served bars.

    Always returns the four keys ``volume_unit``, ``amount_unit``,
    ``price_adjust`` and ``caliber``, falling back to verified constants when
    metadata is missing.
    """
    registry = get_registry()
    volume_unit = short_unit_label(registry.unit(table, "vol")) or "lot"
    amount_unit = short_unit_label(registry.unit(table, "amount")) or "thousand CNY"
    price_adjust = registry.adjust(table, "close") or "raw"
    caliber_bits = [f"ClickHouse ashare.{table} (tushare stk_factor_pro caliber)"]
    vol_caliber = registry.caliber(table, "vol")
    amount_caliber = registry.caliber(table, "amount")
    if vol_caliber:
        caliber_bits.append(f"vol: {vol_caliber}")
    if amount_caliber:
        caliber_bits.append(f"amount: {amount_caliber}")
    caliber_bits.append("prices raw on bare columns, hfq on _hfq columns")
    return {
        "volume_unit": volume_unit,
        "amount_unit": amount_unit,
        "price_adjust": price_adjust,
        "caliber": "; ".join(caliber_bits),
    }


def valuation_field_meta(table: str, column: str) -> dict[str, Any]:
    """Return ``{"unit": ..., "caliber": ...}`` for one valuation field.

    Used by the ``get_valuation`` tool so every emitted field carries its
    COMMENT-layer unit and caliber even when comments.yaml is absent.
    """
    registry = get_registry()
    return {
        "unit": registry.unit(table, column),
        "caliber": registry.caliber(table, column),
    }
