"""Optional deployment-owner selection for later bound writes."""

from __future__ import annotations

import hashlib
import json
import traceback
from pathlib import Path
from typing import Literal, assert_never

import pytest

from src.api.strategy_owner_binding import (
    OwnerBindingError,
    optional_deployment_owner,
)

TOKEN = "synthetic-service-token-binding-5e2a"
OTHER_TOKEN = "synthetic-other-service-token-91bc"
OWNER = "synthetic-owner-17"
TOKEN_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"
BINDING_ENV = "RM_VT_STRATEGY_OWNER_BINDING_FILE"
BoundWriteFailure = Literal["blank-environment", "invalid-json", "mismatched-token", "missing-token"]


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _write_binding(path: Path, content: bytes | None = None, digest: str | None = None) -> Path:
    payload = {
        "schema_version": "strategy-owner-binding-v1",
        "owner_user_id": OWNER,
        "token_sha256": _digest(TOKEN) if digest is None else digest,
    }
    path.write_bytes(content if content is not None else json.dumps(payload).encode())
    path.chmod(0o600)
    return path


@pytest.fixture
def token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "service-token"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv(TOKEN_ENV, str(path))
    return path


@pytest.fixture
def binding_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "owner-binding.json"
    monkeypatch.setenv(BINDING_ENV, str(path))
    return path


def test_optional_owner_selects_bound_or_legacy_from_environment(
    token_file: Path, binding_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    _write_binding(binding_path)
    bound_owner = optional_deployment_owner()
    monkeypatch.delenv(BINDING_ENV, raising=False)
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    # When
    legacy_owner = optional_deployment_owner()
    # Then
    assert bound_owner == OWNER
    assert legacy_owner is None


@pytest.mark.parametrize("case", ["blank-environment", "invalid-json", "mismatched-token", "missing-token"])
def test_invalid_bound_write_configuration_is_typed_error(
    case: BoundWriteFailure,
    token_file: Path,
    binding_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    match case:
        case "blank-environment":
            monkeypatch.setenv(BINDING_ENV, "  ")
        case "invalid-json":
            _write_binding(binding_path, content=b"{")
        case "mismatched-token":
            _write_binding(binding_path, digest=_digest(OTHER_TOKEN))
        case "missing-token":
            _write_binding(binding_path)
            monkeypatch.delenv(TOKEN_ENV, raising=False)
        case unreachable:
            assert_never(unreachable)
    # When / Then
    with pytest.raises(OwnerBindingError) as caught:
        optional_deployment_owner()
    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert str(caught.value) == "invalid deployment owner binding"
    for sensitive in (
        TOKEN,
        _digest(TOKEN),
        _digest(OTHER_TOKEN),
        str(token_file),
        str(binding_path),
    ):
        assert sensitive not in rendered
