"""Opt-in user-auth layer — unit level: hashing, DAO, invariant, precedence.

Covers the store-level half of the Phase-4 checklist of
``.omo/plans/vibe-trading-user-auth.md``: scrypt roundtrip (D2), session
lifecycle/expiry/throttled sliding renewal (D1.1), JOIN-users making
``deactivate`` immediate (D1.1/B5), invite single-use + concurrency +
rollback (D9), the D5 startup invariant, ``Principal.role`` request-scoping
(D7.1), and the D5 precedence matrix (session > shared key > loopback).
The HTTP-surface half (admin gates, §2.5 endpoint contract) lives in
``test_user_auth_api.py``; the capability endpoint in
``test_auth_mode_endpoint.py``.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import api_server
from src.api import password_hashing
from src.api.admin_auth import enforce_user_auth_startup_invariant
from src.api.user_store import UserStore, get_user_store, reset_user_store
from src.api.user_store_schema import (
    InviteInvalidError,
    UsernameExistsError,
    UserNotFoundError,
)
from src.config.accessor import reset_env_config
from src.session.models import AuthMethod, Principal

PASSWORD = "correct-horse-99"
SHARED_KEY = "test-shared-key"


@pytest.fixture()
def store(tmp_path):
    """A standalone UserStore on a tmp database (DAO-level tests)."""
    s = UserStore(tmp_path / "users.db")
    yield s
    s.close()


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
# A. password hashing (D2)
# ---------------------------------------------------------------------------


def test_scrypt_roundtrip_format_and_unique_salts() -> None:
    encoded = password_hashing.hash_password(PASSWORD)
    assert encoded.startswith("scrypt$16384$8$1$")
    assert len(encoded.split("$")) == 6
    assert password_hashing.verify_password(PASSWORD, encoded)
    assert not password_hashing.verify_password(PASSWORD + "x", encoded)
    assert password_hashing.hash_password(PASSWORD) != encoded  # fresh salt
    assert password_hashing.verify_password(
        "pässwörd-ünicode-1", password_hashing.hash_password("pässwörd-ünicode-1")
    )


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "garbage",
        "pbkdf2$1$2$3$YQ==$YQ==",
        "scrypt$x$8$1$!!!$YQ==",
        "scrypt$16384$8$1$YQ==",
    ],
)
def test_verify_rejects_malformed_hashes_without_raising(malformed: str) -> None:
    assert password_hashing.verify_password(PASSWORD, malformed) is False


# ---------------------------------------------------------------------------
# B. store DAO: users, sessions, invites (D3/D4/D9/D1.1)
# ---------------------------------------------------------------------------


def test_username_normalization_and_duplicate_rejection(store: UserStore) -> None:
    user = _make_user(store, "Alice")
    assert user.username == "alice"
    assert user.display_name == "Alice"
    assert store.get_user("  ALICE ").id == user.id
    with pytest.raises(UsernameExistsError):
        _make_user(store, "alice")


def test_session_lifecycle(store: UserStore) -> None:
    user = _make_user(store)
    token = store.create_session(user.id, user_agent="pytest")
    validated = store.validate_session_token(token)
    assert validated is not None
    assert (validated.username, validated.role, validated.display_name) == (
        "alice",
        "user",
        "Alice",
    )
    store.revoke_session(token)
    assert store.validate_session_token(token) is None
    assert store.validate_session_token(token + "tamper") is None
    assert store.validate_session_token("") is None


def test_expired_session_rejected(store: UserStore) -> None:
    token = store.create_session(_make_user(store).id)
    past = "2000-01-01T00:00:00+00:00"
    store._conn.execute("UPDATE sessions SET expires_at = ?", (past,))
    store._conn.commit()
    assert store.validate_session_token(token) is None


def test_sliding_renewal_throttled_to_60s(store: UserStore) -> None:
    token = store.create_session(_make_user(store).id)

    assert store.validate_session_token(token) is not None  # first validate renews
    first_seen = store._conn.execute("SELECT last_seen_at FROM sessions").fetchone()[0]
    first_expiry = store._conn.execute("SELECT expires_at FROM sessions").fetchone()[0]
    assert first_seen is not None

    assert store.validate_session_token(token) is not None  # inside 60s ⇒ no write
    row = store._conn.execute(
        "SELECT last_seen_at, expires_at FROM sessions"
    ).fetchone()
    assert row[0] == first_seen and row[1] == first_expiry

    stale = "2020-01-01T00:00:00+00:00"  # >60s ago ⇒ renewal writes again
    store._conn.execute("UPDATE sessions SET last_seen_at = ?", (stale,))
    store._conn.commit()
    assert store.validate_session_token(token) is not None
    row = store._conn.execute(
        "SELECT last_seen_at, expires_at FROM sessions"
    ).fetchone()
    assert row[0] > stale and row[1] > first_expiry


def test_deactivate_invalidates_sessions_immediately(store: UserStore) -> None:
    """D1.1/B5: the JOIN on u.is_active=1 — CASCADE alone would NOT do this."""
    token = store.create_session(_make_user(store).id)
    assert store.validate_session_token(token) is not None
    store.set_active("alice", False)
    assert store.validate_session_token(token) is None  # row still exists!
    store.set_active("alice", True)
    assert store.validate_session_token(token) is not None
    with pytest.raises(UserNotFoundError):
        store.set_active("ghost", False)


def test_invite_single_use_with_atomic_rollback(store: UserStore) -> None:
    code = store.create_invite(max_uses=1)
    user = store.register_with_invite(
        "bob", password_hashing.hash_password(PASSWORD), "Bob", code
    )
    assert user.username == "bob"
    with pytest.raises(InviteInvalidError):
        store.register_with_invite(
            "carol", password_hashing.hash_password(PASSWORD), "Carol", code
        )
    with pytest.raises(InviteInvalidError):
        store.register_with_invite(
            "dave", password_hashing.hash_password(PASSWORD), "Dave", "not-a-code"
        )

    code2 = store.create_invite(max_uses=1)
    with pytest.raises(UsernameExistsError):
        store.register_with_invite(
            "bob", password_hashing.hash_password(PASSWORD), "Bob2", code2
        )
    # The failed registration rolled the consumption back — code2 is still usable.
    assert (
        store.register_with_invite(
            "erin", password_hashing.hash_password(PASSWORD), "Erin", code2
        ).username
        == "erin"
    )


def test_invite_concurrent_single_use_race(store: UserStore) -> None:
    code = store.create_invite(max_uses=1)
    results: list[object] = []
    barrier = threading.Barrier(4)

    def register(name: str) -> None:
        barrier.wait()
        try:
            store.register_with_invite(
                name, password_hashing.hash_password(PASSWORD), name, code
            )
            results.append("ok")
        except InviteInvalidError:
            results.append("rejected")

    threads = [threading.Thread(target=register, args=(f"user{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1
    assert results.count("rejected") == 3


def test_invite_expiry(store: UserStore) -> None:
    expired = store.create_invite(expires_at="2000-01-01T00:00:00+00:00")
    with pytest.raises(InviteInvalidError):
        store.register_with_invite(
            "frank", password_hashing.hash_password(PASSWORD), None, expired
        )


def test_revoke_scopes(store: UserStore) -> None:
    user = _make_user(store)
    keep = store.create_session(user.id)
    other = store.create_session(user.id)
    assert store.revoke_other_sessions(user.id, keep_token=keep) == 1
    assert store.validate_session_token(other) is None
    assert store.validate_session_token(keep) is not None
    bob = store.create_session(_make_user(store, "bob").id)
    assert store.revoke_sessions("alice") == 1
    assert store.validate_session_token(bob) is not None
    assert store.revoke_sessions() == 1  # --all


def test_session_ttl_days_env_is_honored(
    auth_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_SESSION_TTL_DAYS", "1")
    reset_env_config()
    store = get_user_store()
    token = store.create_session(_make_user(store, "ttluser").id)
    row = store._conn.execute(
        "SELECT created_at, expires_at FROM sessions WHERE token_sha256 = ?",
        (hashlib.sha256(token.encode("utf-8")).hexdigest(),),
    ).fetchone()
    delta = datetime.fromisoformat(row[1]) - datetime.fromisoformat(row[0])
    assert timedelta(hours=23) < delta <= timedelta(days=1)


# ---------------------------------------------------------------------------
# C. D5 startup invariant + Principal.role (D7.1)
# ---------------------------------------------------------------------------


def test_startup_invariant_refuses_flag_on_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.delenv("VIBE_TRADING_API_KEY", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    monkeypatch.setenv("VIBE_TRADING_USER_AUTH", "1")
    reset_env_config()
    with pytest.raises(RuntimeError, match="API_AUTH_KEY"):
        enforce_user_auth_startup_invariant()

    monkeypatch.setenv("API_AUTH_KEY", SHARED_KEY)
    reset_env_config()
    enforce_user_auth_startup_invariant()  # flag=1 + key ⇒ starts

    monkeypatch.delenv("API_AUTH_KEY")
    monkeypatch.setenv("VIBE_TRADING_USER_AUTH", "0")
    reset_env_config()
    enforce_user_auth_startup_invariant()  # flag=0 + no key ⇒ unchanged dev mode


def test_principal_role_is_request_scoped_not_serialized() -> None:
    p = Principal(
        subject="alice",
        auth_method=AuthMethod.USER_SESSION,
        tenant="alice",
        role="admin",
    )
    assert p.attributable is True  # USER_SESSION joined ATTRIBUTABLE_AUTH_METHODS (D6)
    assert "role" not in p.to_dict()  # D7.1: never frozen into persisted records
    assert Principal.from_dict(p.to_dict()).role == "user"


def test_unwrap_auth_dep_sees_through_nested_gates() -> None:
    """Structural guards match dependencies by identity — gates must stay transparent."""
    from src.api.admin_auth import (
        make_admin_gate,
        make_admin_write_gate,
        unwrap_auth_dep,
    )

    async def original() -> None:
        return None

    assert unwrap_auth_dep(original) is original  # unwrapped passes through
    single = make_admin_write_gate(original)
    assert unwrap_auth_dep(single) is original
    nested = make_admin_gate(single)
    assert unwrap_auth_dep(nested) is original


# ---------------------------------------------------------------------------
# D. D5 precedence matrix over HTTP
# ---------------------------------------------------------------------------


def test_flag_on_precedence_session_then_shared_key(auth_env) -> None:
    session_token = _session_for(get_user_store())
    client = TestClient(api_server.app)

    assert client.get("/runs", headers=_bearer(session_token)).status_code == 200
    assert client.get("/runs", headers=_bearer(SHARED_KEY)).status_code == 200
    assert client.get("/runs", headers=_bearer("garbage-token")).status_code == 401
    assert client.get("/runs").status_code == 401


def test_flag_off_ignores_sessions_and_issues_zero_queries(
    monkeypatch, tmp_path
) -> None:
    """Upstreamability contract: flag=0 ⇒ no session branch, no DB file, no queries."""
    singleton_db = tmp_path / "must-not-exist.db"
    monkeypatch.delenv("VIBE_TRADING_USER_AUTH", raising=False)
    monkeypatch.setenv("API_AUTH_KEY", SHARED_KEY)
    monkeypatch.setenv("VIBE_TRADING_USERS_DB_PATH", str(singleton_db))
    monkeypatch.setattr(api_server, "_API_KEY", SHARED_KEY)
    reset_env_config()
    reset_user_store()

    direct = UserStore(tmp_path / "direct.db")
    try:
        token = _session_for(direct)
        client = TestClient(api_server.app)
        # A perfectly valid session token is treated as a wrong shared key:
        assert client.get("/runs", headers=_bearer(token)).status_code == 401
        assert client.get("/runs", headers=_bearer(SHARED_KEY)).status_code == 200
    finally:
        direct.close()
        reset_user_store()
    assert not singleton_db.exists()
