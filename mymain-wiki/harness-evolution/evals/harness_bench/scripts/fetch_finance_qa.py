"""Fetch FinanceBench-150 and FinEval into the eval-only dataset cache.

Downloads BOTH todo-5 datasets through the HuggingFace mirror into
``.venv-eval/data/`` (gitignored; datasets are never committed), verifies row
counts, and writes ``.venv-eval/data/manifest.json`` with a sha256 per file.

Endpoint decision (todo-1 preflight): ``huggingface.co`` is unreachable from
this machine, so the default endpoint is ``https://hf-mirror.com``; set
``HF_ENDPOINT`` to override. If the endpoint is unreachable the script exits 1
with a message naming ``HF_ENDPOINT`` -- it never fakes success.

Layout written under ``.venv-eval/data/``::

    financebench/financebench_merged.jsonl   (150 rows, verified)
    fineval/{dev,val,test}/<subject>.csv     (4,661 rows total: 170 dev +
                                              1,151 val + 3,340 test; the
                                              test split carries no answers)
    manifest.json

``--write-fineval-subset PATH`` additionally derives the parity-spec subset
(``subsets.fineval``: n=500, sampling_seed=20260823) with
``random.Random(20260823).sample`` over the ANSWERABLE pool (dev+val; the
dataset authors withhold test labels) in canonical order (splits dev,val;
subjects sorted by filename; rows in file order; id '<split>:<subject>:<id>')
and writes the committed artifact JSON.

Idempotent: existing files are re-hashed and re-verified, not re-downloaded.
All network access is bounded (per-request timeout x retries). Stdlib only.

Usage (from the repo root)::

    python agent/src/evals/harness_bench/scripts/fetch_finance_qa.py \
        [--data-dir .venv-eval/data] \
        [--write-fineval-subset agent/src/evals/harness_bench/artifacts/fineval_subset.json]

Exit codes: 0 ok, 1 download/verification failure (degraded, named), 2 usage.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import json
import os
import random
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_ENDPOINT = "https://hf-mirror.com"
REQUEST_TIMEOUT_S = 60
MAX_ATTEMPTS = 3
FINANCEBENCH_PATH = (
    "datasets/PatronusAI/financebench/resolve/main/financebench_merged.jsonl"
)
FINEVAL_PATH = "datasets/SUFE-AIFLM-Lab/FinEval/resolve/main/FinEval.zip"
EXPECTED_FINANCEBENCH_ROWS = 150
EXPECTED_FINEVAL_ROWS = 4661  # dataset README: 4,661 questions, 34 subjects
FINEVAL_SUBSET_N = 500
FINEVAL_SUBSET_SEED = 20260823
FINEVAL_ANSWERABLE_SPLITS = ("dev", "val")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: Path) -> None:
    """Bounded download: per-attempt socket timeout, MAX_ATTEMPTS with backoff."""
    if dest.is_file() and dest.stat().st_size > 0:
        print(f"[fetch] cached: {dest.name} ({dest.stat().st_size} bytes)")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_S) as response:
                payload = response.read()
            if not payload:
                raise OSError("empty response body")
            dest.write_bytes(payload)
            print(f"[fetch] downloaded: {dest.name} ({len(payload)} bytes)")
            return
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"[fetch] attempt {attempt}/{MAX_ATTEMPTS} failed: {last_error}")
    raise RuntimeError(
        f"cannot download {url} after {MAX_ATTEMPTS} attempts ({last_error}). "
        "The HuggingFace mirror is unreachable; check the HF_ENDPOINT "
        f"environment variable (current default: {DEFAULT_ENDPOINT}). "
        "huggingface.co itself is unreachable from this machine by "
        "preflight decision, so HF_ENDPOINT must name a working mirror."
    )


def _count_jsonl(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _extract_fineval(zip_path: Path, target_dir: Path) -> dict[str, int]:
    """Safely extract the split CSVs; return per-split row counts."""
    counts = {split: 0 for split in ("dev", "val", "test")}
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            parts = Path(member.filename).parts
            if member.is_dir() or len(parts) != 2 or parts[0] not in counts:
                continue
            if not member.filename.endswith(".csv"):
                continue
            out_path = target_dir / parts[0] / parts[1]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(archive.read(member))
            with out_path.open("r", encoding="utf-8-sig", newline="") as handle:
                counts[parts[0]] += sum(1 for _ in csv.DictReader(handle))
    return counts


def _answerable_pool(fineval_dir: Path) -> list[str]:
    """Canonical question-id pool: dev+val, subjects sorted, rows in order."""
    pool: list[str] = []
    for split in FINEVAL_ANSWERABLE_SPLITS:
        for csv_path in sorted((fineval_dir / split).glob("*.csv")):
            subject = csv_path.stem.removesuffix(f"_{split}")
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    if row.get("answer"):
                        pool.append(f"{split}:{subject}:{row['id']}")
    return pool


def write_fineval_subset(data_dir: Path, artifact_path: Path, zip_sha: str) -> dict:
    pool = _answerable_pool(data_dir / "fineval")
    if len(pool) < FINEVAL_SUBSET_N:
        raise RuntimeError(
            f"answerable FinEval pool has {len(pool)} questions, "
            f"need >= {FINEVAL_SUBSET_N}"
        )
    question_ids = random.Random(FINEVAL_SUBSET_SEED).sample(pool, FINEVAL_SUBSET_N)
    artifact = {
        "artifact": "fineval_subset",
        "benchmark": "fineval",
        "source_dataset": "SUFE-AIFLM-Lab/FinEval",
        "source_paper": "arXiv:2308.09975",
        "n": FINEVAL_SUBSET_N,
        "sampling_seed": FINEVAL_SUBSET_SEED,
        "draw_method": (
            "random.Random(20260823).sample(pool, 500); pool = every ANSWERABLE "
            "FinEval question (dev+val splits; the dataset authors withhold "
            "test-split labels, so test rows cannot be graded offline), in "
            "canonical order: splits ('dev','val'), subjects sorted by CSV "
            "filename, rows in file order, question id '<split>:<subject>:<id "
            "column>'. Redrawing with the same seed and pool reproduces the "
            "identical list."
        ),
        "answerable_pool_size": len(pool),
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "source_files_sha256": {"FinEval.zip": zip_sha},
        "question_ids": question_ids,
    }
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(
        f"[subset] wrote {artifact_path} (n={FINEVAL_SUBSET_N}, "
        f"seed={FINEVAL_SUBSET_SEED}, pool={len(pool)})"
    )
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir",
        default=".venv-eval/data",
        help="dataset cache directory (default: .venv-eval/data)",
    )
    parser.add_argument(
        "--write-fineval-subset",
        metavar="PATH",
        default=None,
        help="also derive the committed FinEval subset artifact at PATH",
    )
    args = parser.parse_args(argv)
    endpoint = os.environ.get("HF_ENDPOINT", "").strip() or DEFAULT_ENDPOINT
    data_dir = Path(args.data_dir)
    try:
        fb_path = data_dir / "financebench" / "financebench_merged.jsonl"
        _download(f"{endpoint}/{FINANCEBENCH_PATH}", fb_path)
        zip_path = data_dir / "fineval" / "FinEval.zip"
        _download(f"{endpoint}/{FINEVAL_PATH}", zip_path)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    fb_rows = _count_jsonl(fb_path)
    if fb_rows != EXPECTED_FINANCEBENCH_ROWS:
        print(
            f"error: FinanceBench row count {fb_rows} != "
            f"{EXPECTED_FINANCEBENCH_ROWS}",
            file=sys.stderr,
        )
        return 1
    fineval_counts = _extract_fineval(zip_path, data_dir / "fineval")
    fineval_total = sum(fineval_counts.values())
    if fineval_total != EXPECTED_FINEVAL_ROWS:
        print(
            f"error: FinEval row count {fineval_total} != "
            f"{EXPECTED_FINEVAL_ROWS} (splits: {fineval_counts})",
            file=sys.stderr,
        )
        return 1

    zip_sha = _sha256(zip_path)
    manifest = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "endpoint": endpoint,
        "endpoint_note": (
            "huggingface.co is unreachable from this machine (preflight "
            "decision); HF_ENDPOINT names the mirror actually used"
        ),
        "files": {
            "financebench_merged.jsonl": {
                "path": str(fb_path),
                "sha256": _sha256(fb_path),
                "bytes": fb_path.stat().st_size,
                "rows": fb_rows,
                "source": "PatronusAI/financebench",
            },
            "FinEval.zip": {
                "path": str(zip_path),
                "sha256": zip_sha,
                "bytes": zip_path.stat().st_size,
                "rows": fineval_total,
                "rows_per_split": fineval_counts,
                "source": "SUFE-AIFLM-Lab/FinEval",
            },
        },
        "notes": (
            "FinEval's README documents 4,661 questions; the test split "
            "(3,340 rows) carries no answer column, so offline grading uses "
            "the dev+val pool (1,321 rows)."
        ),
    }
    manifest_path = data_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        f"[verify] financebench={fb_rows} rows; fineval={fineval_total} rows "
        f"{fineval_counts}; manifest={manifest_path}"
    )

    if args.write_fineval_subset:
        try:
            write_fineval_subset(data_dir, Path(args.write_fineval_subset), zip_sha)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
