"""Run a workflow strategy in exactly one allowlisted chat session.

The pinned spec snapshot is rendered into a fixed six-section prompt and handed
to an injected dispatcher, which creates the session and returns its id; a
later synchronous poll parses the terminal reply against the output contract.

Dispatcher contract: ``async def dispatcher(prompt: str, *, title: str,
tool_allowlist: frozenset[str]) -> str`` returning the new session id.
``tool_allowlist`` is checked with :func:`inspect.signature` before dispatch: a
dispatcher that cannot receive it raises :class:`AllowlistRequiredError` before
any session exists, because the session registry is what enforces the list.

Reader contract: ``def read_session(session_id: str) -> tuple[str, str] | None``
returning ``(status, text)`` or ``None`` while the run is live. A dispatcher
that misses ``timeout_s`` raises :class:`WorkflowTimeoutError`
(``error_kind="timeout"``); one call per run never leaves a second session.
Only the five identity fields of :class:`WorkflowHandle` are persisted, so a
rehydrated handle resolves its contract from the process-local registry and a
restart fails closed.
"""

from __future__ import annotations

import asyncio
import csv
import inspect
import io
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.strategy_library.history import RunOutcome
from src.strategy_library.paths import version_spec_path
from src.strategy_library.spec import ALLOWED_STRATEGY_TOOLS, OutputContract, StrategySpec, validate_spec

if TYPE_CHECKING:
    from src.scheduled_research.models import ScheduledResearchJob

DEFAULT_START_TIMEOUT_S = 120.0
#: Statuses meaning "keep polling"; ``active`` covers an unreported attempt.
NON_TERMINAL_STATUSES = frozenset({"pending", "running", "waiting_user", "active"})
#: Terminal statuses that must never be parsed as a successful artifact.
FAILED_STATUSES = frozenset({"failed", "cancelled", "interrupted", "error"})
#: The prompt headings, in their fixed order.
PROMPT_SECTIONS: tuple[str, ...] = (
    "## Role",
    "## Inputs",
    "## Preconditions",
    "## Steps",
    "## Output Contract",
    "## Failure Handling",
)

