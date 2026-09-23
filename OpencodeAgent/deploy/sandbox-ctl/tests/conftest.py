"""Hermetic env: no host credentials leak into unit tests."""

from __future__ import annotations

import pytest

CREDENTIAL_ENVS = (
    "E2B_API_KEY",
    "E2B_API_KEY_FILE",
    "E2B_DOMAIN",
    "OPENCODE_SERVER_PASSWORD",
    "OPENCODE_SERVER_PASSWORD_FILE",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in CREDENTIAL_ENVS:
        monkeypatch.delenv(var, raising=False)
