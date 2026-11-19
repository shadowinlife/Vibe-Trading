"""Frozen alpha-bench input contract and label-end mask.

Part A of the frozen-bench work: this module validates the submitted
``alpha_zoo`` candidate specification and decides which factor rows carry a
fully realised forward label. It is deliberately pure — no loader, network,
MCP, or CSV side effects — so Part B can apply the mask to the frame produced
by ``_compute_forward_returns`` without changing how prices are loaded.

Label ends are resolved by **position** in the observed close index
(``close_index[pos + horizon]``), never by calendar-day arithmetic: weekends,
exchange holidays and halted bars all consume one trading bar each, so a
``date + timedelta`` window would silently look ahead or shorten the horizon.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from src.tools.path_utils import safe_run_dir

_CANDIDATE_ID_PATTERN = r"^[a-z][a-z0-9]+_[a-z0-9_]+$"
_MAX_SYMBOLS = 500
_MAX_HORIZON = 252

PHASE_IN_SAMPLE = "in_sample"
PHASE_OUT_OF_SAMPLE = "out_of_sample"
_PHASES = (PHASE_IN_SAMPLE, PHASE_OUT_OF_SAMPLE)

_SOURCE = "alpha_zoo"
_SPEC_FIELDS = (
    "candidate_id",
    "phase",
    "symbols",
    "start_date",
    "end_date",
    "horizon",
    "source",
    "split_date",
    "as_of",
    "output_dir",
)

__all__ = [
    "FrozenBenchError",
    "FrozenBenchSpec",
    "FrozenFrames",
    "build_frozen_frames",
    "label_end_dates",
    "usable_label_mask",
    "validate_frozen_bench_spec",
]


class FrozenBenchError(ValueError):
    """Rejected frozen-bench input.

    Attributes:
        field: Name of the offending payload field (or mask argument).
        reason: Human-readable explanation of the rejection.
    """

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


def _chronology_violation(
    start_date: date,
    split_date: date,
    end_date: date,
    as_of: date,
) -> tuple[str, str] | None:
    """Return the first chronology violation as ``(field, reason)``.

    The required ordering is ``start_date < split_date <= end_date <= as_of``.
    """
    if start_date >= split_date:
        return ("split_date", "split_date must be later than start_date")
    if split_date > end_date:
        return ("end_date", "end_date must be on or after split_date")
    if end_date > as_of:
        return ("as_of", "as_of must be on or after end_date")
    return None


class FrozenBenchSpec(BaseModel):
    """Validated, immutable alpha-bench candidate request.

    The model forbids undeclared keys (so a ``universe`` override cannot sneak
    in) and freezes assignment. ``output_dir`` is checked against the same
    ``safe_run_dir`` roots used by the file tools, which rejects arbitrary
    absolute paths.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(pattern=_CANDIDATE_ID_PATTERN)
    phase: Literal["in_sample", "out_of_sample"]
    symbols: tuple[str, ...] = Field(min_length=1, max_length=_MAX_SYMBOLS)
    start_date: date
    end_date: date
    horizon: int = Field(ge=1, le=_MAX_HORIZON)
    source: Literal["alpha_zoo"]
    split_date: date
    as_of: date
    output_dir: Path

    @field_validator("symbols", mode="before")
    @classmethod
    def _validate_symbols(cls, value: Any) -> tuple[str, ...]:
        """Require 1..500 unique, non-blank symbols, preserved byte-for-byte.

        Symbols are identities, not display labels: a padded ``" AAPL "`` is
        rejected rather than trimmed, so the contract never silently rewrites
        the caller's ticker.
        """
        if isinstance(value, (str, bytes)) or not isinstance(
            value, (list, tuple, set, frozenset)
        ):
            raise ValueError("symbols must be a sequence of symbol strings")
        symbols: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("symbols must contain only strings")
            if item != item.strip():
                raise ValueError(
                    f"symbols must not contain leading/trailing whitespace: {item!r}"
                )
            if not item:
                raise ValueError("symbols must not contain blank entries")
            if item in seen:
                raise ValueError(f"symbols must be unique; duplicate {item!r}")
            seen.add(item)
            symbols.append(item)
        if not symbols:
            raise ValueError(
                f"symbols must contain between 1 and {_MAX_SYMBOLS} entries"
            )
        if len(symbols) > _MAX_SYMBOLS:
            raise ValueError(
                f"symbols must contain between 1 and {_MAX_SYMBOLS} entries"
            )
        return tuple(symbols)

    @field_validator("horizon", mode="before")
    @classmethod
    def _reject_bool_horizon(cls, value: Any) -> Any:
        """Reject booleans, which pydantic otherwise coerces to 0/1."""
        if isinstance(value, bool):
            raise ValueError(f"horizon must be an integer between 1 and {_MAX_HORIZON}")
        return value

    @field_validator("output_dir", mode="before")
    @classmethod
    def _validate_output_dir(cls, value: Any) -> Path:
        """Require an absolute path inside the allowed run roots."""
        if not isinstance(value, (str, Path)):
            raise ValueError("output_dir must be an absolute filesystem path")
        candidate = Path(value)
        if not candidate.is_absolute():
            raise ValueError("output_dir must be an absolute path")
        try:
            return safe_run_dir(str(candidate))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def _validate_chronology(self) -> FrozenBenchSpec:
        """Enforce ``start_date < split_date <= end_date <= as_of``."""
        violation = _chronology_violation(
            self.start_date, self.split_date, self.end_date, self.as_of
        )
        if violation is not None:
            field, reason = violation
            raise ValueError(f"{field}: {reason}")
        return self


