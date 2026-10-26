"""Strategy-library agent tools: promote, evaluate, list and schedule.

The three writers declare ``is_readonly = False`` — the MCP read-only mirror
refuses such a class, so the MCP server wraps them explicitly. Scheduling is
delegated to the RM control plane and never touches the local job store. No
``execute`` raises.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

from src.agent.tools import BaseTool

logger = logging.getLogger(__name__)

_MAX_PAGE = 200

#: Asking for this many rows returns the whole matched window, which is how
#: ``total`` is measured without a second scan or a store change.
_FULL_WINDOW = 1_000_000

def _ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False, default=str)


def _error(message: str, *, code: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"ok": False, "error": message, "code": code}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, default=str)


def _text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string, got {type(value).__name__}")
    return value.strip() or None


def _integer(value: Any, name: str, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, got a boolean")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def _boolean(value: Any, name: str, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = value.strip().lower() if isinstance(value, str) else ""
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no", ""}:
        return False
    raise ValueError(f"{name} must be a boolean, got {value!r}")


def _spec_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    return dict(value) if isinstance(value, Mapping) else None


def _summary(artifact: Any) -> dict[str, Any]:
    """Project an artifact row onto the library's public list shape."""
    return {
        "strategy_id": artifact.id, "name": artifact.name, "universe": artifact.universe,
        "kind": artifact.kind, "readiness": artifact.readiness, "version": artifact.artifact_version,
        "demo": artifact.demo, "created_at": artifact.created_at, "updated_at": artifact.updated_at,
    }


class PromoteStrategyTool(BaseTool):
    """Register a strategy draft and auto-evaluate a code strategy's run."""

    name = "promote_strategy"
    description = (
        "Register a strategy draft in the strategy library. A code strategy "
        "requires its source backtest run_dir and is evaluated immediately; a "
        "workflow strategy stays needs_work for the independent review. "
        "Returns the strategy id, readiness and deterministic gaps. WRITE tool."
    )
    parameters = {
        "type": "object",
        "properties": {
            "spec": {
                "type": "object",
                "description": "Strategy spec: name, kind, thesis, universe, inputs, steps, output_contract, failure_handling.",
            },
            "run_dir": {"type": "string", "description": "Source backtest run directory; required for kind=code."},
            "demo": {"type": "boolean", "default": False},
        },
        "required": ["spec"],
    }
    repeatable = False
    is_readonly = False

    def __init__(self, default_session_id: str | None = None, **_kwargs: Any) -> None:
        self._default_session_id = default_session_id

    def execute(self, **kwargs: Any) -> str:
        """Promote the draft and return the registered row's summary."""
        spec = _spec_object(kwargs.get("spec"))
        if spec is None:
            return _error("spec must be a strategy spec object", code="invalid_spec")
        try:
            run_dir = _text(kwargs.get("run_dir"), "run_dir")
            demo = _boolean(kwargs.get("demo"), "demo")
        except ValueError as exc:
            return _error(str(exc), code="invalid_argument")
        session_id = str(kwargs.get("session_id") or self._default_session_id or "").strip()
        try:
            from src.strategy_library.promote import promote

            result = promote(spec, session_id=session_id, run_dir=run_dir, demo=demo)
        except Exception as exc:  # noqa: BLE001 - the envelope is the contract
            logger.exception("promote_strategy failed")
            name = type(exc).__name__
            if name == "StaleVerdictError":
                return _error("the strategy changed while it was evaluated; re-evaluate it", code="stale_verdict")
            return _error(str(exc) or name, code=name)
        gaps = [asdict(gap) if is_dataclass(gap) else dict(gap) for gap in result.gaps]
        if result.strategy_id is None:
            first = gaps[0] if gaps else {}
            return _error(str(first.get("message") or "promotion failed"),
                          code=str(first.get("code") or "promotion_failed"), gaps=gaps)
        return _ok({
            "strategy": _summary(result.artifact) if result.artifact else None,
            "strategy_id": result.strategy_id,
            "readiness": result.readiness,
            "gaps": gaps,
            "verdict": result.verdict,
            "session_id": result.session_id,
        })


