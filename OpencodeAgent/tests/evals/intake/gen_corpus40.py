"""Deterministically build the 40-item intake corpus, in two label versions.

WHY: the frozen ten items anchor the baseline contract; the other thirty add
coverage of the four routing classes and the eight product labels without
hand-writing prompts. Each generated prompt is a template rendered from a slot
vocabulary with a fixed seed, so the output is reproducible byte for byte and
``head -10`` stays identical to ``corpus.jsonl``.

Two label versions exist because Wave 0 and Wave 1 evidence was produced
before the c19/c22 private-product labels were corrected:

- ``v1`` is frozen historical evidence, byte-identical to
  ``git show 32f5e595~1:OpencodeAgent/tests/evals/intake/corpus40.jsonl``.
  ``corpus40.jsonl`` must keep that exact content.
- ``v2`` carries the corrected ``c19``/``c22`` labels, their rationales and
  a ``labels_version: "v2"`` marker on those two records. It is written to
  ``corpus40_v2.jsonl`` and is used only by the bounded probe and the A/B
  corpus. Every other line is byte-identical to v1.

Prompts and ids are identical across the two files; only the two labels,
their rationales and the marker differ. ``FAMILIES`` keeps the corrected
labels so ``gen_corpus_ab.py`` (which imports it for its generated slice)
stays stable; v1 is reconstructed through ``V1_EXPECT_OVERRIDES``.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
FROZEN_PATH = BASE_DIR / "corpus.jsonl"
OUTPUT_PATH = BASE_DIR / "corpus40.jsonl"
V2_OUTPUT_PATH = BASE_DIR / "corpus40_v2.jsonl"
SEED = 20261027
SOURCE = "extended_generated"
GENERATOR = f"corpus40 v1 seed={SEED}"
FROZEN_COUNT = 10
LABELS_VERSIONS = ("v1", "v2")
# Family positions retain the pre-32f5e595 label in v1 (private-product
# families 3=组合体检 and 5=交易复盘 generated an execute label in Wave 0).
V1_EXPECT_OVERRIDES = {3: "execute_with_defaults", 5: "execute_with_defaults"}

TICKERS = ("600519", "000858", "300750", "601318", "600036", "000333")
MARKETS = ("A股", "沪深300", "中证500", "创业板", "科创板", "港股", "美股")
STYLES = ("价值", "成长", "动量", "红利", "低波动", "质量")
HORIZONS = ("近一周", "近一月", "近三个月", "近半年", "近一年", "近三年")
SECTORS = ("新能源", "半导体", "医药", "消费", "军工", "银行")
INDICATORS = ("PE", "PB", "ROE", "毛利率", "营收增速", "自由现金流")
EVENTS = ("美联储加息", "地缘冲突", "俄乌局势", "美联储降息", "国内政策调整")
CONCEPTS = ("ETF", "市盈率", "股票分红", "北向资金", "可转债", "集合竞价")
PREDICTION_TIMES = ("下周", "明天")

RATIONALE = {
    "execute_with_defaults": "模板生成的{product}请求：存在可声明默认值，应直接产出产物而非反问。",
    "clarify_private": "模板生成的{product}请求：场景明确但缺成本价/仓位等私有槽位。",
    "direct_answer": "模板生成的概念或预测负例：直接回答且不产生任何参数。",
    "refuse": "模板生成的能力边界请求：应明确拒绝且不产出参数。",
}

# (expect, product, open_ended, count, templates, slot vocabularies)
FAMILIES: tuple[tuple[str, str, bool, int, tuple[str, ...], dict[str, tuple[str, ...]]], ...] = (
    (
        "execute_with_defaults", "个股体检", False, 3,
        ("帮我看看 {ticker} 最近的走势和估值", "诊断一下 {ticker} 这只票的基本面", "{ticker} 现在能不能买，帮我做个个股体检"),
        {"ticker": TICKERS},
    ),
    (
        "execute_with_defaults", "选股清单", True, 3,
        ("帮我筛选一份 {market}{style}风格的股票池", "{sector}板块最近有什么机会，给我一份候选清单", "按 {indicator} 从高到低排个序，推荐几只{market}标的"),
        {"market": MARKETS, "style": STYLES, "sector": SECTORS, "indicator": INDICATORS},
    ),
    (
        "execute_with_defaults", "策略回测", False, 2,
        ("回测一个{style}策略，标的用{market}，区间{horizon}", "帮我用{indicator}做个选股策略并跑一下回测"),
        {"style": STYLES, "market": MARKETS, "horizon": HORIZONS, "indicator": INDICATORS},
    ),
    (
        # Private holdings: label corrected after Wave 1 observed the mislabel.
        "clarify_private", "组合体检", False, 1,
        ("看看我的账户组合{horizon}表现怎么样",),
        {"horizon": HORIZONS},
    ),
    (
        "execute_with_defaults", "事件/宏观影响", True, 2,
        ("{event}对{market}的{sector}板块会有什么影响", "宏观上{event}会怎么影响{sector}的走势"),
        {"event": EVENTS, "market": MARKETS, "sector": SECTORS},
    ),
    (
        # Private trade history: label corrected after Wave 1 observed the mislabel.
        "clarify_private", "交易复盘", False, 1,
        ("帮我复盘一下{horizon}的交易记录，看看盈亏",),
        {"horizon": HORIZONS},
    ),
    (
        "execute_with_defaults", "定时监控", False, 2,
        ("每天盘后帮我监控{sector}板块的信号并提醒我", "设置一个定时任务，每周一早上给我发{market}监控周报"),
        {"sector": SECTORS, "market": MARKETS},
    ),
    (
        "execute_with_defaults", "报告", False, 1,
        ("生成一份{market}{style}{horizon}的分析报告",),
        {"market": MARKETS, "style": STYLES, "horizon": HORIZONS},
    ),
    (
        "clarify_private", "个股体检", False, 2,
        ("我的{ticker}这只票亏了15%，该怎么办", "我持有的{sector}ETF跌了，帮我看看这只ETF的估值和成本位置"),
        {"ticker": TICKERS, "sector": SECTORS},
    ),
    (
        "clarify_private", "组合体检", False, 2,
        ("我的组合里有{sector}和{market}，现在该怎么调整", "帮我看看我的持仓组合，最近跌得有点多"),
        {"sector": SECTORS, "market": MARKETS},
    ),
    (
        "direct_answer", "none", False, 6,
        ("什么是{concept}", "什么是{concept}", "什么是{concept}", "什么是{concept}", "{when}{market}会涨吗", "{when}{market}能涨吗"),
        {"concept": CONCEPTS, "when": PREDICTION_TIMES, "market": MARKETS},
    ),
    (
        "refuse", "none", False, 5,
        ("帮我把银行卡里的钱转到券商", "帮我下单一手{ticker}", "能帮我开通炒股权限吗", "帮我设定一个保证收益20%的理财方案", "帮我把全部存款买入{sector}ETF"),
        {"ticker": TICKERS, "sector": SECTORS},
    ),
)


def _render(rng: random.Random, template: str, slots: dict[str, tuple[str, ...]]) -> str:
    values = {name: rng.choice(pool) for name, pool in slots.items()}
    return template.format(**{name: values[name] for name in values if "{" + name + "}" in template})


def generate_items(labels: str = "v1") -> list[dict[str, Any]]:
    if labels not in LABELS_VERSIONS:
        raise SystemExit(f"labels must be one of {LABELS_VERSIONS}, got {labels!r}")
    rng = random.Random(SEED)
    generated: list[dict[str, Any]] = []
    for family_index, (family_expect, product, open_ended, count, templates, slots) in enumerate(FAMILIES):
        expect = V1_EXPECT_OVERRIDES.get(family_index, family_expect) if labels == "v1" else family_expect
        for index in range(count):
            prompt = _render(rng, templates[index % len(templates)], slots)
            item = {
                "id": f"c{FROZEN_COUNT + len(generated) + 1:02d}",
                "prompt": prompt,
                "expect": expect,
                "negative": expect == "direct_answer",
                "open_ended": open_ended,
                "expected_product": product,
                "rationale": RATIONALE[expect].format(product=product if product != "none" else "无产物"),
                "source": SOURCE,
                "generator": GENERATOR,
            }
            if labels == "v2" and family_index in V1_EXPECT_OVERRIDES:
                item["labels_version"] = labels
            generated.append(item)
    return generated


def render_corpus(labels: str = "v1") -> str:
    frozen_lines = FROZEN_PATH.read_bytes().splitlines(keepends=True)
    if len(frozen_lines) != FROZEN_COUNT:
        raise SystemExit(f"{FROZEN_PATH} must hold exactly {FROZEN_COUNT} lines, found {len(frozen_lines)}")
    generated = [json.dumps(item, ensure_ascii=False) + "\n" for item in generate_items(labels)]
    return b"".join(frozen_lines + [line.encode("utf-8") for line in generated]).decode("utf-8")


def output_path(labels: str) -> Path:
    return OUTPUT_PATH if labels == "v1" else V2_OUTPUT_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", choices=LABELS_VERSIONS, default="v1", help="label version to render (default: v1)")
    parser.add_argument("--out", type=Path, help="output path (default: corpus40.jsonl for v1, corpus40_v2.jsonl for v2)")
    parser.add_argument("--check", action="store_true", help="verify the output file matches regeneration")
    args = parser.parse_args(argv)
    target = args.out or output_path(args.labels)
    rendered = render_corpus(args.labels)
    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != rendered:
            print(f"{target} is stale; rerun gen_corpus40.py --labels {args.labels}", file=sys.stderr)
            return 1
        print(f"{target}: {len(rendered.splitlines())} items, labels={args.labels}, regeneration matches")
        return 0
    target.write_text(rendered, encoding="utf-8")
    print(f"wrote {target}: {len(rendered.splitlines())} items, labels={args.labels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
