"""T29 gate — the optional live Tushare ``stk_mins`` smoke (P1 intraday, GAP-8).

The feed layer's offline contract is pinned by ``test_paper_trade_p1_feeds.py``
over committed fixtures.  This module is the one test that may talk to the real
provider, and only when a credential is configured: its job is to catch the
class of defect a fixture cannot — a provider that changed its bar labels, its
day boundary, or the number of rows it serves.

Policy
------
* **Absent token => SKIP, never fail.**  Availability is asked of the project's
  own config layer (``feeds._tushare_token_configured``) after loading
  ``agent/.env`` the same way ``data_5m.fetch_5m_frame`` does, so a token that
  lives only in ``.env`` is still found and ``os.environ`` is never read
  directly by the gate.  ``TUSHARE_TOKEN= pytest ...`` (an empty exported value,
  which ``load_dotenv(override=False)`` keeps instead of the file's) exercises
  the absent branch.
* **ONE provider call per run.**  ``stk_mins`` is measured at 2 calls/day, so
  the smoke calls ``TushareFeed.day_bars`` exactly once and replays that frame
  through ``ReplayFeed`` for the closed-bar rule (the rule itself is shared:
  ``_DayFrameFeed.closed_bars``).  Nothing is retried.
* **A quota/empty/unavailable day is a documented SKIP**, not a failure.  The
  rate-limited loader swallows a provider fault into an empty frame, which the
  feed already reports as ``data_missing``; the evidence records the reason.
  Only a MALFORMED frame (wrong columns/index, duplicate labels, off-grid
  labels, a foreign day, non-finite values) fails.
* **No credential leaves the test.**  The token is read inside the test only and
  compared through a boolean, so neither a failing assertion nor the evidence
  report can print it; the report records ``token_configured`` only.

Fixed day: ``2026-10-20`` is the P0-confirmed regular cn_a session (see
``.omo/evidence/p0-5m``) that also backs the committed synthetic fixtures, so a
short count is a provider observation rather than a calendar guess.  Like the
package it exercises, this module derives no date from a wall clock.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from src.config.accessor import get_env_config, reset_env_config
from src.tools.paper_trade import bar_clock, feeds

#: One symbol and one day: the measured ``stk_mins`` budget is 2 calls/day.
_SYMBOL = "600519.SH"
_TRADE_DATE = "2026-10-20"
_AS_OF = "15:00"

_EXPECTED_CONTINUOUS = len(bar_clock.BAR_LABELS)  # 48
_BAR_END_GRID = frozenset({bar_clock.OPENING_AUCTION_PRINT_LABEL, *bar_clock.BAR_LABELS})

_AGENT_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _AGENT_DIR / ".env"
_EVIDENCE_PATH = _REPO_ROOT / ".omo" / "evidence" / "p1-intraday" / "task-29-paper-trade-p1-intraday.md"

#: The adapter's own provider-side messages (``feeds.TushareFeed.day_bars``).
#: Anything else with ``feed_unavailable`` is a malformed frame and must fail.
_PROVIDER_SIDE_MARKERS = ("token is not configured", "Tushare 5m fetch failed:")


def _load_project_env() -> None:
    """Load ``agent/.env`` exactly as the production 5m loader does.

    ``data_5m.fetch_5m_frame`` calls ``load_dotenv`` because the config layer
    reads ``os.environ``; the availability gate repeats that load (rather than
    reading the environment directly) and then resets the cached config so the
    freshly loaded value is visible.  ``override=False`` keeps an explicitly
    exported value, including an empty one — the way the "no token" branch is
    exercised without touching the shared ``.env``.
    """
    from dotenv import load_dotenv

    load_dotenv(_ENV_FILE, override=False)
    reset_env_config()


def _token_available() -> bool:
    """Whether the config layer sees a usable token; a broken dev dep only skips."""
    try:
        _load_project_env()
        return feeds._tushare_token_configured()
    except Exception:  # noqa: BLE001 - optional dev dependency; absence must skip
        return False


TUSHARE_AVAILABLE = _token_available()

_SKIP_REASON = "TUSHARE_TOKEN is not configured; the optional 5m smoke is skipped"
_LIVE_ONLY = pytest.mark.skipif(not TUSHARE_AVAILABLE, reason=_SKIP_REASON)


def _configured_token() -> str:
    """The configured credential, read from the config layer and never logged."""
    from backtest.loaders.tushare import TUSHARE_TOKEN_PLACEHOLDERS

    token = get_env_config().data.tushare_token.strip()
    return "" if token in TUSHARE_TOKEN_PLACEHOLDERS else token


def _assert_no_secret(text: str, token: str, where: str) -> None:
    """Fail without echoing the credential: only the boolean is ever rendered."""
    if not token:
        return
    leaked = token in text
    assert leaked is False, f"the configured Tushare token appears in {where}"


def _labels(frame: pd.DataFrame) -> list[str]:
    return [timestamp.strftime("%H:%M") for timestamp in frame.index]


def _continuous_labels(frame: pd.DataFrame) -> list[str]:
    """The day's continuous bar-END labels; the opening auction print is not one."""
    return [
        label for label in _labels(frame) if bar_clock.classify(label) == bar_clock.SESSION_CONTINUOUS
    ]


