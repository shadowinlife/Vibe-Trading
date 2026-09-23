"""Patch bootstrap: kruise private-protocol patch, executed before any e2b use.

The patch rewrites e2b's host/URL routing to ``http://<E2B_DOMAIN>/kruise/<sid>/<port>/...``
and (with ``https=False``) points the API at the port-forwarded gateway.
No network I/O happens at patch time; credentials are never read here —
:func:`resolve_api_key` only locates them, callers pass them as SDK opts so
no secret is ever written into a mutable global by accident.
"""

from __future__ import annotations

import os
from pathlib import Path

from kruise_agents.patch_e2b import patch_e2b

DEFAULT_DOMAIN = "localhost:8888"
DEFAULT_API_KEY_FILE = "~/.acs-sandbox/admin-api-key"

_patched = False


def ensure_patched() -> None:
    """Apply ``patch_e2b(https=False)`` exactly once (idempotent)."""
    global _patched
    if _patched:
        return
    os.environ.setdefault("E2B_DOMAIN", DEFAULT_DOMAIN)
    patch_e2b(https=False)
    _patched = True


def is_patched() -> bool:
    return _patched


def resolve_api_key() -> str:
    """E2B_API_KEY env, else E2B_API_KEY_FILE, else ~/.acs-sandbox/admin-api-key."""
    key = os.environ.get("E2B_API_KEY", "").strip()
    if key:
        return key
    path = Path(os.environ.get("E2B_API_KEY_FILE", DEFAULT_API_KEY_FILE)).expanduser()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(
            f"no E2B_API_KEY in env and API key file unreadable ({path}): {exc}"
        ) from exc
    if not text:
        raise RuntimeError(f"API key file is empty: {path}")
    return text


def gateway_domain() -> str:
    """The kruise gateway domain (host:port) used for HTTP route probes."""
    return os.environ.get("E2B_DOMAIN", DEFAULT_DOMAIN).strip() or DEFAULT_DOMAIN
