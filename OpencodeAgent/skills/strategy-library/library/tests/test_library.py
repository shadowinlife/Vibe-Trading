# noqa: SIZE_OK - task contract mandates a single-file unittest suite for the full validator + CLI.
"""Unittest suite for library.py.

Validation rules are exercised through validate_library (the strongest
integration boundary: scan + parse + per-entry + library-wide checks). CLI
behavior (exit codes, index/search roundtrip, show, register) is exercised
through the real CLI via subprocess against a temporary copy of library.py,
so REPO_ROOT resolves to the temp dir and CWD-independence is proven.

All fixtures live in tempfile.TemporaryDirectory; no sleeps, no network.
"""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("library", REPO_ROOT / "library.py")
library = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = library
_spec.loader.exec_module(library)

ENTRY_DIRS = ("knowledge", "strategies", "indicators")


def legal_fm(entry_type: str, entry_id: str, **overrides: object) -> dict:
    """Build a fully legal frontmatter mapping for the given type and id."""
    fm = {
        "id": entry_id,
        "type": entry_type,
        "title": f"Title {entry_id}",
        "status": "candidate",
        "markets": ["ashare"],
        "sources": [{"title": "Src", "url": "https://example.com", "date": "2024-01-01"}],
        "intended_use": "research context",
        "limitations": "sample is limited",
        "created": "2024-01-01",
        "updated": "2024-02-01",
    }
    extra = {
        "knowledge": {},
        "strategy": {"entry_rules": "enter", "exit_rules": "exit", "position_sizing": "fixed"},
        "indicator": {"formula": "x", "data_source": "ohlcv", "computation": "rolling", "unit": "pct"},
    }[entry_type]
    fm.update(extra)
    fm.update(overrides)
    return fm


def make_entry_text(fm: dict, body: str) -> str:
    """Serialize a frontmatter mapping + body into the entry file format."""
    dumped = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
    return "---\n" + dumped + "---\n" + body


