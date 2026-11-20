"""Synthetic engine holding the worker live until the test releases it.

The entry-level live-projection test needs a dispatch whose process is
provably alive while find/status read it. This engine prints a ready marker,
then blocks inside ``generate`` until a ``release.marker`` file appears in the
run directory, so the mid-run observation window is event-defined rather than
timed. The release wait is bounded so a broken test cannot hang the worker for
the full 3600 second supervisor timeout.
"""

import os
import time

import pandas as pd

_RELEASE_WAIT_S = 120.0
_RELEASE_POLL_S = 0.05


class SignalEngine:
    """Gate one small CN lot signal behind a test-owned release marker."""

    def generate(self, data_map: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
        """Announce readiness, wait for the release marker, then signal once.

        Args:
            data_map: Sanitized OHLCV frames keyed by bare instrument symbol.

        Returns:
            One target-weight series per supplied instrument.

        Raises:
            RuntimeError: If the release marker never appears within the wait.
        """
        run_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        release = os.path.join(run_dir, "release.marker")
        print("VT_LIVE_PROJECTION_READY", flush=True)
        deadline = time.monotonic() + _RELEASE_WAIT_S
        while not os.path.exists(release):
            if time.monotonic() > deadline:
                raise RuntimeError("release marker never appeared")
            time.sleep(_RELEASE_POLL_S)
        print("VT_LOCAL_ENGINE_GENERATE", flush=True)
        return {
            symbol: pd.Series(
                [0.0, 0.11, 0.0, 0.0, 0.0],
                index=frame.index,
            )
            for symbol, frame in data_map.items()
        }
