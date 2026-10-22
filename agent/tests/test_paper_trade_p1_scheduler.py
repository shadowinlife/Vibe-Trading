"""Todo 26 gate — the live scheduler seam (pt-v1.3, GAP-4).

* ``closed_label`` maps an aware ``+08:00`` instant onto the 48-label lattice;
* one tick applies exactly the next unapplied bar and returns its document;
* a second identical tick is ``already_applied`` and changes ZERO bytes;
* a three-tick sequence advances 09:35 -> 09:40 -> 09:45, one bar per call;
* a missed beat is ``nothing_due``: the scheduler never gap-fills or skips;
* an off-grid / naive / foreign-offset / wrong-day ``as_of`` is ``not_a_bar_boundary``;
* a full aware timestamp names the same bar as its ``HH:MM`` label;
* a closed session (watermark == trade_date) is ``session_closed``;
* a new ledger day starts at 09:35 despite the prior session's ``watermark_bar``;
* a dry run computes the document and writes nothing;
* the module reads no clock, imports no time/concurrency module, and pins its imports.

Offline, deterministic: a tmp runtime root and the committed 5m fixtures only.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import tokenize
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from src.tools.paper_trade.account import create_account
from src.tools.paper_trade.feeds import ReplayFeed
from src.tools.paper_trade.scheduler import (
    SCHEDULER_ERROR_CODES,
    STATUS_NOTHING_DUE,
    STATUS_SESSION_CLOSED,
    SchedulerError,
    SchedulerTick,
    SessionScheduler,
    closed_label,
)
from src.tools.paper_trade.schema_v13 import SCHEMA_VERSION_V1_3
from src.tools.paper_trade.store import AccountStore
from tests.paper_trade_5m_fixtures import continuous_bars, load_5m

_ACCOUNT = "pa_p1_scheduler"
_DAY = "2026-10-20"
_PRIOR_DAY = "2026-10-16"
_SYM = "600519.SH"
_FIRST = "09:35"
_SECOND = "09:40"
_THIRD = "09:45"
_LAST = "15:00"


def _store(tmp_path: Path) -> AccountStore:
    return AccountStore(tmp_path / "paper_trade")


def _account(**overrides: Any):
    kwargs = {
        "account_id": _ACCOUNT,
        "market": "cn_a",
        "start_date": _DAY,
        "initial_cash": 200_000.0,
        "today": _DAY,
        "next_trading_day": "2026-10-21",
        "now": f"{_DAY}T08:00:00+08:00",
    }
    kwargs.update(overrides)
    return create_account(**kwargs)


def _feed() -> ReplayFeed:
    return ReplayFeed(
        frames={_SYM: {_DAY: continuous_bars(load_5m(_SYM, _DAY))}}
    )


def _scheduler(store: AccountStore, **overrides: Any) -> SessionScheduler:
    kwargs: dict[str, Any] = {
        "store": store,
        "account_id": _ACCOUNT,
        "trade_date": _DAY,
        "feed": _feed(),
    }
    kwargs.update(overrides)
    return SessionScheduler(**kwargs)


def _tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` with the sha256 of its bytes."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _bar_dirs(store: AccountStore) -> list[str]:
    root = store.steps_dir(_ACCOUNT) / _DAY / "bars"
    return [] if not root.is_dir() else sorted(path.name for path in root.iterdir())


def _iso(label: str) -> str:
    return f"{_DAY}T{label}:00+08:00"


# ── 1. the pure lattice lookup ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        ("2026-10-20T08:00:00+08:00", None),
        ("2026-10-20T09:30:00+08:00", None),
        ("2026-10-20T09:34:00+08:00", None),
        ("2026-10-20T09:35:00+08:00", "09:35"),
        ("2026-10-20T09:37:00+08:00", "09:35"),
        ("2026-10-20T09:40:00+08:00", "09:40"),
        ("2026-10-20T11:30:00+08:00", "11:30"),
        ("2026-10-20T11:32:00+08:00", "11:30"),
        ("2026-10-20T13:04:00+08:00", "11:30"),
        ("2026-10-20T13:05:00+08:00", "13:05"),
        ("2026-10-20T14:56:00+08:00", "14:55"),
        ("2026-10-20T14:59:00+08:00", "14:55"),
        ("2026-10-20T15:00:00+08:00", "15:00"),
        ("2026-10-20T18:00:00+08:00", "15:00"),
    ],
)
def test_closed_label_maps_an_instant_onto_the_last_closed_bar(
    as_of: str, expected: str | None
) -> None:
    assert closed_label(as_of) == expected


def test_closed_label_is_deterministic_and_reads_nothing() -> None:
    as_of = "2026-10-20T09:37:00+08:00"

    assert closed_label(as_of) == closed_label(as_of) == _FIRST


@pytest.mark.parametrize(
    "as_of",
    [
        "2026-10-20T09:35:00",
        "2026-10-20T01:35:00+00:00",
        "2026-10-20T09:35:30+08:00",
        "2026-10-20",
        "",
        "09:35",
    ],
)
def test_closed_label_refuses_an_instant_that_cannot_name_a_closed_bar(
    as_of: str,
) -> None:
    with pytest.raises(SchedulerError) as excinfo:
        closed_label(as_of)

    assert excinfo.value.code == "not_a_bar_boundary"


# ── 2. one tick applies exactly the next unapplied bar ───────────────────


def test_tick_applies_the_first_closed_bar_and_returns_its_document(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.create(_account())

    tick = _scheduler(store).tick(_FIRST)

    assert tick.status == "applied"
    assert tick.bar_label == _FIRST
    assert tick.run is not None
    assert tick.document == tick.run.document
    payload = json.loads(tick.document)
    assert payload["schema_version"] == SCHEMA_VERSION_V1_3
    assert payload["bar_time"] == _iso(_FIRST)
    assert payload["received_state_hash"]
    assert _bar_dirs(store) == ["0935"]
    assert store.load(_ACCOUNT).watermark_bar == _FIRST


def test_a_second_identical_tick_is_already_applied_and_changes_nothing(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.create(_account())
    scheduler = _scheduler(store)
    scheduler.tick(_FIRST)
    before = _tree(store.account_dir(_ACCOUNT))

    duplicate = scheduler.tick(_FIRST)

    assert duplicate.status == "already_applied"
    assert duplicate.bar_label == _FIRST
    assert duplicate.run is None
    assert duplicate.document == ""
    assert _tree(store.account_dir(_ACCOUNT)) == before
    assert _bar_dirs(store) == ["0935"]


def test_three_ticks_advance_one_bar_each_in_session_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    scheduler = _scheduler(store)

    ticks = [scheduler.tick(label) for label in (_FIRST, _SECOND, _THIRD)]

    assert [tick.status for tick in ticks] == ["applied", "applied", "applied"]
    assert [tick.bar_label for tick in ticks] == [_FIRST, _SECOND, _THIRD]
    assert [json.loads(tick.document)["bar_time"] for tick in ticks] == [
        _iso(_FIRST),
        _iso(_SECOND),
        _iso(_THIRD),
    ]
    assert _bar_dirs(store) == ["0935", "0940", "0945"]
    assert store.load(_ACCOUNT).watermark_bar == _THIRD
    assert store.load(_ACCOUNT).watermark is None


def test_a_missed_beat_is_nothing_due_and_is_never_gap_filled(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.create(_account())
    scheduler = _scheduler(store)

    missed = scheduler.tick(_SECOND)

    assert missed.status == STATUS_NOTHING_DUE
    assert missed.bar_label is None
    assert missed.run is None
    assert missed.document == ""
    assert _bar_dirs(store) == []
    assert store.load(_ACCOUNT).watermark_bar == ""

    assert scheduler.tick(_FIRST).status == "applied"
    late = scheduler.tick(_THIRD)

    assert late.status == STATUS_NOTHING_DUE
    assert _bar_dirs(store) == ["0935"]
    assert scheduler.tick(_SECOND).status == "applied"
    assert _bar_dirs(store) == ["0935", "0940"]


def test_tick_never_applies_more_than_one_bar(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    scheduler = _scheduler(store)

    scheduler.tick(_FIRST)
    assert scheduler.tick(_THIRD).status == STATUS_NOTHING_DUE

    assert _bar_dirs(store) == ["0935"]
    assert store.load(_ACCOUNT).watermark_bar == _FIRST


# ── 3. the input boundary ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "as_of",
    [
        "09:37",
        "09:30",
        "11:35",
        "14:57",
        "15:05",
        "9:35",
        "2026-10-20T09:35:00",
        "2026-10-20T01:35:00+00:00",
        "2026-10-20T09:35:30+08:00",
        "2026-10-20",
        "",
        "2026-10-21T09:35:00+08:00",
    ],
)
def test_a_non_grid_or_malformed_tick_is_refused_and_writes_nothing(
    tmp_path: Path, as_of: str
) -> None:
    store = _store(tmp_path)
    store.create(_account())
    before = _tree(store.account_dir(_ACCOUNT))

    with pytest.raises(SchedulerError) as excinfo:
        _scheduler(store).tick(as_of)

    assert excinfo.value.code == "not_a_bar_boundary"
    assert _tree(store.account_dir(_ACCOUNT)) == before


def test_a_non_string_as_of_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())

    with pytest.raises(SchedulerError) as excinfo:
        _scheduler(store).tick(935)  # type: ignore[arg-type]

    assert excinfo.value.code == "not_a_bar_boundary"


def test_an_aware_timestamp_names_the_same_bar_as_its_hh_mm_label(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.create(_account())
    scheduler = _scheduler(store)

    tick = scheduler.tick(_iso(_FIRST))

    assert tick.status == "applied"
    assert tick.bar_label == _FIRST
    assert json.loads(tick.document)["bar_time"] == _iso(_FIRST)
    assert scheduler.tick(_iso(_SECOND)).status == "applied"


# ── 4. the stale / closed state classes ─────────────────────────────────


def test_a_closed_session_returns_session_closed_and_writes_nothing(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.create(_account())
    store.save_account_atomic(
        replace(store.load(_ACCOUNT), watermark=_DAY, watermark_bar=_LAST)
    )
    before = _tree(store.account_dir(_ACCOUNT))
    scheduler = _scheduler(store)

    for label in (_FIRST, _LAST):
        tick = scheduler.tick(label)
        assert tick.status == STATUS_SESSION_CLOSED
        assert tick.bar_label is None
        assert tick.run is None
        assert tick.document == ""

    assert _tree(store.account_dir(_ACCOUNT)) == before


def test_a_new_ledger_day_starts_at_0935_not_at_the_prior_sessions_label(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.create(_account())
    store.save_account_atomic(
        replace(store.load(_ACCOUNT), watermark=_PRIOR_DAY, watermark_bar=_LAST)
    )
    before = _bar_dirs(store)

    tick = _scheduler(store).tick(_FIRST)

    assert tick.status == "applied"
    assert tick.bar_label == _FIRST
    assert _bar_dirs(store) == [*before, "0935"]
    final = store.load(_ACCOUNT)
    assert final.watermark == _PRIOR_DAY
    assert final.watermark_bar == _FIRST


def test_a_dry_run_tick_returns_the_document_and_writes_nothing(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.create(_account())
    before = _tree(store.account_dir(_ACCOUNT))

    tick = _scheduler(store).tick(_FIRST, dry_run=True)

    assert tick.status == "dry_run"
    assert tick.bar_label == _FIRST
    assert tick.run is not None
    assert tick.run.step_path is None
    assert json.loads(tick.document)["bar_time"] == _iso(_FIRST)
    assert _tree(store.account_dir(_ACCOUNT)) == before


def test_the_tick_result_refuses_an_inconsistent_document(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.create(_account())
    applied = _scheduler(store).tick(_FIRST)

    with pytest.raises(ValueError, match="document"):
        SchedulerTick(
            status="applied",
            bar_label=_FIRST,
            run=applied.run,
            document="{}",
        )

    with pytest.raises(ValueError, match="must not carry a run"):
        SchedulerTick(
            status=STATUS_NOTHING_DUE,
            bar_label=None,
            run=applied.run,
            document=applied.document,
        )


# ── 5. purity ────────────────────────────────────────────────────────────

_CLOCK_CALLS = frozenset(
    {"now", "utcnow", "today", "time", "sleep", "monotonic", "perf_counter"}
)
_FORBIDDEN_MODULES = frozenset(
    {"time", "datetime", "threading", "asyncio", "random", "uuid"}
)
_EXPECTED_IMPORTS = {
    "__future__": {"annotations"},
    "dataclasses": {"dataclass"},
    "typing": {"Any", "Mapping"},
    ".bar_clock": {"BAR_LABELS", "bar_time", "next_bar"},
    ".feeds": {"FiveMinuteFeed"},
    ".limits_cn_a": {"CnALimitsClient"},
    ".rules": {"MarketRules"},
    ".schema_v13": {"BarStepRequest", "PaperTradeSchemaError", "parse_bar_time"},
    ".session_runner": {
        "BAR_STEP_STATUSES",
        "CODE_BAR_ALREADY_APPLIED",
        "STATUS_ADOPTED",
        "STATUS_ALREADY_APPLIED",
        "STATUS_APPLIED",
        "STATUS_DRY_RUN",
        "BarStepRun",
        "SessionRunnerError",
        "bar_step",
    },
    ".store": {"AccountStore"},
}


def _module_source() -> str:
    import src.tools.paper_trade.scheduler as module

    return Path(module.__file__).read_text(encoding="utf-8")


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def test_the_scheduler_module_reads_no_clock_and_spawns_no_worker() -> None:
    tree = ast.parse(_module_source())
    calls = [
        f"{node.lineno}:{_call_name(node.func)}()"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node.func) in _CLOCK_CALLS
    ]
    imports = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert calls == []
    assert {module.split(".")[0] for module in imports} & _FORBIDDEN_MODULES == set()


def test_the_scheduler_module_import_surface_is_pinned() -> None:
    tree = ast.parse(_module_source())
    imports: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            imports.setdefault(module, set()).update(
                alias.name for alias in node.names
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.setdefault(alias.name, set())

    assert imports == _EXPECTED_IMPORTS


def test_the_scheduler_module_has_no_executable_clock_or_sleep_reference() -> None:
    source = _module_source()
    executable = " ".join(
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in (tokenize.STRING, tokenize.COMMENT)
    ).replace("datetime", "dt")

    for forbidden in ("sleep", "threading", "asyncio", "utcnow", "today("):
        assert forbidden not in executable


def test_the_error_vocabulary_is_exactly_the_four_stable_codes() -> None:
    assert SCHEDULER_ERROR_CODES == (
        "not_a_bar_boundary",
        "bar_in_progress",
        "nothing_due",
        "session_closed",
    )
    with pytest.raises(ValueError, match="unknown scheduler error code"):
        SchedulerError("oops", "not a code")