class TempLibraryCase(unittest.TestCase):
    """Base fixture: a temp repo root with the three entry directories."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for name in ENTRY_DIRS:
            (self.root / name).mkdir()

    def write(self, rel: str, fm: dict, body: str = "body text\n") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(make_entry_text(fm, body), encoding="utf-8")
        return path

    def errors_for(self, report: library.Report, rel: str) -> list[str]:
        for entry in report.entries:
            if entry.rel == rel:
                return list(entry.errors)
        self.fail(f"no report entry for {rel}")


class TestEntryValidation(TempLibraryCase):
    """Per-entry frontmatter rules and library-wide checks via validate_library."""

    def test_rejects_missing_sources(self) -> None:
        fm = legal_fm("knowledge", "kn-0001")
        del fm["sources"]
        self.write("knowledge/kn-0001-x.md", fm)
        report = library.validate_library(self.root)
        self.assertFalse(report.ok)
        self.assertTrue(any("sources" in e for e in self.errors_for(report, "knowledge/kn-0001-x.md")))

    def test_rejects_missing_limitations(self) -> None:
        fm = legal_fm("knowledge", "kn-0001")
        del fm["limitations"]
        self.write("knowledge/kn-0001-x.md", fm)
        report = library.validate_library(self.root)
        self.assertFalse(report.ok)
        self.assertTrue(any("limitations" in e for e in self.errors_for(report, "knowledge/kn-0001-x.md")))

    def test_rejects_validated_without_evidence(self) -> None:
        fm = legal_fm("strategy", "st-0001", status="validated")
        self.write("strategies/st-0001-x.md", fm)
        report = library.validate_library(self.root)
        self.assertFalse(report.ok)
        self.assertTrue(any("evidence" in e for e in self.errors_for(report, "strategies/st-0001-x.md")))

    def test_accepts_validated_with_evidence(self) -> None:
        fm = legal_fm(
            "strategy",
            "st-0001",
            status="validated",
            evidence=[{"summary": "backtest", "artifact": "run-1", "date": "2024-03-01"}],
        )
        self.write("strategies/st-0001-x.md", fm)
        report = library.validate_library(self.root)
        self.assertTrue(report.ok, self.errors_for(report, "strategies/st-0001-x.md"))

    def test_rejects_illegal_status(self) -> None:
        fm = legal_fm("knowledge", "kn-0001", status="approved")
        self.write("knowledge/kn-0001-x.md", fm)
        report = library.validate_library(self.root)
        self.assertFalse(report.ok)
        self.assertTrue(any("status" in e for e in self.errors_for(report, "knowledge/kn-0001-x.md")))

    def test_rejects_id_prefix_not_matching_directory(self) -> None:
        fm = legal_fm("strategy", "st-0001")
        self.write("knowledge/st-0001-x.md", fm)
        report = library.validate_library(self.root)
        self.assertFalse(report.ok)
        errors = self.errors_for(report, "knowledge/st-0001-x.md")
        self.assertTrue(any("directory" in e for e in errors), errors)

    def test_rejects_unknown_related_and_indicators(self) -> None:
        fm = legal_fm("knowledge", "kn-0001", related=["st-9999"], indicators=["in-9999"])
        self.write("knowledge/kn-0001-x.md", fm)
        report = library.validate_library(self.root)
        self.assertFalse(report.ok)
        errors = self.errors_for(report, "knowledge/kn-0001-x.md")
        self.assertTrue(any("st-9999" in e for e in errors), errors)
        self.assertTrue(any("in-9999" in e for e in errors), errors)

    def test_accepts_one_legal_entry_per_type(self) -> None:
        self.write("knowledge/kn-0001-x.md", legal_fm("knowledge", "kn-0001", related=["st-0001"], indicators=["in-0001"]))
        self.write("strategies/st-0001-x.md", legal_fm("strategy", "st-0001"))
        self.write("indicators/in-0001-x.md", legal_fm("indicator", "in-0001"))
        report = library.validate_library(self.root)
        self.assertTrue(report.ok, library.format_report(report))
        self.assertEqual(report.violation_count, 0)
        self.assertEqual(len(report.entries), 3)
        self.assertTrue(all(not e.errors for e in report.entries))

    def test_rejects_duplicate_id(self) -> None:
        self.write("knowledge/kn-0001-a.md", legal_fm("knowledge", "kn-0001"))
        self.write("knowledge/kn-0001-b.md", legal_fm("knowledge", "kn-0001"))
        report = library.validate_library(self.root)
        self.assertFalse(report.ok)
        self.assertTrue(any("duplicate" in e for e in self.errors_for(report, "knowledge/kn-0001-a.md")))
        self.assertTrue(any("duplicate" in e for e in self.errors_for(report, "knowledge/kn-0001-b.md")))

    def test_reports_parse_error_for_missing_frontmatter(self) -> None:
        (self.root / "knowledge" / "kn-0001-x.md").write_text("no frontmatter here\n", encoding="utf-8")
        report = library.validate_library(self.root)
        self.assertFalse(report.ok)
        self.assertTrue(any("frontmatter" in e for e in self.errors_for(report, "knowledge/kn-0001-x.md")))

    def test_missing_directories_are_empty(self) -> None:
        shutil.rmtree(self.root / "strategies")
        shutil.rmtree(self.root / "indicators")
        self.write("knowledge/kn-0001-x.md", legal_fm("knowledge", "kn-0001"))
        report = library.validate_library(self.root)
        self.assertTrue(report.ok)
        self.assertEqual(len(report.entries), 1)

    def test_accepts_unquoted_yaml_dates(self) -> None:
        # Unquoted YAML dates load as datetime.date; the gate must accept them,
        # as the canonical seed entries are written this way.
        text = (
            "---\n"
            "id: kn-0001\n"
            "type: knowledge\n"
            "title: T\n"
            "status: candidate\n"
            "markets: [ashare]\n"
            "sources:\n"
            "  - title: S\n"
            "    url: https://example.com\n"
            "    date: 2024-01-01\n"
            "intended_use: u\n"
            "limitations: l\n"
            "created: 2024-01-01\n"
            "updated: 2024-02-01\n"
            "---\n"
            "body\n"
        )
        (self.root / "knowledge" / "kn-0001-x.md").write_text(text, encoding="utf-8")
        report = library.validate_library(self.root)
        self.assertTrue(report.ok, library.format_report(report))

    def test_rejects_invalid_created_date(self) -> None:
        fm = legal_fm("knowledge", "kn-0001", created="not-a-date")
        self.write("knowledge/kn-0001-x.md", fm)
        report = library.validate_library(self.root)
        self.assertFalse(report.ok)
        self.assertTrue(any("created" in e for e in self.errors_for(report, "knowledge/kn-0001-x.md")))

    def test_sources_date_accepts_year_month_and_full(self) -> None:
        for date_value in ("2024", "2024-01", "2024-01-01"):
            with self.subTest(date_value=date_value):
                fm = legal_fm("knowledge", "kn-0001")
                fm["sources"][0]["date"] = date_value
                self.write("knowledge/kn-0001-x.md", fm)
                report = library.validate_library(self.root)
                self.assertTrue(report.ok, library.format_report(report))


class TestRegister(TempLibraryCase):
    """register_candidate: collision, acceptance outside dirs, self-reference, location independence."""

    def setUp(self) -> None:
        super().setUp()
        self.write("knowledge/kn-0001-x.md", legal_fm("knowledge", "kn-0001"))

    def register(self, candidate: Path) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = library.register_candidate(self.root, candidate)
        return code, buf.getvalue()

    def test_rejects_id_collision(self) -> None:
        candidate = self.root / "candidate.md"
        candidate.write_text(make_entry_text(legal_fm("knowledge", "kn-0001"), "body\n"), encoding="utf-8")
        code, out = self.register(candidate)
        self.assertEqual(code, 1)
        self.assertIn("REGISTER REJECTED", out)
        self.assertIn("collision", out)

    def test_accepts_legal_candidate_outside_library_dirs(self) -> None:
        candidate = self.root / "candidate.md"
        text = make_entry_text(legal_fm("knowledge", "kn-0002"), "body\n")
        candidate.write_text(text, encoding="utf-8")
        code, out = self.register(candidate)
        self.assertEqual(code, 0, out)
        self.assertIn("REGISTER OK", out)
        self.assertIn("kn-0002", out)
        # Candidate must not be moved, copied into the library, or modified.
        self.assertTrue(candidate.is_file())
        self.assertEqual(candidate.read_text(encoding="utf-8"), text)
        self.assertFalse((self.root / "knowledge" / "kn-0002-x.md").exists())

    def test_rejects_self_reference(self) -> None:
        fm = legal_fm("knowledge", "kn-0002", related=["kn-0002"])
        candidate = self.root / "candidate.md"
        candidate.write_text(make_entry_text(fm, "body\n"), encoding="utf-8")
        code, out = self.register(candidate)
        self.assertEqual(code, 1)
        self.assertIn("REGISTER REJECTED", out)

    def test_consistency_uses_id_prefix_not_location(self) -> None:
        # A strategy candidate sitting at the repo root (not strategies/) is fine
        # because register checks the id prefix, not the actual location.
        candidate = self.root / "candidate.md"
        candidate.write_text(make_entry_text(legal_fm("strategy", "st-0001"), "body\n"), encoding="utf-8")
        code, out = self.register(candidate)
        self.assertEqual(code, 0, out)
        self.assertIn("REGISTER OK", out)

    def test_accepts_candidate_referencing_existing_id(self) -> None:
        fm = legal_fm("knowledge", "kn-0002", related=["kn-0001"])
        candidate = self.root / "candidate.md"
        candidate.write_text(make_entry_text(fm, "body\n"), encoding="utf-8")
        code, out = self.register(candidate)
        self.assertEqual(code, 0, out)
        self.assertIn("REGISTER OK", out)


class CliCase(unittest.TestCase):
    """Drive the real CLI against a temp copy of library.py (REPO_ROOT=temp)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        shutil.copy(REPO_ROOT / "library.py", self.root / "library.py")
        for name in ENTRY_DIRS:
            (self.root / name).mkdir()
        # A different CWD proves root resolution is __file__-based, not CWD-based.
        self.elsewhere = self.root / "elsewhere"
        self.elsewhere.mkdir()

    def write(self, rel: str, fm: dict, body: str = "body text\n") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(make_entry_text(fm, body), encoding="utf-8")
        return path

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.root / "library.py"), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=self.elsewhere,
            check=False,
        )


