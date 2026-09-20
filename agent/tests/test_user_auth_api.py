"""Opt-in user-auth layer — HTTP surface: D7 admin gates + §2.5 contract.

Covers the API-level half of the Phase-4 checklist of
``.omo/plans/vibe-trading-user-auth.md``: flag-aware admin gates in BOTH
flag states (D7/B2), the NB3 write-gate discrimination (mutations locked,
status/list reads kept, the read-only-POST verify exemption), the frozen
§2.5 endpoint contract (register/login/logout/me/change-password),
username-enumeration protection (D17), the sanitized runtime endpoint (D8),
and the inert-endpoints guarantee when the flag is off. The store-level half
lives in ``test_user_auth.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api_server
from src.api import password_hashing
from src.api.user_store import UserStore, get_user_store, reset_user_store
from src.config.accessor import reset_env_config

PASSWORD = "correct-horse-99"
SHARED_KEY = "test-shared-key"


@pytest.fixture()
def auth_env(monkeypatch, tmp_path):
    """D5 target quadrant: flag=1 + shared key set + isolated users.db."""
    db_path = tmp_path / "singleton-users.db"
    monkeypatch.setenv("VIBE_TRADING_USER_AUTH", "1")
    monkeypatch.setenv("API_AUTH_KEY", SHARED_KEY)
    monkeypatch.setenv("VIBE_TRADING_USERS_DB_PATH", str(db_path))
    monkeypatch.setattr(api_server, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(
        api_server, "LEGACY_ENV_PATH", tmp_path / "legacy.env", raising=False
    )
    monkeypatch.setattr(api_server, "ENV_EXAMPLE_PATH", tmp_path / "example.env")
    reset_env_config()
    reset_user_store()
    yield db_path
    reset_user_store()


def _make_user(
    store: UserStore,
    username: str = "alice",
    role: str = "user",
    password: str = PASSWORD,
):
    return store.create_user(
        username,
        password_hashing.hash_password(password),
        role=role,
        display_name=username.capitalize(),
    )


def _session_for(store: UserStore, username: str = "alice", role: str = "user") -> str:
    return store.create_session(_make_user(store, username, role).id)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# D7 admin gates over HTTP (both flag states)
# ---------------------------------------------------------------------------


def test_settings_admin_gated_flag_on(auth_env) -> None:
    store = get_user_store()
    admin_token = _session_for(store, "root", role="admin")
    user_token = _session_for(store, "alice", role="user")
    client = TestClient(api_server.app)

    assert client.get("/settings/llm", headers=_bearer(admin_token)).status_code == 200
    assert (
        client.get("/settings/llm", headers=_bearer(SHARED_KEY)).status_code == 200
    )  # break-glass
    assert client.get("/settings/llm", headers=_bearer(user_token)).status_code == 403

    put = {"provider": "openai", "model_name": "gpt-4o"}
    assert (
        client.put("/settings/llm", json=put, headers=_bearer(user_token)).status_code
        == 403
    )
    assert (
        client.put("/settings/llm", json=put, headers=_bearer(admin_token)).status_code
        == 200
    )


def test_settings_gate_flag_off_delegates_unchanged(monkeypatch, tmp_path) -> None:
    """D7/B2: flag=0 must not break loopback dev UX (or the desktop shell)."""
    monkeypatch.delenv("VIBE_TRADING_USER_AUTH", raising=False)
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    monkeypatch.setattr(api_server, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(api_server, "ENV_EXAMPLE_PATH", tmp_path / "example.env")
    reset_env_config()
    client = TestClient(api_server.app)  # loopback peer, no key ⇒ dev-mode trust
    assert client.get("/settings/llm").status_code == 200


def test_write_gate_locks_mutations_but_keeps_reads(auth_env) -> None:
    """NB3: admin for writes; plain auth for status/list reads + verify exemption."""
    user_token = _session_for(get_user_store())
    client = TestClient(api_server.app)
    h = _bearer(user_token)

    assert client.post("/channels/start", headers=h).status_code == 403
    assert client.post("/live/halt", json={}, headers=h).status_code == 403
    assert client.get("/channels/status", headers=h).status_code != 403
    assert client.get("/live/status", headers=h).status_code != 403
    assert client.get("/scheduled-runs", headers=h).status_code != 403
    # POST but contractually read-only ⇒ exempt from the admin gate (404 = route ran)
    assert client.post("/live/connectors/nope/verify", headers=h).status_code == 404


# ---------------------------------------------------------------------------
# §2.5 endpoint contract
# ---------------------------------------------------------------------------


def test_full_session_lifecycle_over_http(auth_env) -> None:
    store = get_user_store()
    invite = store.create_invite(max_uses=1)
    client = TestClient(api_server.app)

    reg = client.post(
        "/auth/register",
        json={"username": "Alice", "password": PASSWORD, "invite_code": invite},
        headers={"Origin": "http://testserver"},  # browser-style same-origin (§0.3)
    )
    assert reg.status_code == 201
    body = reg.json()
    assert (
        body["username"] == "alice"
        and body["role"] == "user"
        and body["display_name"] == "Alice"
    )
    token1 = body["token"]

    me = client.get("/auth/me", headers=_bearer(token1))
    assert me.status_code == 200
    assert me.json() == {"username": "alice", "role": "user", "display_name": "Alice"}

    login = client.post("/auth/login", json={"username": "alice", "password": PASSWORD})
    assert login.status_code == 200
    token2 = login.json()["token"]
    assert token2 != token1  # §4.7: always a fresh token

    assert (
        client.post(
            "/auth/change-password",
            json={"old_password": PASSWORD, "new_password": "new-password-1"},
            headers=_bearer(token2),
        ).status_code
        == 204
    )
    assert (
        client.get("/auth/me", headers=_bearer(token1)).status_code == 401
    )  # others revoked
    assert (
        client.get("/auth/me", headers=_bearer(token2)).status_code == 200
    )  # current kept
    assert (
        client.post(
            "/auth/login", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/login", json={"username": "alice", "password": "new-password-1"}
        ).status_code
        == 200
    )

    assert client.post("/auth/logout", headers=_bearer(token2)).status_code == 204
    assert client.get("/auth/me", headers=_bearer(token2)).status_code == 401

    ticket = client.post("/auth/sse-ticket", headers=_bearer(login.json()["token"]))
    assert ticket.status_code == 401  # that token was revoked by logout above


def test_sse_ticket_mints_for_session_token(auth_env) -> None:
    token = _session_for(get_user_store())
    resp = TestClient(api_server.app).post("/auth/sse-ticket", headers=_bearer(token))
    assert resp.status_code == 200 and resp.json()["ticket"]


def test_wrong_old_password_is_400_and_session_survives(auth_env) -> None:
    """§2.5 invariant: 401 ⇔ session invalid, universally — a wrong body value
    must never destroy a valid session (frontend expireSession() fires on 401
    only), so a wrong old password is a 400 validation failure."""
    token = _session_for(get_user_store())
    client = TestClient(api_server.app)

    resp = client.post(
        "/auth/change-password",
        json={"old_password": "not-the-password", "new_password": "new-password-1"},
        headers=_bearer(token),
    )
    assert resp.status_code == 400
    assert resp.json() == {"detail": "Invalid old password"}

    # The regression that matters: the caller's session is STILL alive.
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 200

    resp = client.post(
        "/auth/change-password",
        json={"old_password": PASSWORD, "new_password": "short"},
        headers=_bearer(token),
    )
    assert resp.status_code == 400  # non-compliant new password stays 400

    resp = client.post(
        "/auth/change-password",
        json={"old_password": PASSWORD, "new_password": "new-password-1"},
        headers=_bearer("garbage-token"),
    )
    assert resp.status_code == 401  # rejected session ⇒ 401 from require_auth


def test_register_validation_and_enumeration_protection(auth_env) -> None:
    store = get_user_store()
    invite = store.create_invite(max_uses=10)
    client = TestClient(api_server.app)

    def register(username: str, password: str, code: str):
        return client.post(
            "/auth/register",
            json={"username": username, "password": password, "invite_code": code},
        )

    assert register("ab", PASSWORD, invite).status_code == 400  # too short
    assert register("bad chars!", PASSWORD, invite).status_code == 400
    assert register("valid-01", "short", invite).status_code == 400  # <8 chars
    assert (
        register("valid-01", "x" * 129, invite).status_code == 400
    )  # scrypt input bound
    assert register("valid-01", PASSWORD, "wrong-code").status_code == 400
    assert register("valid-01", PASSWORD, invite).status_code == 201
    assert register("valid-01", PASSWORD, invite).status_code == 409  # duplicate

    ghost = client.post(
        "/auth/login", json={"username": "ghost", "password": "whatever-1"}
    )
    wrong = client.post(
        "/auth/login", json={"username": "valid-01", "password": "whatever-1"}
    )
    assert ghost.status_code == wrong.status_code == 401
    assert ghost.json() == wrong.json() == {"detail": "Invalid username or password"}

    store.set_active("valid-01", False)
    inactive = client.post(
        "/auth/login", json={"username": "valid-01", "password": PASSWORD}
    )
    assert inactive.status_code == 401
    assert inactive.json()["detail"] == "Invalid username or password"


def test_self_register_switch(auth_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOW_SELF_REGISTER", "0")
    reset_env_config()
    invite = get_user_store().create_invite()
    resp = TestClient(api_server.app).post(
        "/auth/register",
        json={"username": "valid-02", "password": PASSWORD, "invite_code": invite},
    )
    assert resp.status_code == 403


def test_credential_endpoints_inert_flag_off(monkeypatch) -> None:
    monkeypatch.delenv("VIBE_TRADING_USER_AUTH", raising=False)
    reset_env_config()
    client = TestClient(api_server.app)
    resp = client.post("/auth/login", json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 403
    assert resp.json() == {"detail": "User authentication is disabled"}


def test_settings_runtime_is_sanitized(auth_env) -> None:
    token = _session_for(get_user_store())  # plain user, NOT admin
    client = TestClient(api_server.app)
    resp = client.get("/settings/runtime", headers=_bearer(token))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "provider",
        "model_name",
        "sse_timeout_seconds",
    }  # D8: nothing else
    assert isinstance(body["sse_timeout_seconds"], int)
    assert client.get("/settings/runtime").status_code == 401
