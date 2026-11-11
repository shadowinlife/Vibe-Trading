"""Deterministic sanitized strategy for the offline execution integration."""

import pandas as pd


class SignalEngine:
    """Open one small CN lot on the third bar, then exit on the fourth."""

    def generate(self, data_map: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
        """Return one next-bar-open entry followed by a zero target.

        Args:
            data_map: Sanitized OHLCV frames keyed by bare instrument symbol.

        Returns:
            One target-weight series per supplied instrument.
        """
        print("VT_LOCAL_ENGINE_GENERATE", flush=True)
        return {
            symbol: pd.Series(
                [0.0, 0.11, 0.0, 0.0, 0.0],
                index=frame.index,
            )
            for symbol, frame in data_map.items()
        }
