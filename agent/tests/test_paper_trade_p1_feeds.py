"""T28 gate — ``FiveMinuteFeed`` adapters (P1 intraday, GAP-8).

The feed layer is the ONLY place a 5m day frame enters the pt-v1.3 engine, and
it must be boring: canonical columns, bar-END labels, a copy on every read, no
wall clock, no sleep, and no provider call on import.  This module pins the
contract that todo 25 (replay session) and todo 26 (live scheduler) consume.

Coverage:

* the protocol is structural and a 48-bar fake satisfies it;
* ``ReplayFeed`` from an injected mapping and from ``FiveMinuteFacts``;
* the closed-bar table (``label <= as_of``, bar ending AT ``as_of`` is closed);
* ``data_missing`` for an unknown symbol/day and a wrong-day frame;
* ``feed_unavailable`` for a malformed frame (missing column, non-datetime
  index), the Tushare skip without a token, and a provider fault;
* the Tushare request is one day with the provider's EXCLUSIVE end date;
* the Eastmoney adapter maps ``fetch_kline`` rows and forwards ``klt=5``;
* no token value reaches the exception, the feed's repr, or a log record.

Offline: committed CSVs and fake clients only.  The Tushare path is
monkeypatched at ``data_5m.fetch_5m_frame`` so a missing-token test can never
touch the network; todo 29 owns the optional real-provider smoke.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.tools.paper_trade import bar_clock, data_5m, feeds
from src.tools.paper_trade.data_5m import FiveMinuteFacts
from tests.paper_trade_5m_fixtures import continuous_bars, load_5m

_DAY = "2026-10-20"
_NEXT_DAY = "2026-10-21"
_SYM = "600519.SH"
_OTHER = "000001.SZ"
_FRAME_COLUMNS = ["open", "high", "low", "close", "volume"]


def _raw_day() -> pd.DataFrame:
    """The committed 49-row day frame: 09:30 auction print + 48 continuous bars."""
    return load_5m(_SYM, _DAY)


def _empty_day() -> pd.DataFrame:
    """A header-only day: the authoritative "zero bars" (halted) fixture shape."""
    return _raw_day().iloc[0:0].copy()


def _facts() -> FiveMinuteFacts:
    return FiveMinuteFacts(bars_by_symbol={_SYM: {_DAY: continuous_bars(_raw_day())}})


def _eastmoney_rows(day: str = _DAY) -> list[dict[str, Any]]:
    labels = ["09:30", *bar_clock.BAR_LABELS]
    return [
        {
            "trade_date": f"{day} {label}",
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 1000.0 + index,
        }
        for index, label in enumerate(labels)
    ]


class _FakeFeed:
    """A minimal structural feed: the 48-row contract without any adapter."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def day_bars(self, *, symbol: str, trade_date: str) -> pd.DataFrame:  # noqa: ARG002
        return self.frame.copy(deep=True)

    def closed_bars(self, *, symbol: str, trade_date: str, as_of: str) -> pd.DataFrame:
        frame = self.day_bars(symbol=symbol, trade_date=trade_date)
        return frame[frame.index.strftime("%H:%M") <= as_of].copy(deep=True)


class _FakeEastmoneyClient:
    """Injected ``eastmoney_client`` surface; records the exact call."""

    KLT_BY_INTERVAL = {"5m": 5}

    def __init__(self, rows: list[dict[str, Any]], secid: str | None = "1.600519") -> None:
        self.rows = rows
        self.secid = secid
        self.calls: list[dict[str, Any]] = []

    def resolve_secid(self, symbol: str) -> str | None:
        self.calls.append({"resolve_secid": symbol})
        return self.secid

    def fetch_kline(
        self,
        secid: str,
        *,
        klt: int,
        fqt: int = 1,
        beg: str = "0",
        end: str = "20500101",
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {"secid": secid, "klt": klt, "fqt": fqt, "beg": beg, "end": end}
        )
        return self.rows


