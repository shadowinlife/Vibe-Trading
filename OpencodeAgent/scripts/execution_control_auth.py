"""Authentication and unknown-identity failure checks over real HTTP."""

from __future__ import annotations

from execution_control_common import CONTRACT
from execution_control_harness import Harness
from execution_control_payloads import stop_payload


def verify_auth_and_unknown_ids(h: Harness) -> None:
    for label, code, error in (
        ("missing-auth", 401, "unauthorized"),
        ("basic-auth", 401, "unauthorized"),
        ("wrong-auth", 403, "forbidden"),
    ):
        auth = (
            "none"
            if label == "missing-auth"
            else "basic" if label == "basic-auth" else "wrong"
        )
        h.expect(
            h.request("A10", label, "GET", "/execution-instances/missing", auth=auth),
            code,
            {"detail": error},
        )
    h.expect(
        h.request("A10", "missing-id", "GET", "/execution-instances/missing"),
        404,
        {"error": "not_found", "contract_version": CONTRACT},
    )
    h.expect(
        h.request(
            "A10", "missing-result", "GET", "/execution-instances/missing/result"
        ),
        404,
        {"error": "not_found", "contract_version": CONTRACT},
    )
    h.expect(
        h.request(
            "A10",
            "missing-stop",
            "POST",
            "/execution-instances/missing/stop",
            stop_payload("missing", "missing", "missing-stop", "2000-01-01T00:00:00Z"),
        ),
        404,
        {"error": "not_found", "contract_version": CONTRACT},
    )
