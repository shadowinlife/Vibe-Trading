"""Eval-local governance stand-ins for the deterministic eval runner.

Ports the policy models and rule engine that ``runner.py`` consumed before
revert 035673b0 removed ``src/governance/{manifest,decisions,policy_engine}.py``.
Sources of truth for the port (state at commit 035673b0^):

- ``agent/src/governance/manifest.py``  -> ToolSurface / RiskLevel / ToolManifest
- ``agent/src/governance/decisions.py`` -> RuntimeContext / PolicyDecision
- ``agent/src/governance/policy_engine.py`` -> PolicyRule / PolicyEngine rules

Scope boundary: this module is deterministic *test infrastructure* used by
:mod:`src.evals.agent_eval.runner` to synthesize expected trace events for
offline scoring. It never wraps a live tool registry and never gates a real
tool execution, so the revert reasons for the runtime stack (#433 session
breakage, #420 bypass paths) do not apply here; product trading safety stays
on the single mandate-gate design. One documented simplification: parameter
auditing (``build_param_audit``: secret redaction + params hash) belonged to
the reverted reliability stack and is not ported — ``params_hash`` /
``params_preview`` stay ``None`` because the eval runner never reads them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolSurface(str, Enum):
    """Execution surfaces where a tool call may originate."""

    CLI = "cli"
    LOCAL_API = "local_api"
    REMOTE_API = "remote_api"
    MCP_STDIO = "mcp_stdio"
    MCP_SSE = "mcp_sse"
    MCP_HTTP = "mcp_http"
    SWARM = "swarm"
    SCHEDULER = "scheduler"
    BACKTEST_SUBPROCESS = "backtest_subprocess"
    LIVE_CONNECTOR = "live_connector"
    CHANNEL_BOT = "channel_bot"


class RiskLevel(str, Enum):
    """Governance risk tiers for tool calls."""

    R0_READ = "R0_READ"
    R1_WRITE_LOCAL = "R1_WRITE_LOCAL"
    R2_NETWORK = "R2_NETWORK"
    R3_TRADE_READ = "R3_TRADE_READ"
    R4_TRADE_WRITE = "R4_TRADE_WRITE"
    R5_SHELL = "R5_SHELL"
    UNCLASSIFIED = "UNCLASSIFIED"


SecretAccess = Literal["none", "market_data_read", "llm", "api_auth", "broker"]
AllowedMode = Literal["research", "paper", "advisory", "live"]


class ToolManifest(BaseModel):
    """Governance metadata derived from an existing BaseTool."""

    model_config = ConfigDict(extra="allow")

    name: str
    surface: ToolSurface
    readonly: bool
    repeatable: bool
    risk_level: RiskLevel
    requires_auth: bool
    requires_consent: bool
    allowed_modes: list[AllowedMode]
    secret_access: SecretAccess
    timeout_seconds: int
    side_effects: list[str]
    live_classification: str | None = None


GovernanceMode = Literal["off", "observe", "warn", "enforce"]
PolicyAction = Literal["allow", "warn", "deny"]


class RuntimeContext(BaseModel):
    """Context available to the policy engine at call time."""

    surface: ToolSurface
    mode: GovernanceMode = "observe"
    session_id: str | None = None
    run_id: str | None = None
    user_auth_state: dict[str, Any] = Field(default_factory=dict)
    live_state: dict[str, Any] = Field(default_factory=dict)
    budget_state: dict[str, Any] = Field(default_factory=dict)


class PolicyDecision(BaseModel):
    """Result of evaluating one tool call against governance policy."""

    tool_name: str
    action: PolicyAction
    mode: GovernanceMode
    reasons: list[str]
    required_checks: list[str] = Field(default_factory=list)
    rule_id: str | None = None
    params_hash: str | None = None
    params_preview: dict[str, Any] | None = None


Predicate = Callable[..., bool]
RuleAction = Literal["allow", "warn", "deny", "allow_if_all"]


@dataclass(frozen=True)
class PolicyRule:
    """One first-match governance rule."""

    priority: int
    rule_id: str
    description: str
    action: RuleAction
    surfaces: set[ToolSurface] | None = None
    risk_levels: set[RiskLevel] | None = None
    tool_names: set[str] | None = None
    predicate: Predicate | None = None
    required_checks: tuple[str, ...] = ()

    def matches(
        self,
        *,
        name: str,
        params: dict[str, Any],
        manifest: ToolManifest,
        context: RuntimeContext,
    ) -> bool:
        """Return whether this rule applies to one tool call."""
        if self.surfaces is not None and context.surface not in self.surfaces:
            return False
        if self.risk_levels is not None and manifest.risk_level not in self.risk_levels:
            return False
        if self.tool_names is not None and name not in self.tool_names:
            return False
        if self.predicate is not None:
            return bool(
                self.predicate(
                    name=name, params=params, manifest=manifest, context=context
                )
            )
        return True


class PolicyEngine:
    """Evaluate tool calls by priority, with first-match wins."""

    def __init__(self, rules: list[PolicyRule] | None = None) -> None:
        self.rules = sorted(
            rules if rules is not None else _builtin_rules(),
            key=lambda rule: rule.priority,
        )

    def evaluate(
        self,
        *,
        name: str,
        params: dict[str, Any],
        manifest: ToolManifest,
        context: RuntimeContext,
    ) -> PolicyDecision:
        """Evaluate one tool call against the rule set, fail-safe."""
        try:
            for rule in self.rules:
                if not rule.matches(
                    name=name, params=params, manifest=manifest, context=context
                ):
                    continue
                return self._decision_for_rule(
                    rule, name=name, manifest=manifest, context=context
                )
            return _decision(
                name=name,
                action="deny",
                context=context,
                rule_id="no_match",
                reasons=["No governance policy rule matched; fail-safe deny"],
            )
        except Exception as exc:  # noqa: BLE001 - policy exceptions must fail safe
            action: PolicyAction = (
                "deny" if _must_fail_closed(manifest, context) else "warn"
            )
            return _decision(
                name=name,
                action=action,
                context=context,
                rule_id="policy_exception",
                reasons=[
                    f"PolicyEngine exception handled fail-safe: {exc.__class__.__name__}"
                ],
            )

    def _decision_for_rule(
        self,
        rule: PolicyRule,
        *,
        name: str,
        manifest: ToolManifest,
        context: RuntimeContext,
    ) -> PolicyDecision:
        if rule.action == "allow_if_all":
            missing = [
                check
                for check in rule.required_checks
                if not _check_state(context, check)
            ]
            if missing:
                return _decision(
                    name=name,
                    action="deny",
                    context=context,
                    rule_id=rule.rule_id,
                    reasons=[
                        f"{rule.description}; missing required checks: {', '.join(missing)}"
                    ],
                    required_checks=list(rule.required_checks),
                )
            return _decision(
                name=name,
                action="allow",
                context=context,
                rule_id=rule.rule_id,
                reasons=[rule.description],
                required_checks=list(rule.required_checks),
            )

        action: PolicyAction
        if rule.rule_id == "P900":
            action = "deny" if context.mode == "enforce" else "warn"
        else:
            action = rule.action  # type: ignore[assignment]
        return _decision(
            name=name,
            action=action,
            context=context,
            rule_id=rule.rule_id,
            reasons=[rule.description],
            required_checks=list(rule.required_checks),
        )


def _builtin_rules() -> list[PolicyRule]:
    return [
        PolicyRule(
            priority=10,
            rule_id="P10",
            description="UNKNOWN live connector tool is fail-closed",
            action="deny",
            surfaces={ToolSurface.LIVE_CONNECTOR},
            predicate=lambda **kw: getattr(kw["manifest"], "live_classification", None)
            == "UNKNOWN",
        ),
        PolicyRule(
            priority=20,
            rule_id="P20",
            description="Remote API, MCP SSE/HTTP, and channel bots cannot execute shell tools by default",
            action="deny",
            surfaces={
                ToolSurface.REMOTE_API,
                ToolSurface.MCP_SSE,
                ToolSurface.MCP_HTTP,
                ToolSurface.CHANNEL_BOT,
            },
            risk_levels={RiskLevel.R5_SHELL},
        ),
        PolicyRule(
            priority=30,
            rule_id="P30",
            description="Scheduler cannot execute live write or shell tools by default",
            action="deny",
            surfaces={ToolSurface.SCHEDULER},
            risk_levels={RiskLevel.R4_TRADE_WRITE, RiskLevel.R5_SHELL},
        ),
        PolicyRule(
            priority=35,
            rule_id="P35",
            description="Swarm workers cannot receive external MCP URLs from prompt input",
            action="deny",
            surfaces={ToolSurface.SWARM},
            predicate=lambda **kw: bool(
                kw["params"].get("external_mcp_url_from_prompt")
            ),
        ),
        PolicyRule(
            priority=40,
            rule_id="P40",
            description="Live trade writes require mandate, clear kill switch, explicit consent, live guard, and connector profile",
            action="allow_if_all",
            risk_levels={RiskLevel.R4_TRADE_WRITE},
            required_checks=(
                "mandate_active",
                "kill_switch_clear",
                "explicit_user_consent",
                "live_order_guard",
                "connector_profile_selected",
            ),
        ),
        PolicyRule(
            priority=50,
            rule_id="P50",
            description="Explicit local market data requests cannot silently fall back to network",
            action="deny",
            tool_names={"get_market_data"},
            predicate=lambda **kw: bool(kw["params"].get("explicit_local"))
            and bool(kw["params"].get("fallback_to_network")),
        ),
        PolicyRule(
            priority=100,
            rule_id="P100",
            description="Read-only MCP stdio R0 tool allowed by default",
            action="allow",
            surfaces={ToolSurface.MCP_STDIO},
            risk_levels={RiskLevel.R0_READ},
            predicate=lambda **kw: bool(kw["manifest"].readonly),
        ),
        PolicyRule(
            priority=900,
            rule_id="P900",
            description="Unclassified tools require observation/manual review before enforcement",
            action="warn",
            risk_levels={RiskLevel.UNCLASSIFIED},
        ),
        PolicyRule(
            priority=999,
            rule_id="P999",
            description="No matching governance rule; fail-safe deny",
            action="deny",
        ),
    ]


def _decision(
    *,
    name: str,
    action: PolicyAction,
    context: RuntimeContext,
    rule_id: str,
    reasons: list[str],
    required_checks: list[str] | None = None,
) -> PolicyDecision:
    return PolicyDecision(
        tool_name=name,
        action=action,
        mode=context.mode,
        reasons=reasons,
        required_checks=required_checks or [],
        rule_id=rule_id,
    )


def _check_state(context: RuntimeContext, check: str) -> bool:
    if check in context.live_state:
        return bool(context.live_state[check])
    if check in context.user_auth_state:
        return bool(context.user_auth_state[check])
    if check in context.budget_state:
        return bool(context.budget_state[check])
    return False


def _must_fail_closed(manifest: ToolManifest, context: RuntimeContext) -> bool:
    return manifest.risk_level in {
        RiskLevel.R4_TRADE_WRITE,
        RiskLevel.R5_SHELL,
    } or context.surface in {
        ToolSurface.REMOTE_API,
        ToolSurface.MCP_SSE,
        ToolSurface.MCP_HTTP,
        ToolSurface.SWARM,
        ToolSurface.SCHEDULER,
        ToolSurface.LIVE_CONNECTOR,
        ToolSurface.CHANNEL_BOT,
    }
