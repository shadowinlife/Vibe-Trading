"""Child-process entry point for one scheduled code-strategy run.

Started by :func:`src.strategy_library.execute_code.start_code_run` as
``python -m src.strategy_library.run_code_entry <run_dir>``. The strategy
source is imported only by the backtest engine this module calls, never here.

``run_backtest`` writes ``state.json`` itself once the engine has run, but it
returns an error envelope *before* that on validation failures (missing
config, unreadable snapshot, rejected ``run_dir``). Such a run has no terminal
state, so the job would stay ``running`` until its wall-clock timeout; this
entry translates that envelope into ``state.json {"status": "failed"}`` and
mirrors the runner's diagnostic into ``logs/runner_stderr.txt`` so the poller
has a reason to report.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Sequence

from src.strategy_library.paths import atomic_write_json

STATE_FILENAME = "state.json"
RUNNER_STDERR_RELPATH = Path("logs") / "runner_stderr.txt"

_MAX_REASON_CHARS = 500
_OK_STATUS = "ok"


def _read_state(run_dir: Path) -> dict[str, Any] | None:
    """Return the parsed state object, or ``None`` when absent or unreadable."""
    try:
        payload = json.loads((run_dir / STATE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_unfinished(state: dict[str, Any] | None) -> bool:
    """Return whether *state* still permits a terminal failure write."""
    if state is None:
        return True
    status = str(state.get("status") or "").strip().lower()
    return not status or status not in {"success", "completed", "failed", "cancelled"}


def _write_failure(run_dir: Path, reason: str) -> None:
    """Record a terminal failure unless the runner already recorded one."""
    if not _is_unfinished(_read_state(run_dir)):
        return
    text = reason.strip() or "strategy run failed"
    atomic_write_json(
        run_dir / STATE_FILENAME,
        {"status": "failed", "reason": text[:_MAX_REASON_CHARS]},
    )


def _append_stderr_log(run_dir: Path, text: str) -> None:
    """Append *text* to the runner stderr log the poller reads."""
    if not text.strip():
        return
    path = run_dir / RUNNER_STDERR_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text if text.endswith("\n") else f"{text}\n")


def _error_text(payload: dict[str, Any]) -> str:
    """Return the most specific error string an envelope carries."""
    for key in ("error", "reason", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def is_error_envelope(payload: Any) -> bool:
    """Return whether a ``run_backtest`` payload reports anything but success.

    Success is exactly ``status == "ok"``: treating an unrecognised shape as
    success would leave the run ``running`` forever.
    """
    if not isinstance(payload, dict):
        return True
    if payload.get("error"):
        return True
    status = str(payload.get("status") or "").strip().lower()
    return status != _OK_STATUS


def apply_result(run_dir: Path, payload: Any) -> bool:
    """Translate one ``run_backtest`` envelope into a terminal state.

    Args:
        run_dir: Directory the run executed in.
        payload: Parsed ``run_backtest`` return value.

    Returns:
        ``True`` when the run succeeded, ``False`` when a failure was recorded.
    """
    if not is_error_envelope(payload):
        return True

    envelope = payload if isinstance(payload, dict) else {}
    reason = _error_text(envelope) or "backtest returned an error envelope"
    _write_failure(run_dir, reason)

    details = [f"[run_code_entry] run_backtest reported an error: {reason}"]
    for key in ("stdout", "stderr"):
        value = envelope.get(key)
        if isinstance(value, str) and value.strip():
            details.append(f"{key}:\n{value.strip()}")
    _append_stderr_log(run_dir, "\n".join(details))
    return False


def _default_backtest(run_dir: str) -> str:
    """Import and call the synchronous backtest entry point."""
    from src.tools.backtest_tool import run_backtest

    return run_backtest(run_dir)


def main(
    argv: Sequence[str] | None = None,
    *,
    run_backtest_fn: Callable[[str], str] | None = None,
) -> int:
    """Execute one run directory and leave it with a terminal state.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``. The first
            element is the run directory.
        run_backtest_fn: Injection seam for tests; defaults to the real
            synchronous backtest entry point, imported lazily.

    Returns:
        ``0`` on success, ``1`` on failure, ``2`` on a usage error.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            "usage: python -m src.strategy_library.run_code_entry <run_dir>",
            file=sys.stderr,
        )
        return 2

    run_dir = Path(args[0]).expanduser().resolve()
    backtest = _default_backtest if run_backtest_fn is None else run_backtest_fn
    try:
        raw = backtest(str(run_dir))
    except BaseException as exc:
        # Even an interpreter-level abort must leave a terminal state, or the
        # job stays RUNNING until its wall-clock timeout.
        reason = f"{type(exc).__name__}: {exc}"
        _write_failure(run_dir, reason)
        _append_stderr_log(run_dir, traceback.format_exc())
        print(reason, file=sys.stderr)
        return 1

    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        payload = {
            "status": "error",
            "error": "run_backtest returned a non-JSON payload",
        }
    return 0 if apply_result(run_dir, payload) else 1


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
