"""Tests for reflection lessons storage and retrieval (src.memory.reflections).

EnvConfig singleton hygiene: the shared autouse ``_reset_env_config`` fixture
in ``agent/tests/conftest.py`` clears the cached config before and after every
test in this package, so ``monkeypatch.setenv``-based flag toggles here never
leak across tests.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from src.memory.reflections import (
    ReflectionLesson,
    auto_reflect_from_run_dir,
    save_lesson,
    search_reflections,
    updated_confidence,
)


@pytest.fixture()
def enable_reflections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn on the reflections feature flag for a test."""
    monkeypatch.setenv("VT_MEMORY_REFLECTIONS", "1")


def _make_lesson(**overrides) -> ReflectionLesson:
    base = dict(
        strategy_type="momentum",
        original_decision={"codes": ["159659.SZ"], "interval": "1D"},
        outcome={"sharpe": 1.2, "max_drawdown": -0.15},
        lesson="Momentum entries during high volatility degrade sharpe.",
        parameters={"lookback": 20},
        confidence=0.5,
        tags=["momentum", "volatility"],
    )
    base.update(overrides)
    return ReflectionLesson(**base)


class TestDataclassRoundtrip:
    """ReflectionLesson serialization tolerance."""

    def test_to_dict_from_dict_roundtrip(self) -> None:
        lesson = _make_lesson(
            id="lesson_20260726_abc123", created_at="2026-07-26T00:00:00Z"
        )
        restored = ReflectionLesson.from_dict(lesson.to_dict())
        assert restored == lesson

    def test_from_dict_tolerates_missing_and_extra_keys(self) -> None:
        restored = ReflectionLesson.from_dict({"lesson": "text only", "unknown_key": 1})
        assert restored.lesson == "text only"
        assert restored.version == 1
        assert restored.tags == []
        assert restored.supporting_cases == []


