"""Validation of a code draft's source run directory before promotion."""

from __future__ import annotations

from pathlib import Path

from src.strategy_library.paths import resolve_runtime_root, strategies_root
from src.strategy_library.spec import Gap

RUN_DIR_REQUIRED = "run_dir_required"
RUN_DIR_REJECTED = "run_dir_rejected"
RUN_DIR_NOT_FOUND = "run_dir_not_found"
FIX_RUN = "re-run the backtest into a runtime-root run directory"

__all__ = [
    "FIX_RUN",
    "RUN_DIR_NOT_FOUND",
    "RUN_DIR_REJECTED",
    "RUN_DIR_REQUIRED",
    "resolve_run_dir",
]


def resolve_run_dir(raw: str | Path | None, runtime_root: Path | None) -> tuple[Path | None, Gap | None]:
    """Resolve *raw* to a run directory inside the runtime root, or explain why not.

    Rejects, in order: an absent path, ``..`` traversal, a symlinked component
    below the runtime root, a target outside the runtime root, and the strategy
    library itself (which holds specs, never run artifacts).

    Returns:
        ``(resolved_path, None)`` or ``(None, gap)``.
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None, Gap(
            code=RUN_DIR_REQUIRED,
            field="run_dir",
            message="a code strategy needs the backtest run it was verified on",
            fix_hint="pass the run directory produced by the backtest, or promote as a workflow",
        )

    candidate = Path(text).expanduser()
    if ".." in Path(text).parts:
        return None, _rejected(text, "the path traverses with '..'")

    root = resolve_runtime_root(runtime_root).expanduser()
    absolute_root = root if root.is_absolute() else Path.cwd() / root
    absolute = candidate if candidate.is_absolute() else Path.cwd() / candidate
    symlink = _symlinked_ancestor(absolute, absolute_root)
    if symlink is not None:
        return None, _rejected(text, f"the path component {symlink} is a symlink")

    resolved = candidate.resolve()
    resolved_root = root.resolve()
    if not resolved.is_relative_to(resolved_root):
        return None, _rejected(text, f"it resolves outside the runtime root {resolved_root}")
    library = strategies_root(runtime_root=runtime_root).resolve()
    if resolved == library or resolved.is_relative_to(library):
        return None, _rejected(text, "the strategy library is never a run directory")
    if not resolved.is_dir():
        return None, Gap(
            code=RUN_DIR_NOT_FOUND,
            field="run_dir",
            message=f"run directory {resolved} does not exist",
            fix_hint=FIX_RUN,
        )
    return resolved, None


def _symlinked_ancestor(target: Path, root: Path) -> Path | None:
    """Return the first symlinked component of *target* below *root*.

    The walk stops at *root* (in both its written and resolved form), so a
    platform-level symlink above the runtime root — macOS ``/tmp`` — is never
    mistaken for a crafted one below it.
    """
    stop = {root, root.resolve()}
    for ancestor in (target, *target.parents):
        if ancestor in stop:
            return None
        if ancestor.is_symlink():
            return ancestor
    return None


def _rejected(text: str, why: str) -> Gap:
    """Build the stable rejection gap for an untrusted run directory."""
    return Gap(
        code=RUN_DIR_REJECTED,
        field="run_dir",
        message=f"run directory {text!r} was refused: {why}",
        fix_hint=FIX_RUN,
    )
