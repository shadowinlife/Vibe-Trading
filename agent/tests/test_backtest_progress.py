"""progress.json stage reporting: atomic shape, best-effort writes, runner sequence.

The engine subprocess publishes ``{"stage", "pct", "updated_at"}`` snapshots
into the run dir so out-of-band readers can follow a long backtest. Two
contracts are pinned here: the write is atomic and never raises into the run,
and ``backtest.runner.main`` marks its stage boundaries in order — including
``failed`` (last pct kept) on error paths and ``done``(100) on success.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from backtest import progress as progress_mod
from backtest import runner
from backtest.progress import PROGRESS_FILENAME, write_progress


def test_write_progress_payload_shape(tmp_path: Path) -> None:
    write_progress(tmp_path, "data_load", 30)

    payload = json.loads((tmp_path / PROGRESS_FILENAME).read_text(encoding="utf-8"))
    assert set(payload) == {"stage", "pct", "updated_at"}
    assert payload["stage"] == "data_load"
    assert payload["pct"] == 30.0
    assert isinstance(payload["pct"], float)
    parsed = datetime.fromisoformat(payload["updated_at"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_write_progress_overwrites_and_leaves_no_tmp_files(tmp_path: Path) -> None:
    write_progress(tmp_path, "validate", 5)
    write_progress(tmp_path, "done", 100)

    payload = json.loads((tmp_path / PROGRESS_FILENAME).read_text(encoding="utf-8"))
    assert payload["stage"] == "done"
    assert payload["pct"] == 100.0
    assert sorted(p.name for p in tmp_path.iterdir()) == [PROGRESS_FILENAME]


def test_write_progress_swallows_missing_run_dir(tmp_path: Path) -> None:
    write_progress(tmp_path / "does-not-exist", "validate", 5)


def test_write_progress_swallows_unwritable_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / PROGRESS_FILENAME).write_text("sentinel", encoding="utf-8")

    def _deny_replace(*args, **kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(progress_mod.os, "replace", _deny_replace)
    write_progress(tmp_path, "done", 100)

    assert (tmp_path / PROGRESS_FILENAME).read_text(encoding="utf-8") == "sentinel"
    assert sorted(p.name for p in tmp_path.iterdir()) == [PROGRESS_FILENAME]


def _synthetic_run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a minimal run dir and stub every network/engine dependency."""
    run_dir = tmp_path / "run"
    (run_dir / "code").mkdir(parents=True)
    (run_dir / "code" / "signal_engine.py").write_text(
        "class SignalEngine:\n    pass\n", encoding="utf-8"
    )
    (run_dir / "config.json").write_text(
        json.dumps(
            {
                "codes": ["AAPL.US"],
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
                "source": "yahoo",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))

    frame = pd.DataFrame(
        {"open": [10.0], "high": [10.0], "low": [10.0], "close": [10.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-01-01")]),
    )

    class FakeLoader:
        name = "yahoo"

        def fetch(self, codes, start_date, end_date, **kwargs):
            del start_date, end_date, kwargs
            return {codes[0]: frame}

    monkeypatch.setattr(runner, "_get_loader", lambda source: FakeLoader)
    monkeypatch.setattr(
        runner,
        "_load_module_from_file",
        lambda path, name: SimpleNamespace(SignalEngine=type("SignalEngine", (), {})),
    )
    monkeypatch.setattr(runner, "_validate_signal_engine_class", lambda cls: None)
    return run_dir


def test_runner_main_marks_stage_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _synthetic_run_dir(tmp_path, monkeypatch)
    marks: list[tuple[str, float]] = []

    class FakeEngine:
        def run_backtest(self, config, loader, signal_engine, path, **kwargs):
            del config, loader, signal_engine, path, kwargs
            marks.append(("engine_ran", -1.0))

    monkeypatch.setattr(
        runner, "_create_market_engine", lambda source, config, codes: FakeEngine()
    )
    monkeypatch.setattr(
        runner,
        "write_progress",
        lambda run_dir_, stage, pct: marks.append((stage, pct)),
    )

    runner.main(run_dir)

    assert marks == [
        ("validate", 5.0),
        ("signal", 15.0),
        ("data_load", 30.0),
        ("simulate", 50.0),
        ("engine_ran", -1.0),
        ("done", 100.0),
    ]


def test_runner_main_writes_real_progress_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _synthetic_run_dir(tmp_path, monkeypatch)

    class FakeEngine:
        def run_backtest(self, config, loader, signal_engine, path, **kwargs):
            del config, loader, signal_engine, path, kwargs
            snapshot = json.loads(
                (run_dir / PROGRESS_FILENAME).read_text(encoding="utf-8")
            )
            assert snapshot["stage"] == "simulate"
            assert snapshot["pct"] == 50.0

    monkeypatch.setattr(
        runner, "_create_market_engine", lambda source, config, codes: FakeEngine()
    )

    runner.main(run_dir)

    final = json.loads((run_dir / PROGRESS_FILENAME).read_text(encoding="utf-8"))
    assert final["stage"] == "done"
    assert final["pct"] == 100.0


def test_runner_failure_keeps_last_pct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _synthetic_run_dir(tmp_path, monkeypatch)

    class ExplodingEngine:
        def run_backtest(self, config, loader, signal_engine, path, **kwargs):
            del config, loader, signal_engine, path, kwargs
            raise RuntimeError("engine boom")

    monkeypatch.setattr(
        runner, "_create_market_engine", lambda source, config, codes: ExplodingEngine()
    )

    with pytest.raises(RuntimeError, match="engine boom"):
        runner.main(run_dir)

    final = json.loads((run_dir / PROGRESS_FILENAME).read_text(encoding="utf-8"))
    assert final["stage"] == "failed"
    assert final["pct"] == 50.0


def test_runner_error_envelope_path_marks_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _synthetic_run_dir(tmp_path, monkeypatch)
    (run_dir / "config.json").write_text(
        json.dumps({"codes": [], "start_date": "2026-01-01", "end_date": "2026-01-02"}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as excinfo:
        runner.main(run_dir)

    assert excinfo.value.code == 1
    final = json.loads((run_dir / PROGRESS_FILENAME).read_text(encoding="utf-8"))
    assert final["stage"] == "failed"
    assert final["pct"] == 0.0


def test_progress_write_failure_does_not_break_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _synthetic_run_dir(tmp_path, monkeypatch)

    class FakeEngine:
        ran = False

        def run_backtest(self, config, loader, signal_engine, path, **kwargs):
            del config, loader, signal_engine, path, kwargs
            type(self).ran = True

    monkeypatch.setattr(
        runner, "_create_market_engine", lambda source, config, codes: FakeEngine()
    )

    def _deny_replace(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(progress_mod.os, "replace", _deny_replace)

    runner.main(run_dir)

    assert FakeEngine.ran
    assert not (run_dir / PROGRESS_FILENAME).exists()
