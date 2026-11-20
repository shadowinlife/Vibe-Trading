"""Cross-process shared and exclusive strategy-version lock contracts."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from src.strategy_versions.locking import (
    exclusive_version_lock,
    shared_version_lock,
    version_lock_path,
)


def _child_lock_attempt(repo_root: Path, runtime_root: Path, kind: str) -> str:
    script = f"""
from src.strategy_versions.errors import VersionLockUnavailable
from src.strategy_versions.locking import {kind}_version_lock
try:
    with {kind}_version_lock({str(runtime_root)!r}, blocking=False):
        print("acquired")
except VersionLockUnavailable:
    print("blocked")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "agent")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return completed.stdout.strip()


def test_shared_lock_does_not_create_a_missing_lock_or_parent(tmp_path: Path) -> None:
    # Given no runtime root or lock file.
    runtime_root = tmp_path / "missing"

    # When a reader takes the shared lock.
    with shared_version_lock(runtime_root):
        pass

    # Then the read-only path has created nothing.
    assert not version_lock_path(runtime_root).exists()
    assert not runtime_root.exists()


def test_writer_lock_is_private_and_excludes_other_processes(tmp_path: Path) -> None:
    # Given a writer holding the exclusive lock.
    repo_root = Path(__file__).resolve().parents[2]
    with exclusive_version_lock(tmp_path):
        # When other processes request either lock without waiting.
        exclusive = _child_lock_attempt(repo_root, tmp_path, "exclusive")
        shared = _child_lock_attempt(repo_root, tmp_path, "shared")

        # Then both are refused and the lock itself is owner-only.
        assert exclusive == "blocked"
        assert shared == "blocked"
        assert stat.S_IMODE(version_lock_path(tmp_path).stat().st_mode) == 0o600


def test_shared_locks_coexist_but_still_exclude_writers(tmp_path: Path) -> None:
    # Given a persisted writer-created lock and a held shared lock.
    with exclusive_version_lock(tmp_path):
        pass
    repo_root = Path(__file__).resolve().parents[2]
    with shared_version_lock(tmp_path):
        # When another reader and a writer request the lock.
        reader = _child_lock_attempt(repo_root, tmp_path, "shared")
        writer = _child_lock_attempt(repo_root, tmp_path, "exclusive")

        # Then only the reader can proceed.
        assert reader == "acquired"
        assert writer == "blocked"

    # When the last reader leaves, a writer can enter.
    assert _child_lock_attempt(repo_root, tmp_path, "exclusive") == "acquired"
