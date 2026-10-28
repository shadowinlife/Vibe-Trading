"""Data model for scheduled research jobs.

A ``ScheduledResearchJob`` records everything needed to describe a deferred
research or backtest run: the prompt/query, when to run it, and an opaque
``config`` dict for future backtest parameters. Execution wiring is deferred
to a follow-up PR once the product shape is confirmed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from .verdict import VerdictRecord

# --- Schedule grammar, moved to src.scheduling.triggers (scheduler unification) ---
# Re-exported here so existing ``from src.scheduled_research.models import ...``
# keeps working (same convention as src/agent/loop.py's llm_usage re-export).
from src.scheduling.triggers import (
    CRON_BOUNDS as CRON_BOUNDS,
    is_interval_schedule as is_interval_schedule,
    parse_cron_field as parse_cron_field,
    validate_schedule as validate_schedule,
    validate_timezone as validate_timezone,
    validate_timezone_shape as validate_timezone_shape,
)
from src.scheduling.types import ExecutorKind, is_executor_kind

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------


class JobStatus(str, Enum):
    """Lifecycle status of a scheduled research job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class DeliveryStatus(str, Enum):
    """Outbox state for one job firing's briefing.

    The dispatch path returns once the agent attempt is *accepted*, not when it
    finishes, so "the run completed" and "the briefing was delivered" are two
    different facts and each needs its own record. PENDING means a run was
    dispatched and its result has not been delivered yet; it is the only state
    a sweep needs to look at.
    """

    NONE = "none"
    PENDING = "pending"
    #: Claimed by a sweep that is inside the send call. The claim is a lease,
    #: not a lock: a process that dies mid-send would otherwise strand the row
    #: forever, so a stale SENDING row becomes eligible again once its lease
    #: expires. Without the claim, a concurrent sweep reads PENDING while the
    #: first send is still in flight and delivers the same briefing twice.
    SENDING = "sending"
    ACCEPTED = "accepted"
    SENT = "sent"
    FAILED = "failed"


