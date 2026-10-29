"""MCP adapter for the persistent memory lifecycle (T4 iteration).

Thin delegation layer between MCP tool wrappers (agent/mcp_server.py) and the
verified memory APIs: ``PersistentMemory`` (storage / retrieval),
``MemoryLifecycle`` (reinforcement / GC / access tracking) and
``src.memory.reflections`` (lesson store). The adapter contains ZERO business
logic — it only translates arguments and wraps results into uniform dict
envelopes ``{"status": "ok" | "error" | "skipped", ...}``.

Contract: adapter methods NEVER raise. Every unexpected exception is caught
and reported as ``{"status": "error", "error": str(exc)}`` so an MCP client
can always parse the response.

Feature gating: MCP exposure of these tools is controlled by
``VT_MEMORY_MCP_TOOLS`` at registration time in mcp_server.py. The adapter
itself stays functional when instantiated directly; per-feature flags
(quality scoring, reflections) are still honored by the underlying APIs.
"""

from __future__ import annotations

from typing import Any

# Maximum characters of an entry body returned by memory_recall.
_SNIPPET_MAX_CHARS = 500


class MemoryMCPAdapter:
    """Pure delegation facade over PersistentMemory + MemoryLifecycle."""

    def __init__(self, memory=None, lifecycle=None) -> None:
        """Wire the adapter to memory backends.

        Args:
            memory: Optional ``PersistentMemory`` instance; a standard one
                (default user memory dir) is constructed when omitted.
            lifecycle: Optional ``MemoryLifecycle``; built over ``memory``
                when omitted.
        """
        from src.memory.lifecycle import MemoryLifecycle
        from src.memory.persistent import PersistentMemory

        self._memory = memory if memory is not None else PersistentMemory()
        self._lifecycle = (
            lifecycle if lifecycle is not None else MemoryLifecycle(self._memory)
        )

    # ------------------------------------------------------------------
    # Save / recall
    # ------------------------------------------------------------------

    def memory_save(
        self,
        name: str,
        description: str,
        content: str,
        memory_type: str = "project",
    ) -> dict[str, Any]:
        """Persist a memory entry via ``PersistentMemory.add``."""
        try:
            path = self._memory.add(name, content, memory_type, description)
            if path is None:
                # add() returns None only for the quality-gated dedup window.
                return {"status": "skipped", "reason": "duplicate"}
            return {"status": "ok", "saved": path.name}
        except Exception as exc:  # noqa: BLE001 — adapter must never raise
            return {"status": "error", "error": str(exc)}

    def memory_recall(
        self, query: str, top_k: int = 5, type_filter: str = ""
    ) -> dict[str, Any]:
        """Retrieve relevant entries via ``PersistentMemory.find_relevant``."""
        try:
            entries = self._memory.find_relevant(query, max_results=top_k)
            if type_filter:
                entries = [e for e in entries if e.memory_type == type_filter]
            results = []
            for entry in entries:
                # Access tracking is best-effort and must not fail a recall.
                try:
                    self._lifecycle.track_access(entry)
                except Exception:  # noqa: BLE001
                    pass
                results.append(
                    {
                        "title": entry.title,
                        "type": entry.memory_type,
                        "snippet": entry.body[:_SNIPPET_MAX_CHARS],
                        "quality_score": entry.quality_score,
                        "importance": entry.importance,
                    }
                )
            return {"status": "ok", "results": results}
        except Exception as exc:  # noqa: BLE001 — adapter must never raise
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Reinforcement
    # ------------------------------------------------------------------

    def memory_reinforce(
        self, name: str, event: str, source: str = "system"
    ) -> dict[str, Any]:
        """Apply a quality-score event via ``MemoryLifecycle.reinforce``."""
        try:
            valid_events = sorted(self._lifecycle._EVENT_DELTAS)
            if event not in valid_events:
                return {
                    "status": "error",
                    "error": f"unknown event '{event}'",
                    "valid_events": valid_events,
                }
            if self._lifecycle.reinforce(name, event, source):
                return {"status": "ok", "name": name, "event": event}
            # False covers: quality flag off, session cap, entry not found.
            return {"status": "skipped", "reason": "not reinforced"}
        except Exception as exc:  # noqa: BLE001 — adapter must never raise
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Reflections
    # ------------------------------------------------------------------

    def memory_reflect(
        self,
        strategy_type: str,
        outcome: dict,
        original_params: dict,
    ) -> dict[str, Any]:
        """Store a reflection lesson via ``src.memory.reflections``."""
        try:
            from src.memory.reflections import ReflectionLesson, save_lesson

            lesson = ReflectionLesson(
                strategy_type=strategy_type,
                original_decision=dict(original_params or {}),
                outcome=dict(outcome or {}),
                lesson=f"Reflection for strategy '{strategy_type}'.",
                tags=[strategy_type] if strategy_type else [],
            )
            lesson_id = save_lesson(lesson)
            if lesson_id is None:
                # Feature flag is disabled — permanent, not worth retrying.
                return {
                    "status": "skipped",
                    "reason": (
                        "reflection not saved — enable VT_MEMORY_REFLECTIONS "
                        "(or VT_MEMORY=full) to store lessons"
                    ),
                }
            if not lesson_id:
                # Empty string: lock timeout (transient — the caller may
                # retry later).
                return {
                    "status": "skipped",
                    "reason": "lock timeout — reflection store is busy, retry later",
                }
            return {"status": "ok", "lesson_id": lesson_id}
        except Exception as exc:  # noqa: BLE001 — adapter must never raise
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def memory_status(self) -> dict[str, Any]:
        """Summarize the memory store; each stat is guarded independently."""
        try:
            status: dict[str, Any] = {"status": "ok"}
            try:
                entries = self._memory.list_entries()
            except Exception:  # noqa: BLE001
                entries = []
            status["entry_count"] = len(entries)
            try:
                status["avg_quality"] = (
                    round(sum(e.quality_score for e in entries) / len(entries), 4)
                    if entries
                    else 0.0
                )
            except Exception:  # noqa: BLE001
                status["avg_quality"] = 0.0
            try:
                status["avg_importance"] = (
                    round(sum(e.importance for e in entries) / len(entries), 4)
                    if entries
                    else 0.0
                )
            except Exception:  # noqa: BLE001
                status["avg_importance"] = 0.0
            try:
                status["gc_pending"] = len(self._lifecycle.run_gc(dry_run=True))
            except Exception:  # noqa: BLE001
                status["gc_pending"] = 0
            return status
        except Exception as exc:  # noqa: BLE001 — adapter must never raise
            return {"status": "error", "error": str(exc)}
