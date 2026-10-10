"""Reflection lessons: generalizable insights extracted from backtest outcomes.

Implements the T1b reflection-lesson store described in the memory improvement
plan (§4.7). Lessons are stored as append-only JSONL files under
``~/.vibe-trading/memory/reflections/``, one file per strategy type. Lessons
are never deleted — failed strategies are equally valuable for learning.

Retrieval uses tag intersection plus case-insensitive substring matching;
BM25 full-text retrieval is deferred to a later tier. The
``search_reflections(strategy_type, keywords, top_k)`` signature is the stable
internal contract that the future ``memory_reflect`` MCP tool will wrap.

Feature flag (via the centralized config accessor):
    VT_MEMORY_REFLECTIONS – enable reflection lessons storage & retrieval

Zero external dependencies — stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.memory.persistent import memory_lock

logger = logging.getLogger(__name__)

# Directory name under the memory base where lesson files live.
REFLECTIONS_DIRNAME = "reflections"

# Scalar metric keys extracted from run_card.json "metrics" (field names match
# agent/backtest/run_card.py output; see _scalar_metrics there).
_RUN_CARD_METRIC_KEYS = (
    "sharpe",
    "max_drawdown",
    "annual_return",
    "total_return",
    "win_rate",
)

# Filenames allow lowercase alphanumerics, underscore and hyphen only.
_FILENAME_DISALLOWED_RE = re.compile(r"[^a-z0-9_\-]")
# Keep sanitized strategy filenames short enough for any filesystem.
_MAX_FILENAME_STEM = 60

# Confidence update steps: counter-evidence is weighted heavier than
# supporting evidence so a lesson loses credibility faster than it gains it.
_SUPPORT_STEP = 0.05
_COUNTER_STEP = 0.08


def _reflections_enabled() -> bool:
    """Check if reflection lessons are enabled via VT_MEMORY_REFLECTIONS."""
    from src.config.accessor import get_env_config

    return get_env_config().memory.reflections_enabled


def default_reflections_dir() -> Path:
    """Resolve the reflections directory under the configured memory base.

    Computed at call time (not import time) so tests can redirect it by
    monkeypatching ``Path.home`` / ``HOME``. Resolution order mirrors
    ``src.memory.persistent._default_memory_base``: ``VT_MEMORY_BASE_DIR``
    override, else the ``VIBE_TRADING_HOME``-aware runtime root, else
    ``~/.vibe-trading/memory``.
    """
    from src.memory.persistent import _default_memory_base

    return _default_memory_base() / REFLECTIONS_DIRNAME


@dataclass
class ReflectionLesson:
    """A single generalizable lesson extracted from a backtest outcome."""

    id: str = ""
    version: int = 1
    created_at: str = ""  # ISO-8601
    strategy_type: str = ""  # e.g. "momentum", "mean_reversion"
    original_decision: dict = field(default_factory=dict)  # backtest params
    outcome: dict = field(default_factory=dict)  # return, sharpe, etc.
    lesson: str = ""  # natural language insight
    parameters: dict = field(default_factory=dict)  # quantitative thresholds
    confidence: float = 0.5  # [0.0, 1.0]
    supporting_cases: list = field(default_factory=list)
    counter_cases: list = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict suitable for one JSONL line."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReflectionLesson":
        """Build a lesson from a dict, tolerating missing or extra keys."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def updated_confidence(base: float, n_support: int, n_counter: int) -> float:
    """Pure confidence update: supporting cases raise, counter cases lower.

    Result is clamped to [0.0, 1.0]. Counter cases apply a larger step than
    supporting cases (see _SUPPORT_STEP / _COUNTER_STEP).
    """
    raw = base + n_support * _SUPPORT_STEP - n_counter * _COUNTER_STEP
    return min(1.0, max(0.0, raw))


def _sanitize_strategy_filename(strategy_type: str) -> str:
    """Map a strategy type to a safe JSONL file stem."""
    stem = _FILENAME_DISALLOWED_RE.sub("_", strategy_type.strip().lower())
    stem = stem[:_MAX_FILENAME_STEM].strip("_")
    return stem or "unknown"


def _generate_lesson_id(lesson: ReflectionLesson) -> str:
    """Generate a lesson id: lesson_{YYYYMMDD}_{6-char hash}."""
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    digest = hashlib.sha256(
        f"{lesson.strategy_type}|{lesson.lesson}|{lesson.created_at}".encode()
    ).hexdigest()[:6]
    return f"lesson_{date_part}_{digest}"


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def save_lesson(
    lesson: ReflectionLesson, base_dir: Optional[Path] = None
) -> Optional[str]:
    """Append a lesson to its strategy-type JSONL file.

    Returns:
        * ``lesson.id`` — the lesson was written successfully.
        * ``None`` — the reflections feature flag is disabled (permanent: the
          caller should not retry).
        * ``""`` (empty string) — the file lock could not be acquired within
          the timeout (transient: the caller may retry later).
    """
    if not _reflections_enabled():
        logger.debug("save_lesson skipped: VT_MEMORY_REFLECTIONS disabled")
        return None

    if not lesson.created_at:
        lesson.created_at = _now_iso()
    if not lesson.id:
        lesson.id = _generate_lesson_id(lesson)

    target_dir = Path(base_dir) if base_dir else default_reflections_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{_sanitize_strategy_filename(lesson.strategy_type)}.jsonl"

    line = json.dumps(lesson.to_dict(), ensure_ascii=False, default=str)
    with memory_lock(target_dir) as acquired:
        if not acquired:
            logger.warning("save_lesson(%s): lock timeout, skipping append", lesson.id)
            return ""
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return lesson.id