@dataclass
class DeliveryRecord:
    """What happened to the briefing produced by one firing.

    Attributes:
        status: Outbox state for this firing.
        session_id: The session whose terminal result is to be delivered.
        key: Idempotency key. A send is refused when a record with the same
            key is already ``SENT``, so a restarted poller, a retried sweep and
            an event arriving twice cannot produce a second message.
        error: Redaction-safe diagnostic from the last failed delivery.
        attempts: Failed send attempts for this firing. A channel outage is
            transient, so a failed send stays PENDING and is retried; only the
            attempt threshold, or a run that produced no briefing at all,
            makes the row terminal.
        updated_at: Epoch-millisecond timestamp of the last state change.
    """

    status: DeliveryStatus = DeliveryStatus.NONE
    session_id: Optional[str] = None
    key: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    updated_at: Optional[int] = None
    provider_message_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain JSON-serializable dict."""
        return {
            "status": self.status.value,
            "session_id": self.session_id,
            "key": self.key,
            "error": self.error,
            "attempts": self.attempts,
            "updated_at": self.updated_at,
            "provider_message_id": self.provider_message_id,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DeliveryRecord":
        """Reconstruct from a raw dict, treating absence as "never delivered".

        Args:
            data: A raw dict as produced by :meth:`to_dict`, or ``None`` for a
                record written before delivery existed.

        Returns:
            The reconstructed :class:`DeliveryRecord`.

        Raises:
            TypeError: If a present field has the wrong type.
            ValueError: If ``status`` is not a recognized value.
        """
        if not data:
            return cls()
        if not isinstance(data, dict):
            raise TypeError("'delivery' must be an object or null")
        raw_status = data.get("status", DeliveryStatus.NONE.value)
        try:
            status = DeliveryStatus(raw_status)
        except ValueError as exc:
            raise ValueError(f"unknown delivery status {raw_status!r}") from exc
        for name in ("session_id", "key", "error", "provider_message_id"):
            value = data.get(name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"'delivery.{name}' must be a string or null")
        updated_at = data.get("updated_at")
        if updated_at is not None and not isinstance(updated_at, int):
            raise TypeError("'delivery.updated_at' must be an integer (epoch ms) or null")
        attempts = data.get("attempts", 0)
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
            raise TypeError("'delivery.attempts' must be a non-negative integer")
        return cls(
            status=status,
            session_id=data.get("session_id"),
            key=data.get("key"),
            error=data.get("error"),
            attempts=attempts,
            updated_at=updated_at,
            provider_message_id=data.get("provider_message_id"),
        )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


def _verdict_record_or_none(data: Any, job_id: str) -> Optional[VerdictRecord]:
    """Parse a persisted last_verdict, degrading an unreadable one to None.

    A malformed verdict must never take the job down with it, the same way an
    unusable timezone degrades to UTC rather than quarantining the record.
    """
    if data is None:
        return None
    if not isinstance(data, dict):
        logger.warning(
            "scheduled research job %s drops a non-dict last_verdict %r", job_id, data
        )
        return None
    try:
        return VerdictRecord.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "scheduled research job %s drops an unreadable last_verdict: %r", job_id, exc
        )
        return None


def contains_pdf_password(value: Any) -> bool:
    """Return whether arbitrary job config contains the Email PDF secret key."""
    if isinstance(value, dict):
        return any(
            str(key).casefold() == "pdf_password" or contains_pdf_password(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_pdf_password(item) for item in value)
    return False


#: Accepted ``source_type`` values. ``"strategy"`` is coupled to
#: ``executor_kind="strategy"``: a strategy job names the pinned version it
#: runs, so neither half is meaningful without the other.
_SOURCE_TYPES: tuple[str, ...] = ("prompt", "playbook", "system", "strategy")

#: Accepted ``failure_kind`` values. ``"strategy"`` marks a strategy job whose
#: runner was unavailable at dispatch time.
_FAILURE_KINDS: tuple[str, ...] = ("dispatch", "schedule", "strategy")

#: The persisted in-flight handle of a running strategy job. ``pid`` is
#: deliberately absent: a process id is only meaningful inside the process
#: that started the run, so it lives in memory and never reaches the store.
_IN_FLIGHT_KEYS = frozenset({"run_id", "kind", "session_id", "run_dir", "started_at"})

_STRATEGY_RUN_KINDS = frozenset({"code", "workflow"})


def _default_run_policy() -> Dict[str, Any]:
    """Return a fresh default run policy for one job.

    ``overlap="skip"`` is the only accepted overlap policy: a financial run
    started twice concurrently is an incident, not a feature. The factory
    keeps two jobs from sharing one mutable dict.
    """
    return {"overlap": "skip", "retention": 20, "run_timeout_s": 3600, "primary": False}


@dataclass
class ScheduledResearchJob:
    """Mutable persisted state for a scheduled research / backtest job.

    Attributes:
        id: Unique job identifier (caller-supplied UUID or slug).
        prompt: Research prompt or backtest description.
        schedule: Interval-ms string or 5-field cron expression.
        next_run_at: Epoch-millisecond timestamp for the next intended
            execution. Defaults to the current time when the job is created.
        status: Current lifecycle status.
        created_at: Epoch-millisecond timestamp of job creation.
        last_run_at: Epoch-millisecond timestamp of the most recent executor
            attempt, or ``None`` when the job has not fired yet.
        consecutive_failures: Number of consecutive dispatch failures. A
            successful dispatch resets it to zero.
        last_error: Redaction-safe diagnostic from the latest failed attempt.
        failure_kind: ``"dispatch"`` for provider/session failures,
            ``"schedule"`` when the schedule cannot be advanced, or
            ``"strategy"`` when a strategy run could not be started.
        config: Opaque dict for future backtest parameters.
        timezone: IANA timezone key the cron schedule is evaluated in, or
            ``None`` for UTC (the semantics every job had before this field
            existed). Interval schedules ignore it.
        delivery_channel: Channel id a finished briefing is pushed to, or
            ``None`` to keep the job's results in the app only. Delivery is
            opt-in per job: absent this, nothing is ever sent anywhere.
        delivery_target: Address within that channel (chat / group / user id).
        delivery: Outbox state for the most recent firing. Separate from
            ``status`` because dispatch returns at enqueue: a job can be
            COMPLETED (accepted) while its briefing is still PENDING delivery.
        last_verdict: The latest run's parsed verdict record, or ``None`` when
            no completed run produced one yet. Embedded with its own
            ``previous`` so the list view renders a delta in one query.
        executor_kind: ``"prompt"`` dispatches ``prompt`` to the agent (every
            job before scheduler unification); ``"internal"`` calls the
            in-process callable registered under ``internal_callable``.
        internal_callable: Registry KEY name — never an import path — used to
            resolve the internal executor for ``executor_kind="internal"``
            jobs. ``None`` for prompt jobs. Internal/system jobs legitimately
            carry ``prompt=""``.
        strategy_id: Strategy the job runs, set iff
            ``executor_kind="strategy"``.
        strategy_version: Immutable strategy version the job is pinned to, set
            iff ``executor_kind="strategy"``. Editing the strategy later never
            changes what an already-scheduled job runs.
        run_policy: Execution policy for the job: ``overlap`` (only
            ``"skip"``), ``retention`` (run-history rows kept, 5-100),
            ``run_timeout_s`` (one run's wall-clock budget, 60-86400) and
            ``primary`` (the one active schedule per strategy).
        in_flight: Persisted handle of the strategy run currently in flight,
            or ``None``. Five keys exactly: ``run_id``, ``kind``
            (``"code"``/``"workflow"``), ``session_id``, ``run_dir`` and
            ``started_at`` (epoch ms).
    """

    id: str
    prompt: str
    schedule: str
    title: str = ""
    source_type: str = "prompt"
    playbook_slug: Optional[str] = None
    # Downgrade risk (documented, fail-closed): ``_SCHEMA_VERSION`` stays 1
    # while ``source_type="system"`` rows may exist, so code from BEFORE
    # scheduler unification rejects such a row in ``from_dict`` and the store
    # quarantines the whole file as ``.corrupt-<ts>`` (data preserved, not
    # loaded). Downgrading to a version before scheduler-unification requires
    # removing system job rows first.
    executor_kind: ExecutorKind = "prompt"
    internal_callable: Optional[str] = None
    end_at: Optional[int] = None
    next_run_at: int = field(default_factory=lambda: int(time.time() * 1000))
    status: JobStatus = JobStatus.PENDING
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    last_run_at: Optional[int] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    failure_kind: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)
    timezone: Optional[str] = None
    delivery_channel: Optional[str] = None
    delivery_target: Optional[str] = None
    delivery_target_ref: Optional[str] = None
    delivery_target_label: Optional[str] = None
    delivery_format: Optional[str] = None
    protect_pdf: bool = False
    delivery: DeliveryRecord = field(default_factory=DeliveryRecord)
    last_verdict: Optional[VerdictRecord] = None
    strategy_id: Optional[str] = None
    strategy_version: Optional[str] = None
    run_policy: Dict[str, Any] = field(default_factory=_default_run_policy)
    in_flight: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        """Reject a record that could never be loaded back from the store.

        A malformed row is not a local problem: ``store.load()`` quarantines
        the whole file when one record fails, so a directly constructed job
        has to fail here instead of taking every other job down with it.
        """
        self._validate_execution_contract()

    def _validate_execution_contract(self) -> None:
        """Enforce the closed source/executor/failure domains and run policy."""
        if self.source_type not in _SOURCE_TYPES:
            raise ValueError("'source_type' must be 'prompt', 'playbook', 'system', or 'strategy'")
        if not is_executor_kind(self.executor_kind):
            raise ValueError("'executor_kind' must be 'prompt', 'internal', or 'strategy'")
        if self.failure_kind is not None and self.failure_kind not in _FAILURE_KINDS:
            raise ValueError(
                "'failure_kind' must be 'dispatch', 'schedule', 'strategy', or null"
            )
        is_strategy = self.executor_kind == "strategy"
        if (self.source_type == "strategy") != is_strategy:
            raise ValueError(
                "'source_type' 'strategy' and 'executor_kind' 'strategy' must be set together"
            )
        if is_strategy:
            for name in ("strategy_id", "strategy_version"):
                value = getattr(self, name)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"'executor_kind' 'strategy' requires a non-empty '{name}'"
                    )
        else:
            for name in ("strategy_id", "strategy_version"):
                if getattr(self, name) is not None:
                    raise ValueError(f"'{name}' is only allowed on strategy jobs")
            if self.in_flight is not None:
                raise ValueError("'in_flight' is only allowed on strategy jobs")
        self._normalize_run_policy()
        if self.in_flight is not None:
            self._validate_in_flight()

    def _normalize_run_policy(self) -> None:
        """Fill omitted policy keys from the defaults and validate every one."""
        if not isinstance(self.run_policy, dict):
            raise ValueError("'run_policy' must be an object")
        policy = _default_run_policy()
        policy.update(self.run_policy)
        if policy["overlap"] != "skip":
            raise ValueError("'run_policy.overlap' must be 'skip'")
        retention = policy["retention"]
        if (
            isinstance(retention, bool)
            or not isinstance(retention, int)
            or not 5 <= retention <= 100
        ):
            raise ValueError("'run_policy.retention' must be an integer in [5, 100]")
        timeout = policy["run_timeout_s"]
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 60 <= timeout <= 86400
        ):
            raise ValueError("'run_policy.run_timeout_s' must be an integer in [60, 86400]")
        if not isinstance(policy["primary"], bool):
            raise ValueError("'run_policy.primary' must be a boolean")
        self.run_policy = policy

    def _validate_in_flight(self) -> None:
        """Require the persisted strategy handle's exact shape."""
        handle = self.in_flight
        if not isinstance(handle, dict) or set(handle) != _IN_FLIGHT_KEYS:
            raise ValueError(
                "'in_flight' must have exactly the keys 'run_id', 'kind', "
                "'session_id', 'run_dir', and 'started_at'"
            )
        if not isinstance(handle["run_id"], str):
            raise ValueError("'in_flight.run_id' must be a string")
        if not isinstance(handle["kind"], str) or handle["kind"] not in _STRATEGY_RUN_KINDS:
            raise ValueError("'in_flight.kind' must be 'code' or 'workflow'")
        for name in ("session_id", "run_dir"):
            if handle[name] is not None and not isinstance(handle[name], str):
                raise ValueError(f"'in_flight.{name}' must be a string or null")
        started_at = handle["started_at"]
        if isinstance(started_at, bool) or not isinstance(started_at, int):
            raise ValueError("'in_flight.started_at' must be an integer (epoch ms)")
        self.in_flight = dict(handle)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain JSON-serializable dict.

        Returns:
            A dict containing all job fields, with ``status`` as its string
            value.
        """
        if contains_pdf_password(self.config):
            raise ValueError("scheduled research config must not contain pdf_password")
        if not isinstance(self.protect_pdf, bool):
            raise TypeError("'protect_pdf' must be a boolean")
        if self.protect_pdf and (self.delivery_channel != "email" or self.delivery_format != "pdf"):
            raise ValueError("'protect_pdf' requires PDF email delivery")
        self._validate_execution_contract()
        return {
            "id": self.id,
            "prompt": self.prompt,
            "schedule": self.schedule,
            "title": self.title,
            "source_type": self.source_type,
            "playbook_slug": self.playbook_slug,
            "executor_kind": self.executor_kind,
            "internal_callable": self.internal_callable,
            "end_at": self.end_at,
            "next_run_at": self.next_run_at,
            "status": self.status.value,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "failure_kind": self.failure_kind,
            "config": self.config,
            "timezone": self.timezone,
            "delivery_channel": self.delivery_channel,
            "delivery_target": self.delivery_target,
            "delivery_target_ref": self.delivery_target_ref,
            "delivery_target_label": self.delivery_target_label,
            "delivery_format": self.delivery_format,
            "protect_pdf": self.protect_pdf,
            "delivery": self.delivery.to_dict(),
            "last_verdict": self.last_verdict.to_dict() if self.last_verdict else None,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "run_policy": self.run_policy,
            "in_flight": self.in_flight,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduledResearchJob":
        """Reconstruct a job from a plain dict.

        Args:
            data: A raw dict as produced by :meth:`to_dict`.

        Returns:
            The reconstructed ``ScheduledResearchJob``.

        Dicts written before scheduler unification lack ``executor_kind`` and
        ``internal_callable``; both default to the prompt executor, so old
        files load unchanged. ``strategy_*``, ``run_policy`` and ``in_flight``
        are likewise defaulted when absent, and the cross-field contract is
        re-checked by ``__post_init__`` on the reconstructed job.

        Raises:
            KeyError: If a required field is missing.
            TypeError: If a field has the wrong type.
            ValueError: If ``status`` is not a recognized ``JobStatus`` value,
                or ``source_type`` / ``executor_kind`` / ``failure_kind`` is
                outside its domain, or an ``internal`` job names no
                ``internal_callable``, or the strategy fields, ``run_policy``
                or ``in_flight`` violate their contract.
        """
        job_id = data["id"]
        prompt = data["prompt"]
        schedule = data["schedule"]
        if not isinstance(job_id, str) or not isinstance(prompt, str) or not isinstance(schedule, str):
            raise TypeError("'id', 'prompt', and 'schedule' must be strings")
        next_run_at = data["next_run_at"]
        created_at = data["created_at"]
        if not isinstance(next_run_at, int) or not isinstance(created_at, int):
            raise TypeError("'next_run_at' and 'created_at' must be integers (epoch ms)")
        last_run_at = data.get("last_run_at")
        if last_run_at is not None and not isinstance(last_run_at, int):
            raise TypeError("'last_run_at' must be an integer (epoch ms) or null")
        consecutive_failures = data.get("consecutive_failures", 0)
        if (
            isinstance(consecutive_failures, bool)
            or not isinstance(consecutive_failures, int)
            or consecutive_failures < 0
        ):
            raise TypeError("'consecutive_failures' must be a non-negative integer")
        last_error = data.get("last_error")
        failure_kind = data.get("failure_kind")
        if last_error is not None and not isinstance(last_error, str):
            raise TypeError("'last_error' must be a string or null")
        if failure_kind is not None and failure_kind not in _FAILURE_KINDS:
            raise ValueError(
                "'failure_kind' must be 'dispatch', 'schedule', 'strategy', or null"
            )
        # Never raises: the store quarantines the whole file when a single
        # record fails to load, so an unusable timezone value degrades that
        # one job to UTC — the semantics it had before the field existed —
        # instead of taking every other job down with it. Absent, blank, and
        # non-string values all normalize to None.
        raw_tz = data.get("timezone")
        tz = raw_tz if isinstance(raw_tz, str) and raw_tz.strip() else None
        if raw_tz is not None and tz is None:
            logger.warning(
                "scheduled research job %s has an unusable timezone %r; "
                "evaluating its schedule in UTC",
                job_id,
                raw_tz,
            )
        status = JobStatus(data["status"])
        title = data.get("title", "")
        source_type = data.get("source_type", "prompt")
        playbook_slug = data.get("playbook_slug")
        end_at = data.get("end_at")
        if not isinstance(title, str):
            raise TypeError("'title' must be a string")
        if source_type not in _SOURCE_TYPES:
            raise ValueError(
                "'source_type' must be 'prompt', 'playbook', 'system', or 'strategy'"
            )
        # Pre-unification dicts carry neither key; both default to the prompt
        # executor. Unknown or contradictory values are REJECTED rather than
        # degraded: a misspelled executor must not silently fall back to
        # dispatching the (possibly empty) prompt.
        executor_kind = data.get("executor_kind", "prompt")
        internal_callable = data.get("internal_callable")
        if not is_executor_kind(executor_kind):
            raise ValueError("'executor_kind' must be 'prompt', 'internal', or 'strategy'")
        if internal_callable is not None and not isinstance(internal_callable, str):
            raise TypeError("'internal_callable' must be a string or null")
        if executor_kind == "internal" and not internal_callable:
            raise ValueError(
                "'executor_kind' 'internal' requires 'internal_callable' "
                "(a registry key name, not an import path)"
            )
        if playbook_slug is not None and not isinstance(playbook_slug, str):
            raise TypeError("'playbook_slug' must be a string or null")
        strategy_id = data.get("strategy_id")
        strategy_version = data.get("strategy_version")
        for name, value in (
            ("strategy_id", strategy_id),
            ("strategy_version", strategy_version),
        ):
            if value is not None and not isinstance(value, str):
                raise TypeError(f"'{name}' must be a string or null")
        raw_run_policy = data.get("run_policy")
        if raw_run_policy is not None and not isinstance(raw_run_policy, dict):
            raise TypeError("'run_policy' must be an object")
        in_flight = data.get("in_flight")
        if in_flight is not None and not isinstance(in_flight, dict):
            raise TypeError("'in_flight' must be an object or null")
        if end_at is not None and (isinstance(end_at, bool) or not isinstance(end_at, int)):
            raise TypeError("'end_at' must be an integer (epoch ms) or null")
        raw_config = data.get("config")
        config: Dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
        if contains_pdf_password(config):
            raise ValueError("scheduled research config must not contain pdf_password")
        delivery_channel = data.get("delivery_channel")
        delivery_target = data.get("delivery_target")
        delivery_target_ref = data.get("delivery_target_ref")
        delivery_target_label = data.get("delivery_target_label")
        delivery_format = data.get("delivery_format")
        if delivery_format not in {None, "html", "pdf"}:
            raise ValueError("'delivery_format' must be 'html', 'pdf', or null")
        protect_pdf = data.get("protect_pdf", False)
        if not isinstance(protect_pdf, bool):
            raise TypeError("'protect_pdf' must be a boolean")
        if protect_pdf and (delivery_channel != "email" or delivery_format != "pdf"):
            raise ValueError("'protect_pdf' requires PDF email delivery")
        for name, value in (
            ("delivery_channel", delivery_channel),
            ("delivery_target", delivery_target),
            ("delivery_target_ref", delivery_target_ref),
            ("delivery_target_label", delivery_target_label),
        ):
            if value is not None and not isinstance(value, str):
                raise TypeError(f"'{name}' must be a string or null")
        return cls(
            id=job_id,
            prompt=prompt,
            schedule=schedule,
            title=title,
            source_type=source_type,
            playbook_slug=playbook_slug,
            executor_kind=executor_kind,
            internal_callable=internal_callable,
            end_at=end_at,
            next_run_at=next_run_at,
            status=status,
            created_at=created_at,
            last_run_at=last_run_at,
            consecutive_failures=consecutive_failures,
            last_error=last_error,
            failure_kind=failure_kind,
            config=config,
            timezone=tz,
            delivery_channel=delivery_channel,
            delivery_target=delivery_target,
            delivery_target_ref=delivery_target_ref,
            delivery_target_label=delivery_target_label,
            delivery_format=delivery_format,
            protect_pdf=protect_pdf,
            delivery=DeliveryRecord.from_dict(data.get("delivery")),
            last_verdict=_verdict_record_or_none(data.get("last_verdict"), job_id),
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            run_policy=raw_run_policy if raw_run_policy is not None else _default_run_policy(),
            in_flight=in_flight,
        )
