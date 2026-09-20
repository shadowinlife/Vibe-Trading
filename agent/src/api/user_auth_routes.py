"""Username/password + invite-code auth routes for the opt-in user-auth layer.

Mounted by ``agent/api_server.py`` via ``register_auth_mode_routes(app)`` and
``register_user_auth_routes(app, ...)`` (same ``register_*(app, deps...)`` +
``sys.modules`` host-resolution pattern as ``auth_routes.py``).

``GET /auth/mode`` is registered in BOTH flag modes, unauthenticated, and
returns JSON ``{"user_auth": <bool>}`` (plan D19). It must never be a path
that falls through to the SPA catch-all: ``src/api/spa.py`` turns any
unmatched GET into ``200 + index.html``, which is exactly why the frontend
cannot probe capability by status code and gates its whole login UX on this
endpoint's JSON body instead.

The remaining endpoints are also registered in both modes but refuse with 403
when the flag is off, so a flag-off deployment never exposes a working
password surface (and a stale ``users.db`` can never authenticate anyone).

Error discipline (plan D17 / §2.5): login failures always return the SAME
``401 {"detail": "Invalid username or password"}`` — never distinguishing
"user not found" from "wrong password" (username enumeration). A missing user
still runs a dummy scrypt verify so the response TIME is uniform too.
"""

from __future__ import annotations

import logging
import re
import sys as _sys
from typing import Any, Awaitable, Callable, Optional

from fastapi import FastAPI, HTTPException, Request, Security, status
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel

from src.api.password_hashing import hash_password, verify_password
from src.api.security import (
    SHARED_KEY_SUBJECT,
    _reject_cross_site_browser_request,
    _security,
    user_auth_enabled,
)
from src.api.user_store import get_user_store
from src.api.user_store_schema import (
    InviteInvalidError,
    UsernameExistsError,
    normalize_username,
)
from src.config.accessor import get_env_config
from src.session.models import AuthMethod, Principal

logger = logging.getLogger(__name__)

AuthDep = Callable[..., Awaitable[Any] | Any]

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
_PASSWORD_MIN_LENGTH = 8
_PASSWORD_MAX_LENGTH = 128  # bounds scrypt input amplification (§2.5)
_INVALID_LOGIN_DETAIL = "Invalid username or password"


class RegisterRequest(BaseModel):
    """POST /auth/register body (§2.5)."""

    username: str
    password: str
    invite_code: str


class LoginRequest(BaseModel):
    """POST /auth/login body (§2.5)."""

    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    """POST /auth/change-password body (§2.5)."""

    old_password: str
    new_password: str


class AuthSessionResponse(BaseModel):
    """Login/register success payload. ``token`` appears exactly once."""

    token: str
    username: str
    role: str
    display_name: Optional[str] = None


class AuthMeResponse(BaseModel):
    """GET /auth/me payload (§2.5)."""

    username: str
    role: str
    display_name: Optional[str] = None


_dummy_hash_cache: Optional[str] = None


def _dummy_hash() -> str:
    """Throwaway scrypt hash so a missing user costs the same as a real verify.

    Without it, login timing would distinguish "no such user" (instant) from
    "wrong password" (full scrypt) — a timing side channel around the uniform
    error message. Lazily computed on first miss; a benign race just computes
    the deterministic-value hash twice.
    """
    global _dummy_hash_cache  # noqa: PLW0603
    if _dummy_hash_cache is None:
        _dummy_hash_cache = hash_password("timing-equalizer-not-a-real-password")
    return _dummy_hash_cache


def _require_user_auth_enabled() -> None:
    if not user_auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User authentication is disabled",
        )


def _validate_registration(username: str, password: str) -> tuple[str, str]:
    """Return ``(normalized_username, display_name)``; 400 on invalid input.

    ``display_name`` preserves the user's original casing for the UI while the
    stored/lookup username is ``strip().lower()`` (plan D4 normalization).
    """
    stripped = username.strip()
    if not _USERNAME_RE.match(stripped):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username must be 3-32 characters from [A-Za-z0-9_-]",
        )
    if not _PASSWORD_MIN_LENGTH <= len(password) <= _PASSWORD_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Password must be {_PASSWORD_MIN_LENGTH}-"
                f"{_PASSWORD_MAX_LENGTH} characters"
            ),
        )
    return normalize_username(stripped), stripped


def register_auth_mode_routes(app: FastAPI) -> None:
    """Mount ``GET /auth/mode`` — the frontend capability gate (plan D19).

    Unauthenticated and registered in BOTH flag modes; flag off returns
    ``{"user_auth": false}`` so the SPA bypasses its login UX entirely.
    """

    @app.get("/auth/mode")
    async def auth_mode() -> dict[str, bool]:
        return {"user_auth": user_auth_enabled()}


def register_auth_stack(app: FastAPI) -> None:
    """Mount the full auth route stack in one call.

    Keeps ``api_server.py`` — a thin assembler under a hard 400-line test
    budget (``test_api_infrastructure.py::test_api_server_is_thin_assembler``)
    — at a single import + call for everything auth-related: the pre-existing
    SSE-ticket helper, the D19 capability endpoint, the credential endpoints,
    and the sanitized D8 runtime-settings endpoint.
    """
    from src.api.auth_routes import register_auth_routes
    from src.api.runtime_settings_routes import register_runtime_settings_routes

    register_auth_routes(app)
    register_auth_mode_routes(app)
    register_user_auth_routes(app)
    register_runtime_settings_routes(app)


