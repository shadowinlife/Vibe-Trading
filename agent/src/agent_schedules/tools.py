"""Six assistant tools that relay strategy-schedule intent to the RM control plane.

The RM control plane owns the schedule table, its due-time loop and the run
history. This module is the thin local half of that contract: it resolves the
strategy and its current version in the local library, validates the cadence,
and forwards the request over the RM agent API. It never writes
``scheduled_research`` and never starts a scheduler — the box has no second
schedule source, and a local fallback would be a schedule that never fires.

Every action returns a plain ``dict``. Failures are
``{"ok": False, "error": <code>, "detail": <bounded text>}`` and never raise
into the MCP wrapper; the RM error taxonomy (``unauthorized``,
``schedule_limit_reached``, ``not_found``, ``invalid_request``,
``rm_unreachable``, ``scheduling_unavailable``) comes from
:mod:`src.agent_schedules.client` unchanged. ``current_version``/``drift`` on a
list row are computed from the local library, so a schedule pinned to an older
version shows as stale until ``retarget_strategy_schedule`` re-pins it.

No token, Authorization header, local path or RM request body is ever placed in
a return value, a detail string or a log line.
"""

from __future__ import annotations

import functools
import json
import logging
import re
from collections.abc import Callable
from typing import Any, TypeVar, cast

from src.agent_schedules.client import AgentScheduleClient, ScheduleError, load_client
from src.config.paths import get_runtime_root
from src.scheduled_research.store import _STORE_FILENAME
from src.strategy_library import store as library_store
from src.strategy_library.paths import (
    version_code_path,
    version_config_path,
    version_dir,
    version_spec_path,
)
from src.strategy_store.models import Artifact

logger = logging.getLogger(__name__)

#: Fallback timezone when the caller does not name one.
DEFAULT_TIMEZONE = "Asia/Shanghai"

#: The only cadence shapes RM accepts.
SCHEDULE_KINDS: tuple[str, ...] = ("cron", "interval", "daily")

_CRON_FIELD_COUNT = 5
_TIME_OF_DAY_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_MAX_DETAIL_CHARS = 300
_BEARER_RE = re.compile(r"bearer\s+\S+", re.IGNORECASE)
_UNEXPECTED_DETAIL = "scheduling request failed"

#: Fixed detail text per error code, used when the client left it empty.
_FALLBACK_DETAILS: dict[str, str] = {
    "scheduling_unavailable": "scheduling is unavailable",
    "unauthorized": "the scheduling service rejected the credential",
    "strategy_not_found": "strategy not found in the local library",
    "strategy_not_ready": "strategy is not ready for scheduling",
    "schedule_limit_reached": "the schedule limit has been reached",
    "version_not_found": "strategy version is not available locally",
    "not_found": "schedule not found",
    "rm_unreachable": "the scheduling service is unreachable",
    "invalid_request": "the scheduling request was rejected",
}

#: Test seam: QA and unit tests swap this for a client bound to a fake
#: transport. The public API never takes a client, so production always reads
#: ``RM_AGENT_API_URL`` and the token file through :func:`load_client`.
_client_factory: Callable[[], AgentScheduleClient] = load_client

_F = TypeVar("_F", bound=Callable[..., dict[str, Any]])


class _ActionError(Exception):
    """A failure this layer decides on: validation or an absent local resource."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _bounded(raw: str) -> str:
    """Return *raw* NUL-free, Bearer-redacted, stripped and length-bounded."""
    cleaned = (raw or "").replace("\x00", "")
    cleaned = _BEARER_RE.sub("Bearer [redacted]", cleaned)
    return cleaned.strip()[:_MAX_DETAIL_CHARS]


def _error(code: str, detail: str = "") -> dict[str, Any]:
    """Build the frozen error envelope for *code*."""
    text = _bounded(detail) or _FALLBACK_DETAILS.get(code, _UNEXPECTED_DETAIL)
    return {"ok": False, "error": code, "detail": text}


def _action(func: _F) -> _F:
    """Map every failure to an error envelope; never raise to the caller."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return func(*args, **kwargs)
        except _ActionError as exc:
            return _error(exc.code, exc.detail)
        except ScheduleError as exc:
            return _error(exc.code, exc.detail)
        except Exception:  # noqa: BLE001 - an honest envelope beats a traceback
            # Deliberately no exception text: it can carry paths or request data.
            logger.warning("agent scheduling action %s failed", func.__name__)
            return _error("rm_unreachable", _UNEXPECTED_DETAIL)

    return cast(_F, wrapper)


