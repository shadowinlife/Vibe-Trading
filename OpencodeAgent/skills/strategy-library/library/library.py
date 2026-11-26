#!/usr/bin/env python3
# noqa: SIZE_OK - task contract mandates a single-file CLI (entry parser + validator + commands).
"""Strategy-library CLI: validate, index, search, show and register Markdown entries.

Entries are Markdown files with a YAML frontmatter block, stored under
``knowledge/`` (kn-), ``strategies/`` (st-) and ``indicators/`` (in-). The repo
root is the directory containing this file, so the CLI is CWD-independent.

Only the Python standard library and PyYAML are used. Date fields accept either
quoted strings or native YAML date scalars (``datetime.date``); both are
normalized to ISO text before the format check, so unquoted ``created:
2026-11-25`` validates just like ``created: "2026-11-25"``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never

import yaml

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent

# Raw YAML frontmatter is an untrusted boundary; values are narrowed with
# isinstance inside validate_entry rather than trusted.
Frontmatter = dict[str, object]  # noqa: OBJECT_OK - YAML boundary, narrowed on validation

ID_RE: Final = re.compile(r"^(kn|st|in)-[0-9]{4}$")
FULL_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FLEX_DATE_RE: Final = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
INDICATOR_REF_RE: Final = re.compile(r"^in-[0-9]{4}$")
RELATED_REF_RE: Final = re.compile(r"^(kn|st|in)-[0-9]{4}$")
TOKEN_RE: Final = re.compile(r"[a-z0-9]+")
CJK_RE: Final = re.compile(r"[一-鿿㐀-䶿]")

STATUSES: Final = frozenset(
    {"candidate", "validated", "research_only", "rejected", "blocked_by_data", "blocked_by_permission"}
)
MARKETS: Final = frozenset({"ashare", "hk", "us", "crypto"})
VALID_TYPES: Final = frozenset({"knowledge", "strategy", "indicator"})
PREFIX_TO_TYPE: Final = {"kn": "knowledge", "st": "strategy", "in": "indicator"}


@dataclass(frozen=True, slots=True)
class DirSpec:
    """One entry directory: its name, id prefix and entry type."""

    name: str
    prefix: str
    type: str


DIR_SPECS: Final = (
    DirSpec("knowledge", "kn", "knowledge"),
    DirSpec("strategies", "st", "strategy"),
    DirSpec("indicators", "in", "indicator"),
)

TYPE_REQUIRED_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "knowledge": (),
    "strategy": ("entry_rules", "exit_rules", "position_sizing"),
    "indicator": ("formula", "data_source", "computation", "unit"),
}


@dataclass(frozen=True, slots=True)
class EntryParseError(Exception):
    """A file could not be read or its frontmatter could not be parsed."""

    path: Path
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


@dataclass(frozen=True, slots=True)
class ParsedEntry:
    """A successfully parsed file: frontmatter mapping, Markdown body, raw text."""

    path: Path
    fm: Frontmatter
    body: str
    raw: str


@dataclass(frozen=True, slots=True)
class Entry:
    """A parsed library entry with its location and directory context."""

    rel: str
    dir_spec: DirSpec
    fm: Frontmatter
    body: str
    raw: str


@dataclass(frozen=True, slots=True)
class ParseFailure:
    """A file that could not be parsed, kept for reporting."""

    rel: str
    reason: str


@dataclass(frozen=True, slots=True)
class Library:
    """All scanned files: parsed entries plus parse failures."""

    entries: tuple[Entry, ...]
    parse_failures: tuple[ParseFailure, ...]


@dataclass(frozen=True, slots=True)
class EntryReport:
    """Validation outcome for one file."""

    rel: str
    id: str | None
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Report:
    """Library-wide validation outcome."""

    entries: tuple[EntryReport, ...]

    @property
    def violation_count(self) -> int:
        return sum(len(e.errors) for e in self.entries)

    @property
    def ok(self) -> bool:
        return self.violation_count == 0


def parse_entry(path: Path) -> ParsedEntry:
    """Read a Markdown file and split its YAML frontmatter from its body.

    Raises EntryParseError if the file is unreadable, lacks a frontmatter block,
    or the frontmatter is not a valid YAML mapping.
    """
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise EntryParseError(path, f"cannot read file: {exc}") from exc
    parts = raw.split("\n")
    if not parts or parts[0].strip() != "---":
        raise EntryParseError(path, "no YAML frontmatter (file must start with '---')")
    end = next((i for i in range(1, len(parts)) if parts[i].strip() == "---"), None)
    if end is None:
        raise EntryParseError(path, "frontmatter not closed (missing second '---')")
    yaml_text = "\n".join(parts[1:end])
    body = "\n".join(parts[end + 1 :])
    try:
        fm = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise EntryParseError(path, f"invalid YAML: {exc}") from exc
    if not isinstance(fm, dict):
        raise EntryParseError(path, "frontmatter is not a mapping")
    return ParsedEntry(path, fm, body, raw)


def _nonempty_str(value: object) -> bool:
    """True if value is a string with at least one non-whitespace character."""
    return isinstance(value, str) and value.strip() != ""


def _date_text(value: object) -> str | None:
    """Normalize a date field to ISO text: accept a string or a YAML date scalar.

    Returns None for anything that is not a string or ``datetime.date`` so the
    caller can report a format violation. Unquoted YAML dates (``2026-11-25``)
    load as ``datetime.date`` and are accepted alongside quoted strings.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, datetime.date):
        return value.isoformat()
    return None


