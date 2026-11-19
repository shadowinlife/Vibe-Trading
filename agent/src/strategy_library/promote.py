"""Promote a session's strategy draft into the strategy library.

Promotion validates the draft deterministically, checks a code strategy's
backtest run, copies its config and engine into ``strategies/<id>/``, and then
evaluates the SOURCE run immediately so a strategy whose backtest already
passed is schedulable without a second request.

Three rules keep the result safe to schedule from:

* Nothing is registered unless the precheck passed. A spec or run-directory gap
  returns a :class:`~src.strategy_library.spec.Gap` and creates neither a row
  nor a directory, for both kinds — a workflow is registered only once it is
  structurally reviewable and stays ``needs_work`` until review decides.
* A code strategy's stored ``run_dir`` is the SOURCE backtest directory, and
  that is the path every gate inspects. The library copy holds no run
  artifacts, so it is never handed to a gate.
* The auto-evaluation is authoritative: a refused run returns ``needs_work``
  with the gate's own reasons. Nothing here upgrades a failure to ``ready``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.strategy_library.gate_code import (
    NEEDS_WORK,
    REASON_NO_METRICS,
    REASON_NO_STATE,
    Verdict,
    evaluate_code,
)
from src.strategy_library.paths import (
    SIGNAL_ENGINE_RELPATH,
    VERSION_CONFIG_FILENAME,
    atomic_write_text,
    resolve_runtime_root,
    strategies_root,
    strategy_dir,
)
from src.strategy_library.spec import Gap, StrategySpec, validate_spec
from src.strategy_library.store import (
    DuplicateNameUniverseError,
    create_draft,
    get,
    set_readiness,
)
from src.strategy_store.models import Artifact
from src.strategy_store.sqlite_store import SqliteStrategyStore

__all__ = [
    "COPY_FAILED",
    "DUPLICATE_NAME_UNIVERSE",
    "INVALID_SPEC",
    "MISSING_CONFIG",
    "MISSING_SIGNAL_ENGINE",
    "RUN_DIR_NOT_FOUND",
    "RUN_DIR_REJECTED",
    "RUN_DIR_REQUIRED",
    "PromotionResult",
    "promote",
]

#: A code draft names no backtest run.
RUN_DIR_REQUIRED = "run_dir_required"

#: The named path escapes the runtime root, traverses with ``..``, passes
#: through a symlink, or points into the strategy library itself.
RUN_DIR_REJECTED = "run_dir_rejected"

#: The path resolved inside the runtime root but is not an existing directory.
RUN_DIR_NOT_FOUND = "run_dir_not_found"

#: The run directory has no non-empty ``config.json``.
MISSING_CONFIG = "missing_config"

#: The run directory has no non-empty ``code/signal_engine.py``.
MISSING_SIGNAL_ENGINE = "missing_signal_engine"

#: Copying the run's config or engine into the strategy directory failed.
COPY_FAILED = "promote_copy_failed"

#: Another strategy already holds this ``(name, universe)`` pair.
DUPLICATE_NAME_UNIVERSE = "duplicate_name_universe"

#: The draft carries an identity the store refuses (e.g. a non-string universe).
INVALID_SPEC = "invalid_spec"

#: Files a promotable code run must carry, in check order. The first two reuse
#: the gate's reason codes so promotion and evaluation name the same defect.
_REQUIRED_RUN_FILES: tuple[tuple[str, str, str], ...] = (
    ("state.json", REASON_NO_STATE, "the run has no non-empty state.json"),
    ("artifacts/metrics.csv", REASON_NO_METRICS, "the run has no non-empty artifacts/metrics.csv"),
    ("config.json", MISSING_CONFIG, "the run has no non-empty config.json"),
    ("code/signal_engine.py", MISSING_SIGNAL_ENGINE, "the run has no non-empty signal engine"),
)

_FIX_RUN = "re-run the backtest into a runtime-root run directory"


@dataclass(frozen=True)
class PromotionResult:
    """Outcome of one promotion attempt.

    Attributes:
        strategy_id: The registered strategy, or ``None`` when nothing was
            created (precheck gap, duplicate, or unsafe run directory).
        readiness: Scheduling readiness after the attempt — what the automatic
            evaluation wrote, or ``needs_work`` when nothing became ready.
        gaps: Deterministic defects, empty on success. A spec/run-directory gap
            means no row exists; a gate gap means the row stays ``needs_work``.
        verdict: The gate document returned by the automatic evaluation with
            the promoting ``session_id`` added, or ``None`` when none ran.
        artifact: The registered row when one was created, else ``None``.
        session_id: Promotion provenance. The store has no session column, so
            this lives only in the result and the verdict document.
    """

    strategy_id: str | None
    readiness: str
    gaps: list[Gap] = field(default_factory=list)
    verdict: dict[str, Any] | None = None
    artifact: Artifact | None = None
    session_id: str = ""


def promote(
    spec_draft: StrategySpec | Mapping[str, Any],
    *,
    session_id: str,
    run_dir: str | Path | None = None,
    demo: bool = False,
    store: SqliteStrategyStore | None = None,
    runtime_root: Path | None = None,
) -> PromotionResult:
    """Register a strategy draft and auto-evaluate a code strategy's run.

    The spec is validated first. Any gap returns immediately with
    ``needs_work`` and registers nothing — an incomplete draft is never a
    library entry, so the listing cannot advertise a strategy that was never
    reviewable.

    ``kind="code"`` requires *run_dir*: a directory under the runtime root,
    outside ``strategies/``, carrying non-empty ``state.json``,
    ``artifacts/metrics.csv``, ``config.json`` and ``code/signal_engine.py``.
    The resolved source path is stored as the artifact's ``run_dir`` and its
    config and engine are copied into ``strategies/<id>/``. The source run is
    then evaluated with no override, so the gate reads exactly the stored path.

    ``kind="workflow"`` writes the spec only and is left ``needs_work`` for the
    independent review; no model runs here.

    A duplicate ``(name, universe)`` is reported as a gap and writes nothing.

    Args:
        spec_draft: The session's spec, as a ``StrategySpec`` or a mapping.
        session_id: Chat session the promotion came from. Recorded as
            provenance in the result and the verdict; may be blank for a
            REST-initiated promotion.
        run_dir: Source backtest run directory, required for ``kind="code"``.
        demo: Mark the strategy as demonstration data.
        store: Strategy store to write; defaults to the process singleton.
        runtime_root: Explicit runtime root override (tests).

    Returns:
        The promotion result. ``readiness`` is the refreshed row's state, so it
        reflects what the gate actually wrote.

    Raises:
        StaleVerdictError: When the strategy was edited between registration
            and the automatic evaluation; nothing of that verdict is written and
            the row keeps its ``needs_work`` state.
    """
    session = str(session_id or "").strip()
    gaps = validate_spec(spec_draft)
    if gaps:
        return PromotionResult(None, NEEDS_WORK, gaps, None, None, session)

    source: Path | None = None
    if _kind(spec_draft) == "code":
        source, gap = _resolve_run_dir(run_dir, runtime_root)
        if gap is not None:
            return PromotionResult(None, NEEDS_WORK, [gap], None, None, session)
        assert source is not None  # a resolved path is the only success shape
        gap = _check_run_files(source)
        if gap is not None:
            return PromotionResult(None, NEEDS_WORK, [gap], None, None, session)

    try:
        artifact = create_draft(
            spec_draft,
            run_dir=str(source) if source is not None else None,
            demo=demo,
            store=store,
            runtime_root=runtime_root,
        )
    except DuplicateNameUniverseError as exc:
        gap = Gap(
            code=DUPLICATE_NAME_UNIVERSE,
            field="name",
            message=str(exc),
            fix_hint="edit the existing strategy or choose another name",
        )
        return PromotionResult(None, NEEDS_WORK, [gap], None, None, session)
    except ValueError as exc:
        return PromotionResult(
            None,
            NEEDS_WORK,
            [Gap(code=INVALID_SPEC, field="spec", message=str(exc))],
            None,
            None,
            session,
        )

    if source is None:
        # The workflow's readiness is the reviewer's decision, so promotion only
        # records "registered, not ready" — never a model verdict and never ready.
        pending = set_readiness(artifact.id, NEEDS_WORK, store=store)
        return PromotionResult(artifact.id, pending.readiness, [], None, pending, session)

    gap = _copy_code_sources(source, artifact.id, runtime_root=runtime_root)
    if gap is not None:
        refreshed = get(artifact.id, store=store) or artifact
        return PromotionResult(artifact.id, NEEDS_WORK, [gap], None, refreshed, session)

    verdict = evaluate_code(artifact.id, store=store)
    refreshed = get(artifact.id, store=store) or artifact
    document = {**verdict.payload(), "session_id": session}
    return PromotionResult(
        artifact.id,
        refreshed.readiness,
        _verdict_gaps(verdict),
        document,
        refreshed,
        session,
    )


def _kind(spec: StrategySpec | Mapping[str, Any]) -> str:
    """Return the validated spec kind (``code`` or ``workflow``)."""
    if isinstance(spec, StrategySpec):
        return spec.kind
    return str(spec.get("kind") or "")


def _resolve_run_dir(
    raw: str | Path | None, runtime_root: Path | None
) -> tuple[Path | None, Gap | None]:
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
            fix_hint=_FIX_RUN,
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
        fix_hint=_FIX_RUN,
    )


def _check_run_files(run_dir: Path) -> Gap | None:
    """Return the first missing or empty required file, or ``None``.

    Structural only: whether the run SUCCEEDED is the gate's business. This
    check runs before anything is created so an unusable run never leaves a
    half-promised strategy behind.
    """
    for relpath, code, message in _REQUIRED_RUN_FILES:
        path = _under(run_dir, relpath)
        try:
            usable = path.is_file() and path.stat().st_size > 0
        except OSError:
            usable = False
        if not usable:
            return Gap(
                code=code,
                field="run_dir",
                message=f"{message} ({path})",
                fix_hint=_FIX_RUN,
            )
    return None


def _copy_code_sources(run_dir: Path, strategy_id: str, *, runtime_root: Path | None) -> Gap | None:
    """Copy the run's config and engine into ``strategies/<id>/``.

    Text is decoded and atomically rewritten, so a crash leaves either the old
    file or the complete new one. A failure discards whatever was already
    copied: the strategy keeps only its spec and stays out of scheduling.
    """
    base = strategy_dir(strategy_id, runtime_root=runtime_root)
    copied: list[Path] = []
    for relpath in (VERSION_CONFIG_FILENAME, SIGNAL_ENGINE_RELPATH):
        source = _under(run_dir, relpath)
        destination = _under(base, relpath)
        try:
            atomic_write_text(destination, source.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            _discard(copied)
            return Gap(
                code=COPY_FAILED,
                field=relpath,
                message=f"could not copy {relpath} into the strategy directory: {exc}",
                fix_hint="check disk space and that the file is UTF-8 text",
            )
        copied.append(destination)
    return None


def _discard(paths: list[Path]) -> None:
    """Remove partially copied files and their now-empty directories."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
            path.parent.rmdir()
        except OSError:
            continue


def _under(base: Path, relpath: str) -> Path:
    """Join a POSIX relative path onto *base* with explicit segments."""
    return base.joinpath(*relpath.split("/"))


def _verdict_gaps(verdict: Verdict) -> list[Gap]:
    """Turn the gate's failure reasons into promotion gaps."""
    detail = str(verdict.details.get("detail") or "")
    return [
        Gap(
            code=reason, field="run_dir", message=detail,
            fix_hint="fix the backtest run, then re-evaluate the strategy",
        )
        for reason in verdict.reasons
    ]
