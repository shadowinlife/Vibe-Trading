"""Proposal token and pinned-version checks used by strategy commit."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from src.strategy_library import store as library_store
from src.strategy_library.scheduling_errors import fail, not_ready, version_changed


def check_token(payload: dict[str, Any], confirm_token: str, now: int) -> None:
    """Refuse an expired, used or mismatching one-time confirmation token.

    Args:
        payload: Persisted scheduling proposal.
        confirm_token: Caller-supplied one-time confirmation token.
        now: Current epoch milliseconds.

    Raises:
        ScheduleError: When the proposal is expired, used or token-mismatched.
    """
    proposal_id = payload.get("proposal_id")
    if int(payload.get("expires_at") or 0) < now:
        fail(f"strategy proposal {proposal_id!r} has expired", 409, "expired")
    if payload.get("status") != "pending" or payload.get("used_at") is not None:
        fail(f"strategy proposal {proposal_id!r} was already used", 403, "token_used")
    expected = payload.get("confirm_token_sha256")
    presented = hashlib.sha256(confirm_token.encode("utf-8")).hexdigest()
    if not isinstance(expected, str) or not hmac.compare_digest(expected, presented):
        fail("confirmation token does not match", 403, "invalid_token")


def check_version(payload: dict[str, Any], store: Any) -> tuple[str, str]:
    """Re-read the strategy and return the ``(strategy_id, version)`` it pins.

    Args:
        payload: Persisted scheduling proposal.
        store: Strategy store used to re-read current readiness and identity.

    Returns:
        The still-current strategy identifier and pinned version.

    Raises:
        ScheduleError: When the proposal is malformed, stale or no longer ready.
    """
    strategy_id = payload.get("strategy_id")
    version = payload.get("strategy_version")
    digest = payload.get("spec_sha256")
    if not all(isinstance(value, str) and value for value in (strategy_id, version, digest)):
        fail(
            "proposal record is malformed; re-attach the strategy",
            409,
            "version_changed",
        )
    artifact = library_store.get(strategy_id, store=store)
    if artifact is None or (artifact.artifact_version != version or artifact.spec_sha256 != digest):
        version_changed(strategy_id)
    if artifact.readiness != "ready":
        not_ready(strategy_id, artifact.readiness)
    return strategy_id, version
