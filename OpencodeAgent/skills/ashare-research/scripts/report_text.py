#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from nanofetch import assert_host, clip, emit, fetch, grep_excerpts, html_to_text, unavailable  # noqa: E402

from bs4 import BeautifulSoup  # noqa: E402
from nanofetch.envelope import fail_args  # noqa: E402
from nanofetch.http import HostNotAllowed  # noqa: E402

SOURCE = "sina"
ALLOWED_HOSTS = ("vip.stock.finance.sina.com.cn", "finance.sina.com.cn")
NOT_FOUND_MARKERS = ("暂时没有数据", "没有找到相关内容", "页面不存在", "您访问的页面")


def _reason(exc: Exception) -> str:
    message = str(exc).strip()
    return (message.splitlines()[0] if message else exc.__class__.__name__)[:180]


def _extract(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for attrs in ({"class": "blk_container"}, {"id": "content"}, {"class": "content"}):
        node = soup.find("div", attrs=attrs)
        if node is None:
            continue
        text = html_to_text(str(node))
        if text:
            return text
    body = soup.find("body")
    if body is None:
        raise RuntimeError("unexpected page shape: no body element")
    return html_to_text(str(body))


def _check(text: str) -> str:
    if len(text) < 40 or any(marker in text[:120] for marker in NOT_FOUND_MARKERS):
        raise RuntimeError("report page returned no body")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch one Sina Finance broker report body."
    )
    parser.add_argument("--url", required=True, help="source_url from industry_reports.py")
    parser.add_argument("--grep", default="", help="regex; return excerpt windows")
    parser.add_argument(
        "--max-chars", dest="max_chars", type=int, default=20000,
        help="clip text to this many characters (default 20000)",
    )
    parser.add_argument("--out", default="", help="write the full body to this file")
    args = parser.parse_args(argv)

    try:
        assert_host(args.url, ALLOWED_HOSTS)
    except HostNotAllowed as exc:
        fail_args(str(exc))

    if args.grep:
        try:
            re.compile(args.grep)
        except re.error as exc:
            fail_args(f"invalid --grep pattern: {exc}")

    try:
        html = fetch(args.url, encoding="gbk")
        text = _check(_extract(html))
    except Exception as exc:
        emit(unavailable(SOURCE, _reason(exc), source_url=args.url))
        return 0

    shown, truncated = clip(text, args.max_chars)
    payload = {
        "available": True,
        "source": SOURCE,
        "source_url": args.url,
        "chars": len(text),
        "truncated": truncated,
        "text": shown,
    }
    if args.grep:
        excerpts = grep_excerpts(text, args.grep)
        payload["excerpts"] = excerpts
        payload["matched"] = len(excerpts)
        payload["text"] = ""
        payload["truncated"] = False
    emit(payload, body=text, out=args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
