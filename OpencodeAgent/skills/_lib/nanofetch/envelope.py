"""Uniform result envelope: one JSON object on stdout, loud argument errors."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def unavailable(source: str, reason: str, **fields: Any) -> dict[str, Any]:
    return {"available": False, "source": source, "reason": reason, **fields}


def fail_args(message: str) -> None:
    """Reject bad arguments: stderr plus exit 2, never a JSON payload."""
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def clip(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def emit(payload: dict[str, Any], *, body: str | None = None, out: str | None = None) -> None:
    """Print *payload*; write *body* to *out* first so the payload can point at it."""
    if out and body is not None:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        payload["path"] = str(path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))