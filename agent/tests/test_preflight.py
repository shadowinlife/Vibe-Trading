"""Tests for startup preflight checks."""

from __future__ import annotations

import sys

import requests

from src import preflight


def _configure_llm_preflight(monkeypatch) -> None:
    """Install a minimal OpenAI-compatible provider environment for preflight tests."""
    from src.providers import settings, llm as llm_module

    monkeypatch.setenv("LANGCHAIN_PROVIDER", "openai")
    monkeypatch.setenv("LANGCHAIN_MODEL_NAME", "gpt-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-preflight-dummy")
    monkeypatch.setattr(settings, "_ensure_dotenv", lambda: None)
    monkeypatch.setattr(settings, "_sync_provider_env", lambda: None)
    monkeypatch.setattr(
        llm_module,
        "provider_diagnostics",
        lambda: {
            "base_url": "https://example.test/v1",
            "timeout_seconds": 120,
            "max_retries": 2,
            "proxy": {},
        },
    )


def test_llm_preflight_probe_does_not_follow_redirects(monkeypatch) -> None:
    """A redirect response still proves the HTTPS provider base is reachable."""
    _configure_llm_preflight(monkeypatch)
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_get(url: str, **kwargs: object) -> object:
        calls.append((url, kwargs))
        response = requests.Response()
        response.status_code = 307
        return response

    monkeypatch.setattr(requests, "get", fake_get)

    result = preflight._check_llm_provider()

    assert result.status == "ready"
    assert calls == [
        (
            "https://example.test",
            {
                "timeout": 10,
                "allow_redirects": False,
            },
        )
    ]


def test_llm_preflight_probe_reports_request_errors(monkeypatch) -> None:
    """Request failures remain critical errors for the LLM provider check."""
    _configure_llm_preflight(monkeypatch)

    def fake_get(url: str, **kwargs: object) -> object:
        del url, kwargs
        raise requests.Timeout("timed out")

    monkeypatch.setattr(requests, "get", fake_get)

    result = preflight._check_llm_provider()

    assert result.status == "error"
    assert result.critical is True
    assert "Timeout: timed out" in result.message


def test_copilot_preflight_uses_sdk_auth_instead_of_openai_base_url(
    monkeypatch,
) -> None:
    """Copilot has no OpenAI base URL because the official SDK owns transport."""
    import src.providers.copilot_auth as copilot_auth
    from src.providers import settings, llm as llm_module

    monkeypatch.setenv("LANGCHAIN_PROVIDER", "copilot")
    monkeypatch.setenv("LANGCHAIN_MODEL_NAME", "claude-sonnet-5")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setattr(settings, "_ensure_dotenv", lambda: None)
    monkeypatch.setattr(settings, "_sync_provider_env", lambda: None)
    monkeypatch.setattr(
        llm_module,
        "provider_diagnostics",
        lambda: {
            "base_url": "https://api.githubcopilot.com",
            "timeout_seconds": 120,
            "max_retries": 2,
            "proxy": {},
        },
    )
    monkeypatch.setattr(
        copilot_auth,
        "get_copilot_auth_status",
        lambda: (True, "sykuang (via gh)"),
        raising=False,
    )

    result = preflight._check_llm_provider()

    assert result.status == "ready"
    assert result.critical is False
    assert "sykuang (via gh)" in result.message


def test_akshare_check_uses_spec_without_import(monkeypatch) -> None:
    """AKShare's package import is heavy; preflight should only check discovery."""
    monkeypatch.delitem(sys.modules, "akshare", raising=False)
    monkeypatch.setattr(preflight, "find_spec", lambda name: object() if name == "akshare" else None)

    result = preflight._check_akshare()

    assert result.status == "ready"
    assert result.message == "installed"
    assert "akshare" not in sys.modules


def test_akshare_check_skips_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(preflight, "find_spec", lambda name: None)

    result = preflight._check_akshare()

    assert result.status == "skipped"
    assert result.message == "package not installed"


def test_content_filter_threshold_check(monkeypatch) -> None:
    """Content Filter Threshold row must appear in preflight output."""
    monkeypatch.setenv("CONTENT_FILTER_WARNING_THRESHOLD", "0.10")

    result = preflight._check_content_filter_threshold()

    assert result.name == "Content Filter Threshold"
    assert result.status == "ready"
    assert "10%" in result.message
    assert "CONTENT_FILTER_WARNING_THRESHOLD" in result.message


def test_content_filter_threshold_default(monkeypatch) -> None:
    """Default threshold is 5% when env var is unset."""
    monkeypatch.delenv("CONTENT_FILTER_WARNING_THRESHOLD", raising=False)

    result = preflight._check_content_filter_threshold()

    assert result.name == "Content Filter Threshold"
    assert result.status == "ready"
    assert "5%" in result.message


def _configure_provider_preflight(
    monkeypatch, *, provider: str, model: str, base_url: str, base_env: str | None = None
) -> None:
    """Install a keyless provider environment for preflight tests."""
    from src.providers import settings, llm as llm_module

    monkeypatch.setenv("LANGCHAIN_PROVIDER", provider)
    monkeypatch.setenv("LANGCHAIN_MODEL_NAME", model)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    if base_env is not None:
        monkeypatch.setenv(base_env, base_url)
    monkeypatch.setattr(settings, "_ensure_dotenv", lambda: None)
    monkeypatch.setattr(settings, "_sync_provider_env", lambda: None)
    monkeypatch.setattr(
        llm_module,
        "provider_diagnostics",
        lambda: {
            "base_url": base_url,
            "timeout_seconds": 120,
            "max_retries": 2,
            "proxy": {},
        },
    )


def _dashscope_preflight(monkeypatch) -> None:
    _configure_provider_preflight(
        monkeypatch,
        provider="dashscope",
        model="qwen-test",
        base_url="https://dashscope.example.test/compatible-mode/v1",
        base_env="DASHSCOPE_BASE_URL",
    )


def test_dashscope_preflight_fails_fast_without_key(monkeypatch) -> None:
    """A reachable endpoint without a credential must not report ready."""
    _dashscope_preflight(monkeypatch)

    def fail_get(url: str, **kwargs: object) -> object:
        del url, kwargs
        raise AssertionError("preflight must fail on the missing key before pinging")

    monkeypatch.setattr(requests, "get", fail_get)

    result = preflight._check_llm_provider()

    assert result.status == "not_configured"
    assert result.critical is True
    assert "DASHSCOPE_API_KEY" in result.message


def test_dashscope_preflight_ready_with_provider_key(monkeypatch) -> None:
    _dashscope_preflight(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope-dummy")
    response = requests.Response()
    response.status_code = 200
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: response)

    result = preflight._check_llm_provider()

    assert result.status == "ready"


def test_dashscope_preflight_accepts_generic_openai_fallback(monkeypatch) -> None:
    """The catalog fallback chain (DASHSCOPE_API_KEY -> OPENAI_API_KEY) stays valid."""
    _dashscope_preflight(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fallback-dummy")
    response = requests.Response()
    response.status_code = 200
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: response)

    result = preflight._check_llm_provider()

    assert result.status == "ready"


def test_ollama_preflight_requires_no_key(monkeypatch) -> None:
    """Keyless local providers get the 'ollama' placeholder, not a false failure."""
    _configure_provider_preflight(
        monkeypatch,
        provider="ollama",
        model="llama3-test",
        base_url="http://localhost:11434/v1",
    )
    response = requests.Response()
    response.status_code = 200
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: response)

    result = preflight._check_llm_provider()

    assert result.status == "ready"
