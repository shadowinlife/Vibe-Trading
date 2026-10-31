#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from nanofetch import assert_host, clip, emit, fetch, grep_excerpts, html_to_text, unavailable  # noqa: E402,F401

from bs4 import BeautifulSoup  # noqa: E402
from nanofetch.envelope import fail_args  # noqa: E402

SOURCE = "sina"
BASE = "https://stock.finance.sina.com.cn"
SEARCH_PATH = "/stock/go.php/vReport_List/kind/search/index.phtml"
COMPANY_PAGE = "https://finance.sina.com.cn/realstock/company/{sina_code}/nc.shtml"
NODES_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
    "/Market_Center.getHQNodes"
)
MAX_PAGES = 15

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
SW2_LINK_RE = re.compile(r"industry=(sw[23]_\d+)(?:&amp;|&)t1=(\d+)")
SW2_ANY_RE = re.compile(r"industry=(sw[23]_\d+)")
SW2_ONLY_RE = re.compile(r"sw[23]_\d{6}")


def _fail(message: str) -> NoReturn:
    fail_args(message)


def _reason(exc: Exception) -> str:
    message = str(exc).strip()
    return (message.splitlines()[0] if message else exc.__class__.__name__)[:180]


def _sina_code(raw: str) -> str:
    number, dot, market = raw.strip().partition(".")
    if not re.fullmatch(r"\d{6}", number):
        _fail(f"--code must be a 6-digit A-share code, got {raw!r}")
    if dot:
        market = market.upper()
        if market not in {"SH", "SZ", "BJ"}:
            _fail(f"--code suffix must be SH, SZ or BJ, got {raw!r}")
        return market.lower() + number
    first = number[0]
    if first in "569":
        return "sh" + number
    if first in "0123":
        return "sz" + number
    if first in "48":
        return "bj" + number
    _fail(f"cannot infer exchange from {raw!r}; pass a .SH/.SZ/.BJ suffix")


def _keywords(raw: str) -> list[str]:
    words: list[str] = []
    for item in raw.split(","):
        word = item.strip()
        if word and word not in words:
            words.append(word)
    return words


def _normalize_name(name: str) -> str:
    return re.sub(r"[ⅡⅢ]+$", "", name.strip()).replace(" ", "")


def _resolve_from_company(sina_code: str) -> tuple[str, str]:
    html = fetch(COMPANY_PAGE.format(sina_code=sina_code), encoding="gbk")
    match = SW2_LINK_RE.search(html)
    if match:
        return match.group(1), match.group(2)
    loose = SW2_ANY_RE.search(html)
    if loose is None:
        raise RuntimeError("unexpected page shape: no 申万行业 link on company page")
    tail = html[loose.end():loose.end() + 200]
    t1 = re.search(r"t1=(\d+)", tail)
    return loose.group(1), t1.group(1) if t1 else "33"


def _resolve_from_name(name: str) -> tuple[str, str]:
    if SW2_ONLY_RE.fullmatch(name):
        return name, name
    payload = fetch(NODES_URL, encoding="utf-8")
    try:
        nodes = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("unexpected response shape from industry index") from exc

    wanted = _normalize_name(name)
    matches: list[tuple[str, str]] = []

    def walk(node: object) -> None:
        if isinstance(node, list):
            if len(node) >= 3 and all(isinstance(item, str) for item in node[:3]):
                label, _, code = node[0], node[1], node[2]
                if code.startswith("sw2_") and _normalize_name(label) == wanted:
                    matches.append((label, code))
            for item in node:
                walk(item)

    walk(nodes)
    if not matches:
        _fail(f"unknown 申万二级行业: {name!r}")
    return matches[0]


def _report_url(sw_code: str, t1: str, page: int) -> str:
    base = f"{BASE}{SEARCH_PATH}?industry={sw_code}&t1={t1}"
    return base if page == 1 else f"{base}&p={page}"


