"""Thin multi-tenant reverse proxy for the T10 tenant containers (plan T11/D2).

Routing only: ``token -> tenant -> upstream`` plus ``Host -> tenant``. Business
auth stays in each tenant gateway (which re-validates the same Bearer key
against its own ``API_AUTH_KEY``), so this package carries no business logic.

Modules:
    config      the single env boundary (:class:`RouterSettings`)
    registry    tenant table + resolution outcomes (tagged union)
    fence       per-tenant admission fence (openwork directory-fence pattern)
    backend     container control (docker CLI local; ECS documented)
    wake        wake-on-inbound cold start + timeout fallback page
    reclaim     idle reclaim with the engine truth check (opencode-router)
    app         the ASGI proxy (Host-preserving forwarding + SSE passthrough)
    cli         operator entry points (serve / show / reclaim)
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
