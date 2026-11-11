"""Validation for persisted factor prototype result and progress documents."""

from __future__ import annotations

from dataclasses import dataclass

from src.factors.registry_freeze import TOOL_SCHEMA_VERSION
from src.strategy_runs.control_contract import CONTRACT_VERSION, JsonValue


@dataclass(frozen=True, slots=True)
class PrototypeResultIdentity:
    remote_id: str
    dispatch_key: str
    registry_digest: str


def _candidate_statuses(payload: JsonValue) -> list[str] | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        return None
    statuses = []
    required = {"candidate_id", "status", "reasons", "metrics", "artifacts"}
    for row in payload["candidates"]:
        if not isinstance(row, dict) or not required.issubset(row):
            return None
        if (
            not isinstance(row["candidate_id"], str)
            or row["status"] not in {"completed", "skipped", "insufficient_data"}
            or not isinstance(row["reasons"], list)
            or not isinstance(row["metrics"], dict)
            or not isinstance(row["artifacts"], dict)
        ):
            return None
        statuses.append(row["status"])
    return statuses


def valid_prototype_result(
    payload: JsonValue, identity: PrototypeResultIdentity
) -> bool:
    """Return whether a worker result is complete and internally consistent.

    Args:
        payload: Bounded JSON read from ``run_dir/result.json``.
        identity: Remote, dispatch, and registry identities from the dispatch row.

    Returns:
        ``True`` only for a complete 0013 result with matching identity/version.
    """
    if not isinstance(payload, dict):
        return False
    required = {
        "remote_id",
        "dispatch_key",
        "registry_digest",
        "tool_schema_version",
        "contract_version",
        "status",
        "completed",
        "total",
        "candidates",
        "quality_bounds",
        "artifacts",
        "artifact_paths",
    }
    if not required.issubset(payload):
        return False
    statuses = _candidate_statuses(payload)
    if statuses is None:
        return False
    completed = payload["completed"]
    total = payload["total"]
    status = payload["status"]
    return (
        payload["remote_id"] == identity.remote_id
        and payload["dispatch_key"] == identity.dispatch_key
        and payload["registry_digest"] == identity.registry_digest
        and payload["tool_schema_version"] == TOOL_SCHEMA_VERSION
        and payload["contract_version"] == CONTRACT_VERSION
        and status in {"completed", "partial", "cancelled", "failed"}
        and type(completed) is int
        and type(total) is int
        and 0 <= completed <= total
        and completed == statuses.count("completed")
        and len(statuses) <= total
        and (status != "completed" or len(statuses) == total)
        and (status != "cancelled" or not statuses)
        and (status != "partial" or 0 < len(statuses) < total)
        and isinstance(payload["quality_bounds"], dict)
        and isinstance(payload["artifacts"], dict)
        and set(payload["artifacts"]) == {"ic_series", "ic_summary", "group_equity"}
        and isinstance(payload["artifact_paths"], list)
    )


def valid_prototype_progress(progress: JsonValue, result: JsonValue) -> bool:
    """Return whether progress is consistent with its terminal result.

    Args:
        progress: Bounded JSON read from ``run_dir/progress.json``.
        result: Already validated terminal result document.

    Returns:
        ``True`` when all counts are integers and consistent with the result.
    """
    if not isinstance(progress, dict):
        return False
    fields = ("completed", "total", "skipped", "insufficient", "processed")
    if any(type(progress.get(field)) is not int for field in fields):
        return False
    statuses = _candidate_statuses(result)
    if statuses is None:
        return False
    return (
        progress["completed"] == result.get("completed")
        and progress["total"] == result.get("total")
        and progress["processed"] == len(statuses)
        and progress["skipped"] == statuses.count("skipped")
        and progress["insufficient"] == statuses.count("insufficient_data")
        and 0 <= progress["completed"] <= progress["processed"] <= progress["total"]
    )
