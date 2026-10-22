"""Todo 27 gate — crash / idempotency / concurrency integration for the runner.

Split out of ``test_paper_trade_p1_runner.py``: that module keeps its 21 unit
tests untouched (590 lines), and adding the file-level crash matrix here keeps
both modules under the repository's 800-line hard cap.

``bar_step`` write order is ``plan -> bar step -> account.json -> session.json``,
so every crash point has one expected recoverable state::

    C0  refusal before any write (bad request / feed)  zero files, clean retry
    C1  failure inside the bar-step write              no step, no account, clean retry
    C2  crash after the step, before the account       orphan adopted once, one fill set
    C3  crash after the account, before the summary    account is authoritative, retry refuses
    L   lock held out-of-band (second fd, LOCK_NB)     account_locked, zero files
    D   duplicate bar                                  bar_already_applied, zero bytes changed
    R   process restart                                resting order restored, same id

C1/C2 are the same retry helper in ``test_a_retry_helper...``; C2 additionally
proves the adopted run is byte-identical to a clean run (step and account) so a
retry cannot duplicate a fill.  C3 is the re-derivable summary rule: the
account closes the session, the summary is written LAST and can never lie.

Offline, deterministic: a tmp runtime root and the committed 5m fixtures only.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from src.tools.paper_trade import bar_clock, bar_store, session_summary
from src.tools.paper_trade.account import create_account
from src.tools.paper_trade.bar_engine import BarEngineState
from src.tools.paper_trade.feeds import ReplayFeed
from src.tools.paper_trade.schema import (
    AccountState,
    BarStepRequest,
    OrderAction,
    Position,
    parse_slippage,
)
from src.tools.paper_trade.session_runner import (
    STATUS_ADOPTED,
    STATUS_APPLIED,
    SessionRunnerError,
    bar_request,
    bar_step,
)
from src.tools.paper_trade.store import AccountStore
from src.tools.paper_trade.storage_io import LOCK_FILE
from tests.paper_trade_5m_fixtures import continuous_bars, load_5m

try:  # POSIX; the store's Windows branch uses msvcrt instead.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

_ACCOUNT = "pa_p1_runner_crash"
_DAY = "2026-10-20"
_SYM = "600519.SH"
_FIRST = "09:35"
_NEXT = "09:40"
_LAST = "15:00"
#: The fixture trades flat at 1421.00 all day, so this buy limit never fills.
_REST_LIMIT = 1300.0


# ── fixtures / helpers (deliberately local to this module) ───────────────


def _store(root: Path) -> AccountStore:
    return AccountStore(root / "paper_trade")


def _account(**overrides):
    kwargs = {
        "account_id": _ACCOUNT,
        "market": "cn_a",
        "start_date": _DAY,
        "initial_cash": 200_000.0,
        "initial_positions": (Position(symbol=_SYM, qty=100.0, avg_cost=1400.0),),
        "today": _DAY,
        "next_trading_day": "2026-10-21",
        "now": f"{_DAY}T08:00:00+08:00",
    }
    kwargs.update(overrides)
    return create_account(**kwargs)


def _feed() -> ReplayFeed:
    return ReplayFeed(frames={_SYM: {_DAY: continuous_bars(load_5m(_SYM, _DAY))}})


def _request(
    label: str,
    *,
    actions: tuple[OrderAction, ...] = (),
    account: AccountState | None = None,
) -> BarStepRequest:
    return BarStepRequest(
        account_id=_ACCOUNT,
        market="cn_a",
        account_state=account if account is not None else AccountState(cash=200_000.0),
        from_date=_DAY,
        to_date=_DAY,
        bar_time=f"{_DAY}T{label}:00+08:00",
        order_actions=actions,
        slippage=parse_slippage(None),
    )


def _place() -> OrderAction:
    return OrderAction(action="place", symbol=_SYM, side="buy", qty=100.0)


def _resting_place() -> OrderAction:
    return OrderAction(action="place", symbol=_SYM, side="buy", qty=100.0, limit=_REST_LIMIT)


def _cancel_payload(order_id: str) -> dict:
    """The MCP-shaped payload a caller rebuilds after a process restart."""
    return {
        "account_id": _ACCOUNT,
        "market": "cn_a",
        "from_date": _DAY,
        "to_date": _DAY,
        "bar_time": f"{_DAY}T{_NEXT}:00+08:00",
        "interval": "5m",
        "session": "cn_a",
        # Parsed by the ONE boundary parser, then replaced by ``bar_request``
        # with the reloaded account (the ledger carrier the step audits).
        "account_state": {"cash": 0.0, "positions": []},
        "order_actions": [{"action": "cancel", "order_id": order_id}],
    }


def _snapshot(root: Path) -> dict[str, str]:
    """Every file under ``root`` with its sha256 (the no-write assertion)."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _step_labels(store: AccountStore) -> tuple[str, ...]:
    """The materialized bar-step directories of the day, as ``HHMM`` names."""
    bars = store.steps_dir(_ACCOUNT) / _DAY / "bars"
    if not bars.is_dir():
        return ()
    return tuple(sorted(path.name for path in bars.iterdir() if path.is_dir()))


