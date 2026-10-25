"""Proposal-file I/O for strategy scheduling.

Proposals live in their own ``<runtime_root>/scheduled_research/
strategy_proposals/`` directory under the ``ssp_`` id namespace, so the legacy
``srp_`` reader in :mod:`src.scheduled_research.proposals` can never list, load
or commit one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.strategy_library.paths import atomic_write_json, resolve_runtime_root

PROPOSAL_ID_RE = re.compile(r"^ssp_[0-9a-f]{32}$")

__all__ = [
    "PROPOSAL_ID_RE",
    "ProposalFileError",
    "proposal_path",
    "read_proposal",
    "strategy_proposals_dir",
    "write_proposal",
]

_SCHEDULED_DIRNAME = "scheduled_research"
_PROPOSALS_DIRNAME = "strategy_proposals"


class ProposalFileError(LookupError):
    """A proposal file is missing, unreadable or outside the ``ssp_`` namespace."""


def strategy_proposals_dir(*, runtime_root: Path | None = None) -> Path:
    """Return the strategy-proposal directory without creating it."""
    return resolve_runtime_root(runtime_root) / _SCHEDULED_DIRNAME / _PROPOSALS_DIRNAME


def proposal_path(proposal_id: str, *, runtime_root: Path | None) -> Path:
    """Return one proposal's file path, refusing ids outside the namespace."""
    if not isinstance(proposal_id, str) or not PROPOSAL_ID_RE.fullmatch(proposal_id):
        raise ProposalFileError(f"strategy proposal {proposal_id!r} is unknown")
    return strategy_proposals_dir(runtime_root=runtime_root) / f"{proposal_id}.json"


def write_proposal(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write one proposal file inside a 0700 directory."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_json(path, payload)


def read_proposal(path: Path) -> dict[str, Any]:
    """Read one proposal file, raising :class:`ProposalFileError` when unusable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProposalFileError(f"strategy proposal {path.stem!r} is unknown") from exc
    if not isinstance(payload, dict):
        raise ProposalFileError(f"strategy proposal {path.stem!r} is unknown")
    return payload
