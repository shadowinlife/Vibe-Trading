"""Polite HTTP GET with retry, jitter and a host allow-list."""

from __future__ import annotations

import gzip
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Sequence

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class HttpError(RuntimeError):
    """A fetch that failed after every retry."""


class HostNotAllowed(ValueError):
    """A URL whose host is outside the caller's allow-list."""


def assert_host(url: str, allowed_suffixes: Sequence[str]) -> str:
    """Return the host of *url*, raising HostNotAllowed unless it matches."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HostNotAllowed(f"unsupported scheme: {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if not any(host == suffix or host.endswith("." + suffix) for suffix in allowed_suffixes):
        raise HostNotAllowed(f"host not allowed: {host!r}")
    return host


def fetch(
    url: str,
    *,
    timeout: float = 20.0,
    retries: int = 3,
    encoding: str = "utf-8",
) -> str:
    """GET *url* and return decoded text, retrying transient failures."""
    last: Exception | None = None
    for attempt in range(retries):
        if attempt:
            time.sleep(min(2 ** attempt, 8) + random.random())
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode(encoding, errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last = exc
    raise HttpError(f"GET {url} failed after {retries} attempts: {last}")