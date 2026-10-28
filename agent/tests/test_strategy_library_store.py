"""Facade contract: drafts, atomic version bumps, CAS verdicts, snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.strategy_library import paths, store as library_store
from src.strategy_library.history import RunRecord, append_run
from src.strategy_library.spec import StrategySpec
from src.strategy_library.store import (
    DuplicateNameUniverseError,
    EditRequiredError,
    StaleVerdictError,
    canonical_spec_bytes,
    create_draft,
    get,
    list_runs,
    list_strategies,
    record_verdict,
    set_readiness,
    snapshot,
    spec_sha256,
    update_draft,
)
from src.strategy_store.models import ArtifactType, ValidationStatus
from src.strategy_store.sqlite_store import SqliteStrategyStore


def _make_store(tmp_path: Path) -> SqliteStrategyStore:
    return SqliteStrategyStore(tmp_path / "strategy_store.db")


def _workflow_spec(name: str = "Momentum Workflow") -> dict:
    return {
        "name": name,
        "kind": "workflow",
        "thesis": "Buy strength after a breakout confirmed by volume.",
        "universe": "cn_a",
        "inputs": [
            {
                "name": "prices",
                "source": "get_market_data",
                "required": True,
                "missing_policy": "stop",
            }
        ],
        "preconditions": ["the market is open"],
        "steps": [
            {
                "id": "s1",
                "action": "load prices",
                "tool": "get_market_data",
                "output": "price table",
            },
            {
                "id": "s2",
                "action": "score momentum",
                "tool": "technical_indicators",
                "output": "momentum score",
            },
            {
                "id": "s3",
                "action": "summarise",
                "tool": "web_search",
                "output": "markdown table",
            },
        ],
        "output_contract": {
            "format": "markdown_table",
            "required_fields": ["symbol", "score"],
            "destination": "chat",
        },
        "failure_handling": "Report the failing step and stop.",
        "schedule_hint": "weekdays 09:00",
        "evidence_refs": [],
    }


def _code_spec(name: str = "Momentum Code") -> dict:
    return {
        "name": name,
        "kind": "code",
        "thesis": "Cross-sectional momentum on liquid names.",
        "universe": "cn_a",
        "inputs": [{"name": "prices", "source": "backtest", "required": True}],
    }


def _write_code_sources(tmp_path: Path, strategy_id: str) -> None:
    base = paths.strategy_dir(strategy_id, runtime_root=tmp_path)
    (base / "code").mkdir(parents=True, exist_ok=True)
    (base / "config.json").write_text('{"source":"local"}\n', encoding="utf-8")
    (base / "code" / "signal_engine.py").write_text(
        "def generate_signals(frame):\n    return frame\n", encoding="utf-8"
    )


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# Canonical encoding
# ---------------------------------------------------------------------------


def test_canonical_bytes_are_sorted_compact_and_newline_free() -> None:
    payload = {"name": "a", "kind": "code", "thesis": "中文"}
    assert canonical_spec_bytes(payload) == (
        '{"kind":"code","name":"a","thesis":"中文"}'.encode("utf-8")
    )
    assert not canonical_spec_bytes(payload).endswith(b"\n")
    assert spec_sha256(payload) == hashlib.sha256(
        canonical_spec_bytes(payload)
    ).hexdigest()
    with pytest.raises(TypeError):
        canonical_spec_bytes(["not", "a", "mapping"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# create_draft
# ---------------------------------------------------------------------------


def test_create_draft_persists_version_hash_and_spec_file(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    spec = _workflow_spec()

    artifact = create_draft(spec, store=store, runtime_root=tmp_path)

    data = canonical_spec_bytes(spec)
    assert artifact.id.startswith("art_")
    assert artifact.type is ArtifactType.STRATEGY
    assert artifact.kind == "workflow"
    assert artifact.readiness == "draft"
    assert artifact.spec == spec
    assert artifact.spec_sha256 == hashlib.sha256(data).hexdigest()
    assert artifact.artifact_version == f"v1-{artifact.spec_sha256[:8]}"
    assert artifact.demo is False
    assert artifact.run_dir is None

    spec_file = paths.spec_path(artifact.id, runtime_root=tmp_path)
    assert spec_file.read_bytes() == data
    assert paths.file_sha256(spec_file) == artifact.spec_sha256

    from_spec_object = create_draft(
        StrategySpec.from_dict(_workflow_spec("Spec Object")),
        store=store,
        runtime_root=tmp_path,
    )
    assert from_spec_object.spec == _workflow_spec("Spec Object")
    demo_code = create_draft(
        _code_spec(), run_dir="/tmp/run-1", demo=True, store=store, runtime_root=tmp_path
    )
    assert (demo_code.run_dir, demo_code.demo) == ("/tmp/run-1", True)


def test_create_draft_accepts_an_incomplete_spec(tmp_path: Path) -> None:
    """A draft may carry review gaps; only name and kind are required."""
    store = _make_store(tmp_path)
    artifact = create_draft(
        {"name": "Bare Draft", "kind": "workflow"},
        store=store,
        runtime_root=tmp_path,
    )
    assert artifact.spec == {"name": "Bare Draft", "kind": "workflow"}
    assert artifact.universe == ""


@pytest.mark.parametrize(
    "spec",
    [
        {"name": "   ", "kind": "code"},
        {"name": "no kind"},
        {"name": "bad kind", "kind": "prompt"},
    ],
)
def test_create_draft_rejects_unusable_identity(tmp_path: Path, spec: dict) -> None:
    store = _make_store(tmp_path)
    with pytest.raises(ValueError):
        create_draft(spec, store=store, runtime_root=tmp_path)
    assert list_strategies(store=store) == []


def test_create_draft_rejects_a_duplicate_name_universe(tmp_path: Path) -> None:
    """The rejected draft changes nothing; a different universe is a new row."""
    store = _make_store(tmp_path)
    original = create_draft(_workflow_spec("Twin"), store=store, runtime_root=tmp_path)
    spec_file = paths.spec_path(original.id, runtime_root=tmp_path)
    bytes_before = spec_file.read_bytes()

    with pytest.raises(DuplicateNameUniverseError) as rejected:
        create_draft(_workflow_spec("Twin"), store=store, runtime_root=tmp_path)

    assert isinstance(rejected.value, ValueError)
    assert "Twin" in str(rejected.value) and "cn_a" in str(rejected.value)
    assert store.get_artifact(original.id) == original
    assert spec_file.read_bytes() == bytes_before
    strategy_dirs = sorted(
        path.name for path in paths.strategies_root(runtime_root=tmp_path).iterdir()
    )
    assert strategy_dirs == [original.id]

    other_universe = _workflow_spec("Twin")
    other_universe["universe"] = "us"
    clone = create_draft(other_universe, store=store, runtime_root=tmp_path)
    assert clone.id != original.id


# ---------------------------------------------------------------------------
# get / list
# ---------------------------------------------------------------------------


def test_get_returns_none_outside_the_library(tmp_path: Path) -> None:
    assert get("art_missing", store=_make_store(tmp_path)) is None


def test_list_filters_by_kind_readiness_name_and_demo(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    first = create_draft(_workflow_spec("Momentum Alpha"), store=store, runtime_root=tmp_path)
    second = create_draft(_code_spec("Mean Reversion"), store=store, runtime_root=tmp_path)
    third = create_draft(
        _workflow_spec("Momo Demo"), demo=True, store=store, runtime_root=tmp_path
    )

    every = list_strategies(store=store)
    assert [artifact.id for artifact in every] == [third.id, second.id, first.id]

    assert [a.id for a in list_strategies(kind="workflow", store=store, include_demo=False)] == [
        first.id
    ]
    assert [a.id for a in list_strategies(kind="code", store=store)] == [second.id]
    assert [a.id for a in list_strategies(readiness="draft", store=store)] == [
        third.id,
        second.id,
        first.id,
    ]
    assert [a.id for a in list_strategies(q="mom", store=store)] == [
        third.id,
        first.id,
    ]
    assert [a.id for a in list_strategies(q="  MOMENTUM  ", store=store)] == [first.id]
    assert third.id not in [a.id for a in list_strategies(include_demo=False, store=store)]


def test_list_paginates_after_filtering(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    ids = [
        create_draft(_workflow_spec(f"Strategy {index}"), store=store, runtime_root=tmp_path).id
        for index in range(5)
    ]
    newest_first = list(reversed(ids))

    assert [a.id for a in list_strategies(limit=2, store=store)] == newest_first[:2]
    assert [a.id for a in list_strategies(limit=2, offset=2, store=store)] == newest_first[2:4]
    assert list_strategies(limit=0, store=store) == []
    assert list_strategies(offset=99, store=store) == []
    with pytest.raises(ValueError):
        list_strategies(limit=-1, store=store)
    with pytest.raises(ValueError):
        list_strategies(offset=-1, store=store)


def test_list_rejects_unknown_domains(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    with pytest.raises(ValueError):
        list_strategies(kind="prompt", store=store)
    with pytest.raises(ValueError):
        list_strategies(readiness="scheduled", store=store)


# ---------------------------------------------------------------------------
# update_draft: version bump + verdict displacement
# ---------------------------------------------------------------------------


def test_update_draft_bumps_version_and_replaces_readiness_and_verdict(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    verdict = {"verdict": "pass", "summary": "looks executable"}
    ready = record_verdict(
        draft.id,
        verdict,
        expected_version=draft.artifact_version,
        expected_spec_sha256=draft.spec_sha256,
        readiness="ready",
        validation_status="validated",
        store=store,
    )
    assert ready.readiness == "ready"
    assert ready.evaluator_verdict == verdict

    new_spec = _workflow_spec()
    new_spec["thesis"] = "Buy strength, but only above the 200-day average."
    edited = update_draft(draft.id, new_spec, store=store, runtime_root=tmp_path)

    assert edited.readiness == "needs_work"
    assert edited.evaluator_verdict is None
    assert edited.previous_verdict == verdict
    assert edited.validation_status is ValidationStatus.UNVALIDATED
    assert edited.spec == new_spec
    assert edited.spec_sha256 == hashlib.sha256(
        canonical_spec_bytes(new_spec)
    ).hexdigest()
    assert edited.artifact_version == f"v2-{edited.spec_sha256[:8]}"

    spec_file = paths.spec_path(draft.id, runtime_root=tmp_path)
    assert spec_file.read_bytes() == canonical_spec_bytes(new_spec)
    assert paths.file_sha256(spec_file) == edited.spec_sha256


def test_update_draft_keeps_identity_columns(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec("Original Name"), store=store, runtime_root=tmp_path)
    renamed = _workflow_spec("Renamed In Spec")
    edited = update_draft(draft.id, renamed, store=store, runtime_root=tmp_path)
    assert edited.name == "Original Name"
    assert edited.universe == "cn_a"
    assert edited.spec["name"] == "Renamed In Spec"


def test_re_evaluating_the_same_version_overwrites_without_accumulating(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    first = {"verdict": "fail", "summary": "step 2 unclear"}
    second = {"verdict": "pass", "summary": "clarified"}

    record_verdict(
        draft.id,
        first,
        expected_version=draft.artifact_version,
        expected_spec_sha256=draft.spec_sha256,
        readiness="needs_work",
        validation_status="rejected",
        store=store,
    )
    updated = record_verdict(
        draft.id,
        second,
        expected_version=draft.artifact_version,
        expected_spec_sha256=draft.spec_sha256,
        readiness="ready",
        validation_status="validated",
        store=store,
    )

    assert updated.evaluator_verdict == second
    assert updated.previous_verdict is None
    assert updated.artifact_version == draft.artifact_version


def test_an_old_verdict_cannot_land_on_the_new_version(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    verdict = {"verdict": "pass"}
    record_verdict(
        draft.id,
        verdict,
        expected_version=draft.artifact_version,
        expected_spec_sha256=draft.spec_sha256,
        readiness="ready",
        validation_status="validated",
        store=store,
    )
    new_spec = _workflow_spec()
    new_spec["failure_handling"] = "Abort on any tool error."
    edited = update_draft(draft.id, new_spec, store=store, runtime_root=tmp_path)

    with pytest.raises(StaleVerdictError):
        record_verdict(
            draft.id,
            verdict,
            expected_version=draft.artifact_version,
            expected_spec_sha256=draft.spec_sha256,
            readiness="ready",
            validation_status="validated",
            store=store,
        )

    unchanged = store.get_artifact(draft.id)
    assert unchanged is not None
    assert unchanged.artifact_version == edited.artifact_version
    assert unchanged.readiness == "needs_work"
    assert unchanged.evaluator_verdict is None
    assert unchanged.previous_verdict == verdict


def test_update_draft_uses_base_zero_for_unrecognised_versions(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    store.update_artifact(replace(draft, artifact_version="legacy-snapshot"))

    edited = update_draft(draft.id, _workflow_spec(), store=store, runtime_root=tmp_path)

    assert edited.artifact_version.startswith("v1-")
    assert len(edited.artifact_version) == len("v1-") + 8


def test_update_draft_requires_a_re_promotion_for_legacy_rows(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    legacy = store.update_artifact(
        replace(draft, artifact_version=None, spec_sha256=None)
    )
    assert legacy is not None

    new_spec = _workflow_spec()
    new_spec["thesis"] = "changed"
    with pytest.raises(EditRequiredError):
        update_draft(draft.id, new_spec, store=store, runtime_root=tmp_path)

    after = store.get_artifact(draft.id)
    assert after is not None
    assert after.readiness == "needs_work"
    assert after.artifact_version is None
    assert after.spec == draft.spec
    assert paths.spec_path(draft.id, runtime_root=tmp_path).read_bytes() == (
        canonical_spec_bytes(draft.spec)
    )


def test_update_draft_missing_strategy_raises_lookup_error(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    with pytest.raises(LookupError):
        update_draft("art_missing", _workflow_spec(), store=store, runtime_root=tmp_path)


def test_update_draft_losing_a_race_writes_nothing(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    competitor_spec = _workflow_spec()
    competitor_spec["thesis"] = "the competing edit"
    stale_spec = _workflow_spec()
    stale_spec["thesis"] = "the losing edit"

    real_get = store.get_artifact
    raced = {"done": False}

    def stale_read(strategy_id: str):
        row = real_get(strategy_id)
        if not raced["done"]:
            raced["done"] = True
            update_draft(draft.id, competitor_spec, store=store, runtime_root=tmp_path)
        return row

    store.get_artifact = stale_read  # type: ignore[method-assign]
    try:
        with pytest.raises(StaleVerdictError):
            update_draft(draft.id, stale_spec, store=store, runtime_root=tmp_path)
    finally:
        store.get_artifact = real_get  # type: ignore[method-assign]

    winner = real_get(draft.id)
    assert winner is not None
    assert winner.spec == competitor_spec
    assert winner.artifact_version.startswith("v2-")
    assert paths.spec_path(draft.id, runtime_root=tmp_path).read_bytes() == (
        canonical_spec_bytes(competitor_spec)
    )


# ---------------------------------------------------------------------------
# record_verdict / set_readiness
# ---------------------------------------------------------------------------


def test_record_verdict_writes_verdict_and_readiness_together(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    verdict = {"verdict": "pass", "gaps": []}

    updated = record_verdict(
        draft.id,
        verdict,
        expected_version=draft.artifact_version,
        expected_spec_sha256=draft.spec_sha256,
        readiness="ready",
        validation_status="validated",
        store=store,
    )

    assert updated.readiness == "ready"
    assert updated.evaluator_verdict == verdict
    assert updated.validation_status is ValidationStatus.VALIDATED
    assert updated.updated_at >= draft.updated_at


@pytest.mark.parametrize("field", ["version", "sha"])
def test_record_verdict_stale_cas_writes_nothing(tmp_path: Path, field: str) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    expected_version = draft.artifact_version
    expected_sha = draft.spec_sha256
    if field == "version":
        expected_version = "v9-deadbeef"
    else:
        expected_sha = "0" * 64

    with pytest.raises(StaleVerdictError):
        record_verdict(
            draft.id,
            {"verdict": "pass"},
            expected_version=expected_version,
            expected_spec_sha256=expected_sha,
            readiness="ready",
            validation_status="validated",
            store=store,
        )

    unchanged = store.get_artifact(draft.id)
    assert unchanged is not None
    assert unchanged.readiness == "draft"
    assert unchanged.evaluator_verdict is None
    assert unchanged.previous_verdict is None
    assert unchanged.validation_status is ValidationStatus.UNVALIDATED
    assert unchanged.updated_at == draft.updated_at


@pytest.mark.parametrize(
    ("readiness", "status"),
    [
        ("ready", "approved"),
        ("ready", "in_validation"),
        ("scheduled", "validated"),
    ],
)
def test_record_verdict_rejects_out_of_contract_gate_values(
    tmp_path: Path, readiness: str, status: str
) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)

    with pytest.raises(ValueError):
        record_verdict(
            draft.id,
            {"verdict": "pass"},
            expected_version=draft.artifact_version,
            expected_spec_sha256=draft.spec_sha256,
            readiness=readiness,
            validation_status=status,
            store=store,
        )

    unchanged = store.get_artifact(draft.id)
    assert unchanged is not None
    assert unchanged.readiness == "draft"
    assert unchanged.evaluator_verdict is None


def test_record_verdict_accepts_unvalidated_for_inconclusive(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    verdict = {"verdict": "inconclusive"}

    updated = record_verdict(
        draft.id,
        verdict,
        expected_version=draft.artifact_version,
        expected_spec_sha256=draft.spec_sha256,
        readiness="reviewing",
        validation_status=ValidationStatus.UNVALIDATED,
        store=store,
    )

    assert updated.readiness == "reviewing"
    assert updated.validation_status is ValidationStatus.UNVALIDATED
    assert updated.evaluator_verdict == verdict

    with pytest.raises(StaleVerdictError):
        record_verdict(
            draft.id,
            {"verdict": "fail"},
            expected_version="v9-deadbeef",
            expected_spec_sha256=draft.spec_sha256,
            readiness="needs_work",
            validation_status=ValidationStatus.REJECTED,
            store=store,
        )

    unchanged = store.get_artifact(draft.id)
    assert unchanged is not None
    assert unchanged.updated_at == updated.updated_at
    assert unchanged.readiness == "reviewing"
    assert unchanged.validation_status is ValidationStatus.UNVALIDATED
    assert unchanged.evaluator_verdict == verdict


def test_record_verdict_requires_a_mapping(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    with pytest.raises(TypeError):
        record_verdict(
            draft.id,
            "not a verdict",  # type: ignore[arg-type]
            expected_version=draft.artifact_version,
            expected_spec_sha256=draft.spec_sha256,
            readiness="ready",
            validation_status="validated",
            store=store,
        )


def test_set_readiness_flips_only_the_gate(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    verdict = {"verdict": "pass"}
    ready = record_verdict(
        draft.id,
        verdict,
        expected_version=draft.artifact_version,
        expected_spec_sha256=draft.spec_sha256,
        readiness="ready",
        validation_status="validated",
        store=store,
    )

    paused = set_readiness(draft.id, "reviewing", store=store)

    assert paused.readiness == "reviewing"
    assert paused.evaluator_verdict == ready.evaluator_verdict == verdict
    assert paused.validation_status is ValidationStatus.VALIDATED
    assert paused.artifact_version == draft.artifact_version


def test_set_readiness_rejects_unknown_values_and_ids(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    with pytest.raises(ValueError):
        set_readiness(draft.id, "scheduled", store=store)
    with pytest.raises(LookupError):
        set_readiness("art_missing", "ready", store=store)


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------


def test_workflow_snapshot_contains_only_spec_and_manifest(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)

    version = snapshot(draft.id, store=store, runtime_root=tmp_path)

    assert version == draft.artifact_version
    directory = paths.version_dir(draft.id, version, runtime_root=tmp_path)
    assert sorted(_tree(directory)) == ["manifest.json", "strategy.json"]
    assert _tree(directory)["strategy.json"] == canonical_spec_bytes(draft.spec)

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["strategy_id"] == draft.id
    assert manifest["version"] == version
    assert manifest["schema_version"] == 1
    assert [entry["path"] for entry in manifest["files"]] == ["strategy.json"]
    assert manifest["files"][0]["sha256"] == draft.spec_sha256


def test_code_snapshot_carries_config_and_engine(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_code_spec(), store=store, runtime_root=tmp_path)
    _write_code_sources(tmp_path, draft.id)

    version = snapshot(draft.id, store=store, runtime_root=tmp_path)

    directory = paths.version_dir(draft.id, version, runtime_root=tmp_path)
    assert sorted(_tree(directory)) == [
        "code/signal_engine.py",
        "config.json",
        "manifest.json",
        "strategy.json",
    ]
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert [entry["path"] for entry in manifest["files"]] == [
        "code/signal_engine.py",
        "config.json",
        "strategy.json",
    ]


def test_code_snapshot_without_config_leaves_no_version_directory(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_code_spec(), store=store, runtime_root=tmp_path)

    with pytest.raises(FileNotFoundError):
        snapshot(draft.id, store=store, runtime_root=tmp_path)

    versions = paths.versions_dir(draft.id, runtime_root=tmp_path)
    assert list(versions.glob("v*")) == []
    assert list(versions.glob(".tmp-*")) == []


def test_snapshot_is_idempotent_for_the_same_version(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)

    version = snapshot(draft.id, store=store, runtime_root=tmp_path)
    directory = paths.version_dir(draft.id, version, runtime_root=tmp_path)
    before = {
        name: (data, (directory / name).stat().st_ino)
        for name, data in _tree(directory).items()
    }
    assert len(before) == 2

    assert snapshot(draft.id, store=store, runtime_root=tmp_path) == version

    after = _tree(directory)
    assert after == {name: data for name, (data, _) in before.items()}
    assert all((directory / name).stat().st_ino == ino for name, (_, ino) in before.items())


def test_snapshot_repairs_a_drifted_strategy_json(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    spec_file = paths.spec_path(draft.id, runtime_root=tmp_path)
    spec_file.write_text('{"name": "tampered"}', encoding="utf-8")

    version = snapshot(draft.id, store=store, runtime_root=tmp_path)

    assert paths.file_sha256(spec_file) == draft.spec_sha256
    version_spec = paths.version_spec_path(draft.id, version, runtime_root=tmp_path)
    assert version_spec.read_bytes() == canonical_spec_bytes(draft.spec)


def test_published_version_stays_byte_identical_across_an_edit(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_code_spec(), store=store, runtime_root=tmp_path)
    _write_code_sources(tmp_path, draft.id)
    first_version = snapshot(draft.id, store=store, runtime_root=tmp_path)
    first_dir = paths.version_dir(draft.id, first_version, runtime_root=tmp_path)
    before = _tree(first_dir)
    assert len(before) == 4  # spec + config + engine + manifest

    new_spec = _code_spec()
    new_spec["thesis"] = "A refined thesis."
    edited = update_draft(draft.id, new_spec, store=store, runtime_root=tmp_path)
    second_version = snapshot(draft.id, store=store, runtime_root=tmp_path)

    assert second_version == edited.artifact_version != first_version
    assert _tree(first_dir) == before
    second_spec = paths.version_spec_path(draft.id, second_version, runtime_root=tmp_path)
    assert second_spec.read_bytes() == canonical_spec_bytes(new_spec)


def test_snapshot_missing_strategy_raises_lookup_error(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    with pytest.raises(LookupError):
        snapshot("art_missing", store=store, runtime_root=tmp_path)


def test_snapshot_without_a_version_requires_an_edit(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)
    store.update_artifact(replace(draft, artifact_version=None))

    with pytest.raises(EditRequiredError):
        snapshot(draft.id, store=store, runtime_root=tmp_path)


# ---------------------------------------------------------------------------
# run history
# ---------------------------------------------------------------------------


def test_list_runs_merges_job_histories_newest_first(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)

    for index, job_id in enumerate(("sr-run-a", "sr-run-b")):
        append_run(
            RunRecord(
                run_id=f"run-{index}",
                job_id=job_id,
                strategy_id=draft.id,
                strategy_version=draft.artifact_version or "",
                started_at=1000 + index,
                finished_at=2000 + index,
                status="completed",
            ),
            runtime_root=tmp_path,
        )
    append_run(
        RunRecord(
            run_id="run-other",
            job_id="sr-run-c",
            strategy_id="art_someone_else",
            strategy_version="v1-00000000",
            started_at=9999,
            finished_at=9999,
            status="failed",
        ),
        runtime_root=tmp_path,
    )

    records = list_runs(draft.id, 20, runtime_root=tmp_path)

    assert [record.run_id for record in records] == ["run-1", "run-0"]
    assert [record.run_id for record in list_runs(draft.id, 1, runtime_root=tmp_path)] == [
        "run-1"
    ]


def test_public_api_is_exported() -> None:
    expected = {
        "DEFAULT_LIST_LIMIT",
        "DuplicateNameUniverseError",
        "EditRequiredError",
        "StaleVerdictError",
        "canonical_spec_bytes",
        "create_draft",
        "get",
        "list_runs",
        "list_strategies",
        "record_verdict",
        "set_readiness",
        "snapshot",
        "spec_sha256",
        "update_draft",
    }
    assert set(library_store.__all__) == expected
    for name in expected:
        assert hasattr(library_store, name)
