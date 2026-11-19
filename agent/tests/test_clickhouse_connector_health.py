"""Health-check authentication regressions for the ClickHouse connector.

``health_check()`` used to send ``SELECT 1`` with no credentials while
``query()`` authenticated.  A password-protected server answers the anonymous
probe with HTTP 403, so ``is_available()`` reported a healthy instance as down
and the loader registry skipped the head of the A-share fallback chain.  These
tests assert the probe authenticates exactly like ``query()``.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from src.clickhouse_connector import ClickHouseConnector


class _FakeResponse:
    """Minimal ``requests.Response`` stand-in."""

    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    @property
    def text(self) -> str:
        return "" if self.ok else "Forbidden"

    def json(self) -> dict[str, Any]:
        return self._payload


class _AuthGate:
    """ClickHouse HTTP surface that 403s unless reader/secret is presented."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def _respond(self, method: str, params: dict[str, str] | None) -> _FakeResponse:
        params = dict(params or {})
        self.calls.append((method, params))
        if params.get("user") != "reader" or params.get("password") != "secret":
            return _FakeResponse(403)
        return _FakeResponse(
            200,
            {"meta": [{"name": "1", "type": "UInt8"}], "data": [[1]], "rows": 1},
        )

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._respond("GET", kwargs.get("params"))

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._respond("POST", kwargs.get("params"))


def _connector(*, password: str = "secret") -> ClickHouseConnector:
    return ClickHouseConnector(
        host="ch.internal",
        port=8123,
        user="reader",
        password=password,
        database="ashare",
    )


def test_health_check_authenticates_like_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe carries the same credentials as ``query()`` (regression)."""
    gate = _AuthGate()
    monkeypatch.setattr(requests, "get", gate.get)
    monkeypatch.setattr(requests, "post", gate.post)

    connector = _connector()
    assert connector.health_check() is True
    assert len(connector.query("SELECT 1")) == 1

    get_method, get_params = gate.calls[0]
    post_method, post_params = gate.calls[1]
    assert get_method == "GET" and post_method == "POST"
    assert get_params["query"] == "SELECT 1"
    assert get_params["user"] == post_params["user"] == "reader"
    assert get_params["password"] == post_params["password"] == "secret"


def test_health_check_reports_false_when_identity_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejected credentials mean unavailable — not a transport failure."""
    gate = _AuthGate()
    monkeypatch.setattr(requests, "get", gate.get)

    assert _connector(password="wrong").health_check() is False


def test_health_check_never_raises_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args: Any, **kwargs: Any) -> _FakeResponse:
        raise requests.ConnectionError("server down")

    monkeypatch.setattr(requests, "get", _boom)

    assert _connector().health_check() is False


def test_loader_is_available_on_a_password_protected_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported symptom: an authenticated instance must stay in the chain."""
    from backtest.loaders.clickhouse import DataLoader

    gate = _AuthGate()
    monkeypatch.setattr(requests, "get", gate.get)
    monkeypatch.setattr(
        DataLoader, "_build_connector", staticmethod(lambda: _connector())
    )

    assert DataLoader().is_available() is True


def test_anonymous_probe_would_be_rejected_by_the_same_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard the fixture itself: an unauthenticated probe still 403s."""
    gate = _AuthGate()
    monkeypatch.setattr(requests, "get", gate.get)

    assert (
        gate.get("http://ch.internal:8123/", params={"query": "SELECT 1"}).ok is False
    )
