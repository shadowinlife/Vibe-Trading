"""Ordered live stop evidence for a native process group."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from execution_control_common import CONTRACT, JsonValue, require
from execution_control_harness import Harness
from execution_control_payloads import execute, prepare, stop_payload


@dataclass(frozen=True, slots=True)
class StopEvidence:
    first_unknown: dict[str, JsonValue]
    stop_id: str
    stopped: dict[str, JsonValue]
    absent: dict[str, JsonValue]


def _stop_rows(h: Harness) -> list[dict[str, JsonValue]]:
    with sqlite3.connect(h.state / "runtime/strategy_runs.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT stop_key,state,evidence_kind,requested_at,resolved_at FROM execution_stops "
            "WHERE stop_key IN ('stop-observe','stop-confirmed') ORDER BY requested_at,stop_key"
        ).fetchall()
    return [dict(row) for row in rows]


def verify_confirmed_stop(
    h: Harness, config: dict[str, JsonValue], long_source: str
) -> StopEvidence:
    stop_config = config | {
        "strategy_id": "fixture-stop",
        "strategy_version": "v1-stop0001",
    }
    unknown_key, unknown_id = "stop.unknown", "mcp-long-unknown-1"
    prepare(h, "A2", unknown_key, unknown_id, stop_config, long_source)
    unknown_body = stop_payload(
        unknown_key, unknown_id, "stop-unknown", "2000-01-01T00:00:00Z"
    )
    first_unknown = h.expect(
        h.request(
            "A2",
            "unknown-stop",
            "POST",
            f"/execution-instances/{unknown_id}/stop",
            unknown_body,
        ),
        202,
    )
    require(
        first_unknown["stop_state"] == "unknown"
        and first_unknown["evidence_kind"] is None,
        "unobservable stop upgraded",
    )
    unknown_replay = h.expect(
        h.request(
            "A2",
            "unknown-replay",
            "POST",
            f"/execution-instances/{unknown_id}/stop",
            unknown_body,
        ),
        202,
    )
    require(
        unknown_replay["stop_state"] == "unknown"
        and unknown_replay["resolved_at"] == first_unknown["resolved_at"]
        and unknown_replay["replayed"] is True,
        "unknown not final",
    )

    dispatch_key, remote_id = "user:1:job:9:2", "mcp-long-stop-1"
    prepared = prepare(h, "A9", dispatch_key, remote_id, stop_config, long_source)
    h.expect(execute(h, "A9", dispatch_key, remote_id, prepared), 202)
    observe = stop_payload(
        dispatch_key, remote_id, "stop-observe", "2100-01-01T00:00:00Z"
    )
    owned = h.expect(
        h.request(
            "A9",
            "live-stop-owned",
            "POST",
            f"/execution-instances/{remote_id}/stop",
            observe,
        ),
        202,
    )
    require(
        owned["stop_state"] == "confirmed"
        and owned["evidence_kind"] == "terminal_record"
        and owned["replayed"] is False,
        "owned live handle did not confirm from the terminal record",
    )
    terminal = h.wait_terminal("A9", remote_id)
    require(
        terminal["status"] in {"completed", "cancelled"},
        f"unexpected stop terminal {terminal}",
    )

    body = stop_payload(
        dispatch_key, remote_id, "stop-confirmed", "2100-01-01T00:00:00Z"
    )
    stopped = h.expect(
        h.request(
            "A9",
            "confirmed-stop",
            "POST",
            f"/execution-instances/{remote_id}/stop",
            body,
        ),
        202,
    )
    require(
        stopped["stop_state"] == "confirmed"
        and stopped["evidence_kind"] == "terminal_record"
        and stopped["replayed"] is True,
        "earlier confirmed stop not replayed",
    )
    replay = h.expect(
        h.request(
            "A9",
            "confirmed-replay",
            "POST",
            f"/execution-instances/{remote_id}/stop",
            body,
        ),
        202,
    )
    require(
        replay == stopped,
        "confirmed stop changed on replay",
    )
    absent = h.expect(
        h.request(
            "A3", "absent", "GET", f"/execution-instances?dispatch_key={dispatch_key}"
        ),
        200,
    )
    require(
        absent["state"] == "absent"
        and absent["absence_evidence"] == "terminal_record"
        and absent["last_status"] == terminal["status"]
        and absent["contract_version"] == CONTRACT,
        "absent shape",
    )
    rows = _stop_rows(h)
    require(
        len(rows) == 1
        and rows[0]["stop_key"] == "stop-observe"
        and rows[0]["state"] == "confirmed"
        and rows[0]["evidence_kind"] == "terminal_record",
        f"stop order {rows}",
    )
    require(
        rows[0]["requested_at"] <= rows[0]["resolved_at"],
        "requested not before evidence",
    )
    h.save(
        "a9-stop-ordered.json",
        {
            "http_order": [owned, terminal, stopped],
            "ledger_order": rows,
            "absent": absent,
        },
    )
    return StopEvidence(first_unknown, remote_id, stopped, absent)
