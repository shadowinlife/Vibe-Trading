"""Deterministic scorers and lazy dataset loaders for the finance-QA adapters.

Companion module to ``finance_qa_adapter.py`` (todo 5): the grading protocol
and the benchmark citations are documented there; this module holds the pure
scoring functions plus the lazy, offline dataset loading so the adapter file
stays focused on the ``HarnessAdapter`` wiring.

Datasets are cached by ``scripts/fetch_finance_qa.py`` under
``.venv-eval/data/`` (gitignored, never committed). Loading failures raise
``FinanceQADataUnavailable``; the adapter converts that into a degraded skip
marker, never a crash.
"""

from __future__ import annotations

import csv
import json
import os
import random
import re
from pathlib import Path
from typing import Any

PKG_DIR = Path(__file__).resolve().parents[1]
SUBSET_ARTIFACT_PATH = PKG_DIR / "artifacts" / "fineval_subset.json"
FINANCEBENCH_FILE = Path("financebench") / "financebench_merged.jsonl"
FINEVAL_SPLITS = ("dev", "val")  # answerable pool; test labels are withheld
_NUMERIC_TOLERANCE = 0.01  # 1% relative tolerance for rounded figures


class FinanceQADataUnavailable(RuntimeError):
    """Dataset cache missing/corrupt under the data dir (run the fetch script)."""


def default_data_dir() -> Path:
    """Dataset cache root: $HARNESS_BENCH_DATA_DIR or <repo>/.venv-eval/data."""
    override = os.environ.get("HARNESS_BENCH_DATA_DIR", "").strip()
    return Path(override) if override else PKG_DIR.parents[3] / ".venv-eval" / "data"


# --------------------------------------------------------------------------- #
# Deterministic scorers (official-grading proxy; protocol in the adapter doc)
# --------------------------------------------------------------------------- #


def normalize_answer(text: str) -> str:
    """Casefold, drop currency/comma separators, collapse whitespace."""
    cleaned = re.sub(r"[$\u00a5\uffe5,]", "", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip().casefold().rstrip(".")


def extract_number(text: str) -> float | None:
    """Last number in the text (digit-grouping commas allowed), else None."""
    matches = re.findall(r"-?\d[\d,]*(?:\.\d+)?", str(text or ""))
    if not matches:
        return None
    try:
        return float(matches[-1].replace(",", ""))
    except ValueError:
        return None


def grade_financebench(prediction: str, gold: str) -> float:
    """Deterministic open-book grade of one FinanceBench answer (0.0/1.0)."""
    pred, gold_norm = normalize_answer(prediction), normalize_answer(gold)
    if not pred or not gold_norm:
        return 0.0
    if pred == gold_norm:
        return 1.0
    gold_tokens = gold_norm.split()
    if gold_tokens[0] in ("yes", "no"):
        # The binary verdict leads the gold answer; the prediction must lead
        # the same way (the yes/no judgement is the core of such answers).
        return 1.0 if pred.split()[0] == gold_tokens[0] else 0.0
    if len(gold_tokens) <= 6:  # short gold: numeric comparison is meaningful
        gold_value, pred_value = extract_number(gold_norm), extract_number(pred)
        if gold_value is not None and pred_value is not None:
            if abs(pred_value - gold_value) <= max(
                _NUMERIC_TOLERANCE * abs(gold_value), 1e-9
            ):
                return 1.0
    return 0.0


def extract_mcq_option(text: str) -> str | None:
    """Extract the chosen A/B/C/D option from messy model output, else None."""
    raw = str(text or "")
    patterns = (
        r"(?:答案|正确答案|参考答案|answer|选项)\s*(?:是|为|应该是)?\s*[:：]?\s*([A-Da-d])",
        r"([A-Da-d])\s*(?:选项|是正确答案|为正确答案)",
        r"[（(\[【]\s*([A-Da-d])\s*[)）\]】]",
    )
    for pattern in patterns:
        matches = re.findall(pattern, raw, flags=re.IGNORECASE)
        if matches:
            return matches[-1].upper()
    stripped = raw.strip().strip("\"'`。.!！")
    if len(stripped) == 1 and stripped.upper() in "ABCD":
        return stripped.upper()
    standalone = re.findall(r"(?<![A-Za-z0-9])([A-Da-d])(?![A-Za-z0-9])", raw)
    return standalone[-1].upper() if standalone else None


def grade_fineval(prediction: str, gold_option: str) -> float:
    """MCQ accuracy grade: extracted option vs ground truth (0.0/1.0)."""
    option = extract_mcq_option(prediction)
    return 1.0 if option is not None and option == gold_option.upper() else 0.0


def extract_final_answer(text: str, marker: str) -> str:
    """Answer after the LAST occurrence of ``marker``; else last non-empty line."""
    raw = str(text or "")
    index = raw.casefold().rfind(marker.casefold())
    if index >= 0:
        return raw[index + len(marker) :].strip().strip("\"'`")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def draw_subset(question_ids: list[str], n: int, seed: int) -> list[str]:
    """The parity-spec subset draw: ``random.Random(seed).sample`` (recorded)."""
    return random.Random(seed).sample(list(question_ids), n)


# --------------------------------------------------------------------------- #
# Lazy dataset loaders (opencode path only; test seams)
# --------------------------------------------------------------------------- #


def load_financebench_records(data_dir: Path) -> list[dict[str, Any]]:
    """The 150 public FinanceBench cases from the local cache."""
    path = Path(data_dir) / FINANCEBENCH_FILE
    if not path.is_file():
        raise FinanceQADataUnavailable(f"missing {path}")
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            records.append(
                {
                    "id": str(row["financebench_id"]),
                    "question": str(row["question"]),
                    "answer": str(row["answer"]),
                    "company": str(row.get("company", "")),
                    "doc_name": str(row.get("doc_name", "")),
                }
            )
    if len(records) != 150:
        raise FinanceQADataUnavailable(f"{path} has {len(records)} rows, want 150")
    return records


def load_fineval_records(data_dir: Path) -> dict[str, dict[str, Any]]:
    """Answerable FinEval questions keyed '<split>:<subject>:<id>' (dev+val)."""
    base = Path(data_dir) / "fineval"
    if not base.is_dir():
        raise FinanceQADataUnavailable(f"missing {base}")
    records: dict[str, dict[str, Any]] = {}
    for split in FINEVAL_SPLITS:
        for csv_path in sorted((base / split).glob("*.csv")):
            subject = csv_path.stem.removesuffix(f"_{split}")
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    if row.get("answer"):
                        key = f"{split}:{subject}:{row['id']}"
                        records[key] = {
                            "id": key,
                            "question": row["question"],
                            **{opt: row[opt] for opt in "ABCD"},
                            "answer": row["answer"].strip().upper(),
                        }
    if not records:
        raise FinanceQADataUnavailable(f"no answerable FinEval rows under {base}")
    return records


def load_fineval_subset_ids() -> list[str]:
    """The committed subset (n=500, seed 20260823) in artifact order."""
    artifact = json.loads(SUBSET_ARTIFACT_PATH.read_text(encoding="utf-8"))
    return [str(qid) for qid in artifact["question_ids"]]
