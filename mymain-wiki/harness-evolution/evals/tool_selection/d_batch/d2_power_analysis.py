"""D2-A0 power analysis: required N for the twin-arbitration W-gate to be closable.

Frozen gate (D2_PLAN §3.1): paired W re-run passes iff the CI lower bound of
(within - full) top-1 hit rate exceeds -10pp. The D batch failed this gate on
power, not on effect: quant N=80 gave CI [-12.0, +4.1] with only 5 discordant
pairs (b/c=1/4); webdocs N=32 gave CI [-25.8, +20.0] with 1 discordant pair.

This script simulates the paired 2x2 cell process (both-correct / within-only /
full-only / neither) at a grid of N and reports P(gate passes) per scenario.
Two CI methods are compared:

- ``unpaired``: the verdict's exact_ci_difference (Newcombe over two
  independent Wilson intervals) — the frozen D-batch method, conservative on
  paired data;
- ``paired``: Newcombe method 10 for paired proportions (uses the discordant
  counts) — a candidate protocol amendment that MUST be frozen before any A3
  data collection if adopted.

No LLM calls, no network. Output: artifacts/d2/power_analysis.md.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from d_batch_report import exact_ci_difference  # noqa: E402

REPORT_PATH = (
    Path(__file__).resolve().parents[1] / "artifacts" / "d2" / "power_analysis.md"
)
MARGIN = -0.10  # frozen non-inferiority margin (D2_PLAN §2.4)
TRIALS = 20000
N_GRID = [80, 120, 160, 200, 240, 320, 400, 500, 640, 800]
Z = 1.959964


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + Z * Z / n
    center = (p + Z * Z / (2 * n)) / denom
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / denom
    return center - half, center + half


def paired_ci_lower(both: int, b: int, c: int, n: int) -> float:
    """Newcombe #10 paired CI lower bound for p_within - p_full.

    both = both arms correct, b = within-only correct, c = full-only correct.
    Margins: p_within = (both + b) / n, p_full = (both + c) / n.
    """
    if n == 0:
        return 0.0
    w_hits = both + b
    f_hits = both + c
    lo_w, _ = wilson(w_hits, n)
    _, hi_f = wilson(f_hits, n)
    d = w_hits / n - f_hits / n
    return d - math.sqrt((w_hits / n - lo_w) ** 2 + (hi_f - f_hits / n) ** 2)


class CellModel:
    """Paired 2x2 cell probabilities: (both, within_only, full_only, neither)."""

    def __init__(
        self, both: float, within_only: float, full_only: float, neither: float
    ):
        total = both + within_only + full_only + neither
        assert abs(total - 1.0) < 1e-9, f"cells must sum to 1, got {total}"
        self.cells = (both, within_only, full_only, neither)

    @property
    def delta(self) -> float:
        return self.cells[1] - self.cells[2]

    @property
    def discordance(self) -> float:
        return self.cells[1] + self.cells[2]


# quant scenarios: D-batch observed cells (73/1/4/2 over 80) as the null;
# fix scenarios shrink within-only discordance toward parity.
QUANT = {
    "Q0 null (v2句无效, 维持观测)": CellModel(0.9125, 0.0125, 0.05, 0.025),
    "Q1 部分有效 (Δ=0, 不和谐2%)": CellModel(0.93, 0.01, 0.01, 0.05),
    "Q2 有效 (Δ=0, 不和谐1%)": CellModel(0.955, 0.005, 0.005, 0.035),
}
# webdocs scenarios assume A2 repairs the D19 label confound first (the
# observed 56-59% base is a corpus property, verdict §3.4): repaired base ~75%.
WEBDOCS = {
    "W0 null (修复语料后仍 Δ=−2pp)": CellModel(0.72, 0.005, 0.025, 0.25),
    "W1 有效 (Δ=0, 不和谐1%)": CellModel(0.75, 0.005, 0.005, 0.24),
}


def simulate(model: CellModel, n: int, trials: int, seed: int) -> tuple[float, float]:
    """P(gate passes) under (unpaired-Newcombe, paired-Newcombe) CI methods."""
    import random

    rng = random.Random(seed)
    cells = model.cells
    cum = (cells[0], cells[0] + cells[1], cells[0] + cells[1] + cells[2], 1.0)
    pass_unpaired = pass_paired = 0
    for _ in range(trials):
        both = within_only = full_only = 0
        for _ in range(n):
            u = rng.random()
            if u < cum[0]:
                both += 1
            elif u < cum[1]:
                within_only += 1
            elif u < cum[2]:
                full_only += 1
        w_hits = both + within_only
        f_hits = both + full_only
        _, lo_u, _ = exact_ci_difference(w_hits, n, f_hits, n)
        lo_p = paired_ci_lower(both, within_only, full_only, n)
        pass_unpaired += lo_u > MARGIN
        pass_paired += lo_p > MARGIN
    return pass_unpaired / trials, pass_paired / trials


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--trials", type=int, default=TRIALS)
    parser.add_argument("--seed", type=int, default=20260829)
    args = parser.parse_args()

    lines = [
        "# D2-A0 功效分析：孪生仲裁 W 门禁闭合所需样本量",
        "",
        "门禁（冻结）：配对 W 复测的 within−full top-1 命中率差 CI 下界 > −10pp。",
        "D 批失败在功效而非效应：quant N=80 仅 5 对不和谐（CI [−12.0,+4.1]），",
        "webdocs N=32 仅 1 对不和谐（CI [−25.8,+20.0]）。本分析回答：A3 复测",
        "语料需要多大，门禁才**有机会**闭合（或诚实失败）。",
        "",
        f"方法：配对 2x2 单元格蒙特卡洛（{args.trials} 试验/格子，种子 {args.seed}）。",
        "CI 双方法对照：`unpaired` = D 批裁决沿用的 Newcombe 独立比例法（保守）；",
        "`paired` = Newcombe #10 配对法（利用不和谐对计数）。**若采用 paired 法，",
        "必须在 A3 采集前作为协议修订冻结（见文末裁决建议）。**",
        "",
        "## quant 域（观测基线：full 96.3% / within 92.5%，不和谐 6.25%）",
        "",
        "| 场景 | 真值 Δ | 不和谐率 | N | P(门禁过, unpaired) | P(门禁过, paired) |",
        "|---|---|---|---|---|---|",
    ]

    recommendations: dict[str, int] = {}
    for domain, scenarios in (("quant", QUANT), ("webdocs", WEBDOCS)):
        if domain == "webdocs":
            lines += [
                "",
                "## webdocs 域（假设 A2 已修复 D19 标注混淆，基线修复至 ~75%）",
                "",
                "| 场景 | 真值 Δ | 不和谐率 | N | P(门禁过, unpaired) | P(门禁过, paired) |",
                "|---|---|---|---|---|---|",
            ]
        for name, model in scenarios.items():
            for n in N_GRID:
                p_u, p_p = simulate(model, n, args.trials, args.seed)
                lines.append(
                    f"| {name} | {model.delta:+.2%} | {model.discordance:.2%} "
                    f"| {n} | {p_u:.1%} | {p_p:.1%} |"
                )
                key = f"{domain}|{name}"
                if key not in recommendations and p_p >= 0.8 and p_u >= 0.8:
                    recommendations[key] = n

    lines += [
        "",
        "## 裁决建议（A3 采集前冻结）",
        "",
        "**本分析的关键发现：非劣门禁认证的是「亏损在界内」，不是「仲裁句有效」。**",
        "观测点估计（quant −3.75pp / webdocs −3.1pp）本身已在 −10pp 界内，因此",
        "N 足够大时，门禁在「仲裁句完全无效」的零假设下也会闭合——门禁真正裁决",
        "的问题是：子代理化的真实亏损是否劣于 −10pp。这正是生产放行需要回答的",
        "问题（D2-2/D2-3 的解锁条件），因此门禁设计不变。",
        "",
        "若需单独认证「v2 仲裁句有效」，须另设 v1-prompt vs v2-prompt 双臂对照",
        "（测量 Δ(prompt) 而非 Δ(面)）——**本批不做**，记录为可选项。",
        "",
        "- 语料规模按零假设下 ≥80% 概率正确判读（过/不过都判得对）的最小 N 定稿；",
        "- paired CI 法作为候选修订附后：**默认维持 unpaired Newcombe**（与 D 批",
        "  裁决口径连续），除非判官成本迫使改用 paired 法——改用须在采集前冻结；",
        "",
        "各场景达 80% 双方法通过概率的最小 N：",
        "",
        "| 域 | 场景 | 最小 N |",
        "|---|---|---|",
    ]
    for key, n in recommendations.items():
        domain, name = key.split("|", 1)
        lines.append(f"| {domain} | {name} | {n} |")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"power analysis written: {args.report}")
    for key, n in recommendations.items():
        print(f"  {key}: min N = {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
