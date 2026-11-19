"""Contract tests for workflow strategy execution.

Every case uses a fake async dispatcher and an injected synchronous session
reader, so nothing here touches the network, a real session service, or a
broker module. The golden prompt pins the six-section rendering byte for byte;
the parse matrix proves prose is never accepted as a result.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from src.scheduled_research.models import ScheduledResearchJob
from src.strategy_library.execute_workflow import (
    AllowlistRequiredError,
    WorkflowHandle,
    WorkflowTimeoutError,
    poll_workflow_run,
    render_workflow_prompt,
    start_workflow_run,
)
from src.strategy_library.paths import version_spec_path
from src.strategy_library.spec import ALLOWED_STRATEGY_TOOLS, OutputContract, StrategySpec

_STRATEGY_ID = "momentum-1"
_VERSION = "v1-abcdef12"
_TITLE_RE = re.compile(rf"^strategy:{_STRATEGY_ID}:{_VERSION}:[0-9a-f]{{32}}$")

_SPEC: dict[str, Any] = {
    "name": "Momentum Scan",
    "kind": "workflow",
    "thesis": "Buy strength confirmed by volume.",
    "universe": "cn_a",
    "inputs": [
        {
            "name": "prices",
            "source": "get_market_data",
            "required": True,
            "missing_policy": "stop",
        },
        {
            "name": "news",
            "source": "get_stock_news",
            "required": False,
            "missing_policy": "continue",
        },
    ],
    "preconditions": ["the market is open", "prices cover 250 days"],
    "steps": [
        {"id": "load", "action": "load daily bars", "tool": "get_market_data", "output": "prices"},
        {
            "id": "rank",
            "action": "rank by 12-1 momentum",
            "tool": "factor_analysis",
            "output": "ranked list",
        },
        {
            "id": "filter",
            "action": "keep the top decile",
            "tool": "technical_indicators",
            "output": "candidates",
        },
        {
            "id": "verify",
            "action": "backtest the candidates",
            "tool": "backtest",
            "output": "metrics",
        },
    ],
    "output_contract": {
        "format": "markdown_table",
        "required_fields": ["rank", "symbol", "score"],
        "destination": "session",
    },
    "failure_handling": "Stop at the failed step and report its id.",
}

_GOLDEN_PROMPT = """\
## Role
You execute the workflow strategy "Momentum Scan" for universe "cn_a".
Thesis: Buy strength confirmed by volume.
Use only the tools named in the steps below and never place orders.

## Inputs
- prices [required] source: get_market_data; if missing: stop
- news [optional] source: get_stock_news; if missing: continue

## Preconditions
- the market is open
- prices cover 250 days

## Steps
1. [load] load daily bars | tool: get_market_data | output: prices
2. [rank] rank by 12-1 momentum | tool: factor_analysis | output: ranked list
3. [filter] keep the top decile | tool: technical_indicators | output: candidates
4. [verify] backtest the candidates | tool: backtest | output: metrics

## Output Contract
Format: markdown_table
Required fields: rank, symbol, score
Destination: session
Return only the contract artifact; surrounding prose is a contract violation.

## Failure Handling
Stop at the failed step and report its id.
"""

_THREE_ROW_TABLE = """\
Here is the screen:

| rank | symbol | score |
| --- | --- | --- |
| 1 | AAA | 9.1 |
| 2 | BBB | 8.2 |
| 3 | CCC | 7.3 |
"""


class _FakeDispatcher:
    """Records the call contract and returns a fixed session id."""

    def __init__(self, session_id: str = "sess-workflow-1") -> None:
        self.session_id = session_id
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, prompt: str, *, title: str, tool_allowlist: Any) -> str:
        self.calls.append(
            {"prompt": prompt, "title": title, "tool_allowlist": tool_allowlist}
        )
        return self.session_id


class _NoAllowlistDispatcher:
    """A dispatcher whose signature cannot receive the enforcement boundary."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, prompt: str, *, title: str) -> str:
        self.calls += 1
        return "sess-unrestricted"