def _require_nonempty(fm: Frontmatter, field: str, errors: list[str]) -> None:
    """Append a reason if a required field is missing or not a non-empty string."""
    if field not in fm:
        errors.append(f"{field}: required field missing")
    elif not _nonempty_str(fm[field]):
        errors.append(f"{field}: must be a non-empty string")


def _validate_sources(fm: Frontmatter, errors: list[str]) -> None:
    """Validate the required non-empty sources list and each source mapping."""
    if "sources" not in fm:
        errors.append("sources: required field missing")
        return
    sources = fm["sources"]
    if not isinstance(sources, list) or not sources:
        errors.append("sources: must be a non-empty list")
        return
    for i, item in enumerate(sources):
        if not isinstance(item, dict):
            errors.append(f"sources[{i}]: must be a mapping")
            continue
        for key in ("title", "url"):
            if not _nonempty_str(item.get(key)):
                errors.append(f"sources[{i}].{key}: must be a non-empty string")
        url = item.get("url")
        if _nonempty_str(url) and not (url.startswith("http://") or url.startswith("https://")):
            errors.append(f"sources[{i}].url: must start with http:// or https://")
        src_date = _date_text(item.get("date"))
        if src_date is None or not src_date.strip():
            errors.append(f"sources[{i}].date: must be a non-empty date")
        elif not FLEX_DATE_RE.fullmatch(src_date):
            errors.append(f"sources[{i}].date: must be YYYY, YYYY-MM, or YYYY-MM-DD")


def _validate_evidence(fm: Frontmatter, errors: list[str]) -> None:
    """Validate the evidence list required when status is 'validated'."""
    evidence = fm.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence: status 'validated' requires a non-empty evidence list")
        return
    for i, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"evidence[{i}]: must be a mapping")
            continue
        for key in ("summary", "artifact"):
            if not _nonempty_str(item.get(key)):
                errors.append(f"evidence[{i}].{key}: must be a non-empty string")
        ev_date = _date_text(item.get("date"))
        if ev_date is None or not ev_date.strip():
            errors.append(f"evidence[{i}].date: must be a non-empty date")
        elif not FLEX_DATE_RE.fullmatch(ev_date):
            errors.append(f"evidence[{i}].date: must be YYYY, YYYY-MM, or YYYY-MM-DD")


def _validate_optional_common(fm: Frontmatter, errors: list[str]) -> None:
    """Type-check optional common fields only when they are present."""
    if "asset_classes" in fm:
        value = fm["asset_classes"]
        if not isinstance(value, list) or not all(_nonempty_str(x) for x in value):
            errors.append("asset_classes: must be a list of non-empty strings")
    if "horizon" in fm and not _nonempty_str(fm["horizon"]):
        errors.append("horizon: must be a non-empty string")
    if "indicators" in fm:
        value = fm["indicators"]
        if not isinstance(value, list) or not all(
            isinstance(x, str) and INDICATOR_REF_RE.fullmatch(x) for x in value
        ):
            errors.append("indicators: must be a list of ids matching ^in-[0-9]{4}$")
    if "related" in fm:
        value = fm["related"]
        if not isinstance(value, list) or not all(
            isinstance(x, str) and RELATED_REF_RE.fullmatch(x) for x in value
        ):
            errors.append("related: must be a list of ids matching ^(kn|st|in)-[0-9]{4}$")
    if "combination_hints" in fm:
        value = fm["combination_hints"]
        ok = _nonempty_str(value) or (isinstance(value, list) and all(_nonempty_str(x) for x in value))
        if not ok:
            errors.append("combination_hints: must be a non-empty string or a list of non-empty strings")


