"""Legacy HTTP compatibility and real restart/stop recovery scenarios."""

from __future__ import annotations

import json
from dataclasses import dataclass

from execution_control_auth import verify_auth_and_unknown_ids
from execution_control_cancel import save_legacy_ordered, verify_live_cancel
from execution_control_common import CONTRACT, JsonValue, mapping, require
from execution_control_harness import Harness
from execution_control_payloads import execute, prepare, prepare_payload, stop_payload


@dataclass(frozen=True, slots=True)
class LegacyEvidence:
    running: dict[str, JsonValue]
    legacy_status: dict[str, JsonValue]
    cancelled: dict[str, JsonValue]
    live_cancelled: dict[str, JsonValue]
    legacy_find: dict[str, JsonValue]
    restart_unknown: dict[str, JsonValue]
    unknown_cancel: dict[str, JsonValue]
    old_native: dict[str, JsonValue]


def verify_legacy(
    h: Harness, config: dict[str, JsonValue], source: str, long_source: str
) -> LegacyEvidence:
    verify_auth_and_unknown_ids(h)

    legacy_body: dict[str, JsonValue] = {
        "strategy_id": "missing-strategy",
        "strategy_version": "v1-missing01",
        "run_key": "legacy.missing",
        "upstream": "https://example.invalid",
        "timeout_s": 60,
    }
    legacy_start = h.expect(
        h.request("A11", "legacy-start", "POST", "/strategy-runs", legacy_body), 202
    )
    legacy_id = str(legacy_start["vt_run_id"])
    legacy_status = h.expect(
        h.request(
            "A11", "legacy-failed-precheck", "GET", f"/strategy-runs/{legacy_id}"
        ),
        200,
    )
    require(
        legacy_status["status"] == "failed"
        and legacy_status["error_kind"] == "strategy_not_found"
        and set(legacy_status) == {"status", "error", "error_kind"},
        "0003 legacy precheck",
    )
    h.expect(
        h.request("A11", "legacy-duplicate", "POST", "/strategy-runs", legacy_body),
        409,
        {"error": "duplicate_run_key", "vt_run_id": legacy_id},
    )
    legacy_find = h.expect(
        h.request(
            "A11",
            "legacy-run-key",
            "GET",
            "/execution-instances?dispatch_key=legacy.missing",
        ),
        200,
    )
    require(
        legacy_find["state"] == "dispatched"
        and legacy_find["remote_id"] == legacy_id
        and legacy_find["run_dir"] is None
        and legacy_find["contract_version"] == CONTRACT,
        "run_key projection",
    )
    cancelled: dict[str, JsonValue] = {}
    for index in range(2):
        cancelled = h.expect(
            h.request(
                "A10",
                f"legacy-cancel-{index}",
                "POST",
                f"/strategy-runs/{legacy_id}/cancel",
            ),
            202,
        )
        require(
            cancelled
            == {"vt_run_id": legacy_id, "status": "failed", "stop_state": "confirmed"},
            "legacy cancel idempotency",
        )
    h.expect(
        h.request(
            "A10", "legacy-cancel-missing", "POST", "/strategy-runs/missing/cancel"
        ),
        404,
        {"detail": "strategy_run_not_found"},
    )
    legacy_result = h.expect(
        h.request(
            "A11",
            "legacy-result-unavailable",
            "GET",
            f"/execution-instances/{legacy_id}/result",
        ),
        200,
    )
    require(
        legacy_result["result_status"] == "unavailable"
        and legacy_result["result"] is None,
        "legacy result contract",
    )

    for label, strategy_id, version, code, error in (
        (
            "strategy-precheck",
            "missing-strategy",
            "v1-missing01",
            404,
            "strategy_not_found",
        ),
        ("version-precheck", "fixture-main", "v1-missing01", 404, "version_not_found"),
    ):
        pre_config = config | {"strategy_id": strategy_id, "strategy_version": version}
        h.expect(
            h.request(
                "A11",
                label,
                "POST",
                "/backtest/prepare",
                prepare_payload(
                    f"precheck.{label}", f"mcp-long-{label}-1", pre_config, source
                ),
            ),
            code,
            {"error": error, "contract_version": CONTRACT},
        )

    setup_legacy = """
import json
from pathlib import Path
from src.strategy_library.store import create_draft, snapshot
root = Path('/vt-state/runtime')
artifact = create_draft({'name': 'Legacy cancellation fixture', 'kind': 'code'}, runtime_root=root)
base = root / 'strategies' / artifact.id
(base / 'code').mkdir(parents=True, exist_ok=True)
(base / 'config.json').write_bytes((root.parent / 'payload' / 'neutral.json').read_bytes())
(base / 'code' / 'signal_engine.py').write_bytes((root.parent / 'payload' / 'long.py').read_bytes())
print(json.dumps({'strategy_id': artifact.id, 'strategy_version': snapshot(artifact.id, runtime_root=root)}))
"""
    legacy_live = mapping(json.loads(h.container_python(setup_legacy)))
    live_cancel, live_cancelled = verify_live_cancel(h, legacy_live)

    live_body: dict[str, JsonValue] = legacy_live | {
        "run_key": "legacy.live",
        "upstream": "https://example.invalid",
        "timeout_s": 3600,
    }
    live_id = str(
        h.expect(
            h.request("A10", "legacy-live-start", "POST", "/strategy-runs", live_body),
            202,
        )["vt_run_id"]
    )
    running = h.expect(
        h.request("A3", "legacy-running", "GET", f"/strategy-runs/{live_id}"), 200
    )
    require(running["status"] == "running", "live legacy row not running")
    restart_key, restart_id = "restart.native", "mcp-long-restart-1"
    restart_config = config | {
        "strategy_id": "fixture-restart",
        "strategy_version": "v1-restart01",
    }
    restart_prepared = prepare(
        h, "A9", restart_key, restart_id, restart_config, long_source
    )
    h.expect(execute(h, "A9", restart_key, restart_id, restart_prepared), 202)
    before = h.docker("exec", h.cid, "ps", "-eo", "pid,pgid,args")
    (h.evidence / "processes-before-restart.txt").write_text(before, encoding="utf-8")
    h.docker("restart", "-t", "20", h.cid)
    h.wait_ready()
    after = h.docker("exec", h.cid, "ps", "-eo", "pid,pgid,args")
    (h.evidence / "processes-after-restart.txt").write_text(after, encoding="utf-8")
    require(
        restart_id in before and restart_id not in after,
        "old native worker survived restart",
    )
    old_native = h.expect(
        h.request(
            "A9", "restart-native-unknown", "GET", f"/execution-instances/{restart_id}"
        ),
        200,
    )
    require(old_native["status"] == "unknown", "restart native upgraded")
    restart_stop = stop_payload(
        restart_key, restart_id, "restart-stop", "2000-01-01T00:00:00Z"
    )
    restart_unknown = h.expect(
        h.request(
            "A9",
            "restart-stop-unknown",
            "POST",
            f"/execution-instances/{restart_id}/stop",
            restart_stop,
        ),
        202,
    )
    require(
        restart_unknown["stop_state"] == "unknown"
        and restart_unknown["evidence_kind"] is None,
        "restart bare-pid stop fabricated evidence",
    )
    replay = h.expect(
        h.request(
            "A9",
            "restart-stop-replay",
            "POST",
            f"/execution-instances/{restart_id}/stop",
            restart_stop,
        ),
        202,
    )
    require(
        replay["stop_state"] == "unknown"
        and replay["resolved_at"] == restart_unknown["resolved_at"],
        "restart unknown upgraded",
    )
    unknown_cancel: dict[str, JsonValue] = {}
    for index in range(2):
        unknown_cancel = h.expect(
            h.request(
                "A10",
                f"legacy-restart-cancel-{index}",
                "POST",
                f"/strategy-runs/{live_id}/cancel",
            ),
            202,
        )
        require(
            unknown_cancel
            == {"vt_run_id": live_id, "status": "running", "stop_state": "unknown"},
            "unobservable legacy cancel",
        )
    save_legacy_ordered(
        h, [live_cancel, live_cancelled, restart_unknown, unknown_cancel]
    )
    return LegacyEvidence(
        running,
        legacy_status,
        cancelled,
        live_cancelled,
        legacy_find,
        restart_unknown,
        unknown_cancel,
        old_native,
    )
