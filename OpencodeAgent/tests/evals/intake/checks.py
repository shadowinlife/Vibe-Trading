"""Closing-block contract checks for artifact-bearing answers.

Every answer that ships a named artifact (个股体检 / 策略回测 / 交割单诊断 /
选股清单 / 事件影响 / HTML 报告) must end with the same four lines:

- ``我假设了什么：`` — the defaults that actually took effect
- ``换一个会怎样：`` — what one alternative assumption would change
- ``下一步能做什么：`` — at most three concrete follow-ups
- ``仅供研究，不构成投资建议；数据截止 T-1。`` — the compliance line

The fourth line is a compliance red line: ``仅供研究``, ``不构成投资建议`` and
``T-1`` must appear together on one line. Paraphrasing it as "投资有风险"
does not satisfy the contract.

Text-only answers (``什么是因子``) must **not** carry the block. Call
``assert_closing_block(text, required=False)`` for those: presence is still
reported, but a missing block is not an error. The default
(``required=True``) is for artifact-bearing answers.

Markdown decoration is tolerated (``**``, ``###``, ``-``, numbered lists,
backticks, block quotes, full-width or half-width colons); the Chinese label
text itself is required.

Library use::

    from checks import assert_closing_block

    result = assert_closing_block(answer_text)
    result = assert_closing_block(text_only_answer, required=False)

CLI use::

    python checks.py <file-or-->
    python checks.py - < answer.txt

The CLI prints the result as JSON and exits 0 when the block is complete,
1 when it is incomplete, and 2 on a usage or IO error.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Canonical prefixes, one per required line. ``disclaimer`` is the prefix of
# the compliance line, whose full requirement is ``DISCLAIMER_TOKENS``.
CLOSING_BLOCK_LINES: dict[str, str] = {
    "assumptions": "我假设了什么",
    "alternative": "换一个会怎样",
    "next_steps": "下一步能做什么",
    "disclaimer": "仅供研究",
}

# Human-readable labels used in ``missing`` and in the AssertionError text.
CLOSING_BLOCK_LABELS: dict[str, str] = {
    "assumptions": "我假设了什么：",
    "alternative": "换一个会怎样：",
    "next_steps": "下一步能做什么：",
    "disclaimer": "仅供研究，不构成投资建议；数据截止 T-1。",
}

# All three must appear on one line, in any order.
DISCLAIMER_TOKENS: tuple[str, ...] = ("仅供研究", "不构成投资建议", "T-1")

_HEADING_RE = re.compile(r"^#{1,6}\s*")
_LIST_RE = re.compile(r"^(?:[-*+•]|\d+[.)、])\s*")
_DECORATION_RE = re.compile(r"[*_`]+")


def _normalize(line: str) -> str:
    """Strip markdown decoration so label matching is layout-independent."""
    text = line.strip()
    previous = None
    while text != previous:
        previous = text
        text = _HEADING_RE.sub("", text)
        text = _LIST_RE.sub("", text)
        text = text.lstrip("> ").strip()
    return _DECORATION_RE.sub("", text).strip()


def find_closing_block(text: str) -> dict:
    """Report which of the four closing-block lines ``text`` carries.

    Returns a dict with one boolean per key in ``CLOSING_BLOCK_LINES``, plus
    ``complete`` and ``missing`` (labels, in canonical order).
    """
    lines = [_normalize(line) for line in text.splitlines()]
    lines = [line for line in lines if line]

    present: dict[str, bool] = {}
    for key, prefix in CLOSING_BLOCK_LINES.items():
        if key == "disclaimer":
            present[key] = any(
                all(token in line for token in DISCLAIMER_TOKENS) for line in lines
            )
        else:
            present[key] = any(line.startswith(prefix) for line in lines)

    missing = [
        CLOSING_BLOCK_LABELS[key] for key in CLOSING_BLOCK_LINES if not present[key]
    ]
    return {**present, "complete": not missing, "missing": missing}


def assert_closing_block(text: str, *, required: bool = True) -> dict:
    """Assert the closing block is complete when ``required``.

    ``required=True`` (the default) is for artifact-bearing answers and raises
    ``AssertionError`` listing the missing lines. ``required=False`` is for
    text-only answers: it returns the same result dict without raising, so the
    eval can record that the block correctly stayed absent.
    """
    result = find_closing_block(text)
    if required and not result["complete"]:
        missing = "、".join(result["missing"])
        raise AssertionError(f"closing block incomplete; missing: {missing}")
    return result


def _read_source(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(source)
    return path.read_text(encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python checks.py <file-or-->", file=sys.stderr)
        return 2
    try:
        text = _read_source(argv[1])
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read {argv[1]}: {exc}", file=sys.stderr)
        return 2

    result = find_closing_block(text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
