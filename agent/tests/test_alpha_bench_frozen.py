"""Contract tests for frozen alpha-bench inputs and label-end masking.

Every mask assertion is a known-answer case over an explicit trading calendar:
the positional expectation comes from the bar list, never from calendar-day
arithmetic, so a `date + timedelta` implementation cannot pass.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from pydantic import ValidationError

from src.tools.alpha_bench_contract import (
    FrozenBenchError,
    FrozenBenchSpec,
    FrozenFrames,
    build_frozen_frames,
    label_end_dates,
    usable_label_mask,
    validate_frozen_bench_spec,
)

# 2024-01-04 and the weekend are absent: bars jump Thu->Fri without Jan 4.
CLOSE_INDEX = pd.DatetimeIndex(
    [
        "2024-01-02",
        "2024-01-03",
        "2024-01-05",
        "2024-01-08",
        "2024-01-09",
        "2024-01-10",
        "2024-01-11",
        "2024-01-12",
        "2024-01-15",
        "2024-01-16",
        "2024-01-17",
        "2024-01-18",
        "2024-01-19",
    ]
)


def _payload(root, **overrides):
    data = {
        "candidate_id": "alpha_zoo_001",
        "phase": "in_sample",
        "symbols": ["000001.SZ", "600000.SH"],
        "start_date": "2024-01-02",
        "end_date": "2024-01-19",
        "horizon": 3,
        "source": "alpha_zoo",
        "split_date": "2024-01-15",
        "as_of": "2024-01-19",
        "output_dir": str(root / "candidate"),
    }
    data.update(overrides)
    return data


def _mask(days, *, horizon, phase, split, end, as_of, close_index=CLOSE_INDEX):
    return usable_label_mask(
        factor_dates=pd.DatetimeIndex(days),
        close_index=close_index,
        horizon=horizon,
        phase=phase,
        split_date=split,
        end_date=end,
        as_of=as_of,
    )


def _error_field(payload) -> str:
    with pytest.raises(FrozenBenchError) as excinfo:
        validate_frozen_bench_spec(payload)
    return excinfo.value.field


@pytest.fixture
def run_root(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(root))
    return root


# ---------------------------------------------------------------------------
# Spec validation
# ---------------------------------------------------------------------------


def test_valid_payload_builds_immutable_spec(run_root):
    spec = validate_frozen_bench_spec(_payload(run_root))

    assert isinstance(spec, FrozenBenchSpec)
    assert spec.candidate_id == "alpha_zoo_001"
    assert spec.phase == "in_sample"
    assert spec.symbols == ("000001.SZ", "600000.SH")
    assert spec.start_date == date(2024, 1, 2)
    assert spec.end_date == date(2024, 1, 19)
    assert spec.horizon == 3
    assert spec.source == "alpha_zoo"
    assert spec.split_date == date(2024, 1, 15)
    assert spec.as_of == date(2024, 1, 19)
    assert spec.output_dir == (run_root / "candidate").resolve()

    with pytest.raises(ValidationError):
        spec.horizon = 1


@pytest.mark.parametrize("bad", ["", "ab", "a_x", "1ab_x", "Ab_x", "ab-x", "ab_"])
def test_candidate_id_shape_is_enforced(run_root, bad):
    assert _error_field(_payload(run_root, candidate_id=bad)) == "candidate_id"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("phase", "walk_forward"),
        ("source", "manual"),
        ("horizon", 0),
        ("horizon", 253),
        ("horizon", None),
    ],
)
def test_unknown_phase_source_and_bad_horizon_are_rejected(run_root, field, bad):
    assert _error_field(_payload(run_root, **{field: bad})) == field


def test_missing_horizon_is_rejected(run_root):
    payload = _payload(run_root)
    del payload["horizon"]
    assert _error_field(payload) == "horizon"


def test_universe_override_is_rejected(run_root):
    assert _error_field(_payload(run_root, universe="csi300")) == "universe"


def test_duplicate_symbols_are_rejected(run_root):
    payload = _payload(run_root, symbols=["000001.SZ", "000001.SZ"])
    assert _error_field(payload) == "symbols"


@pytest.mark.parametrize(
    "symbols", [[], [""], ["   "], [f"S{i:04d}" for i in range(501)]]
)
def test_symbol_count_and_emptiness_are_enforced(run_root, symbols):
    assert _error_field(_payload(run_root, symbols=symbols)) == "symbols"


def test_five_hundred_unique_symbols_are_accepted(run_root):
    symbols = [f"S{i:04d}" for i in range(500)]
    spec = validate_frozen_bench_spec(_payload(run_root, symbols=symbols))
    assert len(spec.symbols) == 500


@pytest.mark.parametrize(
    "symbol", [" AAPL ", "AAPL ", " AAPL", "\tAAPL", "AAPL\n", "600000.SH\t"]
)
def test_whitespace_padded_symbols_are_rejected_not_trimmed(run_root, symbol):
    payload = _payload(run_root, symbols=["000001.SZ", symbol])
    with pytest.raises(FrozenBenchError) as excinfo:
        validate_frozen_bench_spec(payload)
    assert excinfo.value.field == "symbols"
    assert "whitespace" in excinfo.value.reason


def test_valid_symbols_are_preserved_byte_for_byte(run_root):
    symbols = ["aapl", "AAPL", "600000.SH", "BTC-USD", "000001.SZ"]
    spec = validate_frozen_bench_spec(_payload(run_root, symbols=symbols))
    assert spec.symbols == tuple(symbols)
    # Case-sensitive uniqueness: lower/upper variants stay distinct identities.
    assert len(spec.symbols) == len(set(symbols))


@pytest.mark.parametrize(
    "overrides,field",
    [
        ({"start_date": "2024-01-15"}, "split_date"),
        ({"split_date": "2024-01-20"}, "end_date"),
        ({"as_of": "2024-01-18"}, "as_of"),
    ],
)
def test_chronology_violations_name_the_offending_field(run_root, overrides, field):
    assert _error_field(_payload(run_root, **overrides)) == field


def test_relative_output_dir_is_rejected(run_root):
    assert _error_field(_payload(run_root, output_dir="runs/candidate")) == "output_dir"


def test_output_dir_outside_allowed_run_roots_is_rejected(run_root, tmp_path):
    outside = tmp_path / "elsewhere"
    assert _error_field(_payload(run_root, output_dir=str(outside))) == "output_dir"


# ---------------------------------------------------------------------------
# Label-end mask
# ---------------------------------------------------------------------------


def test_in_sample_horizon_one_known_answers():
    mask = _mask(
        ["2024-01-03", "2024-01-09", "2024-01-12"],
        horizon=1,
        phase="in_sample",
        split=date(2024, 1, 15),
        end=date(2024, 1, 19),
        as_of=date(2024, 1, 19),
    )
    # Jan 3 -> next bar Jan 5 (Jan 4 gap); Jan 9 -> Jan 10; Jan 12 -> Jan 15 == split.
    assert mask == (True, True, False)


def test_in_sample_horizon_three_uses_positional_bars():
    assert label_end_dates(
        factor_dates=pd.DatetimeIndex(["2024-01-02"]),
        close_index=CLOSE_INDEX,
        horizon=3,
    ) == (date(2024, 1, 8),)
    mask = _mask(
        ["2024-01-02", "2024-01-11"],
        horizon=3,
        phase="in_sample",
        split=date(2024, 1, 15),
        end=date(2024, 1, 19),
        as_of=date(2024, 1, 19),
    )
    # Jan 2 pos 0 + 3 = Jan 8 (< split); Jan 11 pos 6 + 3 = Jan 16 (>= split).
    assert mask == (True, False)


def test_label_end_skips_weekend_and_gap_without_calendar_arithmetic():
    ends = label_end_dates(
        factor_dates=pd.DatetimeIndex(["2024-01-03", "2024-01-05"]),
        close_index=CLOSE_INDEX,
        horizon=1,
    )
    assert ends == (date(2024, 1, 5), date(2024, 1, 8))


def test_out_of_sample_requires_split_end_and_as_of():
    mask = _mask(
        ["2024-01-09", "2024-01-10", "2024-01-15", "2024-01-16", "2024-01-17"],
        horizon=1,
        phase="out_of_sample",
        split=date(2024, 1, 10),
        end=date(2024, 1, 16),
        as_of=date(2024, 1, 16),
    )
    # Pre-split -> False; Jan 15 label Jan 16 == end == as_of -> True;
    # Jan 16/17 labels fall past both bounds -> False.
    assert mask == (False, True, True, False, False)


def test_out_of_sample_label_exactly_on_end_is_usable():
    mask = _mask(
        ["2024-01-10", "2024-01-11"],
        horizon=2,
        phase="out_of_sample",
        split=date(2024, 1, 10),
        end=date(2024, 1, 12),
        as_of=date(2024, 1, 19),
    )
    # Jan 10 pos 5 + 2 = Jan 12 == end; Jan 11 pos 6 + 2 = Jan 15 > end.
    assert mask == (True, False)


def test_out_of_sample_label_past_as_of_is_unusable():
    mask = _mask(
        ["2024-01-15", "2024-01-16"],
        horizon=1,
        phase="out_of_sample",
        split=date(2024, 1, 10),
        end=date(2024, 1, 19),
        as_of=date(2024, 1, 16),
    )
    # Jan 15 -> Jan 16 == as_of; Jan 16 -> Jan 17 > as_of.
    assert mask == (True, False)


def test_factor_date_missing_from_close_index_is_false():
    mask = _mask(
        ["2024-01-06", "2024-01-09"],
        horizon=1,
        phase="in_sample",
        split=date(2024, 1, 15),
        end=date(2024, 1, 19),
        as_of=date(2024, 1, 19),
    )
    assert mask == (False, True)


def test_window_running_past_the_last_close_bar_is_false():
    mask = _mask(
        ["2024-01-18", "2024-01-19"],
        horizon=2,
        phase="in_sample",
        split=date(2024, 1, 19),
        end=date(2024, 1, 19),
        as_of=date(2024, 1, 19),
    )
    assert mask == (False, False)
    assert label_end_dates(
        factor_dates=pd.DatetimeIndex(["2024-01-18"]),
        close_index=CLOSE_INDEX,
        horizon=2,
    ) == (None,)


def test_unsorted_or_duplicated_close_index_is_misaligned():
    unsorted = CLOSE_INDEX[::-1]
    duplicated = pd.DatetimeIndex([CLOSE_INDEX[0], *CLOSE_INDEX])
    for index in (unsorted, duplicated):
        mask = _mask(
            ["2024-01-09"],
            horizon=1,
            phase="in_sample",
            split=date(2024, 1, 15),
            end=date(2024, 1, 19),
            as_of=date(2024, 1, 19),
            close_index=index,
        )
        assert mask == (False,)


def test_timezone_mismatch_is_misaligned():
    tz_aware = CLOSE_INDEX.tz_localize("UTC")
    mask = _mask(
        ["2024-01-09"],
        horizon=1,
        phase="in_sample",
        split=date(2024, 1, 15),
        end=date(2024, 1, 19),
        as_of=date(2024, 1, 19),
        close_index=tz_aware,
    )
    assert mask == (False,)


def test_empty_factor_dates_return_an_empty_tuple():
    assert (
        _mask(
            [],
            horizon=1,
            phase="in_sample",
            split=date(2024, 1, 15),
            end=date(2024, 1, 19),
            as_of=date(2024, 1, 19),
        )
        == ()
    )


def test_duplicate_factor_dates_stay_aligned_to_input_order():
    mask = _mask(
        ["2024-01-09", "2024-01-09"],
        horizon=1,
        phase="in_sample",
        split=date(2024, 1, 15),
        end=date(2024, 1, 19),
        as_of=date(2024, 1, 19),
    )
    assert mask == (True, True)


@pytest.mark.parametrize("phase", ["walk_forward", ""])
def test_unknown_mask_phase_is_rejected(phase):
    with pytest.raises(FrozenBenchError) as excinfo:
        _mask(
            ["2024-01-09"],
            horizon=1,
            phase=phase,
            split=date(2024, 1, 15),
            end=date(2024, 1, 19),
            as_of=date(2024, 1, 19),
        )
    assert excinfo.value.field == "phase"


@pytest.mark.parametrize("horizon", [0, -1, True, 1.5])
def test_invalid_mask_horizon_is_rejected(horizon):
    with pytest.raises(FrozenBenchError) as excinfo:
        _mask(
            ["2024-01-09"],
            horizon=horizon,
            phase="in_sample",
            split=date(2024, 1, 15),
            end=date(2024, 1, 19),
            as_of=date(2024, 1, 19),
        )
    assert excinfo.value.field == "horizon"


# ---------------------------------------------------------------------------
# Frozen factor/forward-return frames
# ---------------------------------------------------------------------------

FRAME_DATES = pd.DatetimeIndex(
    [
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
        "2024-01-05",
        "2024-01-08",
        "2024-01-09",
        "2024-01-10",
        "2024-01-11",
        "2024-01-12",
        "2024-01-15",
        "2024-01-16",
        "2024-01-17",
    ]
)


def _factor_frame(index=FRAME_DATES):
    return pd.DataFrame(
        {
            "AAA": [10.0 + position for position in range(len(index))],
            "BBB": [100.0 + position for position in range(len(index))],
        },
        index=index,
    )


def _return_frame(index=FRAME_DATES):
    return pd.DataFrame(
        {
            "AAA": [(position + 1) / 100 for position in range(len(index))],
            "BBB": [(position + 1) / 50 for position in range(len(index))],
        },
        index=index,
    )


def _build(**overrides):
    inputs = {
        "factor": _factor_frame(),
        "forward_returns": _return_frame(),
        "phase": "in_sample",
        "horizon": 2,
        "split_date": date(2024, 1, 10),
        "end_date": date(2024, 1, 17),
        "as_of": date(2024, 1, 17),
    }
    inputs.update(overrides)
    return build_frozen_frames(**inputs)


def _row_kept(frame, day) -> bool:
    return bool(frame.loc[day].notna().all())


def _row_masked(frame, day) -> bool:
    return bool(frame.loc[day].isna().all())


def test_in_sample_masks_every_label_touching_the_split():
    frames = _build()

    assert isinstance(frames, FrozenFrames)
    assert frames.status == "ok"
    assert frames.reasons == ()
    assert frames.factor.index.equals(FRAME_DATES)
    assert _row_kept(frames.factor, "2024-01-02")
    # 2024-01-08 is the last usable row: its h=2 label ends on the split.
    assert _row_kept(frames.forward_returns, "2024-01-05")
    assert _row_masked(frames.forward_returns, "2024-01-08")
    for day in FRAME_DATES[4:]:
        assert _row_masked(frames.forward_returns, day.strftime("%Y-%m-%d"))
    assert frames.forward_returns.loc["2024-01-05", "AAA"] == pytest.approx(0.04)
    assert frames.n_obs == 8
    assert frames.n_dates == 4


def test_label_ending_exactly_on_split_is_unusable_in_sample():
    frames = _build(split_date=date(2024, 1, 12))

    # 2024-01-09 labels 2024-01-11 (< split); 2024-01-10 labels the split itself.
    assert _row_kept(frames.forward_returns, "2024-01-09")
    assert _row_masked(frames.forward_returns, "2024-01-10")
    assert frames.n_obs == 12
    assert frames.n_dates == 6


def test_out_of_sample_drops_pre_split_factor_dates():
    frames = _build(
        phase="out_of_sample",
        split_date=date(2024, 1, 8),
        end_date=date(2024, 1, 11),
    )

    # 2024-01-05 labels 2024-01-09 inside [split, end], but its factor date is pre-split.
    assert _row_masked(frames.forward_returns, "2024-01-05")
    assert _row_kept(frames.forward_returns, "2024-01-08")
    assert _row_kept(frames.forward_returns, "2024-01-09")
    assert _row_masked(frames.forward_returns, "2024-01-10")
    assert frames.n_obs == 4
    assert frames.n_dates == 2


def test_out_of_sample_label_on_split_and_end_is_kept():
    frames = _build(
        phase="out_of_sample",
        split_date=date(2024, 1, 10),
        end_date=date(2024, 1, 12),
    )

    assert _row_masked(frames.forward_returns, "2024-01-09")
    assert _row_kept(frames.forward_returns, "2024-01-10")
    assert _row_masked(frames.forward_returns, "2024-01-11")
    assert frames.n_obs == 2
    assert frames.n_dates == 1


def test_label_past_as_of_is_masked():
    frames = _build(
        phase="out_of_sample",
        split_date=date(2024, 1, 2),
        end_date=date(2024, 1, 17),
        as_of=date(2024, 1, 10),
    )

    assert _row_kept(frames.forward_returns, "2024-01-08")
    assert _row_masked(frames.forward_returns, "2024-01-09")
    assert frames.n_obs == 10
    assert frames.n_dates == 5


def test_alignment_uses_the_sorted_intersection_of_labels():
    factor = _factor_frame(FRAME_DATES[::-1])
    factor["CCC"] = 7.0
    returns = _return_frame()
    returns["DDD"] = 0.5

    frames = _build(factor=factor, forward_returns=returns)

    assert list(frames.factor.columns) == ["AAA", "BBB"]
    assert frames.factor.index.equals(FRAME_DATES)
    assert frames.factor.loc["2024-01-02", "AAA"] == 21.0
    assert frames.factor.loc["2024-01-17", "AAA"] == 10.0
    assert frames.forward_returns.loc["2024-01-02", "BBB"] == pytest.approx(0.02)
    assert frames.n_obs == 8
    assert frames.n_dates == 4


def test_missing_cells_stay_nan_and_only_pairs_are_counted():
    factor = _factor_frame()
    factor.loc["2024-01-02", "AAA"] = float("nan")
    returns = _return_frame()
    returns.loc["2024-01-03", "BBB"] = float("nan")

    frames = _build(factor=factor, forward_returns=returns)

    assert frames.status == "ok"
    assert frames.n_obs == 6
    assert frames.n_dates == 4
    assert pd.isna(frames.factor.loc["2024-01-02", "AAA"])
    assert frames.factor.loc["2024-01-02", "BBB"] == 100.0
    assert pd.isna(frames.forward_returns.loc["2024-01-03", "BBB"])
    assert frames.forward_returns.loc["2024-01-03", "AAA"] == pytest.approx(0.02)


def test_empty_overlap_is_insufficient_without_fabricated_values():
    february = pd.DatetimeIndex(["2024-02-01", "2024-02-02"])
    frames = _build(factor=_factor_frame(february))

    assert frames.status == "insufficient_data"
    assert frames.reasons == ("empty_overlap",)
    assert frames.n_obs == 0
    assert frames.n_dates == 0
    assert frames.factor.shape == (0, 2)
    assert frames.forward_returns.shape == (0, 2)


def test_unsorted_close_index_is_insufficient_not_shortened():
    frames = _build(forward_returns=_return_frame(FRAME_DATES[::-1]))

    assert frames.status == "insufficient_data"
    assert frames.reasons == ("misaligned_index",)
    assert frames.n_obs == 0
    assert frames.n_dates == 0
    assert frames.forward_returns.isna().all().all()


def test_horizon_past_the_last_bar_is_insufficient_not_shortened():
    tail = FRAME_DATES[-2:]
    frames = _build(factor=_factor_frame(tail))

    assert label_end_dates(factor_dates=tail, close_index=FRAME_DATES, horizon=2) == (
        None,
        None,
    )
    assert frames.status == "insufficient_data"
    assert frames.reasons == ("horizon_past_end",)
    assert frames.n_obs == 0
    assert frames.n_dates == 0
    assert frames.forward_returns.isna().all().all()


@pytest.mark.parametrize(
    ("phase", "split", "end", "reason"),
    [
        ("in_sample", date(2024, 1, 10), date(2024, 1, 17), "no_in_sample_labels"),
        (
            "out_of_sample",
            date(2024, 1, 10),
            date(2024, 1, 10),
            "no_out_of_sample_labels",
        ),
    ],
)
def test_window_without_usable_labels_names_the_phase(phase, split, end, reason):
    frames = _build(
        factor=_factor_frame(FRAME_DATES[6:]),
        phase=phase,
        split_date=split,
        end_date=end,
    )

    assert frames.status == "insufficient_data"
    assert frames.reasons == (reason,)
    assert frames.n_obs == 0
    assert frames.n_dates == 0
    assert frames.forward_returns.isna().all().all()


def test_frame_builder_reuses_contract_validation_errors():
    cases = (
        ({"phase": "walk_forward"}, "phase"),
        ({"horizon": 0}, "horizon"),
        ({"horizon": True}, "horizon"),
        ({"split_date": "2024-01-10"}, "split_date"),
    )
    for overrides, field in cases:
        with pytest.raises(FrozenBenchError) as excinfo:
            _build(**overrides)
        assert excinfo.value.field == field


def test_non_dataframe_input_is_rejected_not_coerced():
    with pytest.raises(FrozenBenchError) as excinfo:
        _build(factor=[["AAA"]])

    assert excinfo.value.field == "factor"
