"""Byte-level JSONL primitives for scheduled-run history.

Owns the concerns that must not drift between writers: the 8192-byte line cap,
the process-local write lock, whole-line ``O_APPEND`` appends, atomic whole-file
rewrites and the job-scoped run-directory cascade. Record validation and the
public API live in :mod:`src.strategy_library.history`.

A single line is bounded by truncating the free-text ``summary``/``error``
fields (a character-budget binary search) so the JSON structure always stays
parseable. If even an empty free-text pair cannot fit, the optional reference
fields are dropped next; ids, timestamps and status are never removed.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.strategy_library.paths import atomic_write_text, validate_id

MAX_LINE_BYTES = 8192

# Process-local only: cross-process writers are unsupported by contract.
WRITE_LOCK = threading.Lock()


def history_file(directory: Path, job_id: str) -> Path:
    """Return ``<directory>/<job_id>.jsonl`` for a validated job id.

    Raises:
        ValueError: When *job_id* is unsafe or the result escapes *directory*.
    """
    ident = validate_id(job_id, what="job_id")
    target = directory / f"{ident}.jsonl"
    if not target.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f"history path {target} escapes {directory}")
    return target


def dump_payload(payload: Mapping[str, Any]) -> bytes:
    """Serialize a record payload as compact UTF-8 JSON, without a newline."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def shrink_payload(payload: dict[str, Any]) -> bytes:
    """Shrink free-text fields until the serialized line fits the byte cap."""
    summary = payload["summary"]
    error = payload["error"]
    summary_len = len(summary)
    error_len = len(error)
    best = dump_payload({**payload, "summary": "", "error": ""})
    low, high = 0, summary_len + error_len
    while low <= high:
        budget = (low + high) // 2
        take_summary = min(summary_len, budget)
        take_error = min(error_len, budget - take_summary)
        candidate = dump_payload(
            {
                **payload,
                "summary": summary[:take_summary],
                "error": error[:take_error],
            }
        )
        if len(candidate) <= MAX_LINE_BYTES:
            best = candidate
            low = budget + 1
        else:
            high = budget - 1
    if len(best) <= MAX_LINE_BYTES:
        return best
    for name, replacement in (
        ("run_dir", None),
        ("session_id", None),
        ("error_kind", ""),
    ):
        payload = {**payload, name: replacement}
        candidate = dump_payload(payload)
        if len(candidate) <= MAX_LINE_BYTES:
            return candidate
    minimal = {
        key: payload[key]
        for key in (
            "run_id",
            "job_id",
            "strategy_id",
            "strategy_version",
            "started_at",
            "finished_at",
            "status",
        )
    }
    return dump_payload(minimal)


def serialize_payload(payload: Mapping[str, Any]) -> bytes:
    """Return one newline-terminated JSONL line of at most ``MAX_LINE_BYTES``."""
    line = dump_payload(payload)
    if len(line) > MAX_LINE_BYTES:
        line = shrink_payload(dict(payload))
    return line + b"\n"


def read_raw_objects(path: Path) -> list[dict[str, Any]]:
    """Read one JSONL file, skipping blank, oversized and undecodable lines.

    A missing file reads as an empty list; every other read error propagates.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return []
    objects: list[dict[str, Any]] = []
    for chunk in raw.split(b"\n"):
        if not chunk.strip():
            continue
        if len(chunk.rstrip(b"\r")) > MAX_LINE_BYTES:
            continue
        try:
            payload = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(payload, dict):
            objects.append(payload)
    return objects


def append_line(path: Path, line: bytes) -> None:
    """Append one complete line with a single ``O_APPEND`` file description."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        view = memoryview(line)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def rewrite_payloads(path: Path, payloads: Sequence[Mapping[str, Any]]) -> None:
    """Atomically replace the file with *payloads* in the given order."""
    text = "".join(dump_payload(payload).decode("utf-8") + "\n" for payload in payloads)
    atomic_write_text(path, text)


def cascade_run_dirs(job_id: str, run_dirs: Sequence[str | None], runtime_root: Path) -> None:
    """Remove run artifact directories owned by a deleted job.

    Only directories contained in ``<runtime_root>/runs`` are removed; a record
    pointing anywhere else is left alone, so history cleanup can never reach
    outside the runtime tree. Symlinked candidates are skipped.
    """
    runs_root = (runtime_root / "runs").resolve()
    candidates: list[Path] = [runtime_root / "runs" / "scheduled" / job_id]
    candidates.extend(Path(value).expanduser() for value in run_dirs if value)
    for candidate in candidates:
        if not candidate.is_absolute():
            candidate = runtime_root / candidate
        if candidate.is_symlink():
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved == runs_root or not resolved.is_relative_to(runs_root):
            continue
        if resolved.is_dir():
            shutil.rmtree(resolved, ignore_errors=True)
