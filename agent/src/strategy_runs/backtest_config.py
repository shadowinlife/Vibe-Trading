"""Validate neutral frozen backtest inputs and adapt them in memory only."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Final, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

ConfigValue: TypeAlias = (
    str | int | float | bool | None | list["ConfigValue"] | dict[str, "ConfigValue"]
)

NEUTRAL_CONFIG_FIELDS: Final = (
    "strategy_id",
    "strategy_version",
    "market",
    "symbols",
    "start_date",
    "end_date",
    "bar_interval",
    "data_source",
    "data_as_of",
    "snapshot_basis",
    "initial_cash",
    "currency",
    "commission_bps",
    "tax_bps",
    "slippage_bps",
)

_SHARED_NATIVE_FIELDS: Final = frozenset({"start_date", "end_date", "initial_cash"})
_NEUTRAL_MARKERS: Final = frozenset(NEUTRAL_CONFIG_FIELDS) - _SHARED_NATIVE_FIELDS
_NON_EMPTY_TEXT = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_NATIVE_INTERVALS: Final = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "1H": "1H",
    "4h": "4H",
    "4H": "4H",
    "1d": "1D",
    "1D": "1D",
    "1w": "1W",
    "1W": "1W",
    "1M": "1M",
}

__all__ = [
    "NEUTRAL_CONFIG_FIELDS",
    "BacktestConfigValidationError",
    "ConfigValue",
    "NeutralBacktestConfig",
    "adapt_backtest_config",
    "validate_backtest_chronology",
    "validate_duplicate_provenance",
]


@dataclass(frozen=True, slots=True)
class BacktestConfigValidationError(ValueError):
    """A neutral-config validation failure with a stable field identity."""

    field: str
    reason: str

    def __str__(self) -> str:
        return f"{self.field}: {self.reason}"


class NeutralBacktestConfig(BaseModel):
    """The exact 15-field RM config; every execution assumption is explicit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: _NON_EMPTY_TEXT
    strategy_version: _NON_EMPTY_TEXT
    market: _NON_EMPTY_TEXT
    symbols: tuple[_NON_EMPTY_TEXT, ...] = Field(min_length=1)
    start_date: date
    end_date: date
    bar_interval: _NON_EMPTY_TEXT
    data_source: _NON_EMPTY_TEXT
    data_as_of: date
    snapshot_basis: _NON_EMPTY_TEXT
    initial_cash: Decimal = Field(gt=0)
    currency: _NON_EMPTY_TEXT
    commission_bps: Decimal = Field(ge=0)
    tax_bps: Decimal = Field(ge=0)
    slippage_bps: Decimal = Field(ge=0)

    @field_validator("symbols")
    @classmethod
    def _unique_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject duplicate instruments after boundary whitespace normalization."""
        if len(set(value)) != len(value):
            raise BacktestConfigValidationError(
                field="symbols", reason="symbols must be unique"
            )
        return value

    @field_validator("initial_cash", "commission_bps", "tax_bps", "slippage_bps")
    @classmethod
    def _finite_decimal(cls, value: Decimal, info: ValidationInfo) -> Decimal:
        """Reject values that cannot participate in finite native arithmetic."""
        if not value.is_finite() or not math.isfinite(float(value)):
            raise BacktestConfigValidationError(
                field=info.field_name,
                reason="value and its native representation must be finite",
            )
        return value

    @model_validator(mode="after")
    def _ordered_dates(self) -> NeutralBacktestConfig:
        """Require the whole requested window to exist by the snapshot date."""
        if not self.start_date < self.end_date <= self.data_as_of:
            raise BacktestConfigValidationError(
                field="data_as_of",
                reason="expected start_date < end_date <= data_as_of",
            )
        return self

    def to_native_config(self) -> dict[str, ConfigValue]:
        """Return a new native config without touching the frozen input bytes.

        Returns:
            Native aliases, private provenance, and exact decimal execution costs.

        Raises:
            BacktestConfigValidationError: If the interval has no native equivalent.
        """
        try:
            interval = _NATIVE_INTERVALS[self.bar_interval]
        except KeyError:
            raise BacktestConfigValidationError(
                field="bar_interval",
                reason=f"unsupported interval {self.bar_interval!r}",
            ) from None
        return {
            "codes": list(self.symbols),
            "source": self.data_source,
            "interval": interval,
            "initial_cash": float(self.initial_cash),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "neutral_execution_costs": {
                "commission_bps": str(self.commission_bps),
                "tax_bps": str(self.tax_bps),
                "slippage_bps": str(self.slippage_bps),
            },
            "_neutral_market": self.market,
            "_neutral_currency": self.currency,
            "_neutral_strategy_id": self.strategy_id,
            "_neutral_strategy_version": self.strategy_version,
            "_neutral_snapshot_basis": self.snapshot_basis,
        }


def validate_backtest_chronology(
    config: dict[str, ConfigValue],
) -> NeutralBacktestConfig:
    """Parse one neutral config at the boundary before any execution side effect.

    Args:
        config: Candidate neutral config with exactly the 15 declared fields.

    Returns:
        The parsed immutable config with ordered dates and unique symbols.

    Raises:
        ValidationError: If a required field, value, or chronology rule is invalid.
    """
    return NeutralBacktestConfig.model_validate(config)


def validate_duplicate_provenance(
    config: dict[str, ConfigValue],
    *,
    data_source: str,
    data_as_of: str,
    snapshot_basis: str,
) -> NeutralBacktestConfig:
    """Require duplicate wire provenance to equal the embedded config exactly.

    Args:
        config: Embedded neutral config to validate before materialization/execution.
        data_source: Duplicate top-level source declaration.
        data_as_of: Duplicate top-level snapshot date declaration.
        snapshot_basis: Duplicate top-level snapshot basis declaration.

    Returns:
        The parsed config after both chronology and exact provenance checks.

    Raises:
        ValidationError: If the embedded neutral config is invalid.
        BacktestConfigValidationError: If any provenance pair differs.
    """
    parsed = validate_backtest_chronology(config)
    supplied = {
        "data_source": data_source,
        "data_as_of": data_as_of,
        "snapshot_basis": snapshot_basis,
    }
    for field, value in supplied.items():
        if config.get(field) != value:
            raise BacktestConfigValidationError(
                field=field, reason="top-level provenance differs from config"
            )
    return parsed


def adapt_backtest_config(config: dict[str, ConfigValue]) -> dict[str, ConfigValue]:
    """Adapt neutral input, or return a legacy native config object unchanged.

    Args:
        config: Parsed JSON object from a caller-owned config file.

    Returns:
        A new native config for neutral input, or the original legacy object.

    Raises:
        ValidationError: If a neutral marker is present but the config is invalid.
        BacktestConfigValidationError: If the interval has no native equivalent.
    """
    if _NEUTRAL_MARKERS.isdisjoint(config):
        return config
    return validate_backtest_chronology(config).to_native_config()
