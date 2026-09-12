"""opencode engine bridge — transport layer under the vt session seam.

Phase-1 scope (work plan T3): the :class:`EngineDriver` protocol (D12's four
primitives), the :class:`OpencodeDriver` implementation over the opencode
legacy ``/session`` REST + ``/event`` SSE surface, and the MCP tool-name
mapping table. Event *translation* to the vt vocabulary (T4) and the session
*service* contract (T5) build on top of this package; nothing here interprets
event semantics.
"""

from __future__ import annotations

from .driver import DriverSettings, EngineDriver, OpencodeDriver
from .errors import (
    OpencodeBridgeError,
    OpencodeConnectionError,
    OpencodeHttpError,
    OpencodeResponseShapeError,
)
from .events import OpencodeEvent
from .sse import SseFrame, SseFrameParser
from .tool_names import ToolNameMap, build_tool_name_map

__all__ = [
    "DriverSettings",
    "EngineDriver",
    "OpencodeBridgeError",
    "OpencodeConnectionError",
    "OpencodeDriver",
    "OpencodeEvent",
    "OpencodeHttpError",
    "OpencodeResponseShapeError",
    "SseFrame",
    "SseFrameParser",
    "ToolNameMap",
    "build_tool_name_map",
]
