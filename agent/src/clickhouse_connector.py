"""
ClickHouse HTTP connector for Vibe-Trading.

Provides a standalone `ClickHouseConnector` class that queries ClickHouse
via the native HTTP interface (port 8123), returning pandas DataFrames.
No heavy driver dependencies — only `requests` and `pandas`.

Connection parameters are resolved from the centralized env config
(``DataConfig``), falling back to sensible defaults for the internal VPC
ClickHouse instance.

Environment Variables
---------------------
CLICKHOUSE_HOST : str
    ClickHouse host (default ``172.24.165.51``).
CLICKHOUSE_PORT : int
    HTTP port (default ``8123``).
CLICKHOUSE_USER : str
    Username (default ``"default"``).
CLICKHOUSE_PASSWORD : str
    Password (default ``""`` — no auth).
CLICKHOUSE_DATABASE : str
    Default database (default ``"ashare"``).
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import requests


class ClickHouseConnector:
    """HTTP-based ClickHouse connector returning pandas DataFrames.

    Talks to ClickHouse through the native HTTP query interface at
    ``http://host:port/``.  Requests are sent as POST with query
    parameters so the SQL body is not limited by URL length.

    Defaults target the internal VPC ClickHouse instance and can be
    overridden via the ``CLICKHOUSE_*`` environment variables (declared in
    ``src.config.env_schema.DataConfig``).

    Parameters
    ----------
    host : str | None
        ClickHouse host.  Defaults to the ``CLICKHOUSE_HOST`` config value.
    port : int | None
        HTTP port.  Defaults to the ``CLICKHOUSE_PORT`` config value.
    user : str | None
        Username.  Defaults to the ``CLICKHOUSE_USER`` config value.
    password : str | None
        Password.  Defaults to the ``CLICKHOUSE_PASSWORD`` config value.
    database : str | None
        Default database.  Defaults to the ``CLICKHOUSE_DATABASE`` config
        value.
    """

    _DEFAULT_HOST = "172.24.165.51"
    _DEFAULT_PORT = 8123
    _DEFAULT_USER = ""
    _DEFAULT_PASSWORD = ""
    _DEFAULT_DATABASE = "ashare"
    _TIMEOUT = 30

    _DAILY_BARS_DEFAULT_FIELDS = [
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "close_hfq",
        "vol",
        "amount",
        "turnover_rate",
        "pe_ttm",
        "pb",
    ]

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ) -> None:
        cfg_host, cfg_port, cfg_user, cfg_password, cfg_database = (
            self._config_defaults()
        )
        self.host = host or cfg_host
        self.port = port or cfg_port
        self.user = user or cfg_user
        self.password = password or cfg_password
        self.database = database or cfg_database
        self._base_url = f"http://{self.host}:{self.port}/"

    @classmethod
    def _config_defaults(cls) -> tuple[str, int, str, str, str]:
        """Return (host, port, user, password, database) from env config.

        Falls back to the class defaults when the config layer is
        unavailable, so the connector stays import- and construction-safe
        under a malformed environment.
        """
        try:
            from src.config.accessor import get_env_config

            data = get_env_config().data
            return (
                data.clickhouse_host or cls._DEFAULT_HOST,
                data.clickhouse_port or cls._DEFAULT_PORT,
                data.clickhouse_user or cls._DEFAULT_USER,
                data.clickhouse_password or cls._DEFAULT_PASSWORD,
                data.clickhouse_database or cls._DEFAULT_DATABASE,
            )
        except Exception:  # noqa: BLE001 - degrade to built-in defaults
            return (
                cls._DEFAULT_HOST,
                cls._DEFAULT_PORT,
                cls._DEFAULT_USER,
                cls._DEFAULT_PASSWORD,
                cls._DEFAULT_DATABASE,
            )

    # ------------------------------------------------------------------
    # Core query method
    # ------------------------------------------------------------------

    def query(self, sql: str, params: dict[str, str] | None = None) -> pd.DataFrame:
        """Execute a raw SQL statement and return the result as a DataFrame.

        Sends the SQL via HTTP POST with ``default_format=JSONCompact``.
        ClickHouse parameterized queries use ``{name:Type}`` placeholders
        bound via ``param_name`` query parameters.

        Parameters
        ----------
        sql : str
            SQL statement to execute.  May contain ClickHouse-style
            parameter placeholders like ``{ts_code:String}``.
        params : dict[str, str] | None
            Parameter bindings as ``{"param_<name>": value}`` dict.
            These are sent as URL query parameters.

        Returns
        -------
        pd.DataFrame
            Query result.  Columns are named after the ``meta`` block.
            Returns an empty DataFrame when ``rows`` is 0.

        Raises
        ------
        ConnectionError
            If the ClickHouse server is unreachable or returns an HTTP error.
        """
        query_params: dict[str, str] = {
            "database": self.database,
            "default_format": "JSONCompact",
        }
        if params:
            query_params.update(params)
        if self.user:
            query_params["user"] = self.user
        if self.password:
            query_params["password"] = self.password

        try:
            resp = requests.post(
                self._base_url,
                params=query_params,
                data=sql.encode("utf-8"),
                timeout=self._TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ConnectionError(
                f"Failed to connect to ClickHouse at {self._base_url}: {exc}"
            ) from exc

        if not resp.ok:
            raise ConnectionError(
                f"ClickHouse returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        payload: dict[str, Any] = resp.json()

        meta: list[dict[str, Any]] = payload.get("meta", [])
        data: list[list[Any]] = payload.get("data", [])
        rows_count: int = payload.get("rows", 0)

        if not meta or rows_count == 0:
            return pd.DataFrame()

        columns = [col["name"] for col in meta]
        return pd.DataFrame(data, columns=columns)

    # ------------------------------------------------------------------
    # Health / discovery
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        """Check whether the ClickHouse server is reachable.

        Returns
        -------
        bool
            ``True`` if the server responds successfully, ``False`` otherwise.
            Never raises.
        """
        try:
            resp = requests.get(
                self._base_url,
                params={"query": "SELECT 1"},
                timeout=self._TIMEOUT,
            )
            return resp.ok
        except requests.RequestException:
            return False

    def list_tables(self) -> pd.DataFrame:
        """List all tables in the default database with row counts.

        Returns
        -------
        pd.DataFrame
            Columns: ``table``, ``rows``.
        """
        sql = """
            SELECT
                name          AS table,
                total_rows    AS rows
            FROM system.tables
            WHERE database = {database:String}
            ORDER BY name
        """
        return self.query(sql, params={"param_database": self.database})

    # ------------------------------------------------------------------
    # Daily bars
    # ------------------------------------------------------------------

    def get_daily_bars(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """Fetch daily OHLCV and factor data for a single stock.

        Queries the ``stk_factor_pro`` table.

        Parameters
        ----------
        ts_code : str
            Tushare-style stock code, e.g. ``"000001.SZ"``.
        start_date : str
            Inclusive start date, ``YYYY-MM-DD``.
        end_date : str
            Inclusive end date, ``YYYY-MM-DD``.
        fields : list[str] | None
            Columns to return.  Defaults to a curated set of 11 fields:
            trade_date, open, high, low, close, close_hfq, vol, amount,
            turnover_rate, pe_ttm, pb.

        Returns
        -------
        pd.DataFrame
            Daily bars sorted by ``trade_date`` ascending.
        """
        columns = fields if fields else self._DAILY_BARS_DEFAULT_FIELDS
        col_str = ", ".join(columns)

        sql = f"""
            SELECT {col_str}
            FROM stk_factor_pro
            WHERE ts_code = {{ts_code:String}}
              AND trade_date >= {{start_date:String}}
              AND trade_date <= {{end_date:String}}
            ORDER BY trade_date
        """
        return self.query(
            sql,
            params={
                "param_ts_code": ts_code,
                "param_start_date": start_date,
                "param_end_date": end_date,
            },
        )

    # ------------------------------------------------------------------
    # Financial indicators
    # ------------------------------------------------------------------

    def get_financial_indicators(
        self,
        ts_code: str,
        periods: int = 8,
    ) -> pd.DataFrame:
        """Fetch key financial indicators for a stock.

        Queries the ``fin_indicator`` table, returning the most recent
        *periods* reporting periods.

        Parameters
        ----------
        ts_code : str
            Tushare-style stock code, e.g. ``"000001.SZ"``.
        periods : int
            Number of most-recent reporting periods to return (default 8).

        Returns
        -------
        pd.DataFrame
            Financial indicators sorted by ``end_date`` descending.
        """
        sql = """
            SELECT *
            FROM fin_indicator
            WHERE ts_code = {ts_code:String}
            ORDER BY end_date DESC
            LIMIT {periods:UInt32}
        """
        return self.query(
            sql,
            params={
                "param_ts_code": ts_code,
                "param_periods": str(periods),
            },
        )

    # ------------------------------------------------------------------
    # Money flow
    # ------------------------------------------------------------------

    def get_moneyflow(
        self,
        ts_code: str,
        days: int = 60,
    ) -> pd.DataFrame:
        """Fetch daily money-flow data for a stock.

        Queries the ``stk_moneyflow`` table.

        Parameters
        ----------
        ts_code : str
            Tushare-style stock code, e.g. ``"000001.SZ"``.
        days : int
            Number of most-recent trading days to return (default 60).

        Returns
        -------
        pd.DataFrame
            Money-flow data sorted by ``trade_date`` descending.
        """
        sql = """
            SELECT *
            FROM stk_moneyflow
            WHERE ts_code = {ts_code:String}
            ORDER BY trade_date DESC
            LIMIT {days:UInt32}
        """
        return self.query(
            sql,
            params={
                "param_ts_code": ts_code,
                "param_days": str(days),
            },
        )

    # ------------------------------------------------------------------
    # Margin trading
    # ------------------------------------------------------------------

    def get_margin(
        self,
        ts_code: str,
        days: int = 60,
    ) -> pd.DataFrame:
        """Fetch daily margin-trading (融资融券) balances for a stock.

        Queries the ``stk_margin`` table.

        Parameters
        ----------
        ts_code : str
            Tushare-style stock code, e.g. ``"000001.SZ"``.
        days : int
            Number of most-recent trading days to return (default 60).

        Returns
        -------
        pd.DataFrame
            Margin data sorted by ``trade_date`` descending.
            Key columns: rzye (融资余额), rqye (融券余额),
            rzmre (融资买入额), rzrqye (融资融券余额).
        """
        sql = """
            SELECT *
            FROM stk_margin
            WHERE ts_code = {ts_code:String}
            ORDER BY trade_date DESC
            LIMIT {days:UInt32}
        """
        return self.query(
            sql,
            params={
                "param_ts_code": ts_code,
                "param_days": str(days),
            },
        )

    # ------------------------------------------------------------------
    # Dragon-tiger board (top list)
    # ------------------------------------------------------------------

    def get_top_list(
        self,
        trade_date: str,
        ts_code: str | None = None,
    ) -> pd.DataFrame:
        """Fetch dragon-tiger board (龙虎榜) data for a date.

        Queries the ``stk_top_list`` table.

        Parameters
        ----------
        trade_date : str
            Trade date, ``YYYY-MM-DD``.
        ts_code : str | None
            Optional Tushare-style stock code to filter by.
            When omitted, returns all stocks on the board that day.

        Returns
        -------
        pd.DataFrame
            Top-list data sorted by ``ts_code`` ascending.
        """
        if ts_code:
            sql = """
                SELECT *
                FROM stk_top_list
                WHERE trade_date = {trade_date:String}
                  AND ts_code = {ts_code:String}
                ORDER BY ts_code
            """
            return self.query(
                sql,
                params={
                    "param_trade_date": trade_date,
                    "param_ts_code": ts_code,
                },
            )

        sql = """
            SELECT *
            FROM stk_top_list
            WHERE trade_date = {trade_date:String}
            ORDER BY ts_code
        """
        return self.query(sql, params={"param_trade_date": trade_date})

    # ------------------------------------------------------------------
    # Northbound / Southbound flow
    # ------------------------------------------------------------------

    def get_moneyflow_hsgt(
        self,
        lookback_days: int = 30,
    ) -> pd.DataFrame:
        """Fetch Stock Connect (沪深港通) money-flow data.

        Queries the ``stk_moneyflow_hsgt`` table.

        Parameters
        ----------
        lookback_days : int
            Number of most-recent trading days to return (default 30).

        Returns
        -------
        pd.DataFrame
            HSGT flow data sorted by ``trade_date`` descending.
            Key columns: ggt_ss (港股通上海), ggt_sz (港股通深圳),
            hgt (沪股通), sgt (深股通), north_money, south_money.
        """
        sql = """
            SELECT *
            FROM stk_moneyflow_hsgt
            ORDER BY trade_date DESC
            LIMIT {days:UInt32}
        """
        return self.query(sql, params={"param_days": str(lookback_days)})

    # ------------------------------------------------------------------
    # Trade calendar
    # ------------------------------------------------------------------

    def get_trade_calendar(
        self,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Fetch trade calendar for a date range.

        Queries the ``trade_calendar`` table.

        Parameters
        ----------
        start_date : str
            Inclusive start date, ``YYYY-MM-DD``.
        end_date : str
            Inclusive end date, ``YYYY-MM-DD``.

        Returns
        -------
        pd.DataFrame
            Trading days sorted by ``cal_date`` ascending.
            Columns: exchange, cal_date, is_open, pretrade_date.
        """
        sql = """
            SELECT *
            FROM trade_calendar
            WHERE cal_date >= {start_date:String}
              AND cal_date <= {end_date:String}
            ORDER BY cal_date
        """
        return self.query(
            sql,
            params={
                "param_start_date": start_date,
                "param_end_date": end_date,
            },
        )

    # ------------------------------------------------------------------
    # Stock info
    # ------------------------------------------------------------------

    def get_stock_info(
        self,
        ts_code: str,
    ) -> pd.DataFrame:
        """Fetch basic profile information for a stock.

        Queries the ``stk_info`` table.

        Parameters
        ----------
        ts_code : str
            Tushare-style stock code, e.g. ``"000001.SZ"``.

        Returns
        -------
        pd.DataFrame
            Stock profile data.  Returns an empty DataFrame if the
            stock is not found.
            Columns: ts_code, symbol, name, area, industry, cnspell,
            market, list_date, act_name, act_ent_type.
        """
        sql = """
            SELECT *
            FROM stk_info
            WHERE ts_code = {ts_code:String}
        """
        return self.query(sql, params={"param_ts_code": ts_code})