def _client() -> AgentScheduleClient:
    """Return the configured RM client, or raise :class:`SchedulingUnavailable`."""
    return _client_factory()


def _require_text(value: Any, field: str) -> str:
    """Return *value* stripped when it is a non-blank string, else refuse it."""
    if not isinstance(value, str) or not value.strip():
        raise _ActionError("invalid_request", f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, field: str) -> str | None:
    """Return *value* stripped, ``None`` when omitted, else refuse a non-string."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _ActionError("invalid_request", f"{field} must be a string")
    return value.strip() or None


def _optional_string(value: Any) -> str | None:
    """Return *value* when it is a string, else ``None`` (RM body fields)."""
    return value if isinstance(value, str) else None


def _artifact_version(artifact: Artifact) -> str | None:
    """Return the artifact's current version when it is a non-blank string."""
    version = artifact.artifact_version
    return version if isinstance(version, str) and version else None


def _version_available(artifact: Artifact, version: str) -> bool:
    """Return whether *version* is fully materialised for this strategy.

    A version directory alone is not enough: a code version must carry its
    config and signal engine, and a workflow version its spec, or the schedule
    would pin bytes that cannot run. Unsafe version strings are treated as
    absent instead of raising out of the path helpers.
    """
    try:
        base = version_dir(artifact.id, version)
        if not base.is_dir():
            return False
        if artifact.kind == "code":
            return version_config_path(artifact.id, version).is_file() and version_code_path(
                artifact.id, version
            ).is_file()
        return version_spec_path(artifact.id, version).is_file()
    except (OSError, ValueError):
        return False


def _ready_artifact(strategy_id: str) -> Artifact:
    """Return the local strategy when it exists and is ready to be scheduled."""
    artifact = library_store.get(strategy_id)
    if artifact is None:
        raise _ActionError(
            "strategy_not_found", f"strategy {strategy_id!r} is not in the local library"
        )
    if artifact.readiness != "ready":
        raise _ActionError(
            "strategy_not_ready", f"strategy {strategy_id!r} is not ready for scheduling"
        )
    return artifact


def _validate_cadence(
    kind: str,
    cron_spec: Any,
    interval_s: Any,
    time_of_day: Any,
) -> dict[str, Any]:
    """Return the cadence fields RM accepts for *kind*.

    Only the relevant field is forwarded; a caller-supplied field for another
    cadence is ignored rather than rejected.
    """
    if kind == "cron":
        spec = cron_spec.strip() if isinstance(cron_spec, str) else ""
        if len(spec.split()) != _CRON_FIELD_COUNT:
            raise _ActionError(
                "invalid_request", "cron_spec must be a five-field cron expression"
            )
        return {"cron_spec": spec}
    if kind == "interval":
        # ``bool`` is an ``int`` subclass: True would otherwise mean one second.
        if isinstance(interval_s, bool) or not isinstance(interval_s, int):
            raise _ActionError("invalid_request", "interval_s must be a positive integer")
        if interval_s <= 0:
            raise _ActionError("invalid_request", "interval_s must be a positive integer")
        return {"interval_s": interval_s}
    value = time_of_day.strip() if isinstance(time_of_day, str) else ""
    if not _TIME_OF_DAY_RE.fullmatch(value):
        raise _ActionError(
            "invalid_request", "time_of_day must be HH:MM between 00:00 and 23:59"
        )
    return {"time_of_day": value}


def _legacy_job_count() -> int:
    """Count jobs in the legacy in-box store without opening or touching it.

    Read-only on purpose: the scheduled-research store quarantines a corrupt
    file when instantiated, and that would mutate a directory this feature is
    required to leave untouched. A missing or unparseable store falls back to
    the directory entry count so a non-empty legacy directory is never reported
    as zero.
    """
    path = get_runtime_root() / "scheduled_research" / _STORE_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _legacy_directory_entries(path.parent)
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    return len(jobs) if isinstance(jobs, list) else 0