def _validation_error_fields(exc: ValidationError) -> tuple[str, str]:
    """Map the first pydantic error back to a payload field and reason."""
    error = exc.errors()[0]
    message = str(error.get("msg", "invalid value")).removeprefix("Value error, ")
    location = error.get("loc") or ()
    if location:
        return str(location[0]), message
    for field in _SPEC_FIELDS:
        prefix = f"{field}:"
        if message.startswith(prefix):
            return field, message[len(prefix) :].strip()
    return "payload", message


def validate_frozen_bench_spec(payload: Mapping[str, Any]) -> FrozenBenchSpec:
    """Validate a raw frozen-bench payload into a :class:`FrozenBenchSpec`.

    Args:
        payload: Caller-supplied mapping, typically decoded JSON.

    Returns:
        The validated, frozen specification.

    Raises:
        FrozenBenchError: If the payload is not a mapping, any field is
            invalid, an undeclared key is present, the chronology is broken,
            or ``output_dir`` escapes the allowed run roots.
    """
    if not isinstance(payload, Mapping):
        raise FrozenBenchError(
            "payload", f"payload must be a mapping, got {type(payload).__name__}"
        )
    try:
        return FrozenBenchSpec.model_validate(dict(payload))
    except ValidationError as exc:
        field, reason = _validation_error_fields(exc)
        raise FrozenBenchError(field, reason) from exc


