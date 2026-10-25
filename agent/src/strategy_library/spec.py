"""Strategy spec contract, deterministic gap report and exact tool allowlist.

A strategy spec is the reviewable artifact the library stores and replays.
:func:`validate_spec` is the deterministic precheck: it reads the spec only —
no model call, no code parsing, no execution — so the same spec always yields
the same gaps in the same order. The review gate layers a model-based rubric on
top; this module stays purely structural so its result is reproducible.

Tools are gated by an exact allowlist (:data:`ALLOWED_STRATEGY_TOOLS`), never a
prefix rule. :data:`FORBIDDEN_TOOL_PREFIXES` is a runtime backstop for broker
and connector tools: a strategy may never reach an order path even if a future
allowlist edit adds such a name by mistake.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

# Exactly the local BaseTool names a strategy may call. Every entry is a
# registered tool name, so a renamed or removed tool fails closed here instead
# of failing mid-run. Broker write tools (trading_*), swarm orchestration and
# file/shell tools are deliberately absent.
ALLOWED_STRATEGY_TOOLS: frozenset[str] = frozenset(
    {
        "search_symbol",
        "get_market_data",
        "technical_indicators",
        "get_fundamentals",
        "get_financial_statements",
        "get_stock_news",
        "get_fund_flow",
        "get_sector_info",
        "get_northbound_flow",
        "get_margin_trading",
        "get_research_reports",
        "get_sec_filings",
        "web_search",
        "read_url",
        "backtest",
        "paper_trade_step",
        "quantlib_call",
        "factor_analysis",
        "alpha_zoo",
        "list_strategies",
        "get_strategy_evidence",
    }
)

# Defense in depth on top of the exact allowlist: broker, etoro and connector
# tools are order-placing or account-mutating surfaces, so they never run
# inside a strategy regardless of how the allowlist evolves.
FORBIDDEN_TOOL_PREFIXES: tuple[str, ...] = ("trading_", "etoro_", "connector_")

SPEC_KINDS: frozenset[str] = frozenset({"code", "workflow"})
OUTPUT_CONTRACT_FORMATS: frozenset[str] = frozenset({"markdown_table", "json", "csv"})
MIN_WORKFLOW_STEPS = 3


@dataclass(frozen=True)
class Gap:
    """One deterministic defect in a strategy spec.

    Attributes:
        code: Stable machine-readable identifier; callers branch on this.
        field: Location inside the spec, e.g. ``steps[2].tool``.
        message: Human-readable description of the defect.
        fix_hint: What to change so the gap clears.
    """

    code: str
    field: str
    message: str = ""
    fix_hint: str = ""


@dataclass(frozen=True)
class SpecInput:
    """One declared input and the policy for a missing value."""

    name: str = ""
    source: str = ""
    required: bool = True
    missing_policy: str = ""


@dataclass(frozen=True)
class SpecStep:
    """One ordered action of a workflow spec."""

    id: str = ""
    action: str = ""
    tool: str = ""
    output: str = ""


@dataclass(frozen=True)
class OutputContract:
    """How a workflow's final answer must be shaped and delivered."""

    format: str = ""
    required_fields: list[str] = field(default_factory=list)
    destination: str = ""


