"""Docker-free unit tests for the T12 tenancy-matrix helpers.

Pins the pure data-shaping layer (``tenancy_lib``, the probe-output parser,
the dual-bot smoke's fail-closed gate) that the live matrix driver relies on
— the rig itself is exercised by ``deploy/e2e_tenancy_matrix.py``.
"""

from __future__ import annotations

import json

from e2e_im_checks import parse_probe_result
from tenancy_lib import (
    classify_loop_passes,
    decisions_by_tenant,
    dumps,
    parse_llm_usage,
    sanitize_registry,
    summarize_rss,
    summarize_window,
    wake_distribution,
)

# --- summarize_window / summarize_rss -----------------------------------------


def test_summarize_window_empty_shape() -> None:
    assert summarize_window([]) == {
        "n": 0,
        "min": None,
        "median": None,
        "mean": None,
        "max": None,
    }


def test_summarize_window_distribution() -> None:
    stats = summarize_window([19.7, 40.8, 20.0])
    assert stats["n"] == 3
    assert stats["min"] == 19.7
    assert stats["median"] == 20.0
    assert stats["max"] == 40.8
    assert 26.0 < stats["mean"] < 27.0


def test_summarize_rss_groups_by_tenant_and_label() -> None:
    rows = [
        {"tenant": "a", "label": "idle", "mem_mib": 1200.0},
        {"tenant": "a", "label": "idle", "mem_mib": 1240.0},
        {"tenant": "a", "label": "active-web", "mem_mib": 1450.0},
        {"tenant": "b", "label": "idle", "mem_mib": 1210.0},
        {"tenant": "b", "label": "idle", "mem_mib": "broken"},  # skipped
    ]
    summary = summarize_rss(rows)
    assert summary["a"]["idle"]["n"] == 2
    assert summary["a"]["idle"]["median"] == 1220.0
    assert summary["a"]["active-web"]["n"] == 1
    assert summary["b"]["idle"]["n"] == 1


# --- reclaim verdict classification -------------------------------------------


def _pass_entry(t: float, decisions: list[dict]) -> dict:
    return {"t": t, "status": 200, "decisions": decisions}


def test_decisions_by_tenant_indexes_and_guards() -> None:
    payload = [{"tenant": "a", "reclaim": False}, {"tenant": "b", "reclaim": True}]
    indexed = decisions_by_tenant(payload)
    assert set(indexed) == {"a", "b"}
    assert indexed["b"]["reclaim"] is True
    assert decisions_by_tenant("not-a-list") == {}


def test_classify_loop_passes_counts_keeps_and_stops() -> None:
    passes = [
        _pass_entry(
            1.0,
            [
                {
                    "tenant": "a",
                    "reclaim": False,
                    "truth_source": "engine",
                    "idle_ms": 3000,
                    "reason": "idle 3000ms < ttl",
                }
            ],
        ),
        _pass_entry(
            2.0,
            [
                {
                    "tenant": "a",
                    "reclaim": False,
                    "truth_source": "engine",
                    "idle_ms": 4000,
                    "reason": "idle 4000ms < ttl",
                },
                {
                    "tenant": "b",
                    "reclaim": True,
                    "truth_source": "engine",
                    "idle_ms": 90000,
                    "reason": "idle 90000ms >= ttl",
                    "drained": True,
                },
            ],
        ),
        _pass_entry(
            3.0,
            [
                {
                    "tenant": "b",
                    "reclaim": False,
                    "truth_source": "unavailable",
                    "reason": "not running",
                    "idle_ms": None,
                }
            ],
        ),
    ]
    a = classify_loop_passes(passes, "a")
    assert a["keep_verdicts"] == 2 and a["stop_verdicts"] == 0
    assert a["truth_sources"] == ["engine"]
    assert a["idle_ms_range"] == [3000, 4000]
    b = classify_loop_passes(passes, "b")
    assert b["stop_verdicts"] == 1 and b["keep_verdicts"] == 1
    assert b["first_stop_reason"] == "idle 90000ms >= ttl"
    assert b["stop_drained"] == [True]


# --- wake + usage + registry helpers -------------------------------------------


def test_wake_distribution_filters_by_name() -> None:
    measurements = [
        {"name": "cold_start_wake_s", "value": 19.7},
        {"name": "cold_start_wake_s", "value": 40.8},
        {"name": "im_terminal_s", "value": 21.0},
        {"name": "cold_start_wake_s", "value": "broken"},
    ]
    distribution = wake_distribution(measurements)
    assert distribution["n"] == 2
    assert distribution["samples"] == [19.7, 40.8]
    assert distribution["max"] == 40.8


def test_parse_llm_usage_first_payload() -> None:
    events = [
        {"event": "text_delta", "data": {"delta": "O"}},
        {"event": "llm_usage", "data": {"input_tokens": 64000, "output_tokens": 36}},
        {"event": "llm_usage", "data": {"input_tokens": 1}},
    ]
    usage = parse_llm_usage(events)
    assert usage == {"input_tokens": 64000, "output_tokens": 36}
    assert parse_llm_usage([{"event": "text_delta"}]) is None


def test_sanitize_registry_strips_token_digests() -> None:
    payload = {
        "version": 1,
        "tenants": {
            "a": {"tenant_id": "a", "token_sha256": "deadbeef", "host_port": 28081},
        },
    }
    sanitized = sanitize_registry(payload)
    assert "token_sha256" not in sanitized["tenants"]["a"]
    assert sanitized["tenants"]["a"]["host_port"] == 28081


def test_dumps_is_stable_json_text() -> None:
    text = dumps({"b": 1, "a": 2})
    assert json.loads(text) == {"b": 1, "a": 2}
    assert text.endswith("\n")


# --- probe output parsing -------------------------------------------------------


def test_parse_probe_result_extracts_sentinel_line() -> None:
    stdout = (
        "some container log noise\n"
        '__T12_PROBE_RESULT__{"tenant": "a", "round_trip": true, "im_terminal_s": 21.5}\n'
        "trailing noise\n"
    )
    parsed = parse_probe_result(stdout)
    assert parsed is not None
    assert parsed["round_trip"] is True
    assert parsed["im_terminal_s"] == 21.5


def test_parse_probe_result_rejects_garbage() -> None:
    assert parse_probe_result("no sentinel here") is None
    assert parse_probe_result("__T12_PROBE_RESULT__{broken json") is None


# --- dual-bot smoke fail-closed gate ---------------------------------------------


def test_dual_bot_smoke_refuses_without_credentials(monkeypatch) -> None:
    import real_dual_bot_smoke

    for key in list(monkeypatch_os_environ_keys()):
        monkeypatch.delenv(key, raising=False)
    exit_code = real_dual_bot_smoke.main(["--evidence", "/tmp/t12-unit-smoke-evidence"])
    assert exit_code == 2  # refused, nothing docker-touching was attempted


def monkeypatch_os_environ_keys() -> list[str]:
    import os

    return [key for key in os.environ if key.startswith("VT_T12_")]
