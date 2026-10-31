#!/usr/bin/env python3
"""Fetch one A-share periodic report (annual/semi/q1/q3) from Sina Finance."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from nanofetch import assert_host, clip, emit, fetch, grep_excerpts, html_to_text, unavailable

from nanofetch import HttpError
from nanofetch.envelope import fail_args

from bs4 import BeautifulSoup

_BASE = "https://vip.stock.finance.sina.com.cn"
_LIST_TPL = "/corp/go.php/{view}/stockid/{stockid}/page_type/{page_type}.phtml"
_DETAIL_TPL = "/corp/view/vCB_AllBulletinDetail.php?stockid={stockid}&id={id}"
_HOSTS = ("vip.stock.finance.sina.com.cn",)

# view/page_type are fixed by the navigation links on Sina's own report pages.
_VIEWS = {
    "annual": ("vCB_Bulletin", "ndbg"),
    "semi": ("vCB_BulletinZhong", "zqbg"),
    "q1": ("vCB_BulletinYi", "yjdbg"),
    "q3": ("vCB_BulletinSan", "sjdbg"),
}
_LABELS = {"annual": "年报", "semi": "半年报", "q1": "一季报", "q3": "三季报"}
_TITLE_PATTERNS = {
    "annual": r"(年度报告|年报)",
    "semi": r"(半年度报告|半年报|中期报告|中报)",
    "q1": r"(第一季度报告|一季度报告|一季报)",
    "q3": r"(第三季度报告|三季度报告|三季报)",
}

_CODE = re.compile(r"(\d{6})(?:\.(?:SZ|SH))?", re.IGNORECASE)
_REPORT_ID = re.compile(r"[?&]id=(\d+)")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_MIN_BODY_CHARS = 200


def _stockid(raw: str) -> str:
    match = _CODE.fullmatch(raw.strip())
    if match is None:
        fail_args(f"--code 必须是 6 位数字（可带 .SZ/.SH 后缀），收到 {raw!r}")
    return match.group(1)


def _listing_url(stockid: str, report_type: str) -> str:
    view, page_type = _VIEWS[report_type]
    return _BASE + _LIST_TPL.format(view=view, stockid=stockid, page_type=page_type)


def _detail_url(stockid: str, report_id: str) -> str:
    return _BASE + _DETAIL_TPL.format(stockid=stockid, id=report_id)


def _publish_date(anchor) -> str:
    # Sina puts the date in the text node before the link, not in the <li>.
    node = anchor.previous_sibling
    for _ in range(4):
        if node is None:
            break
        text = node if isinstance(node, str) else node.get_text(" ", strip=True)
        match = _DATE.search(text)
        if match:
            return match.group(1)
        node = node.previous_sibling
    return ""


def _parse_listing(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=lambda href: href and "vCB_AllBulletinDetail" in href):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        match = _REPORT_ID.search(href)
        if match is None:
            continue
        report_id = match.group(1)
        if report_id in seen:
            continue
        seen.add(report_id)
        entries.append({
            "id": report_id,
            "title": anchor.get_text(" ", strip=True),
            "date": _publish_date(anchor),
        })
    return entries


def _select_report(entries: list[dict[str, str]], year: int, report_type: str) -> dict[str, str] | None:
    title_pattern = re.compile(_TITLE_PATTERNS[report_type])
    year_pattern = re.compile(rf"(?<!\d){year}\s*年")
    for entry in entries:
        title = entry["title"]
        if "英文" in title or "摘要" in title:
            continue
        if title_pattern.search(title) and year_pattern.search(title):
            return entry
    return None


def _detail_text(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find(id="con02-7") or soup.find(id="allbulletin")
    if container is None:
        return None
    text = html_to_text(str(container))
    return text if len(text) >= _MIN_BODY_CHARS else None


def _run(args: argparse.Namespace) -> int:
    stockid = _stockid(args.code)
    if not 1900 <= args.year <= 2100:
        fail_args("--year 必须是 1900 到 2100 之间的整数")
    if args.max_chars <= 0:
        fail_args("--max-chars 必须为正整数")
    if args.grep:
        try:
            re.compile(args.grep)
        except re.error as exc:
            fail_args(f"--grep 不是合法正则: {exc}")

    label = _LABELS[args.report_type]
    listing_url = _listing_url(stockid, args.report_type)
    try:
        assert_host(listing_url, _HOSTS)
        listing_html = fetch(listing_url, encoding="gbk")
    except (HttpError, OSError, ValueError):
        emit(unavailable("sina", "新浪定期报告列表页不可达"))
        return 0

    entry = _select_report(_parse_listing(listing_html), args.year, args.report_type)
    if entry is None:
        emit(unavailable("sina", f"列表页可达但未找到 {args.year} 年的{label}"))
        return 0

    url = _detail_url(stockid, entry["id"])
    try:
        assert_host(url, _HOSTS)
        detail_html = fetch(url, encoding="gbk")
    except (HttpError, OSError, ValueError):
        emit(unavailable("sina", "新浪定期报告详情页不可达"))
        return 0

    text = _detail_text(detail_html)
    if text is None:
        emit(unavailable("sina", "详情页不是可解析的定期报告正文"))
        return 0

    body, truncated = clip(text, args.max_chars)
    payload: dict[str, object] = {
        "available": True,
        "source": "sina",
        "title": entry["title"],
        "publish_date": entry["date"],
        "url": url,
        "chars": len(text),
        "truncated": truncated,
        "text": body,
    }
    if args.grep:
        excerpts = grep_excerpts(text, args.grep)
        payload["excerpts"] = excerpts
        payload["matched"] = len(excerpts)
        payload["text"] = ""
        payload["truncated"] = False
    emit(payload, body=text, out=args.out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓取 A 股定期报告全文（新浪财经）")
    parser.add_argument("--code", required=True, help="6 位股票代码，可带 .SZ/.SH 后缀")
    parser.add_argument("--year", required=True, type=int, help="报告所属年份")
    parser.add_argument(
        "--report-type",
        default="annual",
        choices=list(_VIEWS),
        help="annual=年报，semi=半年报，q1=一季报，q3=三季报",
    )
    parser.add_argument("--grep", help="在全文检索的关键词或正则；返回 offset 与上下文")
    parser.add_argument("--max-chars", type=int, default=20000, help="text 字段的最大字数")
    parser.add_argument("--out", help="把全文写入该文件")
    return _run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
