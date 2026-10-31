#!/usr/bin/env python3
"""A 股监管处罚 / 违规处理（来源：新浪财经 vGP_GetOutOfLine）。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from bs4 import BeautifulSoup
from nanofetch import assert_host, clip, emit, fetch, grep_excerpts, html_to_text, unavailable
from nanofetch.envelope import fail_args

_PAGE_URL = (
    "https://vip.stock.finance.sina.com.cn/corp/go.php/vGP_GetOutOfLine/stockid/{stockid}.phtml"
)

_DATE_FIELD = re.compile(r"公告日期[:：]\s*(\d{4}-\d{2}-\d{2})")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CODE = re.compile(r"^\d{6}$")
_SUFFIX = re.compile(r"\.(SH|SZ|BJ)$", re.IGNORECASE)

_ISSUER_RULES: tuple[tuple[str, str], ...] = (
    ("上海证券交易所", "上交所"),
    ("深圳证券交易所", "深交所"),
    ("北京证券交易所", "北交所"),
)
_PROVINCE = re.compile(
    r"(北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|"
    r"山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|内蒙古|广西|"
    r"西藏|宁夏|新疆)(?:监管局)?"
)

_FIELDS = {
    "标题": "title",
    "批复原因": "reason",
    "批复内容": "content",
}


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


def _issuer(raw: str) -> str:
    for keyword, abbr in _ISSUER_RULES:
        if keyword in raw:
            return abbr
    if "证券监督管理委员会" in raw:
        found = _PROVINCE.search(raw)
        return f"{found.group(1)}证监局" if found else "证监会"
    return raw


def _record(header: str, source_url: str) -> dict[str, str]:
    record = dict.fromkeys(
        ("punish_date", "event_type", "title", "reason", "content", "issuer", "source_url"),
        "",
    )
    found = _DATE_FIELD.search(header)
    record["punish_date"] = found.group(1) if found else ""
    record["event_type"] = header.split("公告日期")[0].strip()
    record["source_url"] = source_url
    return record


def _parse(html: str, source_url: str) -> list[dict[str, str]] | None:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="collectFund_1")
    if table is None:
        return None

    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for node in table.descendants:
        if node.name == "th" and node.get("colspan") == "2":
            if current and (current["punish_date"] or current["title"]):
                records.append(current)
            current = _record(node.get_text(" ", strip=True), source_url)
            continue
        if node.name != "tr" or current is None:
            continue
        cells = node.find_all("td", recursive=False)
        if len(cells) != 2:
            continue
        label = cells[0].find("strong")
        if label is None:
            continue
        key = _FIELDS.get(label.get_text(strip=True))
        if key:
            current[key] = cells[1].get_text(" ", strip=True)
        elif label.get_text(strip=True) == "处理人":
            current["issuer"] = _issuer(cells[1].get_text(" ", strip=True))

    if current and (current["punish_date"] or current["title"]):
        records.append(current)
    return records


def _in_range(record: dict[str, str], start: str, end: str) -> bool:
    punish_date = record["punish_date"]
    if not punish_date:
        return True
    if start and punish_date < start:
        return False
    return not (end and punish_date > end)


def main() -> int:
    parser = argparse.ArgumentParser(description="A 股监管处罚记录（新浪财经）")
    parser.add_argument("--code", required=True, help="6 位代码，可带 .SH/.SZ 后缀")
    parser.add_argument("--start", help="起始日期 YYYY-MM-DD，默认不限")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD，默认不限")
    args = parser.parse_args()

    stockid, exchange = _split_code(args.code)
    ts_code = f"{stockid}.{exchange}"
    start = _check_date(args.start, "--start") if args.start else ""
    end = _check_date(args.end, "--end") if args.end else ""
    if start and end and start > end:
        fail_args(f"--start {start} 晚于 --end {end}")

    page_url = _PAGE_URL.format(stockid=stockid)
    try:
        html = fetch(page_url, encoding="gbk")
    except RuntimeError as exc:  # nanofetch.HttpError：抓取失败
        emit(unavailable("sina", f"penalty page fetch failed: {type(exc).__name__}", ts_code=ts_code))
        return 0

    records = _parse(html, page_url)
    if records is None:
        emit(unavailable("sina", "unexpected penalty page shape", ts_code=ts_code))
        return 0

    penalties = [record for record in records if _in_range(record, start, end)]

    emit(
        {
            "available": True,
            "source": "sina",
            "ts_code": ts_code,
            "count": len(penalties),
            "penalties": penalties,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
