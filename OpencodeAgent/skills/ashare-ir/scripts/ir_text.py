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

INLINE_PARTICIPANTS_RE = re.compile(
    r"(?:参会|参加|与会|接待|来访)[^：:\n]{0,12}[：:]\s*([^\n]{2,220})"
)
PARTICIPANTS_HEADER_RE = re.compile(
    r"^(?:参与单位|参会单位|参与机构|参会机构|参会人员|参与人员|接待对象|来访单位)"
    r"[^\n：:]{0,8}$"
)
STOP_LABELS = {
    "时间", "地点", "形式", "会议形式", "召开方式",
    "上市公司接待人员姓名", "接待人员姓名", "投资者关系活动主要内容介绍",
}
ORG_SPLITTER_RE = re.compile(r"[、，,；;]+")
ORG_KEYWORDS = (
    "证券", "资本", "基金", "银行", "资产", "投资", "保险", "信托",
    "公司", "研究所", "研究院", "财富", "期货", "私募", "家族办公室",
)
ORG_BLOCKLIST = (
    "其他", "详见", "附件", "见下", "名单", "等机构", "以上", "不限于",
    "投资者", "等人员", "共计",
)


def _reason(exc: Exception) -> str:
    message = str(exc).strip()
    return (message.splitlines()[0] if message else exc.__class__.__name__)[:180]


def _extract(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for container_id in ("content", "con02-7", "box"):
        node = soup.find(id=container_id)
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
    if not text or any(marker in text[:120] for marker in NOT_FOUND_MARKERS):
        raise RuntimeError("announcement page returned no body")
    return text


def _add_participants(raw: str, found: list[str]) -> None:
    for part in ORG_SPLITTER_RE.split(raw):
        name = part.strip(" .。")
        if not 2 <= len(name) <= 30:
            continue
        if any(bad in name for bad in ORG_BLOCKLIST):
            continue
        if not any(good in name for good in ORG_KEYWORDS):
            continue
        if name not in found:
            found.append(name)


def _participants(text: str) -> list[str]:
    found: list[str] = []
    lines = [line.strip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        if PARTICIPANTS_HEADER_RE.match(line):
            for following in lines[index + 1:index + 3]:
                if not following:
                    continue
                if following in STOP_LABELS:
                    break
                _add_participants(following, found)
                break
        for match in INLINE_PARTICIPANTS_RE.finditer(line):
            _add_participants(match.group(1), found)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch one Sina Finance IR meeting record and extract its attendees."
    )
    parser.add_argument("--url", required=True, help="source_url from ir_list.py")
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
        "participants": _participants(text),
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
