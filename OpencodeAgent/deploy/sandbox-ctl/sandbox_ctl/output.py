"""Strict JSON emitter + the exit-code contract (docker-CLI-aligned).

stdout is machine-owned: exactly one JSON document per invocation.
stderr is human/diagnostic-only. Failure documents carry
``{"ok": false, "error": {"code", "message"}}`` so a shell-out caller
(T16's E2bSandboxBackend) can branch on the exit code alone.
"""

from __future__ import annotations

import json
import sys
from enum import IntEnum
from typing import Any


class Exit(IntEnum):
    OK = 0
    USAGE = 2  # argparse owns this value too (bad flags / missing args)
    BACKEND_UNAVAILABLE = 3  # SDK/network/gateway dead -> T16 BackendUnavailable
    SANDBOX_ABSENT = 4
    OPERATION_FAILED = 5  # route budget exhausted, pause refused, command error
    AUTH_FAILURE = 6  # bad/missing credential (API key or serve password)


ERROR_CODES = {
    Exit.USAGE: "usage",
    Exit.BACKEND_UNAVAILABLE: "backend_unavailable",
    Exit.SANDBOX_ABSENT: "sandbox_absent",
    Exit.OPERATION_FAILED: "operation_failed",
    Exit.AUTH_FAILURE: "auth_failure",
}


class CtlError(Exception):
    """A mapped, exit-coded CLI failure."""

    def __init__(self, exit_code: Exit, message: str):
        super().__init__(message)
        self.exit_code = exit_code
        self.code = ERROR_CODES[exit_code]


def emit(payload: dict[str, Any]) -> None:
    """Write one strict-JSON document to stdout."""
    json.dump(payload, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()


def diag(message: str) -> None:
    """Diagnostics go to stderr only — stdout stays parseable."""
    print(message, file=sys.stderr, flush=True)


def emit_failure(error: CtlError) -> int:
    emit({"ok": False, "error": {"code": error.code, "message": str(error)}})
    return int(error.exit_code)
