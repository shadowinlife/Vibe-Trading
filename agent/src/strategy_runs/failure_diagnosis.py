"""Bounded, redacted failure diagnostics for a terminated execution worker.

The worker's own files are the only witnesses of a failure: ``state.json``
carries the runner's wrapped exception reason, and ``worker.log`` carries the
stdout/stderr tail. Both disappear when the execution box is reclaimed, so
the supervisor folds them into one bounded, redacted reason plus a machine
classification while the evidence still exists.

Classification vocabulary: ``network_timeout`` for data-plane fetch and
timeout signatures, ``logic_error`` for a child-reported exception without a
network signature, and ``runner_error`` when nothing classifiable survived.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from src.strategy_runs.control_contract import FAILURE_REASON_LIMIT
from src.tools.redaction import redact_internal_paths, redact_text

__all__ = ["failure_diagnostic"]

_LOG_TAIL_BYTES: Final = 4096
_STATE_BYTE_CAP: Final = 64 * 1024
_NETWORK_SIGNATURES: Final = (
    "timed out",
    "timeout",
    "failed after 3 attempts",
    "incomplete tencent history",
    "noavailablesourceerror",
    "connectionerror",
    "connection reset",
    "connection aborted",
    "max retries exceeded",
    "name resolution",
    "remotedisconnected",
)


def failure_diagnostic(
    run_dir: Path, log_path: Path, exit_code: int | None
) -> tuple[str, str]:
    """Return ``(failure_kind, failure_reason)`` for one failed worker.

    Args:
        run_dir: Worker run directory holding ``state.json`` when written.
        log_path: Worker stdout/stderr log written by the supervisor.
        exit_code: Observed child exit code, or ``None`` when unobservable.

    Returns:
        A classification from the module vocabulary and a non-empty reason
        of at most ``FAILURE_REASON_LIMIT`` characters, redacted of
        credentials and internal topology.
    """
    state_reason = _state_reason(run_dir)
    text = state_reason if state_reason is not None else _log_tail(log_path)
    reason = redact_text(redact_internal_paths(text)).strip()
    if not reason:
        reason = (
            f"worker exited with code {exit_code}"
            if exit_code is not None
            else "worker exit was not observable"
        )
    lowered = reason.lower()
    if any(signature in lowered for signature in _NETWORK_SIGNATURES):
        kind = "network_timeout"
    elif state_reason is not None:
        kind = "logic_error"
    else:
        kind = "runner_error"
    return kind, reason[-FAILURE_REASON_LIMIT:]


def _state_reason(run_dir: Path) -> str | None:
    """Return the runner's failure reason when ``state.json`` records one."""
    try:
        with (run_dir / "state.json").open("rb") as stream:
            raw = stream.read(_STATE_BYTE_CAP + 1)
        if len(raw) > _STATE_BYTE_CAP:
            return None
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("status") != "failed":
        return None
    reason = payload.get("reason")
    return reason if isinstance(reason, str) and reason.strip() else None


def _log_tail(log_path: Path) -> str:
    """Return the last bounded window of the worker log, best effort."""
    try:
        with log_path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - _LOG_TAIL_BYTES))
            raw = stream.read(_LOG_TAIL_BYTES)
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace")
