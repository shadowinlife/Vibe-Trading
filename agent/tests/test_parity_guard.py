"""Negative controls for the T8 native-engine parity guard helpers.

Proves the narrowed guard in ``test_opencode_bridge_im_parity.py`` STILL
BITES, using fabricated git output fed to the pure helpers — the real
working tree is never modified and no git process is spawned:

* a diff that DELETES a line from ``agent/src/session/models.py`` must fail
  the additive-only assertion;
* a diff touching any other ``session/`` path must fail;
* a new (untracked) file under ``agent/src/channels/`` must fail the strict
  zero-diff assertion.
"""

from __future__ import annotations

from tests.e2e_engine_bridge.parity_guard import (
    SESSION_ALLOWED_PATH,
    parse_numstat,
    session_zone_violations,
    strict_zone_violations,
)


def test_parse_numstat_rows_and_binary_placeholder() -> None:
    rows = parse_numstat("25\t0\tagent/src/session/models.py\n-\t-\tsome.bin\n\n")
    assert rows[0].path == SESSION_ALLOWED_PATH
    assert (rows[0].added, rows[0].deleted) == (25, 0)
    assert (rows[1].added, rows[1].deleted) == (0, 0)  # binary '-' counts


def test_session_guard_accepts_additive_models_only_diff() -> None:
    """Positive control: the shape of our real user-auth diff must pass."""
    assert session_zone_violations("", "25\t0\tagent/src/session/models.py", []) == []


def test_session_guard_rejects_deleted_line_in_models() -> None:
    """Negative control 1: deleting from models.py breaks native parity."""
    violations = session_zone_violations("5\t2\tagent/src/session/models.py", "", [])
    assert len(violations) == 1
    assert "deletes 2 line(s)" in violations[0]
    assert "purely additive" in violations[0]


def test_session_guard_rejects_any_other_path() -> None:
    """Negative control 2: models.py is the ONLY path that may differ."""
    violations = session_zone_violations("", "3\t0\tagent/src/session/store.py", [])
    assert len(violations) == 1
    assert "agent/src/session/store.py" in violations[0]


def test_session_guard_rejects_untracked_file() -> None:
    violations = session_zone_violations(
        "", "", ["?? agent/src/session/user_models.py"]
    )
    assert len(violations) == 1
    assert "untracked" in violations[0]


def test_strict_zone_rejects_new_untracked_file() -> None:
    """Negative control 3: a new file under channels/ fails zero-diff."""
    violations = strict_zone_violations(
        "agent/src/channels/",
        "",
        "?? agent/src/channels/adapters/smuggled.py",
    )
    assert len(violations) == 1
    assert "uncommitted changes in protected zone agent/src/channels/" in violations[0]


def test_strict_zone_rejects_committed_diff() -> None:
    violations = strict_zone_violations(
        "agent/src/agent/", "agent/src/agent/loop.py", ""
    )
    assert len(violations) == 1
    assert "committed diff" in violations[0]


def test_strict_zone_accepts_clean_outputs() -> None:
    assert strict_zone_violations("agent/src/providers/", "", "") == []
