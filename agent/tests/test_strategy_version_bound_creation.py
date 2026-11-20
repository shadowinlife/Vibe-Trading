"""Bound-owner promotion and scheduling integration for frozen 0003.

The real promotion and scheduling helpers run against a temporary SQLite store
and runtime tree; with no binding the legacy snapshot stays unchanged.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from src.api.strategy_owner_binding import OwnerBindingError
from src.scheduled_research.store import ScheduledResearchJobStore
from src.strategy_discovery.evidence_store import EvidenceStore
from src.strategy_library import paths
from src.strategy_library.promote import promote
from src.strategy_library.scheduling import attach
from src.strategy_library.store import record_verdict, update_draft
from src.strategy_store.sqlite_store import SqliteStrategyStore
from src.strategy_versions.errors import VersionConflictError, VersionNotFoundError
from src.strategy_versions.integration import (
    BoundStrategyError,
    initialize_bound_strategy,
)
from src.strategy_versions.models import RegistrationKey, SourceFacts, VersionState
from src.strategy_versions.projection import LINEAGE_FILENAME, verify_source
from tests import _strategy_bound_support as support
from tests._strategy_bound_support import (
    OTHER_OWNER,
    OTHER_TOKEN,
    OWNER,
    SCHEDULE,
    SIGNAL_ENGINE,
    TIMEZONE,
    code_spec,
    configure_binding,
    files,
    key_for,
    manual_strategy,
    promote_code,
    registration_count,
    registry,
    strategy_root,
    table_exists,
    workflow_spec,
    write_run,
)
from tests._strategy_version_support import tree_state

binding, evidence, store = support.binding, support.evidence, support.store
_clean_binding_environment = support._clean_binding_environment


def test_bound_code_promotion_persists_owner_and_one_pending_identity(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore, binding: str
) -> None:
    # Given a valid deployment binding and a promotable code run.
    # When the strategy is promoted.
    result = promote_code(tmp_path, store)

    # Then the owner is persisted and one pending row holds the random identity.
    assert result.strategy_id is not None
    assert result.gaps == []
    artifact = store.get_artifact(result.strategy_id)
    assert artifact is not None
    assert artifact.owner == OWNER
    assert registration_count(store.db_path) == 1
    pending = registry(store).get(key_for(artifact))
    assert pending is not None
    assert pending.key == key_for(artifact)
    assert pending.state is VersionState.PENDING
    assert pending.version_id.startswith("ver_")
    assert len(pending.version_id) == len("ver_") + 32
    assert artifact.id not in pending.version_id
    assert not paths.version_dir(artifact.id, artifact.artifact_version, runtime_root=tmp_path).exists()


def test_bound_scheduling_activates_exact_source_with_lineage_and_owner(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore, binding: str
) -> None:
    # Given a promoted bound strategy with a pending registration.
    result = promote_code(tmp_path, store)
    assert result.strategy_id is not None
    artifact = store.get_artifact(result.strategy_id)
    assert artifact is not None
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")

    # When it is scheduled.
    attached = attach(
        artifact.id,
        schedule=SCHEDULE,
        timezone=TIMEZONE,
        store=store,
        job_store=jobs,
        runtime_root=tmp_path,
    )

    # Then the row is active with the same random identity and the F9 tree
    # carries exact root bytes plus lineage naming owner, strategy and version.
    active = registry(store).get(key_for(artifact))
    assert active is not None
    assert active.state is VersionState.ACTIVE
    assert active.version_id.startswith("ver_")
    root = strategy_root(artifact.id, tmp_path)
    version_root = paths.version_dir(artifact.id, attached.strategy_version, runtime_root=tmp_path)
    version_files = files(version_root)
    assert version_files["strategy.json"] == (root / "strategy.json").read_bytes()
    assert version_files["config.json"] == (root / "config.json").read_bytes()
    assert version_files["code/signal_engine.py"] == (root / "code" / "signal_engine.py").read_bytes()
    lineage = json.loads(version_files[LINEAGE_FILENAME])
    assert lineage["owner_user_id"] == OWNER
    assert lineage["strategy_id"] == artifact.id
    assert lineage["strategy_version"] == artifact.artifact_version
    assert verify_source(active, tmp_path) == version_files["code/signal_engine.py"]
    assert jobs.get(attached.job_id).strategy_version == artifact.artifact_version


def test_subsequent_version_publishes_while_old_version_is_unchanged(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore, binding: str
) -> None:
    # Given a scheduled bound strategy whose version is active.
    result = promote_code(tmp_path, store)
    assert result.strategy_id is not None
    artifact = store.get_artifact(result.strategy_id)
    assert artifact is not None
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    first = attach(
        artifact.id,
        schedule=SCHEDULE,
        timezone=TIMEZONE,
        store=store,
        job_store=jobs,
        runtime_root=tmp_path,
    )
    first_root = paths.version_dir(artifact.id, first.strategy_version, runtime_root=tmp_path)
    first_tree = files(first_root)
    new_source = SIGNAL_ENGINE + "\n# revised engine\n"

    # When a newer version is edited, its source changes, and it is scheduled.
    updated = update_draft(artifact.id, code_spec(), store=store, runtime_root=tmp_path)
    (strategy_root(artifact.id, tmp_path) / "code" / "signal_engine.py").write_text(new_source, encoding="utf-8")
    record_verdict(
        artifact.id,
        {"verdict": "pass", "summary": "executable"},
        expected_version=updated.artifact_version,
        expected_spec_sha256=updated.spec_sha256,
        readiness="ready",
        validation_status="validated",
        store=store,
    )
    second = attach(
        artifact.id,
        schedule=SCHEDULE,
        timezone=TIMEZONE,
        store=store,
        job_store=jobs,
        runtime_root=tmp_path,
    )

    # Then the new version publishes through the lineage-backed publisher while
    # the old version's directory is untouched and still active.
    assert second.strategy_version != first.strategy_version
    second_active = registry(store).get(RegistrationKey.parse(OWNER, artifact.id, second.strategy_version))
    assert second_active is not None
    assert second_active.state is VersionState.ACTIVE
    second_root = paths.version_dir(artifact.id, second.strategy_version, runtime_root=tmp_path)
    assert (second_root / "code" / "signal_engine.py").read_bytes() == new_source.encode()
    assert files(first_root) == first_tree
    first_active = registry(store).get(RegistrationKey.parse(OWNER, artifact.id, first.strategy_version))
    assert first_active is not None
    assert first_active.state is VersionState.ACTIVE


def test_absent_binding_keeps_legacy_snapshot_without_owner_or_row(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    # Given no deployment binding.
    result = promote_code(tmp_path, store)
    assert result.strategy_id is not None
    artifact = store.get_artifact(result.strategy_id)
    assert artifact is not None
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")

    # When the strategy is scheduled.
    attached = attach(
        artifact.id,
        schedule=SCHEDULE,
        timezone=TIMEZONE,
        store=store,
        job_store=jobs,
        runtime_root=tmp_path,
    )

    # Then the legacy snapshot layout is unchanged, no owner is written and the
    # registry is never created.
    version_files = files(paths.version_dir(artifact.id, attached.strategy_version, runtime_root=tmp_path))
    assert set(version_files) == {
        "strategy.json",
        "config.json",
        "code/signal_engine.py",
        "manifest.json",
    }
    root = strategy_root(artifact.id, tmp_path)
    assert version_files["code/signal_engine.py"] == (root / "code" / "signal_engine.py").read_bytes()
    assert version_files["config.json"] == (root / "config.json").read_bytes()
    assert store.get_artifact(artifact.id).owner is None
    assert registry(store).has_strategy(OWNER, artifact.id) is False
    assert not table_exists(store.db_path)


def test_manual_owner_or_existing_legacy_tree_never_selects_publisher(
    tmp_path: Path, store: SqliteStrategyStore, binding: str
) -> None:
    # Given a manually owned legacy strategy with no registry proof.
    artifact = manual_strategy(tmp_path, store, owner=OWNER)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")

    # When scheduled under a binding, registry evidence decides: none means the
    # legacy snapshot is used and nothing is registered.
    attached = attach(
        artifact.id,
        schedule=SCHEDULE,
        timezone=TIMEZONE,
        store=store,
        job_store=jobs,
        runtime_root=tmp_path,
    )
    first_tree = tree_state(paths.version_dir(artifact.id, attached.strategy_version, runtime_root=tmp_path))
    second = attach(
        artifact.id,
        schedule=SCHEDULE,
        timezone=TIMEZONE,
        store=store,
        job_store=jobs,
        runtime_root=tmp_path,
    )

    # Then no row exists and the existing legacy tree is byte-identical.
    assert second.strategy_version == attached.strategy_version
    assert first_tree == tree_state(paths.version_dir(artifact.id, second.strategy_version, runtime_root=tmp_path))
    assert registry(store).has_strategy(OWNER, artifact.id) is False
    assert not table_exists(store.db_path)
    assert (
        not paths.version_dir(artifact.id, second.strategy_version, runtime_root=tmp_path)
        .joinpath(LINEAGE_FILENAME)
        .exists()
    )


def test_invalid_binding_fails_promotion_before_row_directory_or_registry(
    tmp_path: Path,
    store: SqliteStrategyStore,
    evidence: EvidenceStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a configured binding whose token digest does not match.
    configure_binding(
        tmp_path,
        monkeypatch,
        digest=hashlib.sha256(OTHER_TOKEN.encode("utf-8")).hexdigest(),
    )
    run_dir = write_run(tmp_path)

    # When promotion runs, it fails typed before creating anything.
    with pytest.raises(OwnerBindingError):
        promote(
            code_spec(),
            session_id="bound-session",
            run_dir=str(run_dir),
            store=store,
            runtime_root=tmp_path,
        )

    # Then no artifact row, strategy directory or registry row exists.
    assert store.list_artifacts() == []
    assert not (tmp_path / "strategies").exists()
    assert not table_exists(store.db_path)


def test_demo_and_workflow_promotion_do_not_register_or_set_owner(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore, binding: str
) -> None:
    # Given a valid binding.
    # When a demo code strategy and a workflow strategy are promoted.
    demo = promote_code(tmp_path, store, name="Demo Momentum", demo=True)
    workflow = promote(
        workflow_spec(),
        session_id="bound-session",
        store=store,
        runtime_root=tmp_path,
    )

    # Then neither gets an owner nor any registration.
    demo_artifact = store.get_artifact(demo.strategy_id)
    workflow_artifact = store.get_artifact(workflow.strategy_id)
    assert demo_artifact is not None and demo_artifact.demo is True
    assert demo_artifact.owner is None
    assert workflow_artifact is not None and workflow_artifact.kind == "workflow"
    assert workflow_artifact.owner is None
    assert registry(store).has_strategy(OWNER, demo.strategy_id) is False
    assert registry(store).has_strategy(OWNER, workflow.strategy_id) is False
    assert not table_exists(store.db_path)


def test_bound_initialization_refuses_an_existing_directory_or_registry_row(
    tmp_path: Path, store: SqliteStrategyStore
) -> None:
    # Given a newly created draft that already has a final version directory.
    draft, target = support.draft_with_version_dir(tmp_path, store)

    # When initialization runs, it refuses before writing a row or owner.
    with pytest.raises(BoundStrategyError):
        initialize_bound_strategy(draft, owner=OWNER, store=store, runtime_root=tmp_path)
    assert registry(store).get(key_for(draft)) is None
    assert store.get_artifact(draft.id).owner is None

    # When the directory is gone but a registry row already exists, it refuses
    # again and leaves that identity untouched.
    shutil.rmtree(target)
    pending = registry(store).register_pending(key_for(draft), SourceFacts.from_bytes(SIGNAL_ENGINE.encode()))
    with pytest.raises(BoundStrategyError):
        initialize_bound_strategy(draft, owner=OWNER, store=store, runtime_root=tmp_path)
    assert registry(store).get(key_for(draft)) == pending
    assert store.get_artifact(draft.id).owner is None


def test_failed_bound_publication_leaves_legacy_tree_and_pending_row(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore, binding: str
) -> None:
    # Given a bound promotion whose final version directory is a legacy tree
    # written by something else before scheduling.
    result = promote_code(tmp_path, store)
    assert result.strategy_id is not None
    artifact = store.get_artifact(result.strategy_id)
    assert artifact is not None
    target = paths.version_dir(artifact.id, artifact.artifact_version, runtime_root=tmp_path)
    before = support.write_legacy_tree(target)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")

    # When scheduling attempts a bound publication.
    with pytest.raises(VersionNotFoundError):
        attach(
            artifact.id,
            schedule=SCHEDULE,
            timezone=TIMEZONE,
            store=store,
            job_store=jobs,
            runtime_root=tmp_path,
        )

    # Then the legacy bytes are not substituted and the registration stays
    # pending, so the strategy is not readable as a bound version.
    assert files(target) == before
    row = registry(store).get(key_for(artifact))
    assert row is not None
    assert row.state is VersionState.PENDING
    assert jobs.load() == {}


def test_bound_initialization_rejects_a_cross_owner_pending_claim(tmp_path: Path, store: SqliteStrategyStore) -> None:
    # Given owner A holds a pending claim and no final tree for the F9 path.
    draft = support.create_draft(code_spec(), store=store, runtime_root=tmp_path)
    root = strategy_root(draft.id, tmp_path)
    (root / "code").mkdir(parents=True)
    (root / "config.json").write_text('{"source":"local"}\n', encoding="utf-8")
    (root / "code" / "signal_engine.py").write_text(SIGNAL_ENGINE, encoding="utf-8")
    key_a = RegistrationKey.parse(OWNER, draft.id, draft.artifact_version)
    pending_a = registry(store).register_pending(key_a, SourceFacts.from_bytes(SIGNAL_ENGINE.encode()))
    target = paths.version_dir(draft.id, draft.artifact_version, runtime_root=tmp_path)

    # When owner B initializes the same strategy version.
    key_b = RegistrationKey.parse(OTHER_OWNER, draft.id, draft.artifact_version)
    with pytest.raises(VersionConflictError):
        initialize_bound_strategy(draft, owner=OTHER_OWNER, store=store, runtime_root=tmp_path)

    # Then owner B has no row, the artifact stays unowned and no tree exists.
    assert registry(store).get(key_b) is None
    assert registry(store).get(key_a) == pending_a
    assert store.get_artifact(draft.id).owner is None
    assert not target.exists()