def _label(dirname: str) -> str:
    return f"{dirname[:2]}:{dirname[2:]}"


def _fill_total(store: AccountStore) -> int:
    """Every fill recorded by every stored bar step (the one-fill-set count)."""
    return sum(
        len(bar_store.read_bar_step(store.account_dir(_ACCOUNT), _DAY, _label(name)).fills)
        for name in _step_labels(store)
    )


def _retry(
    store: AccountStore, request: BarStepRequest, feed: ReplayFeed, *, attempts: int = 3
):
    """Call ``bar_step`` again on a transient failure; domain refusals still raise.

    ``SessionRunnerError`` subclasses ``RuntimeError``, so it must be re-raised
    first: a refused bar is a terminal answer, not something to retry.
    """
    transient: list[str] = []
    for _ in range(attempts):
        try:
            return bar_step(store, request, feed)
        except SessionRunnerError:
            raise
        except RuntimeError as exc:
            transient.append(str(exc))
    raise AssertionError(f"bar_step never recovered from the transient failures {transient!r}")


# ── C2: crash between the step and the account ───────────────────────────


def test_a_crash_between_the_step_and_the_account_is_adopted_without_a_second_fill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crash_store = _store(tmp_path / "crash")
    clean_store = _store(tmp_path / "clean")
    crash_store.create(_account())
    clean_store.create(_account())
    pristine = crash_store.account_path(_ACCOUNT).read_bytes()
    request = _request(_FIRST, actions=(_place(),), account=crash_store.load(_ACCOUNT).state())
    clean_request = _request(_FIRST, actions=(_place(),), account=clean_store.load(_ACCOUNT).state())

    real_save = crash_store.save_account_atomic
    attempts = {"count": 0}

    def explode_once(account) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("crash between the step and the account")
        real_save(account)

    monkeypatch.setattr(crash_store, "save_account_atomic", explode_once)

    with pytest.raises(RuntimeError, match="crash between the step and the account"):
        bar_step(crash_store, request, _feed())

    # The step is durable and complete even though the account never moved.
    assert _step_labels(crash_store) == ("0935",)
    orphan = bar_store.read_bar_step(crash_store.account_dir(_ACCOUNT), _DAY, _FIRST)
    assert set(orphan.manifest["files"]) >= {
        "step.json",
        "orders.jsonl",
        "fills.jsonl",
        "order_events.jsonl",
        "positions.jsonl",
        "events.jsonl",
    }
    assert orphan.step["counts"]["fills"] == 1
    assert len(orphan.fills) == 1
    croaked = crash_store.load(_ACCOUNT)
    assert croaked.watermark is None
    assert croaked.watermark_bar == ""
    assert crash_store.account_path(_ACCOUNT).read_bytes() == pristine

    adopted = bar_step(crash_store, request, _feed())
    clean = bar_step(clean_store, clean_request, _feed())

    assert adopted.status == STATUS_ADOPTED
    assert clean.status == STATUS_APPLIED
    assert attempts["count"] == 2
    assert _step_labels(crash_store) == ("0935",)
    assert _fill_total(crash_store) == 1
    crash_step = bar_store.read_bar_step(crash_store.account_dir(_ACCOUNT), _DAY, _FIRST)
    clean_step = bar_store.read_bar_step(clean_store.account_dir(_ACCOUNT), _DAY, _FIRST)
    assert crash_step.fills == clean_step.fills
    assert _snapshot(crash_store.steps_dir(_ACCOUNT) / _DAY / "bars" / "0935") == _snapshot(
        clean_store.steps_dir(_ACCOUNT) / _DAY / "bars" / "0935"
    )
    assert (
        crash_store.account_path(_ACCOUNT).read_bytes()
        == clean_store.account_path(_ACCOUNT).read_bytes()
    )


# ── D: duplicate bar is a whole-tree no-op ───────────────────────────────


