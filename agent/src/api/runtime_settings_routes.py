"""Sanitized runtime settings for every authenticated user (plan D8).

``GET /settings/llm`` is admin-gated under user auth, but the chat page still
needs the SSE watchdog timeout and the provider/model display names. This
endpoint returns ONLY ``{provider, model_name, sse_timeout_seconds}``.

Deliberately excluded (compare ``LLMSettingsResponse`` in
``settings_routes.py``): ``api_key_hint`` / ``api_key_configured``
(credential state), ``env_path`` (filesystem path leak), and the ``providers``
catalog (contains ``api_key_env`` names). The ``response_model`` below is the
enforcement point: even though the builder computes the full settings object,
FastAPI serializes only the three declared fields.
"""

from __future__ import annotations

import sys as _sys
from typing import Any, Awaitable, Callable, Optional  # noqa: F401

from fastapi import Depends, FastAPI
from pydantic import BaseModel

AuthDep = Callable[..., Awaitable[Any] | Any]


class RuntimeSettingsResponse(BaseModel):
    """The only three fields a non-admin client may see (§2.5)."""

    provider: str
    model_name: str
    sse_timeout_seconds: int


def register_runtime_settings_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    """Mount ``GET /settings/runtime`` onto ``app``.

    Args:
        app: The host FastAPI app.
        require_auth: Any-authenticated-user dependency (NOT the admin gate).
            When omitted it is resolved from the host ``api_server`` module via
            ``sys.modules`` (matches the other ``register_*_routes`` helpers).
    """
    if require_auth is None:
        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:  # pragma: no cover — only on weird import setups
            raise RuntimeError(
                "register_runtime_settings_routes: api_server module not in "
                "sys.modules; pass require_auth explicitly"
            )
        require_auth = host.require_auth

    @app.get(
        "/settings/runtime",
        response_model=RuntimeSettingsResponse,
        dependencies=[Depends(require_auth)],
    )
    async def get_runtime_settings() -> RuntimeSettingsResponse:
        from src.api.settings_routes import _build_llm_settings_response

        settings = _build_llm_settings_response()
        return RuntimeSettingsResponse(
            provider=settings.provider,
            model_name=settings.model_name,
            sse_timeout_seconds=settings.sse_timeout_seconds,
        )