def _as_date(value: Any, name: str) -> date:
    """Normalise a ``date``/``datetime`` argument, rejecting other types."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise FrozenBenchError(name, f"{name} must be a date, got {type(value).__name__}")


def _validated_horizon(horizon: Any) -> int:
    """Require a positive integer bar horizon (booleans rejected)."""
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise FrozenBenchError(
            "horizon", f"horizon must be an integer >= 1, got {horizon!r}"
        )
    return horizon


def _label_positions(
    factor_dates: pd.DatetimeIndex,
    close_index: pd.DatetimeIndex,
    horizon: int,
) -> tuple[int, ...] | None:
    """Map factor dates to positional close-index rows.

    Returns:
        One positional index per factor date (``-1`` for a missing date), or
        ``None`` when the two indexes cannot be aligned safely: non-Datetime
        inputs, mixed timezone awareness, or a close index that is not unique
        and strictly increasing.
    """
    if not isinstance(factor_dates, pd.DatetimeIndex) or not isinstance(
        close_index, pd.DatetimeIndex
    ):
        return None
    if (factor_dates.tz is None) != (close_index.tz is None):
        return None
    if not close_index.is_unique or not close_index.is_monotonic_increasing:
        return None
    try:
        positions = close_index.get_indexer(factor_dates)
    except (TypeError, ValueError, pd.errors.InvalidIndexError):
        return None
    return tuple(int(position) for position in positions)


def label_end_dates(
    *,
    factor_dates: pd.DatetimeIndex,
    close_index: pd.DatetimeIndex,
    horizon: int,
) -> tuple[date | None, ...]:
    """Return each factor row's positional label end, or ``None``.

    A position is unusable when the factor date is absent from the close index
    or when ``pos + horizon`` runs past the last observed bar; the window is
    never shortened and no future row is invented.

    Args:
        factor_dates: Timestamps of the factor rows being labelled.
        close_index: Observed trading-bar index of the close frame.
        horizon: Forward horizon in trading bars (``>= 1``).

    Returns:
        A tuple aligned to ``factor_dates``.
    """
    horizon = _validated_horizon(horizon)
    factor_count = len(factor_dates)
    positions = _label_positions(factor_dates, close_index, horizon)
    if positions is None:
        return (None,) * factor_count
    bar_count = len(close_index)
    ends: list[date | None] = []
    for position in positions:
        if position < 0 or position + horizon >= bar_count:
            ends.append(None)
        else:
            ends.append(close_index[position + horizon].date())
    return tuple(ends)


def usable_label_mask(
    *,
    factor_dates: pd.DatetimeIndex,
    close_index: pd.DatetimeIndex,
    horizon: int,
    phase: str,
    split_date: date,
    end_date: date,
    as_of: date,
) -> tuple[bool, ...]:
    """Return which factor rows have a fully realised, in-window label.

    ``in_sample`` rows are usable only when the label ends strictly before
    ``split_date``. ``out_of_sample`` rows additionally require the factor date
    to be on or after the split and the label to end on or before both
    ``end_date`` and ``as_of``.

    Args:
        factor_dates: Timestamps of the factor rows being labelled.
        close_index: Observed trading-bar index of the close frame.
        horizon: Forward horizon in trading bars (``>= 1``).
        phase: ``in_sample`` or ``out_of_sample``.
        split_date: First out-of-sample date.
        end_date: Last date whose label may be consumed.
        as_of: Information cutoff; labels beyond it are future data.

    Returns:
        A tuple of booleans aligned to ``factor_dates``; unavailable rows,
        missing factor dates and misaligned indexes are all ``False``.

    Raises:
        FrozenBenchError: If ``phase``, ``horizon`` or a date argument is
            invalid.
    """
    horizon = _validated_horizon(horizon)
    if phase not in _PHASES:
        raise FrozenBenchError(
            "phase", f"phase must be one of {list(_PHASES)}, got {phase!r}"
        )
    split = _as_date(split_date, "split_date")
    end = _as_date(end_date, "end_date")
    cutoff = _as_date(as_of, "as_of")

    factor_count = len(factor_dates)
    positions = _label_positions(factor_dates, close_index, horizon)
    if positions is None:
        return (False,) * factor_count

    bar_count = len(close_index)
    usable: list[bool] = []
    for factor_date, position in zip(factor_dates, positions, strict=True):
        if position < 0 or position + horizon >= bar_count:
            usable.append(False)
            continue
        label_end = close_index[position + horizon].date()
        if phase == PHASE_IN_SAMPLE:
            usable.append(label_end < split)
        else:
            usable.append(
                factor_date.date() >= split and label_end <= end and label_end <= cutoff
            )
    return tuple(usable)


@dataclass(frozen=True, slots=True)
class FrozenFrames:
    """Aligned factor and masked forward-return frames for one bench phase.

    ``forward_returns`` keeps every cell whose label is unusable as ``NaN``;
    input gaps stay ``NaN`` too, because the builder never fills.
    """

    factor: pd.DataFrame
    forward_returns: pd.DataFrame
    n_obs: int
    n_dates: int
    status: str
    reasons: tuple[str, ...]


def _validated_frame(frame: Any, name: str) -> pd.DataFrame:
    """Require a label-unique DataFrame so rows map unambiguously to dates."""
    if not isinstance(frame, pd.DataFrame):
        raise FrozenBenchError(
            name, f"{name} must be a pandas DataFrame, got {type(frame).__name__}"
        )
    if not frame.index.is_unique:
        raise FrozenBenchError(name, f"{name} index must not contain duplicates")
    if not frame.columns.is_unique:
        raise FrozenBenchError(name, f"{name} columns must not contain duplicates")
    return frame


def _sorted_intersection(left: pd.Index, right: pd.Index) -> pd.Index:
    """Return the label intersection in ascending order."""
    return left.intersection(right).sort_values()


def build_frozen_frames(
    *,
    factor: pd.DataFrame,
    forward_returns: pd.DataFrame,
    phase: str,
    horizon: int,
    split_date: date,
    end_date: date,
    as_of: date,
) -> FrozenFrames:
    """Align a loaded panel and mask every not-fully-realised label.

    The mask is computed against the **unaligned** ``forward_returns`` index,
    which is the observed close index produced by ``_compute_forward_returns``:
    intersecting first would shorten a positional window whenever the factor
    frame omits intermediate bars. Both frames are then restricted to their
    sorted common index and columns; gaps stay ``NaN`` and are never filled.

    Args:
        factor: Factor values indexed by trading date, columns by symbol.
        forward_returns: Forward returns on the observed close index.
        phase: ``in_sample`` or ``out_of_sample``.
        horizon: Forward horizon in trading bars (``>= 1``).
        split_date: First out-of-sample date.
        end_date: Last date whose label may be consumed.
        as_of: Information cutoff; labels beyond it are future data.

    Returns:
        The aligned factor frame, the masked forward-return frame, the paired
        observation counts, and a status/reason pair. ``insufficient_data``
        always carries ``n_obs == 0`` and never fabricates values.

    Raises:
        FrozenBenchError: If a frame is not a label-unique DataFrame, or
            ``phase``, ``horizon`` or a date argument is invalid.
    """
    factor = _validated_frame(factor, "factor")
    forward_returns = _validated_frame(forward_returns, "forward_returns")

    usable = usable_label_mask(
        factor_dates=factor.index,
        close_index=forward_returns.index,
        horizon=horizon,
        phase=phase,
        split_date=split_date,
        end_date=end_date,
        as_of=as_of,
    )

    common_index = _sorted_intersection(factor.index, forward_returns.index)
    common_columns = _sorted_intersection(factor.columns, forward_returns.columns)
    aligned_factor = factor.reindex(index=common_index, columns=common_columns)
    aligned_returns = forward_returns.reindex(
        index=common_index, columns=common_columns
    )

    usable_by_date = dict(zip(factor.index, usable, strict=True))
    row_usable = pd.Series(
        [usable_by_date[label] for label in common_index],
        index=common_index,
        dtype=bool,
    )
    masked_returns = aligned_returns.copy()
    masked_returns.loc[~row_usable, :] = float("nan")

    paired = aligned_factor.notna() & masked_returns.notna()
    n_obs = int(paired.to_numpy().sum())
    n_dates = int(paired.any(axis=1).sum())

    reasons: tuple[str, ...] = ()
    if n_obs == 0:
        if aligned_factor.empty:
            reasons = ("empty_overlap",)
        else:
            positions = _label_positions(factor.index, forward_returns.index, horizon)
            resolvable = positions is not None and any(
                0 <= position < len(forward_returns.index) - horizon
                for position in positions
            )
            if positions is None:
                reasons = ("misaligned_index",)
            elif not resolvable:
                reasons = ("horizon_past_end",)
            elif not any(usable):
                reasons = (
                    (
                        "no_in_sample_labels"
                        if phase == PHASE_IN_SAMPLE
                        else "no_out_of_sample_labels"
                    ),
                )
            else:
                reasons = ("no_paired_observations",)

    return FrozenFrames(
        factor=aligned_factor,
        forward_returns=masked_returns,
        n_obs=n_obs,
        n_dates=n_dates,
        status="ok" if n_obs else "insufficient_data",
        reasons=reasons,
    )