def test_a_duplicate_bar_is_a_no_op_across_the_whole_account_tree(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = store.account_dir(_ACCOUNT)
    store.create(_account())
    bar_step(store, _request(_FIRST, actions=(_place(),), account=store.load(_ACCOUNT).state()), _feed())
    resting = _request(_NEXT, actions=(_resting_place(),), account=store.load(_ACCOUNT).state())
    bar_step(store, resting, _feed())
    before = _snapshot(root)

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_step(store, resting, _feed())

    assert excinfo.value.code == "bar_already_applied"
    assert _snapshot(root) == before
    assert _step_labels(store) == ("0935", "0940")
    assert _fill_total(store) == 1


# ── L: a lock held through a SECOND file descriptor ──────────────────────


@pytest.mark.skipif(fcntl is None, reason="POSIX flock is unavailable on this platform")
def test_an_out_of_band_lock_is_account_locked_and_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = store.account_dir(_ACCOUNT)
    store.create(_account())
    request = _request(_FIRST, actions=(_place(),), account=store.load(_ACCOUNT).state())

    fd = os.open(str(root / LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = _snapshot(root)
        with pytest.raises(SessionRunnerError) as excinfo:
            bar_step(store, request, _feed())
        assert excinfo.value.code == "account_locked"
        assert _snapshot(root) == before
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    run = bar_step(store, request, _feed())
    assert run.status == STATUS_APPLIED
    assert _step_labels(store) == ("0935",)


# ── R: a fresh process restores the resting book from account.json ───────


def test_a_restart_restores_the_resting_order_with_the_same_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    bar_step(
        store,
        _request(_FIRST, actions=(_resting_place(),), account=store.load(_ACCOUNT).state()),
        _feed(),
    )

    reloaded = store.load(_ACCOUNT)
    assert len(reloaded.open_orders) == 1
    resting = reloaded.open_orders[0]
    assert resting.status == "new"
    order_id = resting.order_id

    restarted = BarEngineState.from_account(reloaded)
    restored = restarted.book.open_orders()
    assert [row.order_id for row in restored] == [order_id]
    assert restored[0].status == "new"
    assert restarted.order_seq == reloaded.order_seq

    run = bar_step(store, bar_request(_cancel_payload(order_id), reloaded), _feed())

    assert run.status == STATUS_APPLIED
    assert store.load(_ACCOUNT).open_orders == ()
    step = bar_store.read_bar_step(store.account_dir(_ACCOUNT), _DAY, _NEXT)
    cancelled = [row for row in step.orders if row["order_id"] == order_id]
    assert [row["status"] for row in cancelled] == ["canceled"]
    assert any(
        event["event_type"] == "canceled" and event["order_id"] == order_id
        for event in step.order_events
    )


# ── C1/C2: a retry helper never duplicates a step or a fill ──────────────


@pytest.mark.parametrize("phase", ("before_step", "after_step"))
def test_a_retry_helper_never_duplicates_a_step_or_a_fill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    store = _store(tmp_path / phase)
    store.create(_account())
    request = _request(_FIRST, actions=(_place(),), account=store.load(_ACCOUNT).state())
    failures = {"count": 0}

    if phase == "before_step":
        real_write = bar_store.write_bar_step

        def flaky_write(root, content):
            if failures["count"] == 0:
                failures["count"] += 1
                raise RuntimeError("transient failure inside the step write")
            return real_write(root, content)

        monkeypatch.setattr(bar_store, "write_bar_step", flaky_write)
        expected = STATUS_APPLIED
    else:
        real_save = store.save_account_atomic

        def flaky_save(account):
            if failures["count"] == 0:
                failures["count"] += 1
                raise RuntimeError("transient failure after the step write")
            return real_save(account)

        monkeypatch.setattr(store, "save_account_atomic", flaky_save)
        expected = STATUS_ADOPTED

    run = _retry(store, request, _feed())

    assert failures["count"] == 1
    assert run.status == expected
    assert _step_labels(store) == ("0935",)
    assert _fill_total(store) == 1
    assert store.load(_ACCOUNT).watermark_bar == _FIRST


# ── C3: crash at the session summary write ───────────────────────────────


def test_a_crash_at_the_summary_write_leaves_the_account_as_the_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    root = store.account_dir(_ACCOUNT)
    store.create(_account())
    feed = _feed()

    real_summary = session_summary.write_session_summary
    crashes = {"count": 0}

    def flaky_summary(root_path, **kwargs):
        if kwargs.get("status") == "closed" and crashes["count"] == 0:
            crashes["count"] += 1
            raise RuntimeError("crash at the session summary write")
        return real_summary(root_path, **kwargs)

    monkeypatch.setattr(session_summary, "write_session_summary", flaky_summary)

    with pytest.raises(RuntimeError, match="crash at the session summary write"):
        for label in bar_clock.BAR_LABELS:
            account = store.load(_ACCOUNT)
            actions = (_place(),) if label == _FIRST else ()
            bar_step(store, _request(label, actions=actions, account=account.state()), feed)

    # The account owns the close; the step is durable; the summary is stale but
    # honest (it still says "open" at 14:55 rather than claiming the 15:00 close).
    closed = store.load(_ACCOUNT)
    assert crashes["count"] == 1
    assert closed.watermark == _DAY
    assert closed.watermark_bar == _LAST
    assert closed.open_orders == ()
    assert _step_labels(store) == tuple(label.replace(":", "") for label in bar_clock.BAR_LABELS)
    last_step = bar_store.read_bar_step(root, _DAY, _LAST)
    assert last_step.step["counts"]["fills"] == 0
    stale = session_summary.read_session_summary(root, _DAY)
    assert stale["status"] == "open"
    assert stale["applied_bar_count"] == len(bar_clock.BAR_LABELS) - 1
    crashed_tree = _snapshot(root)
    crashed_account = store.account_path(_ACCOUNT).read_bytes()

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_step(store, _request(_LAST, account=closed.state()), feed)

    assert excinfo.value.code == "bar_already_applied"
    assert _snapshot(root) == crashed_tree
    assert store.account_path(_ACCOUNT).read_bytes() == crashed_account
    assert _step_labels(store) == tuple(label.replace(":", "") for label in bar_clock.BAR_LABELS)