def _iter_lessons(target_dir: Path, strategy_type: str) -> list[ReflectionLesson]:
    """Read lessons from disk, silently skipping corrupted JSONL lines."""
    if strategy_type:
        stem = _sanitize_strategy_filename(strategy_type)
        files = [target_dir / f"{stem}.jsonl"]
    else:
        files = sorted(target_dir.glob("*.jsonl"))

    lessons: list[ReflectionLesson] = []
    for path in files:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    lessons.append(ReflectionLesson.from_dict(data))
            except (json.JSONDecodeError, TypeError):
                logger.debug("skipping corrupted JSONL line in %s", path)
    return lessons


def search_reflections(
    strategy_type: str = "",
    keywords: Optional[list[str]] = None,
    top_k: int = 5,
    base_dir: Optional[Path] = None,
) -> list[ReflectionLesson]:
    """Retrieve lessons via tag intersection + substring matching.

    Args:
        strategy_type: Restrict the search to a single strategy JSONL file;
            empty string scans all strategy files.
        keywords: Terms matched case-insensitively against tags (exact
            intersection) and against the lesson text / original decision
            (substring). When omitted, lessons are ranked by recency only.
        top_k: Maximum number of lessons returned.
        base_dir: Override storage directory (defaults to the user memory base).

    Returns:
        Up to ``top_k`` lessons sorted by (score desc, created_at desc).
        Empty list when the reflections feature flag is disabled.
    """
    if not _reflections_enabled():
        return []

    # Guard: a negative top_k slice would drop items from the end of the
    # result list instead of limiting it, so treat top_k <= 0 as "no results".
    if top_k <= 0:
        return []

    target_dir = Path(base_dir) if base_dir else default_reflections_dir()
    if not target_dir.exists():
        return []

    lessons = _iter_lessons(target_dir, strategy_type)
    terms = [k.strip().lower() for k in (keywords or []) if k.strip()]

    scored: list[tuple[float, ReflectionLesson]] = []
    for lesson in lessons:
        if not terms:
            scored.append((0.0, lesson))
            continue
        tag_set = {str(t).lower() for t in lesson.tags}
        # Tags are exact matches and weigh double vs. substring text hits.
        score = 2.0 * len(tag_set & set(terms))
        haystack = f"{lesson.lesson} {lesson.original_decision}".lower()
        score += sum(1.0 for term in terms if term in haystack)
        if score > 0:
            scored.append((score, lesson))

    # Order by score desc, then newest first among equal scores.
    scored.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
    return [lesson for _, lesson in scored[:top_k]]


def auto_reflect_from_run_dir(
    run_dir: "str | Path", base_dir: Optional[Path] = None
) -> Optional[str]:
    """Build and save a lesson from a backtest run directory.

    Reads ``run_card.json`` (written by agent/backtest/run_card.py), extracts
    scalar metrics and the backtest summary, and persists a lesson. Entirely
    best-effort: any failure returns None and logs a warning; when the
    reflections feature flag is disabled it returns None immediately.
    """
    if not _reflections_enabled():
        return None
    try:
        card_path = Path(run_dir) / "run_card.json"
        card = json.loads(card_path.read_text(encoding="utf-8"))

        metrics = card.get("metrics") or {}
        outcome = {
            key: metrics[key]
            for key in _RUN_CARD_METRIC_KEYS
            if isinstance(metrics.get(key), (int, float))
        }
        backtest = card.get("backtest") or {}
        # Run cards do not currently emit a strategy_type field; accept it
        # from the card top level or backtest summary when present.
        strategy_type = str(
            card.get("strategy_type") or backtest.get("strategy_type") or "unknown"
        )

        summary_bits = [f"{key}={value}" for key, value in sorted(outcome.items())]
        lesson_text = (
            f"Backtest outcome for strategy '{strategy_type}' "
            f"({backtest.get('engine', 'unknown')} engine): "
            + (", ".join(summary_bits) if summary_bits else "no scalar metrics")
            + "."
        )

        lesson = ReflectionLesson(
            strategy_type=strategy_type,
            original_decision=dict(backtest),
            outcome=outcome,
            lesson=lesson_text,
            tags=[strategy_type, "auto_reflect"],
        )
        lesson_id = save_lesson(lesson, base_dir=base_dir)
        return lesson_id or None
    except Exception as exc:  # noqa: BLE001 — reflection must never be fatal
        logger.warning("auto_reflect_from_run_dir(%s) failed: %s", run_dir, exc)
        return None
