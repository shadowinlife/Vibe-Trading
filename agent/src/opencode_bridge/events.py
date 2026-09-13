"""The opencode-native event envelope emitted by the driver.

The driver passes events through untouched — semantic translation to the vt
vocabulary is T4's EventTranslator job, and unknown event types must survive
the trip (version-drift defense, spike report §8: the translator allowlists,
the transport never filters).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping

logger = logging.getLogger("opencode_bridge")

__all__ = ["OpencodeEvent", "decode_event"]


@dataclass(frozen=True, slots=True)
class OpencodeEvent:
    """One parsed opencode-native event from the ``/event`` stream.

    Attributes:
        type: Wire event type (``message.part.delta``, ``session.idle``,
            ...). Unknown types are passed through — T4 filters.
        properties: The event's ``properties`` object (``{}`` when absent).
        raw: The full decoded wire payload (including ``id`` and ``type``),
            kept lossless for drift forensics and consumer dedup.
    """

    type: str
    properties: Mapping[str, Any]
    raw: Mapping[str, Any]


def decode_event(payload: str) -> OpencodeEvent | None:
    """Decode one SSE data payload; undecodable frames are dropped.

    Args:
        payload: The raw ``data:`` payload string of one SSE frame.

    Returns:
        The parsed :class:`OpencodeEvent`, or ``None`` when the payload is
        not a JSON object carrying a string ``type`` (such frames are
        ignored without crashing the stream — allowlist tolerance).
    """
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError:
        logger.debug("dropping undecodable /event frame: %.120s", payload)
        return None
    if not isinstance(raw, dict):
        logger.debug("dropping non-object /event frame: %.120s", payload)
        return None
    event_type = raw.get("type")
    if not isinstance(event_type, str) or not event_type:
        logger.debug("dropping /event frame without a type: %.120s", payload)
        return None
    properties = raw.get("properties")
    return OpencodeEvent(
        type=event_type,
        properties=properties if isinstance(properties, dict) else {},
        raw=raw,
    )
