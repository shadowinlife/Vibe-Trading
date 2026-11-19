"""Bounded ``scope=run`` report payload for the run ledger.

The payload follows the existing backtest-report shape (``metrics`` /
``equity_curve`` / ``trades``) so the RM run normalizer needs no second format.
Run directories are constrained to ``<runtime_root>/runs``: a handle that
points elsewhere is ignored in favour of the ``svc-<vt_run_id>`` scan. Source
artifacts are read with a 64 MiB head cap and a 200k-row cap so an oversized
CSV or markdown cannot exhaust memory; hitting either cap sets ``truncated``,
and the 2 MiB payload shrinker reports its own trimming through the same flag.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.state_export._errors import UnknownRunId
from src.state_export._paths import (
    MAX_RUN_PAYLOAD_BYTES,
    _STRATEGY_DIRNAME,
    _epoch_ms_to_iso,
    _is_safe_segment,
    _json_safe,
    _resolved_root,
)
from src.strategy_runs.store import DEFAULT_DB_FILENAME, StrategyRunStore

__all__ = ["export_run"]

#: Largest prefix of any single run artifact that is read into memory.
_MAX_SOURCE_FILE_BYTES = 64 * 1024 * 1024
#: Hard row bound for one CSV artifact, independent of the byte cap.
_MAX_CSV_ROWS = 200_000
#: Report markdown is capped head-first before the JSON shrink loop starts.
_REPORT_HEAD_BYTES = 256 * 1024
_INT_RE = re.compile(r"[+-]?\d+")


def export_run(vt_run_id: str, *, runtime_root: Path | None = None) -> dict[str, Any]:
    """Return the bounded report payload for one ledger run.

    Args:
        vt_run_id: Ledger run identifier.
        runtime_root: Explicit runtime root (tests); defaults to the configured
            runtime root.

    Returns:
        A JSON-safe dict of at most :data:`MAX_RUN_PAYLOAD_BYTES` bytes.

    Raises:
        UnknownRunId: When the ledger has no row for *vt_run_id*.
    """
    if not isinstance(vt_run_id, str) or not vt_run_id:
        raise UnknownRunId("vt_run_id must be a non-empty string")
    root = _resolved_root(runtime_root)
    store = StrategyRunStore(root / DEFAULT_DB_FILENAME) if runtime_root is not None else StrategyRunStore()
    row = store.get(vt_run_id)
    if row is None:
        raise UnknownRunId(f"unknown vt_run_id {vt_run_id!r}")
    run_dir = _run_directory(root, row.vt_run_id, row.handle_json)
    card, card_truncated = _read_run_card(run_dir)
    metrics = card.get("metrics") if isinstance(card.get("metrics"), dict) else None
    if metrics is None:
        metrics = _first_csv_row(run_dir, "metrics.csv")
    strategy = _strategy_name(root, row.strategy_id)
    equity_curve, equity_truncated = _csv_records(run_dir, "equity.csv")
    trades, trades_truncated = _csv_records(run_dir, "trades.csv")
    report_markdown, report_truncated = _read_report_markdown(run_dir)
    payload: dict[str, Any] = {
        "vt_run_id": row.vt_run_id,
        "strategy_id": row.strategy_id,
        "strategy_version": row.strategy_version,
        "status": row.status,
        "started_at": _epoch_ms_to_iso(row.created_at),
        "finished_at": None if row.status == "running" else _epoch_ms_to_iso(row.updated_at),
        "title": _run_title(card, strategy, row.vt_run_id),
        "strategy": strategy,
        "metrics": _json_safe(metrics),
        "equity_curve": equity_curve,
        "trades": trades,
        "report_markdown": report_markdown,
        "truncated": (
            equity_truncated or trades_truncated or report_truncated or card_truncated
        ),
    }
    return _cap_run_payload(payload)


def _runs_root(root: Path) -> Path:
    """Return the resolved ``<runtime_root>/runs`` directory."""
    return (root / "runs").resolve()


def _contained_directory(path: Path, root: Path) -> bool:
    """Return whether *path* is a directory resolving inside *root*."""
    try:
        return path.is_dir() and path.resolve().is_relative_to(root)
    except OSError:
        return False


def _run_directory(root: Path, vt_run_id: str, handle_json: str | None) -> Path | None:
    """Resolve a handle run directory inside ``runs/``, else the newest run dir.

    A persisted handle is untrusted input for path purposes: even a ledger row
    edited by hand cannot make this read files outside the runtime root's
    ``runs/`` tree. The fallback scan is authoritative.
    """
    runs_root = _runs_root(root)
    if handle_json:
        try:
            handle = json.loads(handle_json)
        except (TypeError, ValueError):
            handle = None
        if isinstance(handle, dict):
            raw = handle.get("run_dir")
            if isinstance(raw, str) and raw and _contained_directory(Path(raw), runs_root):
                return Path(raw)
    if not _is_safe_segment(vt_run_id):
        return None
    job_dir = runs_root / "scheduled" / f"svc-{vt_run_id}"
    if not _contained_directory(job_dir, runs_root):
        return None
    candidates = [
        path for path in job_dir.iterdir() if path.is_dir() and not path.is_symlink() and _contained_directory(path, runs_root)
    ]
    if not candidates:
        return None
    try:
        return max(candidates, key=lambda path: path.stat().st_mtime_ns)
    except OSError:
        return None


def _artifact_path(run_dir: Path | None, *parts: str) -> Path | None:
    """Return an existing artifact inside *run_dir*, refusing symlink escapes."""
    if run_dir is None:
        return None
    path = run_dir.joinpath(*parts)
    try:
        if path.is_file() and path.resolve().is_relative_to(run_dir.resolve()):
            return path
    except OSError:
        return None
    return None


def _read_capped_bytes(path: Path) -> tuple[bytes, bool]:
    """Read at most :data:`_MAX_SOURCE_FILE_BYTES`; the flag reports a cap hit."""
    with path.open("rb") as handle:
        raw = handle.read(_MAX_SOURCE_FILE_BYTES + 1)
    return raw[:_MAX_SOURCE_FILE_BYTES], len(raw) > _MAX_SOURCE_FILE_BYTES


def _read_run_card(run_dir: Path | None) -> tuple[dict[str, Any], bool]:
    """Return ``run_card.json`` and whether its read hit the size cap."""
    path = _artifact_path(run_dir, "run_card.json")
    if path is None:
        return {}, False
    truncated = False
    try:
        raw, truncated = _read_capped_bytes(path)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}, truncated
    return (payload, truncated) if isinstance(payload, dict) else ({}, truncated)


def _read_report_markdown(run_dir: Path | None) -> tuple[str | None, bool]:
    """Return up to 64 MiB of ``run_card.md`` and whether the cap was hit."""
    path = _artifact_path(run_dir, "run_card.md")
    if path is None:
        return None, False
    try:
        raw, truncated = _read_capped_bytes(path)
    except OSError:
        return None, False
    try:
        return raw.decode("utf-8"), truncated
    except UnicodeDecodeError:
        if not truncated:
            return None, False
        return raw.decode("utf-8", errors="ignore"), True


def _first_csv_row(run_dir: Path | None, filename: str) -> dict[str, Any]:
    """Return the first row of ``artifacts/<filename>`` with numeric coercion."""
    records, _truncated = _csv_records(run_dir, filename, limit=1)
    return records[0] if records else {}


def _csv_records(
    run_dir: Path | None,
    filename: str,
    *,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Read ``artifacts/<filename>`` as rows of JSON-safe, coerced cells.

    At most 64 MiB of the file and 200k rows are consumed. Either cap sets the
    returned flag; a byte-capped read drops the final partial line so a cut
    number is never reported as a row.
    """
    path = _artifact_path(run_dir, "artifacts", filename)
    if path is None:
        return [], False
    try:
        truncated = path.stat().st_size > _MAX_SOURCE_FILE_BYTES
        if truncated:
            raw, _capped = _read_capped_bytes(path)
            text = raw.decode("utf-8", errors="ignore")
            last_newline = text.rfind("\n")
            text = text[: last_newline + 1] if last_newline >= 0 else ""
            source: Any = io.StringIO(text)
        else:
            source = path.open("r", encoding="utf-8", newline="")
        rows: list[dict[str, Any]] = []
        with source:
            for index, record in enumerate(csv.DictReader(source)):
                if limit is not None and index >= limit:
                    break
                if index >= _MAX_CSV_ROWS:
                    truncated = True
                    break
                rows.append({str(key): _coerce_cell(value) for key, value in record.items()})
    except (OSError, csv.Error, UnicodeDecodeError):
        return [], False
    return rows, truncated


