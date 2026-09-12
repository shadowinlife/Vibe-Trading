"""IM streaming producer — the ``_stream_delta`` bypass (plan T9, D7/§7.5).

The IM half of the two-surface streaming contract: Web SSE keeps the T4/T5
token-delta passthrough (untouched — this tap only ADDS IM-bound
publications), IM channels get grinev-style progressive edits (§7.3:
1s→2s→5s→10s layered UNDER the manager's coalescing, ``manager.py:323-367``).
This is the FIRST producer of the ``_stream_delta`` vocabulary — the channels
layer (coalescing, ``base.py::send_delta``, the adapter edit buffers) shipped
as dormant consumer infrastructure; ``src/channels/`` stays zero-diff.

Contract (mirrored from the frozen consumers):

* **Addressing** — ``OutboundMessage(channel, chat_id)`` from the session's
  ``config`` ``{channel, channel_chat_id}`` (``runtime.py:329`` shape); web
  sessions carry no such config → the producer is inert for them.
* **Gating** — per-channel ``streaming`` switch with ``base.py``
  ``supports_streaming`` semantics (config enables AND the adapter
  implements ``send_delta``), re-checked at every attempt open. Switch OFF
  ⇒ ZERO publications; the terminal single-message fallback is unchanged.
* **Metadata** — deltas: ``{"_stream_delta": True, "_stream_id":
  attempt_id}``; segment close: ``{"_stream_end": True, "_stream_id":
  attempt_id}`` (+ ``"_resuming": True`` on intermediate closes — feishu's
  multi-tool-round semantics). ``_stream_id`` = attempt id isolates
  interleaved attempts in the adapters (QA: 乱序 delta 不串话). End markers
  deliberately do NOT carry ``_stream_delta``: the coalescer would absorb
  the marker into a merged delta batch whose content telegram/matrix
  discard on the ``_stream_end`` branch (text loss).
* **Throttle** — event-driven flushes (no background timers): first flush
  immediate, then one per ladder interval for the attempt's age
  (:data:`THROTTLE_LADDER`); tool events and terminals force a flush.
* **Segmentation & flush ordering** — one segment per translator ``iter``;
  pending text is ALWAYS flushed before a segment close or tool-boundary
  advance, so tool lines and question-class terminal content order after the
  text that preceded them (grinev flush ordering; the manager keeps FIFO —
  coalescing stops at any non-delta message).
* **Terminal** — ``_stream_end`` on ALL THREE terminals
  (completed/failed/cancelled), after a forced flush.

No duplicate final message: the producer never publishes a final itself —
the runtime's polled terminal message (``runtime.py:218-234``) is the only
one, and when the finalized stream delivered EXACTLY that content the
producer marks it with the manager's own ``_streamed`` flag (see
:meth:`ImStreamProducer._install_tagger`). Plumbing (bus + manager
singletons from ``src.api.state``) resolves LAZILY — the bridge service is
constructed before the channel runtime exists and channels may start later
(``POST /channels/start``); resolution failure keeps the producer inert.
"""

from __future__ import annotations

import logging
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

from src.channels.bus.events import OutboundMessage

if TYPE_CHECKING:
    from src.channels.bus.queue import MessageBus
    from src.channels.manager import ChannelManager
    from src.session.store import SessionStore

    from ._types import VtEventLike

logger = logging.getLogger("opencode_bridge")

__all__ = ["THROTTLE_LADDER", "ImStreamProducer", "resolve_channel_plumbing"]

#: grinev progressive-edit ladder (plan T9): ``(attempt-age upper bound s,
#: minimum publication interval s)`` — 1s→2s→5s→10s cap (:data:`THROTTLE_CAP_S`
#: past the last bound). Age is measured from stream open (≈ attempt start).
THROTTLE_LADDER: Tuple[Tuple[float, float], ...] = (
    (15.0, 1.0),
    (60.0, 2.0),
    (180.0, 5.0),
)
THROTTLE_CAP_S: float = 10.0

#: Bound on one-shot ``_streamed`` tagger records (evicts unconsumed ones).
_STREAMED_FINALS_CAP = 128

