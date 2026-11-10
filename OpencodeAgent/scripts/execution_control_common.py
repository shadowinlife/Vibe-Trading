"""Shared typed values, fixed contract constants and small pure helpers."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
CONTRACT: Final = "mcp-execution-control-v1"
TOKEN: Final = "dummy-execution-control-token"
PASSWORD: Final = "dummy-opencode-password"
RUN_ROOT: Final = Path("/vt-state/runtime/runs")
CANDIDATE_ID: Final = "academic_strev"
REGISTRY_DIGEST: Final = (
    "d65ae6e5360e6d61462c863d43dfe9e8a733606362c7cc164a93da31531c2a58"
)
SUMMARY_KEYS: Final = {
    "run_id",
    "metrics",
    "equity_preview",
    "codes",
    "start_date",
    "end_date",
    "interval",
    "artifact_paths",
}
RESULT_KEYS: Final = {
    "remote_id",
    "status",
    "result_status",
    "result",
    "progress",
    "has_more",
    "contract_version",
}
FACTOR_KEYS: Final = {
    "remote_id",
    "dispatch_key",
    "registry_digest",
    "tool_schema_version",
    "contract_version",
    "status",
    "completed",
    "total",
    "candidates",
    "quality_bounds",
    "artifacts",
    "artifact_paths",
}
CSVS: Final = {
    "artifacts/metrics.csv",
    "artifacts/equity.csv",
    "artifacts/trades.csv",
    "artifacts/positions.csv",
}


class CheckFailure(Exception):
    """One observed response or side effect contradicts the frozen contract."""


@dataclass(frozen=True, slots=True)
class Reply:
    sequence: int
    status: int
    body: JsonValue
    raw: str


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def mapping(value: JsonValue) -> dict[str, JsonValue]:
    require(
        isinstance(value, dict), f"expected JSON object, got {type(value).__name__}"
    )
    return value


def canonical(value: JsonValue) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def command(
    args: list[str], *, data: bytes | None = None, timeout: int = 360
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        args, input=data, capture_output=True, timeout=timeout, check=False
    )
