"""``GET /auth/mode`` — the frontend capability gate (plan D19).

The SPA cannot probe capability by status code: the catch-all in
``src/api/spa.py`` turns ANY unmatched GET into ``200 + index.html``, and a
probe of ``POST /auth/login`` would get an equally ambiguous 405. So this
endpoint must be (a) registered in BOTH flag modes, (b) unauthenticated,
(c) always JSON — and the last test proves it wins over a mounted SPA
catch-all instead of being swallowed into ``index.html``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api_server
from src.api.user_auth_routes import register_auth_mode_routes
from src.config.accessor import reset_env_config


def test_auth_mode_flag_off_returns_json_false() -> None:
    client = TestClient(api_server.app)
    resp = client.get("/auth/mode")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"user_auth": False}


def test_auth_mode_flag_on_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBE_TRADING_USER_AUTH", "1")
    reset_env_config()
    resp = TestClient(api_server.app).get("/auth/mode")
    assert resp.status_code == 200
    assert resp.json() == {"user_auth": True}


def test_auth_mode_needs_no_credentials_even_with_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_AUTH_KEY", "some-shared-key")
    monkeypatch.setattr(api_server, "_API_KEY", "some-shared-key")
    reset_env_config()
    resp = TestClient(api_server.app).get("/auth/mode")
    assert resp.status_code == 200
    assert resp.json() == {"user_auth": False}


def test_auth_mode_is_not_swallowed_by_spa_catch_all(tmp_path) -> None:
    """The registered route must beat the SPA 404→index.html fallback (D19)."""
    from src.api.spa import SPAStaticFiles

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<html><body>spa-shell</body></html>", encoding="utf-8"
    )

    app = FastAPI()
    register_auth_mode_routes(app)
    app.mount("/", SPAStaticFiles(directory=str(dist), html=True), name="frontend")

    client = TestClient(app)

    mode = client.get("/auth/mode")
    assert mode.status_code == 200
    assert mode.headers["content-type"].startswith("application/json")
    assert mode.json() == {"user_auth": False}

    swallowed = client.get("/some/unknown/deep/link")
    assert swallowed.status_code == 200
    assert "spa-shell" in swallowed.text  # the catch-all IS active in this app
