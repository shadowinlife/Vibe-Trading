"""ClickHouse loader for A-share daily bars with data federation.

Queries the ``stk_factor_pro`` table (199 columns) via the ClickHouse
HTTP connector.  For pure historical ranges (end_date < today) it returns
the full CH data.  For ranges spanning today it federates CH (T-1 and
earlier) with a network source (today's OHLCV) via outer-join merge,
where CH columns take priority.

Non-A-share symbols and minute intervals return an empty dict so the
fallback chain continues.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

import pandas as pd

from backtest.loaders.base import cached_loader_fetch, validate_date_range
from backtest.loaders.registry import register

if TYPE_CHECKING:
    from src.clickhouse_connector import ClickHouseConnector

logger = logging.getLogger(__name__)


def _is_a_share(code: str) -> bool:
    """Return True when *code* is an A-share symbol (SH / SZ / BJ)."""
    return code.upper().endswith((".SH", ".SZ", ".BJ"))


def _yesterday() -> str:
    """Return yesterday's date as a YYYY-MM-DD string."""
    return (dt.date.today() - dt.timedelta(days=1)).isoformat()


def _today() -> str:
    """Return today's date as a YYYY-MM-DD string."""
    return dt.date.today().isoformat()


@register
class DataLoader:
    """ClickHouse-backed OHLCV loader with data federation.

    Pure historical ranges are served entirely from ClickHouse.  When the
    requested range crosses into today, the loader federates today's bars
    from the first available network source in the A-share fallback chain
    and outer-joins them with the CH data, letting CH columns take priority
    on overlapping dates.
    """

    name = "clickhouse"
    markets = {"a_share"}
    requires_auth = False

    def __init__(self) -> None:
        self._connector = self._build_connector()

    # ------------------------------------------------------------------
    # Protocol methods
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True when the ClickHouse server is reachable.

        Never raises — a connection failure returns ``False``.
        """
        try:
            return self._connector.health_check()
        except Exception:  # noqa: BLE001 — health_check is already defensive
            return False

    def fetch(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: list[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch A-share daily bars, federating today when needed.

        Args:
            codes: A-share stock codes (e.g. ``000001.SZ``).
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            interval: Bar size.  Only ``1D`` is supported; minute
                intervals return an empty dict.
            fields: Ignored — CH always returns all ``stk_factor_pro``
                columns.

        Returns:
            Mapping ``{code: DataFrame}``.  Non-A-share codes and
            minute intervals yield an empty dict so the fallback chain
            continues.
        """
        del fields  # CH always returns all columns
        validate_date_range(start_date, end_date)

        if interval != "1D":
            return {}

        end_dt = pd.Timestamp(end_date).date()
        today_dt = dt.date.today()
        needs_federation = end_dt >= today_dt

        # CH covers [start_date, min(end_date, yesterday)].
        ch_end = _yesterday() if needs_federation else end_date
        # Network covers [max(start_date, today), end_date] when federating.
        net_start = max(start_date, _today()) if needs_federation else None

        result: dict[str, pd.DataFrame] = {}

        for code in codes:
            if not _is_a_share(code):
                continue

            # --- 1. Fetch ClickHouse data -----------------------------------
            ch_df = self._fetch_ch_bars_cached(code, start_date, ch_end)

            # --- 2. Pure historical (no federation) → return CH directly ----
            if not needs_federation:
                if ch_df is not None and not ch_df.empty:
                    result[code] = self._normalize_ch_frame(ch_df)
                continue

            # --- 3. Federation: network today + CH merge ---------------------
            net_df = self._fetch_network_bars(code, net_start, end_date)  # type: ignore[arg-type]

            # Both empty → nothing to return.
            ch_empty = ch_df is None or ch_df.empty
            net_empty = net_df is None or net_df.empty

            if ch_empty and net_empty:
                continue
            if ch_empty:
                if net_df is not None:
                    result[code] = net_df
                continue
            if net_empty:
                if ch_df is not None:
                    result[code] = self._normalize_ch_frame(ch_df)
                continue

            # Merge: outer-join on trade_date, CH columns take priority.
            assert ch_df is not None and net_df is not None
            ch_norm = self._normalize_ch_frame(ch_df)
            merged = ch_norm.combine_first(net_df)
            result[code] = merged

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_connector() -> ClickHouseConnector:
        """Build a ClickHouse connector from the centralised config."""
        from src.clickhouse_connector import ClickHouseConnector
        from src.config.accessor import get_env_config

        cfg = get_env_config().data
        return ClickHouseConnector(
            host=cfg.clickhouse_host or None,
            port=cfg.clickhouse_port or None,
            user=cfg.clickhouse_user or None,
            password=cfg.clickhouse_password or None,
            database=cfg.clickhouse_database or None,
        )

    def _fetch_ch_bars_cached(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """Fetch all ``stk_factor_pro`` columns for *ts_code*, cached."""

        def _fetch() -> pd.DataFrame | None:
            try:
                return self._fetch_ch_bars_raw(ts_code, start_date, end_date)
            except Exception as exc:
                logger.warning(
                    "ClickHouse fetch failed for %s [%s, %s]: %s",
                    ts_code,
                    start_date,
                    end_date,
                    exc,
                )
                return None

        return cached_loader_fetch(
            source=self.name,
            symbol=ts_code,
            timeframe="1D",
            start_date=start_date,
            end_date=end_date,
            fields=None,  # CH returns all columns
            fetch=_fetch,
        )

    def _fetch_ch_bars_raw(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Query ``stk_factor_pro`` for all columns, sorted ascending."""
        sql = """
            SELECT *
            FROM stk_factor_pro
            WHERE ts_code = {ts_code:String}
              AND trade_date >= {start_date:String}
              AND trade_date <= {end_date:String}
            ORDER BY trade_date
        """
        return self._connector.query(
            sql,
            params={
                "param_ts_code": ts_code,
                "param_start_date": start_date,
                "param_end_date": end_date,
            },
        )

    @staticmethod
    def _normalize_ch_frame(df: pd.DataFrame) -> pd.DataFrame:
        """Normalise a CH DataFrame: date index, ``vol`` → ``volume``, numeric."""
        df = df.copy()
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.set_index("trade_date")
        df = df.sort_index()
        if "vol" in df.columns:
            df = df.rename(columns={"vol": "volume"})
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    @staticmethod
    def _fetch_network_bars(
        code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """Fetch OHLCV bars from the first available A-share network source.

        Uses the registry's fallback chain so the loader never hard-codes
        a specific network provider.  Returns ``None`` when no network
        source is available.
        """
        from backtest.loaders.registry import NoAvailableSourceError, resolve_loader

        try:
            loader = resolve_loader("a_share")
        except NoAvailableSourceError:
            logger.warning(
                "no network source available for federation of %s",
                code,
            )
            return None

        try:
            result = loader.fetch(
                [code],
                start_date=start_date,
                end_date=end_date,
                interval="1D",
            )
        except Exception as exc:
            logger.warning(
                "network fetch failed for %s [%s, %s]: %s",
                code,
                start_date,
                end_date,
                exc,
            )
            return None

        return result.get(code)
