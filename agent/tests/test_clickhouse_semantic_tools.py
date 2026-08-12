"""Tool-level tests for the ClickHouse flexibility channel (Phase 2).

Covers ch_query (credentials gate, guard envelope, execution, serialization,
truncation, audit log), ch_list_tables and ch_describe_table. Everything runs
against a mocked ``clickhouse_connect`` client / ``ClickHouseConnector`` — no
live ClickHouse is contacted. The small live-CH class at the bottom is
skip-when-unreachable, mirroring the existing CH test conventions.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.clickhouse_connector import ClickHouseConnector
from src.config.accessor import reset_env_config
from src.tools.clickhouse_explore_tools import ChDescribeTableTool, ChListTablesTool
from src.tools.clickhouse_query_tool import ChQueryTool

_CH_REACHABLE: bool | None = None


def _ch_is_reachable() -> bool:
    global _CH_REACHABLE
    if _CH_REACHABLE is not None:
        return _CH_REACHABLE
    try:
        _CH_REACHABLE = ClickHouseConnector().health_check()
    except Exception:
        _CH_REACHABLE = False
    return _CH_REACHABLE


class _FakeQueryResult:
    def __init__(self, column_names: tuple, result_rows: list):
        self.column_names = column_names
        self.result_rows = result_rows


class _FakeClient:
    """Stands in for a clickhouse_connect client.

    Serves the whitelist query from ``whitelist`` and every other query from
    ``result``; records executed SQL for assertions.
    """

    def __init__(
        self,
        result: _FakeQueryResult | None = None,
        whitelist: tuple[str, ...] = ("stk_factor_pro", "fin_indicator"),
        whitelist_error: Exception | None = None,
        query_error: Exception | None = None,
    ):
        self.result = result or _FakeQueryResult((), [])
        self.whitelist = whitelist
        self.whitelist_error = whitelist_error
        self.query_error = query_error
        self.executed: list[str] = []
        self.closed = False

    def query(self, sql: str, parameters: dict | None = None):
        if "system.tables" in sql:
            if self.whitelist_error is not None:
                raise self.whitelist_error
            return _FakeQueryResult(("name",), [(name,) for name in self.whitelist])
        self.executed.append(sql)
        if self.query_error is not None:
            raise self.query_error
        return self.result

    def close(self):
        self.closed = True


@pytest.fixture()
def llm_creds(monkeypatch):
    """Configure llm_role credentials through the env accessor layer."""
    monkeypatch.setenv("CLICKHOUSE_LLM_USER", "llm_role")
    monkeypatch.setenv("CLICKHOUSE_LLM_PASSWORD", "test-password")
    reset_env_config()
    yield
    reset_env_config()


@pytest.fixture()
def audit_home(monkeypatch, tmp_path):
    """Point the audit log at a temp runtime root."""
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path))
    return tmp_path


def _run_query(sql: str) -> dict[str, Any]:
    return json.loads(ChQueryTool().execute(sql=sql))


# ---------------------------------------------------------------------------
# (h) missing llm_role credentials -> actionable error, never default user
# ---------------------------------------------------------------------------


class TestCredentialGate:
    def test_missing_credentials_fail_with_actionable_error(self, monkeypatch):
        monkeypatch.delenv("CLICKHOUSE_LLM_USER", raising=False)
        monkeypatch.delenv("CLICKHOUSE_LLM_PASSWORD", raising=False)
        reset_env_config()
        with patch("clickhouse_connect.get_client") as get_client:
            envelope = _run_query("SELECT 1")
        assert envelope["ok"] is False
        assert "CLICKHOUSE_LLM_USER" in envelope["error"]
        assert "CLICKHOUSE_LLM_PASSWORD" in envelope["error"]
        assert envelope["guard"] == "missing_llm_role_credentials"
        get_client.assert_not_called()  # never falls back to the default user

    def test_partial_credentials_fail(self, monkeypatch):
        monkeypatch.setenv("CLICKHOUSE_LLM_USER", "llm_role")
        monkeypatch.delenv("CLICKHOUSE_LLM_PASSWORD", raising=False)
        reset_env_config()
        with patch("clickhouse_connect.get_client") as get_client:
            envelope = _run_query("SELECT 1")
        assert envelope["ok"] is False
        get_client.assert_not_called()


# ---------------------------------------------------------------------------
# guard rejections surface the guard reason
# ---------------------------------------------------------------------------


class TestGuardRejections:
    def test_write_statement_rejected_with_guard_reason(self, llm_creds):
        client = _FakeClient()
        with patch("clickhouse_connect.get_client", return_value=client):
            envelope = _run_query("DROP TABLE stk_factor_pro")
        assert envelope["ok"] is False
        assert envelope["guard"] == "non_select_root"
        assert client.executed == []
        assert client.closed

    def test_unknown_table_rejected(self, llm_creds):
        client = _FakeClient()
        with patch("clickhouse_connect.get_client", return_value=client):
            envelope = _run_query("SELECT * FROM not_in_ashare")
        assert envelope["ok"] is False
        assert envelope["guard"] == "unknown_table"

    def test_whitelist_falls_back_to_snapshots(self, llm_creds):
        client = _FakeClient(
            result=_FakeQueryResult(("n",), [(1,)]),
            whitelist_error=RuntimeError("system.tables denied for llm_role"),
        )
        with patch("clickhouse_connect.get_client", return_value=client):
            envelope = _run_query("SELECT count() AS n FROM stk_factor_pro")
        assert envelope["ok"] is True
        assert envelope["rows"] == [[1]]


# ---------------------------------------------------------------------------
# successful execution
# ---------------------------------------------------------------------------


class TestQueryExecution:
    def test_success_envelope_and_serialization(self, llm_creds):
        result = _FakeQueryResult(
            ("ts_code", "trade_date", "close", "big"),
            [
                ("000001.SZ", dt.date(2024, 1, 2), Decimal("9.05"), 2**64 - 1),
                ("600519.SH", dt.date(2024, 1, 2), None, 0),
            ],
        )
        client = _FakeClient(result=result)
        with patch("clickhouse_connect.get_client", return_value=client):
            envelope = _run_query(
                "SELECT ts_code, trade_date, close, big FROM stk_factor_pro"
            )
        assert envelope["ok"] is True
        assert envelope["columns"] == ["ts_code", "trade_date", "close", "big"]
        assert envelope["rows"][0] == ["000001.SZ", "2024-01-02", 9.05, 2**64 - 1]
        assert envelope["rows"][1][2] is None
        assert envelope["row_count"] == 2
        assert envelope["truncated"] is False
        assert envelope["limit_applied"] == 500
        assert envelope["elapsed_ms"] >= 0
        assert "LIMIT 500" in client.executed[0]
        assert client.closed

    def test_limit_clamped_before_execution(self, llm_creds):
        client = _FakeClient(result=_FakeQueryResult(("n",), [(1,)]))
        with patch("clickhouse_connect.get_client", return_value=client):
            envelope = _run_query("SELECT count() AS n FROM stk_factor_pro LIMIT 5000")
        assert envelope["limit_applied"] == 500
        assert client.executed[0].endswith("LIMIT 500")

    def test_connection_failure_envelope(self, llm_creds):
        with patch("clickhouse_connect.get_client", side_effect=OSError("unreachable")):
            envelope = _run_query("SELECT 1")
        assert envelope["ok"] is False
        assert "connection failed" in envelope["error"].lower()

    def test_query_runtime_error_envelope(self, llm_creds):
        client = _FakeClient(query_error=RuntimeError("TIMEOUT_EXCEEDED"))
        with patch("clickhouse_connect.get_client", return_value=client):
            envelope = _run_query("SELECT * FROM stk_factor_pro")
        assert envelope["ok"] is False
        assert "TIMEOUT_EXCEEDED" in envelope["error"]

    def test_client_receives_llm_role_credentials(self, llm_creds):
        client = _FakeClient(result=_FakeQueryResult(("n",), [(1,)]))
        with patch("clickhouse_connect.get_client", return_value=client) as get_client:
            _run_query("SELECT count() AS n FROM stk_factor_pro")
        kwargs = get_client.call_args.kwargs
        assert kwargs["username"] == "llm_role"
        assert kwargs["password"] == "test-password"
        assert kwargs["settings"] == {"max_execution_time": 30}

    def test_truncation_declared_when_over_cap(self, llm_creds):
        rows = [(i, "payload" * 40) for i in range(400)]
        client = _FakeClient(result=_FakeQueryResult(("i", "p"), rows))
        with patch("clickhouse_connect.get_client", return_value=client):
            envelope = _run_query("SELECT i, p FROM stk_factor_pro")
        assert envelope["truncated"] is True
        assert envelope["row_count"] < 400
        assert "truncation_note" in envelope
        serialized = json.dumps(envelope["rows"], ensure_ascii=False).encode("utf-8")
        assert len(serialized) <= 50 * 1024


# ---------------------------------------------------------------------------
# audit log
# ---------------------------------------------------------------------------


class TestAuditLog:
    def test_audit_record_written(self, llm_creds, audit_home):
        client = _FakeClient(result=_FakeQueryResult(("n",), [(1,)]))
        with patch("clickhouse_connect.get_client", return_value=client):
            _run_query("SELECT count() AS n FROM stk_factor_pro")
        path = audit_home / "logs" / "ch_query_audit.jsonl"
        assert path.is_file()
        record = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert record["rows_returned"] == 1
        assert record["truncated"] is False
        assert record["error"] is None
        assert "SELECT" in record["sql"]
        assert record["elapsed_ms"] >= 0
        assert record["timestamp"]

    def test_guard_rejection_audited(self, llm_creds, audit_home):
        client = _FakeClient()
        with patch("clickhouse_connect.get_client", return_value=client):
            _run_query("DROP TABLE stk_factor_pro")
        path = audit_home / "logs" / "ch_query_audit.jsonl"
        record = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert record["error"] == "guard:non_select_root"
        assert record["rows_returned"] == 0

    def test_audit_failure_never_breaks_the_query(
        self, llm_creds, monkeypatch, tmp_path
    ):
        # Point the runtime root at a plain FILE so the real audit writer's
        # mkdir/open fails — the query must still succeed (audit is best-effort).
        home_file = tmp_path / "not_a_dir"
        home_file.write_text("blocker", encoding="utf-8")
        monkeypatch.setenv("VIBE_TRADING_HOME", str(home_file))
        client = _FakeClient(result=_FakeQueryResult(("n",), [(1,)]))
        with patch("clickhouse_connect.get_client", return_value=client):
            envelope = _run_query("SELECT count() AS n FROM stk_factor_pro")
        assert envelope["ok"] is True
        assert envelope["rows"] == [[1]]


# ---------------------------------------------------------------------------
# ch_list_tables / ch_describe_table (mocked connector)
# ---------------------------------------------------------------------------


def _mock_connector(frames: list[pd.DataFrame], database: str = "ashare"):
    instance = MagicMock()
    instance.database = database
    instance.query.side_effect = frames
    return patch(
        "src.tools.clickhouse_explore_tools.ClickHouseConnector",
        return_value=instance,
    )


class TestListTables:
    def test_envelope_shape_and_comment_degradation(self):
        frame = pd.DataFrame(
            {
                "name": ["fin_indicator", "stk_factor_pro"],
                "comment": ["财务指标", None],
                "total_rows": [1234, None],
            }
        )
        with _mock_connector([frame]):
            envelope = json.loads(ChListTablesTool().execute())
        assert envelope["ok"] is True
        assert envelope["database"] == "ashare"
        assert envelope["count"] == 2
        assert envelope["tables"][0] == {
            "table": "fin_indicator",
            "comment": "财务指标",
            "row_estimate": 1234,
        }
        assert envelope["tables"][1]["comment"] == ""
        assert envelope["tables"][1]["row_estimate"] is None

    def test_unreachable_warehouse_error_envelope(self):
        with patch(
            "src.tools.clickhouse_explore_tools.ClickHouseConnector",
            side_effect=OSError("CH unreachable"),
        ):
            envelope = json.loads(ChListTablesTool().execute())
        assert envelope["ok"] is False
        assert "failed" in envelope["error"]


class TestDescribeTable:
    def test_success_envelope(self):
        meta = pd.DataFrame(
            {
                "name": ["stk_factor_pro"],
                "comment": ["日频因子"],
                "engine": ["MergeTree"],
                "partition_key": ["toYYYYMM(trade_date)"],
                "sorting_key": ["ts_code, trade_date"],
                "total_rows": [999],
            }
        )
        columns = pd.DataFrame(
            {
                "name": ["ts_code", "trade_date", "close"],
                "type": ["String", "Date", "Float64"],
                "comment": ["股票代码", "交易日期", ""],
            }
        )
        samples = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [dt.date(2024, 1, 2)],
                "close": [9.05],
            }
        )
        with _mock_connector([meta, columns, samples]):
            envelope = json.loads(ChDescribeTableTool().execute(table="stk_factor_pro"))
        assert envelope["ok"] is True
        assert envelope["engine"] == "MergeTree"
        assert envelope["partition_key"] == "toYYYYMM(trade_date)"
        assert envelope["columns"][2] == {
            "name": "close",
            "type": "Float64",
            "comment": "",
        }
        assert envelope["sample_rows"]["rows"] == [["000001.SZ", "2024-01-02", 9.05]]

    def test_invalid_table_name_rejected_before_any_query(self):
        with patch(
            "src.tools.clickhouse_explore_tools.ClickHouseConnector"
        ) as connector_cls:
            for bad in ("stk; DROP TABLE x", "a b", "ashare.tbl", "", None):
                envelope = json.loads(ChDescribeTableTool().execute(table=bad))
                assert envelope["ok"] is False
                assert "invalid table name" in envelope["error"]
            connector_cls.assert_not_called()

    def test_unknown_table_error(self):
        with _mock_connector([pd.DataFrame()]):
            envelope = json.loads(ChDescribeTableTool().execute(table="ghost_tbl"))
        assert envelope["ok"] is False
        assert "unknown table" in envelope["error"]
        assert "ch_list_tables" in envelope["error"]


# ---------------------------------------------------------------------------
# live-CH smoke (skip when unreachable; never required in CI)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _ch_is_reachable(), reason="ClickHouse is unreachable")
class TestLiveChannel:
    def test_live_list_tables(self):
        envelope = json.loads(ChListTablesTool().execute())
        assert envelope["ok"] is True
        assert envelope["count"] >= 50

    def test_live_describe_table(self):
        envelope = json.loads(ChDescribeTableTool().execute(table="stk_factor_pro"))
        assert envelope["ok"] is True
        assert envelope["columns"]
