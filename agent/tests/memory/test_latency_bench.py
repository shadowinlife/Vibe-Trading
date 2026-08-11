"""Latency benchmarks for the persistent memory stack (T4 PR3).

Every test here is marked ``bench``: normal CI runs skip them (see
``pytest_collection_modifyitems`` in tests/conftest.py) and they are only
executed when explicitly selected via ``pytest -m bench``.

Performance gates from the memory improvement plan §7.1 (search latency):
p50 < 200 ms and p95 < 500 ms over a 500-entry corpus. ``add`` and adapter
``memory_recall`` latencies are measured and recorded but have no gate.

On completion each variant appends a results block to the LOCAL baseline
document ``.omo/memory/LATENCY_BASELINES.md`` (workspace-relative, not part
of any PR). When that directory is absent the write is silently skipped.
"""

from __future__ import annotations

import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.config.accessor import reset_env_config
from src.memory.mcp_adapter import MemoryMCPAdapter
from src.memory.persistent import PersistentMemory

pytestmark = pytest.mark.bench

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINES_DOC = REPO_ROOT / ".omo" / "memory" / "LATENCY_BASELINES.md"

N_ENTRIES = 500
N_ITERATIONS = 50

# Gates from the memory improvement plan §7.1 (search only).
SEARCH_P50_GATE_MS = 200.0
SEARCH_P95_GATE_MS = 500.0

# Topic vocabulary so synthetic entries and queries share searchable terms.
_TOPICS = (
    "momentum breakout",
    "mean reversion",
    "volatility targeting",
    "pairs trading",
    "risk parity",
    "trend following",
    "statistical arbitrage",
    "position sizing",
    "drawdown control",
    "factor rotation",
)


def _entry_content(i: int) -> str:
    """Unique, quality-gate-friendly content for synthetic entry ``i``."""
    topic = _TOPICS[i % len(_TOPICS)]
    return (
        f"Synthetic memory {i} about {topic} strategies. It documents the "
        f"parameter sweep number {i}, the observed sharpe behaviour, and the "
        f"risk management rules applied during backtest validation runs."
    )


def _percentiles(samples_ms: list[float]) -> tuple[float, float]:
    """Return (p50, p95) in milliseconds using median + nearest-rank p95."""
    ordered = sorted(samples_ms)
    p50 = statistics.median(ordered)
    rank = max(0, min(len(ordered) - 1, round(0.95 * (len(ordered) - 1))))
    return p50, ordered[rank]


def _record_baseline(variant: str, rows: list[tuple[str, float, float]]) -> None:
    """Append a results block to the local baselines doc (best-effort).

    The doc lives under ``.omo/memory/`` which exists only in this workspace;
    its absence must never fail the benchmark.
    """
    if not BASELINES_DOC.parent.is_dir():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    machine = f"{platform.platform()} / Python {platform.python_version()}"
    block = [
        f"\n## {stamp} — variant `{variant}`\n",
        f"- Machine: {machine}",
        f"- Corpus: {N_ENTRIES} synthetic entries, {N_ITERATIONS} iterations/op",
        f"- Gates (search): p50 < {SEARCH_P50_GATE_MS:.0f} ms, "
        f"p95 < {SEARCH_P95_GATE_MS:.0f} ms\n",
        "| operation | p50 (ms) | p95 (ms) |",
        "|---|---|---|",
    ]
    block += [f"| {op} | {p50:.1f} | {p95:.1f} |" for op, p50, p95 in rows]
    text = "\n".join(block) + "\n"
    if BASELINES_DOC.exists():
        with open(BASELINES_DOC, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        header = (
            "# Memory latency baselines\n\n"
            "Local document (not part of any PR). Blocks are appended by\n"
            "`agent/tests/memory/test_latency_bench.py` on each `-m bench` run.\n"
        )
        BASELINES_DOC.write_text(header + text, encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_fts_singleton():
    """Reset the FTS singleton so the `full` variant uses a fresh index."""
    import src.memory.search_index as si

    original = si._shared_index
    si._shared_index = None
    yield
    if si._shared_index is not None:
        try:
            si._shared_index.close()
        except Exception:
            pass
    si._shared_index = original


@pytest.fixture(params=["on", "full"])
def bench_env(request, tmp_path, monkeypatch):
    """Configure the memory preset variant and a tmp-backed store."""
    variant = request.param
    monkeypatch.setenv("VT_MEMORY", variant)
    if variant == "full":
        # Keep the FTS database inside tmp_path for the full preset.
        import src.memory.search_index as si

        monkeypatch.setattr(si, "_DEFAULT_DB_PATH", tmp_path / "bench_fts.db")
    reset_env_config()

    memory = PersistentMemory(tmp_path / "memory")
    adapter = MemoryMCPAdapter(memory=memory)
    for i in range(N_ENTRIES):
        path = memory.add(f"bench entry {i}", _entry_content(i), "project")
        assert path is not None, f"population failed at entry {i}"
    return variant, memory, adapter


class TestLatencyGates:
    def test_search_add_recall_latency(self, bench_env):
        """Measure p50/p95 for find_relevant / add / memory_recall."""
        variant, memory, adapter = bench_env

        search_ms: list[float] = []
        for i in range(N_ITERATIONS):
            query = _TOPICS[i % len(_TOPICS)]
            start = time.perf_counter()
            memory.find_relevant(query)
            search_ms.append((time.perf_counter() - start) * 1000.0)

        add_ms: list[float] = []
        for i in range(N_ITERATIONS):
            # Unique names AND content keep the dedup window out of the path.
            name = f"bench extra {i}"
            content = _entry_content(N_ENTRIES + i) + f" Extra iteration {i}."
            start = time.perf_counter()
            saved = memory.add(name, content, "project")
            add_ms.append((time.perf_counter() - start) * 1000.0)
            assert saved is not None

        recall_ms: list[float] = []
        for i in range(N_ITERATIONS):
            query = _TOPICS[(i + 3) % len(_TOPICS)]
            start = time.perf_counter()
            envelope = adapter.memory_recall(query, top_k=5)
            recall_ms.append((time.perf_counter() - start) * 1000.0)
            assert envelope["status"] == "ok"

        rows = [
            ("find_relevant", *_percentiles(search_ms)),
            ("add", *_percentiles(add_ms)),
            ("memory_recall", *_percentiles(recall_ms)),
        ]
        _record_baseline(variant, rows)
        for op, p50, p95 in rows:
            print(f"[bench:{variant}] {op}: p50={p50:.1f}ms p95={p95:.1f}ms")

        search_p50, search_p95 = rows[0][1], rows[0][2]
        assert (
            search_p50 < SEARCH_P50_GATE_MS
        ), f"search p50 {search_p50:.1f}ms breaches {SEARCH_P50_GATE_MS}ms gate"
        assert (
            search_p95 < SEARCH_P95_GATE_MS
        ), f"search p95 {search_p95:.1f}ms breaches {SEARCH_P95_GATE_MS}ms gate"
