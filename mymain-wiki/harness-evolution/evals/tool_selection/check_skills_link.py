#!/usr/bin/env python3
"""F3 closure check: .opencode/skills must never silently become a real copy.

The opencode host surface reads skills from ``.opencode/skills``. Locally this
is a symlink to ``agent/src/skills``, which makes content drift structurally
impossible — the "copy" IS the source. The residual failure mode is someone
replacing the symlink with a real directory copy, which this script detects
(and, if a real directory is ever intentional, byte-compares instead).

Exit 0 = healthy (symlink to the source, or byte-identical directory).
Exit 1 = drift / wrong layout, with a diff summary on stdout.

Not wired into the upstream test suite: the symlink is local harness layout
and does not exist in the upstream checkout. Run manually or from local
tooling after touching ``agent/src/skills`` or ``.opencode``.
"""

from __future__ import annotations

import filecmp
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
LINK = REPO_ROOT / ".opencode" / "skills"
SOURCE = REPO_ROOT / "agent" / "src" / "skills"


def _dir_diff(left: Path, right: Path) -> list[str]:
    problems: list[str] = []
    cmp = filecmp.dircmp(left, right)
    problems.extend(f"only in {left}: {n}" for n in cmp.left_only)
    problems.extend(f"only in {right}: {n}" for n in cmp.right_only)
    problems.extend(f"content differs: {n}" for n in cmp.diff_files)
    for sub in cmp.common_dirs:
        problems.extend(_dir_diff(left / sub, right / sub))
    return problems


def main() -> int:
    if not SOURCE.is_dir():
        print(f"FAIL: source {SOURCE} missing")
        return 1
    if LINK.is_symlink():
        target = Path(os.readlink(LINK))
        resolved = (LINK.parent / target).resolve()
        if resolved == SOURCE.resolve():
            print(f"OK: {LINK} -> {target} (symlink; drift structurally impossible)")
            return 0
        print(f"FAIL: symlink points at {resolved}, expected {SOURCE.resolve()}")
        return 1
    if LINK.is_dir():
        problems = _dir_diff(LINK, SOURCE)
        if not problems:
            print(f"OK: {LINK} is a real directory but byte-identical to {SOURCE}")
            return 0
        print(f"FAIL: real directory drifted ({len(problems)} differences):")
        for p in problems[:20]:
            print(f"  {p}")
        return 1
    print(f"FAIL: {LINK} is neither symlink nor directory")
    return 1


if __name__ == "__main__":
    sys.exit(main())