def _legacy_directory_entries(directory: Path) -> int:
    """Return the number of entries in the legacy directory, never raising."""
    try:
        return sum(1 for _ in directory.iterdir())
    except OSError:
        return 0


def _current_version(artifact: Artifact | None) -> str | None:
    """Return the library's current version, or ``None`` when it is unknown."""
    return _artifact_version(artifact) if artifact is not None else None


@_action
def schedule_strategy(
    *,
    strategy_id: str,
    kind: str,
    strategy_version: str | None = None,
    name: str | None = None,
    cron_spec: str | None = None,
    interval_s: int | None = None,
    time_of_day: str | None = None,
    tz: str | None = None,
) -> dict[str, Any]:
    """Create one RM control-plane schedule for a ready local strategy.

    The strategy must exist in the local library with ``readiness == "ready"``,
    and the pinned version must already be materialised locally (a code version
    needs ``config.json`` and ``code/signal_engine.py``; a workflow version
    needs ``strategy.json``). Any missing piece fails before RM is called.

    Args:
        strategy_id: Library id of the strategy to schedule.
        kind: Cadence shape — ``cron``, ``interval`` or ``daily``.
        strategy_version: Optional version to pin; defaults to the library's
            current version.
        name: Optional human-readable schedule name; defaults to the strategy
            name.
        cron_spec: Five-field cron expression; required for ``kind="cron"``.
        interval_s: Positive interval in seconds; required for
            ``kind="interval"``.
        time_of_day: ``HH:MM`` wall-clock time; required for ``kind="daily"``.
        tz: IANA timezone; defaults to ``Asia/Shanghai``.

    Returns:
        ``{"ok": True, "schedule_id", "next_run_at", "pinned_version"}`` on a
        real RM create, otherwise the error envelope.
    """
    identifier = _require_text(strategy_id, "strategy_id")
    if not isinstance(kind, str) or kind not in SCHEDULE_KINDS:
        raise _ActionError("invalid_request", "kind must be cron, interval or daily")
    artifact = _ready_artifact(identifier)

    requested = _optional_text(strategy_version, "strategy_version")
    version = requested or _artifact_version(artifact)
    if not version:
        raise _ActionError(
            "version_not_found", f"strategy {identifier!r} has no version to pin"
        )
    if not _version_available(artifact, version):
        raise _ActionError(
            "version_not_found", f"strategy version {version!r} is not available locally"
        )

    payload: dict[str, Any] = {
        "strategy_id": artifact.id,
        "strategy_version": version,
        "name": _optional_text(name, "name") or artifact.name,
        "kind": kind,
        "tz": _optional_text(tz, "tz") or DEFAULT_TIMEZONE,
        **_validate_cadence(kind, cron_spec, interval_s, time_of_day),
    }
    body = _client().create(payload)
    schedule_id = _optional_string(body.get("schedule_id"))
    if not schedule_id or not schedule_id.strip():
        # A 2xx without an id is not a schedule: never report one as created.
        raise _ActionError(
            "rm_unreachable", "the scheduling service returned no schedule id"
        )
    return {
        "ok": True,
        "schedule_id": schedule_id,
        "next_run_at": _optional_string(body.get("next_run_at")),
        "pinned_version": _optional_string(body.get("pinned_version")) or version,
    }


@_action
def list_strategy_schedules() -> dict[str, Any]:
    """List RM schedules with local version-drift annotations.

    Each RM row is returned unchanged plus ``current_version`` (the local
    library's version, ``None`` when the strategy is absent here), ``drift``
    (``pinned_version != current_version`` when both are known) and ``last_run``
    (RM's latest run summary, or ``None`` when RM does not return one).
    ``legacy_jobs_ignored`` counts jobs in the deprecated in-box store so the
    assistant can tell the user a schedule predating the control plane never
    fired; that file is only read, never modified.

    Returns:
        ``{"ok": True, "schedules": [...], "legacy_jobs_ignored": N}`` or the
        error envelope when RM is unreachable.
    """
    schedules: list[dict[str, Any]] = []
    for row in _client().list():
        entry = dict(row)
        strategy_id = _row_strategy_id(entry)
        current = (
            _current_version(library_store.get(strategy_id))
            if strategy_id is not None
            else None
        )
        pinned = _optional_string(entry.get("pinned_version"))
        entry["current_version"] = current
        entry["drift"] = (
            pinned != current if pinned is not None and current is not None else None
        )
        entry["last_run"] = entry.get("last_run")
        schedules.append(entry)
    return {
        "ok": True,
        "schedules": schedules,
        "legacy_jobs_ignored": _legacy_job_count(),
    }


