#!/usr/bin/env python3
"""A 股临时公告列表（来源：新浪财经 vCB_AllBulletin）。"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from bs4 import BeautifulSoup
from nanofetch import assert_host, clip, emit, fetch, grep_excerpts, html_to_text, unavailable
from nanofetch.envelope import fail_args
from nanofetch.http import HostNotAllowed

_LIST_PAGE = (
    "http://money.finance.sina.com.cn/corp/go.php/vCB_AllBulletin/stockid/{stockid}.phtml"
)
_DETAIL_URL = (
    "http://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php"
    "?stockid={stockid}&id={bulletin_id}"
)
_LIST_HOSTS = ("money.finance.sina.com.cn", "vip.stock.finance.sina.com.cn")
_MAX_PAGES = 40

# 顺序有意义：litigation 必须前置于 penalty，否则 "诉讼立案" 会被 "立案" 吃掉。
_ANN_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("inquiry", ("问询函", "监管工作函", "问询回复", "关注函", "问询函回复")),
    (
        "audit",
        (
            "审计报告",
            "审计意见",
            "关键审计事项",
            "非标准审计",
            "保留意见",
            "无法表示意见",
            "鉴证报告",
        ),
    ),
    ("accountant_change", ("会计师事务所变更", "更换会计师", "续聘会计师", "聘请会计师")),
    ("litigation", ("诉讼", "仲裁", "法律纠纷")),
    (
        "penalty",
        (
            "行政处罚",
            "纪律处分",
            "监管处罚",
            "监管措施",
            "立案调查",
            "立案告知",
            "被立案",
            "警示函",
            "通报批评",
            "告知书",
        ),
    ),
    (
        "restatement",
        ("差错更正", "财报重述", "追溯调整", "前期会计差错", "前期差错更正", "前期财务"),
    ),
)
_ANN_TYPES = tuple(rule[0] for rule in _ANN_TYPE_RULES) + ("other",)

_DATE_IN_TEXT = re.compile(r"(\d{4}-\d{2}-\d{2})")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BULLETIN_HREF = re.compile(r"vCB_AllBulletinDetail")
_BULLETIN_ID = re.compile(r"[?&]id=(\d+)")
_CODE = re.compile(r"^\d{6}$")
_SUFFIX = re.compile(r"\.(SH|SZ|BJ)$", re.IGNORECASE)


def _infer_exchange(stockid: str) -> str:
    if stockid[0] in "69":
        return "SH"
    if stockid[0] in "023":
        return "SZ"
    if stockid[0] in "48":
        return "BJ"
    return "SH"


def _split_code(raw: str) -> tuple[str, str]:
    text = raw.strip().upper()
    suffix = _SUFFIX.search(text)
    stockid = text[: suffix.start()] if suffix else text
    if not _CODE.fullmatch(stockid):
        fail_args(f"--code 必须是 6 位数字（可带 .SH/.SZ 后缀），收到: {raw!r}")
    return stockid, suffix.group(1) if suffix else _infer_exchange(stockid)


def _check_date(value: str, flag: str) -> str:
    if not _ISO_DATE.fullmatch(value):
        fail_args(f"{flag} 格式应为 YYYY-MM-DD，收到: {value!r}")
    return value


def _classify(title: str) -> str:
    for ann_type, keywords in _ANN_TYPE_RULES:
        if any(keyword in title for keyword in keywords):
            return ann_type
    return "other"


def _ann_date(anchor) -> str:
    node = anchor.previous_sibling
    while node is not None:
        raw = node if isinstance(node, str) else node.get_text(" ", strip=True)
        found = _DATE_IN_TEXT.search(raw or "")
        if found:
            return found.group(1)
        node = node.previous_sibling
    found = _DATE_IN_TEXT.search(anchor.parent.get_text(" ", strip=True))
    return found.group(1) if found else ""


def _parse_list(html: str, stockid: str) -> list[dict[str, str]] | None:
    soup = BeautifulSoup(html, "html.parser")
    datelist = soup.find("div", class_="datelist")
    if datelist is None:
        return None

    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in datelist.find_all("a", href=_BULLETIN_HREF):
        id_match = _BULLETIN_ID.search(anchor.get("href", ""))
        if id_match is None:
            continue
        bulletin_id = id_match.group(1)
        if bulletin_id in seen:
            continue
        seen.add(bulletin_id)
        title = anchor.get_text(strip=True)
        entries.append(
            {
                "ann_date": _ann_date(anchor),
                "title": title,
                "ann_type": _classify(title),
                "source_url": _DETAIL_URL.format(stockid=stockid, bulletin_id=bulletin_id),
            }
        )
    return entries


def _next_page(html: str, page_url: str, stockid: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a"):
        if "下一页" not in anchor.get_text():
            continue
        href = urllib.parse.urljoin(page_url, anchor.get("href", "").strip())
        try:
            assert_host(href, _LIST_HOSTS)
        except HostNotAllowed:
            return None
        params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        if params.get("stockid", [""])[0] != stockid:
            return None
        return href
    return None


def _collect(
    stockid: str,
    start: str,
    end: str,
    types: tuple[str, ...],
    limit: int,
) -> list[dict[str, str]] | None:
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    page_url = _LIST_PAGE.format(stockid=stockid)

    for page in range(_MAX_PAGES):
        html = fetch(page_url, encoding="gbk")
        page_entries = _parse_list(html, stockid)
        if page_entries is None:
            if page == 0:
                return None
            break

        fresh = 0
        stop = False
        for entry in page_entries:
            if entry["source_url"] in seen:
                continue
            seen.add(entry["source_url"])
            fresh += 1
            ann_date = entry["ann_date"]
            if end and ann_date and ann_date > end:
                continue
            if start and ann_date and ann_date < start:
                stop = True
                break
            if types and entry["ann_type"] not in types:
                continue
            entries.append(entry)
            if len(entries) >= limit:
                stop = True
                break

        if stop or not fresh:
            break
        next_url = _next_page(html, page_url, stockid)
        if next_url is None:
            break
        page_url = next_url

    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="A 股临时公告列表（新浪财经）")
    parser.add_argument("--code", required=True, help="6 位代码，可带 .SH/.SZ 后缀")
    parser.add_argument("--start", help="起始日期 YYYY-MM-DD，默认当年 1 月 1 日")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--types", help="逗号分隔：" + ",".join(_ANN_TYPES))
    parser.add_argument("--limit", type=int, default=50, help="最多返回条数，默认 50")
    args = parser.parse_args()

    stockid, exchange = _split_code(args.code)
    ts_code = f"{stockid}.{exchange}"

    today = date.today().isoformat()
    start = _check_date(args.start, "--start") if args.start else f"{today[:4]}-01-01"
    end = _check_date(args.end, "--end") if args.end else today
    if start > end:
        fail_args(f"--start {start} 晚于 --end {end}")

    if args.limit < 1:
        fail_args(f"--limit 必须为正整数，收到: {args.limit}")

    types: tuple[str, ...] = ()
    if args.types:
        requested = tuple(part.strip() for part in args.types.split(",") if part.strip())
        unknown = [name for name in requested if name not in _ANN_TYPES]
        if unknown:
            fail_args(f"未知 --types: {','.join(unknown)}；合法值: {','.join(_ANN_TYPES)}")
        types = requested

    try:
        entries = _collect(stockid, start, end, types, args.limit)
    except RuntimeError as exc:  # nanofetch.HttpError：抓取失败
        emit(unavailable("sina", f"list page fetch failed: {type(exc).__name__}", ts_code=ts_code))
        return 0

    if entries is None:
        emit(unavailable("sina", "unexpected list page shape", ts_code=ts_code))
        return 0

    emit(
        {
            "available": True,
            "source": "sina",
            "ts_code": ts_code,
            "count": len(entries),
            "announcements": entries,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
