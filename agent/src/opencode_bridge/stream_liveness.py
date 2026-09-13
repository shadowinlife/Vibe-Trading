"""Bounded engine-liveness detection for the persistent /event stream (T8-1).

Design choice (task T8-1: live engine-death mid-turn must land pending
attempts ``failed`` in <30 s; the old unbounded reconnect loop left them
hanging until the IM 600 s polling budget): liveness is tracked HERE, inside
the driver layer, and surfaced as a typed :class:`EnginePresumedDeadError`
raised out of :meth:`OpencodeDriver.events`. Rationale against the
alternatives:

* **Driver-internal surfacing (chosen)** — the driver is the only layer
  that sees byte-level liveness (every SSE frame, including the
  ``server.heartbeat`` that opencode emits every ~10 s even inside a
  120 s content-silent tool — T1 trace scenario g). The service pump's
  EXISTING no-hang path (``except Exception -> _fail_all_pending``) then
  fails pending attempts through the frozen T5 terminal shape, and the
  EXISTING ``_ensure_pumps`` restart-on-next-send re-establishes the stream
  when the engine returns — no gateway restart, no new service machinery.
* **Service-level watchdog (rejected)** — would re-derive byte liveness
  from translated events, conflating content silence (legitimate, unbounded)
  with transport silence (bounded by heartbeats); the translator's golden
  contracts are frozen and scenario g proves content gaps >120 s are normal.
* **In-place pump supervisor (rejected)** — restarting the pump task
  without letting it finish would defeat ``_await_terminal``'s done-pump
  guard, re-hanging attempts registered after a failure sweep.

Death signals (either suffices; both are sound per the heartbeat invariant,
neither fires on pure content silence):

1. ``liveness_max_silent_cycles`` consecutive connection cycles that
   delivered ZERO frames (connection-refused, HTTP 5xx, frameless EOF,
   read timeout). SIGKILL on localhost: immediate EOF + refused connects
   -> declared in ~3.5 s at the default backoff floor.
2. ``liveness_silence_window_s`` without ANY received frame, evaluated at
   every cycle end. A frozen-but-connected serve hits it on the first
   read-timeout cycle (~20 s, ``stream_read_timeout_s``).

Known bounded residual (documented, out of T8 scope — recovery.py module
docstring's zombie-turn edge): a serve that dies AND restarts faster than
signal 1's budget (~3.5 s) never gets declared dead; its in-flight turn
died with the old process, so the pending attempt waits like a long-silent
tool. New turns are unaffected (the stream re-establishes on reconnect).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .client import DriverSettings
from .errors import EnginePresumedDeadError

__all__ = ["StreamLiveness"]


@dataclass
class StreamLiveness:
    """Frame-liveness budget for one :meth:`OpencodeDriver.events` iteration.

    One instance per generator iteration (the budget is per stream
    lifetime, not per driver). The driver calls :meth:`note_frame` for
    every decoded event and :meth:`cycle_ended` at every connection-cycle
    boundary; the latter raises :class:`EnginePresumedDeadError` once a
    death signal fires.

    Attributes:
        base_url: Serve root, for the error message only.
        settings: Liveness thresholds (``liveness_*`` fields).
        clock: Monotonic time source (injectable for tests — no real
            waiting in unit tests).
    """

    base_url: str
    settings: DriverSettings
    clock: Callable[[], float] = time.monotonic
    last_frame_at: float = field(init=False)
    silent_cycles: int = field(init=False)

    def __post_init__(self) -> None:
        self.last_frame_at = self.clock()
        self.silent_cycles = 0

    def note_frame(self) -> None:
        """Record one received frame: proof of life resets BOTH signals."""
        self.last_frame_at = self.clock()
        self.silent_cycles = 0

    def cycle_ended(self, cycle_frames: int) -> None:
        """Evaluate the budget at one connection-cycle boundary.

        Args:
            cycle_frames: Frames the finished cycle delivered (a cycle
                that delivered any frame is not silent, even if the
                connection then dropped).

        Raises:
            EnginePresumedDeadError: A death signal fired — N consecutive
                frameless cycles, or the no-frame window elapsed.
        """
        if cycle_frames == 0:
            self.silent_cycles += 1
        else:
            self.silent_cycles = 0
        silence_s = self.clock() - self.last_frame_at
        if self.silent_cycles >= self.settings.liveness_max_silent_cycles:
            raise EnginePresumedDeadError(
                f"opencode serve at {self.base_url} presumed dead: "
                f"{self.silent_cycles} consecutive /event cycles delivered "
                f"no frame (last frame {silence_s:.1f}s ago; a live serve "
                "sends server.heartbeat every ~10s)"
            )
        if silence_s >= self.settings.liveness_silence_window_s:
            raise EnginePresumedDeadError(
                f"opencode serve at {self.base_url} presumed dead: no frame "
                f"received for {silence_s:.1f}s (window "
                f"{self.settings.liveness_silence_window_s:.0f}s = 1.5x the "
                "10s heartbeat cadence; content silence alone never trips "
                "this — heartbeats are wire bytes)"
            )
