"""Federation-aware provenance through the public backtest fetch seam."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest import runner


def _frame(federation: dict[str, object]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "open": [10.0, 10.5, 11.0],
            "high": [10.5, 11.0, 11.5],
            "low": [9.5, 10.0, 10.5],
            "close": [10.2, 10.7, 11.2],
            "volume": [100.0, 110.0, 120.0],
        },
        index=pd.bdate_range("2026-11-12", periods=3),
    )
    frame.attrs["_provenance"] = {"federation": federation}
    return frame


def _history() -> dict[str, object]:
    return {
        "source": "clickhouse",
        "table": "stk_factor_pro",
        "start_date": "2026-11-12",
        "end_date": "2026-11-14",
        "adjustment": "raw",
        "volume_unit": "lot",
        "amount_unit": "thousand CNY",
    }


def _realtime() -> dict[str, object]:
    return {
        "source": "tencent",
        "start_date": "2026-11-15",
        "end_date": "2026-11-15",
        "adjustment": "split_dividend_additive",
        "volume_unit": "lots",
        "amount_unit": None,
    }


def _config() -> dict[str, object]:
    return {
        "source": "clickhouse",
        "codes": ["000001.SZ"],
        "start_date": "2026-11-01",
        "end_date": "2026-11-15",
        "interval": "1D",
    }


def _install_loader(
    monkeypatch: pytest.MonkeyPatch,
    federation: dict[str, object],
    *,
    reject_clickhouse: bool = False,
    fallback_reason: str | None = None,
) -> None:
    class FixtureLoader:
        name = "clickhouse"
        volume_units = {"a_share": "lot"}
        amount_units = {"a_share": "thousand CNY"}

        def fetch(
            self,
            codes: list[str],
            start_date: str,
            end_date: str,
            **kwargs: object,
        ) -> dict[str, pd.DataFrame]:
            frames = {code: _frame(federation) for code in codes}
            if fallback_reason is not None:
                for frame in frames.values():
                    frame.attrs["_provenance"]["fallback_reason"] = fallback_reason
            return frames

    def resolve(
        source: str, *, diagnostics: list[dict[str, str]] | None = None
    ) -> type[FixtureLoader]:
        if reject_clickhouse and diagnostics is not None:
            diagnostics.append({"source": "clickhouse", "reason": "source_unavailable"})
        return FixtureLoader

    monkeypatch.setattr(runner, "get_loader_cls_with_fallback", resolve)


def test_runner_marks_federated_source_and_mixed_caliber(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    federation = {
        "mode": "federated",
        "history": _history(),
        "realtime": _realtime(),
    }
    _install_loader(monkeypatch, federation)

    result = runner.fetch_data_map(_config())

    assert result.effective_sources == ["federated"]
    entry = result.data_provenance["000001.SZ"]
    assert entry["source"] == "federated"
    assert entry["adjustment"] == "mixed"
    assert entry["federation"]["mode"] == "federated"
    assert entry["fallback_attempts"] == []
    assert result.caliber_warning == (
        "history adjustment=raw; realtime adjustment=split_dividend_additive"
    )


def test_runner_keeps_clickhouse_only_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_loader(
        monkeypatch,
        {"mode": "clickhouse_only", "history": _history()},
    )

    result = runner.fetch_data_map(_config())

    entry = result.data_provenance["000001.SZ"]
    assert entry["source"] == "clickhouse"
    assert entry["adjustment"] == "raw"
    assert entry["federation"]["history"]["table"] == "stk_factor_pro"
    assert result.caliber_warning is None


def test_runner_realtime_only_records_network_source_and_ch_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_loader(
        monkeypatch,
        {"mode": "realtime_only", "realtime": _realtime()},
    )

    result = runner.fetch_data_map(_config())

    assert result.effective_sources == ["tencent"]
    entry = result.data_provenance["000001.SZ"]
    assert entry["source"] == "tencent"
    assert entry["adjustment"] == "split_dividend_additive"
    assert entry["fallback_attempts"] == [{"source": "clickhouse", "reason": "no_data"}]
    assert result.caliber_warning is not None
    assert "additive" in result.caliber_warning


def test_runner_realtime_only_does_not_duplicate_ch_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_loader(
        monkeypatch,
        {"mode": "realtime_only", "realtime": _realtime()},
        reject_clickhouse=True,
    )

    result = runner.fetch_data_map(_config())

    assert result.data_provenance["000001.SZ"]["fallback_attempts"] == [
        {"source": "clickhouse", "reason": "source_unavailable"}
    ]


def test_runner_realtime_only_preserves_ch_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_loader(
        monkeypatch,
        {"mode": "realtime_only", "realtime": _realtime()},
        fallback_reason="clickhouse:fetch_failed",
    )

    result = runner.fetch_data_map(_config())

    assert result.data_provenance["000001.SZ"]["fallback_attempts"] == [
        {"source": "clickhouse", "reason": "fetch_failed"}
    ]
