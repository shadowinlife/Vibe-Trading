"""Flag-aware admin gate for the opt-in user-auth layer (plan D5/D7).

Why flag-aware at REQUEST time, not registration time (Oracle B2): injection
happens once, at app assembly, while ``VIBE_TRADING_USER_AUTH`` is read per
request. A gate that unconditionally demanded an admin user session would
break the flag-off world — local dev loopback principals AND the desktop
Electron shell, which injects a per-launch ``API_AUTH_KEY`` bearer
(``desktop/electron/src/main.ts``) — violating the "default off = behavior
100% unchanged" upstreamability contract. So:

* flag=0 ⇒ delegate to the wrapped original dependency, byte-identical.
* flag=1 ⇒ require ``USER_SESSION`` with ``role == "admin"``; the shared key
  is ALSO accepted as break-glass. The shared key is already god-mode (it can
  rewrite LLM credentials), so refusing it would add no security — it would
  only cut the machine channel and risk a lockout when ``users.db`` is
  damaged. The D10 admin CLI is the server-side complement (direct DB access,
  no HTTP).

Also home to the D5 startup invariant: ``VIBE_TRADING_USER_AUTH=1`` with an
empty ``API_AUTH_KEY`` refuses to start. That combination is the
"public-internet-wide-open" misconfiguration quadrant — with no key, key-first
precedence (GHSA-7wgj) cannot disable loopback trust, and a reverse proxy
reporting clients as local would hand out full access with zero credentials.
The invariant excludes the quadrant by design instead of relying on deployment
order and operator discipline.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Optional

from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials

from src.api.security import (
    _configured_api_key,
    _security,
    _validate_api_auth,
    user_auth_enabled,
)
from src.session.models import AuthMethod, Principal

AuthDep = Callable[..., Awaitable[Any] | Any]

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Unsafe-method routes that are documented read-only and therefore must NOT
#: be admin-gated when injected as a module-wide ``require_auth`` replacement.
#: ``POST /live/connectors/{profile_id}/verify`` is a POST by RPC convention
#: only — its own contract states "Never writes or mutates broker state" — and
#: the Runtime page consumes it for connection status display.
LIVE_READ_ONLY_UNSAFE_ROUTES = frozenset({"/live/connectors/{profile_id}/verify"})


def enforce_user_auth_startup_invariant() -> None:
    """Refuse to start when user auth is on without a shared key (plan D5).

    Called from ``api_server._run_startup_preflight`` AFTER ``run_preflight``
    (which loads ``agent/.env`` into the process environment and resets the
    cached EnvConfig), so a key living only in the dotenv file is seen.

    Raises:
        RuntimeError: ``VIBE_TRADING_USER_AUTH=1`` and no ``API_AUTH_KEY``.
    """
    if not user_auth_enabled():
        return
    if not _configured_api_key():
        raise RuntimeError(
            "VIBE_TRADING_USER_AUTH=1 requires API_AUTH_KEY to be set. "
            "Without the shared key, key-first precedence cannot disable "
            "loopback trust, and any peer a reverse proxy reports as local "
            "would get full API access with zero credentials. Set a long "
            "random API_AUTH_KEY (the machine/break-glass channel) alongside "
            "the flag."
        )


def ensure_admin_principal(
    *,
    request: Request,
    cred: Optional[HTTPAuthorizationCredentials],
) -> Principal:
    """Authenticate and require admin (flag=1 semantics); return the principal.

    Runs the full D5 precedence via :func:`_validate_api_auth` — session
    branch first, then shared key — so a valid user session is never mistaken
    for a wrong key, then narrows the verdict to admin-or-break-glass.

    Raises:
        HTTPException: 401 from the underlying auth (bad/missing credential),
            403 for an authenticated non-admin.
    """
    principal = _validate_api_auth(request=request, cred=cred)
    if principal.auth_method is AuthMethod.SHARED_KEY:
        return principal
    if principal.auth_method is AuthMethod.USER_SESSION and principal.role == "admin":
        return principal
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Administrator access required",
    )


async def _delegate(original: AuthDep, request: Request, cred: Any) -> None:
    result = original(request, cred)
    if inspect.isawaitable(result):
        await result


def unwrap_auth_dep(dep: AuthDep) -> AuthDep:
    """Follow the ``__vt_wrapped_auth__`` chain to the innermost real dependency.

    Gate wrappers must stay introspectably transparent: a structural guard
    (``test_playbooks_surface.py`` — "every /scheduled-runs route declares
    require_auth") asserts route dependencies by object IDENTITY, and a
    wrapper that hid the wrapped original would fail that guard even though
    it delegates to the original on every non-admin path. Returns *dep*
    unchanged when it carries no wrapper attribute; handles nested gates and
    refuses to spin on a pathological attribute cycle.
    """
    seen: set[int] = set()
    while id(dep) not in seen:
        seen.add(id(dep))
        inner = getattr(dep, "__vt_wrapped_auth__", None)
        if inner is None:
            return dep
        dep = inner
    return dep


def make_admin_gate(original: AuthDep) -> AuthDep:
    """Wrap *original* so flag=1 requires admin and flag=0 delegates unchanged.

    The original dependency object is captured at registration time — never
    re-resolved from the host module — so monkeypatch-based tests and the
    ``api_server.require_local_or_auth is security.require_local_or_auth``
    identity assertion (test_api_infrastructure.py) are unaffected.
    """

    async def require_admin(
        request: Request,
        cred: Optional[HTTPAuthorizationCredentials] = Security(_security),
    ) -> None:
        if user_auth_enabled():
            ensure_admin_principal(request=request, cred=cred)
            return
        await _delegate(original, request, cred)

    require_admin.__vt_wrapped_auth__ = original  # see unwrap_auth_dep
    return require_admin


def make_admin_write_gate(
    original: AuthDep,
    *,
    read_only_unsafe_routes: frozenset[str] = frozenset(),
) -> AuthDep:
    """Wrap *original* so only MUTATING endpoints require admin under flag=1.

    ``register_live_routes`` / ``register_channels_routes`` /
    ``register_scheduled_routes`` each take a single module-wide
    ``require_auth`` parameter, so per-endpoint discrimination has to happen
    at request time. Safe methods (GET/HEAD/OPTIONS) and the explicitly listed
    read-only unsafe routes keep plain *original* auth: a normal user needs to
    SEE state (live status, channel status, scheduled list) to understand why
    they cannot change it. Everything else — every POST/PUT/DELETE that
    mutates — requires admin under flag=1, and delegates unchanged under
    flag=0.
    """

    async def require_admin_for_writes(
        request: Request,
        cred: Optional[HTTPAuthorizationCredentials] = Security(_security),
    ) -> None:
        if user_auth_enabled() and _is_mutating_request(
            request, read_only_unsafe_routes
        ):
            ensure_admin_principal(request=request, cred=cred)
            return
        await _delegate(original, request, cred)

    require_admin_for_writes.__vt_wrapped_auth__ = original  # see unwrap_auth_dep
    return require_admin_for_writes


def make_live_admin_write_gate(original: AuthDep) -> AuthDep:
    """Write gate for ``register_live_routes`` — honors the verify exemption.

    ``POST /live/connectors/{profile_id}/verify`` stays plain-auth: its own
    contract is "never writes or mutates broker state" and the Runtime page
    consumes it for connection status display. Every other unsafe-method live
    endpoint (mandate commit, halt/resume, authorize, runner start/stop) is
    real-money territory and requires admin under flag=1.
    """
    return make_admin_write_gate(
        original, read_only_unsafe_routes=LIVE_READ_ONLY_UNSAFE_ROUTES
    )


def _is_mutating_request(
    request: Request, read_only_unsafe_routes: frozenset[str]
) -> bool:
    """Classify by HTTP method, with the matched route template as exemption.

    ``request.scope["route"]`` is set by FastAPI before dependencies run, so
    the exemption matches the route TEMPLATE (``/live/connectors/{profile_id}/
    verify``) — no regex over concrete paths. A missing route (direct unit
    invocation) defaults to "mutating", the fail-closed side.
    """
    if request.method.upper() in _SAFE_METHODS:
        return False
    route_path = getattr(request.scope.get("route"), "path", None)
    return route_path not in read_only_unsafe_routes