def _coerce_cell(value: Any) -> Any:
    """Coerce a clean integer/float cell, leaving every other value untouched."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if _INT_RE.fullmatch(text):
        try:
            return int(text)
        except ValueError:
            return value
    try:
        number = float(text)
    except ValueError:
        return value
    return number if math.isfinite(number) else value


def _strategy_name(root: Path, strategy_id: str) -> str:
    """Return the spec's display name, falling back to the strategy id."""
    if not _is_safe_segment(strategy_id):
        return strategy_id
    try:
        payload = json.loads(
            (root / _STRATEGY_DIRNAME / strategy_id / "strategy.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return strategy_id
    if isinstance(payload, dict):
        name = payload.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return strategy_id


def _run_title(card: Mapping[str, Any], strategy: str, vt_run_id: str) -> str:
    """Derive a readable title from the run card, then the strategy, then the id."""
    raw_title = card.get("title")
    if isinstance(raw_title, str) and raw_title.strip():
        return raw_title.strip()
    backtest = card.get("backtest")
    if isinstance(backtest, Mapping):
        codes = backtest.get("codes")
        if isinstance(codes, list):
            labels = [str(code) for code in codes if str(code).strip()]
            if labels:
                return " \u00b7 ".join(labels)
    return strategy or vt_run_id


def _cap_run_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Bound *payload* to :data:`MAX_RUN_PAYLOAD_BYTES` deterministically.

    The first step trims report markdown to a 256 KiB head. The shrink loop
    then halves ``equity_curve`` and ``trades`` behind stable first/last rows,
    drops them once only two rows remain, halves the markdown, and finally
    removes metric keys in sorted order.
    """
    if _serialised_size(payload) <= MAX_RUN_PAYLOAD_BYTES:
        return payload

    payload["truncated"] = True
    markdown = payload.get("report_markdown")
    if isinstance(markdown, str):
        payload["report_markdown"] = _head_utf8(markdown, _REPORT_HEAD_BYTES)

    for _ in range(4096):
        if _serialised_size(payload) <= MAX_RUN_PAYLOAD_BYTES:
            break
        equity = payload.get("equity_curve")
        trades = payload.get("trades")
        if isinstance(equity, list) and len(equity) > 2:
            payload["equity_curve"] = _halve(equity)
            continue
        if isinstance(trades, list) and len(trades) > 2:
            payload["trades"] = _halve(trades)
            continue
        if (isinstance(equity, list) and equity) or (isinstance(trades, list) and trades):
            payload["equity_curve"] = []
            payload["trades"] = []
            continue
        markdown = payload.get("report_markdown")
        if isinstance(markdown, str) and markdown:
            payload["report_markdown"] = _head_utf8(markdown, len(markdown.encode("utf-8")) // 2)
            continue
        metrics = payload.get("metrics")
        if isinstance(metrics, dict) and metrics:
            keys = sorted(metrics)
            metrics.pop(keys[-1], None)
            continue
        break
    if _serialised_size(payload) > MAX_RUN_PAYLOAD_BYTES:
        payload["metrics"] = {}
        payload["equity_curve"] = []
        payload["trades"] = []
        payload["report_markdown"] = None
    return payload


def _serialised_size(payload: Mapping[str, Any]) -> int:
    """Return the UTF-8 byte length of the compact JSON encoding."""
    return len(
        json.dumps(
            _json_safe(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def _halve(items: list[Any]) -> list[Any]:
    """Return a deterministic half of *items* with the first and last pinned."""
    if len(items) <= 2:
        return list(items)
    sampled = list(items[0::2])
    if sampled and sampled[-1] != items[-1]:
        sampled.append(items[-1])
    return sampled


def _head_utf8(text: str, limit: int) -> str:
    """Return the longest prefix of *text* whose UTF-8 form fits *limit* bytes."""
    if limit <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")