class TestCliValidation(CliCase):
    def test_validate_clean_exits_zero(self) -> None:
        self.write("knowledge/kn-0001-x.md", legal_fm("knowledge", "kn-0001"))
        result = self.cli("validate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[OK] kn-0001", result.stdout)
        self.assertIn("0 violation(s)", result.stdout)

    def test_validate_violation_exits_one(self) -> None:
        fm = legal_fm("knowledge", "kn-0001")
        del fm["sources"]
        self.write("knowledge/kn-0001-x.md", fm)
        result = self.cli("validate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL]", result.stdout)

    def test_no_args_prints_usage_exit_two(self) -> None:
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stderr.lower())

    def test_unknown_command_exit_two(self) -> None:
        result = self.cli("bogus")
        self.assertEqual(result.returncode, 2)

    def test_help_exits_zero(self) -> None:
        result = self.cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("validate", result.stdout)
        self.assertIn("register", result.stdout)


class TestIndexSearch(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write(
            "knowledge/kn-0001-momentum.md",
            legal_fm("knowledge", "kn-0001", title="动量效应研究"),
            body="# 动量效应\nChinese body about momentum. English tokens here.\n",
        )
        self.write(
            "knowledge/kn-0002-value.md",
            legal_fm("knowledge", "kn-0002", title="价值投资笔记"),
            body="# 价值\nValue investing notes. dividend yield.\n",
        )

    def test_index_writes_keywords_with_title_and_body(self) -> None:
        result = self.cli("index")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(data["count"], 2)
        by_id = {e["id"]: e for e in data["entries"]}
        entry = by_id["kn-0001"]
        self.assertIn("动量效应研究", entry["keywords"])
        self.assertIn("# 动量效应\nChinese body about momentum. English tokens here.\n", entry["keywords"])
        self.assertIn("momentum", entry["keywords"])
        self.assertEqual(entry["path"], "knowledge/kn-0001-momentum.md")
        self.assertEqual([e["id"] for e in data["entries"]], sorted(by_id))

    def test_search_cjk_substring_hits_correct_entry(self) -> None:
        self.assertEqual(self.cli("index").returncode, 0)
        result = self.cli("search", "动量")
        self.assertEqual(result.returncode, 0)
        self.assertIn("kn-0001", result.stdout)
        self.assertNotIn("kn-0002", result.stdout)
        self.assertIn("1 hit(s)", result.stdout)

    def test_search_ascii_token_hits_correct_entry(self) -> None:
        self.assertEqual(self.cli("index").returncode, 0)
        result = self.cli("search", "dividend")
        self.assertEqual(result.returncode, 0)
        self.assertIn("kn-0002", result.stdout)
        self.assertNotIn("kn-0001", result.stdout)

    def test_search_and_semantics_and_no_match(self) -> None:
        self.assertEqual(self.cli("index").returncode, 0)
        both = self.cli("search", "动量", "momentum")
        self.assertIn("kn-0001", both.stdout)
        none = self.cli("search", "动量", "dividend")
        self.assertEqual(none.returncode, 0)
        self.assertIn("no match", none.stdout)
        self.assertIn("0 hit(s)", none.stdout)

    def test_search_without_index_exits_one(self) -> None:
        result = self.cli("search", "momentum")
        self.assertEqual(result.returncode, 1)
        self.assertIn("index", result.stderr.lower())

    def test_search_no_terms_exit_two(self) -> None:
        result = self.cli("search")
        self.assertEqual(result.returncode, 2)

    def test_index_refuses_to_write_on_violation(self) -> None:
        fm = legal_fm("knowledge", "kn-0003")
        del fm["limitations"]
        self.write("knowledge/kn-0003-bad.md", fm)
        result = self.cli("index")
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.root / "index.json").exists())

    def test_index_serializes_entries_with_unquoted_dates(self) -> None:
        text = (
            "---\n"
            "id: kn-0003\n"
            "type: knowledge\n"
            "title: 未引号日期条目\n"
            "status: candidate\n"
            "markets: [ashare]\n"
            "sources:\n"
            "  - title: S\n"
            "    url: https://example.com\n"
            "    date: 2024-01-01\n"
            "intended_use: u\n"
            "limitations: l\n"
            "created: 2024-01-01\n"
            "updated: 2024-02-01\n"
            "---\n"
            "正文 body\n"
        )
        (self.root / "knowledge" / "kn-0003-x.md").write_text(text, encoding="utf-8")
        result = self.cli("index")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(data["count"], 3)
        self.assertIn("kn-0003", {e["id"] for e in data["entries"]})


