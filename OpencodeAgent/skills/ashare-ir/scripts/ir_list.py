#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from nanofetch import assert_host, clip, emit, fetch, grep_excerpts, html_to_text, unavailable  # noqa: E402,F401

from bs4 import BeautifulSoup  # noqa: E402
from nanofetch.envelope import fail_args  # noqa: E402

SOURCE = "sina"
MAX_PAGES = 12
VALID_TYPES = ("research", "earnings_call", "site_visit", "other")

LIST_PAGE_ONE = (
    "http://money.finance.sina.com.cn/corp/go.php/vCB_AllBulletin/"
    "stockid/{stockid}.phtml?ftype=lsgg"
)
LIST_PAGE_NEXT = (
    "http://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletin.php"
    "?stockid={stockid}&Page={page}&ftype=lsgg"
)
DETAIL_URL = (
    "http://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php"
    "?stockid={stockid}&id={bulletin_id}"
)

IR_TITLE_KEYWORDS = (
    "投资者关系活动记录",
    "投资者关系管理信息",
    "业绩说明会",
    "投资者说明会",
    "调研活动信息表",
    "投资者开放日",
    "网上业绩说明会",
    "电话会议纪要",
    "投资者关系活动",
)

TYPE_KEYWORDS = {
    "earnings_call": (
        "业绩说明会", "业绩交流会", "电话会议", "网上业绩",
        "业绩交流", "投资者说明会", "业绩发布",
    ),
    "site_visit": ("实地调研", "现场参观", "参观", "现场考察"),
    "research": (
        "调研", "机构投资者", "投资者关系活动记录",
        "投资者关系管理信息", "调研活动信息表",
        "投资者关系活动", "投资者开放日",
    ),
}

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_CODE = re.compile(r"^\d{6}$")
_SUFFIX = re.compile(r"\.(SH|SZ|BJ)$", re.IGNORECASE)


def _fail(message: str) -> NoReturn:
    fail_args(message)


def _reason(exc: Exception) -> str:
    message = str(exc).strip()
    return (message.splitlines()[0] if message else exc.__class__.__name__)[:180]


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
        _fail(f"--code must be a 6-digit A-share code, got {raw!r}")
    return stockid, suffix.group(1) if suffix else _infer_exchange(stockid)


def _day(raw: str, flag: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        _fail(f"{flag} must be YYYY-MM-DD, got {raw!r}")


def _types(raw: str) -> list[str]:
    selected = [item.strip() for item in raw.split(",") if item.strip()]
    if not selected:
        return list(VALID_TYPES)
    unknown = sorted(set(selected) - set(VALID_TYPES))
    if unknown:
        _fail(f"--types accepts {','.join(VALID_TYPES)}; got {','.join(unknown)}")
    return selected


def _classify(title: str) -> str:
    for meeting_type in ("earnings_call", "site_visit", "research"):
        if any(word in title for word in TYPE_KEYWORDS[meeting_type]):
            return meeting_type
    return "other"


def _parse_page(html: str, stockid: str) -> tuple[list[dict], str, int]:
    soup = BeautifulSoup(html, "html.parser")
    datelist = soup.find("div", class_="datelist")
    if datelist is None:
        raise RuntimeError("unexpected page shape: div.datelist missing")

    anchors = datelist.find_all("a", href=re.compile(r"vCB_AllBulletinDetail"))
    entries: list[dict] = []
    seen: set[str] = set()
    for anchor in anchors:
        match = re.search(r"[?&]id=(\d+)", anchor.get("href", ""))
        if match is None:
            continue
        bulletin_id = match.group(1)
        if bulletin_id in seen:
            continue
        title = anchor.get_text(strip=True)
        if not title or not any(word in title for word in IR_TITLE_KEYWORDS):
            continue
        seen.add(bulletin_id)

        meeting_date = ""
        previous = anchor.previous_sibling
        if isinstance(previous, str):
            date_match = _DATE_RE.search(previous)
            if date_match:
                meeting_date = date_match.group(1)

        entries.append(
            {
                "meeting_date": meeting_date,
                "meeting_type": _classify(title),
                "participants": [],
                "title": title,
                "summary": "",
                "source_url": DETAIL_URL.format(
                    stockid=stockid, bulletin_id=bulletin_id
                ),
            }
        )

    dates = _DATE_RE.findall(datelist.get_text(" ", strip=True))
    oldest = min(dates) if dates else ""
    return entries, oldest, len(anchors)


def _page_url(stockid: str, page: int) -> str:
    if page == 1:
        return LIST_PAGE_ONE.format(stockid=stockid)
    return LIST_PAGE_NEXT.format(stockid=stockid, page=page)


def _collect(stockid: str, start: str, end: str) -> list[dict]:
    meetings: list[dict] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        html = fetch(_page_url(stockid, page), encoding="gbk")
        try:
            entries, oldest, item_count = _parse_page(html, stockid)
        except RuntimeError:
            if page == 1:
                html = fetch(_page_url(stockid, page), encoding="gbk")
                entries, oldest, item_count = _parse_page(html, stockid)
            else:
                raise
        if item_count == 0:
            break
        for entry in entries:
            url = entry["source_url"]
            if url in seen:
                continue
            seen.add(url)
            day = entry["meeting_date"]
            if day and (day < start or day > end):
                continue
            meetings.append(entry)
        if oldest and oldest < start:
            break
        if "下一页" not in html:
            break
    return meetings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List Sina Finance IR meetings and survey records for an A-share stock."
    )
    parser.add_argument("--code", required=True, help="300750 or 300750.SZ")
    parser.add_argument("--start", default="", help="inclusive start date, YYYY-MM-DD")
    parser.add_argument("--end", default="", help="inclusive end date, YYYY-MM-DD")
    parser.add_argument(
        "--types",
        default="research,earnings_call,site_visit,other",
        help="comma-separated meeting types",
    )
    parser.add_argument("--limit", type=int, default=50, help="max meetings to return")
    args = parser.parse_args(argv)

    stockid, exchange = _split_code(args.code)
    ts_code = f"{stockid}.{exchange}"
    end_day = _day(args.end, "--end") if args.end else date.today()
    start_day = _day(args.start, "--start") if args.start else end_day - timedelta(days=182)
    if start_day > end_day:
        _fail("--start must not be later than --end")
    wanted_types = _types(args.types)
    limit = max(1, min(200, args.limit))

    try:
        meetings = _collect(stockid, start_day.isoformat(), end_day.isoformat())
    except Exception as exc:
        emit(unavailable(SOURCE, _reason(exc)))
        return 0

    meetings = [item for item in meetings if item["meeting_type"] in wanted_types]
    meetings.sort(key=lambda item: item["meeting_date"], reverse=True)
    selected = meetings[:limit]
    emit(
        {
            "available": True,
            "source": SOURCE,
            "ts_code": ts_code,
            "count": len(selected),
            "meetings": selected,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