class _ExplodingClient(_FakeEastmoneyClient):
    def fetch_kline(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("provider exploded with an internal payload")


# ── protocol and the canonical frame ──


def test_fake_feed_satisfies_the_protocol_and_returns_48_bars() -> None:
    fake = _FakeFeed(continuous_bars(_raw_day()))

    assert isinstance(fake, feeds.FiveMinuteFeed)
    frame = fake.day_bars(symbol=_SYM, trade_date=_DAY)

    assert len(frame) == 48
    assert list(frame.columns) == _FRAME_COLUMNS
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert frame.index.name == "bar_time"
    assert frame.index.strftime("%H:%M").tolist() == list(bar_clock.BAR_LABELS)


def test_replay_from_frames_returns_a_copy_and_repeats_exactly() -> None:
    source = _raw_day()
    feed = feeds.ReplayFeed(frames={_SYM: {_DAY: source}})

    first = feed.day_bars(symbol=_SYM, trade_date=_DAY)
    assert len(first) == 49
    assert list(first.columns) == _FRAME_COLUMNS
    assert all(dtype == "float64" for dtype in first.dtypes), first.dtypes
    assert first.index.strftime("%H:%M").tolist()[0] == "09:30"
    assert first.index.strftime("%H:%M").tolist()[-1] == "15:00"

    first.loc[first.index[0], "close"] = -1.0
    first.rename(columns={"close": "tampered"}, inplace=True)

    second = feed.day_bars(symbol=_SYM, trade_date=_DAY)
    pd.testing.assert_frame_equal(second, source.astype({c: "float64" for c in _FRAME_COLUMNS}))
    assert list(second.columns) == _FRAME_COLUMNS
    pd.testing.assert_frame_equal(feed.day_bars(symbol=_SYM, trade_date=_DAY), second)


def test_replay_from_facts_serves_the_48_continuous_bars_only() -> None:
    feed = feeds.ReplayFeed.from_facts(_facts())

    frame = feed.day_bars(symbol=_SYM, trade_date=_DAY)

    assert len(frame) == 48
    labels = frame.index.strftime("%H:%M").tolist()
    assert labels == list(bar_clock.BAR_LABELS)
    assert "09:30" not in labels


def test_replay_accepts_an_explicit_empty_day_as_zero_bars() -> None:
    feed = feeds.ReplayFeed(frames={_SYM: {_DAY: _empty_day()}})

    frame = feed.day_bars(symbol=_SYM, trade_date=_DAY)

    assert frame.empty
    assert list(frame.columns) == _FRAME_COLUMNS
    assert isinstance(frame.index, pd.DatetimeIndex)


# ── closed-bar semantics: a label is the bar END ──


@pytest.mark.parametrize(
    ("as_of", "expected_last", "expected_count"),
    [
        ("09:20", None, 0),
        ("09:30", "09:30", 1),
        ("09:35", "09:35", 2),
        ("10:00", "10:00", 7),
        ("11:30", "11:30", 25),
        ("12:00", "11:30", 25),
        ("13:04", "11:30", 25),
        ("13:05", "13:05", 26),
        ("15:00", "15:00", 49),
        ("15:05", "15:00", 49),
    ],
)
def test_closed_bars_includes_the_bar_ending_at_as_of(
    as_of: str, expected_last: str | None, expected_count: int
) -> None:
    feed = feeds.ReplayFeed(frames={_SYM: {_DAY: _raw_day()}})

    closed = feed.closed_bars(symbol=_SYM, trade_date=_DAY, as_of=as_of)
    labels = closed.index.strftime("%H:%M").tolist()

    assert (labels[-1] if labels else None) == expected_last
    assert len(labels) == expected_count
    assert all(label <= as_of for label in labels)
    if expected_last is not None and expected_last != "15:00":
        assert labels[-1] == expected_last


def test_an_in_progress_bar_is_never_returned_as_closed() -> None:
    feed = feeds.ReplayFeed(frames={_SYM: {_DAY: _raw_day()}})

    closed = feed.closed_bars(symbol=_SYM, trade_date=_DAY, as_of="09:40")

    labels = closed.index.strftime("%H:%M").tolist()
    assert "09:40" in labels
    assert "09:45" not in labels
    assert "10:00" not in labels


@pytest.mark.parametrize("as_of", ["9:35", "09:60", "0935", "09:35:00", "", "25:00", None])
def test_invalid_as_of_is_not_closed(as_of: object) -> None:
    feed = feeds.ReplayFeed(frames={_SYM: {_DAY: _raw_day()}})

    with pytest.raises(feeds.FeedError) as caught:
        feed.closed_bars(symbol=_SYM, trade_date=_DAY, as_of=as_of)  # type: ignore[arg-type]

    assert caught.value.code == "not_closed"
    assert isinstance(caught.value, RuntimeError)


# ── malformed / missing input ──


@pytest.mark.parametrize(
    ("symbol", "trade_date"),
    [(_OTHER, _DAY), (_SYM, _NEXT_DAY)],
)
def test_unknown_symbol_or_day_is_data_missing(symbol: str, trade_date: str) -> None:
    feed = feeds.ReplayFeed(frames={_SYM: {_DAY: _raw_day()}})

    with pytest.raises(feeds.FeedError) as caught:
        feed.day_bars(symbol=symbol, trade_date=trade_date)

    assert caught.value.code == "data_missing"


def test_a_frame_holding_only_another_day_is_data_missing() -> None:
    feed = feeds.ReplayFeed(frames={_SYM: {_DAY: load_5m(_SYM, "2026-10-16")}})

    with pytest.raises(feeds.FeedError) as caught:
        feed.day_bars(symbol=_SYM, trade_date=_DAY)

    assert caught.value.code == "data_missing"


def test_a_frame_missing_a_column_is_feed_unavailable() -> None:
    broken = _raw_day().drop(columns=["volume"])
    feed = feeds.ReplayFeed(frames={_SYM: {_DAY: broken}})

    with pytest.raises(feeds.FeedError) as caught:
        feed.day_bars(symbol=_SYM, trade_date=_DAY)

    assert caught.value.code == "feed_unavailable"
    assert "volume" in caught.value.message


def test_a_non_datetime_index_is_feed_unavailable() -> None:
    broken = _raw_day()
    broken.index = pd.Index(broken.index.strftime("%H:%M"))
    feed = feeds.ReplayFeed(frames={_SYM: {_DAY: broken}})

    with pytest.raises(feeds.FeedError) as caught:
        feed.day_bars(symbol=_SYM, trade_date=_DAY)

    assert caught.value.code == "feed_unavailable"


def test_a_non_finite_price_is_feed_unavailable() -> None:
    broken = _raw_day()
    broken.loc[broken.index[1], "close"] = float("nan")
    feed = feeds.ReplayFeed(frames={_SYM: {_DAY: broken}})

    with pytest.raises(feeds.FeedError) as caught:
        feed.day_bars(symbol=_SYM, trade_date=_DAY)

    assert caught.value.code == "feed_unavailable"


# ── Tushare adapter: skip without a token, sanitize provider faults ──


def test_tushare_feed_skips_without_a_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from src.config.accessor import reset_env_config

    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    reset_env_config()

    def _must_not_fetch(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the Tushare feed must not fetch without a token")

    monkeypatch.setattr(data_5m, "fetch_5m_frame", _must_not_fetch)

    with caplog.at_level(logging.WARNING, logger=feeds.__name__):
        with pytest.raises(feeds.FeedError) as caught:
            feeds.TushareFeed().day_bars(symbol=_SYM, trade_date=_DAY)

    assert caught.value.code == "feed_unavailable"
    assert "not configured" in caught.value.message
    assert any("not configured" in record.getMessage() for record in caplog.records)


def test_tushare_feed_requests_one_day_with_an_exclusive_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config.accessor import reset_env_config

    secret = "ts-secret-sentinel-9f3a"
    monkeypatch.setenv("TUSHARE_TOKEN", secret)
    reset_env_config()
    calls: list[tuple[str, str, str]] = []

    def _fetch(code: str, start: str, end: str) -> tuple[pd.DataFrame, object]:
        calls.append((code, start, end))
        return _raw_day(), object()

    monkeypatch.setattr(data_5m, "fetch_5m_frame", _fetch)
    feed = feeds.TushareFeed()

    frame = feed.day_bars(symbol=_SYM, trade_date=_DAY)

    assert calls == [(_SYM, _DAY, _NEXT_DAY)]
    assert len(frame) == 49
    assert frame.index.strftime("%H:%M").tolist()[-1] == "15:00"
    assert secret not in frame.to_json()
    assert secret not in repr(feed)


def test_tushare_feed_maps_an_empty_provider_day_to_data_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config.accessor import reset_env_config

    monkeypatch.setenv("TUSHARE_TOKEN", "configured-for-this-test")
    reset_env_config()
    monkeypatch.setattr(data_5m, "fetch_5m_frame", lambda *args: (None, object()))

    with pytest.raises(feeds.FeedError) as caught:
        feeds.TushareFeed().day_bars(symbol=_SYM, trade_date=_DAY)

    assert caught.value.code == "data_missing"


def test_no_token_reaches_the_error_or_a_log_record(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from src.config.accessor import reset_env_config

    secret = "ts-secret-sentinel-9f3a"
    monkeypatch.setenv("TUSHARE_TOKEN", secret)
    reset_env_config()

    def _leaky_fetch(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"provider rejected token {secret} for {_SYM}")

    monkeypatch.setattr(data_5m, "fetch_5m_frame", _leaky_fetch)
    feed = feeds.TushareFeed()

    with caplog.at_level(logging.WARNING, logger=feeds.__name__):
        with pytest.raises(feeds.FeedError) as caught:
            feed.day_bars(symbol=_SYM, trade_date=_DAY)

    error = caught.value
    payload = json.dumps(
        {
            "code": error.code,
            "message": error.message,
            "str": str(error),
            "repr": repr(error),
            "args": list(error.args),
            "feed": repr(feed),
        },
        default=str,
    )
    log_text = "\n".join(
        f"{record.name}:{record.levelname}:{record.getMessage()}"
        for record in caplog.records
    )

    assert error.code == "feed_unavailable"
    assert error.message == "Tushare 5m fetch failed: RuntimeError"
    assert error.__cause__ is None
    assert secret not in payload
    assert secret not in log_text
    assert caplog.records, "the provider fault must leave an audit record"


# ── Eastmoney adapter: map interval-5m kline rows, no network ──


def test_eastmoney_maps_interval_5m_rows_to_the_canonical_frame() -> None:
    client = _FakeEastmoneyClient(_eastmoney_rows())
    feed = feeds.EastmoneyFeed(client=client)

    frame = feed.day_bars(symbol=_SYM, trade_date=_DAY)

    assert list(frame.columns) == _FRAME_COLUMNS
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert frame.index.name == "bar_time"
    assert len(frame) == 49
    assert frame.index.strftime("%H:%M").tolist() == ["09:30", *bar_clock.BAR_LABELS]
    assert frame["close"].iloc[1] == pytest.approx(101.5)
    assert any(
        call.get("secid") == "1.600519"
        and call.get("klt") == 5
        and call.get("beg") == "20261020"
        and call.get("end") == "20261020"
        for call in client.calls
    )


def test_eastmoney_closed_bars_drop_the_in_progress_row() -> None:
    feed = feeds.EastmoneyFeed(client=_FakeEastmoneyClient(_eastmoney_rows()))

    closed = feed.closed_bars(symbol=_SYM, trade_date=_DAY, as_of="10:00")

    labels = closed.index.strftime("%H:%M").tolist()
    assert labels == ["09:30", *bar_clock.BAR_LABELS[:6]]
    assert "10:05" not in labels


def test_eastmoney_empty_rows_are_data_missing() -> None:
    feed = feeds.EastmoneyFeed(client=_FakeEastmoneyClient([]))

    with pytest.raises(feeds.FeedError) as caught:
        feed.day_bars(symbol=_SYM, trade_date=_DAY)

    assert caught.value.code == "data_missing"


def test_eastmoney_client_without_a_5m_interval_is_feed_unavailable() -> None:
    client = _FakeEastmoneyClient(_eastmoney_rows())
    client.KLT_BY_INTERVAL = {}
    feed = feeds.EastmoneyFeed(client=client)

    with pytest.raises(feeds.FeedError) as caught:
        feed.day_bars(symbol=_SYM, trade_date=_DAY)

    assert caught.value.code == "feed_unavailable"
    assert "5m" in caught.value.message
    assert not any("klt" in call for call in client.calls), "no fetch without an interval code"


def test_eastmoney_unresolvable_symbol_is_data_missing() -> None:
    client = _FakeEastmoneyClient(_eastmoney_rows(), secid=None)
    feed = feeds.EastmoneyFeed(client=client)

    with pytest.raises(feeds.FeedError) as caught:
        feed.day_bars(symbol="NOPE.XX", trade_date=_DAY)

    assert caught.value.code == "data_missing"
    assert len(client.calls) == 1, "an unresolvable symbol must not reach fetch_kline"


def test_eastmoney_provider_fault_is_feed_unavailable_and_sanitized() -> None:
    feed = feeds.EastmoneyFeed(client=_ExplodingClient([]))

    with pytest.raises(feeds.FeedError) as caught:
        feed.day_bars(symbol=_SYM, trade_date=_DAY)

    assert caught.value.code == "feed_unavailable"
    assert caught.value.message == "Eastmoney 5m fetch failed: RuntimeError"
    assert "internal payload" not in caught.value.message


def test_eastmoney_frame_from_rows_rejects_a_row_without_a_timestamp() -> None:
    rows = _eastmoney_rows()
    rows[3] = {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}
    feed = feeds.EastmoneyFeed(client=_FakeEastmoneyClient(rows))

    with pytest.raises(feeds.FeedError) as caught:
        feed.day_bars(symbol=_SYM, trade_date=_DAY)

    assert caught.value.code == "feed_unavailable"


# ── source-level guards (no clock, no env read) ──


def test_feed_module_reads_no_clock_and_no_environment() -> None:
    source = Path(feeds.__file__).read_text(encoding="utf-8")

    for forbidden in ("datetime.now", "datetime.today", "time.sleep", "os.environ", "os.getenv"):
        assert forbidden not in source, f"feeds.py must not use {forbidden}"