class _SleepingDispatcher:
    """Never resolves inside the test's timeout."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, prompt: str, *, title: str, tool_allowlist: Any) -> str:
        self.calls += 1
        await asyncio.sleep(5)
        return "sess-late"


def _spec(**overrides: Any) -> dict[str, Any]:
    spec = copy.deepcopy(_SPEC)
    spec.update(overrides)
    return spec


def _write_spec(root: Path, spec: dict[str, Any]) -> None:
    path = version_spec_path(_STRATEGY_ID, _VERSION, runtime_root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec), encoding="utf-8")


def _job() -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id="job-1",
        prompt="",
        schedule="60000",
        source_type="strategy",
        executor_kind="strategy",
        strategy_id=_STRATEGY_ID,
        strategy_version=_VERSION,
        next_run_at=1000,
        created_at=0,
    )


def _start(root: Path, dispatcher: Any, *, timeout_s: float = 120.0) -> WorkflowHandle:
    return asyncio.run(
        start_workflow_run(
            _job(), dispatcher, runtime_root=root, timeout_s=timeout_s
        )
    )


def _started(root: Path, spec: dict[str, Any] | None = None) -> tuple[WorkflowHandle, _FakeDispatcher]:
    _write_spec(root, spec if spec is not None else _SPEC)
    dispatcher = _FakeDispatcher()
    return _start(root, dispatcher), dispatcher


def _poll(handle: WorkflowHandle, status: str, text: str):
    return poll_workflow_run(handle, read_session=lambda _: (status, text))


def test_start_returns_a_workflow_handle_and_passes_the_exact_allowlist(
    tmp_path: Path,
) -> None:
    handle, dispatcher = _started(tmp_path)

    assert len(dispatcher.calls) == 1
    call = dispatcher.calls[0]
    assert call["tool_allowlist"] == ALLOWED_STRATEGY_TOOLS
    assert isinstance(call["tool_allowlist"], frozenset)
    assert _TITLE_RE.fullmatch(call["title"])
    assert handle.kind == "workflow"
    assert handle.session_id == "sess-workflow-1"
    assert handle.run_dir is None
    assert isinstance(handle.started_at, int) and handle.started_at > 0
    assert re.fullmatch(r"[0-9a-f]{32}", handle.run_id)
    assert handle.output_contract is not None
    assert set(handle.to_in_flight()) == {
        "run_id",
        "kind",
        "session_id",
        "run_dir",
        "started_at",
    }


def test_prompt_pins_the_six_sections_and_step_order(tmp_path: Path) -> None:
    handle, dispatcher = _started(tmp_path)

    prompt = dispatcher.calls[0]["prompt"]
    assert prompt == _GOLDEN_PROMPT
    positions = [prompt.index(heading) for heading in (
        "## Role",
        "## Inputs",
        "## Preconditions",
        "## Steps",
        "## Output Contract",
        "## Failure Handling",
    )]
    assert positions == sorted(positions)
    step_lines = [line for line in prompt.splitlines() if line.startswith(("1.", "2.", "3.", "4."))]
    assert [line.split("]")[0] + "]" for line in step_lines] == [
        "1. [load]",
        "2. [rank]",
        "3. [filter]",
        "4. [verify]",
    ]
    assert handle.run_id in dispatcher.calls[0]["title"]


def test_renderer_is_stable_for_the_fixed_four_step_spec() -> None:
    first = render_workflow_prompt(StrategySpec.from_dict(_SPEC))
    second = render_workflow_prompt(StrategySpec.from_dict(_SPEC))
    assert first == second == _GOLDEN_PROMPT


def test_markdown_table_with_three_rows_completes_with_a_row_count(
    tmp_path: Path,
) -> None:
    handle, _ = _started(tmp_path)

    outcome = _poll(handle, "completed", _THREE_ROW_TABLE)

    assert outcome is not None
    assert outcome.status == "completed"
    assert "3 row" in outcome.summary
    assert outcome.error == ""
    assert outcome.session_id == handle.session_id


def test_prose_reply_is_a_contract_violation(tmp_path: Path) -> None:
    handle, _ = _started(tmp_path)

    outcome = _poll(
        handle, "completed", "The screen looks constructive; three names stand out."
    )

    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.error_kind == "contract_violation"
    assert "missing required field" in outcome.error


def test_dispatcher_without_allowlist_is_refused_before_any_call(
    tmp_path: Path,
) -> None:
    _write_spec(tmp_path, _SPEC)
    dispatcher = _NoAllowlistDispatcher()

    with pytest.raises(AllowlistRequiredError):
        _start(tmp_path, dispatcher)

    assert dispatcher.calls == 0


def test_a_none_reader_stays_running_until_the_terminal_reply(
    tmp_path: Path,
) -> None:
    handle, _ = _started(tmp_path)
    replies: list[tuple[str, str] | None] = [
        None,
        None,
        ("completed", _THREE_ROW_TABLE),
    ]
    seen: list[str] = []

    def read_session(session_id: str) -> tuple[str, str] | None:
        seen.append(session_id)
        return replies.pop(0)

    assert poll_workflow_run(handle, read_session=read_session) is None
    assert poll_workflow_run(handle, read_session=read_session) is None
    outcome = poll_workflow_run(handle, read_session=read_session)

    assert seen == [handle.session_id] * 3
    assert outcome is not None and outcome.status == "completed"


def test_non_terminal_statuses_are_not_completions(tmp_path: Path) -> None:
    handle, _ = _started(tmp_path)

    for status in ("pending", "running", "waiting_user", "active"):
        assert _poll(handle, status, _THREE_ROW_TABLE) is None


def test_dispatcher_timeout_raises_and_dispatches_once(tmp_path: Path) -> None:
    _write_spec(tmp_path, _SPEC)
    dispatcher = _SleepingDispatcher()

    with pytest.raises(WorkflowTimeoutError) as caught:
        _start(tmp_path, dispatcher, timeout_s=0.01)

    assert dispatcher.calls == 1
    assert caught.value.error_kind == "timeout"


def test_missing_snapshot_raises_without_dispatch(tmp_path: Path) -> None:
    dispatcher = _FakeDispatcher()

    with pytest.raises(RuntimeError, match="version spec is unavailable"):
        _start(tmp_path, dispatcher)

    assert dispatcher.calls == []


def test_json_contract_requires_every_required_field(tmp_path: Path) -> None:
    spec = _spec(
        output_contract={
            "format": "json",
            "required_fields": ["symbol", "score"],
            "destination": "session",
        }
    )
    handle, _ = _started(tmp_path, spec)

    happy = _poll(handle, "completed", '[{"symbol": "AAA", "score": 9.1}]')
    assert happy is not None and happy.status == "completed"
    assert "1 record" in happy.summary

    fenced = _poll(
        handle,
        "completed",
        'Report:\n```json\n{"symbol": "AAA", "score": 9.1}\n```\n',
    )
    assert fenced is not None and fenced.status == "completed"

    missing = _poll(handle, "completed", '{"symbol": "AAA"}')
    assert missing is not None and missing.status == "failed"
    assert missing.error_kind == "contract_violation"
    assert "score" in missing.error


def test_csv_contract_requires_a_header_field_and_a_data_row(tmp_path: Path) -> None:
    spec = _spec(
        output_contract={
            "format": "csv",
            "required_fields": ["symbol", "score"],
            "destination": "session",
        }
    )
    handle, _ = _started(tmp_path, spec)

    happy = _poll(handle, "completed", "symbol,score\nAAA,9.1\nBBB,8.2\n")
    assert happy is not None and happy.status == "completed"
    assert "2 row" in happy.summary

    missing = _poll(handle, "completed", "symbol\nAAA\n")
    assert missing is not None and missing.error_kind == "contract_violation"
    assert "score" in missing.error

    header_only = _poll(handle, "completed", "symbol,score\n")
    assert header_only is not None and header_only.error_kind == "contract_violation"
    assert "no data rows" in header_only.error


def test_a_failed_status_is_not_parsed_as_success(tmp_path: Path) -> None:
    handle, _ = _started(tmp_path)

    outcome = _poll(handle, "failed", _THREE_ROW_TABLE)

    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.error_kind == "session_failed"
    assert "failed" in outcome.error


def test_a_handle_without_a_contract_fails_closed() -> None:
    handle = WorkflowHandle(
        run_id="a" * 32,
        kind="workflow",
        session_id="sess-rehydrated",
        run_dir=None,
        started_at=1,
        output_contract=None,
    )

    outcome = _poll(handle, "completed", _THREE_ROW_TABLE)

    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.error_kind == "contract_violation"
    assert "unavailable" in outcome.error


def test_a_rehydrated_five_key_handle_resolves_the_started_contract(
    tmp_path: Path,
) -> None:
    handle, _ = _started(tmp_path)
    rehydrated = WorkflowHandle(**handle.to_in_flight())

    outcome = _poll(rehydrated, "completed", _THREE_ROW_TABLE)

    assert outcome is not None and outcome.status == "completed"
    assert "3 row" in outcome.summary

    again = _poll(rehydrated, "completed", _THREE_ROW_TABLE)
    assert again is not None and again.error_kind == "contract_violation"


def test_an_unsupported_contract_format_fails_closed() -> None:
    handle = WorkflowHandle(
        run_id="b" * 32,
        kind="workflow",
        session_id="sess-format",
        run_dir=None,
        started_at=1,
        output_contract=OutputContract(format="yaml", required_fields=["symbol"]),
    )

    outcome = _poll(handle, "completed", "symbol: AAA")

    assert outcome is not None and outcome.error_kind == "contract_violation"
    assert "unsupported output format" in outcome.error
