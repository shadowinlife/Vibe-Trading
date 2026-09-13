"""Pure helpers for the T12 tenancy matrix (docker-free, unit-tested).

Everything here is deterministic data shaping — sampling, summarizing and
verdict classification for ``e2e_tenancy_matrix.py`` — kept separate so
``OpencodeAgent/tests/test_tenancy_matrix.py`` can pin it without a rig.
"""

from __future__ import annotations

import json
import statistics
from typing import Any

WAKE_MEASUREMENT = "cold_start_wake_s"


def summarize_window(values: list[float]) -> dict[str, Any]:
    """Distribution stats for one measurement window (n=0 -> empty shape)."""
    if not values:
        return {"n": 0, "min": None, "median": None, "mean": None, "max": None}
    return {
        "n": len(values),
        "min": round(min(values), 3),
        "median": round(statistics.median(values), 3),
        "mean": round(statistics.fmean(values), 3),
        "max": round(max(values), 3),
    }


def summarize_rss(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Group RSS sample rows by (tenant, label) and summarize each window.

    Row shape: ``{"tenant": str, "label": str, "mem_mib": float, ...}``.
    """
    grouped: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        tenant = str(row.get("tenant", "?"))
        label = str(row.get("label", "?"))
        value = row.get("mem_mib")
        if not isinstance(value, (int, float)):
            continue
        grouped.setdefault(tenant, {}).setdefault(label, []).append(float(value))
    return {
        tenant: {label: summarize_window(values) for label, values in labels.items()}
        for tenant, labels in grouped.items()
    }


def decisions_by_tenant(payload: Any) -> dict[str, dict[str, Any]]:
    """Index one reclaim pass payload (list of decisions) by tenant id."""
    if not isinstance(payload, list):
        return {}
    return {
        str(item.get("tenant")): item
        for item in payload
        if isinstance(item, dict) and item.get("tenant") is not None
    }


def classify_loop_passes(
    passes: list[dict[str, Any]], tenant_id: str
) -> dict[str, Any]:
    """Summarize what the policy loop decided for one tenant across passes.

    Pass shape: ``{"t": float, "decisions": [<reclaim decision>, ...]}``.
    Returns counts of keep/stop verdicts, the truth sources seen, and the
    idle_ms range — the evidence behind "the loop stopped the idle tenant /
    kept the live-turn tenant".
    """
    keeps: list[dict[str, Any]] = []
    stops: list[dict[str, Any]] = []
    for entry in passes:
        for decision in entry.get("decisions") or []:
            if not isinstance(decision, dict) or decision.get("tenant") != tenant_id:
                continue
            (stops if decision.get("reclaim") else keeps).append(decision)
    idle_values = [
        int(d["idle_ms"]) for d in keeps + stops if isinstance(d.get("idle_ms"), int)
    ]
    return {
        "tenant": tenant_id,
        "passes_seen": len(passes),
        "keep_verdicts": len(keeps),
        "stop_verdicts": len(stops),
        "truth_sources": sorted(
            {str(d.get("truth_source")) for d in keeps + stops if d.get("truth_source")}
        ),
        "idle_ms_range": [min(idle_values), max(idle_values)] if idle_values else None,
        "first_stop_reason": next(
            (str(d.get("reason")) for d in stops if d.get("reason")), None
        ),
        "stop_drained": [d.get("drained") for d in stops],
    }


def wake_distribution(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    """The cold-start wake sample distribution from recorder measurements."""
    samples = [
        float(item["value"])
        for item in measurements
        if item.get("name") == WAKE_MEASUREMENT
        and isinstance(item.get("value"), (int, float))
    ]
    return {
        "samples": [round(s, 3) for s in samples],
        **summarize_window(samples),
    }


def parse_llm_usage(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """First llm_usage payload seen on a web turn (token counts for costing)."""
    for event in events:
        if event.get("event") == "llm_usage" and isinstance(event.get("data"), dict):
            return event["data"]
    return None


def sanitize_registry(payload: dict[str, Any]) -> dict[str, Any]:
    """Registry snapshot with token digests stripped (evidence hygiene)."""
    return {
        "version": payload.get("version"),
        "tenants": {
            key: {
                field: value
                for field, value in entry.items()
                if field != "token_sha256"
            }
            for key, entry in payload.get("tenants", {}).items()
            if isinstance(entry, dict)
        },
        "note": (
            "token_sha256 stripped; generated keys are scratch and stay in the "
            "0600 tenant.env files"
        ),
    }


def dumps(payload: Any) -> str:
    """Stable JSON text for evidence files."""
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n"
