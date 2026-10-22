"""Immutable per-bar ledger steps for the pt-v1.3 intraday store (P1, GAP-5).

``store.py`` owns the v1.2 *day* step (``steps/<date>/step.json``); this module
owns the v1.3 *bar* step and never reads or writes that v1.2 layout::

    <account_root>/steps/<YYYY-MM-DD>/bars/<HHMM>/
      step.json           # identity + request/result hashes (no clock)
      orders.jsonl        # every order row after this bar
      fills.jsonl         # this bar's fills
      order_events.jsonl  # this bar's ordered lifecycle events
      positions.jsonl     # end-state positions
      events.jsonl        # session events of this bar
      bars/<symbol>.csv   # the bar row(s) this step consumed
      manifest.json       # sha256 + bytes + lines per file, + format version

The day summary lives next to the bar directories (``steps/<date>/session.json``)
and is written by :func:`~.session_summary.write_session_summary`; both writers
are re-exported here so a caller needs one import.

Invariants:

* **A bar step is never overwritten.**  :func:`write_bar_step` refuses an
  existing ``<HHMM>/`` with :class:`~.bar_layout.BarStepExists`; a step is
  evidence of what the account did at that bar, so rewriting it would
  disconnect the ledger from its own history.
* **Assembly is atomic.**  The tree is built under ``.staging-<HHMM>-*`` in the
  same parent (see :func:`~.storage_io.write_step_directory`), every file is
  fsynced, the manifest is computed over what was actually written, and the
  directory is renamed into place.  A reader never sees a partial bar, and a
  failure leaves no staging residue behind.
* **No symlink is followed and nothing escapes.**  The account root, the
  ``steps/``, date, ``bars/`` and ``<HHMM>/`` directories, ``session.json`` and
  every manifest-listed file are refused when they are symbolic links, and a
  resolved path outside the account root is refused.  Those guards live in
  :mod:`~.bar_layout`, so the write and read paths cannot drift.
* **No clock.**  ``bar_time`` is the caller's injected instant; the store only
  normalizes it to the bar's ``+08:00`` spelling.  Same inputs produce
  byte-identical artefacts, which is what lets a replayed bar be recognized
  as the same bar.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .bar_layout import (
    BAR_STEP_FORMAT,
    BARS_DIRNAME,
    EVENTS_FILE,
    FILLS_FILE,
    MANIFEST_FILE,
    ORDERS_FILE,
    ORDER_EVENTS_FILE,
    POSITIONS_FILE,
    SESSION_FILE,
    SESSION_FORMAT,
    SESSION_STATUSES,
    STEP_FILE,
    BarStepCorrupt,
    BarStepExists,
    BarStepNotFound,
    BarStepPaths,
    BarStoreError,
    InvalidBarLabel,
    SessionCorrupt,
    SessionNotFound,
    UnsafeBarPath,
    bar_step_paths,
    ensure_dir,
    guard_existing_dir,
    normalize_bar_time,
    require_containment,
    require_hash,
    resolve_safely,
    session_summary_path,
)
from .session_summary import read_session_summary, write_session_summary
from .storage_io import (
    StoreError,
    _bar_filename,
    dumps,
    jsonl,
    read_json,
    verify_files,
    write_step_directory,
)

__all__ = [
    "BAR_STEP_FORMAT",
    "BARS_DIRNAME",
    "EVENTS_FILE",
    "FILLS_FILE",
    "MANIFEST_FILE",
    "ORDERS_FILE",
    "ORDER_EVENTS_FILE",
    "POSITIONS_FILE",
    "SESSION_FILE",
    "SESSION_FORMAT",
    "SESSION_STATUSES",
    "STEP_FILE",
    "BarStep",
    "BarStepContent",
    "BarStepCorrupt",
    "BarStepExists",
    "BarStepNotFound",
    "BarStepPaths",
    "BarStoreError",
    "InvalidBarLabel",
    "SessionCorrupt",
    "SessionNotFound",
    "UnsafeBarPath",
    "bar_step_paths",
    "read_bar_step",
    "read_session_summary",
    "session_summary_path",
    "write_bar_step",
    "write_session_summary",
]

#: Every flat file a bar step must carry.  ``bars/<symbol>.csv`` is a set (one
#: per symbol) and ``manifest.json`` cannot hash itself, so both are checked
#: separately in :func:`_verify_step_tree`.
_REQUIRED_FILES: tuple[str, ...] = (
    STEP_FILE,
    ORDERS_FILE,
    FILLS_FILE,
    ORDER_EVENTS_FILE,
    POSITIONS_FILE,
    EVENTS_FILE,
)

_BAR_KEY_RE = re.compile(r"^bars/[A-Za-z0-9][A-Za-z0-9._-]*\.csv$")


# ── values ──


@dataclass(frozen=True, slots=True)
class BarStepContent:
    """Everything one bar step records, as one value (identity + rows).

    Grouping these into a value object keeps :func:`write_bar_step` at two
    arguments and gives the identity/hash fields a single place to be
    validated.  ``bar_time`` is the caller's injected instant (the engine's
    clock), never a clock read by the store.
    """

    account_id: str
    trade_date: str
    bar_label: str
    bar_time: str
    request_hash: str
    result_hash: str
    orders: Sequence[Any] = ()
    fills: Sequence[Any] = ()
    order_events: Sequence[Any] = ()
    positions: Sequence[Any] = ()
    events: Sequence[Any] = ()
    bars: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class BarStep:
    """A verified bar step: the identity document, manifest and parsed rows."""

    account_id: str
    trade_date: str
    bar_label: str
    directory: Path
    step: Mapping[str, Any]
    manifest: Mapping[str, Any]
    orders: tuple[Mapping[str, Any], ...]
    fills: tuple[Mapping[str, Any], ...]
    order_events: tuple[Mapping[str, Any], ...]
    positions: tuple[Mapping[str, Any], ...]
    events: tuple[Mapping[str, Any], ...]
    bars: Mapping[str, str]

    @property
    def bar_time(self) -> str:
        return str(self.step.get("bar_time") or "")

    @property
    def request_hash(self) -> str:
        return str(self.step.get("request_hash") or "")

    @property
    def result_hash(self) -> str:
        return str(self.step.get("result_hash") or "")


# ── write ──


def _prepare_write(paths: BarStepPaths) -> Path:
    """Create the parent chain and return ``bar_dir`` after refusing an existing step."""
    account_root = resolve_safely(paths.account_root, "account root")
    ensure_dir(paths.steps_dir, account_root=account_root, what="steps directory")
    ensure_dir(paths.date_dir, account_root=account_root, what=f"date directory {paths.trade_date}")
    ensure_dir(paths.bars_root, account_root=account_root, what="bars directory")
    if paths.bar_dir.is_symlink():
        raise UnsafeBarPath(
            f"bar directory {paths.bar_dir.name} on {paths.trade_date} is a symbolic link; "
            "refusing to follow it"
        )
    require_containment(resolve_safely(paths.bar_dir, "bar directory"), account_root, "bar directory")
    if paths.bar_dir.exists():
        raise BarStepExists(paths.trade_date, paths.bar_label)
    return paths.bar_dir


def write_bar_step(account_root: str | Path, content: BarStepContent) -> Path:
    """Write one immutable bar step and return its directory.

    All files are assembled under ``.staging-<HHMM>-*`` and renamed into place,
    so a reader sees either the complete step or no step.  Raises
    :class:`~.bar_layout.BarStepExists` when ``<HHMM>/`` is already present — a
    stored bar is never rewritten — and :class:`~.bar_layout.UnsafeBarPath` for
    a symlinked account, date or bar directory (or any path resolving outside
    the account root).
    """
    paths = bar_step_paths(
        account_root,
        account_id=content.account_id,
        trade_date=content.trade_date,
        bar_label=content.bar_label,
    )
    stamp = normalize_bar_time(content.bar_time, paths.trade_date, paths.bar_label)
    request_hash = require_hash(content.request_hash, "request_hash")
    result_hash = require_hash(content.result_hash, "result_hash")
    rows = {
        ORDERS_FILE: tuple(content.orders),
        FILLS_FILE: tuple(content.fills),
        ORDER_EVENTS_FILE: tuple(content.order_events),
        POSITIONS_FILE: tuple(content.positions),
        EVENTS_FILE: tuple(content.events),
    }
    bars = dict(content.bars or {})
    try:
        # Reuse the store's one file-name rule instead of restating its regex,
        # and fail before a directory is created.
        for symbol in bars:
            _bar_filename(symbol)
    except StoreError as exc:
        raise BarStoreError(exc.code, exc.message) from exc
    target = _prepare_write(paths)

    counts = {name[: -len(".jsonl")]: len(value) for name, value in rows.items()}
    counts["bars"] = len(bars)
    identity = {
        "format": BAR_STEP_FORMAT,
        "account_id": paths.account_id,
        "trade_date": paths.trade_date,
        "bar_label": paths.bar_label,
        "bar_time": stamp,
        "request_hash": request_hash,
        "result_hash": result_hash,
        "counts": counts,
    }
    files = {STEP_FILE: (dumps(identity) + "\n").encode("utf-8")}
    files.update({name: jsonl(value) for name, value in rows.items()})
    try:
        write_step_directory(target, files=files, bars=bars, manifest_fields=dict(identity))
    except OSError as exc:
        # A racing writer that landed the directory after our pre-check fails
        # here (a non-empty rename target) — report the domain refusal, not a
        # bare errno.
        if target.exists() or target.is_symlink():
            raise BarStepExists(paths.trade_date, paths.bar_label) from exc
        raise
    return target


# ── read ──


def _read_object(path: Path, error: type[BarStoreError], what: str) -> dict[str, Any]:
    if path.is_symlink():
        raise UnsafeBarPath(f"{what} is a symbolic link; refusing to follow it")
    try:
        value = read_json(path, "bar_step_corrupt")
    except StoreError as exc:
        raise error(exc.message) from exc
    if not isinstance(value, dict):
        raise error(f"{what} is not a JSON object")
    return value


def _read_jsonl(path: Path, what: str) -> tuple[Mapping[str, Any], ...]:
    if path.is_symlink():
        raise UnsafeBarPath(f"{what} is a symbolic link; refusing to follow it")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BarStepCorrupt(f"cannot read {what}: {exc}") from exc
    rows: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BarStepCorrupt(f"{what} line {line_number} is not JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise BarStepCorrupt(f"{what} line {line_number} is not a JSON object")
        rows.append(value)
    return tuple(rows)


def _walk_tree(directory: Path) -> Iterator[tuple[str, Path]]:
    """Every entry below ``directory``, never descending through a symlinked dir."""
    for base, dirnames, filenames in os.walk(directory, followlinks=False):
        base_path = Path(base)
        for name in sorted(dirnames) + sorted(filenames):
            path = base_path / name
            yield path.relative_to(directory).as_posix(), path


def _audit_tree(directory: Path, manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """Return ``symlink:`` / ``unlisted:`` / ``unexpected-dir:`` / ``not-a-file:`` problems."""
    files = manifest.get("files") or {}
    allowed = set(files) | {MANIFEST_FILE}
    problems: list[str] = []
    for relative, path in _walk_tree(directory):
        if path.is_symlink():
            problems.append(f"symlink:{relative}")
        elif path.is_dir():
            if relative != BARS_DIRNAME:
                problems.append(f"unexpected-dir:{relative}")
        elif not path.is_file():
            problems.append(f"not-a-file:{relative}")
        elif relative not in allowed:
            problems.append(f"unlisted:{relative}")
    return tuple(problems)


def _size_problems(directory: Path, manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """Compare each manifest ``bytes`` with the file on disk (``verify_files`` skips sizes)."""
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        return ("manifest:<files>",)
    problems: list[str] = []
    for relative, entry in sorted(files.items()):
        candidate = Path(relative)
        if candidate.anchor or ".." in candidate.parts or not isinstance(entry, Mapping):
            continue
        path = directory / candidate
        if path.is_symlink() or not path.is_file():
            continue
        if path.stat().st_size != entry.get("bytes"):
            problems.append(f"bytes:{relative}")
    return tuple(problems)


def _verify_step_tree(directory: Path, manifest: Mapping[str, Any]) -> None:
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise BarStepCorrupt(f"{directory.name}/manifest.json has no files map")
    missing = tuple(name for name in _REQUIRED_FILES if name not in files)
    if missing:
        raise BarStepCorrupt(f"{directory.name}/manifest.json does not cover {', '.join(missing)}")
    unexpected = tuple(
        name for name in files if name not in _REQUIRED_FILES and _BAR_KEY_RE.fullmatch(name) is None
    )
    if unexpected:
        raise BarStepCorrupt(
            f"{directory.name}/manifest.json lists an unexpected entry {unexpected[0]!r}"
        )
    problems = (
        _audit_tree(directory, manifest)
        + verify_files(directory, manifest)
        + _size_problems(directory, manifest)
    )
    if problems:
        raise BarStepCorrupt(
            f"bar step {directory.name} failed verification: {', '.join(sorted(problems)[:8])}"
        )


def _read_bars(directory: Path, manifest: Mapping[str, Any]) -> dict[str, str]:
    bars: dict[str, str] = {}
    for relative in sorted(manifest.get("files") or {}):
        if _BAR_KEY_RE.fullmatch(relative) is None:
            continue
        path = directory / relative
        try:
            bars[relative[len(BARS_DIRNAME) + 1 : -len(".csv")]] = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BarStepCorrupt(f"cannot read {relative}: {exc}") from exc
    return bars


def _guard_bar_chain(paths: BarStepPaths) -> Path:
    """Refuse every symlink / escape on the ancestor chain and return the resolved root."""
    account_root = resolve_safely(paths.account_root, "account root")
    for path, what in (
        (paths.steps_dir, "steps directory"),
        (paths.date_dir, f"date directory {paths.trade_date}"),
        (paths.bars_root, "bars directory"),
    ):
        guard_existing_dir(
            path,
            account_root=account_root,
            what=what,
            missing=BarStepNotFound(paths.trade_date, paths.bar_label),
        )
    if paths.bar_dir.is_symlink():
        raise UnsafeBarPath(
            f"bar directory {paths.bar_dir.name} on {paths.trade_date} is a symbolic link; "
            "refusing to follow it"
        )
    if not paths.bar_dir.is_dir():
        raise BarStepNotFound(paths.trade_date, paths.bar_label)
    require_containment(resolve_safely(paths.bar_dir, "bar directory"), account_root, "bar directory")
    return paths.bar_dir


def _verify_identity(step: Mapping[str, Any], paths: BarStepPaths) -> None:
    identity = {
        "format": BAR_STEP_FORMAT,
        "account_id": paths.account_id,
        "trade_date": paths.trade_date,
        "bar_label": paths.bar_label,
    }
    for key, expected in identity.items():
        if step.get(key) != expected:
            raise BarStepCorrupt(f"step.json {key} is {step.get(key)!r}, expected {expected!r}")
    raw_bar_time = step.get("bar_time")
    if normalize_bar_time(raw_bar_time, paths.trade_date, paths.bar_label) != raw_bar_time:
        raise BarStepCorrupt("step.json bar_time is not in canonical +08:00 form")
    require_hash(step.get("request_hash"), "step.request_hash")
    require_hash(step.get("result_hash"), "step.result_hash")


def read_bar_step(
    account_root: str | Path,
    trade_date: str,
    bar_label: str,
    *,
    account_id: str | None = None,
) -> BarStep:
    """Read one bar step after verifying its manifest (sha256 + bytes + tree).

    The manifest is the trust anchor: every listed file is hashed and sized,
    every unlisted file is refused, and any symlink in the tree is refused, so
    a tampered or partially written step raises
    :class:`~.bar_layout.BarStepCorrupt` rather than returning rows that no
    longer match what was recorded.
    """
    root = Path(account_root).expanduser()
    paths = bar_step_paths(
        root,
        account_id=root.name if account_id is None else account_id,
        trade_date=trade_date,
        bar_label=bar_label,
    )
    directory = _guard_bar_chain(paths)
    manifest = _read_object(directory / MANIFEST_FILE, BarStepCorrupt, "manifest.json")
    if manifest.get("format") != BAR_STEP_FORMAT:
        raise BarStepCorrupt(
            f"manifest.json carries format {manifest.get('format')!r}, expected {BAR_STEP_FORMAT!r}"
        )
    _verify_step_tree(directory, manifest)
    step = _read_object(directory / STEP_FILE, BarStepCorrupt, "step.json")
    _verify_identity(step, paths)

    rows = {
        name: _read_jsonl(directory / name, name)
        for name in (ORDERS_FILE, FILLS_FILE, ORDER_EVENTS_FILE, POSITIONS_FILE, EVENTS_FILE)
    }
    bars = _read_bars(directory, manifest)
    expected_counts = {name[: -len(".jsonl")]: len(value) for name, value in rows.items()}
    expected_counts["bars"] = len(bars)
    if dict(step.get("counts") or {}) != expected_counts:
        raise BarStepCorrupt(
            f"step.json counts {step.get('counts')!r} do not match the rows on disk {expected_counts!r}"
        )
    return BarStep(
        account_id=paths.account_id,
        trade_date=paths.trade_date,
        bar_label=paths.bar_label,
        directory=directory,
        step=step,
        manifest=manifest,
        orders=rows[ORDERS_FILE],
        fills=rows[FILLS_FILE],
        order_events=rows[ORDER_EVENTS_FILE],
        positions=rows[POSITIONS_FILE],
        events=rows[EVENTS_FILE],
        bars=bars,
    )