def _assert_canonical(frame: pd.DataFrame) -> None:
    """Reject a malformed frame; a provider gap never reaches here."""
    assert isinstance(frame.index, pd.DatetimeIndex), "the live frame must be datetime-indexed"
    assert frame.index.name == "bar_time", f"the live index is named {frame.index.name!r}"
    assert list(frame.columns) == list(feeds.CANONICAL_COLUMNS), f"columns are {list(frame.columns)}"
    assert len(frame) > 0, "an empty live frame is a documented skip, not a canonical day"
    assert frame.index.is_unique, "duplicate bar labels are malformed"
    assert frame.index.is_monotonic_increasing, "bar labels must be ascending"
    on_day = bool((frame.index.strftime("%Y-%m-%d") == _TRADE_DATE).all())
    assert on_day, f"the live frame holds bars outside {_TRADE_DATE} (stale or wrong-day frame)"
    finite = bool(frame.notna().all().all())
    assert finite, "the live frame has a non-numeric or non-finite OHLCV value"
    assert all(str(dtype) == "float64" for dtype in frame.dtypes), f"dtypes are {list(frame.dtypes)}"
    off_grid = sorted({label for label in _labels(frame) if label not in _BAR_END_GRID})
    assert not off_grid, f"bar labels outside the bar-END grid: {off_grid}"


def _assert_full_session(report: Mapping[str, object]) -> None:
    """A complete cn_a session: 48 continuous bar-END labels ending at 15:00.

    This is what distinguishes a bar-END feed from a bar-START one: the two
    five-minute grids overlap almost everywhere, but a start-labelled day ends
    at ``14:55`` and turns the 09:30 auction print into a continuous bar.
    """
    assert report["last_label"] == bar_clock.BAR_LABELS[-1], "the last label must be the 15:00 bar-END label"
    assert report["continuous_labels"] == list(bar_clock.BAR_LABELS), "labels are not the session grid"
    assert report["continuous_count"] == _EXPECTED_CONTINUOUS, (
        f"{report['continuous_count']} continuous labels, expected {_EXPECTED_CONTINUOUS}"
    )


def _report(frame: pd.DataFrame, closed: pd.DataFrame | None) -> dict[str, object]:
    labels = _labels(frame)
    continuous = _continuous_labels(frame)
    auction = [
        label for label in labels if bar_clock.classify(label) == bar_clock.SESSION_OPENING_AUCTION_PRINT
    ]
    closed_labels = _labels(closed) if closed is not None else []
    return {
        "rows": int(len(frame)),
        "columns": [str(column) for column in frame.columns],
        "index_name": frame.index.name,
        "first_label": labels[0],
        "last_label": labels[-1],
        "auction_rows": auction,
        "continuous_count": len(continuous),
        "continuous_labels": continuous,
        "closed_rows": int(len(closed)) if closed is not None else 0,
        "closed_last_label": closed_labels[-1] if closed_labels else "",
    }