class EvaluateStrategyTool(BaseTool):
    """Re-run the gate that matches a strategy's kind."""

    name = "evaluate_strategy"
    description = (
        "Evaluate one strategy and persist the version-bound verdict. Code "
        "strategies use the deterministic backtest gate; workflow strategies "
        "use the independent rubric review. Returns passed, failure reasons "
        "and the resulting readiness. WRITE tool."
    )
    parameters = {
        "type": "object",
        "properties": {"strategy_id": {"type": "string", "description": "Library id to evaluate."}},
        "required": ["strategy_id"],
    }
    repeatable = True
    is_readonly = False

    def execute(self, **kwargs: Any) -> str:
        """Evaluate the strategy and return passed/reasons/readiness."""
        try:
            strategy_id = _text(kwargs.get("strategy_id"), "strategy_id")
        except ValueError as exc:
            return _error(str(exc), code="invalid_argument")
        if not strategy_id:
            return _error("strategy_id is required", code="invalid_argument")
        try:
            from src.strategy_library.store import get as get_strategy

            artifact = get_strategy(strategy_id)
            if artifact is None:
                return _error(f"strategy {strategy_id!r} does not exist", code="not_found")
            return _ok(_evaluate(artifact))
        except Exception as exc:  # noqa: BLE001 - the envelope is the contract
            logger.exception("evaluate_strategy failed for %s", strategy_id)
            return _error(str(exc) or type(exc).__name__, code=type(exc).__name__)


def _evaluate(artifact: Any) -> dict[str, Any]:
    """Dispatch on the artifact kind and normalize both verdict shapes."""
    if artifact.kind == "workflow":
        from src.strategy_library.review import evaluate_workflow

        document = evaluate_workflow(artifact.id)
        passed = document.get("verdict") == "pass"
        reasons = [
            str(row.get("code") or row.get("message")) for row in document.get("gaps") or ()
            if isinstance(row, Mapping) and (row.get("code") or row.get("message"))
        ]
        if not passed and not reasons:
            reasons = [str(document.get("verdict") or "workflow_review_failed")]
    else:
        from src.strategy_library.gate_code import evaluate_code

        verdict = evaluate_code(artifact.id)
        document, passed, reasons = verdict.payload(), bool(verdict.passed), list(verdict.reasons)

    from src.strategy_library.store import get as get_strategy

    refreshed = get_strategy(artifact.id)
    return {
        "strategy_id": artifact.id, "kind": artifact.kind, "passed": passed,
        "reasons": reasons, "readiness": refreshed.readiness if refreshed is not None else None,
        "verdict": document,
    }


class ListStrategyLibraryTool(BaseTool):
    """List library strategies with filters and pagination."""

    name = "list_strategy_library"
    description = (
        "List strategies in the library, newest first, with pagination. Filter "
        "by kind, readiness, a case-insensitive name substring, or demo "
        "inclusion. Read-only."
    )
    parameters = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["code", "workflow"]},
            "readiness": {"type": "string", "enum": ["draft", "needs_work", "reviewing", "ready", "rejected"]},
            "q": {"type": "string", "description": "Case-insensitive name substring."},
            "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": _MAX_PAGE},
            "offset": {"type": "integer", "default": 0, "minimum": 0},
            "include_demo": {"type": "boolean", "default": True},
        },
    }
    repeatable = True
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        """Return one page plus the filtered total."""
        try:
            kind = _text(kwargs.get("kind"), "kind")
            readiness = _text(kwargs.get("readiness"), "readiness")
            query = _text(kwargs.get("q"), "q")
            limit = _integer(kwargs.get("limit"), "limit", 50)
            offset = _integer(kwargs.get("offset"), "offset", 0)
            demo = _boolean(kwargs.get("include_demo"), "include_demo", True)
        except ValueError as exc:
            return _error(str(exc), code="invalid_argument")
        assert limit is not None and offset is not None
        if not 1 <= limit <= _MAX_PAGE:
            return _error(f"limit must be between 1 and {_MAX_PAGE}", code="invalid_argument")
        if offset < 0:
            return _error("offset must be >= 0", code="invalid_argument")
        try:
            from src.strategy_library.store import list_strategies

            matched = list_strategies(kind=kind, readiness=readiness, q=query,
                                      limit=_FULL_WINDOW, offset=0, include_demo=demo)
        except ValueError as exc:
            return _error(str(exc), code="invalid_argument")
        except Exception as exc:  # noqa: BLE001 - never echo internal paths
            logger.exception("list_strategy_library failed")
            return _error(
                "list_strategy_library failed internally; see server logs",
                code=type(exc).__name__,
            )
        return _ok({
            "items": [_summary(row) for row in matched[offset : offset + limit]],
            "total": len(matched),
            "limit": limit,
            "offset": offset,
        })


