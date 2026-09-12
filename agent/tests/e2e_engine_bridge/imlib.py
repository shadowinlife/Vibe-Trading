"""T8 IM-parity helpers: a mock channel adapter + an in-process IM stack.

The stack is the production wiring, assembled in the test process against the
live rig's ``opencode serve`` (T7's ``start_rig.py``):

* the REAL, UNCHANGED ``ChannelRuntime`` / ``ChannelManager`` / ``BaseChannel``
  ingress path (``_handle_message`` -> bus -> runtime -> outbound dispatch);
* a test-defined ``MockChannel`` adapter (the bus-level mock pattern of
  ``tests/test_channels_runtime.py`` promoted to a real ``BaseChannel``
  subclass, so the manager's consumption contract is exercised too). It lives
  HERE, never under ``src/channels/`` — the 16 adapters stay zero-diff;
* the REAL bridge service (``RecoverableOpencodeSessionService`` + T4
  translator + T3 driver), started through the production startup sequence
  ``opencode_bridge.wiring.start_session_service`` (tool map -> subscribe-first
  pumps -> reconcile).

Rationale for in-process instead of inside the gateway: the gateway hosts the
same classes (T7 proved the gateway wiring live); the parity claim under test
is that ``ChannelRuntime`` + adapters consume the SessionService seam WITHOUT
knowing the engine changed, and an in-process stack observes that seam with
per-poll timing precision (the engine-death scenario needs it). The gateway
still gets its own live preamble check (scenario 0 of the parity module).

Hygiene: every engine (opencode) session created is appended to a JSONL
registry in the evidence dir so the runner can cost + DELETE them afterwards
(the rig shares the user's opencode data dir — spike §7h).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.channels.base import BaseChannel
from src.channels.bus.events import OutboundMessage
from src.channels.bus.queue import MessageBus
from src.channels.manager import ChannelManager
from src.channels.runtime import ChannelRuntime
from src.opencode_bridge.driver import OpencodeDriver
from src.opencode_bridge.recovery import (
    ENGINE_SESSION_CONFIG_KEY,
    RecoverableOpencodeSessionService,
)
from src.opencode_bridge.translator import EventTranslator
from src.opencode_bridge.wiring import start_session_service
from src.session.events import EventBus, SSEEvent
from src.session.store import SessionStore

#: Channel name of the mock adapter. Deliberately NOT one of the 16 built-in
#: names so a registry/config mix-up can never route to a real platform.
MOCK_CHANNEL_NAME = "mockim"

#: Production polling budget (ChannelRuntimeConfig default; the operator knob
#: ``reply_timeout_s`` in src/config/schema.py:451 defaults to the same 600).
PROD_REPLY_TIMEOUT_S = 600.0

#: Production poll cadence (ChannelRuntimeConfig default).
PROD_POLL_INTERVAL_S = 0.25

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})


class MockChannel(BaseChannel):
    """In-test IM adapter: records outbound, injects inbound via BaseChannel.

    Inbound goes through the shared ``BaseChannel._handle_message`` ingress
    (allowlist check + ``InboundMessage`` construction + bus publish) — the
    exact code path all 16 production adapters use. Outbound arrives via the
    ChannelManager dispatcher calling ``send`` (and ``send_with_receipt`` for
    the scheduled-briefing path, inherited unchanged from BaseChannel).
    """

    name = MOCK_CHANNEL_NAME
    display_name = "MockIM (T8)"

    def __init__(self, config: Any, bus: MessageBus) -> None:
        super().__init__(config, bus)
        self.sent: List[OutboundMessage] = []
        self._queues: Dict[str, asyncio.Queue] = {}
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self) -> None:
        self._running = True
        self.start_calls += 1
        while self._running:
            await asyncio.sleep(0.05)

    async def stop(self) -> None:
        self._running = False
        self.stop_calls += 1

    async def send(self, msg: OutboundMessage) -> None:
        self.sent.append(msg)
        queue = self._queues.setdefault(msg.chat_id, asyncio.Queue())
        await queue.put(msg)

    async def inject(
        self,
        content: str,
        *,
        sender_id: str = "t8-user",
        chat_id: str = "t8-chat",
        message_id: Optional[str] = None,
        is_dm: bool = True,
    ) -> str:
        """Push one inbound message through the real BaseChannel ingress."""
        mid = message_id or f"t8-{time.time_ns()}"
        await self._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            metadata={"message_id": mid},
            is_dm=is_dm,
        )
        return mid

    async def next_for(self, chat_id: str, timeout_s: float) -> OutboundMessage:
        """Await the next outbound message for *chat_id*."""
        queue = self._queues.setdefault(chat_id, asyncio.Queue())
        return await asyncio.wait_for(queue.get(), timeout=timeout_s)

    def sent_for(self, chat_id: str) -> List[OutboundMessage]:
        return [m for m in self.sent if m.chat_id == chat_id]


class BusRecorder:
    """Records every EventBus event (type + relative time) as evidence."""

    def __init__(self, event_bus: EventBus) -> None:
        self.t0 = time.time()
        self.events: List[Dict[str, Any]] = []
        event_bus.add_listener(self._on_event)

    def _on_event(self, event: SSEEvent) -> None:
        data = event.data or {}
        self.events.append(
            {
                "t": round(time.time() - self.t0, 3),
                "type": event.event_type,
                "session_id": event.session_id,
                "attempt_id": data.get("attempt_id"),
            }
        )

    def types_seen(self) -> List[str]:
        return sorted({e["type"] for e in self.events})

    def times_of(self, event_type: str) -> List[float]:
        return [e["t"] for e in self.events if e["type"] == event_type]


@dataclass
class ImStack:
    """One fully wired IM stack (bus + mock adapter + manager + runtime + service)."""

    bus: MessageBus
    mock: MockChannel
    manager: ChannelManager
    runtime: ChannelRuntime
    service: RecoverableOpencodeSessionService
    driver: OpencodeDriver
    store: SessionStore
    event_bus: EventBus
    recorder: BusRecorder
    registry_path: Path
    scenario: str
    built_at: float = field(default_factory=time.time)


class EngineSessionRegistry:
    """Append-only JSONL of engine sessions created (cleanup + cost source)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, scenario: str, vt_session_id: str, engine_session_id: str) -> None:
        entry = {
            "scenario": scenario,
            "vt_session_id": vt_session_id,
            "engine_session_id": engine_session_id,
            "recorded_at": time.time(),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def read(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows


async def build_bridge_service(
    serve_url: str, store_dir: Path
) -> tuple[RecoverableOpencodeSessionService, SessionStore, OpencodeDriver]:
    """Build + start the bridge service exactly like the gateway factory does.

    Mirrors ``api/state.py::_get_session_service`` (opencode branch) +
    ``opencode_bridge.wiring.start_session_service`` (tool map -> subscribe-
    first pumps -> reconcile) with an explicit serve URL.
    """
    store_dir.mkdir(parents=True, exist_ok=True)
    store = SessionStore(base_dir=store_dir)
    event_bus = EventBus()
    event_bus.set_loop(asyncio.get_running_loop())
    driver = OpencodeDriver(base_url=serve_url)
    translator = EventTranslator(
        quiescence_s=8.0, child_events="drop", tool_map=driver.tool_map
    )
    service = RecoverableOpencodeSessionService(
        store=store,
        event_bus=event_bus,
        runs_dir=store_dir.parent / "runs",
        driver=driver,
        translator=translator,
    )
    await start_session_service(service)
    return service, store, driver


async def build_im_stack(
    *,
    serve_url: str,
    store_dir: Path,
    session_map_path: Path,
    registry_path: Path,
    scenario: str,
    reply_timeout_s: float = PROD_REPLY_TIMEOUT_S,
    poll_interval_s: float = PROD_POLL_INTERVAL_S,
    operators: tuple = ("t8-operator",),
) -> ImStack:
    """Assemble and start one IM stack against the live rig serve.

    Mirrors the gateway's construction (``api/state.py::_get_channel_runtime``
    + ``opencode_bridge.wiring``): bridge service (production startup
    sequence) -> ChannelManager (+ injected mock) -> ChannelRuntime with the
    PRODUCTION polling budget/cadence.
    """
    service, store, driver = await build_bridge_service(serve_url, store_dir)
    event_bus = service.event_bus

    bus = MessageBus()
    mock = MockChannel({"enabled": True, "allow_from": ["*"]}, bus)
    manager = ChannelManager({}, bus, session_service=service)
    manager.channels[MOCK_CHANNEL_NAME] = mock
    runtime = ChannelRuntime(
        bus=bus,
        session_service=service,
        manager=manager,
        session_map_path=session_map_path,
        reply_timeout_s=reply_timeout_s,
        poll_interval_s=poll_interval_s,
        operators=list(operators),
    )
    recorder = BusRecorder(event_bus)
    await runtime.start(start_manager=True)
    return ImStack(
        bus=bus,
        mock=mock,
        manager=manager,
        runtime=runtime,
        service=service,
        driver=driver,
        store=store,
        event_bus=event_bus,
        recorder=recorder,
        registry_path=registry_path,
        scenario=scenario,
    )


async def close_im_stack(stack: ImStack) -> None:
    """Tear down the stack; record engine sessions for cleanup/costing."""
    await stack.runtime.stop()
    registry = EngineSessionRegistry(stack.registry_path)
    for vt_session_id, engine_session_id in stack.service._engine_sessions.items():
        registry.record(stack.scenario, vt_session_id, engine_session_id)
    await stack.service.aclose()


def engine_session_id_for(stack: ImStack, vt_session_id: str) -> Optional[str]:
    """The persisted D3 mapping (session.config) for a vt session."""
    session = stack.service.get_session(vt_session_id)
    if session is None:
        return None
    value = session.config.get(ENGINE_SESSION_CONFIG_KEY)
    return value if isinstance(value, str) and value else None


def attempt_status(store: SessionStore, attempt_id: str) -> Optional[str]:
    """Current status of *attempt_id* read fresh from disk (None if absent)."""
    for attempt in store.list_attempts():
        if attempt.attempt_id == attempt_id:
            return attempt.status.value
    return None


async def wait_attempt_terminal(
    store: SessionStore, attempt_id: str, timeout_s: float, poll_s: float = 1.0
) -> tuple[Optional[str], Optional[float]]:
    """Poll the store until the attempt lands terminal or the window expires.

    Returns ``(status_or_None, seconds_to_land_or_None)``.
    """
    t0 = time.time()
    deadline = t0 + timeout_s
    while time.time() < deadline:
        status = attempt_status(store, attempt_id)
        if status in _TERMINAL_STATUSES:
            return status, round(time.time() - t0, 3)
        await asyncio.sleep(poll_s)
    return None, None


# ---------------------------------------------------------------------------
# Engine-death scenario mechanics (port-ownership verified before any kill)
# ---------------------------------------------------------------------------


def verified_serve_pid(rig_state: Dict[str, Any]) -> int:
    """The rig's serve pid, verified to be OURS before anything kills it.

    Ownership proof: the process command line names opencode AND the process
    listens on the rig's serve port. Anything else raises — we never kill a
    process we cannot positively identify (parallel rigs use other ports).
    """
    pid = int(rig_state["serve_pid"])
    cmdline = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if "opencode" not in cmdline:
        raise RuntimeError(f"pid {pid} is not an opencode process: {cmdline!r}")
    port = rig_state["serve_url"].rsplit(":", 1)[-1]
    listeners = subprocess.run(
        ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    if f":{port}" not in listeners:
        raise RuntimeError(
            f"pid {pid} does not listen on serve port {port}; refusing to kill.\n"
            f"lsof: {listeners!r}"
        )
    return pid


def kill_serve(rig_state: Dict[str, Any]) -> tuple[float, int]:
    """SIGKILL the rig's serve after ownership verification; return (t_kill, pid)."""
    pid = verified_serve_pid(rig_state)
    t_kill = time.time()
    os.kill(pid, signal.SIGKILL)
    return t_kill, pid


def serve_alive(rig_state: Dict[str, Any]) -> bool:
    try:
        os.kill(int(rig_state["serve_pid"]), 0)
        return True
    except OSError:
        return False


def respawn_serve(rig_state: Dict[str, Any]) -> int:
    """Respawn the rig's serve on its port after the s2 kill (T8-1 Phase C).

    Blocking (spawn + readiness waits) — callers in async scenarios use
    ``asyncio.to_thread``. Mirrors the runner's transient-cleanup respawn
    (same scratch XDG config) but UPDATES ``rig_state.json`` with the new
    pid so the runner's cost/DELETE sweep and ``stop_rig`` reap the process
    this started.

    Two environment traps, both learned empirically (run-3 debug evidence):

    * ``HOME`` — the pytest process runs under ``agent/tests/conftest.py``'s
      sandbox HOME, but the ORIGINAL serve was spawned by the runner process
      with the REAL HOME, so its opencode data dir (provider auth + engine
      sessions, shared read-as-is per spike §7h) is ``~/.local/share/opencode``.
      Inheriting the sandbox HOME would give the respawn a DIFFERENT data dir
      (no auth, orphaned sessions the cleanup sweep cannot see) — restore the
      real home from the passwd entry (env-independent).
    * Readiness — ``/app`` answers HTML as soon as the listener binds, while
      the API/event subsystem keeps initializing (observed: ~14 s; requests
      queue until then, and a queued /event delivers no heartbeats). Gate on
      ``GET /mcp`` answering 200 — the same call the gateway preflight's
      ``load_tool_mapping`` makes — so the resume turn and the restarted
      pump meet a fully initialized serve.

    Port-ownership guard: refuses to bind unless the rig's serve port is
    free (the killed serve released it; a foreign listener raises).

    Returns:
        The respawned serve pid.
    """
    import pwd

    from tests.e2e_engine_bridge import start_rig

    rig_root = Path(rig_state["rig_root"])
    port = int(rig_state["serve_url"].rsplit(":", 1)[-1])
    if not start_rig._port_free(port):
        raise RuntimeError(
            f"serve port {port} is not free — refusing to respawn "
            "(foreign listener?)"
        )
    env = {
        **os.environ,
        "HOME": pwd.getpwuid(os.getuid()).pw_dir,
        "XDG_CONFIG_HOME": str(rig_root / "xdg"),
        "VIBE_TRADING_HOME": str(rig_root / "vt-home"),
    }
    env.pop("XDG_DATA_HOME", None)
    log = rig_root / "serve-respawn.log"
    proc = start_rig._spawn(
        [
            start_rig._find_opencode_bin(),
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
            "--print-logs",
            "--log-level",
            "INFO",
        ],
        cwd=rig_root / "workspace",
        env=env,
        log=log,
    )
    start_rig._wait_http(f"{rig_state['serve_url']}/app", 90.0, "respawned serve", log)
    _wait_serve_api_ready(rig_state["serve_url"], 90.0)
    state_path = rig_root / "rig_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["serve_pid"] = proc.pid
    state["serve_respawn_log"] = str(log)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    rig_state["serve_pid"] = proc.pid
    return proc.pid


def _wait_serve_api_ready(serve_url: str, timeout_s: float) -> None:
    """Block until the serve's API answers (GET /mcp 200), not just its UI."""
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{serve_url}/mcp", timeout=3.0) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        time.sleep(0.5)
    raise RuntimeError(
        f"respawned serve API not ready within {timeout_s}s ({serve_url}/mcp)"
    )


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return path


def copy_store_artifacts(
    stack: ImStack, evidence_dir: Path, scenario: str
) -> List[str]:
    """Copy the scenario's transcripts/attempt records into the evidence dir."""
    copied: List[str] = []
    target_root = evidence_dir / f"{scenario}-store"
    for session_dir in sorted(stack.store.base_dir.glob("*")):
        if not session_dir.is_dir():
            continue
        target = target_root / session_dir.name
        target.mkdir(parents=True, exist_ok=True)
        for name in ("session.json", "messages.jsonl"):
            src = session_dir / name
            if src.exists():
                (target / name).write_bytes(src.read_bytes())
                copied.append(str(target / name))
        for attempt_file in sorted(session_dir.glob("attempts/*/attempt.json")):
            dst = target / "attempts" / attempt_file.parent.name
            dst.mkdir(parents=True, exist_ok=True)
            (dst / "attempt.json").write_bytes(attempt_file.read_bytes())
            copied.append(str(dst / "attempt.json"))
    return copied