_TERMINAL_EVENTS = frozenset(
    ("attempt.completed", "attempt.failed", "attempt.cancelled")
)
#: Events forcing a pending-text flush (ordering contract: text precedes the
#: tool boundary; heartbeats give a trailing edge during long silent tools).
_BOUNDARY_EVENTS = frozenset(
    {"tool_call", "tool_result", "tool_heartbeat", "tool_progress", "stream_reset"}
)


def normalize_text(content: str) -> str:
    """Whitespace-fold *content* exactly like the manager's dedup fingerprint."""
    return " ".join(content.split())


def interval_for_age(age_s: float) -> float:
    """The ladder interval governing publications at attempt age *age_s*."""
    for bound, interval in THROTTLE_LADDER:
        if age_s < bound:
            return interval
    return THROTTLE_CAP_S


def resolve_channel_plumbing() -> Tuple[Optional[Any], Optional[Any]]:
    """Resolve the gateway's ``(MessageBus, ChannelManager)`` singletons.

    ``src.api.state`` globals first, ``api_server`` host-attr spelling as
    fallback (same resolution as ``wiring.stop_engine_bridge``); ``None``
    pieces while the channel runtime is unbuilt keep the producer inert.
    """
    from src.api import state as _state

    bus = getattr(_state, "_channel_bus", None)
    manager = getattr(_state, "_channel_manager", None)
    host = sys.modules.get("api_server")
    if host is not None:
        if bus is None:
            bus = getattr(host, "_channel_bus", None)
        if manager is None:
            manager = getattr(host, "_channel_manager", None)
    return bus, manager


@dataclass
class _AttemptStream:
    """Producer state for one in-flight attempt (one ``_stream_id``).

    ``buffer`` = text since the last publication; ``segment_text`` = all text
    of the current segment; ``segment_emitted``/``streamed_any`` = segment /
    attempt ever published a delta; ``last_emitted_text`` = normalized text of
    the most recent emitted segment (tagger source).
    """

    channel: str
    chat_id: str
    started_at: float
    segment_iter: Optional[int] = None
    buffer: str = ""
    segment_text: str = ""
    last_flush: Optional[float] = None
    segment_emitted: bool = False
    streamed_any: bool = False
    last_emitted_text: str = ""