def _row_strategy_id(entry: dict[str, Any]) -> str | None:
    """Return an RM row's ``strategy_id`` when it is a non-blank string."""
    value = entry.get("strategy_id")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


@_action
def pause_strategy_schedule(schedule_id: str) -> dict[str, Any]:
    """Pause one RM schedule without deleting its run history.

    Args:
        schedule_id: RM schedule id returned by ``schedule_strategy``.

    Returns:
        ``{"ok": True, "schedule_id", "enabled": False, "next_run_at"}`` or the
        error envelope.
    """
    identifier = _require_text(schedule_id, "schedule_id")
    body = _client().pause(identifier)
    return {
        "ok": True,
        "schedule_id": identifier,
        "enabled": False,
        "next_run_at": _optional_string(body.get("next_run_at")),
    }


@_action
def resume_strategy_schedule(schedule_id: str) -> dict[str, Any]:
    """Re-enable one paused RM schedule.

    Args:
        schedule_id: RM schedule id returned by ``schedule_strategy``.

    Returns:
        ``{"ok": True, "schedule_id", "enabled": True, "next_run_at"}`` or the
        error envelope.
    """
    identifier = _require_text(schedule_id, "schedule_id")
    body = _client().resume(identifier)
    return {
        "ok": True,
        "schedule_id": identifier,
        "enabled": True,
        "next_run_at": _optional_string(body.get("next_run_at")),
    }


@_action
def delete_strategy_schedule(schedule_id: str) -> dict[str, Any]:
    """Delete one RM schedule; its history stays with RM.

    Args:
        schedule_id: RM schedule id returned by ``schedule_strategy``.

    Returns:
        ``{"ok": True, "schedule_id", "deleted": True}`` or the error envelope.
    """
    identifier = _require_text(schedule_id, "schedule_id")
    _client().delete(identifier)
    return {"ok": True, "schedule_id": identifier, "deleted": True}


@_action
def retarget_strategy_schedule(
    schedule_id: str, strategy_version: str | None = None
) -> dict[str, Any]:
    """Re-pin one RM schedule to a version, keeping the schedule id and history.

    The local strategy and target version are resolved first: the target is the
    requested version or the library's current one, and it must already be
    materialised locally. Nothing is sent to RM when that check fails.

    Args:
        schedule_id: RM schedule id returned by ``schedule_strategy``.
        strategy_version: Optional version to pin; defaults to the library's
            current version.

    Returns:
        ``{"ok": True, "schedule_id", "pinned_version"}`` or the error envelope.
    """
    identifier = _require_text(schedule_id, "schedule_id")
    client = _client()
    row = client.get(identifier)
    strategy_id = _row_strategy_id(row)
    if strategy_id is None:
        raise _ActionError(
            "rm_unreachable", "the scheduling service returned no strategy id"
        )
    artifact = library_store.get(strategy_id)
    if artifact is None:
        raise _ActionError(
            "strategy_not_found", f"strategy {strategy_id!r} is not in the local library"
        )
    version = _optional_text(strategy_version, "strategy_version") or _artifact_version(
        artifact
    )
    if not version:
        raise _ActionError(
            "version_not_found", f"strategy {strategy_id!r} has no version to pin"
        )
    if not _version_available(artifact, version):
        raise _ActionError(
            "version_not_found", f"strategy version {version!r} is not available locally"
        )
    client.retarget(identifier, version)
    return {"ok": True, "schedule_id": identifier, "pinned_version": version}


__all__ = [
    "SCHEDULE_KINDS",
    "delete_strategy_schedule",
    "list_strategy_schedules",
    "pause_strategy_schedule",
    "resume_strategy_schedule",
    "retarget_strategy_schedule",
    "schedule_strategy",
]
