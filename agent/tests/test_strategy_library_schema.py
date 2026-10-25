"""Strategy-library schema: new artifact columns, migration and gate writer."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from src.strategy_store import sqlite_store as sqlite_store_module
from src.strategy_store.adaptation import AdaptationPatch, adapt_artifact
from src.strategy_store.models import (
    Artifact,
    ArtifactStatus,
    ArtifactType,
    ValidationStatus,
)
from src.strategy_store.sqlite_store import SqliteStrategyStore

_LIBRARY_COLUMNS = (
    "kind",
    "spec",
    "spec_sha256",
    "readiness",
    "evaluator_verdict",
    "previous_verdict",
    "demo",
)

_SPEC = {
    "name": "gold trend",
    "inputs": [{"name": "universe", "required": True}],
    "steps": [{"action": "rank", "tool": "screen_market"}],
    "output_contract": {"format": "json"},
}

_VERDICT = {"verdict": "pass", "summary": "all must-pass items hold"}
_PREVIOUS = {"verdict": "fail", "summary": "missing inputs"}


def _strategy(**overrides) -> Artifact:
    fields = {
        "id": "",
        "type": ArtifactType.STRATEGY,
        "name": "momentum",
        "universe": "SP500",
        "status": ArtifactStatus.ACTIVE,
    }
    fields.update(overrides)
    return Artifact(**fields)


def _store(tmp_path) -> SqliteStrategyStore:
    return SqliteStrategyStore(tmp_path / "library.db")


def _row(store: SqliteStrategyStore, artifact_id: str) -> dict:
    row = store._conn.execute(
        "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
    ).fetchone()
    return dict(row)


def _ids(store: SqliteStrategyStore, **filters) -> set[str]:
    return {artifact.id for artifact in store.list_artifacts(**filters)}


def test_new_row_defaults_to_code_draft(tmp_path):
    store = _store(tmp_path)
    fetched = store.get_artifact(store.register_artifact(_strategy()))

    assert fetched.kind == "code"
    assert fetched.readiness == "draft"
    assert fetched.demo is False
    assert fetched.spec is None
    assert fetched.spec_sha256 is None
    assert fetched.evaluator_verdict is None
    assert fetched.previous_verdict is None


def test_library_fields_round_trip(tmp_path):
    store = _store(tmp_path)
    aid = store.register_artifact(
        _strategy(
            kind="workflow",
            spec=_SPEC,
            spec_sha256="a" * 64,
            readiness="reviewing",
            evaluator_verdict=_VERDICT,
            previous_verdict=_PREVIOUS,
            demo=True,
            artifact_version="v2-aaaaaaaa",
            run_dir="/runs/parent",
        )
    )
    fetched = store.get_artifact(aid)

    assert fetched.kind == "workflow"
    assert fetched.spec == _SPEC
    assert fetched.spec_sha256 == "a" * 64
    assert fetched.readiness == "reviewing"
    assert fetched.evaluator_verdict == _VERDICT
    assert fetched.previous_verdict == _PREVIOUS
    assert fetched.demo is True
    assert fetched.artifact_version == "v2-aaaaaaaa"
    assert fetched.run_dir == "/runs/parent"


@pytest.mark.parametrize(
    "overrides",
    [{"kind": "prompt"}, {"readiness": "live"}],
)
def test_invalid_kind_or_readiness_rejected(overrides):
    with pytest.raises(ValueError):
        _strategy(**overrides)


def test_rejected_write_leaves_db_reopenable(tmp_path):
    db_path = tmp_path / "library.db"
    store = SqliteStrategyStore(db_path)
    aid = store.register_artifact(_strategy(name="valid"))

    with pytest.raises(ValueError):
        store.register_artifact(_strategy(name="invalid", kind="prompt"))

    reopened = SqliteStrategyStore(db_path)
    assert reopened.get_artifact(aid).name == "valid"


def test_update_artifact_cannot_write_gate_columns(tmp_path):
    store = _store(tmp_path)
    aid = store.register_artifact(_strategy())
    assert (
        store._update_gate_columns(
            aid,
            readiness="ready",
            evaluator_verdict=_VERDICT,
            previous_verdict=_PREVIOUS,
        )
        == 1
    )

    fetched = store.get_artifact(aid)
    updated = store.update_artifact(
        replace(
            fetched,
            name="renamed",
            readiness="draft",
            evaluator_verdict=None,
            previous_verdict=None,
        )
    )

    assert updated.name == "renamed"
    assert updated.readiness == "ready"
    assert updated.evaluator_verdict == _VERDICT
    assert updated.previous_verdict == _PREVIOUS


def test_update_artifact_still_writes_validation_status(tmp_path):
    store = _store(tmp_path)
    aid = store.register_artifact(_strategy())
    fetched = store.get_artifact(aid)

    updated = store.update_artifact(
        replace(fetched, validation_status=ValidationStatus.VALIDATED)
    )

    assert updated.validation_status is ValidationStatus.VALIDATED


def test_gate_writer_changes_only_named_columns(tmp_path, monkeypatch):
    store = _store(tmp_path)
    aid = store.register_artifact(
        _strategy(
            kind="workflow",
            spec=_SPEC,
            spec_sha256="a" * 64,
            readiness="reviewing",
            evaluator_verdict=_VERDICT,
            previous_verdict=_PREVIOUS,
            demo=True,
            artifact_version="v3-aaaaaaaa",
            run_dir="/runs/one",
            developer="Alice",
            owner="Bob",
        )
    )
    before = _row(store, aid)
    monkeypatch.setattr(
        sqlite_store_module, "_now_iso", lambda: "2026-02-02T00:00:00+00:00"
    )

    assert store._update_gate_columns(aid, readiness="ready") == 1

    after = _row(store, aid)
    changed = {column for column in before if before[column] != after[column]}
    assert changed == {"readiness", "updated_at"}
    assert after["readiness"] == "ready"
    assert after["updated_at"] == "2026-02-02T00:00:00+00:00"


def test_gate_writer_clears_verdict_only_when_passed_none(tmp_path):
    store = _store(tmp_path)
    aid = store.register_artifact(
        _strategy(
            readiness="reviewing",
            evaluator_verdict=_VERDICT,
            previous_verdict=_PREVIOUS,
        )
    )

    assert (
        store._update_gate_columns(
            aid, evaluator_verdict=None, previous_verdict=None
        )
        == 1
    )

    fetched = store.get_artifact(aid)
    assert fetched.evaluator_verdict is None
    assert fetched.previous_verdict is None
    assert fetched.readiness == "reviewing"


def test_gate_writer_cas_mismatch_returns_zero_without_writing(tmp_path):
    store = _store(tmp_path)
    aid = store.register_artifact(
        _strategy(artifact_version="v2-aaaaaaaa", spec_sha256="a" * 64)
    )
    before = _row(store, aid)

    assert (
        store._update_gate_columns(
            aid,
            readiness="ready",
            expected_version="v1-old",
            expected_spec_sha256="a" * 64,
        )
        == 0
    )
    assert _row(store, aid) == before

    assert (
        store._update_gate_columns(
            aid,
            readiness="ready",
            expected_version="v2-aaaaaaaa",
            expected_spec_sha256="b" * 64,
        )
        == 0
    )
    assert _row(store, aid) == before

    assert (
        store._update_gate_columns(
            aid,
            readiness="ready",
            expected_version="v2-aaaaaaaa",
            expected_spec_sha256="a" * 64,
        )
        == 1
    )
    assert store.get_artifact(aid).readiness == "ready"


def test_gate_writer_omits_absent_cas_guard(tmp_path):
    store = _store(tmp_path)
    aid = store.register_artifact(_strategy(artifact_version="v1-00000000"))
    assert store.get_artifact(aid).spec_sha256 is None

    assert (
        store._update_gate_columns(
            aid,
            validation_status=ValidationStatus.VALIDATED,
            expected_version="v1-00000000",
        )
        == 1
    )
    assert store.get_artifact(aid).validation_status is ValidationStatus.VALIDATED


def test_gate_writer_unknown_id_returns_zero(tmp_path):
    store = _store(tmp_path)
    assert store._update_gate_columns("art_missing", readiness="ready") == 0


def test_gate_writer_refuses_approved(tmp_path):
    store = _store(tmp_path)
    aid = store.register_artifact(_strategy())

    with pytest.raises(ValueError):
        store._update_gate_columns(aid, validation_status=ValidationStatus.APPROVED)

    assert store.get_artifact(aid).validation_status is ValidationStatus.UNVALIDATED


def test_gate_writer_rejects_unknown_readiness(tmp_path):
    store = _store(tmp_path)
    aid = store.register_artifact(_strategy())

    with pytest.raises(ValueError):
        store._update_gate_columns(aid, readiness="live")


def test_list_artifacts_filters_kind_and_readiness(tmp_path):
    store = _store(tmp_path)
    code_draft = store.register_artifact(_strategy(name="code_draft"))
    workflow_ready = store.register_artifact(
        _strategy(name="flow", kind="workflow", readiness="ready")
    )
    code_ready = store.register_artifact(_strategy(name="code_ready"))
    store._update_gate_columns(code_ready, readiness="ready")

    assert _ids(store, kind="workflow") == {workflow_ready}
    assert _ids(store, readiness="ready") == {workflow_ready, code_ready}
    assert _ids(store, kind="code", readiness="draft") == {code_draft}
    assert _ids(store) == {code_draft, workflow_ready, code_ready}


def test_legacy_row_gains_library_defaults(tmp_path):
    db_path = tmp_path / "legacy.db"
    store = SqliteStrategyStore(db_path)
    aid = store.register_artifact(
        _strategy(name="legacy", artifact_version="v0-old", run_dir="/runs/legacy")
    )
    store._conn.close()

    conn = sqlite3.connect(str(db_path))
    for column in _LIBRARY_COLUMNS:
        conn.execute(f"ALTER TABLE artifacts DROP COLUMN {column}")
    conn.commit()
    conn.close()

    reopened = SqliteStrategyStore(db_path)
    fetched = reopened.get_artifact(aid)

    assert fetched.kind == "code"
    assert fetched.readiness == "draft"
    assert fetched.demo is False
    assert fetched.spec is None
    assert fetched.spec_sha256 is None
    assert fetched.evaluator_verdict is None
    assert fetched.previous_verdict is None
    assert fetched.artifact_version == "v0-old"
    assert fetched.run_dir == "/runs/legacy"


def test_migration_is_idempotent(tmp_path):
    store = SqliteStrategyStore(tmp_path / "idempotent.db")
    store._migrate_artifact_columns()
    store._migrate_artifact_columns()

    columns = [
        row["name"]
        for row in store._conn.execute("PRAGMA table_info(artifacts)")
    ]
    for column in _LIBRARY_COLUMNS:
        assert columns.count(column) == 1


def test_adaptation_resets_the_child_gate_state(tmp_path):
    store = _store(tmp_path)
    parent_id = store.register_artifact(
        _strategy(
            name="parent",
            kind="workflow",
            spec=_SPEC,
            spec_sha256="a" * 64,
            readiness="ready",
            evaluator_verdict=_VERDICT,
            previous_verdict=_PREVIOUS,
            demo=True,
            artifact_version="v4-aaaaaaaa",
            run_dir="/runs/parent",
            validation_status=ValidationStatus.VALIDATED,
            validation_date="2026-01-01",
        )
    )
    parent = store.get_artifact(parent_id)

    child = adapt_artifact(parent, AdaptationPatch(universe="CSI300"))

    assert child.kind == "workflow"
    assert child.derived_from == parent_id
    assert child.readiness == "draft"
    assert child.evaluator_verdict is None
    assert child.previous_verdict is None
    assert child.demo is False
    assert child.spec is None
    assert child.spec_sha256 is None
    assert child.run_dir is None
    assert child.artifact_version is None
    assert child.validation_status is ValidationStatus.UNVALIDATED

    stored = store.get_artifact(store.register_artifact(child))

    assert stored.readiness == "draft"
    assert stored.evaluator_verdict is None
    assert stored.previous_verdict is None
    assert stored.demo is False
    assert stored.spec is None
    assert stored.spec_sha256 is None
    assert stored.run_dir is None
