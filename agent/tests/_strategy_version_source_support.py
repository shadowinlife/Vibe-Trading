"""Fixtures for the authenticated strategy-version source route tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.models import RegistrationKey, VersionRegistration
from src.strategy_versions.publisher import (
    PublicationPayload,
    StrategyVersionPublisher,
)
from src.strategy_versions.reader import StrategyVersionSource, StrategyVersionSourceReader

URL = "/strategy-version-source"
TOKEN = "synthetic-service-token-0003-source-6f2a"
OTHER_TOKEN = "synthetic-other-token-0003-source-91bc"
OWNER = "synthetic-owner-0003-source"
TOKEN_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"
BINDING_ENV = "RM_VT_STRATEGY_OWNER_BINDING_FILE"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FIELDS = frozenset(
    {
        "caller_scope",
        "contract_version",
        "owner_user_id",
        "source_byte_length",
        "source_bytes_base64",
        "source_encoding",
        "source_path",
        "source_sha256",
        "strategy_id",
        "strategy_version",
        "version_id",
    }
)

SPEC_BYTES = '{"kind":"code","name":"精确读取"}\n'.encode("utf-8")
CONFIG_BYTES = '{"source":"local","note":"中文"}\r\n'.encode("utf-8")
SOURCE_BYTES = ("\ufeff# 中文注释\r\n" "def generate_signals(frame):\n" "    return frame\r\n").encode("utf-8")


def body(strategy_id: str = "momentum", version: str = "v7", **extra: object) -> dict[str, object]:
    """Return a request body with the two required fields plus overrides."""
    payload: dict[str, object] = {
        "strategy_id": strategy_id,
        "strategy_version": version,
    }
    payload.update(extra)
    return payload


def digest(token: str) -> str:
    """Return the lowercase SHA-256 of a synthetic token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def binding_bytes(owner: str, token: str = TOKEN) -> bytes:
    """Return a valid deployment-binding document for *owner*."""
    return json.dumps(
        {
            "schema_version": "strategy-owner-binding-v1",
            "owner_user_id": owner,
            "token_sha256": digest(token),
        }
    ).encode("utf-8")


class CountingReader:
    """Record every lookup that actually reaches the reader seam."""

    def __init__(self, inner: StrategyVersionSourceReader, harness: "Harness") -> None:
        self._inner = inner
        self._harness = harness

    def read(self, key: RegistrationKey, expected_source_sha256: str | None = None) -> StrategyVersionSource:
        self._harness.lookups += 1
        return self._inner.read(key, expected_source_sha256)


class Harness:
    """Binding/token environment plus a real publisher and reader."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = tmp_path
        self.db_path = tmp_path / "state" / "strategy_store.db"
        self.token_path = tmp_path / "service-token"
        self.binding_path = tmp_path / "owner-binding.json"
        self.publisher = StrategyVersionPublisher(SqliteVersionRegistry(self.db_path), tmp_path)
        self.provider_calls = 0
        self.lookups = 0
        self._monkeypatch = monkeypatch
        self.bind(OWNER)
        monkeypatch.setenv(TOKEN_ENV, str(self.token_path))

    def bind(
        self,
        owner: str,
        *,
        content: bytes | None = None,
        token: str = TOKEN,
    ) -> None:
        """Write a 0600 token mount and binding document for *owner*."""
        self.token_path.write_text(token + "\n", encoding="utf-8")
        self.token_path.chmod(0o600)
        self.binding_path.write_bytes(content or binding_bytes(owner, token))
        self.binding_path.chmod(0o600)
        self._monkeypatch.setenv(BINDING_ENV, str(self.binding_path))

    def provider(self) -> CountingReader:
        """Return a reader over the real registry, counting construction."""
        self.provider_calls += 1
        registry = SqliteVersionRegistry(self.db_path, read_only=True)
        return CountingReader(StrategyVersionSourceReader(registry, self.root), self)

    def publish(
        self,
        *,
        strategy_id: str = "momentum",
        version: str = "v7",
        owner: str = OWNER,
    ) -> tuple[RegistrationKey, VersionRegistration]:
        """Publish one active version and return its key and registration."""
        key = RegistrationKey.parse(owner, strategy_id, version)
        active = self.publisher.publish(key, PublicationPayload(SPEC_BYTES, CONFIG_BYTES, SOURCE_BYTES))
        return key, active
