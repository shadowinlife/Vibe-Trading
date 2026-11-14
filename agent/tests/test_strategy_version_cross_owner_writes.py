"""Cross-owner claims are rejected before any write on every write path."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.strategy_library import paths
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import VersionConflictError
from src.strategy_versions.models import SourceFacts, VersionState
from src.strategy_versions.publisher import PublicationPayload, StrategyVersionPublisher
from tests._strategy_version_support import make_key, tree_state

SPEC_BYTES = b'{"kind":"code","name":"cross owner"}\n'
CONFIG_BYTES = b'{"source":"local"}\n'
SOURCE_BYTES = b"def generate_signals(frame):\n    return frame\n"


def _seed_retry(tmp_path: Path, foreign_state: VersionState) -> tuple[SqliteVersionRegistry, StrategyVersionPublisher]:
    registry = SqliteVersionRegistry(tmp_path / "state" / "strategy_store.db")
    own = make_key(owner="owner-b", strategy_id="momentum", version="v1")
    registry.register_pending(own, SourceFacts.from_bytes(SOURCE_BYTES))
    foreign = make_key(owner="owner-a", strategy_id="momentum", version="v1")
    registry.register_pending(foreign, SourceFacts.from_bytes(SOURCE_BYTES))
    if foreign_state is VersionState.TOMBSTONED:
        registry.tombstone(foreign)
    return registry, StrategyVersionPublisher(registry, tmp_path)


@pytest.mark.parametrize("foreign_state", [VersionState.PENDING, VersionState.TOMBSTONED])
def test_publish_rejects_foreign_claim_on_a_same_owner_retry(tmp_path: Path, foreign_state: VersionState) -> None:
    # Given owner B's own pending retry and a foreign claim on the same
    # strategy id and version that is pending or tombstoned.
    registry, publisher = _seed_retry(tmp_path, foreign_state)
    key_b = make_key(owner="owner-b", strategy_id="momentum", version="v1")

    # When owner B replays its publication.
    with pytest.raises(VersionConflictError):
        publisher.publish(key_b, PublicationPayload(SPEC_BYTES, CONFIG_BYTES, SOURCE_BYTES))

    # Then no staging or final tree was created and owner B's row is unchanged.
    assert not paths.strategy_dir("momentum", runtime_root=tmp_path).exists()
    own = registry.get(key_b)
    assert own is not None
    assert own.state is VersionState.PENDING


def _publisher(tmp_path: Path) -> tuple[SqliteVersionRegistry, StrategyVersionPublisher]:
    registry = SqliteVersionRegistry(tmp_path / "state" / "strategy_store.db")
    return registry, StrategyVersionPublisher(registry, tmp_path)


def _payload() -> PublicationPayload:
    return PublicationPayload(SPEC_BYTES, CONFIG_BYTES, SOURCE_BYTES)


def test_cross_owner_pending_claim_conflicts_before_any_write(tmp_path: Path) -> None:
    # Given owner A holds a pending claim for the shared strategy/version with
    # no final tree.
    registry, publisher = _publisher(tmp_path)
    key_a = make_key(owner="owner-a", strategy_id="momentum", version="v1")
    pending_a = registry.register_pending(key_a, SourceFacts.from_bytes(SOURCE_BYTES))
    before = tree_state(tmp_path / "strategies")

    # When owner B publishes the same strategy id and version.
    key_b = make_key(owner="owner-b", strategy_id="momentum", version="v1")
    with pytest.raises(VersionConflictError):
        publisher.publish(key_b, _payload())

    # Then owner B has no row, no staging or final tree was written, and owner
    # A's pending claim is untouched.
    assert registry.get(key_b) is None
    assert registry.get(key_a) == pending_a
    assert tree_state(tmp_path / "strategies") == before


def test_cross_owner_tombstoned_claim_conflicts_before_any_write(tmp_path: Path) -> None:
    # Given owner A's tombstoned version at the shared strategy/version.
    registry, publisher = _publisher(tmp_path)
    key_a = make_key(owner="owner-a", strategy_id="momentum", version="v1")
    registry.register_pending(key_a, SourceFacts.from_bytes(SOURCE_BYTES))
    tombstoned = registry.tombstone(key_a)
    before = tree_state(tmp_path / "strategies")

    # When owner B publishes the same strategy id and version.
    key_b = make_key(owner="owner-b", strategy_id="momentum", version="v1")
    with pytest.raises(VersionConflictError):
        publisher.publish(key_b, _payload())

    # Then owner B has no row and no staging or final tree was written.
    assert registry.get(key_b) is None
    assert registry.get(key_a) == tombstoned
    assert tree_state(tmp_path / "strategies") == before
