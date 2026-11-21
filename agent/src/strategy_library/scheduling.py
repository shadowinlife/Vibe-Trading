"""Attach, confirm and detach strategy schedules on the shared job kernel.

A code strategy attaches immediately against an immutable snapshot. A workflow
strategy is a two-step operation: :func:`attach` writes a proposal holding only
the SHA-256 of a one-time confirmation token, and :func:`commit` — called from
the human REST surface, never from an agent tool — consumes that token to
create the job. Proposals live in their own ``scheduled_research/
strategy_proposals/`` directory under the ``ssp_`` id namespace, invisible to
the legacy ``srp_`` reader. Every refusal raises :class:`ScheduleError`
carrying the HTTP status the route layer should answer with.

:func:`request_schedule` is retained for runner and model compatibility only.
It is **not** an agent-tool entry point: the assistant path reaches the RM
control plane through ``src.agent_schedules.tools``, and time-based triggering
belongs to RM alone. Do not re-wire it back into an agent tool.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore
from src.scheduling.triggers import next_due, validate_schedule, validate_timezone
from src.strategy_library import scheduling_io as proposal_io
from src.strategy_library import store as library_store
from src.strategy_library.history import delete_runs
from src.strategy_library.scheduling_errors import (
    ScheduleError,
    fail as _fail,
    not_ready as _not_ready,
    version_changed as _version_changed,
)
from src.strategy_library.scheduling_io import PROPOSAL_ID_RE, strategy_proposals_dir
from src.strategy_library.scheduling_jobs import (
    DISABLED_STATUSES,
    has_enabled_primary,
    now_ms as _now_ms,
    resolve_job_store,
)
from src.strategy_library.scheduling_validation import check_token, check_version
from src.strategy_versions import integration

__all__ = [
    "CONFIRM_TOKEN_RE",
    "PROPOSAL_ID_RE",
    "PROPOSAL_OPERATION",
    "PROPOSAL_TTL_MS",
    "AttachResult",
    "ScheduleError",
    "attach",
    "commit",
    "detach",
    "list_schedules",
    "request_schedule",
    "strategy_proposals_dir",
]

PROPOSAL_OPERATION = "strategy_schedule"
PROPOSAL_TTL_MS = 15 * 60 * 1000
CONFIRM_TOKEN_RE = re.compile(r"^scp_[0-9a-f]{32}$")

_COMMIT_LOCK = threading.Lock()


@dataclass(frozen=True)
class AttachResult:
    """Outcome of :func:`attach`: a job (code) or a pending proposal (workflow).

    ``confirm_token`` is the one-time secret for :func:`commit`; it is never persisted or exposed to an agent tool.
    """

    proposal_id: str | None
    confirm_token: str | None
    job_id: str | None
    strategy_version: str
    requires_confirmation: bool


def _primary_conflict(strategy_id: str) -> NoReturn:
    _fail(
        f"strategy {strategy_id!r} already has an enabled primary schedule",
        409,
        "primary_exists",
    )


def _pin(strategy_id: str, *, store: Any, runtime_root: Path | None) -> tuple[Any, str, str]:
    """Return ``(artifact, version, spec_sha256)`` pinned by a fresh snapshot."""
    artifact = library_store.get(strategy_id, store=store)
    if artifact is None:
        raise LookupError(f"strategy {strategy_id!r} does not exist")
    if artifact.readiness != "ready":
        _not_ready(strategy_id, artifact.readiness)
    digest = artifact.spec_sha256
    if not isinstance(digest, str) or not digest:
        _fail(f"strategy {strategy_id!r} has no spec digest; re-promote it first", 409, "edit_required")
    try:
        version = integration.snapshot_version(strategy_id, store=store, runtime_root=runtime_root)
    except library_store.EditRequiredError as exc:
        raise ScheduleError(str(exc), status=409, code="edit_required") from exc
    # An edit between the read above and the snapshot bumps version and digest
    # in one CAS, so a different version means the digest we hold is stale.
    if version != artifact.artifact_version:
        _fail(f"strategy {strategy_id!r} changed while attaching; retry", 409, "version_changed")
    return artifact, version, digest


def _new_job(
    *, artifact: Any, version: str, schedule: str, timezone: str | None,
    now: int, run_timeout_s: int | None, retention: int | None, primary: bool,
) -> ScheduledResearchJob:
    try:
        return ScheduledResearchJob(
            id=f"sr-{uuid.uuid4().hex[:12]}",
            title=artifact.name,
            prompt="",
            source_type="strategy",
            executor_kind="strategy",
            strategy_id=artifact.id,
            strategy_version=version,
            schedule=schedule,
            timezone=timezone,
            next_run_at=next_due(schedule, now, timezone),
            status=JobStatus.PENDING,
            created_at=now,
            config={},
            run_policy={
                "overlap": "skip",
                "retention": 20 if retention is None else retention,
                "run_timeout_s": 3600 if run_timeout_s is None else run_timeout_s,
                "primary": primary,
            },
        )
    except ValueError as exc:
        raise ScheduleError(str(exc), status=400, code="invalid_request") from exc


def attach(
    strategy_id: str,
    *,
    schedule: str,
    timezone: str | None,
    run_timeout_s: int | None = None,
    retention: int | None = None,
    primary: bool = False,
    store: Any = None,
    job_store: ScheduledResearchJobStore | None = None,
    runtime_root: Path | None = None,
) -> AttachResult:
    """Schedule a ready strategy, pinned to a fresh immutable version.

    A ``code`` strategy yields a job immediately; a ``workflow`` strategy yields
    a pending proposal plus a single-use ``confirm_token`` that only the human
    REST surface may return to its caller and replay into :func:`commit`.

    Raises:
        LookupError: When the strategy does not exist.
        ScheduleError: 409 for a non-ready strategy, a second primary schedule
            or a version race; 400 for an invalid schedule, timezone or policy.
    """
    try:
        validate_schedule(schedule)
        validate_timezone(timezone)
    except ValueError as exc:
        raise ScheduleError(str(exc), status=400, code="invalid_schedule") from exc
    artifact, version, digest = _pin(
        strategy_id, store=store, runtime_root=runtime_root
    )
    jobs = resolve_job_store(job_store, runtime_root)
    if primary and has_enabled_primary(jobs, artifact.id):
        _primary_conflict(artifact.id)
    job = _new_job(
        artifact=artifact,
        version=version,
        schedule=schedule,
        timezone=timezone,
        now=_now_ms(),
        run_timeout_s=run_timeout_s,
        retention=retention,
        primary=primary,
    )
    if artifact.kind == "code":
        jobs.upsert(job)
        return AttachResult(None, None, job.id, version, False)
    if artifact.kind != "workflow":
        _fail(f"strategy kind {artifact.kind!r} cannot be scheduled", 400, "invalid_request")
    proposal_id = f"ssp_{secrets.token_hex(16)}"
    token = f"scp_{secrets.token_hex(16)}"
    job_payload = job.to_dict()
    job_payload.pop("in_flight", None)
    proposal_io.write_proposal(
        proposal_io.proposal_path(proposal_id, runtime_root=runtime_root),
        {
            "proposal_id": proposal_id,
            "operation": PROPOSAL_OPERATION,
            "status": "pending",
            "created_at": job.created_at,
            "expires_at": job.created_at + PROPOSAL_TTL_MS,
            "strategy_id": artifact.id,
            "strategy_version": version,
            "spec_sha256": digest,
            "confirm_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "job": job_payload,
            "used_at": None,
        },
    )
    return AttachResult(proposal_id, token, None, version, True)


def request_schedule(
    strategy_id: str, schedule: str, timezone: str = "UTC", *,
    run_timeout_s: int | None = None, session_id: str | None = None,
    store: Any = None, job_store: ScheduledResearchJobStore | None = None,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    """Return the token-free attach result for the retired local tool bridge.

    A workflow strategy still needs a human to confirm the returned proposal
    through the REST commit route; an agent can never do it. ``session_id`` is
    accepted for tool-signature compatibility and intentionally unused.

    Not an agent-tool entry point: the assistant path is RM-only via
    ``src.agent_schedules.tools``, so do not re-wire this function there.
    """
    zone = timezone.strip() if isinstance(timezone, str) else timezone
    result = attach(
        strategy_id, schedule=schedule, timezone=zone or "UTC",
        run_timeout_s=run_timeout_s, store=store, job_store=job_store,
        runtime_root=runtime_root,
    )
    return {
        "proposal_id": result.proposal_id, "job_id": result.job_id,
        "strategy_version": result.strategy_version,
        "requires_confirmation": result.requires_confirmation,
    }


def commit(
    proposal_id: str,
    confirm_token: str,
    *,
    store: Any = None,
    job_store: ScheduledResearchJobStore | None = None,
    runtime_root: Path | None = None,
) -> ScheduledResearchJob:
    """Create the proposed job after proving the one-time token.

    The proposal stays pending on every refusal; a successful commit stamps
    ``used_at`` so the token cannot be replayed.

    Raises:
        ScheduleError: 404 unknown proposal; 403 missing, wrong or used token;
            409 expired proposal, changed version/digest, revoked readiness or
            a primary conflict.
    """
    if not isinstance(confirm_token, str) or not CONFIRM_TOKEN_RE.fullmatch(confirm_token):
        _fail("a valid confirmation token is required", 403, "invalid_token")
    with _COMMIT_LOCK:
        try:
            path = proposal_io.proposal_path(proposal_id, runtime_root=runtime_root)
            payload = proposal_io.read_proposal(path)
        except proposal_io.ProposalFileError as exc:
            raise ScheduleError(str(exc), status=404, code="proposal_not_found") from exc
        if payload.get("operation") != PROPOSAL_OPERATION:
            _fail(
                f"strategy proposal {proposal_id!r} is unknown",
                404,
                "proposal_not_found",
            )
        now = _now_ms()
        check_token(payload, confirm_token, now)
        strategy_id, version = check_version(payload, store)
        job_payload = payload.get("job")
        if not isinstance(job_payload, dict):
            _version_changed(strategy_id)
        try:
            job = ScheduledResearchJob.from_dict(job_payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise ScheduleError(
                "proposal job record is malformed; re-attach the strategy",
                status=409,
                code="version_changed",
            ) from exc
        if job.strategy_id != strategy_id or job.strategy_version != version:
            _version_changed(strategy_id)
        jobs = resolve_job_store(job_store, runtime_root)
        if job.run_policy.get("primary") is True and has_enabled_primary(jobs, strategy_id):
            _primary_conflict(strategy_id)
        jobs.upsert(job)
        payload["status"] = "committed"
        payload["used_at"] = now
        payload["committed_job_id"] = job.id
        proposal_io.write_proposal(path, payload)
        return job


def detach(
    job_id: str,
    *,
    job_store: ScheduledResearchJobStore | None = None,
    runtime_root: Path | None = None,
) -> bool:
    """Delete a schedule and cascade its run history.

    Returns:
        ``True`` when the job existed and was removed, ``False`` when it was
        already gone. Both paths delete any orphaned history file.
    """
    removed = resolve_job_store(job_store, runtime_root).delete(job_id)
    try:
        delete_runs(job_id, runtime_root=runtime_root)
    except ValueError:
        pass
    return removed


def list_schedules(
    *,
    strategy_id: str | None = None,
    job_store: ScheduledResearchJobStore | None = None,
    runtime_root: Path | None = None,
) -> list[ScheduledResearchJob]:
    """Return enabled strategy schedules, earliest ``next_run_at`` first."""
    jobs = resolve_job_store(job_store, runtime_root).load().values()
    selected = [
        job
        for job in jobs
        if job.executor_kind == "strategy"
        and job.status not in DISABLED_STATUSES
        and (strategy_id is None or job.strategy_id == strategy_id)
    ]
    return sorted(selected, key=lambda job: (job.next_run_at, job.id))