def _evidence_text(
    *,
    status: str,
    detail: str,
    report: Mapping[str, object],
    logs: Sequence[str],
) -> str:
    """Render the evidence report; it contains no credential by construction."""
    lines = [
        "# Task 29 evidence — optional Tushare 5m smoke (P1 intraday, GAP-8)",
        "",
        f"- Worktree: `{_REPO_ROOT}`",
        f"- Symbol / day / as_of: `{_SYMBOL}` / `{_TRADE_DATE}` / `{_AS_OF}`",
        "- Provider calls this run: **1** (`stk_mins`, measured at 2/day, never retried)",
        f"- Status: **{status}**",
        "",
        "## Outcome",
        "",
        detail or "(see the frame report)",
        "",
        "## Frame report",
        "",
        "```json",
        json.dumps(report, indent=2, default=str),
        "```",
        "",
        "## Captured log records",
        "",
    ]
    lines.extend(["```", *logs, "```"] if logs else ["(none)"])
    lines.extend(
        [
            "",
            "## Redaction",
            "",
            "The configured token is never rendered here: it is read inside the test",
            "and compared through a boolean, so neither this file nor a failing",
            "assertion can print it. The test asserts `token_substring_in_report",
            "is False` and the run proves it by scanning this file.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_evidence(
    *,
    status: str,
    detail: str,
    report: Mapping[str, object],
    logs: Sequence[str],
) -> Path:
    text = _evidence_text(status=status, detail=detail, report=report, logs=logs)
    _EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _EVIDENCE_PATH.write_text(text, encoding="utf-8")
    return _EVIDENCE_PATH


class _RecordHandler(logging.Handler):
    """Collect every record emitted during the one live call for the leak scan."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.name}:{record.levelname}:{record.getMessage()}")


@dataclass(frozen=True)
class _SmokeOutcome:
    frame: pd.DataFrame | None
    closed: pd.DataFrame | None
    report: dict[str, object]
    logs: tuple[str, ...]
    error_code: str
    error_text: str
    status: str
    detail: str


def _provider_side(error: feeds.FeedError) -> bool:
    """True for a provider gap (skip), False for a malformed frame (fail)."""
    if error.code == feeds.DATA_MISSING:
        return True
    return error.code == feeds.FEED_UNAVAILABLE and any(
        marker in error.message for marker in _PROVIDER_SIDE_MARKERS
    )


def _run_smoke() -> _SmokeOutcome:
    """Issue the ONE live call, then run every offline assertion this smoke owns."""
    handler = _RecordHandler()
    root = logging.getLogger()
    root.addHandler(handler)
    frame: pd.DataFrame | None = None
    error: feeds.FeedError | None = None
    try:
        frame = feeds.TushareFeed().day_bars(symbol=_SYMBOL, trade_date=_TRADE_DATE)
    except feeds.FeedError as exc:
        error = exc
    finally:
        root.removeHandler(handler)
    logs = tuple(handler.records)

    if error is not None:
        provider_side = _provider_side(error)
        if provider_side:
            detail = (
                f"`{error.code}`: {error.message}\n\n"
                "A provider-side gap is a documented SKIP: `stk_mins` is measured at\n"
                "2 calls/day, the rate-limited loader turns a provider fault into an\n"
                "empty frame, and no retry was made."
            )
            _write_evidence(status="skipped (provider)", detail=detail, report={}, logs=logs)
            return _SmokeOutcome(
                frame=None,
                closed=None,
                report={},
                logs=logs,
                error_code=error.code,
                error_text=str(error),
                status="provider_gap",
                detail=f"documented provider gap, no retry: {error}",
            )
        detail = (
            f"`{error.code}`: {error.message}\n\n"
            "This is a MALFORMED-frame failure: the adapter validated a returned\n"
            "frame and rejected it, so it is not a provider gap."
        )
        _write_evidence(status="FAILED (malformed)", detail=detail, report={}, logs=logs)
        pytest.fail(f"the live frame is malformed: {error}")

    assert frame is not None, "the adapter must raise FeedError instead of returning None"
    try:
        _assert_canonical(frame)
    except AssertionError as exc:
        _write_evidence(status="FAILED (malformed)", detail=str(exc), report={}, logs=logs)
        raise

    # Replay the ALREADY-FETCHED frame through the shared closure rule: a second
    # TushareFeed.closed_bars() would spend a second live call on the 2/day budget.
    closed = feeds.ReplayFeed(frames={_SYMBOL: {_TRADE_DATE: frame}}).closed_bars(
        symbol=_SYMBOL, trade_date=_TRADE_DATE, as_of=_AS_OF
    )
    report = _report(frame, closed)
    if report["continuous_count"] > _EXPECTED_CONTINUOUS:
        detail = (
            f"{report['continuous_count']} continuous bar-END labels, more than the "
            f"{_EXPECTED_CONTINUOUS} a cn_a session holds: the auction print leaked "
            "into the continuous set or the provider labels bars by START."
        )
        _write_evidence(status="FAILED (contract violation)", detail=detail, report=report, logs=logs)
        pytest.fail(detail)
    if report["continuous_count"] < _EXPECTED_CONTINUOUS:
        detail = (
            f"{report['continuous_count']} continuous bar-END labels, expected "
            f"{_EXPECTED_CONTINUOUS}. A short but well-formed day is documented "
            "(halted/illiquid session), not a failure; the labels are recorded below."
        )
        _write_evidence(status="skipped (documented short day)", detail=detail, report=report, logs=logs)
        return _SmokeOutcome(
            frame=frame,
            closed=closed,
            report=report,
            logs=logs,
            error_code="",
            error_text="",
            status="short_day",
            detail=f"documented short day: {detail}",
        )

    _write_evidence(
        status="passed",
        detail=(
            "One live `stk_mins` day frame mapped onto the canonical shape; "
            f"{report['continuous_count']} continuous bar-END labels after timeline "
            "classification (the 09:30 opening-auction print is excluded)."
        ),
        report=report,
        logs=logs,
    )
    return _SmokeOutcome(
        frame=frame,
        closed=closed,
        report=report,
        logs=logs,
        error_code="",
        error_text="",
        status="passed",
        detail="",
    )


@pytest.fixture(scope="module")
def smoke_outcome() -> _SmokeOutcome:
    """The single live call, shared by both live tests (quota: one per run)."""
    if not TUSHARE_AVAILABLE:
        pytest.skip(_SKIP_REASON)
    return _run_smoke()


# ── Offline guards: the bar-END grid, the auction split, the redaction check ──


def _synthetic_day(labels: Sequence[str], day: str = _TRADE_DATE) -> pd.DataFrame:
    index = pd.DatetimeIndex([f"{day} {label}" for label in labels], name="bar_time")
    values = {
        column: [100.0 + position for position in range(len(labels))]
        for column in feeds.CANONICAL_COLUMNS
    }
    return pd.DataFrame(values, index=index)


def test_continuous_labels_exclude_the_opening_auction_print() -> None:
    frame = _synthetic_day([bar_clock.OPENING_AUCTION_PRINT_LABEL, *bar_clock.BAR_LABELS])

    labels = _continuous_labels(frame)

    assert len(labels) == _EXPECTED_CONTINUOUS
    assert bar_clock.OPENING_AUCTION_PRINT_LABEL not in labels
    assert labels[0] == "09:35"
    assert labels[-1] == "15:00"


def test_bar_start_labels_are_rejected() -> None:
    """A bar-START day (09:30 .. 14:55) is caught by the session-shape check."""
    starts = [
        (pd.Timestamp(f"{_TRADE_DATE} {label}") - pd.Timedelta(minutes=5)).strftime("%H:%M")
        for label in bar_clock.BAR_LABELS
    ]
    frame = _synthetic_day(starts)
    report = _report(frame, None)

    assert report["continuous_count"] == _EXPECTED_CONTINUOUS - 2, (
        "a start-labelled day loses two continuous labels: 09:30 is the auction "
        "print and 13:00 is off the afternoon grid"
    )
    assert report["last_label"] == "14:55"
    with pytest.raises(AssertionError, match="bar-END"):
        _assert_full_session(report)


def test_off_grid_labels_are_rejected() -> None:
    frame = _synthetic_day([bar_clock.OPENING_AUCTION_PRINT_LABEL, *bar_clock.BAR_LABELS[:-1], "15:01"])

    with pytest.raises(AssertionError, match="bar-END grid"):
        _assert_canonical(frame)


def test_the_redaction_guard_fails_on_a_leak() -> None:
    secret = "ts-smoke-placeholder-value"

    with pytest.raises(AssertionError) as caught:
        _assert_no_secret(f"prefix {secret} suffix", secret, "the probe payload")

    assert secret not in str(caught.value), "the guard must not print what it guards"


# ── The live gates (skipped without a token) ──


@_LIVE_ONLY
def test_tushare_5m_smoke(smoke_outcome: _SmokeOutcome) -> None:
    if smoke_outcome.status != "passed":
        pytest.skip(smoke_outcome.detail)

    frame = smoke_outcome.frame
    closed = smoke_outcome.closed
    report = smoke_outcome.report
    assert frame is not None and closed is not None

    assert list(frame.columns) == list(feeds.CANONICAL_COLUMNS)
    assert frame.index.name == "bar_time"
    assert frame.index.is_unique, "no duplicate bar labels"
    assert (frame.index.strftime("%Y-%m-%d") == _TRADE_DATE).all(), "the frame is the requested day"
    _assert_full_session(report)
    assert report["closed_last_label"] == _AS_OF
    # ``as_of`` is the last label, so every returned bar is closed and kept.
    assert len(closed) == len(frame)
    assert _EVIDENCE_PATH.is_file(), f"the evidence report is missing: {_EVIDENCE_PATH}"


@_LIVE_ONLY
def test_token_never_appears_in_the_outcome(smoke_outcome: _SmokeOutcome) -> None:
    token = _configured_token()
    if not token:
        pytest.skip("TUSHARE_TOKEN is not configured; there is no credential to redact")

    frame = smoke_outcome.frame
    payload = json.dumps(
        {
            "frame_records": frame.to_dict(orient="records") if frame is not None else [],
            "frame_repr": repr(frame),
            "error_code": smoke_outcome.error_code,
            "error_text": smoke_outcome.error_text,
            "report": smoke_outcome.report,
            "logs": list(smoke_outcome.logs),
        },
        default=str,
    )

    _assert_no_secret(payload, token, "the smoke outcome")
    _assert_no_secret(_EVIDENCE_PATH.read_text(encoding="utf-8"), token, "the evidence report")
