"""HTML flattening and excerpt extraction for filing/announcement bodies."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

_BLANK_LINES = re.compile(r"\n{3,}")


def html_to_text(html: str) -> str:
    """Flatten *html* to plain text, preserving paragraph breaks."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines()]
    return _BLANK_LINES.sub("\n\n", "\n".join(line for line in lines if line))


def grep_excerpts(
    text: str,
    pattern: str,
    *,
    context: int = 240,
    max_hits: int = 20,
) -> list[dict[str, Any]]:
    """Return windows around each match so the caller reads a slice, not the whole file."""
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"invalid --grep pattern: {exc}") from exc
    # Adjacent matches (e.g. repeated glossary terms) would otherwise return the
    # same paragraph several times; merge them into one window instead, bounded
    # so a dense run of matches cannot produce a multi-thousand-char excerpt.
    limit = context * 4
    windows: list[tuple[int, int]] = []
    for match in regex.finditer(text):
        start = max(0, match.start() - context)
        end = min(len(text), match.end() + context)
        if windows:
            last_start, last_end = windows[-1]
            if start <= last_end and max(last_end, end) - last_start <= limit:
                windows[-1] = (last_start, max(last_end, end))
            else:
                windows.append((start, end))
        else:
            windows.append((start, end))
        if len(windows) >= max_hits:
            break
    return [{"offset": start, "excerpt": text[start:end]} for start, end in windows]