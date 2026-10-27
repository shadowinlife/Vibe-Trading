"""Privacy tests for the first-message exporter.

WHY: the intake eval may count real request labels but must never commit a
message. These tests build a throwaway opencode-like SQLite store with PII in
one session and prove that:

- the scanner fires for every injected pattern (the "must fire" probe);
- the aggregate contains no message text and no PII;
- a repository-internal raw path is refused;
- the original store is byte-identical after the run (the exporter reads a
  temporary copy, then deletes it);
- a missing store reports ``unavailable`` and the synthetic fallback is
  generated, deterministic and tagged ``source="synthetic"``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

EXPORTER_PATH = Path(__file__).resolve().parent / "export_first_messages.py"
_spec = importlib.util.spec_from_file_location("export_first_messages", EXPORTER_PATH)
exporter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exporter)

# Assemble the fake values at runtime so the test file does not carry a
# contiguous secret-shaped literal that a repository scanner would flag.
FAKE_MOBILE = "138" + "00138000"
FAKE_ID = "110105" + "19491231002X"
FAKE_EMAIL = "a.b" + "@example.com"
FAKE_MONEY = "¥" + "1,234.56"
FAKE_TOKEN = "sk-" + "abcdefghijklmnop" + "1234"
INJECTED = f"看看我的持仓 {FAKE_MOBILE} {FAKE_ID} {FAKE_EMAIL} {FAKE_MONEY} {FAKE_TOKEN}"


def _write_store(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE session (
                id TEXT PRIMARY KEY,
                parent_id TEXT,
                time_created INTEGER NOT NULL
            );
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                data TEXT NOT NULL
            );
            CREATE TABLE part (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                data TEXT NOT NULL
            );
            """
        )
        sessions = {
            "ses_alpha": [
                ("assistant", "你好"),
                ("user", "帮我体检一下宁德时代"),
                ("user", "再补充一下财报"),
            ],
            "ses_beta": [("user", "按红利风格筛一份A股选股清单")],
            "ses_gamma": [("user", INJECTED)],
        }
        for session_id, rows in sessions.items():
            conn.execute(
                "INSERT INTO session (id, parent_id, time_created) VALUES (?, NULL, ?)",
                (session_id, 1),
            )
            for index, (role, text) in enumerate(rows):
                message_id = f"{session_id}_m{index}"
                conn.execute(
                    "INSERT INTO message (id, session_id, time_created, data) "
                    "VALUES (?, ?, ?, ?)",
                    (message_id, session_id, index, json.dumps({"role": role})),
                )
                conn.execute(
                    "INSERT INTO part (id, message_id, session_id, time_created, data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        f"{message_id}_p0",
                        message_id,
                        session_id,
                        index,
                        json.dumps({"type": "text", "text": text}, ensure_ascii=False),
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _export(tmp_path: Path, db: Path, synthetic: int = 0):
    out = tmp_path / "aggregate.json"
    raw = tmp_path / "raw.jsonl"
    code = exporter.main(
        [
            "--db",
            str(db),
            "--out",
            str(out),
            "--raw-out",
            str(raw),
            "--synthetic",
            str(synthetic),
        ]
    )
    assert code == 0
    return out, raw


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    path = tmp_path / "store.db"
    _write_store(path)
    return path


def test_scan_fires_on_every_injected_pattern():
    assert set(exporter.scan_text(INJECTED)) == {
        "cn_mobile",
        "cn_id",
        "email",
        "money",
        "secret",
    }
    assert exporter.scan_text("帮我体检一下宁德时代") == []


def test_aggregate_has_no_message_text_and_no_pii(tmp_path, store):
    out, raw = _export(tmp_path, store)
    rendered = out.read_text(encoding="utf-8")
    payload = json.loads(rendered)

    assert exporter.scan_text(rendered) == []
    for sample in (FAKE_MOBILE, FAKE_ID, FAKE_EMAIL, FAKE_MONEY, FAKE_TOKEN):
        assert sample not in rendered
    assert "宁德时代" not in rendered

    assert payload["source_mix"] == {"real": 3, "synthetic": 0}
    assert payload["n_total"] == 3
    assert payload["label_distribution"] == {
        "个股体检": 1,
        "选股清单": 1,
        "策略回测": 0,
        "组合体检": 1,
        "事件/宏观影响": 0,
        "交易复盘": 0,
        "定时监控": 0,
        "报告": 0,
        "都不是": 0,
    }
    assert set(payload["length_buckets"]) == set(exporter.LENGTH_BUCKETS)
    assert sum(payload["length_buckets"].values()) == 3

    # Raw text belongs only in the external export, never in the aggregate.
    raw_lines = raw.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 3
    assert any(FAKE_MOBILE in line for line in raw_lines)


def test_raw_export_refuses_repository_internal_path(tmp_path, store):
    repo_root = Path(__file__).resolve().parents[4]
    blocked = repo_root / "tmp_raw_export_should_not_exist.jsonl"
    with pytest.raises(SystemExit) as excinfo:
        exporter.main(
            [
                "--db",
                str(store),
                "--raw-out",
                str(blocked),
                "--out",
                str(tmp_path / "aggregate.json"),
            ]
        )
    assert "repository" in str(excinfo.value)
    assert not blocked.exists()


def test_original_store_is_untouched_and_copy_is_removed(tmp_path, store, monkeypatch):
    before = hashlib.sha256(store.read_bytes()).hexdigest()
    mtime = store.stat().st_mtime_ns
    created: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(exporter.tempfile, "mkdtemp", recording_mkdtemp)
    _export(tmp_path, store)

    assert hashlib.sha256(store.read_bytes()).hexdigest() == before
    assert store.stat().st_mtime_ns == mtime
    assert not store.with_name(store.name + "-wal").exists()
    assert not store.with_name(store.name + "-shm").exists()
    assert created and not Path(created[0]).exists()


def test_missing_store_is_unavailable_and_synthetic_is_tagged(tmp_path):
    out, raw = _export(tmp_path, tmp_path / "missing.db", synthetic=5)
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["source"] == "unavailable"
    assert "missing" in payload["notes"]
    assert payload["source_mix"] == {"real": 0, "synthetic": 5}
    assert payload["n_total"] == 5
    assert sum(payload["label_distribution"].values()) == 5

    raw_text = raw.read_text(encoding="utf-8")
    lines = [json.loads(line) for line in raw_text.splitlines()]
    assert len(lines) == 5
    assert all(item["source"] == "synthetic" for item in lines)
    assert exporter.scan_text(raw_text) == []


def test_synthetic_generator_is_generated_and_deterministic():
    first = exporter.generate_synthetic(16, seed=7)
    second = exporter.generate_synthetic(16, seed=7)

    assert first == second
    assert all(item["source"] == "synthetic" for item in first)
    assert len({item["text"] for item in first}) > 1
    assert {item["expected_label"] for item in first[:8]} == set(exporter.PRODUCT_LABELS)