class ScheduleStrategyTool(BaseTool):
    """Create a strategy schedule in the RM control plane; never write locally."""

    name = "schedule_strategy"
    description = (
        "Create a schedule for a ready strategy through the RM control plane. "
        "Kind is cron, interval or daily; returns the RM schedule id, the next "
        "run time and the pinned strategy version. Fails closed with "
        "scheduling_unavailable when RM is not configured, and never writes "
        "the local job store. WRITE tool: places no order."
    )
    parameters = {
        "type": "object",
        "properties": {
            "strategy_id": {"type": "string", "description": "Library id to schedule."},
            "kind": {"type": "string", "enum": ["cron", "interval", "daily"]},
            "strategy_version": {
                "type": "string",
                "description": "Version to pin; defaults to the library's current version.",
            },
            "name": {"type": "string", "description": "Human-readable schedule name."},
            "cron_spec": {
                "type": "string",
                "description": "Five-field cron expression; required for kind=cron.",
            },
            "interval_s": {
                "type": "integer",
                "minimum": 1,
                "description": "Interval in seconds; required for kind=interval.",
            },
            "time_of_day": {
                "type": "string",
                "description": "HH:MM wall-clock time; required for kind=daily.",
            },
            "tz": {"type": "string", "description": "IANA timezone name."},
        },
        "required": ["strategy_id", "kind"],
    }
    repeatable = False
    is_readonly = False

    def __init__(self, **_kwargs: Any) -> None:
        """Accept host-injected construction kwargs; this tool needs none of them."""

    def execute(self, **kwargs: Any) -> str:
        """Create the schedule through ``agent_schedules.tools`` and return its envelope."""
        try:
            strategy_id = _text(kwargs.get("strategy_id"), "strategy_id")
            kind = _text(kwargs.get("kind"), "kind")
            version = _text(kwargs.get("strategy_version"), "strategy_version")
            name = _text(kwargs.get("name"), "name")
            cron_spec = _text(kwargs.get("cron_spec"), "cron_spec")
            time_of_day = _text(kwargs.get("time_of_day"), "time_of_day")
            tz = _text(kwargs.get("tz"), "tz")
            interval_s = _integer(kwargs.get("interval_s"), "interval_s")
        except ValueError as exc:
            return _error(str(exc), code="invalid_argument")
        if not strategy_id:
            return _error("strategy_id is required", code="invalid_argument")
        if not kind:
            return _error("kind is required", code="invalid_argument")
        try:
            from src.agent_schedules.tools import schedule_strategy
        except ImportError:
            logger.warning("schedule_strategy is unavailable: agent scheduling tools are not installed")
            return _error("scheduling is unavailable", code="scheduling_unavailable")
        try:
            result = schedule_strategy(
                strategy_id=strategy_id,
                kind=kind,
                strategy_version=version,
                name=name,
                cron_spec=cron_spec,
                interval_s=interval_s,
                time_of_day=time_of_day,
                tz=tz,
            )
        except Exception:  # noqa: BLE001 - the envelope is the contract; RM details stay out
            logger.exception("schedule_strategy failed for %s", strategy_id)
            return _error("scheduling call failed", code="rm_unreachable")
        return json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