def register_user_auth_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    """Mount register/login/logout/me/change-password onto ``app``.

    Args:
        app: The host FastAPI app.
        require_auth: Dependency guarding the session-bearing endpoints. When
            omitted it is resolved from the host ``api_server`` module via
            ``sys.modules`` (matches the other ``register_*_routes`` helpers).
            Under flag=1 it accepts user session tokens automatically through
            the D5 step-2 branch of ``_validate_api_auth``.
    """
    if require_auth is None:
        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:  # pragma: no cover — only on weird import setups
            raise RuntimeError(
                "register_user_auth_routes: api_server module not in sys.modules; "
                "pass require_auth explicitly"
            )
        require_auth = host.require_auth

    @app.post(
        "/auth/register",
        response_model=AuthSessionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def register(
        payload: RegisterRequest, request: Request
    ) -> AuthSessionResponse:
        """Create a user from a valid invite and return a fresh session (§2.5)."""
        _require_user_auth_enabled()
        # Login/register are browser POSTs, so they are subject to the same
        # cross-site guard every other unsafe-method path runs (the 2026-09-19
        # production incident was exactly this check failing behind nginx).
        _reject_cross_site_browser_request(request)
        if not get_env_config().api.vibe_trading_allow_self_register:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Self-registration is disabled",
            )
        username, display_name = _validate_registration(
            payload.username, payload.password
        )
        invite_code = payload.invite_code.strip()
        if not invite_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invite code is required",
            )
        store = get_user_store()
        try:
            user = store.register_with_invite(
                username, hash_password(payload.password), display_name, invite_code
            )
        except InviteInvalidError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or exhausted invite code",
            ) from exc
        except UsernameExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username already exists",
            ) from exc
        token = store.create_session(
            user.id, user_agent=request.headers.get("user-agent")
        )
        logger.info("user registered: username=%s", user.username)
        return AuthSessionResponse(
            token=token,
            username=user.username,
            role=user.role,
            display_name=user.display_name,
        )

    @app.post("/auth/login", response_model=AuthSessionResponse)
    async def login(payload: LoginRequest, request: Request) -> AuthSessionResponse:
        """Exchange username/password for a fresh session token (§2.5).

        Always mints a NEW token — no pre-login state is ever reused (session
        fixation, §4.7).
        """
        _require_user_auth_enabled()
        _reject_cross_site_browser_request(request)
        username = normalize_username(payload.username)
        store = get_user_store()
        user = store.get_user(username)
        password_hash = (
            user.password_hash if (user and user.is_active) else _dummy_hash()
        )
        password_ok = verify_password(payload.password, password_hash)
        if user is None or not user.is_active or not password_ok:
            logger.info("login failed: username=%s", username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_INVALID_LOGIN_DETAIL,
            )
        token = store.create_session(
            user.id, user_agent=request.headers.get("user-agent")
        )
        store.touch_last_login(user.id)
        logger.info("login succeeded: username=%s", username)
        return AuthSessionResponse(
            token=token,
            username=user.username,
            role=user.role,
            display_name=user.display_name,
        )

    @app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(
        request: Request,
        cred: Optional[HTTPAuthorizationCredentials] = Security(_security),
    ) -> Response:
        """Revoke the presenting session (§2.5: 204, no body)."""
        _require_user_auth_enabled()
        await require_auth(request, cred)
        get_user_store().revoke_session(
            cred.credentials if (cred and cred.credentials) else ""
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/auth/me", response_model=AuthMeResponse)
    async def me(
        request: Request,
        cred: Optional[HTTPAuthorizationCredentials] = Security(_security),
    ) -> AuthMeResponse:
        """Return the caller's identity (§2.5)."""
        _require_user_auth_enabled()
        principal = await require_auth(request, cred)
        return _me_response(principal)

    @app.post("/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
    async def change_password(
        payload: ChangePasswordRequest,
        request: Request,
        cred: Optional[HTTPAuthorizationCredentials] = Security(_security),
    ) -> Response:
        """Rehash the password and revoke every OTHER session (§4.7)."""
        _require_user_auth_enabled()
        principal = await require_auth(request, cred)
        if principal.auth_method is not AuthMethod.USER_SESSION:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Session authentication required",
            )
        if (
            not _PASSWORD_MIN_LENGTH
            <= len(payload.new_password)
            <= _PASSWORD_MAX_LENGTH
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Password must be {_PASSWORD_MIN_LENGTH}-"
                    f"{_PASSWORD_MAX_LENGTH} characters"
                ),
            )
        store = get_user_store()
        user = store.get_user(principal.subject)
        if user is None or not user.is_active:
            # TOCTOU race: the account died between require_auth's JOIN and
            # this re-read. The session genuinely no longer holds ⇒ 401, the
            # status the frontend treats as "session dead".
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session is no longer valid",
            )
        if not verify_password(payload.old_password, user.password_hash):
            # 400, NOT 401: the caller IS authenticated — a wrong value in the
            # request body is a validation failure. Returning 401 here would
            # trip the frontend's "401 ⇒ destroy the session" invariant and
            # log out a user whose session is perfectly valid (§2.5).
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid old password",
            )
        store.set_password(user.username, hash_password(payload.new_password))
        store.revoke_other_sessions(
            user.id, keep_token=cred.credentials if (cred and cred.credentials) else ""
        )
        logger.info("password changed: username=%s", user.username)
        return Response(status_code=status.HTTP_204_NO_CONTENT)


def _me_response(principal: Principal) -> AuthMeResponse:
    if principal.auth_method is AuthMethod.USER_SESSION:
        return AuthMeResponse(
            username=principal.subject,
            role=principal.role,
            display_name=principal.display_name,
        )
    if principal.auth_method is AuthMethod.SHARED_KEY:
        # Break-glass machine channel: the shared key is god-mode by design
        # (D7), so report it as admin rather than invent a user identity.
        return AuthMeResponse(
            username=SHARED_KEY_SUBJECT, role="admin", display_name=None
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Session authentication required",
    )