class ImStreamProducer:
    """Bypass tap: translated ``text_delta`` events → per-channel stream edits.

    Attached as ``service.vt_event_observer`` by
    :func:`~src.opencode_bridge.wiring.build_session_service`; the dispatch
    loop awaits :meth:`handle_vt_event` per routed event (after the
    ``attempt_id`` stamp, before terminal resolution — so ``_stream_end``
    reaches the bus before the runtime's polled final can be published).
    """

    def __init__(
        self,
        *,
        store: "SessionStore",
        clock: Callable[[], float] = time.monotonic,
        plumbing_resolver: Callable[[], Tuple[Optional[Any], Optional[Any]]] = (
            resolve_channel_plumbing
        ),
    ) -> None:
        """Bind a producer.

        Args:
            store: Session store (``session.config`` → IM target, cached).
            clock: Injectable monotonic-seconds source (tests drive virtual
                time — no real sleeping).
            plumbing_resolver: ``(bus, manager)`` resolver override.
        """
        self._store = store
        self._clock = clock
        self._resolve_plumbing = plumbing_resolver
        self._bus: Optional[MessageBus] = None
        self._manager: Optional[ChannelManager] = None
        self._targets: Dict[str, Optional[Tuple[str, str]]] = {}
        self._streams: Dict[str, _AttemptStream] = {}
        self._streamed_finals: "OrderedDict[str, str]" = OrderedDict()
        self._wrapped_bus: Optional[MessageBus] = None

    def attach(self, service: Any) -> "ImStreamProducer":
        """Install as *service*'s vt-event observer (composition root)."""
        service.vt_event_observer = self.handle_vt_event
        return self

    def detach(self) -> None:
        """Restore a wrapped bus (shutdown/test hygiene; idempotent)."""
        bus = self._wrapped_bus
        if bus is not None and getattr(bus, "_vt_im_stream_tagger", None) is self:
            try:
                del bus.publish_outbound  # instance attr → class method again
                del bus._vt_im_stream_tagger
            except AttributeError:  # pragma: no cover - defensive
                pass
        self._wrapped_bus = None

    async def handle_vt_event(self, event: "VtEventLike", session_id: str) -> None:
        """Route one translated event (``attempt_id`` stamped) into the IM stream."""
        event_type = event.type
        if event_type == "text_delta":
            await self._on_text(event.data, session_id)
        elif event_type in _TERMINAL_EVENTS:
            await self._on_terminal(event_type, event.data)
        elif event_type in _BOUNDARY_EVENTS:
            await self._on_boundary(event.data)
        # reasoning_delta / llm_usage are not IM-stream content (T9 = text only).

    async def _on_text(self, data: Dict[str, Any], session_id: str) -> None:
        attempt_id = data.get("attempt_id")
        delta = data.get("delta")
        if not isinstance(attempt_id, str) or not isinstance(delta, str) or not delta:
            return
        stream = self._streams.get(attempt_id)
        if stream is None:
            stream = self._open(attempt_id, session_id)
            if stream is None:
                return
        iter_no = data.get("iter")
        if not isinstance(iter_no, int) or isinstance(iter_no, bool):
            iter_no = 1
        if stream.segment_iter is not None and iter_no != stream.segment_iter:
            # Iteration boundary: close the segment AFTER flushing its text
            # (flush ordering); the next segment opens a fresh edit target.
            await self._close_segment(attempt_id, stream, resuming=True)
        stream.segment_iter = iter_no
        stream.buffer += delta
        stream.segment_text += delta
        await self._flush(attempt_id, stream, force=False)

    async def _on_boundary(self, data: Dict[str, Any]) -> None:
        attempt_id = data.get("attempt_id")
        if not isinstance(attempt_id, str):
            return
        stream = self._streams.get(attempt_id)
        if stream is None:
            return
        # Pending text precedes the tool boundary (grinev flush ordering).
        await self._flush(attempt_id, stream, force=True)

    async def _on_terminal(self, event_type: str, data: Dict[str, Any]) -> None:
        attempt_id = data.get("attempt_id")
        if not isinstance(attempt_id, str):
            return
        stream = self._streams.pop(attempt_id, None)
        if stream is None:
            return  # never gated in (web session / switch OFF) → zero publications
        await self._flush(attempt_id, stream, force=True)
        if stream.segment_emitted:
            stream.last_emitted_text = normalize_text(stream.segment_text)
        # Terminal marker on ALL THREE terminals, even after an empty last
        # segment — adapters no-op on an empty buffer (contract uniformity).
        await self._publish(stream, "", {"_stream_end": True, "_stream_id": attempt_id})
        if event_type != "attempt.completed" or not stream.streamed_any:
            return
        summary = data.get("summary")
        if (
            isinstance(summary, str)
            and summary
            and normalize_text(summary) == stream.last_emitted_text
        ):
            # The stream already delivered this exact text: arm the one-shot
            # `_streamed` mark for the runtime's polled final message.
            self._streamed_finals[attempt_id] = stream.last_emitted_text
            while len(self._streamed_finals) > _STREAMED_FINALS_CAP:
                self._streamed_finals.popitem(last=False)

    def _open(self, attempt_id: str, session_id: str) -> Optional[_AttemptStream]:
        """Gate one attempt in; ``None`` keeps it out (re-checked next event)."""
        target = self._target_for(session_id)
        if target is None:
            return None
        if self._bus is None or self._manager is None:
            bus, manager = self._resolve_plumbing()
            if bus is None or manager is None:
                return None  # channel runtime not built → nobody would consume
            self._bus, self._manager = bus, manager
        channel = self._manager.channels.get(target[0])
        if channel is None or not channel.supports_streaming:
            return None  # switch OFF (or no send_delta) → zero publications
        self._install_tagger(self._bus)
        stream = _AttemptStream(
            channel=target[0], chat_id=target[1], started_at=self._clock()
        )
        self._streams[attempt_id] = stream
        return stream

    def _target_for(self, session_id: str) -> Optional[Tuple[str, str]]:
        """Cached ``(channel, channel_chat_id)`` from ``session.config``."""
        if session_id in self._targets:
            return self._targets[session_id]
        target: Optional[Tuple[str, str]] = None
        session = self._store.get_session(session_id)
        config = getattr(session, "config", None)
        if isinstance(config, dict):
            channel, chat_id = config.get("channel"), config.get("channel_chat_id")
            if all(isinstance(v, str) and v for v in (channel, chat_id)):
                target = (channel, chat_id)
        self._targets[session_id] = target
        return target

    async def _flush(
        self, attempt_id: str, stream: _AttemptStream, *, force: bool
    ) -> None:
        """Publish the pending buffer when the grinev ladder allows (or forced)."""
        if not stream.buffer:
            return
        now = self._clock()
        if not force and stream.last_flush is not None:
            if now - stream.last_flush < interval_for_age(now - stream.started_at):
                return
        text, stream.buffer = stream.buffer, ""
        stream.last_flush = now
        stream.segment_emitted = True
        stream.streamed_any = True
        await self._publish(
            stream, text, {"_stream_delta": True, "_stream_id": attempt_id}
        )

    async def _close_segment(
        self, attempt_id: str, stream: _AttemptStream, *, resuming: bool
    ) -> None:
        """Flush pending text, then close the segment (empty ones are skipped)."""
        await self._flush(attempt_id, stream, force=True)
        if not stream.segment_emitted:
            return
        stream.last_emitted_text = normalize_text(stream.segment_text)
        metadata: Dict[str, Any] = {"_stream_end": True, "_stream_id": attempt_id}
        if resuming:
            metadata["_resuming"] = True  # feishu: more tool rounds coming
        await self._publish(stream, "", metadata)
        stream.buffer = ""
        stream.segment_text = ""
        stream.segment_emitted = False

    async def _publish(
        self, stream: _AttemptStream, content: str, metadata: Dict[str, Any]
    ) -> None:
        bus = self._bus
        if bus is None:  # pragma: no cover - _open guarantees a bus
            return
        await bus.publish_outbound(
            OutboundMessage(
                channel=stream.channel,
                chat_id=stream.chat_id,
                content=content,
                metadata=metadata,
            )
        )

    def _install_tagger(self, bus: "MessageBus") -> None:
        """Decorate the bus's ``publish_outbound`` once (idempotent, reversible).

        Only runtime finals (``_channel_runtime`` + a recorded ``attempt_id``)
        whose content the stream provably delivered are marked; every other
        message passes through byte-identical.
        """
        if self._wrapped_bus is bus:
            return
        if getattr(bus, "_vt_im_stream_tagger", None) is not None:
            logger.warning("MessageBus already carries an IM-stream tagger; skipping")
            return
        original = bus.publish_outbound
        producer = self

        async def publish_outbound(msg: OutboundMessage) -> None:
            producer._mark_streamed(msg)
            await original(msg)

        bus.publish_outbound = publish_outbound  # type: ignore[method-assign]
        bus._vt_im_stream_tagger = self  # type: ignore[attr-defined]
        self._wrapped_bus = bus

    def _mark_streamed(self, msg: OutboundMessage) -> None:
        """One-shot: tag a runtime final already delivered by the stream."""
        metadata = msg.metadata
        if not metadata or not metadata.get("_channel_runtime"):
            return
        attempt_id = metadata.get("attempt_id")
        if not isinstance(attempt_id, str):
            return
        expected = self._streamed_finals.get(attempt_id)
        if expected is None or normalize_text(msg.content or "") != expected:
            return  # mismatch (proposal suffix, continuation) → final passes through
        metadata["_streamed"] = True
        del self._streamed_finals[attempt_id]
        logger.info("runtime final for attempt %s marked _streamed", attempt_id)