@dataclass(frozen=True)
class StrategySpec:
    """Reviewable description of a code or workflow strategy.

    ``kind="code"`` points at a backtest run directory validated at promotion
    time; ``kind="workflow"`` is a multi-step research plan. The spec itself
    never carries executable code.
    """

    name: str = ""
    kind: str = ""
    thesis: str = ""
    universe: str = ""
    inputs: list[SpecInput] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    steps: list[SpecStep] = field(default_factory=list)
    output_contract: OutputContract | None = None
    failure_handling: str = ""
    schedule_hint: str = ""
    evidence_refs: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StrategySpec:
        """Coerce a JSON-shaped mapping into a spec, tolerating absent keys.

        Malformed entries are kept as empty values rather than dropped, so
        validation reports the defect instead of silently accepting a partial
        draft. Unknown keys are ignored.

        Args:
            data: Mapping shaped like the spec, typically a model-drafted JSON
                object.

        Returns:
            A spec with every field populated.
        """
        contract = data.get("output_contract")
        return cls(
            name=_text(data.get("name")),
            kind=_text(data.get("kind")),
            thesis=_text(data.get("thesis")),
            universe=_text(data.get("universe")),
            inputs=[
                _spec_input(item) for item in _items(data.get("inputs"))
            ],
            preconditions=_texts(data.get("preconditions")),
            steps=[_spec_step(item) for item in _items(data.get("steps"))],
            output_contract=_output_contract(contract)
            if isinstance(contract, Mapping)
            else None,
            failure_handling=_text(data.get("failure_handling")),
            schedule_hint=_text(data.get("schedule_hint")),
            evidence_refs=_texts(data.get("evidence_refs")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-shaped dict, the canonical input for spec hashing."""
        return asdict(self)


def is_allowed_tool(name: object) -> bool:
    """Return whether ``name`` may be called by a strategy.

    Exact membership in :data:`ALLOWED_STRATEGY_TOOLS`, minus anything carrying
    a forbidden prefix.

    Args:
        name: Candidate tool name; non-strings are rejected.

    Returns:
        ``True`` only for an allowlisted, non-forbidden name.
    """
    if not isinstance(name, str) or not name:
        return False
    if name.startswith(FORBIDDEN_TOOL_PREFIXES):
        return False
    return name in ALLOWED_STRATEGY_TOOLS


def validate_spec(spec: StrategySpec | Mapping[str, Any] | None) -> list[Gap]:
    """Report every deterministic gap in a strategy spec.

    ``None`` stands for an absent spec (adapting a workflow parent can produce
    such a child) and yields a single ``missing_spec`` gap instead of an
    exception. Mappings are coerced with :meth:`StrategySpec.from_dict`; any
    other value is treated as a missing spec.

    The declarative checks are workflow-scoped: a ``workflow`` spec needs three
    steps, non-empty preconditions, a usable output contract, failure handling
    and allowlisted tools. A ``code`` spec carries no plan here — promotion
    validates its run directory — so only name, kind and input sources apply.

    Args:
        spec: A spec instance, a JSON-shaped mapping, or ``None``.

    Returns:
        Gaps sorted by ``field``; gaps sharing a field are ordered by ``code``.
        Empty when the spec is usable.
    """
    if isinstance(spec, Mapping):
        spec = StrategySpec.from_dict(spec)
    if not isinstance(spec, StrategySpec):
        return [
            Gap(
                code="missing_spec",
                field="spec",
                message="the strategy spec is missing",
                fix_hint="provide a spec with name, kind and an output contract",
            )
        ]

    gaps: list[Gap] = []
    if _blank(spec.name):
        gaps.append(
            Gap(
                code="empty_name",
                field="name",
                message="the strategy has no name",
                fix_hint="set a short, unique name",
            )
        )
    if spec.kind not in SPEC_KINDS:
        gaps.append(
            Gap(
                code="bad_kind",
                field="kind",
                message=f"kind {spec.kind!r} is not one of {sorted(SPEC_KINDS)}",
                fix_hint="use 'code' for a backtested engine or 'workflow' for a research plan",
            )
        )

    for index, item in enumerate(spec.inputs):
        if _blank(item.source):
            gaps.append(
                Gap(
                    code="missing_input_source",
                    field=f"inputs[{index}].source",
                    message=f"input {item.name or index!r} declares no source",
                    fix_hint="name the tool or artifact that provides this input",
                )
            )

    if spec.kind == "workflow":
        contract_gap = _contract_gap(spec.output_contract)
        if contract_gap is not None:
            gaps.append(contract_gap)
        if len(spec.steps) < MIN_WORKFLOW_STEPS:
            gaps.append(
                Gap(
                    code="too_few_steps",
                    field="steps",
                    message=(
                        f"a workflow needs at least {MIN_WORKFLOW_STEPS} steps, "
                        f"got {len(spec.steps)}"
                    ),
                    fix_hint="split the plan into concrete, observable steps",
                )
            )
        if not any(not _blank(item) for item in spec.preconditions):
            gaps.append(
                Gap(
                    code="missing_preconditions",
                    field="preconditions",
                    message="a workflow declares no preconditions",
                    fix_hint="list the machine-checkable conditions that must hold first",
                )
            )
        if _blank(spec.failure_handling):
            gaps.append(
                Gap(
                    code="missing_failure_handling",
                    field="failure_handling",
                    message="a workflow declares no failure handling",
                    fix_hint="state what to report and stop when a step fails",
                )
            )

        for index, step in enumerate(spec.steps):
            if not is_allowed_tool(step.tool):
                gaps.append(
                    Gap(
                        code="tool_not_allowed",
                        field=f"steps[{index}].tool",
                        message=f"tool {step.tool!r} is not in the strategy allowlist",
                        fix_hint=f"use one of {sorted(ALLOWED_STRATEGY_TOOLS)}",
                    )
                )

    gaps.sort(key=lambda gap: (gap.field, gap.code))
    return gaps


def _contract_gap(contract: OutputContract | None) -> Gap | None:
    """Return the single output-contract gap, or ``None`` when usable."""
    if contract is None:
        return Gap(
            code="missing_output_contract",
            field="output_contract",
            message="the spec declares no output contract",
            fix_hint="declare a format, required fields and a destination",
        )
    if contract.format not in OUTPUT_CONTRACT_FORMATS:
        return Gap(
            code="missing_output_contract",
            field="output_contract",
            message=(
                f"output format {contract.format!r} is not one of "
                f"{sorted(OUTPUT_CONTRACT_FORMATS)}"
            ),
            fix_hint="pick one of the supported output formats",
        )
    if not contract.required_fields:
        return Gap(
            code="missing_output_contract",
            field="output_contract",
            message="the output contract lists no required fields",
            fix_hint="list the fields every result must contain",
        )
    return None


def _text(value: object) -> str:
    """Return a stripped string, or ``""`` for any non-string value."""
    return value.strip() if isinstance(value, str) else ""


def _texts(value: object) -> list[str]:
    """Return the string items of a sequence, ignoring non-sequences."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return []
    return [item if isinstance(item, str) else str(item) for item in value]


def _items(value: object) -> list[object]:
    """Return sequence items for nested spec collections, never a bare string."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return []
    return list(value)


def _blank(value: object) -> bool:
    """Return whether ``value`` is empty or whitespace-only."""
    return not str(value or "").strip()


def _spec_input(item: object) -> SpecInput:
    """Build one input, keeping malformed entries visible to validation."""
    if not isinstance(item, Mapping):
        return SpecInput(name=str(item))
    required = item.get("required")
    return SpecInput(
        name=_text(item.get("name")),
        source=_text(item.get("source")),
        required=True if required is None else bool(required),
        missing_policy=_text(item.get("missing_policy")),
    )


def _spec_step(item: object) -> SpecStep:
    """Build one step, keeping malformed entries visible to validation."""
    if not isinstance(item, Mapping):
        return SpecStep(action=str(item))
    return SpecStep(
        id=_text(item.get("id")),
        action=_text(item.get("action")),
        tool=_text(item.get("tool")),
        output=_text(item.get("output")),
    )


def _output_contract(contract: Mapping[str, Any]) -> OutputContract:
    """Build an output contract from a mapping."""
    return OutputContract(
        format=_text(contract.get("format")),
        required_fields=_texts(contract.get("required_fields")),
        destination=_text(contract.get("destination")),
    )


__all__ = [
    "ALLOWED_STRATEGY_TOOLS",
    "FORBIDDEN_TOOL_PREFIXES",
    "MIN_WORKFLOW_STEPS",
    "OUTPUT_CONTRACT_FORMATS",
    "SPEC_KINDS",
    "Gap",
    "OutputContract",
    "SpecInput",
    "SpecStep",
    "StrategySpec",
    "is_allowed_tool",
    "validate_spec",
]
