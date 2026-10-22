"""T9 — per-bar ledger layout (``bar_store`` / ``bar_layout`` / ``session_summary``).

Every test injects a temporary account root; nothing here touches the real
home, the v1.2 day-step layout, or a wall clock.  The refusals are the point of
the module, so they are probed harder than the happy path: an existing bar, a
symlinked component, a traversal segment and a tampered manifest each have a
dedicated test.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from src.tools.paper_trade import bar_clock, bar_store, session_summary, storage_io
from src.tools.paper_trade.bar_layout import (
    BAR_STEP_FORMAT,
    SESSION_FORMAT,
    BarStepCorrupt,
    BarStepExists,
    BarStepNotFound,
    BarStoreError,
    SessionCorrupt,
    SessionNotFound,
    UnsafeBarPath,
)
from src.tools.paper_trade.store import AccountStore

DAY = "2026-03-02"
ACCT = "acct_bar"
LABEL = "09:35"
SYMBOL = "600519.SH"
BARS_CSV = "ts,open,high,low,close,volume\n09:35,1500,1501,1499,1500.5,1000\n"
_MODULES = (bar_store, session_summary)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "accounts" / ACCT
    root.mkdir(parents=True, mode=0o700)
    return root


def _content(label: str = LABEL, **overrides) -> bar_store.BarStepContent:
    base = bar_store.BarStepContent(
        account_id=ACCT,
        trade_date=DAY,
        bar_label=label,
        bar_time=bar_clock.bar_time(DAY, label).isoformat(),
        request_hash=_sha(f"request:{label}"),
        result_hash=_sha(f"result:{label}"),
        orders=[{"order_id": f"{ACCT}:{DAY}:{label}:00000001", "status": "new"}],
        fills=[{"order_id": f"{ACCT}:{DAY}:{label}:00000001", "qty": 100.0, "price": 1500.5}],
        order_events=[{"order_id": f"{ACCT}:{DAY}:{label}:00000001", "event": "filled"}],
        positions=[{"symbol": SYMBOL, "qty": 100.0, "avg_cost": 1500.5}],
        events=[{"type": "bar_applied", "bar_label": label}],
        bars={SYMBOL: BARS_CSV},
    )
    return replace(base, **overrides)


def _bar_dir(root: Path, label: str = LABEL) -> Path:
    return root / "steps" / DAY / "bars" / label.replace(":", "")


def _session(**overrides):
    kwargs = {
        "account_id": ACCT,
        "trade_date": DAY,
        "status": "open",
        "applied_bars": ("09:35", "09:40"),
        "counts": {"fills": 4, "rejections": 1},
        "result_hash_chain_head": _sha("head"),
    }
    kwargs.update(overrides)
    return kwargs


def _json_keys(value, found: set[str]) -> set[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            _json_keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _json_keys(item, found)
    return found


_CLOCK_SUFFIXES = (".json", ".jsonl")


# ── write / read round-trip ──


def test_write_then_read_round_trips_every_artefact(tmp_path: Path) -> None:
    root = _root(tmp_path)
    content = _content()

    written = bar_store.write_bar_step(root, content)

    assert written == _bar_dir(root)
    step = bar_store.read_bar_step(root, DAY, LABEL)
    assert step.account_id == ACCT
    assert step.bar_label == LABEL
    assert step.bar_time == "2026-03-02T09:35:00+08:00"
    assert step.request_hash == content.request_hash
    assert step.result_hash == content.result_hash
    assert step.orders[0]["status"] == "new"
    assert step.fills[0]["qty"] == 100.0
    assert step.order_events[0]["event"] == "filled"
    assert step.positions[0]["symbol"] == SYMBOL
    assert step.events[0]["bar_label"] == LABEL
    assert step.bars == {SYMBOL: BARS_CSV}


def test_bar_step_tree_has_the_documented_files(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bar_store.write_bar_step(root, _content())

    bar_dir = _bar_dir(root)
    assert sorted(path.name for path in bar_dir.iterdir()) == [
        "bars",
        "events.jsonl",
        "fills.jsonl",
        "manifest.json",
        "order_events.jsonl",
        "orders.jsonl",
        "positions.jsonl",
        "step.json",
    ]
    assert (bar_dir / "bars" / f"{SYMBOL}.csv").read_text(encoding="utf-8") == BARS_CSV
    for name in ("orders.jsonl", "fills.jsonl", "order_events.jsonl", "positions.jsonl", "events.jsonl"):
        assert (bar_dir / name).read_bytes(), name


def test_manifest_hashes_and_sizes_every_file(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bar_store.write_bar_step(root, _content())
    bar_dir = _bar_dir(root)

    manifest = json.loads((bar_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == BAR_STEP_FORMAT
    assert manifest["account_id"] == ACCT
    assert manifest["bar_label"] == LABEL
    assert manifest["counts"] == {
        "orders": 1,
        "fills": 1,
        "order_events": 1,
        "positions": 1,
        "events": 1,
        "bars": 1,
    }
    assert "manifest.json" not in manifest["files"]
    for relative, entry in manifest["files"].items():
        data = (bar_dir / relative).read_bytes()
        assert entry == {"sha256": hashlib.sha256(data).hexdigest(), "lines": data.count(b"\n"), "bytes": len(data)}


def test_bar_time_is_normalized_from_another_offset(tmp_path: Path) -> None:
    root = _root(tmp_path)
    content = _content(bar_time="2026-03-02T01:35:00+00:00")

    bar_store.write_bar_step(root, content)

    assert bar_store.read_bar_step(root, DAY, LABEL).bar_time == "2026-03-02T09:35:00+08:00"


# ── overwrite refusal ──


def test_second_write_to_the_same_bar_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bar_store.write_bar_step(root, _content())
    before = {path: path.read_bytes() for path in _bar_dir(root).rglob("*") if path.is_file()}

    with pytest.raises(BarStepExists) as excinfo:
        bar_store.write_bar_step(root, _content(fills=[]))

    assert excinfo.value.code == "bar_step_exists"
    after = {path: path.read_bytes() for path in _bar_dir(root).rglob("*") if path.is_file()}
    assert after == before


def test_symlinked_date_directory_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "steps").mkdir(mode=0o700)
    outside = tmp_path / "outside_date"
    outside.mkdir()
    (root / "steps" / DAY).symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeBarPath) as excinfo:
        bar_store.write_bar_step(root, _content())

    assert excinfo.value.code == "bar_store_unsafe_path"
    assert list(outside.iterdir()) == []


def test_symlinked_bar_directory_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bars_root = root / "steps" / DAY / "bars"
    bars_root.mkdir(parents=True, mode=0o700)
    outside = tmp_path / "outside_bar"
    outside.mkdir()
    (bars_root / "0935").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeBarPath):
        bar_store.write_bar_step(root, _content())

    assert list(outside.iterdir()) == []


def test_symlinked_steps_directory_inside_the_account_is_refused(tmp_path: Path) -> None:
    """Containment alone would allow this link: only the is_symlink() guard refuses it."""
    root = _root(tmp_path)
    inside = root / "relocated"
    inside.mkdir()
    (root / "steps").symlink_to(inside, target_is_directory=True)

    with pytest.raises(UnsafeBarPath):
        bar_store.write_bar_step(root, _content())

    assert list(inside.iterdir()) == []


def test_existing_empty_bar_directory_is_not_replaced(tmp_path: Path) -> None:
    """An empty directory is the one target os.replace() WOULD silently overwrite."""
    root = _root(tmp_path)
    bar_dir = _bar_dir(root)
    bar_dir.mkdir(parents=True, mode=0o700)

    with pytest.raises(BarStepExists):
        bar_store.write_bar_step(root, _content())

    assert list(bar_dir.iterdir()) == []


def test_symlinked_account_root_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real_account"
    real.mkdir()
    link = tmp_path / "acct_link"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(UnsafeBarPath):
        bar_store.write_bar_step(link, _content(account_id="acct_link"))


def test_symlinked_bar_csv_is_refused_on_read(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bar_store.write_bar_step(root, _content())
    csv_path = _bar_dir(root) / "bars" / f"{SYMBOL}.csv"
    outside = tmp_path / "outside.csv"
    outside.write_text(BARS_CSV, encoding="utf-8")
    csv_path.unlink()
    csv_path.symlink_to(outside)

    with pytest.raises(BarStepCorrupt, match="symlink:bars/"):
        bar_store.read_bar_step(root, DAY, LABEL)


def test_symlinked_bar_directory_is_refused_on_read(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bar_store.write_bar_step(root, _content())
    moved = tmp_path / "moved_bars"
    moved.mkdir()
    bar_dir = _bar_dir(root)
    for path in bar_dir.iterdir():
        shutil.move(str(path), moved / path.name)
    bar_dir.rmdir()
    bar_dir.symlink_to(moved, target_is_directory=True)

    with pytest.raises(UnsafeBarPath):
        bar_store.read_bar_step(root, DAY, LABEL)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("account_id", "../escape", "invalid_account"),
        ("account_id", "acct/other", "invalid_account"),
        ("trade_date", "../../2026-03-02", "invalid_trade_date"),
        ("trade_date", "2026-3-2", "invalid_trade_date"),
        ("bar_label", "../0935", "invalid_bar_label"),
        ("bar_label", "9:35", "invalid_bar_label"),
        ("bar_label", "09:31", "invalid_bar_label"),
        ("bar_label", "09:30", "invalid_bar_label"),
    ],
)
def test_traversal_and_off_grid_identity_is_refused(
    tmp_path: Path, field: str, value: str, code: str
) -> None:
    root = _root(tmp_path)
    account_id = ACCT if field != "account_id" else value

    with pytest.raises(BarStoreError) as excinfo:
        bar_store.bar_step_paths(
            root,
            account_id=account_id,
            trade_date="2026-03-02" if field != "trade_date" else value,
            bar_label="09:35" if field != "bar_label" else value,
        )

    assert excinfo.value.code == code
    assert not (root / "steps").exists()


# ── tamper detection ──


def test_tampered_file_is_detected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bar_store.write_bar_step(root, _content())
    with (_bar_dir(root) / "fills.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"order_id":"forged"}\n')

    with pytest.raises(BarStepCorrupt, match=r"sha256:fills\.jsonl|lines:fills\.jsonl"):
        bar_store.read_bar_step(root, DAY, LABEL)


def test_deleted_file_is_detected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bar_store.write_bar_step(root, _content())
    (_bar_dir(root) / "orders.jsonl").unlink()

    with pytest.raises(BarStepCorrupt, match=r"missing:orders\.jsonl"):
        bar_store.read_bar_step(root, DAY, LABEL)


def test_tampered_manifest_digest_is_detected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bar_store.write_bar_step(root, _content())
    manifest_path = _bar_dir(root) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["fills.jsonl"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BarStepCorrupt, match=r"sha256:fills\.jsonl"):
        bar_store.read_bar_step(root, DAY, LABEL)


def test_tampered_manifest_size_is_detected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bar_store.write_bar_step(root, _content())
    manifest_path = _bar_dir(root) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["events.jsonl"]["bytes"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BarStepCorrupt, match=r"bytes:events\.jsonl"):
        bar_store.read_bar_step(root, DAY, LABEL)


def test_unlisted_file_is_detected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bar_store.write_bar_step(root, _content())
    (_bar_dir(root) / "stray.txt").write_text("not part of the step", encoding="utf-8")

    with pytest.raises(BarStepCorrupt, match=r"unlisted:stray\.txt"):
        bar_store.read_bar_step(root, DAY, LABEL)


def test_missing_manifest_is_detected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bar_store.write_bar_step(root, _content())
    (_bar_dir(root) / "manifest.json").unlink()

    with pytest.raises(BarStepCorrupt):
        bar_store.read_bar_step(root, DAY, LABEL)


def test_missing_bar_step_raises_not_found(tmp_path: Path) -> None:
    root = _root(tmp_path)

    with pytest.raises(BarStepNotFound) as excinfo:
        bar_store.read_bar_step(root, DAY, LABEL)

    assert excinfo.value.code == "bar_step_not_found"


def test_account_root_mismatch_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)

    with pytest.raises(UnsafeBarPath, match="does not belong"):
        bar_store.write_bar_step(root, _content(account_id="someone_else"))


def test_invalid_hashes_are_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    for overrides in ({"request_hash": "abc"}, {"result_hash": "G" * 64}):
        with pytest.raises(BarStoreError) as excinfo:
            bar_store.write_bar_step(root, _content(**overrides))
        assert excinfo.value.code == "invalid_hash"
    assert not (root / "steps").exists()


def test_naive_or_mismatched_bar_time_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    for stamp in ("2026-03-02T09:35:00", "2026-03-02T09:40:00+08:00", "not-a-time"):
        with pytest.raises(BarStoreError) as excinfo:
            bar_store.write_bar_step(root, _content(bar_time=stamp))
        assert excinfo.value.code == "invalid_bar_time"
    assert not (root / "steps").exists()


def test_unsafe_bar_symbol_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)

    with pytest.raises(BarStoreError) as excinfo:
        bar_store.write_bar_step(root, _content(bars={"../escape": BARS_CSV}))

    assert excinfo.value.code == "invalid_bar_symbol"


# ── atomicity ──


def test_mid_write_failure_leaves_no_partial_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    calls = {"count": 0}
    real_write = storage_io._write_durable

    def failing_write(path: Path, data: bytes) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("injected failure")
        real_write(path, data)

    monkeypatch.setattr(storage_io, "_write_durable", failing_write)

    with pytest.raises(OSError, match="injected failure"):
        bar_store.write_bar_step(root, _content())

    bars_root = root / "steps" / DAY / "bars"
    assert not (bars_root / "0935").exists()
    assert list(bars_root.glob(".staging-*")) == []
    with pytest.raises(BarStepNotFound):
        bar_store.read_bar_step(root, DAY, LABEL)


# ── determinism / no clock, no paths ──


def test_bar_artefacts_carry_no_clock_and_no_absolute_path(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bar_store.write_bar_step(root, _content())
    bar_dir = _bar_dir(root)
    banned = {"applied_at", "created_at", "now", "timestamp", "updated_at"}
    stamps: set[str] = set()

    for path in sorted(bar_dir.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert str(tmp_path) not in text, path
        assert str(root) not in text, path
        stamps.update(re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})", text))
        if path.suffix in _CLOCK_SUFFIXES:
            document = json.loads(text) if path.suffix == ".json" else None
            if document is not None:
                assert not (_json_keys(document, set()) & banned), path

    assert stamps == {"2026-03-02T09:35:00+08:00"}


def test_modules_never_read_a_clock() -> None:
    for module in _MODULES:
        source = Path(module.__file__).read_text(encoding="utf-8")
        for token in ("datetime.now", "utcnow", "time.time", "date.today", "time.monotonic"):
            assert token not in source, (module.__name__, token)


# ── session summary ──


def test_session_summary_round_trips_and_is_replaced(tmp_path: Path) -> None:
    root = _root(tmp_path)
    path = session_summary.write_session_summary(root, **_session())

    document = session_summary.read_session_summary(root, DAY)
    assert document["format"] == SESSION_FORMAT
    assert document["account_id"] == ACCT
    assert document["first_bar"] == "09:35"
    assert document["last_bar"] == "09:40"
    assert document["watermark_bar"] == "09:40"
    assert document["applied_bar_count"] == 2
    assert document["counts"] == {"bars": 2, "fills": 4, "rejections": 1}
    assert document["result_hash_chain_head"] == _sha("head")
    assert path == root / "steps" / DAY / "session.json"

    session_summary.write_session_summary(
        root,
        **_session(applied_bars=("09:35", "09:40", "09:45"), status="closed", counts={"fills": 6}),
    )
    replaced = session_summary.read_session_summary(root, DAY)
    assert replaced["applied_bar_count"] == 3
    assert replaced["status"] == "closed"
    assert replaced["watermark_bar"] == "09:45"
    assert replaced["counts"] == {"bars": 3, "fills": 6}
    assert list(path.parent.glob(".tmp-*")) == []


def test_empty_session_summary_has_no_watermark(tmp_path: Path) -> None:
    root = _root(tmp_path)
    session_summary.write_session_summary(root, **_session(applied_bars=(), counts=None, result_hash_chain_head=""))

    document = session_summary.read_session_summary(root, DAY)
    assert document["first_bar"] == document["last_bar"] == document["watermark_bar"] == ""
    assert document["applied_bar_count"] == 0
    assert document["result_hash_chain_head"] == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "paused"},
        {"applied_bars": ("09:40", "09:35")},
        {"applied_bars": ("09:35", "09:35")},
        {"applied_bars": ("09:31",)},
        {"applied_bars": (), "result_hash_chain_head": _sha("head")},
        {"counts": {"bars": 9}},
        {"counts": {"Fills": 1}},
        {"counts": {"fills": -1}},
        {"counts": {"bars": True}},
    ],
)
def test_invalid_session_inputs_are_refused(tmp_path: Path, overrides) -> None:
    root = _root(tmp_path)

    with pytest.raises(BarStoreError) as excinfo:
        session_summary.write_session_summary(root, **_session(**overrides))

    assert excinfo.value.code == "invalid_session"
    assert not (root / "steps" / DAY / "session.json").exists()


def test_session_summary_is_guarded_and_detects_tampering(tmp_path: Path) -> None:
    root = _root(tmp_path)
    with pytest.raises(SessionNotFound):
        session_summary.read_session_summary(root, DAY)
    path = session_summary.write_session_summary(root, **_session())

    outside = tmp_path / "outside_session.json"
    outside.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(UnsafeBarPath):
        session_summary.read_session_summary(root, DAY)
    path.unlink()

    path.write_text(json.dumps({"format": SESSION_FORMAT, "trade_date": DAY}), encoding="utf-8")
    with pytest.raises(SessionCorrupt):
        session_summary.read_session_summary(root, DAY)


def test_session_summary_carries_no_clock(tmp_path: Path) -> None:
    root = _root(tmp_path)
    path = session_summary.write_session_summary(root, **_session())
    document = json.loads(path.read_text(encoding="utf-8"))

    assert "timestamp" not in _json_keys(document, set())
    assert not {"created_at", "updated_at", "applied_at"} & _json_keys(document, set())


# ── coexistence with the v1.2 day-step layout ──


def test_bar_layout_leaves_the_v12_day_step_untouched(tmp_path: Path) -> None:
    store = AccountStore(tmp_path)
    root = _root(tmp_path)
    (root / "steps").mkdir(mode=0o700)
    record = store.write_step(ACCT, DAY, step={"engine_fingerprint": "fp-test", "end_state": {}})
    day_step = record.directory / "step.json"
    before = day_step.read_bytes()

    bar_store.write_bar_step(root, _content())

    assert day_step.read_bytes() == before
    assert store.read_step(ACCT, DAY).directory == record.directory
    assert (record.directory / "bars" / "0935" / "step.json").is_file()
