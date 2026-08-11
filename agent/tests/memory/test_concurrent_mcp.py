"""Concurrency tests for the shared MemoryMCPAdapter (T4 PR3).

Runs in normal CI (no ``bench`` marker). A 5-worker ThreadPoolExecutor storm
mixes recall + save (+ occasional reinforce) against one shared adapter and
asserts:

* no deadlock — the storm finishes well inside a generous timeout;
* no exception escapes — every operation returns a dict envelope;
* no data corruption — entries parse cleanly afterwards, every saved unique
  name is present exactly once, and the MEMORY.md index references each
  saved entry exactly once.

The lock-timeout tests pin the OBSERVED best-effort semantics of the
underlying APIs (read from the code, not forced):
``PersistentMemory.add`` still writes after a lock timeout (best-effort
write), while ``save_lesson`` fails closed and the adapter reports
``skipped``.
"""

from __future__ import annotations

import sys
import time
from concurrent import futures
from pathlib import Path

import pytest

from src.config.accessor import reset_env_config
from src.memory.mcp_adapter import MemoryMCPAdapter
from src.memory.persistent import PersistentMemory

N_WORKERS = 5
OPS_PER_WORKER = 10
STORM_TIMEOUT_S = 60.0

_ALLOWED_STATUSES = {"ok", "skipped", "error"}


def _unique_content(worker_id: int, i: int) -> str:
    """Content unique per (worker, op) so the dedup window never triggers."""
    return (
        f"Concurrent session {worker_id} op {i}: notes on momentum risk "
        f"controls, position sizing and drawdown limits gathered while "
        f"stress-testing the shared memory adapter."
    )


@pytest.fixture()
def shared_store(tmp_path, monkeypatch):
    """One tmp-backed PersistentMemory + adapter shared by all sessions."""
    monkeypatch.setenv("VT_MEMORY", "on")
    reset_env_config()

    memory = PersistentMemory(tmp_path / "memory")
    adapter = MemoryMCPAdapter(memory=memory)
    # Seed one entry so recall and reinforce have a stable target, and prime
    # the cached EnvConfig before any worker thread touches it.
    seeded = adapter.memory_save(
        "seed entry",
        "seeded before the storm",
        "Reference notes about momentum backtest risk controls shared by "
        "every concurrent session in this test module.",
    )
    assert seeded["status"] == "ok"
    return adapter, memory


def _session(adapter: MemoryMCPAdapter, worker_id: int) -> list[tuple[str, str, dict]]:
    """Run one session's mixed op sequence; returns (op, name, envelope)."""
    envelopes: list[tuple[str, str, dict]] = []
    for i in range(OPS_PER_WORKER):
        if i % 3 == 0:
            name = f"conc {worker_id} {i}"
            result = adapter.memory_save(
                name, f"desc {worker_id}-{i}", _unique_content(worker_id, i)
            )
            envelopes.append(("save", name, result))
        elif i == 5:
            result = adapter.memory_reinforce("seed entry", "task_success")
            envelopes.append(("reinforce", "seed entry", result))
        else:
            result = adapter.memory_recall("momentum risk controls", top_k=3)
            envelopes.append(("recall", "", result))
    return envelopes


