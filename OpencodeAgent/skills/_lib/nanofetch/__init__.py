"""Shared HTTP/text helpers for the ashare-* skill scripts."""

from __future__ import annotations

from .envelope import clip, emit, unavailable
from .http import HttpError, assert_host, fetch
from .text import grep_excerpts, html_to_text

__all__ = [
    "HttpError",
    "assert_host",
    "clip",
    "emit",
    "fetch",
    "grep_excerpts",
    "html_to_text",
    "unavailable",
]