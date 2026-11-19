"""Budget-aware serialization of the backtest tool result envelope.

The same envelope is delivered to two consumers with different caps: the
in-process model loop accepts 10,000 characters, while the MCP host cuts a
single tool result at 2000 lines / 50 KiB.  A cut JSON document is not valid
JSON and the downstream consumer skips the run entirely, so an oversized
envelope is trimmed here instead of being truncated by the transport.  The
structured ``summary`` that reporters read is never removed.
"""

from __future__ import annotations

import json
from typing import Any

from src.config.limits import TOOL_RESULT_LINE_BUDGET

#: Room reserved for ``summary["omitted_fields"]`` so adding the diagnostic
#: list cannot push a payload that was trimmed to exactly the limit over it.
_OMITTED_RESERVE = 256


def _clip_summary_text(summary: dict[str, Any]) -> bool:
    """Bound string fields that cannot be dropped without losing the schema.

    Returns:
        ``True`` when at least one value was shortened.
    """
    changed = False

    def clip(key: str, limit: int) -> None:
        nonlocal changed
        value = summary.get(key)
        if isinstance(value, str) and len(value) > limit:
            summary[key] = value[:limit]
            changed = True

    clip("run_id", 128)
    for key in ("start_date", "end_date", "interval"):
        clip(key, 64)

    codes = summary.get("codes")
    if isinstance(codes, list):
        clipped_codes = []
        for code in codes:
            if isinstance(code, str) and len(code) > 64:
                clipped_codes.append(code[:64])
                changed = True
            else:
                clipped_codes.append(code)
        summary["codes"] = clipped_codes

    points = summary.get("equity_preview")
    if isinstance(points, list):
        clipped_points = []
        for point in points:
            if not isinstance(point, dict):
                clipped_points.append(point)
                continue
            clipped_point = {}
            for key, value in point.items():
                if key == "time" and isinstance(value, str) and len(value) > 64:
                    clipped_point[key] = value[:64]
                    changed = True
                else:
                    clipped_point[key] = value
            clipped_points.append(clipped_point)
        summary["equity_preview"] = clipped_points

    return changed


def serialize_result(envelope: dict[str, Any], byte_limit: int) -> str:
    """Fit ``envelope`` into one tool result without ever dropping ``summary``.

    The MCP host cuts a single result at 2000 lines / 50 KiB and hands the
    consumer an unparseable prefix, so an oversized envelope is trimmed here
    instead.  Trimming is deterministic: logs first, then the legacy artifact
    map, then the optional summary blocks (``validation``,
    ``structured_metrics``, ``warnings``, ``initial_cash``, ``title``,
    ``artifact_paths.ohlcv``), then oversized required text is clipped and
    ``equity_preview`` is thinned.  Only as a last resort are ``codes``
    shortened and the largest ``metrics`` entries dropped.  Every dropped summary block
    is named in ``summary["omitted_fields"]`` and its full value stays readable
    through ``artifact_paths`` / ``artifact_paths.run_card_json``.  ``summary``
    itself is never removed.

    Args:
        envelope: Result envelope to serialize.
        byte_limit: Maximum UTF-8 bytes of the returned JSON text.

    Returns:
        The serialized envelope, trimmed to fit whenever trimming can help.
    """

    def encode() -> str:
        return json.dumps(envelope, ensure_ascii=False)

    def fits(payload: str, reserve: int = 0) -> bool:
        return (
            len(payload.encode("utf-8")) <= max(0, byte_limit - reserve)
            and payload.count("\n") + 1 <= TOOL_RESULT_LINE_BUDGET
        )

    encoded = encode()
    if fits(encoded):
        return encoded

    if envelope.get("stdout") or envelope.get("stderr"):
        envelope["stdout"] = ""
        envelope["stderr"] = ""
        envelope["logs_omitted"] = "Read the run logs for full stdout/stderr."

    if isinstance(envelope.get("artifacts"), dict) and envelope["artifacts"]:
        envelope["artifacts"] = {}
        envelope["artifacts_omitted"] = (
            "Artifact paths exceed the result budget; inspect the run directory "
            "with read_file."
        )

    dropped: set[str] = set()
    summary = envelope.get("summary")
    if isinstance(summary, dict):
        for key in ("validation", "structured_metrics", "warnings", "initial_cash"):
            if fits(encode(), _OMITTED_RESERVE):
                break
            if key in summary:
                del summary[key]
                dropped.add(key)

        artifact_paths = summary.get("artifact_paths")
        if not fits(encode(), _OMITTED_RESERVE) and "title" in summary:
            del summary["title"]
            dropped.add("title")
        if (
            not fits(encode(), _OMITTED_RESERVE)
            and isinstance(artifact_paths, dict)
            and "ohlcv" in artifact_paths
        ):
            del artifact_paths["ohlcv"]
            dropped.add("artifact_paths.ohlcv")

        if not fits(encode(), _OMITTED_RESERVE) and _clip_summary_text(summary):
            dropped.add("text")

        points = summary.get("equity_preview")
        while (
            not fits(encode(), _OMITTED_RESERVE)
            and isinstance(points, list)
            and len(points) > 1
        ):
            count = len(points) // 2
            points = (
                [points[0]]
                if count <= 1
                else [
                    points[i * (len(points) - 1) // (count - 1)] for i in range(count)
                ]
            )
            summary["equity_preview"] = points

        codes = summary.get("codes")
        while (
            not fits(encode(), _OMITTED_RESERVE)
            and isinstance(codes, list)
            and len(codes) > 1
        ):
            codes = codes[:-1]
            summary["codes"] = codes
            dropped.add("codes")

        metrics = summary.get("metrics")
        while (
            not fits(encode(), _OMITTED_RESERVE)
            and isinstance(metrics, dict)
            and metrics
        ):
            largest = max(
                metrics,
                key=lambda key: len(json.dumps(metrics[key], ensure_ascii=False)),
            )
            del metrics[largest]
            dropped.add("metrics")

        if dropped:
            summary["omitted_fields"] = sorted(dropped)

        if not fits(encode()):
            if isinstance(summary.get("codes"), list):
                summary["codes"] = summary["codes"][:1]
            if isinstance(summary.get("equity_preview"), list):
                summary["equity_preview"] = summary["equity_preview"][:1]
            summary["metrics"] = {}
        if not fits(encode()) and "omitted_fields" in summary:
            del summary["omitted_fields"]
    return encode()