def _absolute(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return BASE + href
    return urllib.parse.urljoin(BASE + "/", href)


def _parse_rows(html: str) -> tuple[list[dict], str]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    dates: list[str] = []
    seen: set[str] = set()
    for table_row in soup.find_all("tr"):
        anchor = table_row.find("a", href=re.compile(r"vReport_Show"))
        if anchor is None:
            continue
        href = anchor.get("href")
        if not isinstance(href, str) or not href:
            continue
        source_url = _absolute(href)
        if source_url in seen:
            continue
        title = str(anchor.get("title") or anchor.get_text(strip=True)).strip()
        if not title:
            continue
        seen.add(source_url)

        cells = [cell.get_text(" ", strip=True) for cell in table_row.find_all("td")]
        report_date = ""
        date_index = -1
        for index, cell in enumerate(cells):
            if DATE_RE.fullmatch(cell):
                report_date = cell
                date_index = index
                break
        if not report_date:
            found = DATE_RE.search(table_row.get_text(" ", strip=True))
            report_date = found.group(0) if found else ""
        publisher = ""
        if date_index >= 0:
            for cell in cells[date_index + 1:]:
                if cell:
                    publisher = cell
                    break

        rows.append(
            {
                "report_date": report_date,
                "publisher": publisher,
                "title": title,
                "industry_tags": [],
                "source_url": source_url,
                "summary": "",
            }
        )
        if report_date:
            dates.append(report_date)
    return rows, (min(dates) if dates else "")


def _tags(title: str, label: str, keywords: list[str]) -> list[str]:
    tags = [label] if label else []
    for word in keywords:
        if word in title and word not in tags:
            tags.append(word)
    return tags


def _collect(
    sw_code: str,
    t1: str,
    label: str,
    start: str,
    end: str,
    keywords: list[str],
    limit: int,
) -> list[dict]:
    reports: list[dict] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        html = fetch(_report_url(sw_code, t1, page), encoding="gbk")
        rows, oldest = _parse_rows(html)
        if not rows and page == 1:
            html = fetch(_report_url(sw_code, t1, page), encoding="gbk")
            rows, oldest = _parse_rows(html)
        if not rows:
            if page == 1 and "没有找到相关内容" not in html:
                raise RuntimeError("unexpected page shape: no report rows")
            break
        for row in rows:
            if row["source_url"] in seen:
                continue
            day = row["report_date"]
            if day and (day < start or day > end):
                continue
            if keywords and not any(word in row["title"] for word in keywords):
                continue
            seen.add(row["source_url"])
            row["industry_tags"] = _tags(row["title"], label, keywords)
            reports.append(row)
            if len(reports) >= limit:
                return reports
        if oldest and oldest < start:
            break
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List Sina Finance broker industry reports for a stock or industry."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--code", help="route via the stock's 申万二级行业, e.g. 300750.SZ")
    source.add_argument("--industry", help="申万二级行业名, e.g. 电池")
    parser.add_argument("--keywords", default="", help="comma list; any title match")
    parser.add_argument("--days", type=int, default=365, help="lookback days")
    parser.add_argument("--limit", type=int, default=50, help="max reports to return")
    args = parser.parse_args(argv)

    end_day = date.today()
    start_day = end_day - timedelta(days=max(1, args.days))
    limit = max(1, min(200, args.limit))
    keywords = _keywords(args.keywords)

    try:
        if args.code:
            sw_code, t1 = _resolve_from_company(_sina_code(args.code))
            label = sw_code
        else:
            label, sw_code = _resolve_from_name(args.industry.strip())
            t1 = "33"
        reports = _collect(
            sw_code,
            t1,
            label,
            start_day.isoformat(),
            end_day.isoformat(),
            keywords,
            limit,
        )
    except Exception as exc:
        emit(unavailable(SOURCE, _reason(exc)))
        return 0

    emit(
        {
            "available": True,
            "source": SOURCE,
            "industry_sw_l2": label,
            "count": len(reports),
            "reports": reports,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
