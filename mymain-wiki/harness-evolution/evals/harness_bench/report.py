"""Benchmark report schema loader, builder and validator.

The report contract lives in ``report_schema.json``. Shape in one paragraph:
``metrics`` is a table with one row per (benchmark, metric) and every row is
a PERFORMANCE metric (higher is better); ``total_cost`` is the dedicated
cost row and is never a row-level performance metric; ``skip_markers``
discloses degraded benchmarks; ``provenance`` pins the run (harness id,
parity_spec hash, git commit, timestamp, seeds used).

``validate_report`` raises ``ReportValidationError`` naming the offending
field path (e.g. ``metrics[0].value``) on any violation.

CLI usage (from ``agent/``):

    python -m src.evals.harness_bench.report --validate some_report.json

Exit codes: 0 valid, 1 invalid (field names on stderr), 2 load error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

PKG_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = PKG_DIR / "report_schema.json"
PARITY_SPEC_PATH = PKG_DIR / "parity_spec.json"

REPORT_VERSION = "1.0"

#: Benchmarks may be skipped, but a skipped benchmark must not simultaneously
#: carry performance rows — the two sections are mutually exclusive per id.
_SKIP_DECISIONS = ("excluded_from_adjudication", "one_sided_measurement")


class ReportValidationError(ValueError):
    """Raised when a report violates the schema; message names the field."""


def load_schema() -> dict[str, Any]:
    """Return the parsed report JSON schema."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def parity_spec_sha256() -> str:
    """sha256 hex digest of the committed parity_spec.json."""
    return hashlib.sha256(PARITY_SPEC_PATH.read_bytes()).hexdigest()


def current_git_commit() -> str:
    """Best-effort HEAD commit of this checkout; 'unknown' when unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(PKG_DIR),
            check=False,
        )
        commit = out.stdout.strip()
        if out.returncode == 0 and len(commit) >= 7:
            return commit
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _field_path(error: jsonschema.ValidationError) -> str:
    """Dotted field path for a jsonschema error ('metrics[0].value' style)."""
    parts: list[str] = []
    for element in error.absolute_path:
        if isinstance(element, int):
            parts.append(f"[{element}]")
        else:
            parts.append(f".{element}" if parts else str(element))
    return "".join(parts) or "<root>"


def validate_report(report_dict: Any) -> None:
    """Validate a report dict; raise ``ReportValidationError`` naming fields.

    Structural checks come from ``report_schema.json`` (jsonschema); the
    semantic checks below catch contradictions the schema cannot express.
    """
    if not isinstance(report_dict, dict):
        raise ReportValidationError("invalid field: <root> (report must be an object)")
    try:
        jsonschema.validate(report_dict, load_schema())
    except jsonschema.ValidationError as exc:
        path = _field_path(exc)
        raise ReportValidationError(f"invalid field: {path} ({exc.message})") from exc

    metrics = report_dict["metrics"]
    seen_rows: set[tuple[str, str]] = set()
    for index, row in enumerate(metrics):
        key = (row["benchmark"], row["metric"])
        if key in seen_rows:
            raise ReportValidationError(
                f"invalid field: metrics[{index}] "
                f"(duplicate (benchmark, metric) row {key!r})"
            )
        seen_rows.add(key)

    skipped = {marker["benchmark"] for marker in report_dict["skip_markers"]}
    measured = {row["benchmark"] for row in metrics}
    overlap = sorted(skipped & measured)
    if overlap:
        raise ReportValidationError(
            f"invalid field: skip_markers (benchmarks {overlap!r} are both "
            "skipped and measured; a degraded benchmark cannot carry rows)"
        )


def build_report(
    harness_id: str,
    metrics: list[dict[str, Any]],
    total_cost_usd: float,
    skip_markers: list[dict[str, Any]] | None = None,
    seeds: dict[str, list[int]] | None = None,
    git_commit: str | None = None,
    generated_at: str | None = None,
    parity_spec_hash: str | None = None,
    cost_note: str = "",
) -> dict[str, Any]:
    """Assemble a schema-conformant report dict (then validates it).

    Args:
        harness_id: Harness under measurement.
        metrics: Performance rows ``{benchmark, metric, value[, seeds]}``.
        total_cost_usd: The total-cost row value (never a performance row).
        skip_markers: Degraded-mode disclosures, if any.
        seeds: Seeds used per benchmark, for provenance.
        git_commit: Provenance commit (default: current HEAD or 'unknown').
        generated_at: ISO timestamp (default: now, UTC).
        parity_spec_hash: Override for the parity spec digest (tests).
        cost_note: Optional disclosure attached to the cost row.
    """
    total_cost: dict[str, Any] = {
        "value": round(float(total_cost_usd), 6),
        "currency": "USD",
    }
    if cost_note:
        total_cost["note"] = cost_note
    report_dict: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "provenance": {
            "harness_id": harness_id,
            "parity_spec_sha256": parity_spec_hash or parity_spec_sha256(),
            "git_commit": git_commit or current_git_commit(),
            "generated_at": generated_at
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seeds": {k: list(v) for k, v in sorted((seeds or {}).items())},
        },
        "metrics": list(metrics),
        "total_cost": total_cost,
        "skip_markers": list(skip_markers or []),
    }
    validate_report(report_dict)
    return report_dict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a harness_bench report.")
    parser.add_argument("--validate", metavar="REPORT_JSON", required=True)
    args = parser.parse_args(argv)
    try:
        report_dict = json.loads(Path(args.validate).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot load report: {exc}", file=sys.stderr)
        return 2
    try:
        validate_report(report_dict)
    except ReportValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("report valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
