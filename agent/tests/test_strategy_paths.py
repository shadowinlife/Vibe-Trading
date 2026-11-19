"""Layout, id-safety and atomic-write checks for the strategy library."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.strategy_library import paths

STRATEGY_ID = "momentum-alpha"
VERSION = "v1-abcdef12"


def test_layout_paths_are_runtime_root_relative(tmp_path: Path) -> None:
    assert paths.strategies_root(runtime_root=tmp_path) == tmp_path / "strategies"
    assert (
        paths.strategy_dir(STRATEGY_ID, runtime_root=tmp_path)
        == tmp_path / "strategies" / STRATEGY_ID
    )
    assert (
        paths.spec_path(STRATEGY_ID, runtime_root=tmp_path)
        == tmp_path / "strategies" / STRATEGY_ID / "strategy.json"
    )
    assert (
        paths.versions_dir(STRATEGY_ID, runtime_root=tmp_path)
        == tmp_path / "strategies" / STRATEGY_ID / "versions"
    )
    assert (
        paths.version_dir(STRATEGY_ID, VERSION, runtime_root=tmp_path)
        == tmp_path / "strategies" / STRATEGY_ID / "versions" / VERSION
    )
    assert (
        paths.version_spec_path(STRATEGY_ID, VERSION, runtime_root=tmp_path)
        == tmp_path
        / "strategies"
        / STRATEGY_ID
        / "versions"
        / VERSION
        / "strategy.json"
    )
    assert (
        paths.version_config_path(STRATEGY_ID, VERSION, runtime_root=tmp_path)
        == tmp_path
        / "strategies"
        / STRATEGY_ID
        / "versions"
        / VERSION
        / "config.json"
    )
    assert (
        paths.version_code_path(STRATEGY_ID, VERSION, runtime_root=tmp_path)
        == tmp_path
        / "strategies"
        / STRATEGY_ID
        / "versions"
        / VERSION
        / "code"
        / "signal_engine.py"
    )
    assert (
        paths.manifest_path(STRATEGY_ID, VERSION, runtime_root=tmp_path)
        == tmp_path
        / "strategies"
        / STRATEGY_ID
        / "versions"
        / VERSION
        / "manifest.json"
    )


def test_layout_defaults_to_the_shared_runtime_root() -> None:
    assert paths.strategies_root() == paths.resolve_runtime_root() / "strategies"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "..",
        ".",
        "../escape",
        "/absolute",
        "C:\\absolute",
        "a/b",
        "a\\b",
        "a:b",
        ".hidden",
        "Upper",
        "has space",
        "-leading",
        "_leading",
        "a" * 65,
        "a..b",
        "a\nb",
        "a\x00b",
    ],
)
def test_validate_id_rejects_unsafe_values(bad: str) -> None:
    with pytest.raises(ValueError):
        paths.validate_id(bad, what="strategy_id")


@pytest.mark.parametrize("good", ["a", "a1", "mom-alpha", "mom_alpha.v2", "0", "a" * 64])
def test_validate_id_accepts_safe_slugs(good: str) -> None:
    assert paths.validate_id(good, what="strategy_id") == good


@pytest.mark.parametrize("bad", [None, 7, b"abc", ["a"]])
def test_validate_id_rejects_non_strings(bad: object) -> None:
    with pytest.raises(ValueError):
        paths.validate_id(bad, what="strategy_id")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad",
    ["../etc", "/tmp/evil", "a/b", "a\\b", "a:b", "", ".."],
)
def test_path_helpers_reject_unsafe_strategy_ids(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        paths.spec_path(bad, runtime_root=tmp_path)


@pytest.mark.parametrize("bad", ["../v2", "v2/1", "v2:1", "", "V2"])
def test_version_helpers_validate_the_version(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        paths.version_dir(STRATEGY_ID, bad, runtime_root=tmp_path)


def test_symlinked_strategy_dir_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    (strategies / "evil").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        paths.strategy_dir("evil", runtime_root=tmp_path)
    with pytest.raises(ValueError, match="symlink"):
        paths.spec_path("evil", runtime_root=tmp_path)


def test_symlinked_versions_dir_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    strategy = tmp_path / "strategies" / STRATEGY_ID
    strategy.mkdir(parents=True)
    (strategy / "versions").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        paths.version_dir(STRATEGY_ID, VERSION, runtime_root=tmp_path)


def test_atomic_write_text_roundtrips_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "note.json"
    paths.atomic_write_text(target, '{"summary": "恢复"}')

    assert json.loads(target.read_text(encoding="utf-8")) == {"summary": "恢复"}
    assert [item for item in target.parent.iterdir() if item.name.endswith(".tmp")] == []


def test_atomic_write_text_replaces_previous_content(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("old", encoding="utf-8")
    paths.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


def test_failed_atomic_replace_preserves_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "note.txt"
    target.write_text("old", encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("simulated replace failure")

    monkeypatch.setattr("src.strategy_library.paths.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        paths.atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "old"
    assert [item for item in tmp_path.iterdir() if item.name.endswith(".tmp")] == []


def test_atomic_write_json_is_deterministic_and_utf8(tmp_path: Path) -> None:
    target = tmp_path / "manifest.json"
    paths.atomic_write_json(target, {"b": 1, "a": "中文"})
    text = target.read_text(encoding="utf-8")

    assert text.index('"a"') < text.index('"b"')
    assert json.loads(text) == {"a": "中文", "b": 1}
    assert text.endswith("\n")


def test_file_sha256_matches_hashlib(tmp_path: Path) -> None:
    target = tmp_path / "payload.bin"
    target.write_bytes(b"signal-engine-bytes")
    assert paths.file_sha256(target) == hashlib.sha256(b"signal-engine-bytes").hexdigest()


def test_manifest_records_every_file_sha256(tmp_path: Path) -> None:
    version_dir = paths.version_dir(STRATEGY_ID, VERSION, runtime_root=tmp_path)
    (version_dir / "code").mkdir(parents=True)
    files = {
        "strategy.json": '{"name": "momentum"}',
        "config.json": '{"source": "local"}',
        "code/signal_engine.py": "def generate_signals(ohlcv):\n    return ohlcv\n",
    }
    for relative, content in files.items():
        (version_dir / relative).write_text(content, encoding="utf-8")

    manifest = paths.write_version_manifest(STRATEGY_ID, VERSION, runtime_root=tmp_path)

    assert manifest["strategy_id"] == STRATEGY_ID
    assert manifest["version"] == VERSION
    assert [entry["path"] for entry in manifest["files"]] == sorted(files)
    for entry in manifest["files"]:
        expected = hashlib.sha256(files[entry["path"]].encode("utf-8")).hexdigest()
        assert entry["sha256"] == expected
        assert entry["size"] == len(files[entry["path"]].encode("utf-8"))

    on_disk = json.loads(
        paths.manifest_path(STRATEGY_ID, VERSION, runtime_root=tmp_path).read_text(encoding="utf-8")
    )
    assert on_disk == manifest
    assert "manifest.json" not in [entry["path"] for entry in on_disk["files"]]


def test_manifest_skips_temp_leftovers_and_updates_on_change(tmp_path: Path) -> None:
    version_dir = paths.version_dir(STRATEGY_ID, VERSION, runtime_root=tmp_path)
    version_dir.mkdir(parents=True)
    (version_dir / "strategy.json").write_text("one", encoding="utf-8")
    (version_dir / ".strategy.json.123.tmp").write_text("partial", encoding="utf-8")

    first = paths.write_version_manifest(STRATEGY_ID, VERSION, runtime_root=tmp_path)
    assert [entry["path"] for entry in first["files"]] == ["strategy.json"]

    (version_dir / "strategy.json").write_text("two", encoding="utf-8")
    second = paths.write_version_manifest(STRATEGY_ID, VERSION, runtime_root=tmp_path)
    assert second["files"][0]["sha256"] != first["files"][0]["sha256"]
    assert second["files"][0]["sha256"] == hashlib.sha256(b"two").hexdigest()


def test_manifest_requires_an_existing_version_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        paths.write_version_manifest(STRATEGY_ID, VERSION, runtime_root=tmp_path)
