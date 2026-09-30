"""Run-artifact reader: chunked, downsampled, structured JSON over run CSVs.

A backtest run writes CSV artifacts (``equity.csv``, ``trades.csv``, ...) plus
small JSON sidecars (``run_card.json``, ``progress.json``).  The generic
``read_file`` tool returns raw text, burning LLM tokens on comma-separated
noise and truncating mid-file at 50K characters.  This tool parses the
artifact once and serves structured JSON pages a frontend can consume:

* ``rows`` — offset paging over whole records with an honest ``truncated`` /
  ``next_offset`` contract, so a walk reassembles the file losslessly.
* ``downsample`` — equal-stride sampling to at most ``max_rows`` points, first
  and last row always pinned; ``offset`` is ignored (the sample spans all).
* ``meta`` — columns / total_rows / size_bytes only.

Serialized envelopes stay under :data:`_BYTE_BUDGET` by shrinking whole
records (never mid-record, never broken JSON), following the
proportional-shrink pattern of ``src/tools/_result_paging.py``.  Security:
``run_dir`` goes through ``safe_run_dir``, artifact names through a fixed
whitelist, and the resolved path is re-verified inside ``run_dir``.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from src.agent.tools import BaseTool
from src.tools.path_utils import safe_run_dir

# Serialized-envelope budget in characters. The opencode harness truncates MCP
# tool results at 50KB by default (image config raises it to 256KB); 120K keeps
# a full page intact under the raised limit while leaving headroom for framing.
_BYTE_BUDGET = 120_000

# Artifact whitelist: name -> path relative to run_dir.
_CSV_ARTIFACTS: dict[str, str] = {
    "equity": "artifacts/equity.csv",
    "trades": "artifacts/trades.csv",
    "metrics": "artifacts/metrics.csv",
    "positions": "artifacts/positions.csv",
    "target_positions": "artifacts/target_positions.csv",
}
_JSON_ARTIFACTS: dict[str, str] = {
    "run_card": "run_card.json",
    "progress": "progress.json",
}
_OHLCV_PREFIX = "ohlcv:"

_VALID_FORMATS = ("rows", "downsample", "meta")
_MAX_ROWS_CEILING = 5000


def _error(error: str, hint: str) -> str:
    """Serialize the FE error envelope: ``{"ok": false, "error", "hint"}``."""
    return json.dumps({"ok": False, "error": error, "hint": hint}, ensure_ascii=False)


def _artifact_whitelist_hint() -> str:
    """Return the hint text listing every accepted artifact name."""
    names = ", ".join([*_CSV_ARTIFACTS, f"{_OHLCV_PREFIX}<CODE>", *_JSON_ARTIFACTS])
    return f"artifact must be one of: {names}."


def _validate_ohlcv_code(code: str) -> None:
    """Reject an ``ohlcv:<CODE>`` suffix that could escape the artifacts dir.

    Args:
        code: Symbol code after the prefix, e.g. ``600519.SH``.

    Raises:
        ValueError: If the code is empty or carries path separators, parent
            references, or a leading dot.
    """
    if not code or not code.strip():
        raise ValueError("ohlcv: requires a non-empty symbol code")
    if "/" in code or "\\" in code or "\x00" in code:
        raise ValueError(f"ohlcv code {code!r} must not contain path separators")
    if code.startswith("."):
        raise ValueError(f"ohlcv code {code!r} must not start with a dot")
    if ".." in code:
        raise ValueError(f"ohlcv code {code!r} must not contain '..'")


def _resolve_artifact(run_root: Path, artifact: str) -> Path:
    """Map a whitelisted artifact name to a path inside ``run_root``.

    Args:
        run_root: Resolved, allowed run directory.
        artifact: Whitelist name (``equity``, ``ohlcv:<CODE>``, ``run_card``, ...).

    Returns:
        Resolved path guaranteed to sit inside ``run_root``.

    Raises:
        ValueError: If the name is not whitelisted, an ``ohlcv:`` code is
            malformed, or the path escapes ``run_root`` (e.g. via a symlink).
    """
    if artifact in _CSV_ARTIFACTS or artifact in _JSON_ARTIFACTS:
        relative = _CSV_ARTIFACTS.get(artifact) or _JSON_ARTIFACTS[artifact]
    elif artifact.startswith(_OHLCV_PREFIX):
        code = artifact[len(_OHLCV_PREFIX) :]
        _validate_ohlcv_code(code)
        relative = f"artifacts/ohlcv_{code}.csv"
    else:
        raise ValueError(f"unknown artifact {artifact!r}")

    resolved = (run_root / relative).resolve()
    if not resolved.is_relative_to(run_root):
        raise ValueError(f"artifact {artifact!r} resolves outside run_dir")
    return resolved


def _coerce_value(raw: str) -> Any:
    """Coerce one CSV cell to int / float / None / str.

    Integer-looking text becomes ``int``, finite float-looking text becomes
    ``float``, empty or non-finite values (``nan``/``inf`` in any spelling
    ``float()`` accepts) become ``None``, anything else stays a string.
    Underscored numerics (``1_000``) stay strings because ``int()``/``float()``
    would silently accept them.

    Args:
        raw: Cell text as read from the CSV file.

    Returns:
        The coerced JSON-safe value.
    """
    text = raw.strip()
    if not text:
        return None
    if "_" not in text:
        try:
            return int(text)
        except ValueError:
            pass
        try:
            value = float(text)
        except ValueError:
            return raw
        return value if math.isfinite(value) else None
    return raw


def _read_csv(path: Path) -> tuple[list[str], list[list[Any]]]:
    """Parse a CSV artifact into columns plus coerced, header-aligned rows.

    Args:
        path: CSV file to read.

    Returns:
        ``(columns, rows)``; each row has exactly ``len(columns)`` cells
        (short rows padded with ``None``, extra trailing cells dropped).
        An empty file yields ``([], [])``.
    """
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return [], []
        width = len(header)
        rows: list[list[Any]] = []
        for record in reader:
            if not record:
                continue
            aligned = record[:width] + [""] * (width - len(record))
            rows.append([_coerce_value(cell) for cell in aligned])
    return header, rows


def _project(columns: list[str], rows: list[list[Any]], requested: list[str]) -> tuple[list[str], list[list[Any]]]:
    """Project rows onto the requested columns, preserving request order.

    Args:
        columns: Full header of the artifact.
        rows: Parsed rows aligned to ``columns``.
        requested: Column names to keep.

    Returns:
        ``(requested, projected_rows)``.

    Raises:
        ValueError: If a requested name is not in ``columns``; the message
            lists the valid columns.
    """
    unknown = [name for name in requested if name not in columns]
    if unknown:
        raise ValueError(f"unknown column(s) {unknown}; valid columns: {columns}")
    indices = [columns.index(name) for name in requested]
    return list(requested), [[row[i] for i in indices] for row in rows]


def _downsample_indices(total: int, max_points: int) -> tuple[list[int], int]:
    """Pick equal-stride sample indices with the first and last row pinned.

    Args:
        total: Number of source rows.
        max_points: Target sample size (at least 2 so both ends stay pinned).

    Returns:
        ``(indices, stride)``.  When ``total <= max_points`` every index is
        returned with stride 1.
    """
    if total <= 0:
        return [], 1
    if max_points >= total:
        return list(range(total)), 1
    if max_points < 2:
        # Both endpoints outrank the point cap: a one-point "sample" could
        # never pin first AND last, so the floor is two.
        return [0, total - 1], max(1, total - 1)
    stride = -(-(total - 1) // (max_points - 1))  # ceil division
    indices = list(range(0, total, stride))
    if indices[-1] != total - 1:
        indices.append(total - 1)
    return indices, stride


def _serialize(envelope: dict[str, Any]) -> str:
    """Serialize an envelope the way every response in this tool does."""
    return json.dumps(envelope, ensure_ascii=False)


def _fit_rows_payload(
    base: dict[str, Any],
    page: list[list[Any]],
    offset: int,
    total_rows: int,
    budget: int,
) -> str:
    """Serialize a rows-mode page, shrinking whole rows until it fits budget.

    Mirrors ``_result_paging.fit_records``: shrink proportionally to the
    overflow (then one further) so wide records still converge, keeping
    ``returned_rows`` / ``truncated`` / ``next_offset`` honest — a shrunk page
    always reports ``truncated: true`` with the resume offset, so the tail is
    never dropped silently.

    Args:
        base: Envelope skeleton without the row-page fields.
        page: Rows selected for this page (offset/clamped/projected already).
        offset: Row index the page starts at.
        total_rows: Total data rows in the artifact.
        budget: Character budget for the serialized envelope.

    Returns:
        The serialized envelope; valid JSON even when a single row alone
        exceeds the budget.
    """
    count = len(page)
    while True:
        rows = page[:count]
        returned = len(rows)
        truncated = offset + returned < total_rows
        envelope = {
            **base,
            "rows": rows,
            "returned_rows": returned,
            "truncated": truncated,
            "next_offset": offset + returned if truncated else None,
        }
        payload = _serialize(envelope)
        if len(payload) <= budget or count <= 1:
            return payload
        count = max(1, min(count - 1, int(count * budget / len(payload))))


def _fit_downsample_payload(base: dict[str, Any], rows: list[list[Any]], max_points: int, budget: int) -> str:
    """Serialize a downsample envelope, re-striding to fewer points if needed.

    Args:
        base: Envelope skeleton without ``rows`` / ``downsample`` fields.
        rows: Full set of parsed (and projected) source rows.
        max_points: Requested sample size cap.
        budget: Character budget for the serialized envelope.

    Returns:
        The serialized envelope, first and last source row pinned.
    """
    total = len(rows)
    target = max_points
    while True:
        indices, stride = _downsample_indices(total, target)
        sample = [rows[i] for i in indices]
        envelope = {
            **base,
            "rows": sample,
            "returned_rows": len(sample),
            "truncated": False,
            "next_offset": None,
            "downsample": {
                "stride": stride,
                "pinned_last": True,
                "algorithm": "every-nth",
                "total_rows": total,
            },
        }
        payload = _serialize(envelope)
        if len(payload) <= budget or target <= 2:
            return payload
        target = max(2, min(target - 1, int(target * budget / len(payload))))


def _read_json_artifact(path: Path, artifact: str, run_dir: str) -> str:
    """Parse and envelope a small JSON artifact (run_card / progress).

    Args:
        path: Resolved JSON file inside the run directory.
        artifact: Whitelist name for the response envelope.
        run_dir: Caller-supplied run directory, echoed back.

    Returns:
        Serialized ``{"artifact", "run_dir", "json", "size_bytes"}`` envelope,
        or the error envelope when the file is corrupt.
    """
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _error(
            f"{artifact} is not valid JSON: {exc}",
            f"Delete or regenerate {path.name}; this tool never returns partial JSON.",
        )
    return _serialize({"artifact": artifact, "run_dir": run_dir, "json": parsed, "size_bytes": path.stat().st_size})


def read_run_artifact(
    run_dir: str,
    artifact: str,
    format: str = "rows",
    offset: int = 0,
    max_rows: int = 1000,
    columns: list[str] | None = None,
) -> str:
    """Read one run artifact as structured, budget-bounded JSON.

    Args:
        run_dir: Run directory; must sit inside an allowed run root.
        artifact: Whitelist name — ``equity``/``trades``/``metrics``/
            ``positions``/``target_positions`` (CSVs under ``artifacts/``),
            ``ohlcv:<CODE>``, ``run_card``, or ``progress``.
        format: ``rows`` (offset paging), ``downsample`` (equal-stride sample,
            first+last pinned; ``offset`` ignored) or ``meta`` (shape only).
            JSON artifacts return their parsed object regardless of format.
        offset: First row index for ``rows`` mode; negative values refused.
        max_rows: Page/sample size, clamped to ``[1, 5000]``.
        columns: Optional projection applied before budget fitting; an unknown
            name is refused with the valid column list.

    Returns:
        JSON string.  Success envelopes carry no ``ok``/``status`` field;
        failures are ``{"ok": false, "error": ..., "hint": ...}``.
    """
    if format not in _VALID_FORMATS:
        return _error(f"unknown format {format!r}", f"format must be one of: {', '.join(_VALID_FORMATS)}.")

    try:
        run_root = safe_run_dir(run_dir)
    except ValueError as exc:
        return _error(str(exc), "Pass the run_dir a backtest/tool call returned.")

    try:
        path = _resolve_artifact(run_root, artifact)
    except ValueError as exc:
        return _error(str(exc), _artifact_whitelist_hint())

    if not path.is_file():
        return _error(
            f"artifact {artifact!r} not found at {path}",
            "The run may not have produced it yet — check the backtest result's "
            "'artifacts' map, or use format='meta' on an artifact that exists.",
        )

    if artifact in _JSON_ARTIFACTS:
        return _read_json_artifact(path, artifact, run_dir)

    try:
        offset = int(offset)
        max_rows = int(max_rows)
    except (TypeError, ValueError):
        return _error("offset and max_rows must be integers", "Omit them for the defaults (offset=0, max_rows=1000).")
    if offset < 0:
        return _error(f"offset must be >= 0, got {offset}", "Start at offset=0 and follow next_offset.")
    max_rows = max(1, min(max_rows, _MAX_ROWS_CEILING))

    try:
        header, rows = _read_csv(path)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return _error(f"failed to read {artifact}: {exc}", "The file may be corrupt or not a UTF-8 CSV artifact.")
    if columns is not None:
        try:
            header, rows = _project(header, rows, list(columns))
        except ValueError as exc:
            return _error(str(exc), "Pass only column names from the artifact header, or omit 'columns'.")

    total_rows = len(rows)
    base: dict[str, Any] = {"artifact": artifact, "run_dir": run_dir, "columns": header}
    budget = _BYTE_BUDGET

    if format == "meta":
        return _serialize({**base, "total_rows": total_rows, "size_bytes": path.stat().st_size})

    if format == "downsample":
        return _fit_downsample_payload({**base, "total_rows": total_rows, "offset": 0}, rows, max_rows, budget)

    # rows mode
    page = rows[offset : offset + max_rows] if offset < total_rows else []
    envelope_base = {**base, "total_rows": total_rows, "offset": offset, "downsample": None}
    return _fit_rows_payload(envelope_base, page, offset, total_rows, budget)


class RunArtifactTool(BaseTool):
    """Chunked/downsampled reader for backtest run artifacts."""

    name = "read_run_artifact"
    description = (
        "Read a backtest run artifact as structured JSON: paged rows "
        "(format='rows', follow next_offset), an equal-stride chart sample "
        "with first+last pinned (format='downsample', offset ignored), or "
        "shape only (format='meta'). Artifacts: equity, trades, metrics, "
        "positions, target_positions, ohlcv:<CODE>, run_card, progress. "
        "Values arrive typed (int/float/null/string), never as raw CSV text, "
        "and every page stays within a bounded byte budget."
    )
    parameters = {
        "type": "object",
        "properties": {
            "run_dir": {"type": "string", "description": "Path to the run directory"},
            "artifact": {
                "type": "string",
                "description": (
                    "Artifact name: equity | trades | metrics | positions | "
                    "target_positions | ohlcv:<CODE> (e.g. ohlcv:600519.SH) | "
                    "run_card | progress"
                ),
            },
            "format": {
                "type": "string",
                "enum": list(_VALID_FORMATS),
                "description": (
                    "rows: offset paging over whole records; downsample: "
                    "equal-stride sample with first+last row pinned (offset "
                    "ignored); meta: columns/total_rows/size_bytes only"
                ),
            },
            "offset": {
                "type": "integer",
                "description": "First row index (rows mode only, default 0)",
            },
            "max_rows": {
                "type": "integer",
                "description": "Page/sample size, clamped to [1, 5000] (default 1000)",
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional column projection; unknown names are refused with the valid list",
            },
        },
        "required": ["run_dir", "artifact"],
    }
    repeatable = True
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        """Execute the artifact read.

        Args:
            **kwargs: Must include ``run_dir`` and ``artifact``; optional
                ``format``, ``offset``, ``max_rows``, ``columns``.

        Returns:
            JSON string envelope (see :func:`read_run_artifact`).
        """
        return read_run_artifact(
            run_dir=kwargs["run_dir"],
            artifact=kwargs["artifact"],
            format=kwargs.get("format", "rows"),
            offset=kwargs.get("offset", 0),
            max_rows=kwargs.get("max_rows", 1000),
            columns=kwargs.get("columns"),
        )