class TestSaveAndSearch:
    """save_lesson + search_reflections behaviour."""

    def test_roundtrip_tag_and_keyword_match(
        self, tmp_path: Path, enable_reflections: None
    ) -> None:
        lesson_id = save_lesson(_make_lesson(), base_dir=tmp_path)
        assert lesson_id.startswith("lesson_")

        # Tag match
        by_tag = search_reflections(
            strategy_type="momentum", keywords=["volatility"], base_dir=tmp_path
        )
        assert len(by_tag) == 1
        assert by_tag[0].id == lesson_id

        # Case-insensitive substring match over lesson text
        by_text = search_reflections(keywords=["SHARPE"], base_dir=tmp_path)
        assert len(by_text) == 1

        # Non-matching keyword yields nothing
        assert search_reflections(keywords=["nomatch_xyz"], base_dir=tmp_path) == []

    def test_top_k_limits_results(
        self, tmp_path: Path, enable_reflections: None
    ) -> None:
        for i in range(4):
            save_lesson(
                _make_lesson(
                    lesson=f"case {i} sharpe insight",
                    created_at=f"2026-07-0{i + 1}T00:00:00Z",
                ),
                base_dir=tmp_path,
            )
        results = search_reflections(keywords=["sharpe"], top_k=2, base_dir=tmp_path)
        assert len(results) == 2
        # Equal scores: newest first
        assert results[0].created_at > results[1].created_at

    @pytest.mark.parametrize("top_k", [0, -1])
    def test_top_k_non_positive_returns_empty(
        self, tmp_path: Path, enable_reflections: None, top_k: int
    ) -> None:
        save_lesson(_make_lesson(), base_dir=tmp_path)
        assert (
            search_reflections(keywords=["sharpe"], top_k=top_k, base_dir=tmp_path)
            == []
        )

    def test_lock_timeout_skips_write(
        self, tmp_path: Path, enable_reflections: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """save_lesson is fail-closed: no unlocked append on lock timeout."""

        @contextmanager
        def _timed_out_lock(_dir: Path):
            yield False

        monkeypatch.setattr("src.memory.reflections.memory_lock", _timed_out_lock)
        assert save_lesson(_make_lesson(), base_dir=tmp_path) == ""
        assert not (tmp_path / "momentum.jsonl").exists()

    def test_jsonl_append_semantics(
        self, tmp_path: Path, enable_reflections: None
    ) -> None:
        save_lesson(_make_lesson(), base_dir=tmp_path)
        save_lesson(_make_lesson(lesson="second lesson"), base_dir=tmp_path)
        path = tmp_path / "momentum.jsonl"
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
        assert len(lines) == 2
        assert all(json.loads(ln)["strategy_type"] == "momentum" for ln in lines)

    def test_filename_sanitization(
        self, tmp_path: Path, enable_reflections: None
    ) -> None:
        save_lesson(_make_lesson(strategy_type="Mean/Rev ersion!"), base_dir=tmp_path)
        assert (tmp_path / "mean_rev_ersion.jsonl").exists()

    def test_corrupted_line_tolerance(
        self, tmp_path: Path, enable_reflections: None
    ) -> None:
        save_lesson(_make_lesson(), base_dir=tmp_path)
        path = tmp_path / "momentum.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("{not valid json]\n")
        save_lesson(
            _make_lesson(lesson="post-garbage sharpe lesson"), base_dir=tmp_path
        )

        results = search_reflections(keywords=["sharpe"], base_dir=tmp_path)
        assert len(results) == 2

    def test_disabled_flag_blocks_save_and_search(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VT_MEMORY_REFLECTIONS", raising=False)
        monkeypatch.delenv("VT_MEMORY", raising=False)
        assert save_lesson(_make_lesson(), base_dir=tmp_path) is None
        assert list(tmp_path.iterdir()) == []
        assert search_reflections(keywords=["sharpe"], base_dir=tmp_path) == []


class TestAutoReflect:
    """auto_reflect_from_run_dir behaviour."""

    @staticmethod
    def _write_run_card(run_dir: Path, **card_overrides) -> None:
        card = {
            "backtest": {
                "codes": ["159659.SZ"],
                "engine": "daily",
                "interval": "1D",
            },
            "metrics": {
                "sharpe": 0.61,
                "max_drawdown": -0.0838,
                "annual_return": 0.0287,
                "total_return": 0.088,
                "win_rate": 0.55,
            },
        }
        card.update(card_overrides)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run_card.json").write_text(json.dumps(card), encoding="utf-8")

    def test_flag_disabled_returns_none_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VT_MEMORY_REFLECTIONS", raising=False)
        monkeypatch.delenv("VT_MEMORY", raising=False)
        run_dir = tmp_path / "run"
        self._write_run_card(run_dir)
        store = tmp_path / "store"
        assert auto_reflect_from_run_dir(run_dir, base_dir=store) is None
        assert not store.exists()

    def test_auto_reflect_from_synthetic_run_dir(
        self, tmp_path: Path, enable_reflections: None
    ) -> None:
        run_dir = tmp_path / "run"
        self._write_run_card(run_dir)
        store = tmp_path / "store"
        lesson_id = auto_reflect_from_run_dir(run_dir, base_dir=store)
        assert lesson_id is not None

        results = search_reflections(strategy_type="unknown", base_dir=store)
        assert len(results) == 1
        lesson = results[0]
        # Scalar metrics extracted with real run_card.json field names
        assert lesson.outcome["sharpe"] == 0.61
        assert lesson.outcome["max_drawdown"] == -0.0838
        assert lesson.outcome["annual_return"] == 0.0287
        assert lesson.original_decision["engine"] == "daily"
        assert "auto_reflect" in lesson.tags

    def test_strategy_type_from_card_when_present(
        self, tmp_path: Path, enable_reflections: None
    ) -> None:
        run_dir = tmp_path / "run"
        self._write_run_card(run_dir, strategy_type="dual_ma")
        store = tmp_path / "store"
        assert auto_reflect_from_run_dir(run_dir, base_dir=store) is not None
        assert (store / "dual_ma.jsonl").exists()

    def test_missing_run_card_returns_none(
        self, tmp_path: Path, enable_reflections: None
    ) -> None:
        assert auto_reflect_from_run_dir(tmp_path / "empty", base_dir=tmp_path) is None


class TestUpdatedConfidence:
    """Pure confidence update helper."""

    def test_supporting_cases_raise_confidence(self) -> None:
        assert updated_confidence(0.5, 2, 0) == pytest.approx(0.6)

    def test_counter_cases_lower_confidence_faster(self) -> None:
        assert updated_confidence(0.5, 0, 2) == pytest.approx(0.34)

    def test_clamped_to_unit_interval(self) -> None:
        assert updated_confidence(0.9, 10, 0) == 1.0
        assert updated_confidence(0.1, 0, 10) == 0.0
