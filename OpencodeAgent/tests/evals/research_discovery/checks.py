"""Check multi-turn target and artifact evidence, not mere tool-call presence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def check_record(record: dict[str, Any], case: dict[str, Any], root: Path) -> list[str]:
    """Return trace/target/artifact violations for one synthetic evaluation case.

    Args:
        record: Recorded user turns, final target/route, results and artifact receipt.
        case: Pre-registered corpus expectation.
        root: Allowed directory for sanitized result artifacts.

    Returns:
        All mechanical failures. Semantic usefulness still needs blind human review.
    """
    errors: list[str] = []
    if record.get("user_turns") != case["turns"]:
        errors.append("user turns must be recorded, not inferred from a tool call")
    if record.get("target") != case["target"]:
        errors.append("final research target differs from the user's revised target")
    if record.get("route") != case["route"]:
        errors.append("selected route differs from the final research goal")
    unknown = record.get("unknown", [])
    if not all(name in unknown for name in case["unknown"]):
        errors.append("unknown user information was replaced with defaults")
    results = record.get("tool_results", [])
    ids = [result.get("call_id") for result in results]
    if any(not isinstance(identifier, str) or not identifier for identifier in ids):
        errors.append("tool results require call IDs")
    if len(ids) != len(set(ids)):
        errors.append("duplicate result call IDs")
    receipt = record.get("artifact")
    if not case["artifact_required"]:
        if receipt:
            errors.append("unexpected research artifact for non-artifact case")
        return errors
    if not isinstance(receipt, dict):
        return errors + ["missing actual artifact receipt"]
    path = (root / receipt.get("path", "")).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return errors + ["artifact missing or outside result directory"]
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != receipt.get("sha256"):
        return errors + ["artifact digest mismatch"]
    artifact = json.loads(data)
    if artifact.get("target") != case["target"]:
        errors.append("artifact does not answer the revised research target")
    if not artifact.get("conclusion") or not artifact.get("limitations"):
        errors.append("artifact requires conclusion and limitations")
    refs = artifact.get("source_call_ids", [])
    if not refs or any(ref not in ids for ref in refs):
        errors.append("artifact cites no observed evidence or fabricates citations")
    for result in results:
        if result.get("call_id") in refs:
            payload = result.get("payload")
            if not payload or not result.get("data_as_of"):
                errors.append("cited result lacks actual evidence or data_as_of")
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
            ).hexdigest()
            if result.get("sha256") != digest:
                errors.append("cited result digest mismatch")
    return errors
