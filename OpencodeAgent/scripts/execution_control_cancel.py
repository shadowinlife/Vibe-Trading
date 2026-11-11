"""Live legacy process-group cancellation evidence."""

from __future__ import annotations

from execution_control_common import JsonValue, require
from execution_control_harness import Harness


def verify_live_cancel(
    h: Harness, legacy_live: dict[str, JsonValue]
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    body: dict[str, JsonValue] = legacy_live | {
        "run_key": "legacy.cancelled",
        "upstream": "https://example.invalid",
        "timeout_s": 3600,
    }
    cancel_id = str(
        h.expect(
            h.request(
                "A10", "legacy-live-cancel-start", "POST", "/strategy-runs", body
            ),
            202,
        )["vt_run_id"]
    )
    cancelled: dict[str, JsonValue] = {}
    for index in range(2):
        cancelled = h.expect(
            h.request(
                "A10",
                f"legacy-live-cancel-{index}",
                "POST",
                f"/strategy-runs/{cancel_id}/cancel",
            ),
            202,
        )
        require(
            cancelled
            == {
                "vt_run_id": cancel_id,
                "status": "cancelled",
                "stop_state": "confirmed",
            },
            "live legacy cancel",
        )
    status = h.expect(
        h.request(
            "A3", "legacy-cancelled-status", "GET", f"/execution-instances/{cancel_id}"
        ),
        200,
    )
    require(status["status"] == "cancelled", "legacy cancelled status")
    return cancelled, status


def save_legacy_ordered(h: Harness, bodies: list[dict[str, JsonValue]]) -> None:
    names = [
        "live_cancel",
        "live_cancelled",
        "restart_native_unknown",
        "restart_legacy_unknown",
    ]
    require(len(bodies) == len(names), "legacy ordered evidence count")
    h.save(
        "a10-legacy-ordered.json",
        {"sequence": names, **dict(zip(names, bodies, strict=True))},
    )