def _validate_type_specific(fm: Frontmatter, entry_type: str, errors: list[str]) -> None:
    """Validate required and optional fields that depend on the entry type."""
    for field in TYPE_REQUIRED_FIELDS[entry_type]:
        _require_nonempty(fm, field, errors)
    if entry_type == "strategy":
        for field in ("signal_definition", "vt_artifact_id"):
            if field in fm and not isinstance(fm[field], str):
                errors.append(f"{field}: must be a string")
    if entry_type == "indicator" and "thresholds" in fm:
        value = fm["thresholds"]
        if not (_nonempty_str(value) or isinstance(value, (list, dict))):
            errors.append("thresholds: must be a non-empty string, a list, or a mapping")


def validate_entry(fm: Frontmatter, dir_spec: DirSpec | None) -> list[str]:
    """Return every frontmatter violation for one entry (empty list if legal).

    When dir_spec is given (library mode) the id prefix and type must also match
    the directory. When dir_spec is None (register mode) only the intrinsic
    id-prefix/type consistency is checked, not the file's actual location.
    """
    errors: list[str] = []
    entry_id = fm.get("id")
    id_ok = isinstance(entry_id, str) and ID_RE.fullmatch(entry_id) is not None
    if "id" not in fm:
        errors.append("id: required field missing")
    elif not id_ok:
        errors.append("id: must be a string matching ^(kn|st|in)-[0-9]{4}$")

    entry_type = fm.get("type")
    type_ok = isinstance(entry_type, str) and entry_type in VALID_TYPES
    if "type" not in fm:
        errors.append("type: required field missing")
    elif not type_ok:
        errors.append("type: must be one of knowledge, strategy, indicator")

    if id_ok and type_ok:
        prefix = entry_id.split("-")[0]
        if PREFIX_TO_TYPE[prefix] != entry_type:
            errors.append(
                f"type: {entry_type!r} inconsistent with id prefix {prefix!r} "
                f"(expected {PREFIX_TO_TYPE[prefix]!r})"
            )
    if dir_spec is not None:
        if id_ok and entry_id.split("-")[0] != dir_spec.prefix:
            errors.append(f"id: prefix does not match directory {dir_spec.name!r} (expected {dir_spec.prefix!r})")
        if type_ok and entry_type != dir_spec.type:
            errors.append(f"type: {entry_type!r} does not match directory {dir_spec.name!r} (expected {dir_spec.type!r})")

    for field in ("title", "intended_use", "limitations"):
        _require_nonempty(fm, field, errors)

    status = fm.get("status")
    if "status" not in fm:
        errors.append("status: required field missing")
    elif not isinstance(status, str) or status not in STATUSES:
        errors.append(
            "status: must be one of candidate, validated, research_only, rejected, "
            "blocked_by_data, blocked_by_permission"
        )

    if "markets" not in fm:
        errors.append("markets: required field missing")
    else:
        markets = fm["markets"]
        if not isinstance(markets, list) or not markets:
            errors.append("markets: must be a non-empty list")
        else:
            for market in markets:
                if not isinstance(market, str) or market not in MARKETS:
                    errors.append(f"markets: invalid market {market!r} (allowed: ashare, hk, us, crypto)")

    _validate_sources(fm, errors)

    for field in ("created", "updated"):
        if field not in fm:
            errors.append(f"{field}: required field missing")
        else:
            text = _date_text(fm[field])
            if text is None or not FULL_DATE_RE.fullmatch(text):
                errors.append(f"{field}: must be a date in YYYY-MM-DD format")

    if status == "validated":
        _validate_evidence(fm, errors)

    _validate_optional_common(fm, errors)
    if type_ok:
        _validate_type_specific(fm, entry_type, errors)
    return errors


def load_library(root: Path) -> Library:
    """Scan the three entry directories under root for *.md files.

    Missing directories are treated as empty. Files are returned in a stable
    order (directory order, then filename).
    """
    entries: list[Entry] = []
    failures: list[ParseFailure] = []
    for spec in DIR_SPECS:
        directory = root / spec.name
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            rel = path.relative_to(root).as_posix()
            try:
                parsed = parse_entry(path)
            except EntryParseError as exc:
                failures.append(ParseFailure(rel, exc.reason))
                continue
            entries.append(Entry(rel, spec, parsed.fm, parsed.body, parsed.raw))
    return Library(tuple(entries), tuple(failures))