class TestShow(CliCase):
    def test_show_prints_full_raw_content(self) -> None:
        fm = legal_fm("knowledge", "kn-0001", title="动量效应研究")
        body = "# 动量\nfull body 内容 with detail.\n"
        path = self.write("knowledge/kn-0001-x.md", fm, body)
        result = self.cli("show", "kn-0001")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, path.read_text(encoding="utf-8"))

    def test_show_unknown_id_exits_one(self) -> None:
        result = self.cli("show", "kn-9999")
        self.assertEqual(result.returncode, 1)
        self.assertIn("kn-9999", result.stderr)


class TestRegisterCli(CliCase):
    def test_register_collision_rejected(self) -> None:
        self.write("knowledge/kn-0001-x.md", legal_fm("knowledge", "kn-0001"))
        candidate = self.root / "candidate.md"
        candidate.write_text(make_entry_text(legal_fm("knowledge", "kn-0001"), "body\n"), encoding="utf-8")
        result = self.cli("register", str(candidate))
        self.assertEqual(result.returncode, 1)
        self.assertIn("REGISTER REJECTED", result.stdout)

    def test_register_legal_candidate_accepted(self) -> None:
        self.write("knowledge/kn-0001-x.md", legal_fm("knowledge", "kn-0001"))
        candidate = self.root / "candidate.md"
        candidate.write_text(make_entry_text(legal_fm("knowledge", "kn-0002"), "body\n"), encoding="utf-8")
        result = self.cli("register", str(candidate))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("REGISTER OK", result.stdout)
        self.assertTrue(candidate.is_file())


if __name__ == "__main__":
    unittest.main()