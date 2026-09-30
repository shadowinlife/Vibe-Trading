"""Best-effort stage progress reporting for backtest runs.

The engine subprocess writes a small ``progress.json`` snapshot into the run
directory at stage boundaries so out-of-band readers (e.g. the
``read_run_artifact`` progress channel) can tell how far a long-running
backtest has gotten without waiting for it to finish.

Two properties are load-bearing:

* **Atomic** — the payload lands via a temporary file plus :func:`os.replace`,
  so a concurrent reader never observes a half-written JSON document.
* **Best-effort** — every filesystem error is swallowed and logged at debug
  level. Progress reporting is observability, not behavior: a run whose
  directory is read-only, full, or vanished mid-flight must complete exactly
  as if this module did not exist.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

#: Name of the progress snapshot inside the run directory.
PROGRESS_FILENAME = "progress.json"


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_progress(run_dir: Path | str, stage: str, pct: float) -> None:
    """Atomically write a stage progress snapshot into the run directory.

    The payload is exactly::

        {"stage": str, "pct": float, "updated_at": "<ISO-8601 UTC>"}

    so consumers can rely on the key set. The write goes to a per-process
    temporary file beside the target and is published with :func:`os.replace`,
    which is atomic on the same filesystem.

    This function never raises: any :class:`OSError` (unwritable directory,
    missing run dir, disk full) is logged at debug level and ignored, so a
    progress write can never break a backtest run.

    Args:
        run_dir: Run directory that should contain ``progress.json``.
        stage: Stage label, e.g. ``"data_load"``, ``"done"`` or ``"failed"``.
        pct: Completion percentage, ``0`` to ``100``.
    """
    target = Path(run_dir) / PROGRESS_FILENAME
    # Per-process suffix so two writers sharing a run dir cannot clobber each
    # other's temporary file; the published name is still exactly one file.
    tmp_path = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        payload = {
            "stage": str(stage),
            "pct": float(pct),
            "updated_at": _utc_now_iso(),
        }
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp_path, target)
    except OSError:
        logger.debug(
            "progress write failed (run_dir=%s, stage=%s); continuing",
            run_dir,
            stage,
            exc_info=True,
        )
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