def library_ids(lib: Library) -> set[str]:
    """The set of well-formed ids present among parsed library entries."""
    ids: set[str] = set()
    for entry in lib.entries:
        entry_id = entry.fm.get("id")
        if isinstance(entry_id, str) and ID_RE.fullmatch(entry_id):
            ids.add(entry_id)
    return ids


def validate_library(root: Path) -> Report:
    """Validate every entry plus library-wide id uniqueness and reference checks."""
    lib = load_library(root)
    errors_by_rel: dict[str, list[str]] = {}
    id_by_rel: dict[str, str | None] = {}
    for failure in lib.parse_failures:
        errors_by_rel[failure.rel] = [failure.reason]
        id_by_rel[failure.rel] = None
    for entry in lib.entries:
        errors_by_rel[entry.rel] = validate_entry(entry.fm, entry.dir_spec)
        entry_id = entry.fm.get("id")
        id_by_rel[entry.rel] = entry_id if isinstance(entry_id, str) and ID_RE.fullmatch(entry_id) else None

    all_ids = library_ids(lib)
    id_count: dict[str, int] = {}
    for entry in lib.entries:
        entry_id = id_by_rel[entry.rel]
        if entry_id is not None:
            id_count[entry_id] = id_count.get(entry_id, 0) + 1
    for entry in lib.entries:
        entry_id = id_by_rel[entry.rel]
        if entry_id is not None and id_count[entry_id] > 1:
            errors_by_rel[entry.rel].append(f"duplicate id {entry_id!r} across library")
        for field in ("indicators", "related"):
            refs = entry.fm.get(field)
            if isinstance(refs, list):
                for ref in refs:
                    if isinstance(ref, str) and ref not in all_ids:
                        errors_by_rel[entry.rel].append(f"{field}: references unknown id {ref!r}")

    reports = [EntryReport(rel, id_by_rel[rel], tuple(errors_by_rel[rel])) for rel in sorted(errors_by_rel)]
    return Report(tuple(reports))


def format_report(report: Report) -> str:
    """Render the human-readable validation report with a trailing summary line."""
    lines: list[str] = []
    for entry in report.entries:
        if entry.errors:
            lines.append(f"[FAIL] {entry.rel}: {'; '.join(entry.errors)}")
        else:
            lines.append(f"[OK] {entry.id} {entry.rel}")
    lines.append(f"checked {len(report.entries)} entries, {report.violation_count} violation(s)")
    return "\n".join(lines)


def _fm_strings(fm: Frontmatter) -> list[str]:
    """Collect every string leaf in the frontmatter (including nested mappings/lists)."""
    out: list[str] = []

    def walk(value: object) -> None:
        # Open YAML scalar tree (str/dict/list/other), not a closed variant set.
        if isinstance(value, str):  # noqa: IF_VARIANT_OK - recursive YAML tree walk
            out.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(fm)
    return out


def entry_keywords(title: str, body: str, fm: Frontmatter) -> list[str]:
    """Build index keywords: raw title, raw body, plus lowercased English tokens."""
    keywords = [title, body]
    text = " ".join([title, body, *_fm_strings(fm)])
    tokens = sorted({token for token in TOKEN_RE.findall(text.lower()) if len(token) >= 2})
    keywords.extend(tokens)
    return keywords


def build_index(lib: Library) -> dict[str, object]:  # noqa: DICT_OK - JSON document boundary
    """Build the index.json document from a fully validated library."""
    entries: list[dict[str, object]] = []
    for entry in sorted(lib.entries, key=lambda e: str(e.fm.get("id"))):
        fm = entry.fm
        title = str(fm["title"])
        entries.append(
            {
                "id": fm["id"],
                "type": fm["type"],
                "title": title,
                "status": fm["status"],
                "markets": fm["markets"],
                "path": entry.rel,
                "keywords": entry_keywords(title, entry.body, fm),
            }
        )
    return {"generated": datetime.date.today().isoformat(), "count": len(entries), "entries": entries}


def _term_matches(term: str, text: str) -> bool:
    """CJK terms match by case-insensitive substring; ASCII terms by whole token."""
    if CJK_RE.search(term):
        return term.lower() in text.lower()
    return term.lower() in set(TOKEN_RE.findall(text.lower()))


