"""ClickHouse loader for A-share daily bars with data federation.

Each symbol is routed to the table that actually serves its instrument class:

* stocks (``600519.SH``) → ``stk_factor_pro`` (199 columns)
* ETFs / LOFs (``510300.SH``, ``159915.SZ``) → ``fund_daily``
* price indices (``000300.SH``, ``399001.SZ``, ``899050.BJ``) →
  ``idx_factor_pro``

For pure historical ranges (end_date < today) the loader returns the full CH
data.  For ranges spanning today it federates CH (T-1 and earlier) with a
network source (today's OHLCV) via outer-join merge, where CH columns take
priority.

No SELECT uses ``*``: each table has its own explicit column list (generated
from the DDL snapshots under ``schema/clickhouse/``), so a schema change fails
loudly instead of silently leaking new columns (Flow-B closure,
mymain-wiki/clickhouse/CLICKHOUSE_ITERATION_PLAN.md P1.1).

``trade_date`` is a ``Date`` live but a ``YYYYMMDD`` String on older
deployments, so ``fund_daily`` casts it with ``toDate(trade_date)`` and the
normaliser parses ``format="ISO8601"``, which accepts both spellings.  Frames
carry additive unit metadata under ``df.attrs["_provenance"]`` (P1.2).

Non-A-share symbols and minute intervals return an empty dict so the fallback
chain continues.  An empty ClickHouse answer is logged with symbol, table and
date range rather than failing silently.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import TYPE_CHECKING

import pandas as pd

from backtest.loaders.base import cached_loader_fetch, validate_date_range
from backtest.loaders.clickhouse_columns import table_column_sql
from backtest.loaders.registry import register
from backtest.source_contract import federation_from_attrs, normalize_federation

if TYPE_CHECKING:
    from src.clickhouse_connector import ClickHouseConnector

logger = logging.getLogger(__name__)

#: Six-digit A-share code with an exchange suffix.
_A_SHARE_SYMBOL = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$", re.IGNORECASE)
#: Price-index code ranges.  The suffix decides: ``000001.SH`` is the SSE
#: Composite while ``000001.SZ`` is Ping An Bank.
_A_SHARE_INDEX_SYMBOL = re.compile(r"^(?:000\d{3}\.SH|399\d{3}\.SZ|899\d{3}\.BJ)$", re.IGNORECASE)
#: Prefix ranges of exchange-listed funds (ETF / LOF) on SH and SZ.
_FUND_CODE_PREFIXES = frozenset({"15", "16", "50", "51", "52", "56", "58"})


def resolve_table(code: str) -> str | None:
    """Return the ClickHouse table that serves *code*, or ``None`` when unrouted.

    The exchange suffix, not the numeric prefix, decides whether a ``000xxx``
    code is an index (``000001.SH``) or a stock (``000001.SZ``).  ETF/LOF codes
    are six-digit Shanghai or Shenzhen fund codes whose first two digits fall
    in the exchange's fund ranges.

    Args:
        code: Symbol such as ``600519.SH``, ``510300.SH`` or ``000300.SH``.

    Returns:
        ``"stk_factor_pro"``, ``"fund_daily"``, ``"idx_factor_pro"``, or
        ``None`` for a non-A-share symbol that this loader does not serve.
    """
    if not _A_SHARE_SYMBOL.match(code):
        return None
    upper = code.upper()
    if _A_SHARE_INDEX_SYMBOL.match(upper):
        return "idx_factor_pro"
    exchange = upper[-2:]
    if exchange in {"SH", "SZ"} and upper[:2] in _FUND_CODE_PREFIXES:
        return "fund_daily"
    return "stk_factor_pro"


def _bar_query(table: str) -> str:
    """Build one table's ascending OHLCV query with its own column list.

    ``fund_daily`` casts ``trade_date`` with ``toDate()``: the column is a
    ``Date`` live but a ``YYYYMMDD`` String on older deployments, and a
    String-versus-Date comparison would compare two different types.
    """
    if table == "fund_daily":
        start_predicate = "toDate(trade_date) >= toDate({start_date:String})"
        end_predicate = "toDate(trade_date) <= toDate({end_date:String})"
    else:
        start_predicate = "trade_date >= {start_date:String}"
        end_predicate = "trade_date <= {end_date:String}"
    return f"""
            SELECT {table_column_sql(table)}
            FROM {table}
            WHERE ts_code = {{ts_code:String}}
              AND {start_predicate}
              AND {end_predicate}
            ORDER BY trade_date
        """


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
        self._ch_fetch_error: Exception | None = None

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
            codes: A-share codes; stocks, ETFs/LOFs and price indices each
                resolve to their own ClickHouse table.
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            interval: Bar size.  Only ``1D`` is supported; minute
                intervals return an empty dict.
            fields: Ignored — CH returns the routed table's full explicit
                column contract.

        Returns:
            Mapping ``{code: DataFrame}``.  Non-A-share codes and
            minute intervals yield an empty dict so the fallback chain
            continues.  Frames served (fully or partly) from ClickHouse
            carry additive unit metadata under ``attrs["_provenance"]``.
        """
        del fields  # CH returns the full explicit column contract
        validate_date_range(start_date, end_date)

        if interval != "1D":
            return {}

        end_dt = pd.Timestamp(end_date).date()
        today_dt = dt.date.today()
        needs_federation = end_dt >= today_dt

        # CH covers [start_date, min(end_date, yesterday)].
        ch_end = _yesterday() if needs_federation else end_date
        # A request whose start is already today has no T-1 window at all.
        ch_has_window = pd.Timestamp(start_date).date() <= pd.Timestamp(ch_end).date()
        # Network covers [max(start_date, today), end_date] when federating.
        net_start = max(start_date, _today()) if needs_federation else None

        result: dict[str, pd.DataFrame] = {}

        for code in codes:
            table = resolve_table(code)
            if table is None:
                continue

            # --- 1. Fetch ClickHouse data -----------------------------------
            self._ch_fetch_error = None
            ch_df = self._fetch_ch_bars_cached(code, table, start_date, ch_end) if ch_has_window else None
            ch_empty = ch_df is None or ch_df.empty
            if ch_empty and ch_has_window:
                logger.warning(
                    "ClickHouse %s returned no bars for %s over [%s, %s]; continuing down the fallback chain",
                    table,
                    code,
                    start_date,
                    ch_end,
                )

            # --- 2. Pure historical (no federation) → return CH directly ----
            if not needs_federation:
                if self._ch_fetch_error is not None:
                    raise self._ch_fetch_error
                if ch_df is not None and not ch_df.empty:
                    result[code] = self._with_provenance(
                        self._normalize_ch_frame(ch_df),
                        table,
                        start_date=start_date,
                        end_date=ch_end,
                    )
                continue

            # --- 3. Federation: network today + CH merge ---------------------
            assert net_start is not None
            net_df = self._fetch_network_bars(code, net_start, end_date)
            net_empty = net_df is None or net_df.empty
            realtime_leg = (
                self._realtime_leg(net_df, code, net_start, end_date) if net_df is not None and not net_empty else None
            )

            # Both empty → nothing to return. A planned but unserved network
            # request is not evidence and therefore never becomes a leg.
            if ch_empty and net_empty:
                if self._ch_fetch_error is not None:
                    raise self._ch_fetch_error
                continue
            if ch_empty:
                if net_df is not None and realtime_leg is not None:
                    fallback_reason = (
                        "clickhouse:fetch_failed"
                        if self._ch_fetch_error is not None
                        else "clickhouse:no_data"
                    )
                    result[code] = self._with_realtime_provenance(
                        net_df, realtime_leg, fallback_reason=fallback_reason
                    )
                continue
            if net_empty:
                if ch_df is not None:
                    result[code] = self._with_provenance(
                        self._normalize_ch_frame(ch_df),
                        table,
                        start_date=start_date,
                        end_date=ch_end,
                    )
                continue

            # Merge: outer-join on trade_date, CH columns take priority.
            assert ch_df is not None and net_df is not None and realtime_leg is not None
            ch_norm = self._normalize_ch_frame(ch_df)
            merged = ch_norm.combine_first(net_df)
            result[code] = self._with_provenance(
                merged,
                table,
                start_date=start_date,
                end_date=ch_end,
                realtime=realtime_leg,
            )

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
        table: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """Fetch *table*'s explicit column contract for *ts_code*, cached."""
        try:
            return cached_loader_fetch(
                source=self.name,
                symbol=ts_code,
                timeframe="1D",
                start_date=start_date,
                end_date=end_date,
                fields=None,  # CH returns the full explicit column contract
                fetch=lambda: self._fetch_ch_bars_raw(
                    ts_code, table, start_date, end_date
                ),
            )
        except Exception as exc:
            self._ch_fetch_error = exc
            logger.warning(
                "ClickHouse %s fetch failed for %s [%s, %s]: %s",
                table,
                ts_code,
                start_date,
                end_date,
                exc,
            )
            return None

    def _fetch_ch_bars_raw(
        self,
        ts_code: str,
        table: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Query *table* with its own explicit column list, ascending."""
        return self._connector.query(
            _bar_query(table),
            params={
                "param_ts_code": ts_code,
                "param_start_date": start_date,
                "param_end_date": end_date,
            },
        )

    @staticmethod
    def _with_provenance(
        df: pd.DataFrame,
        table: str,
        *,
        start_date: str,
        end_date: str,
        realtime: dict[str, object] | None = None,
    ) -> pd.DataFrame:
        existing = df.attrs.get("_provenance")
        provenance = dict(existing) if isinstance(existing, dict) else {}
        try:
            from src.clickhouse_units import clickhouse_bar_provenance

            provenance.update(clickhouse_bar_provenance(table))
        except Exception as exc:  # noqa: BLE001 — unit metadata is best-effort
            logger.debug("clickhouse provenance metadata unavailable: %s", exc)
        provenance["table"] = table
        history = {
            "source": "clickhouse",
            "table": table,
            "start_date": start_date,
            "end_date": end_date,
            "adjustment": provenance.get("price_adjust"),
            "volume_unit": provenance.get("volume_unit"),
            "amount_unit": provenance.get("amount_unit"),
        }
        federation: dict[str, object] = {
            "mode": "federated" if realtime is not None else "clickhouse_only",
            "history": history,
        }
        if realtime is not None:
            federation["realtime"] = realtime
        provenance["federation"] = normalize_federation(federation)
        df.attrs["_provenance"] = provenance
        return df

    @staticmethod
    def _with_realtime_provenance(
        df: pd.DataFrame,
        realtime: dict[str, object],
        *,
        fallback_reason: str = "clickhouse:no_data",
    ) -> pd.DataFrame:
        existing = df.attrs.get("_provenance")
        provenance = dict(existing) if isinstance(existing, dict) else {}
        provenance["federation"] = normalize_federation({"mode": "realtime_only", "realtime": realtime})
        provenance["fallback_reason"] = fallback_reason
        df.attrs["_provenance"] = provenance
        return df

    @staticmethod
    def _realtime_leg(df: pd.DataFrame, code: str, start_date: str, end_date: str) -> dict[str, object]:
        existing = federation_from_attrs(df.attrs)
        if existing is not None and existing["mode"] == "realtime_only":
            return existing["realtime"]

        # Compatibility for tests that replace _fetch_network_bars directly.
        # Production frames take the named-loader path below instead.
        from backtest.loaders.registry import frame_caliber

        raw_provenance = df.attrs.get("_provenance")
        provenance = raw_provenance if isinstance(raw_provenance, dict) else {}
        raw_source = provenance.get("source")
        source = raw_source if isinstance(raw_source, str) and raw_source else "unknown"
        return {
            "source": source,
            "start_date": start_date,
            "end_date": end_date,
            "adjustment": frame_caliber(df, source, "a_share", code),
            "volume_unit": provenance.get("volume_unit"),
            "amount_unit": provenance.get("amount_unit"),
        }

    @staticmethod
    def _build_realtime_leg(
        loader: object,
        df: pd.DataFrame,
        code: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, object]:
        from backtest.loaders.registry import frame_caliber

        source = getattr(loader, "name", None)
        if not isinstance(source, str) or not source:
            raise ValueError("resolved network loader has no source name")
        raw_provenance = df.attrs.get("_provenance")
        provenance = raw_provenance if isinstance(raw_provenance, dict) else {}
        volume_units = getattr(loader, "volume_units", None)
        volume_units = volume_units if isinstance(volume_units, dict) else {}
        amount_units = getattr(loader, "amount_units", None)
        amount_units = amount_units if isinstance(amount_units, dict) else {}
        return {
            "source": source,
            "start_date": start_date,
            "end_date": end_date,
            "adjustment": frame_caliber(df, source, "a_share", code),
            "volume_unit": provenance.get("volume_unit") or volume_units.get("a_share"),
            "amount_unit": provenance.get("amount_unit") or amount_units.get("a_share"),
        }

    @staticmethod
    def _normalize_ch_frame(df: pd.DataFrame) -> pd.DataFrame:
        """Normalise a CH frame: date index, ``vol`` → ``volume``, numeric.

        ``trade_date`` is ISO 8601 for a ``Date`` column and ``YYYYMMDD`` for
        a legacy String column; ``format="ISO8601"`` accepts both.
        """
        df = df.copy()
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="ISO8601")
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
        a specific network provider.  ClickHouse is excluded: the A-share
        chain leads with it, and resolving it here would re-enter this
        loader's federation path until the recursion limit.  Returns
        ``None`` when no network source is available.
        """
        from backtest.loaders.registry import NoAvailableSourceError, resolve_loader

        try:
            loader = resolve_loader("a_share", exclude={"clickhouse"})
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
            frame = (result or {}).get(code)
            if frame is None:
                return None
            realtime = DataLoader._build_realtime_leg(loader, frame, code, start_date, end_date)
        except Exception as exc:
            logger.warning(
                "network fetch failed for %s [%s, %s]: %s",
                code,
                start_date,
                end_date,
                exc,
            )
            return None

        return DataLoader._with_realtime_provenance(frame, realtime)
