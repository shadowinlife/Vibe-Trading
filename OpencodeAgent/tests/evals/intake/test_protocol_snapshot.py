"""Offline digest tests for the frozen ask-first protocol snapshot.

WHY: todo 6 deletes ``AGENTS.md`` lines 66-125 and replaces scenario C with
a one-line pointer. todo 14's ask-first control arm must stay reproducible
from a fixture that cannot silently drift. This suite recomputes each
block's SHA-256 from the snapshot file alone and compares it against both
the digest carried in the block's frontmatter and a constant frozen here.

This test deliberately never opens ``AGENTS.md`` or the skills tree, so it
stays green after the source text is deleted.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "ask_first_protocol_snapshot.md"
)

# Frozen at commit abcaf2629b4537c7e909b607dd99702c860728b8.
AGENTS_PROTOCOL_SHA256 = (
    "598dbd1546f426baff3e890ff39c15b9b25cce6b87f567a297d20973a17f068f"
)
SCENARIO_C_SHA256 = (
    "b23b6da54e1f4b48aea2706144bb2883eb09c6cc3d0fe8dbc2645f12f27bac11"
)

EXPECTED_BLOCKS = {
    "agents-protocol": {
        "source_path": "OpencodeAgent/AGENTS.md",
        "line_range": "66-125",
        "lines": 60,
        "sha256": AGENTS_PROTOCOL_SHA256,
        "markers": ("明确可执行", "开放型", "待澄清型", "宏观型", "最多 1 轮澄清", "绝不重复问第二轮"),
    },
    "research-scenarios-scenario-c": {
        "source_path": "OpencodeAgent/skills/research-scenarios/SKILL.md",
        "line_range": "64-70",
        "lines": 7,
        "sha256": SCENARIO_C_SHA256,
        "markers": ("场景 C：开放性问题",),
    },
}

BLOCK_RE = re.compile(
    r"<!-- block id=(?P<id>[a-z0-9-]+) "
    r"source_path=(?P<source_path>\S+) "
    r"line_range=(?P<line_range>\d+-\d+) "
    r"git_commit=(?P<git_commit>[0-9a-f]{40}) "
    r"sha256=(?P<sha256>[0-9a-f]{64}) -->\n"
    r"```text\n(?P<body>.*?)```",
    re.DOTALL,
)


def load_blocks() -> dict[str, dict[str, str]]:
    text = SNAPSHOT_PATH.read_text(encoding="utf-8")
    blocks: dict[str, dict[str, str]] = {}
    for match in BLOCK_RE.finditer(text):
        assert match.group("id") not in blocks, f"duplicate block id {match.group('id')}"
        blocks[match.group("id")] = match.groupdict()
    return blocks


def digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_snapshot_exists_and_has_only_the_two_known_blocks() -> None:
    assert SNAPSHOT_PATH.is_file(), f"missing snapshot fixture: {SNAPSHOT_PATH}"
    blocks = load_blocks()
    assert set(blocks) == set(EXPECTED_BLOCKS)


def test_block_frontmatter_matches_frozen_sources() -> None:
    blocks = load_blocks()
    for block_id, expected in EXPECTED_BLOCKS.items():
        block = blocks[block_id]
        assert block["source_path"] == expected["source_path"]
        assert block["line_range"] == expected["line_range"]
        assert block["git_commit"] == "abcaf2629b4537c7e909b607dd99702c860728b8"
        assert len(block["body"].splitlines()) == expected["lines"], (
            f"{block_id}: verbatim block lost or gained lines"
        )


def test_block_digests_match_frontmatter_and_hardcoded_constants() -> None:
    blocks = load_blocks()
    for block_id, expected in EXPECTED_BLOCKS.items():
        block = blocks[block_id]
        observed = digest(block["body"])
        assert observed == block["sha256"], (
            f"{block_id}: fixture body no longer matches its recorded digest"
        )
        assert observed == expected["sha256"], (
            f"{block_id}: fixture body no longer matches the frozen constant"
        )


def test_canonical_protocol_markers_exist() -> None:
    blocks = load_blocks()
    for block_id, expected in EXPECTED_BLOCKS.items():
        body = blocks[block_id]["body"]
        for marker in expected["markers"]:
            assert marker in body, f"{block_id}: missing canonical marker {marker!r}"


def test_scenario_c_block_bounds_are_exact() -> None:
    body = load_blocks()["research-scenarios-scenario-c"]["body"]
    lines = body.splitlines()
    assert lines[0] == '## 场景 C：开放性问题（"明天买什么"/"最近有什么机会"）'
    assert lines[-1].startswith("6. "), "scenario C must end at its last numbered item"


def test_snapshot_is_long_enough_to_be_verbatim() -> None:
    assert len(SNAPSHOT_PATH.read_text(encoding="utf-8").splitlines()) >= 60