_FENCE_RE = re.compile(r"```[ \t]*([A-Za-z0-9_-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)
_SEPARATOR_RE = re.compile(r":?-{1,}:?")
#: Contracts for handles rehydrated from the five persisted fields; a restart
#: has none and fails closed instead of guessing a format.
_CONTRACTS: dict[str, OutputContract] = {}

__all__ = [
    "DEFAULT_START_TIMEOUT_S", "FAILED_STATUSES", "NON_TERMINAL_STATUSES", "PROMPT_SECTIONS",
    "AllowlistRequiredError", "WorkflowHandle", "WorkflowTimeoutError",
    "poll_workflow_run", "render_workflow_prompt", "start_workflow_run",
]


class AllowlistRequiredError(RuntimeError):
    """Raised when a dispatcher cannot accept ``tool_allowlist``."""


class WorkflowTimeoutError(RuntimeError):
    """Raised when the dispatcher does not return a session id in time."""

    error_kind = "timeout"


@dataclass(frozen=True)
class WorkflowHandle:
    """One in-flight workflow run; ``output_contract`` is not persisted."""

    run_id: str
    kind: str
    session_id: str | None
    run_dir: str | None
    started_at: int
    output_contract: OutputContract | None = field(default=None, repr=False, compare=False)

    def to_in_flight(self) -> dict[str, Any]:
        """Return exactly the five keys the job model persists."""
        payload = asdict(self)
        payload.pop("output_contract", None)
        return payload


@dataclass(frozen=True)
class _Parsed:
    summary: str = ""
    error: str = ""


def render_workflow_prompt(spec: StrategySpec) -> str:
    """Render a spec as the fixed six-section workflow prompt.

    Sections always appear in :data:`PROMPT_SECTIONS` order; declared steps
    keep their spec order and render one line each carrying their id, action,
    tool and output. Returns the prompt, ending with one newline.
    """
    contract = spec.output_contract
    inputs = [
        f"- {_or(item.name, f'input-{index}')} "
        f"[{'required' if item.required else 'optional'}] "
        f"source: {_or(item.source, '(not declared)')}; "
        f"if missing: {_or(item.missing_policy, 'stop and report the gap')}"
        for index, item in enumerate(spec.inputs, start=1)
    ] or ["- (none declared)"]
    preconditions = [f"- {_or(item, '(blank)')}" for item in spec.preconditions] or ["- (none declared)"]
    steps = [
        f"{index}. [{_or(step.id, f'step-{index}')}] {_or(step.action, '(no action)')} "
        f"| tool: {_or(step.tool, '(none)')} | output: {_or(step.output, '(none)')}"
        for index, step in enumerate(spec.steps, start=1)
    ] or ["1. (no steps declared)"]
    fields = ", ".join(contract.required_fields) if contract and contract.required_fields else "(none declared)"
    contract_lines = [
        f"Format: {_or(contract.format, '(not declared)') if contract else '(not declared)'}",
        f"Required fields: {fields}",
        f"Destination: {_or(contract.destination, 'session') if contract else 'session'}",
        "Return only the contract artifact; surrounding prose is a contract violation.",
    ]
    bodies = (
        [
            f'You execute the workflow strategy "{_or(spec.name, "(unnamed)")}" '
            f'for universe "{_or(spec.universe, "(not declared)")}".',
            f"Thesis: {_or(spec.thesis, '(not declared)')}",
            "Use only the tools named in the steps below and never place orders.",
        ],
        inputs,
        preconditions,
        steps,
        contract_lines,
        [_or(spec.failure_handling, "Stop at the first failed step and report which step failed.")],
    )
    blocks = ["\n".join((heading, *body)) for heading, body in zip(PROMPT_SECTIONS, bodies, strict=True)]
    return "\n\n".join(blocks) + "\n"


async def start_workflow_run(
    job: ScheduledResearchJob,
    dispatcher: Callable[..., Awaitable[str]],
    *,
    runtime_root: Path | None = None,
    timeout_s: float = DEFAULT_START_TIMEOUT_S,
) -> WorkflowHandle:
    """Start one workflow run and return its handle without waiting.

    The pinned snapshot at ``strategies/<id>/versions/<version>/strategy.json``
    is the only spec source, so a later strategy edit cannot change what an
    already-scheduled job runs. Raises :class:`AllowlistRequiredError` before
    any dispatch when the dispatcher cannot receive ``tool_allowlist`` and
    :class:`WorkflowTimeoutError` when it misses ``timeout_s``.
    """
    if not callable(dispatcher):
        raise TypeError("dispatcher must be callable")
    _require_allowlist_support(dispatcher)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise ValueError("timeout_s must be a positive number")

    spec = _load_pinned_spec(job, runtime_root)
    if gaps := validate_spec(spec):
        raise ValueError(f"workflow spec is not runnable ({len(gaps)} gap(s))")

    run_id = uuid.uuid4().hex
    prompt = render_workflow_prompt(spec)
    title = f"strategy:{job.strategy_id}:{job.strategy_version}:{run_id}"
    started_at = int(time.time() * 1000)
    try:
        session_id = await asyncio.wait_for(
            dispatcher(prompt, title=title, tool_allowlist=ALLOWED_STRATEGY_TOOLS),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError as exc:
        raise WorkflowTimeoutError(f"workflow dispatcher did not start run {run_id} within {timeout_s}s") from exc
    if not isinstance(session_id, str) or not session_id.strip():
        raise RuntimeError("workflow dispatcher returned no session id")
    _CONTRACTS[run_id] = spec.output_contract
    return WorkflowHandle(
        run_id=run_id,
        kind="workflow",
        session_id=session_id.strip(),
        run_dir=None,
        started_at=started_at,
        output_contract=spec.output_contract,
    )


def poll_workflow_run(
    handle: WorkflowHandle,
    *,
    read_session: Callable[[str], tuple[str, str] | None],
) -> RunOutcome | None:
    """Return the terminal outcome, or ``None`` while the session is live.

    ``None`` from the reader is never turned into a completion. Terminal
    failure statuses fail without parsing; a terminal success reply that does
    not satisfy the contract is a ``contract_violation``.
    """
    if not callable(read_session):
        raise TypeError("read_session must be callable")
    session_id = getattr(handle, "session_id", None)
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("workflow handle has no session id")
    snapshot = read_session(session_id)
    if snapshot is None:
        return None
    status, text = _reader_payload(snapshot)
    normalized = status.strip().lower()
    if normalized in NON_TERMINAL_STATUSES:
        return None
    cached = _CONTRACTS.pop(getattr(handle, "run_id", ""), None)
    if normalized in FAILED_STATUSES:
        return RunOutcome(
            status="failed",
            summary="workflow session ended without a result",
            error=f"workflow session {session_id} ended with status {normalized!r}",
            error_kind="session_failed",
            session_id=session_id,
        )
    contract = getattr(handle, "output_contract", None)
    if not isinstance(contract, OutputContract):
        contract = cached
    if not isinstance(contract, OutputContract):
        return _contract_failure(session_id, "the output contract is unavailable for polling")
    parsed = _parse_contract(text, contract)
    if parsed.error:
        return _contract_failure(session_id, parsed.error)
    return RunOutcome(status="completed", summary=parsed.summary, session_id=session_id)


def _require_allowlist_support(dispatcher: Callable[..., Any]) -> None:
    try:
        parameters = inspect.signature(dispatcher).parameters.values()
    except (TypeError, ValueError) as exc:
        raise AllowlistRequiredError("dispatcher signature cannot be inspected") from exc
    for parameter in parameters:
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return
        if parameter.name == "tool_allowlist" and parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY,
        ):
            return
    raise AllowlistRequiredError(
        "dispatcher must accept the keyword argument 'tool_allowlist'; "
        "refusing to create an unrestricted strategy session"
    )


def _load_pinned_spec(job: ScheduledResearchJob, runtime_root: Path | None) -> StrategySpec:
    strategy_id = getattr(job, "strategy_id", None)
    version = getattr(job, "strategy_version", None)
    for name, value in (("strategy_id", strategy_id), ("strategy_version", version)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"a workflow job needs a non-empty {name}")
    path = version_spec_path(strategy_id, version, runtime_root=runtime_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"strategy version spec is unavailable: {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("strategy version spec must be a JSON object")
    spec = StrategySpec.from_dict(payload)
    if spec.kind != "workflow":
        raise ValueError(f"strategy {strategy_id!r} is kind {spec.kind!r}, not 'workflow'")
    if spec.output_contract is None:
        raise RuntimeError("workflow spec has no output contract")
    return spec


def _reader_payload(snapshot: object) -> tuple[str, str]:
    if not isinstance(snapshot, (tuple, list)) or len(snapshot) != 2:
        raise TypeError("read_session must return (status, text) or None")
    status, text = snapshot
    if not isinstance(status, str) or not isinstance(text, (str, type(None))):
        raise TypeError("read_session must return a string status and text")
    return status, text or ""


def _contract_failure(session_id: str, message: str) -> RunOutcome:
    error = f"output contract violation: {message}"
    return RunOutcome(
        status="failed", summary="output contract violation", error=error,
        error_kind="contract_violation", session_id=session_id,
    )


def _parse_contract(text: str, contract: OutputContract) -> _Parsed:
    required = [item.strip() for item in contract.required_fields if item.strip()]
    if not required:
        return _Parsed(error="the output contract lists no required fields")
    parser = _PARSERS.get(contract.format)
    if parser is None:
        return _Parsed(error=f"unsupported output format {contract.format!r}")
    return parser(text, required)


def _parse_json(text: str, required: list[str]) -> _Parsed:
    """Parse a JSON object or list and require every field on every record."""
    try:
        data = json.loads(text.strip() if (body := _fenced(text, "json")) is None else body)
    except (json.JSONDecodeError, TypeError):
        return _Parsed(error="the session reply is not a JSON document")
    if isinstance(data, Mapping):
        records: list[Any] = [data]
    elif isinstance(data, list):
        records = data
    else:
        return _Parsed(error="the JSON reply must be an object or a list of objects")
    if not records:
        return _Parsed(error="the JSON reply contains no records")
    for record in records:
        missing = [name for name in required if not isinstance(record, Mapping) or name not in record]
        if missing:
            return _Parsed(error=f"missing required field(s): {', '.join(missing)}")
    return _Parsed(summary=f"workflow completed: json with {len(records)} record(s)")


def _parse_csv(text: str, required: list[str]) -> _Parsed:
    """Parse CSV text and require every field in the header and at least one row."""
    reader = csv.DictReader(io.StringIO(text.strip() if (body := _fenced(text, "csv")) is None else body))
    try:
        rows = list(reader)
    except csv.Error as exc:
        return _Parsed(error=f"the session reply is not valid CSV: {exc}")
    header = [cell.strip().lstrip("\ufeff") for cell in (reader.fieldnames or [])]
    if not header:
        return _Parsed(error="the CSV reply has no header")
    if missing := [name for name in required if name not in header]:
        return _Parsed(error=f"missing required field(s): {', '.join(missing)}")
    data_rows = [row for row in rows if any(str(value or "").strip() for value in row.values())]
    if not data_rows:
        return _Parsed(error="the CSV reply has no data rows")
    return _Parsed(summary=f"workflow completed: csv with {len(data_rows)} row(s)")


def _parse_markdown_table(text: str, required: list[str]) -> _Parsed:
    """Parse a pipe table and require every field in the header."""
    lines = text.splitlines()
    wanted = {_normalize_cell(name) for name in required}
    for index, line in enumerate(lines):
        cells = _row_cells(line)
        if cells is None or not wanted <= {_normalize_cell(cell) for cell in cells}:
            continue
        cursor = index + 1
        count = 0
        while cursor < len(lines):
            if _is_separator(lines[cursor]):
                cursor += 1
                continue
            if _row_cells(lines[cursor]) is None:
                break
            count += 1
            cursor += 1
        if count:
            return _Parsed(summary=f"workflow completed: markdown_table with {count} row(s)")
        return _Parsed(error="the markdown table has no data rows")
    return _Parsed(error=f"missing required field(s): {', '.join(required)}")


_PARSERS = {"json": _parse_json, "csv": _parse_csv, "markdown_table": _parse_markdown_table}


def _fenced(text: str, language: str) -> str | None:
    matches = (m for m in _FENCE_RE.finditer(text) if m.group(1).lower() in ("", language))
    return next((match.group(2).strip() for match in matches), None)


def _row_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if "|" not in stripped:
        return None
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator(line: str) -> bool:
    return bool(cells := _row_cells(line)) and all(_SEPARATOR_RE.fullmatch(c.strip()) for c in cells)


def _normalize_cell(cell: str) -> str:
    return cell.strip().strip("`*_").strip().casefold()


def _or(value: str, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback
