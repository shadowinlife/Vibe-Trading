"""opencode-side session bridge for driving the baseline harness.

Shared component (todo 4 owns it; todo 7's SWE/terminal adapters import it):
``OpenCodeBridge`` drives the baseline opencode+OMO harness through the
opencode-serve deployment, reusing ``opencode_check`` container lifecycle and
the ``opencode_probe`` round-trip protocol (extend, don't fork).

Interface todo 7 relies on::

    bridge = OpenCodeBridge(BridgeConfig(...))
    probe  = bridge.preflight()            # bounded single-task round-trip
    traj   = bridge.run_task(prompt, timeout_s=600, seed=0)
    bridge.teardown()                      # as-found: only what WE started

Read-only driving: the bridge never writes OpencodeAgent files nor mutates
container config; teardown returns container state to as-found. Every wait is
bounded (``timeouts``); structured failures (timeout / auth_401 /
no_tool_call / transport / interrupted) are typed ``BridgeError`` objects on
the trajectory or probe report — never uncaught hangs.

Test seams (documented, used by tests/evals/test_tau2_adapter.py):
``BridgeConfig.probe_fn`` replaces the round-trip probe;
``OpenCodeBridge._make_dialect`` is monkeypatchable for fake transports.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.evals.harness_bench import opencode_check, opencode_probe

#: Completion marker the bridge appends to every task prompt so polling has a
#: deterministic, bounded end-of-turn signal (mirrors the probe's markers).
DONE_MARKER = "BRIDGE_TASK_DONE"

ERROR_KINDS = (
    "timeout",
    "auth_401",
    "no_tool_call",
    "transport",
    "session_error",
    "interrupted",
)

MODEL_CONFIG_PATH = "/workspace/.opencode/oh-my-openagent.json"


@dataclass(frozen=True)
class BridgeError:
    """Typed, JSON-able failure attached to a trajectory/probe report."""

    kind: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "detail": self.detail[:1000]}


@dataclass
class BridgeTrajectory:
    """One bridge-driven task: transcript plus typed error (None when ok)."""

    task_prompt: str
    seed: int
    messages: list[dict] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    final_result: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0
    error: BridgeError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class ProbeReport:
    """Outcome of ``OpenCodeBridge.preflight()`` (fresh, never cached)."""

    ok: bool
    phases: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    tool_names: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    container: dict[str, Any] = field(default_factory=dict)
    error: BridgeError | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BridgeConfig:
    """Endpoint/image/env pin plus parity model params for the bridge.

    ``endpoint``/``password`` point at a pre-existing service (left as
    found); when absent the bridge starts an ephemeral container via
    ``opencode_check.start_ephemeral_container`` and owns its teardown.
    """

    endpoint: str | None = None
    password: str | None = None
    image: str | None = None
    model_id: str = ""
    env_pin: dict[str, str] = field(default_factory=dict)
    timeouts: dict[str, float] = field(default_factory=dict)
    probe_fn: Callable[..., dict] | None = None

    @classmethod
    def from_env(cls, env: dict, model_id: str = "") -> "BridgeConfig":
        return cls(
            endpoint=env.get("HARNESS_BENCH_OPENCODE_URL", "").strip() or None,
            password=env.get("HARNESS_BENCH_OPENCODE_PASSWORD", "") or None,
            image=env.get("HARNESS_BENCH_OPENCODE_IMAGE", "").strip() or None,
            model_id=model_id,
            env_pin={"TZ": "Asia/Shanghai"},
        )


def _classify_http_failure(status: int, detail: str) -> BridgeError:
    if status == 401:
        return BridgeError("auth_401", f"HTTP 401 from opencode-serve: {detail[:300]}")
    return BridgeError("transport", f"HTTP {status}: {detail[:300]}")


class OpenCodeBridge:
    """Drives the baseline opencode+OMO harness; see module docstring."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.timeouts = {**opencode_probe.DEFAULT_TIMEOUTS, **config.timeouts}
        self._url: str | None = None
        self._password: str | None = None
        self._container: dict[str, Any] = {}
        self._probe: ProbeReport | None = None
        self.image_facts: dict[str, Any] = {}
        self._torn_down = False

    # -- service lifecycle ------------------------------------------------- #

    def _ensure_service(self) -> BridgeError | None:
        if self._url:
            return None
        if self.config.endpoint:
            status, _, raw = opencode_probe.http_request(
                f"{self.config.endpoint.rstrip('/')}/health",
                (
                    opencode_probe.BasicAuth(self.config.password)
                    if self.config.password
                    else None
                ),
                self.timeouts["connect"],
            )
            if status in (200, 401):
                if not self.config.password:
                    return BridgeError(
                        "session_error",
                        "endpoint reachable but no bridge password configured",
                    )
                self._url = self.config.endpoint.rstrip("/")
                self._password = self.config.password
                self._container = {"origin": "pre-existing service", "as_found": True}
                return None
            return _classify_http_failure(status, f"health check failed: {raw[:200]}")
        env = {
            "HARNESS_BENCH_OPENCODE_IMAGE": self.config.image or "",
            **self.config.env_pin,
        }
        import os

        for key in ("DASHSCOPE_API_KEY",):
            if os.environ.get(key):
                env[key] = os.environ[key]
        started = opencode_check.start_ephemeral_container(env)
        if not started.get("ok"):
            return BridgeError("transport", f"cannot start service: {started}")
        self._url = started["url"]
        self._password = started["password"]
        self._container = {
            "origin": f"ephemeral container {started['image']}",
            "container": started["container"],
            "image": started["image"],
            "started_at": started["started_at"],
            "as_found": False,
        }
        return None

    def _make_dialect(self, timeouts: dict[str, float]) -> opencode_probe._Dialect:
        assert self._url is not None and self._password is not None
        return opencode_probe._Dialect(
            self._url, opencode_probe.BasicAuth(self._password), timeouts
        )

    def _classify_session_failure(self, detail: str) -> BridgeError:
        if "401" in detail:
            return BridgeError("auth_401", detail)
        status, _, raw = opencode_probe.http_request(
            f"{self._url}/health",
            opencode_probe.BasicAuth(self._password or ""),
            self.timeouts["health"],
        )
        if status == 401:
            return BridgeError("auth_401", f"health 401 after session failure: {raw}")
        return BridgeError("session_error", detail)

    def collect_image_facts(self) -> dict[str, Any]:
        """Read-only facts about the deployed image (model config, tag).

        Reads ``oh-my-openagent.json`` baked into the image via a bounded
        ``docker run --rm --entrypoint cat`` (container auto-removed; nothing
        left running). Confirms the qwen3.7-max vs qwen3.8-max finding.
        """
        image = self._container.get("image") or self.config.image
        facts: dict[str, Any] = {"image": image}
        if not image:
            facts["error"] = "no image resolved"
            return facts
        code, out = opencode_check._run(
            ["docker", "run", "--rm", "--entrypoint", "cat", image, MODEL_CONFIG_PATH],
            opencode_check.COMMAND_TIMEOUT * 2,
        )
        if code != 0:
            facts["model_config_error"] = out[:300]
            return facts
        models = sorted(set(re.findall(r'"model"\s*:\s*"([^"]+)"', out)))
        facts["model_config_path"] = MODEL_CONFIG_PATH
        facts["model_config_models"] = models
        facts["parity_model_id_expected"] = self.config.model_id
        facts["model_config_matches_parity"] = bool(models) and all(
            m == self.config.model_id for m in models
        )
        return facts

    # -- preflight gate ---------------------------------------------------- #

    def preflight(self) -> ProbeReport:
        """Fresh single-task round-trip: session create -> >=2-turn dialog ->
        >=1 tool call -> result retrieval. Bounded; structured on failure."""
        err = self._ensure_service()
        if err is not None:
            self._probe = ProbeReport(ok=False, error=err, container=self._container)
            self.teardown()
            return self._probe
        probe_fn = self.config.probe_fn or opencode_probe.probe_session_round_trip
        started = time.monotonic()
        try:
            raw = probe_fn(self._url, self._password, self.timeouts)
        except Exception as exc:  # noqa: BLE001 - interrupted probe must settle
            self.image_facts = self.collect_image_facts()
            self._probe = ProbeReport(
                ok=False,
                phases={"interrupted": {"ok": False, "detail": str(exc)[:500]}},
                elapsed_seconds=round(time.monotonic() - started, 2),
                container=self._container,
                error=BridgeError("interrupted", f"probe aborted mid-flight: {exc}"),
            )
            self.teardown()
            return self._probe
        self.image_facts = self.collect_image_facts()
        self._probe = ProbeReport(
            ok=bool(raw.get("ok")),
            phases=raw.get("phases", {}),
            session_id=raw.get("session_id"),
            tool_names=raw.get("tool_names", []),
            elapsed_seconds=raw.get("elapsed_seconds", 0.0),
            container=self._container,
            error=None if raw.get("ok") else self._probe_error_from(raw),
            raw=raw,
        )
        if not self._probe.ok and not self._container.get("as_found"):
            self.teardown()
        return self._probe

    @staticmethod
    def _probe_error_from(raw: dict) -> BridgeError:
        for phase, info in raw.get("phases", {}).items():
            if not info.get("ok"):
                detail = str(info.get("detail", ""))
                if "401" in detail or "missing_api_key" in detail:
                    return BridgeError("auth_401", f"{phase}: {detail[:400]}")
                if phase == "turn2_tool_call" and "budget" in detail:
                    return BridgeError("timeout", f"{phase}: {detail[:400]}")
                return BridgeError("session_error", f"{phase}: {detail[:400]}")
        return BridgeError("session_error", "probe reported not-ok without a phase")

    # -- task driving ------------------------------------------------------ #

    def run_task(
        self,
        task_prompt: str,
        *,
        timeout_s: int,
        seed: int,
        require_tool: bool = False,
    ) -> BridgeTrajectory:
        """Drive one task through the deployment; bounded; typed errors."""
        started = time.monotonic()
        trajectory = BridgeTrajectory(task_prompt=task_prompt, seed=seed)
        err = self._ensure_service()
        if err is not None:
            trajectory.error = err
            return trajectory
        timeouts = {**self.timeouts, "message_turn": float(timeout_s)}
        dialect = self._make_dialect(timeouts)
        prompt = (
            f"{task_prompt}\n\nWhen you have fully completed the task, reply with "
            f"exactly one line: {DONE_MARKER}"
        )
        session_id, detail = dialect.create_session()
        if session_id is None:
            trajectory.error = self._classify_session_failure(detail)
            trajectory.duration_s = round(time.monotonic() - started, 2)
            return trajectory
        admitted, detail = dialect.send_prompt(session_id, prompt)
        if not admitted:
            trajectory.error = (
                BridgeError("auth_401", detail)
                if "401" in detail
                else BridgeError("session_error", detail)
            )
            trajectory.duration_s = round(time.monotonic() - started, 2)
            return trajectory
        ok, parts, detail = dialect.poll_until(session_id, [DONE_MARKER], False)
        messages, msg_detail = dialect.list_messages(session_id)
        all_parts: list[dict] = []
        for message in messages:
            all_parts.extend(opencode_probe._iter_parts(message))
        tool_parts = [p for p in all_parts if opencode_probe._part_is_tool(p)]
        trajectory.messages = messages
        trajectory.tool_calls = opencode_probe._tool_names(tool_parts)
        text = "\n".join(opencode_probe._part_text(p) for p in all_parts)
        trajectory.final_result = text.split(DONE_MARKER)[0].strip()[-4000:] or None
        trajectory.duration_s = round(time.monotonic() - started, 2)
        if not ok:
            kind = "timeout" if "budget" in detail else "session_error"
            blob = f"{detail} {msg_detail} {text}"
            if "401" in blob or "missing_api_key" in blob:
                kind = "auth_401"
            trajectory.error = BridgeError(kind, f"{detail}; {msg_detail}"[:800])
        elif require_tool and not trajectory.tool_calls:
            trajectory.error = BridgeError(
                "no_tool_call", "task completed but no tool call was observed"
            )
        return trajectory

    # -- teardown ---------------------------------------------------------- #

    def teardown(self) -> dict[str, Any]:
        """As-found rule: only tear down what THIS bridge started. Idempotent."""
        if self._torn_down:
            return {"torn_down": False, "reason": "already torn down"}
        self._torn_down = True
        container_name = self._container.get("container")
        if not container_name or self._container.get("as_found"):
            return {"torn_down": False, "reason": "no ephemeral container owned"}
        receipt = opencode_check.teardown_container(container_name)
        self._container["teardown_receipt"] = receipt
        return receipt

    # -- HIL evidence ------------------------------------------------------ #

    def hil_facts(self) -> dict[str, Any]:
        """Container/image facts for the HIL package (never contains keys)."""
        return {
            "service": {
                "url": self._url,
                "container": {
                    k: v for k, v in self._container.items() if k != "teardown_receipt"
                },
            },
            "image_facts": self.image_facts or self.collect_image_facts(),
        }
