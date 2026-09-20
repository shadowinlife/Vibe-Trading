"""Pure helpers for the T8 native-engine parity guard.

Extracted from ``test_opencode_bridge_im_parity.py`` so the git-diff parsing
behind the guard is unit-testable with FABRICATED input (negative controls in
``tests/test_parity_guard.py``) — proving the guard still bites without ever
touching the real working tree.

The guard's claim (T8 acceptance): the opencode engine bridge is
"bridge increment, native path zero touch", so ``VIBE_TRADING_ENGINE=native``
remains a one-key rollback. Zone policy (user adjudication 2026-09-20):

* ``channels/`` ``agent/`` ``providers/`` — strict zero diff, committed AND
  worktree, exactly as the original guard.
* ``session/`` — narrowed to additive-only: the ONLY path that may differ is
  ``agent/src/session/models.py`` and its diff must have zero deleted lines.
  Rationale: the user-auth layer adds ``AuthMethod.USER_SESSION`` (a new enum
  member) and ``Principal.role`` (a new field WITH a default), so no existing
  construction or comparison changes. "Zero deleted lines" is the
  machine-checkable form of "additive only" — removing or rewriting an enum
  member, which is what would actually break native parity, always shows
  ``deleted > 0`` and still fails.
* ``frontend/`` — deliberately NOT guarded here: the user-auth UI is a
  separate feature from the engine bridge, and the T8 parity claim is about
  the native Python engine seam. The frontend has its own gate (``npm run
  build`` + the vitest suite, including the D19 flag-off byte-identical
  assertions). It was originally listed only because the engine-bridge round
  happened to need no UI changes — a different claim, now superseded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

#: Zones that must show ZERO diff vs the merge base, committed AND worktree.
STRICT_ZERO_DIFF_ZONES: Tuple[str, ...] = (
    "agent/src/channels/",
    "agent/src/agent/",
    "agent/src/providers/",
)

#: The narrowed zone and the only path allowed to differ inside it.
SESSION_ZONE = "agent/src/session/"
SESSION_ALLOWED_PATH = "agent/src/session/models.py"


@dataclass(frozen=True)
class PathDiff:
    """One ``git diff --numstat`` row (binary files report ``-`` as 0/0)."""

    path: str
    added: int
    deleted: int


def parse_numstat(output: str) -> List[PathDiff]:
    """Parse ``git diff --numstat`` output into :class:`PathDiff` rows.

    Assumes no rename tracking (the guarded zones never rename; a renamed
    path would simply fail the allowed-path check, which is fail-closed).
    Malformed rows raise ValueError rather than being skipped silently.
    """
    rows: List[PathDiff] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        added_s, deleted_s, path = line.split("\t", 2)
        rows.append(
            PathDiff(
                path=path.strip(),
                added=0 if added_s == "-" else int(added_s),
                deleted=0 if deleted_s == "-" else int(deleted_s),
            )
        )
    return rows


def strict_zone_violations(
    zone: str, committed_names: str, worktree_status: str
) -> List[str]:
    """Violations for a strict zero-diff zone.

    Args:
        zone: The protected path prefix (for messages).
        committed_names: ``git diff --name-only <merge-base>..HEAD -- <zone>``.
        worktree_status: ``git status --porcelain -- <zone>`` (catches staged,
            unstaged AND untracked — a dirty checkout cannot smuggle edits).
    """
    violations: List[str] = []
    if committed_names.strip():
        violations.append(
            f"committed diff in protected zone {zone}:\n{committed_names}"
        )
    if worktree_status.strip():
        violations.append(
            f"uncommitted changes in protected zone {zone}:\n{worktree_status}"
        )
    return violations


def session_zone_violations(
    committed_numstat: str,
    worktree_numstat: str,
    untracked_lines: Sequence[str],
) -> List[str]:
    """Violations for the additive-only ``session/`` zone.

    Args:
        committed_numstat: ``git diff --numstat <merge-base>..HEAD -- <zone>``.
        worktree_numstat: ``git diff --numstat HEAD -- <zone>`` (staged +
            unstaged tracked changes vs HEAD).
        untracked_lines: ``??``-prefixed ``git status --porcelain`` rows for
            the zone — any untracked file is a new path, and the only path
            allowed to differ is ``models.py`` (tracked), so these always
            violate.
    """
    violations: List[str] = []
    for label, output in (
        ("committed", committed_numstat),
        ("uncommitted", worktree_numstat),
    ):
        for row in parse_numstat(output):
            if row.path != SESSION_ALLOWED_PATH:
                violations.append(
                    f"{label} diff at {row.path} — the only path allowed to "
                    f"differ under {SESSION_ZONE} is {SESSION_ALLOWED_PATH}"
                )
            elif row.deleted != 0:
                violations.append(
                    f"{label} diff in {SESSION_ALLOWED_PATH} deletes "
                    f"{row.deleted} line(s) — the diff must be purely "
                    f"additive (zero deleted lines)"
                )
    for line in untracked_lines:
        violations.append(
            f"untracked file under {SESSION_ZONE} (only tracked "
            f"{SESSION_ALLOWED_PATH} edits are allowed):\n{line}"
        )
    return violations
