"""Corpus contract shared by the intake eval driver and its offline tests.

WHY: the frozen 10-item corpus is the baseline contract and must stay exactly
10 items, while Wave 0's 40-item corpus reuses the same field contract at a
different size. Keeping the rules here lets ``run.py`` stay inside the 400-line
practical cap and gives both call sites one implementation.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

EXPECT_LABELS = ("direct_answer", "execute_with_defaults", "clarify_private", "refuse")
PRODUCT_LABELS = "个股体检 选股清单 策略回测 组合体检 事件/宏观影响 交易复盘 定时监控 报告 none".split()
REQUIRED_CORPUS_FIELDS = ("id", "prompt", "expect", "negative", "open_ended", "expected_product", "rationale")
FROZEN_CORPUS_SIZE = 10
MIN_CUSTOM_CORPUS_SIZE = 10


def validate_corpus(items: Iterable[dict[str, Any]], expected: dict[str, dict[str, Any]] | None = None, *, frozen: bool = False) -> list[str]:
    """Validate a corpus under the frozen (exactly 10) or custom (>=10) rules.

    ``frozen=True`` is reserved for the default ``corpus.jsonl`` path; custom
    corpora such as ``corpus40.jsonl`` use the same field contract with a
    lower bound instead of an exact size. ``expected`` is only meaningful for
    the frozen corpus, whose ids are enumerated in ``expected.json``.
    """
    items = list(items)
    errors: list[str] = []
    if frozen:
        if len(items) != FROZEN_CORPUS_SIZE:
            errors.append(f"frozen corpus must contain exactly {FROZEN_CORPUS_SIZE} items, found {len(items)}")
    elif len(items) < MIN_CUSTOM_CORPUS_SIZE:
        errors.append(f"custom corpus must contain at least {MIN_CUSTOM_CORPUS_SIZE} items, found {len(items)}")
    seen: set[str] = set()
    for index, item in enumerate(items, 1):
        label = str(item.get("id") or f"line {index}")
        errors.extend(f"{label}: missing or empty required field {key!r}" for key in REQUIRED_CORPUS_FIELDS if key not in item or item.get(key) is None or (isinstance(item.get(key), str) and not item[key].strip()))
        item_id = item.get("id")
        if isinstance(item_id, str) and not re.fullmatch(r"c\d{2}", item_id):
            errors.append(f"{label}: id must match cNN")
        if item_id in seen:
            errors.append(f"{label}: duplicate id")
        seen.add(item_id)
        if item.get("expect") not in EXPECT_LABELS:
            errors.append(f"{label}: expect must be one of {EXPECT_LABELS}")
        if item.get("expected_product") not in PRODUCT_LABELS:
            errors.append(f"{label}: expected_product must be one of the 9 labels")
        errors.extend(f"{label}: {flag} must be a boolean" for flag in ("negative", "open_ended") if not isinstance(item.get(flag), bool))
    negatives = [item for item in items if item.get("negative") is True]
    if len(negatives) < 2:
        errors.append(f"at least 2 negative items required, found {len(negatives)}")
    errors.extend(f"{item.get('id', '?')}: negative item must be direct_answer/none" for item in negatives if (item.get("expect"), item.get("expected_product")) != ("direct_answer", "none"))
    if expected is None:
        return errors
    item_ids = {item.get("id") for item in items}
    if missing := sorted(item_ids - set(expected)):
        errors.append(f"expected.json is missing ids: {missing}")
    if extra := sorted(set(expected) - item_ids):
        errors.append(f"expected.json has unknown ids: {extra}")
    for item in items:
        detail = expected.get(item.get("id")) or {}
        for key in ("expect", "expected_product", "negative"):
            if detail.get(key) != item.get(key):
                errors.append(f"{item.get('id')}: expected.json disagrees on {key}")
        if detail.get("max_user_turns") != 2:
            errors.append(f"{item.get('id')}: max_user_turns must be 2")
        markers = detail.get("required_markers") or []
        if not all(isinstance(marker, str) and marker for marker in markers):
            errors.append(f"{item.get('id')}: required_markers must be a non-empty string list")
    return errors
