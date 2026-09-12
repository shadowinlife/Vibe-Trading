"""opencode engine bridge — the vt session seam over an opencode engine.

Phase-1 surface (work plan T3-T7, complete):

* transport (T3): the :class:`EngineDriver` protocol (D12's four
  primitives), :class:`OpencodeDriver` over the opencode legacy
  ``/session`` REST + ``/event`` SSE surface, and the MCP tool-name map;
* translation (T4): :class:`EventTranslator`, the opencode-event ->
  vt-SSE-vocabulary state machine (quiescence terminals, tool heartbeats,
  partID->part.type join, sessionID-scoped idle);
* service (T5): :class:`OpencodeSessionService`, the SessionService seam
  contract (D6) with the D8 prompt-injection block;
* recovery (T6): :class:`RecoverableOpencodeSessionService`, the
  three-branch startup reconciliation + delete cascade (D3);
* wiring (T7): :func:`build_session_service` / :func:`preflight_engine_bridge`
  / :func:`stop_engine_bridge`, consumed by the ``api/state.py`` factory
  switch and the ``api_server`` lifespan (``VIBE_TRADING_ENGINE=opencode``).

The native construction path never imports this package.
"""

from __future__ import annotations

from .driver import DriverSettings, EngineDriver, OpencodeDriver
from .errors import (
    EnginePresumedDeadError,
    OpencodeBridgeError,
    OpencodeConnectionError,
    OpencodeHttpError,
    OpencodeResponseShapeError,
)
from .events import OpencodeEvent
from .im_stream import ImStreamProducer
from .recovery import (
    ENGINE_SESSION_CONFIG_KEY,
    ReconciliationReport,
    RecoverableOpencodeSessionService,
    delete_engine_session,
)
from .service import OpencodeSessionService
from .sse import SseFrame, SseFrameParser
from .tool_names import ToolNameMap, build_tool_name_map
from .translator import VT_EVENT_TYPES, EventTranslator, VtEvent
from .wiring import (
    build_session_service,
    preflight_engine_bridge,
    start_session_service,
    stop_engine_bridge,
)

__all__ = [
    "ENGINE_SESSION_CONFIG_KEY",
    "DriverSettings",
    "EngineDriver",
    "EnginePresumedDeadError",
    "EventTranslator",
    "ImStreamProducer",
    "OpencodeBridgeError",
    "OpencodeConnectionError",
    "OpencodeDriver",
    "OpencodeEvent",
    "OpencodeHttpError",
    "OpencodeResponseShapeError",
    "OpencodeSessionService",
    "ReconciliationReport",
    "RecoverableOpencodeSessionService",
    "SseFrame",
    "SseFrameParser",
    "ToolNameMap",
    "VT_EVENT_TYPES",
    "VtEvent",
    "build_session_service",
    "build_tool_name_map",
    "delete_engine_session",
    "preflight_engine_bridge",
    "start_session_service",
    "stop_engine_bridge",
]
