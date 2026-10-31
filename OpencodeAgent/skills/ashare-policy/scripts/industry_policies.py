#!/usr/bin/env python3
"""行业政策检索 —— gov.cn 政策文件库站内搜索（无 API key）。

实测可用端点（2026-10-31）：

    https://sousuo.www.gov.cn/search-gov/data
        ?t=zhengcelibrary&q=<关键词>&p=1&n=30
        &sort=pubtime&sortType=1&searchfield=title|空&type=gwyzcwjk

- ``searchfield=title`` 为标题检索，留空为全文检索；
- 结果位于 ``searchVO.catMap.<gongwen|bumenfile>.listVO``，日期字段 ``pubtimeStr``；
- 该搜索对空格分词做 OR 匹配，所以不使用原百炼版的「行业 + 产业政策 / 行业规范」
  组合词（会把只含“产业政策”的文件带进来），改为「标题检索 + 全文检索」，
  并用连续行业名对标题与摘要做一次过滤。

原则：拿不到或解析不了就返回 unavailable + exit 0；不伪造结果、不换镜像站。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from nanofetch import clip, emit, fetch, html_to_text, unavailable

ENDPOINT = "https://sousuo.www.gov.cn/search-gov/data"
SOURCE = "gov.cn"
MAX_LIMIT = 5
PAGE_SIZE = 30
SUMMARY_CHARS = 240

# 只取政策文件库中的“国务院文件”和“部门文件”两类；gongbao 与其重复，
# otherfile 是新闻/解读，不属于政策文件本身。
CATEGORIES = ("gongwen", "bumenfile")
PASSES = (("title", 2), ("", 1))  # 标题命中优先于全文命中

_TAGS = re.compile(r"<[^>]+>")

# 与原百炼版一致的 gov.cn 子域 -> (机构名, level) 映射。
_DOMAIN_MAP: dict[str, tuple[str, str]] = {
    "ndrc.gov.cn": ("国家发展改革委", "ministry"),
    "miit.gov.cn": ("工业和信息化部", "ministry"),
    "mof.gov.cn": ("财政部", "ministry"),
    "mee.gov.cn": ("生态环境部", "ministry"),
    "samr.gov.cn": ("国家市场监督管理总局", "ministry"),
    "csrc.gov.cn": ("中国证监会", "ministry"),
    "nfra.gov.cn": ("国家金融监督管理总局", "ministry"),
    "cbirc.gov.cn": ("国家金融监督管理总局", "ministry"),  # 旧域名，已合并到 nfra
    "pboc.gov.cn": ("中国人民银行", "central"),
    "safe.gov.cn": ("国家外汇管理局", "ministry"),
    "stats.gov.cn": ("国家统计局", "ministry"),
    "customs.gov.cn": ("海关总署", "ministry"),
    "moa.gov.cn": ("农业农村部", "ministry"),
    "mot.gov.cn": ("交通运输部", "ministry"),
    "mohurd.gov.cn": ("住房和城乡建设部", "ministry"),
    "nea.gov.cn": ("国家能源局", "ministry"),
    "nrb.gov.cn": ("国家铁路局", "ministry"),
}

_CENTRAL_PREFIXES = (
    "国务院",
    "中共中央",
    "全国人民代表大会",
    "全国人大",
    "全国政协",
    "中央军委",
    "最高人民法院",
    "最高人民检察院",
)


def _fail_args(message: str) -> None:
    """参数非法：stderr + 退出码 2，不输出 JSON。"""
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def _plain(raw: str | None) -> str:
    """去掉检索高亮标签并压平空白，且不在标签边界插入空格。"""
    return " ".join(html_to_text(_TAGS.sub("", raw or "")).split())


def _issuer_level(url: str, puborg: str | None) -> tuple[str, str]:
    """优先用文件自带的发文机关，缺失时回落到 URL 域名映射。"""
    issuer = " ".join((puborg or "").split())
    if issuer:
        level = "central" if issuer.startswith(_CENTRAL_PREFIXES) else "ministry"
        return issuer, level

    host = (urllib.parse.urlparse(url).hostname or "").lower()
    for suffix, (name, level) in _DOMAIN_MAP.items():
        if host == suffix or host.endswith("." + suffix):
            return name, level
    if host == "www.gov.cn":
        return "中国政府网", "central"
    if host.endswith(".gov.cn"):
        return "地方政府", "local"
    return "未知机构", "local"


def _publish_date(item: dict) -> date | None:
    raw = (item.get("pubtimeStr") or "").strip()
    for fmt in ("%Y.%m.%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _allowed_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host == "gov.cn" or host.endswith(".gov.cn")


def _search(industry: str, searchfield: str) -> list[dict]:
    params = {
        "t": "zhengcelibrary",
        "q": industry,
        "p": 1,
        "n": PAGE_SIZE,
        "sort": "pubtime",
        "sortType": 1,
        "searchfield": searchfield,
        "type": "gwyzcwjk",
        "timetype": "timeqb",
        "mintime": "",
        "maxtime": "",
    }
    payload = json.loads(fetch(ENDPOINT + "?" + urllib.parse.urlencode(params)))
    if payload.get("code") != 200:
        raise ValueError(f"gov.cn search returned code={payload.get('code')!r}")

    cat_map = (payload.get("searchVO") or {}).get("catMap") or {}
    items: list[dict] = []
    for category in CATEGORIES:
        block = cat_map.get(category) or {}
        items.extend(block.get("listVO") or [])
    return items


def _collect(industry: str, days: int, limit: int) -> list[dict] | None:
    """返回政策列表；两条检索均失败时返回 None（源不可用）。"""
    cutoff = date.today() - timedelta(days=days)
    best: dict[str, tuple[int, date, dict]] = {}
    failures = 0

    for searchfield, priority in PASSES:
        try:
            rows = _search(industry, searchfield)
        except Exception:  # noqa: BLE001
            # CLI 边界：传输/解析失败统一降级为 unavailable
            failures += 1
            continue

        for item in rows:
            url = (item.get("url") or "").strip()
            if not url or not _allowed_url(url):
                continue
            published = _publish_date(item)
            if published is None or published < cutoff:
                continue
            # 该搜索按空格分词做 OR 匹配；要求行业名连续出现在标题或摘要中，
            # 滤掉只命中单个词根的无关文件（如「汽车零部件」只命中「汽车」）。
            haystack = f"{_plain(item.get('title'))} {_plain(item.get('summary'))}"
            if industry not in haystack:
                continue
            current = best.get(url)
            if current is None or priority > current[0]:
                best[url] = (priority, published, item)

    if failures == len(PASSES):
        return None

    ordered = sorted(best.values(), key=lambda row: (row[0], row[1]), reverse=True)
    policies: list[dict] = []
    for _, published, item in ordered[:limit]:
        issuer, level = _issuer_level(item.get("url", ""), item.get("puborg"))
        summary, clipped = clip(_plain(item.get("summary")), SUMMARY_CHARS)
        policies.append(
            {
                "publish_date": published.isoformat(),
                "title": _plain(item.get("title")),
                "issuer": issuer,
                "level": level,
                "source_url": item.get("url", "").strip(),
                "summary": summary + ("…" if clipped else ""),
            }
        )
    return policies


def _parse_args(argv: list[str]) -> tuple[str, int, int]:
    parser = argparse.ArgumentParser(
        prog="industry_policies",
        description="检索 gov.cn 政策文件库中的中央部委行业政策（无 API key）。",
    )
    parser.add_argument(
        "--industry", required=True, help="行业名，如 光伏、新能源、汽车零部件"
    )
    parser.add_argument("--days", type=int, default=365, help="回溯天数，默认 365")
    parser.add_argument(
        "--limit", type=int, default=MAX_LIMIT, help="返回条数，默认 5，最多 5"
    )
    args = parser.parse_args(argv)

    industry = args.industry.strip()
    if not industry:
        _fail_args("--industry 不能为空")
    if args.days < 1:
        _fail_args("--days 必须为正整数")
    if args.limit < 1:
        _fail_args("--limit 必须为正整数")
    return industry, args.days, min(args.limit, MAX_LIMIT)


def main(argv: list[str]) -> int:
    industry, days, limit = _parse_args(argv)
    policies = _collect(industry, days, limit)

    if policies is None:
        emit(
            unavailable(
                SOURCE,
                "gov_search_unavailable",
                industry=industry,
                window_days=days,
                endpoint=ENDPOINT,
            )
        )
        return 0

    payload: dict = {
        "available": True,
        "source": SOURCE,
        "industry": industry,
        "window_days": days,
        "count": len(policies),
        "policies": policies,
        "endpoint": ENDPOINT,
    }
    if not policies:
        payload["coverage_note"] = (
            f"近 {days} 天 gov.cn 政策文件库未检索到与「{industry}」相关的政策文件；"
            "gov.cn 仅收录中央部委公开发布文件，不含地方政策与征求意见稿，"
            "可放宽 --days 或改用 VT web_search。"
        )
    emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
