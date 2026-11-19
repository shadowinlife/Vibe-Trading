"""Frozen HTTP surface for VT state export/import (change request 0004).

Two routes on the existing strategy-run entry process — no second process, port
or scheduler. Both authenticate with the RM service token from
:mod:`src.api.strategy_run_auth` and answer exact top-level JSON bodies; a
failure is never ``200 + {"ok": false}`` and never wrapped in ``detail``.

* ``POST /state/export`` — ``{"scope": "full"}`` streams the ``vt-state-v1``
  gzip blob with ``X-VT-State-Layout`` / ``X-VT-State-Content-Sha256``;
  ``{"scope": "run", "vt_run_id": ...}`` returns the bounded run payload.
* ``POST /state/import`` — one gzip blob plus ``X-VT-State-Layout``.

Business logic (SQLite snapshots, manifest validation, staging, atomic
replacement, live-handle reopen) belongs to ``src.state_export.service`` and is
imported lazily inside the three module-level seams below, so this module
imports cleanly before that package lands and tests can monkeypatch the seams::

    _export_full() -> bundle                  # .blob plus .manifest (or a pair)
    _import_full(blob, layout) -> result      # the five import counters
    _export_run(vt_run_id) -> dict            # bounded metrics/equity/trades

Errors are mapped by their stable ``code`` (the contract of
:class:`src.state_export.manifest.StateExportError`); a seam error without one
is recognised by its frozen class name. Only the exception type is ever logged:
the service token, the ``Authorization`` header and the blob never reach a log
line or a response body.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from src.api.strategy_run_auth import require_strategy_run_token
from src.state_export.manifest import MAX_BLOB_BYTES

logger = logging.getLogger(__name__)

router = APIRouter(tags=["state"])

LAYOUT_HEADER = "X-VT-State-Layout"
CONTENT_SHA_HEADER = "X-VT-State-Content-Sha256"
GZIP_MEDIA_TYPE = "application/gzip"

_IMPORT_FIELDS = ("layout_version", "content_sha256", "strategies", "versions", "reopened")

#: Stable error code -> ``(status, response token)``. Codes are the taxonomy
#: anchor; the token is what the frozen wire contract prints.
_STATUS_BY_CODE: dict[str, tuple[int, str]] = {
    "export_unstable": (status.HTTP_409_CONFLICT, "export_unstable"),
    "unknown_run_id": (status.HTTP_404_NOT_FOUND, "unknown_vt_run_id"),
    "unknown_vt_run_id": (status.HTTP_404_NOT_FOUND, "unknown_vt_run_id"),
    "import_in_progress": (status.HTTP_409_CONFLICT, "import_in_progress"),
    "blob_too_large": (status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "blob_too_large"),
    "layout_version_unsupported": (status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "layout_version_unsupported"),
    "manifest_invalid": (status.HTTP_422_UNPROCESSABLE_ENTITY, "manifest_invalid"),
    "path_escape": (status.HTTP_422_UNPROCESSABLE_ENTITY, "path_escape"),
    "blob_corrupt": (status.HTTP_422_UNPROCESSABLE_ENTITY, "blob_corrupt"),
    "import_failed": (status.HTTP_500_INTERNAL_SERVER_ERROR, "import_failed"),
}

#: Fallback for a seam error that carries no ``code`` attribute.
_TYPE_TO_CODE: dict[str, str] = {
    "ExportUnstable": "export_unstable",
    "UnknownRunId": "unknown_run_id",
    "ImportInProgress": "import_in_progress",
    "BlobTooLarge": "blob_too_large",
    "LayoutUnsupported": "layout_version_unsupported",
    "ManifestInvalid": "manifest_invalid",
    "PathEscape": "path_escape",
    "BlobCorrupt": "blob_corrupt",
    "ImportFailed": "import_failed",
}

_EXPORT_FULL_ERRORS = frozenset({"export_unstable", "blob_too_large"})
_EXPORT_RUN_ERRORS = frozenset({"unknown_vt_run_id"})
_IMPORT_ERRORS = frozenset(
    {
        "import_in_progress",
        "blob_too_large",
        "layout_version_unsupported",
        "manifest_invalid",
        "path_escape",
        "blob_corrupt",
        "import_failed",
    }
)


class StateExportRequest(BaseModel):
    """Body of ``POST /state/export``; the scope is validated by the handler."""

    model_config = ConfigDict(extra="forbid")

    scope: str | None = None
    vt_run_id: str | None = None


def _json(status_code: int, payload: dict[str, Any]) -> JSONResponse:
    """Return an exact JSON body, bypassing any response-model wrapping."""
    return JSONResponse(status_code=status_code, content=payload)


def _error_status(exc: BaseException) -> tuple[int, str] | None:
    """Map a seam exception onto its frozen ``(status, token)``, if any."""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in _STATUS_BY_CODE:
        return _STATUS_BY_CODE[code]
    alias = _TYPE_TO_CODE.get(type(exc).__name__)
    return _STATUS_BY_CODE.get(alias) if alias is not None else None


def _error_response(exc: BaseException, *, allowed: frozenset[str], fallback: str) -> JSONResponse:
    """Return a mapped error when the route owns it, else the fixed 500."""
    mapped = _error_status(exc)
    if mapped is not None and mapped[1] in allowed:
        return _json(mapped[0], {"error": mapped[1]})
    # Only the exception type: a message may carry blob bytes or request data.
    logger.error("state request failed (%s)", type(exc).__name__)
    return _json(status.HTTP_500_INTERNAL_SERVER_ERROR, {"error": fallback})


def _bundle_parts(bundle: Any) -> tuple[bytes, str, str]:
    """Read ``(blob, layout_version, content_sha256)`` off the export result.

    Accepts the seam's bundle object (``.blob`` plus ``.manifest`` or the two
    header values directly) and its ``(blob, manifest)`` pair form.
    """
    manifest = getattr(bundle, "manifest", None)
    blob = getattr(bundle, "blob", None)
    if isinstance(bundle, tuple) and len(bundle) == 2:
        blob, manifest = bundle
    layout = getattr(bundle, "layout_version", None)
    digest = getattr(bundle, "content_sha256", None)
    if isinstance(manifest, Mapping):
        layout = layout if isinstance(layout, str) else manifest.get("layout_version")
        digest = digest if isinstance(digest, str) else manifest.get("content_sha256")
    elif manifest is not None:
        layout = layout if isinstance(layout, str) else getattr(manifest, "layout_version", None)
        digest = digest if isinstance(digest, str) else getattr(manifest, "content_sha256", None)
    if not isinstance(blob, (bytes, bytearray)) or not isinstance(layout, str) or not layout:
        raise ValueError("incomplete state export bundle")
    if not isinstance(digest, str) or not digest:
        raise ValueError("incomplete state export bundle")
    return bytes(blob), layout, digest


def _import_payload(result: Any) -> dict[str, Any]:
    """Project the import result onto exactly the five frozen keys."""
    values = {
        name: result.get(name) if isinstance(result, Mapping) else getattr(result, name, None)
        for name in _IMPORT_FIELDS
    }
    texts = (values["layout_version"], values["content_sha256"])
    counters = (values["strategies"], values["versions"])
    valid = (
        all(isinstance(value, str) and value for value in texts)
        and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in counters)
        and isinstance(values["reopened"], bool)
    )
    if not valid:
        raise ValueError("incomplete state import result")
    return values


def _export_full() -> Any:
    """Build the full state blob; lazy so this module imports before T2/T3."""
    from src.state_export.service import export_full

    return export_full()


def _import_full(blob: bytes, layout: str) -> Any:
    """Import one blob; lazy for the same reason."""
    from src.state_export.service import import_full

    return import_full(blob, layout)


def _export_run(vt_run_id: str) -> dict[str, Any]:
    """Build the bounded run payload; lazy for the same reason."""
    from src.state_export.service import export_run

    return export_run(vt_run_id)


def _export_run_response(vt_run_id: str | None) -> Response:
    """Return the bounded run payload, or the frozen 400/404/500."""
    if not isinstance(vt_run_id, str) or not vt_run_id.strip():
        return _json(status.HTTP_400_BAD_REQUEST, {"error": "missing_vt_run_id"})
    try:
        payload = _export_run(vt_run_id)
        if not isinstance(payload, dict):
            raise ValueError("state run export is not an object")
        return _json(status.HTTP_200_OK, payload)
    except Exception as exc:  # noqa: BLE001 — mapped by the frozen error table
        return _error_response(exc, allowed=_EXPORT_RUN_ERRORS, fallback="export_failed")


@router.post(
    "/state/export",
    dependencies=[Depends(require_strategy_run_token)],
    response_model=None,
)
def export_state(body: StateExportRequest) -> Response:
    """Export the full state blob or one bounded run payload."""
    if body.scope not in {"full", "run"}:
        return _json(status.HTTP_400_BAD_REQUEST, {"error": "unknown_scope"})
    if body.scope == "run":
        return _export_run_response(body.vt_run_id)
    try:
        blob, layout, digest = _bundle_parts(_export_full())
    except Exception as exc:  # noqa: BLE001 — mapped by the frozen error table
        return _error_response(exc, allowed=_EXPORT_FULL_ERRORS, fallback="export_failed")
    return Response(
        content=blob,
        media_type=GZIP_MEDIA_TYPE,
        headers={LAYOUT_HEADER: layout, CONTENT_SHA_HEADER: digest},
    )


@router.post(
    "/state/import",
    dependencies=[Depends(require_strategy_run_token)],
    response_model=None,
)
async def import_state(request: Request) -> Response:
    """Replace the strategy library with one validated ``vt-state-v1`` blob."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BLOB_BYTES:
        return _json(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, {"error": "blob_too_large"})
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BLOB_BYTES:
            return _json(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, {"error": "blob_too_large"})
    body = bytes(body)
    layout = (request.headers.get(LAYOUT_HEADER) or "").strip()
    if not layout:
        return _json(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, {"error": "layout_version_unsupported"})
    try:
        # The service unarchives, hashes and replaces files: never block the loop.
        result = await run_in_threadpool(_import_full, body, layout)
        payload = _import_payload(result)
    except Exception as exc:  # noqa: BLE001 — mapped by the frozen error table
        return _error_response(exc, allowed=_IMPORT_ERRORS, fallback="import_failed")
    return _json(status.HTTP_200_OK, payload)


def register_state_routes(app: FastAPI) -> None:
    """Mount ``POST /state/export`` and ``POST /state/import`` on *app*.

    Args:
        app: The existing strategy-run entry application.
    """
    app.include_router(router)


__all__ = ["register_state_routes", "router"]
