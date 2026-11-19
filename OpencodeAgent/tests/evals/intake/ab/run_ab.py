"""Drive the ``aNN`` A/B corpus with ``run.py``'s tested single-item runner.

WHY this wrapper exists: ``run.py`` is the frozen driver for the ``cNN``
corpora, and its custom-corpus validator requires ``cNN`` ids plus at least two
negative items. The A/B corpus uses ``aNN`` ids and open-type items only, so it
cannot pass that validator even though every item still carries the ``expect`` /
``negative`` / ``expected_product`` fields ``run.py`` needs at event-build time.

This module reuses ``run.py`` unchanged: its parser, ``_run_one`` (timeout
recovery included), ``append_records`` and event builder. Only corpus validation
and the output default are replaced. ``--dry-run`` is fully offline; a real run
costs one model session per corpus item, exactly like ``run.py``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
INTAKE_DIR = BASE_DIR.parent
DEFAULT_CORPUS = BASE_DIR / "corpus_ab.jsonl"
AB_REQUIRED = (
    "id",
    "prompt",
    "expect",
    "negative",
    "open_ended",
    "expected_product",
    "expected_artifact",
    "rationale",
)
AB_SOURCES = ("frozen", "extended_generated", "real_label_guided")


def load_run_module():
    spec = importlib.util.spec_from_file_location("intake_run", INTAKE_DIR / "run.py")
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {INTAKE_DIR / 'run.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["intake_run"] = module
    spec.loader.exec_module(module)
    return module


def validate_ab_corpus(
    items: list[dict[str, Any]], expect_labels: tuple[str, ...]
) -> list[str]:
    errors: list[str] = []
    if len(items) < 30:
        errors.append(f"A/B corpus must hold at least 30 items, found {len(items)}")
    seen: set[str] = set()
    for index, item in enumerate(items, 1):
        label = str(item.get("id") or f"line {index}")
        errors.extend(
            f"{label}: missing or empty field {key!r}"
            for key in AB_REQUIRED
            if key not in item
            or item.get(key) is None
            or (isinstance(item.get(key), str) and not item[key].strip())
        )
        if not re.fullmatch(r"a\d{2}", str(item.get("id", ""))):
            errors.append(f"{label}: id must match aNN")
        if item.get("expect") not in expect_labels:
            errors.append(f"{label}: expect must be one of {expect_labels}")
        if item.get("open_ended") is not True:
            errors.append(f"{label}: open_ended must be true")
        if item.get("expected_artifact") in (None, "", "none"):
            errors.append(f"{label}: expected_artifact must name an artifact")
        if item.get("source") not in AB_SOURCES:
            errors.append(f"{label}: source must be one of {AB_SOURCES}")
        if isinstance(item.get("id"), str):
            if item["id"] in seen:
                errors.append(f"{label}: duplicate id")
            seen.add(item["id"])
    if len({item.get("source") for item in items}) < 3:
        errors.append("A/B corpus must use all three sources")
    return errors


def _real_run(
    args: argparse.Namespace, corpus: list[dict[str, Any]], run_module: Any
) -> int:
    if args.only:
        known = {item["id"] for item in corpus}
        if unknown := sorted(set(args.only) - known):
            print(f"[corpus] unknown --only ids: {unknown}", file=sys.stderr)
            return 2
        corpus = [item for item in corpus if item["id"] in args.only]
    items = corpus[: args.limit] if args.limit is not None else corpus
    schema = json.loads(run_module.SCHEMA_PATH.read_text(encoding="utf-8"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else Path("results") / f"ab_run_{stamp}.jsonl"
    total = terminal = 0
    for _ in range(args.repeat):
        for item in items:
            total += 1
            records = run_module._run_one(args, item)
            failures = run_module.append_records(out_path, records, schema)
            if failures:
                print(f"[schema] {failures}", file=sys.stderr)
            terminal += int(not failures and bool(records))
            print(
                f"[{item['id']}] {records[-1]['event'] if records else 'none'}",
                file=sys.stderr,
            )
    print(f"[done] {terminal}/{total} terminal records -> {out_path}", file=sys.stderr)
    return 0 if terminal == total else 1


def main(argv: list[str] | None = None) -> int:
    run_module = load_run_module()
    parser = run_module.build_parser()
    parser.description = __doc__.splitlines()[0]
    parser.set_defaults(corpus=str(DEFAULT_CORPUS))
    args = parser.parse_args(argv)
    corpus = run_module.load_corpus(Path(args.corpus))
    problems = validate_ab_corpus(corpus, run_module.EXPECT_LABELS)
    if problems:
        print("\n".join(f"[corpus] {problem}" for problem in problems), file=sys.stderr)
        return 2
    if args.dry_run:
        for item in corpus:
            print(
                f"{item['id']} | {item['source']} | {item['expect']} | {item['expected_artifact']} | {item['prompt']}"
            )
        return 0
    if (args.limit is not None and args.limit < 1) or args.repeat < 1:
        parser.error("--limit and --repeat must be >= 1")
    if not args.raw_dir:
        args.raw_dir = str(Path(tempfile.gettempdir()) / "vt-intake-ab-raw")
    return _real_run(args, corpus, run_module)


if __name__ == "__main__":
    raise SystemExit(main())