def search_index(root: Path, terms: list[str]) -> int:
    """Search index.json for entries matching ALL terms; print hits and count."""
    index_path = root / "index.json"
    if not index_path.is_file():
        print("index.json not found; run 'index' first", file=sys.stderr)
        return 1
    data = json.loads(index_path.read_text(encoding="utf-8"))
    hits = []
    for entry in data["entries"]:
        text = entry["title"] + " " + " ".join(entry["keywords"])
        if all(_term_matches(term, text) for term in terms):
            hits.append(entry)
    for entry in hits:
        print(f"{entry['id']}  [{entry['status']}]  {entry['title']}  ({entry['path']})")
    if not hits:
        print("no match")
    print(f"{len(hits)} hit(s)")
    return 0


def show_entry(root: Path, entry_id: str) -> int:
    """Print the full raw content of the entry with the given id."""
    lib = load_library(root)
    for entry in lib.entries:
        if entry.fm.get("id") == entry_id:
            sys.stdout.write(entry.raw)
            return 0
    print(f"unknown id: {entry_id}", file=sys.stderr)
    return 1


def register_candidate(root: Path, candidate: Path) -> int:
    """Validate a candidate file without touching it or any library file.

    Directory consistency is checked against the id prefix in the file itself,
    not its location. Rejects id collisions and references (including
    self-references) that do not exist in the current library.
    """
    try:
        parsed = parse_entry(candidate)
    except EntryParseError as exc:
        print(f"[REGISTER REJECTED] {exc.reason}")
        return 1
    fm = parsed.fm
    errors = validate_entry(fm, None)
    all_ids = library_ids(load_library(root))
    cand_id = fm.get("id")
    if isinstance(cand_id, str) and ID_RE.fullmatch(cand_id):
        if cand_id in all_ids:
            errors.append(f"id collision: {cand_id!r} already exists in library")
        for field in ("indicators", "related"):
            refs = fm.get(field)
            if isinstance(refs, list):
                for ref in refs:
                    if isinstance(ref, str) and ref not in all_ids:
                        errors.append(f"{field}: references unknown id {ref!r} (self-references are not allowed)")
    if errors:
        print(f"[REGISTER REJECTED] {'; '.join(errors)}")
        return 1
    print(f"[REGISTER OK] {cand_id} ({candidate})")
    return 0


def cmd_validate() -> int:
    """CLI: validate the library and print the report."""
    report = validate_library(REPO_ROOT)
    print(format_report(report))
    return 0 if report.ok else 1


def cmd_index() -> int:
    """CLI: validate, then write index.json only if there are no violations."""
    report = validate_library(REPO_ROOT)
    if not report.ok:
        print(format_report(report))
        return 1
    index = build_index(load_library(REPO_ROOT))
    out = REPO_ROOT / "index.json"
    out.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({index['count']} entries)")
    return 0


def cmd_search(terms: list[str]) -> int:
    """CLI: search the index for all terms."""
    return search_index(REPO_ROOT, terms)


def cmd_show(entry_id: str) -> int:
    """CLI: show the full raw content of one entry."""
    return show_entry(REPO_ROOT, entry_id)


def cmd_register(file: str) -> int:
    """CLI: register (validate only) a candidate entry file."""
    return register_candidate(REPO_ROOT, Path(file))


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser with the five subcommands."""
    parser = argparse.ArgumentParser(prog="library.py", description="Strategy-library entry tooling.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="validate all entries and library-wide rules")
    sub.add_parser("index", help="validate, then write index.json")
    search = sub.add_parser("search", help="search index.json (AND across terms)")
    search.add_argument("terms", nargs="+", help="search terms (CJK substring or ASCII token)")
    show = sub.add_parser("show", help="print the full raw content of an entry")
    show.add_argument("id", help="entry id, e.g. st-0001")
    register = sub.add_parser("register", help="validate a candidate entry file without moving it")
    register.add_argument("file", help="path to the candidate entry file")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    args = _build_parser().parse_args(argv)
    match args.command:
        case "validate":
            return cmd_validate()
        case "index":
            return cmd_index()
        case "search":
            return cmd_search(args.terms)
        case "show":
            return cmd_show(args.id)
        case "register":
            return cmd_register(args.file)
        case unreachable:
            assert_never(unreachable)


if __name__ == "__main__":
    raise SystemExit(main())