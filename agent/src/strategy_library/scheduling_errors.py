"""Typed refusals shared by strategy scheduling modules."""

from __future__ import annotations

from typing import Any, NoReturn


class ScheduleError(RuntimeError):
    """A refused schedule mutation; ``status`` is the HTTP code to answer with."""

    def __init__(self, message: str, *, status: int = 409, code: str = "schedule_error"):
        super().__init__(message)
        self.status = status
        self.code = code


def fail(message: str, status: int, code: str) -> NoReturn:
    """Raise a :class:`ScheduleError` with the given HTTP mapping."""
    raise ScheduleError(message, status=status, code=code)


def not_ready(strategy_id: str, readiness: Any) -> NoReturn:
    """Refuse a strategy whose readiness is not ``ready``."""
    fail(
        f"strategy {strategy_id!r} is not ready to schedule " f"(readiness={readiness!r})",
        409,
        "not_ready",
    )


def version_changed(strategy_id: str) -> NoReturn:
    """Refuse a proposal whose pinned version no longer matches."""
    fail(
        f"strategy {strategy_id!r} changed since the proposal was created; re-attach it",
        409,
        "version_changed",
    )
