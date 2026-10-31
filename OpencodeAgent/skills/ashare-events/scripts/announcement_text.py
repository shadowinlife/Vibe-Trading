#!/usr/bin/env python3
"""单条公告正文（来源：新浪财经 vCB_AllBulletinDetail）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from bs4 import BeautifulSoup
from nanofetch import assert_host, clip, emit, fetch, grep_excerpts, html_to_text, unavailable
from nanofetch.envelope import fail_args
from nanofetch.http import HostNotAllowed

_ALLOWED_HOSTS = (
    "vip.stock.finance.sina.com.cn",
    "finance.sina.com.cn",
    "money.finance.sina.com.cn",
)
# 正文容器优先级，已实地验证；找不到时宁可报 shape 失败也不回退到整页导航噪声。
_CONTAINERS = ("content", "con02-7", "box")
_MIN_BODY_CHARS = 120


def _extract_body(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for container_id in _CONTAINERS:
        node = soup.find(id=container_id)
        if node is None:
            continue
        text = html_to_text(str(node))
        if len(text) >= _MIN_BODY_CHARS:
            return text
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="单条公告正文（新浪财经）")
    parser.add_argument("--url", required=True, help="list_announcements 返回的 source_url")
    parser.add_argument("--grep", help="正则，返回命中片段而不是整篇正文")
    parser.add_argument("--max-chars", type=int, default=20000, help="stdout 正文字符上限，默认 20000")
    parser.add_argument("--out", help="把完整正文写入该文件")
    args = parser.parse_args()

    try:
        assert_host(args.url, _ALLOWED_HOSTS)
    except HostNotAllowed as exc:
        fail_args(f"--url 不在允许范围（新浪财经公告页）: {exc}")

    if args.max_chars < 1:
        fail_args(f"--max-chars 必须为正整数，收到: {args.max_chars}")

    try:
        html = fetch(args.url, encoding="gbk")
    except RuntimeError as exc:  # nanofetch.HttpError：抓取失败
        emit(unavailable("sina", f"detail page fetch failed: {type(exc).__name__}", source_url=args.url))
        return 0

    text = _extract_body(html)
    if not text:
        emit(unavailable("sina", "unexpected detail page shape", source_url=args.url))
        return 0

    body, truncated = clip(text, args.max_chars)
    payload: dict[str, object] = {
        "available": True,
        "source": "sina",
        "source_url": args.url,
        "chars": len(text),
        "truncated": truncated,
        "text": body,
    }

    if args.grep:
        try:
            excerpts = grep_excerpts(text, args.grep)
        except ValueError as exc:
            fail_args(str(exc))
        payload["excerpts"] = excerpts
        payload["matched"] = len(excerpts)
        payload["text"] = ""
        payload["truncated"] = False

    emit(payload, body=text, out=args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