class TestConcurrentStorm:
    def test_storm_no_deadlock_no_exception_no_corruption(self, shared_store):
        adapter, memory = shared_store
        start = time.monotonic()

        with futures.ThreadPoolExecutor(max_workers=N_WORKERS) as pool:
            pending = [pool.submit(_session, adapter, w) for w in range(N_WORKERS)]
            done, not_done = futures.wait(pending, timeout=STORM_TIMEOUT_S)

        # No deadlock: everything completed inside the generous timeout.
        assert not not_done, f"{len(not_done)} sessions still running (deadlock?)"
        assert time.monotonic() - start < STORM_TIMEOUT_S

        # No exception escapes: result() re-raises worker exceptions, and
        # every operation must have produced a well-formed dict envelope.
        all_ops: list[tuple[str, str, dict]] = []
        for future in done:
            all_ops.extend(future.result(timeout=5))
        assert len(all_ops) == N_WORKERS * OPS_PER_WORKER
        for op, _name, envelope in all_ops:
            assert isinstance(envelope, dict), f"{op} returned {type(envelope)}"
            assert envelope.get("status") in _ALLOWED_STATUSES, envelope

        # Uncontended-by-design ops must not degrade to error envelopes.
        saves = [(name, env) for op, name, env in all_ops if op == "save"]
        for name, envelope in saves:
            assert envelope["status"] == "ok", f"save {name!r} -> {envelope}"
        for op, _name, envelope in all_ops:
            if op == "recall":
                assert envelope["status"] == "ok", envelope
            elif op == "reinforce":
                # Session-delta caps may legitimately skip later events.
                assert envelope["status"] in ("ok", "skipped"), envelope

        # No data corruption: the store parses cleanly and every saved
        # unique name is present exactly once.
        entries = memory.list_entries()
        titles = [entry.title for entry in entries]
        for name, _envelope in saves:
            assert titles.count(name) == 1, f"{name!r} appears {titles.count(name)}x"

        # MEMORY.md index invariant: one index line per saved entry.
        index_text = (memory._dir / "MEMORY.md").read_text(encoding="utf-8")
        for name, _envelope in saves:
            assert index_text.count(f"[{name}]") == 1, f"index broken for {name!r}"


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl locks are POSIX-only")
class TestLockTimeoutBestEffort:
    def _hold_lock(self, lock_path: Path):
        """Open and exclusively flock ``lock_path``; caller closes the fd."""
        import fcntl

        lock_path.touch(exist_ok=True)
        holder = open(lock_path, "w")
        fcntl.flock(holder, fcntl.LOCK_EX)
        return holder

    def test_save_still_writes_after_lock_timeout(self, shared_store, monkeypatch):
        """add() under lock timeout performs a best-effort write (observed)."""
        import src.memory.persistent as persistent

        adapter, memory = shared_store
        monkeypatch.setattr(persistent, "_LOCK_TIMEOUT_S", 0.05)

        holder = self._hold_lock(memory._dir / ".lock")
        try:
            result = adapter.memory_save(
                "contended entry",
                "saved while the lock is held",
                "Unique content written during simulated lock contention to "
                "pin the best-effort write semantics of PersistentMemory.add.",
            )
        finally:
            holder.close()

        # Never raises; add() logs a warning and still writes the entry.
        assert result["status"] == "ok"
        assert any(e.title == "contended entry" for e in memory.list_entries())

    def test_reflect_fails_closed_under_lock_timeout(self, tmp_path, monkeypatch):
        """save_lesson under lock timeout skips the append (fail-closed)."""
        import src.memory.persistent as persistent

        monkeypatch.setenv("VT_MEMORY", "on")
        monkeypatch.setenv("VT_MEMORY_REFLECTIONS", "1")
        # Redirect Path.home() so default_reflections_dir lands in tmp_path.
        monkeypatch.setenv("HOME", str(tmp_path))
        reset_env_config()
        monkeypatch.setattr(persistent, "_LOCK_TIMEOUT_S", 0.05)

        reflections_dir = tmp_path / ".vibe-trading" / "memory" / "reflections"
        reflections_dir.mkdir(parents=True)
        adapter = MemoryMCPAdapter(memory=PersistentMemory(tmp_path / "memory"))

        holder = self._hold_lock(reflections_dir / ".lock")
        try:
            result = adapter.memory_reflect("momentum", {"sharpe": 1.2}, {"window": 20})
        finally:
            holder.close()

        # Fail-closed: skipped envelope, and no lesson line was appended.
        assert result["status"] == "skipped"
        assert not list(reflections_dir.glob("*.jsonl"))
