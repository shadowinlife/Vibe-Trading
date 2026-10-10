"""Config assembly tests for the OpencodeAgent container wiring.

Covers the correctness of the startup config pipeline that used to be
inlined in ``entrypoint.sh``:

- ``opencode.json.tmpl`` renders to valid JSON with both MCP servers.
- ``vibe-trading-tools.json`` (tool governance manifest) entries compile
  into opencode ``permission`` deny entries, so disabled VT tools are
  removed from the model's visible tool surface.
- Per-agent tool scoping keeps finance MCP tools away from agents that do
  not need them (explore / multimodal-looker).
- ``oh-my-openagent.json`` model tiering invariants (cheap agents stay
  cheap, deep agents stay on the max model).
- ``AGENTS.md`` stays within the always-loaded context budget.
- The ``research-scenarios`` skill keeps its frontmatter + playbook body.
- Env-value hardening (incident 2026-10-10): poisoned env values (trailing
  newline / quote / tab / unicode / backslash) never break the JSON render,
  and the static fallback config keeps the VT MCP registration alive when
  rendering fails anyway.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

OPENCODE_AGENT_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = OPENCODE_AGENT_DIR / "config"
TEMPLATE_PATH = CONFIG_DIR / "opencode.json.tmpl"
MANIFEST_PATH = CONFIG_DIR / "vibe-trading-tools.json"
FALLBACK_PATH = CONFIG_DIR / "opencode.json.fallback"
ENTRYPOINT_PATH = OPENCODE_AGENT_DIR / "entrypoint.sh"
AGENTS_MD_PATH = OPENCODE_AGENT_DIR / "AGENTS.md"
SCENARIOS_SKILL_PATH = OPENCODE_AGENT_DIR / "skills" / "research-scenarios" / "SKILL.md"

# Always-loaded instruction budget: every AGENTS.md line is paid on every
# turn, so growth must be deliberate. Scenario playbooks live in the
# research-scenarios skill instead.
AGENTS_MD_MAX_LINES = 450

# Captured verbatim from the v3.3.0 template (pre-proxy) rendered with the
# fixture env below — contract A.7 requires direct mode to stay byte-identical
# so the image can ship before the RM proxy exists.
DIRECT_MODE_GOLDEN_V330 = """\
{
  "$schema": "https://opencode.ai/config.json",
  "model": "alibaba-cn/qwen3.8-max",
  "tool_output": {
    "max_bytes": 262144,
    "max_lines": 8000
  },
  "mcp": {
    "vibe-trading": {
      "type": "local",
      "command": [
        "/opt/venv/bin/python",
        "/opt/vibe-trading/agent/mcp_server.py"
      ],
      "timeout": 600000,
      "env": {
        "CLICKHOUSE_HOST": "ch.test",
        "CLICKHOUSE_PORT": "8123",
        "CLICKHOUSE_USER": "default",
        "CLICKHOUSE_PASSWORD": "secret",
        "CLICKHOUSE_DATABASE": "ashare",
        "CLICKHOUSE_LLM_USER": "llm_role",
        "CLICKHOUSE_LLM_PASSWORD": "llm_secret",
        "VT_MEMORY": "full",
        "VT_MEMORY_MCP_TOOLS": "1",
        "VT_MEMORY_BASE_DIR": "/workspace/.vt-memory"
      }
    }
  },
  "agent": {
    "explore": {
      "permission": {
        "vibe-trading_*": "deny"
      }
    },
    "multimodal-looker": {
      "permission": {
        "vibe-trading_*": "deny"
      }
    }
  },
  "plugin": [
    "oh-my-openagent@5.1.0"
  ],
  "permission": {
    "vibe-trading_trading_*": "deny",
    "vibe-trading_qveris_*": "deny",
    "vibe-trading_iwencai_search": "deny"
  }
}
"""


def _load_render_config():
    spec = importlib.util.spec_from_file_location(
        "opencode_agent_render_config", CONFIG_DIR / "render_config.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_config = _load_render_config()


@pytest.fixture(autouse=True)
def _clickhouse_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("CLICKHOUSE_"):
            monkeypatch.delenv(key, raising=False)
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "HOSTNAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLICKHOUSE_HOST", "ch.test")
    monkeypatch.setenv("CLICKHOUSE_PORT", "8123")
    monkeypatch.setenv("CLICKHOUSE_USER", "default")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "secret")
    monkeypatch.setenv("CLICKHOUSE_DATABASE", "ashare")
    monkeypatch.setenv("CLICKHOUSE_LLM_USER", "llm_role")
    monkeypatch.setenv("CLICKHOUSE_LLM_PASSWORD", "llm_secret")


def _rendered() -> dict:
    return json.loads(render_config.render(TEMPLATE_PATH, MANIFEST_PATH))


class TestTemplateRender:
    def test_renders_valid_json_with_vt_mcp_server(self):
        config = _rendered()
        assert list(config["mcp"]) == ["vibe-trading"]
        assert config["mcp"]["vibe-trading"]["type"] == "local"

    def test_clickhouse_credentials_reach_vt_mcp_env(self):
        vt_env = _rendered()["mcp"]["vibe-trading"]["env"]
        assert vt_env["CLICKHOUSE_HOST"] == "ch.test"
        assert vt_env["CLICKHOUSE_LLM_USER"] == "llm_role"
        assert vt_env["VT_MEMORY"] == "full"
        assert vt_env["VT_MEMORY_MCP_TOOLS"] == "1"

    def test_no_unrendered_jinja_placeholders(self):
        raw = render_config.render(TEMPLATE_PATH, MANIFEST_PATH)
        assert "{{" not in raw
        assert "}}" not in raw

    def test_long_running_tool_support(self):
        """Minute-long backtests must survive opencode's MCP call timeout, and
        structured artifact pages must not be truncated mid-JSON.

        ``timeout`` is the per-server MCP request timeout in ms — opencode's
        SDK default is 60s, which kills any longer backtest; the backtest
        tool's progress heartbeats reset it, and 600s bounds a silent stall.
        ``tool_output`` raises opencode's 50KB/2000-line tool-result
        truncation (which spills the tail to a file and hands the model
        broken JSON) above read_run_artifact's 120K-char page budget.
        """
        config = _rendered()
        assert config["mcp"]["vibe-trading"]["timeout"] == 600000
        assert config["tool_output"] == {"max_bytes": 262144, "max_lines": 8000}


class TestLlmProxyRender:
    """Contract A.7 (Phase 9a): the provider block renders ONLY when
    LLM_BASE_URL is set; direct mode stays byte-identical to v3.3.0 so the
    image can ship before the RM proxy. Provider ID must stay ``alibaba-cn``
    (opencode hardcodes enable_thinking injection against that ID)."""

    LLM_BASE_URL = "http://resource-manager.default.svc.cluster.local:8900/llm/v1"
    LLM_API_KEY = "pool-fake-token"

    def test_direct_mode_matches_v330_golden_byte_for_byte(self):
        raw = render_config.render(TEMPLATE_PATH, MANIFEST_PATH)
        json.loads(raw)
        assert raw == DIRECT_MODE_GOLDEN_V330

    def test_proxy_mode_renders_provider_block(self, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", self.LLM_BASE_URL)
        monkeypatch.setenv("LLM_API_KEY", self.LLM_API_KEY)
        monkeypatch.setenv("HOSTNAME", "opencode-poc-smoke1")
        raw = render_config.render(TEMPLATE_PATH, MANIFEST_PATH)
        config = json.loads(raw)
        options = config["provider"]["alibaba-cn"]["options"]
        assert options["baseURL"] == self.LLM_BASE_URL
        assert options["apiKey"] == self.LLM_API_KEY
        assert options["headers"] == {"x-sandbox-id": "opencode-poc-smoke1"}
        assert config["model"] == "alibaba-cn/qwen3.8-max"
        assert "{{" not in raw
        assert "}}" not in raw

    def test_sandbox_id_prefers_hostname_env(self, monkeypatch):
        monkeypatch.setenv("HOSTNAME", "pod-name-123")
        assert render_config.build_context()["SANDBOX_ID"] == "pod-name-123"

    def test_sandbox_id_defaults_to_nodename_when_hostname_unset(self):
        assert render_config.build_context()["SANDBOX_ID"] == os.uname().nodename


class TestToolGovernanceManifest:
    def test_manifest_schema(self):
        manifest = render_config.load_manifest(MANIFEST_PATH)
        disabled = manifest["disabled"]
        assert isinstance(disabled, list) and disabled
        assert all(isinstance(entry, str) and entry for entry in disabled)

    def test_trading_surface_stays_denied(self):
        manifest = render_config.load_manifest(MANIFEST_PATH)
        assert "trading_*" in manifest["disabled"]

    def test_paid_surface_stays_denied(self):
        """Paid/key-gated surfaces are schema tax without credentials: the
        QVeris marketplace (billable execution) and iWenCai (commercial key)
        stay denied until keys are provisioned in the container .env."""
        manifest = render_config.load_manifest(MANIFEST_PATH)
        assert "qveris_*" in manifest["disabled"]
        assert "iwencai_search" in manifest["disabled"]

    def test_disabled_entries_compile_to_permission_denies(self):
        config = _rendered()
        permission = config["permission"]
        manifest = render_config.load_manifest(MANIFEST_PATH)
        for entry in manifest["disabled"]:
            key = f"{render_config.VT_SERVER}_{entry}"
            assert permission[key] == "deny", key

    def test_invalid_manifest_fails_loud(self, tmp_path):
        bad = tmp_path / "bad-manifest.json"
        bad.write_text(json.dumps({"disabled": ["ok", 42]}), encoding="utf-8")
        with pytest.raises(ValueError):
            render_config.render(TEMPLATE_PATH, bad)

    def test_non_object_template_output_rejected(self, tmp_path):
        bad_tmpl = tmp_path / "list.json.tmpl"
        bad_tmpl.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError):
            render_config.render(bad_tmpl, MANIFEST_PATH)


class TestPerAgentToolScoping:
    @pytest.mark.parametrize("agent_name", ["explore", "multimodal-looker"])
    def test_finance_mcp_tools_denied_for_non_finance_agents(self, agent_name):
        permission = _rendered()["agent"][agent_name]["permission"]
        assert permission["vibe-trading_*"] == "deny"


class TestOmoModelConfig:
    """Two-tier model policy (aligned with the maintainer's local OMO roster):
    qwen3.8-max is the primary model for every reasoning-heavy role; the
    high-frequency, low-complexity, latency-sensitive roles (explore agent,
    quick / unspecified-low categories) run on qwen3.8-flash. multimodal-looker
    stays on the primary tier: qwen3.8-max is verified multimodal, flash is
    not."""

    PRIMARY_MODEL = "alibaba-cn/qwen3.8-max"
    LIGHT_MODEL = "alibaba-cn/qwen3.8-flash"
    LIGHT_AGENTS = {"explore"}
    LIGHT_CATEGORIES = {"quick", "unspecified-low"}

    def _omo_config(self) -> dict:
        with open(CONFIG_DIR / "oh-my-openagent.json", encoding="utf-8") as f:
            return json.load(f)

    def test_agents_follow_the_two_tier_policy(self):
        omo = self._omo_config()
        assert omo["agents"], "agents section must not be empty"
        for agent_name, spec in omo["agents"].items():
            if agent_name in self.LIGHT_AGENTS:
                assert spec["model"] == self.LIGHT_MODEL, agent_name
                assert spec["reasoningEffort"] == "medium", agent_name
            else:
                assert spec["model"] == self.PRIMARY_MODEL, agent_name
                assert spec["reasoningEffort"] == "max", agent_name

    def test_categories_follow_the_two_tier_policy(self):
        omo = self._omo_config()
        assert omo["categories"], "categories section must not be empty"
        for category, spec in omo["categories"].items():
            if category in self.LIGHT_CATEGORIES:
                assert spec["model"] == self.LIGHT_MODEL, category
                assert spec["reasoningEffort"] == "medium", category
            else:
                assert spec["model"] == self.PRIMARY_MODEL, category
                assert spec["reasoningEffort"] == "max", category

    def test_opencode_default_model_matches(self):
        config = _rendered()
        assert config["model"] == self.PRIMARY_MODEL


class TestAlwaysLoadedContextBudget:
    def test_agents_md_within_budget(self):
        lines = AGENTS_MD_PATH.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= AGENTS_MD_MAX_LINES, (
            f"AGENTS.md grew to {len(lines)} lines (budget {AGENTS_MD_MAX_LINES}); "
            "move scenario details into skills instead of the always-loaded file"
        )

    def test_agents_md_routes_to_research_scenarios_skill(self):
        body = AGENTS_MD_PATH.read_text(encoding="utf-8")
        assert "research-scenarios" in body
        assert "场景路由" in body


class TestResearchScenariosSkill:
    def test_frontmatter_contract(self):
        text = SCENARIOS_SKILL_PATH.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        frontmatter = text.split("---\n", 2)[1]
        assert "name: research-scenarios" in frontmatter
        assert "description:" in frontmatter

    @pytest.mark.parametrize(
        "marker",
        [
            "通用前置检查",
            "场景 A：股票/ETF 分析",
            "场景 B：量化回测",
            "场景 B2：Shadow Account",
            "场景 C：开放性问题",
            "场景 D：策略周期执行",
            "场景 E：选股策略",
            "场景 F：宏观/事件驱动问题",
        ],
    )
    def test_playbook_sections_present(self, marker):
        body = SCENARIOS_SKILL_PATH.read_text(encoding="utf-8")
        assert marker in body, marker


class TestEnvValueHardening:
    """Incident 2026-10-10: a k8s secret fed ``LLM_API_KEY`` with a trailing
    ``\\n``; the raw control character landed inside a JSON string literal of
    the template render (``Invalid control character at line 8 column 84``),
    the render failed, and the entrypoint fell back to a minimal config
    WITHOUT the vibe-trading MCP block. Env values must be stripped and
    JSON-escaped in ``build_context`` so no env value can ever break the
    render again."""

    LLM_BASE_URL = "http://rm:8900/llm/v1"

    def _render_proxy(self, monkeypatch, api_key: str) -> dict:
        monkeypatch.setenv("LLM_BASE_URL", self.LLM_BASE_URL)
        monkeypatch.setenv("LLM_API_KEY", api_key)
        monkeypatch.setenv("HOSTNAME", "opencode-poc-test")
        # json.loads is strict by default: raw control characters inside
        # string literals raise, so a successful parse IS the assertion.
        return json.loads(render_config.render(TEMPLATE_PATH, MANIFEST_PATH))

    def test_trailing_newline_in_api_key_renders_valid_json(self, monkeypatch):
        config = self._render_proxy(monkeypatch, "deadbeef\n")
        assert "vibe-trading" in config["mcp"]
        assert config["provider"]["alibaba-cn"]["options"]["apiKey"] == "deadbeef"

    def test_quote_and_tab_in_api_key_renders_valid_json(self, monkeypatch):
        config = self._render_proxy(monkeypatch, 'to"ken\tkey')
        assert config["provider"]["alibaba-cn"]["options"]["apiKey"] == 'to"ken\tkey'

    def test_unicode_in_api_key_round_trips(self, monkeypatch):
        config = self._render_proxy(monkeypatch, "key-密钥\n")
        assert config["provider"]["alibaba-cn"]["options"]["apiKey"] == "key-密钥"

    def test_backslash_in_api_key_round_trips(self, monkeypatch):
        config = self._render_proxy(monkeypatch, "C:\\path\\key\n")
        assert config["provider"]["alibaba-cn"]["options"]["apiKey"] == "C:\\path\\key"

    def test_newline_in_clickhouse_password_never_reaches_mcp_env(self, monkeypatch):
        monkeypatch.setenv("CLICKHOUSE_PASSWORD", "secret\n")
        vt_env = _rendered()["mcp"]["vibe-trading"]["env"]
        assert vt_env["CLICKHOUSE_PASSWORD"] == "secret"

    def test_newline_in_hostname_stripped_from_sandbox_id(self, monkeypatch):
        monkeypatch.setenv("HOSTNAME", "pod-name-123\n")
        assert render_config.build_context()["SANDBOX_ID"] == "pod-name-123"


class TestFallbackConfig:
    """``entrypoint.sh`` copies ``config/opencode.json.fallback`` when the
    template render fails. Incident 2026-10-10: the fallback did not exist in
    the image, so the container booted with a minimal config that had NO mcp
    block while the MCP server itself was perfectly importable. The static
    fallback must keep the VT MCP registration alive on any render failure."""

    def _fallback(self) -> dict:
        return json.loads(FALLBACK_PATH.read_text(encoding="utf-8"))

    def test_fallback_exists_and_parses_as_strict_json(self):
        assert FALLBACK_PATH.is_file(), f"missing fallback: {FALLBACK_PATH}"
        assert isinstance(self._fallback(), dict)

    def test_fallback_contains_full_vt_mcp_block(self):
        vt = self._fallback()["mcp"]["vibe-trading"]
        assert vt["type"] == "local"
        assert vt["command"] == [
            "/opt/venv/bin/python",
            "/opt/vibe-trading/agent/mcp_server.py",
        ]
        assert vt["timeout"] == 600000
        env = vt["env"]
        for key in (
            "CLICKHOUSE_HOST",
            "CLICKHOUSE_PORT",
            "CLICKHOUSE_USER",
            "CLICKHOUSE_PASSWORD",
            "CLICKHOUSE_DATABASE",
            "CLICKHOUSE_LLM_USER",
            "CLICKHOUSE_LLM_PASSWORD",
        ):
            assert env[key] == "", key
        assert env["VT_MEMORY"] == "full"
        assert env["VT_MEMORY_MCP_TOOLS"] == "1"
        assert env["VT_MEMORY_BASE_DIR"] == "/workspace/.vt-memory"

    def test_fallback_has_no_provider_block(self):
        # inject_llm_proxy() adds the provider block AFTER the copy — the
        # static file must never hardcode a proxy URL or credentials.
        assert "provider" not in self._fallback()

    def test_fallback_keeps_schema_model_tool_output_agents_plugin(self):
        config = self._fallback()
        assert config["$schema"] == "https://opencode.ai/config.json"
        assert config["model"] == "alibaba-cn/qwen3.8-max"
        assert config["tool_output"] == {"max_bytes": 262144, "max_lines": 8000}
        assert config["plugin"] == ["oh-my-openagent@5.1.0"]
        for agent_name in ("explore", "multimodal-looker"):
            assert config["agent"][agent_name]["permission"]["vibe-trading_*"] == "deny"

    def test_fallback_permission_denies_match_governance_manifest(self):
        # Drift guard: the fallback carries the SAME governance as the
        # rendered config, so a render failure never weakens the manifest.
        denies = render_config.build_permission_denies(
            render_config.load_manifest(MANIFEST_PATH)
        )
        assert self._fallback()["permission"] == denies


class TestInjectLlmProxy:
    """``inject_llm_proxy()`` is the fallback-only counterpart of the
    template's conditional provider block (shell function + heredoc python
    in ``entrypoint.sh``). Extract the function verbatim into a temp script
    and drive it through bash so the real production code path is tested."""

    @pytest.fixture()
    def injector(self, tmp_path):
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        start = text.index("inject_llm_proxy() {")
        end = text.index("\n}\n", start) + len("\n}\n")
        script = tmp_path / "inject_llm_proxy.sh"
        script.write_text(
            text[start:end].replace("/opt/venv/bin/python3", f"'{sys.executable}'"),
            encoding="utf-8",
        )
        target = tmp_path / "opencode.json"
        shutil.copyfile(FALLBACK_PATH, target)
        return script, target

    def _run(self, script: Path, target: Path, env_extra: dict) -> None:
        env = {"PATH": os.environ["PATH"], "TARGET": str(target), **env_extra}
        result = subprocess.run(
            ["bash", "-c", f"source '{script}' && inject_llm_proxy"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

    def test_noop_when_llm_base_url_empty(self, injector):
        script, target = injector
        before = target.read_bytes()
        self._run(
            script,
            target,
            {"LLM_BASE_URL": "", "LLM_API_KEY": "ignored", "SANDBOX_ID": "x"},
        )
        assert target.read_bytes() == before

    def test_poisoned_api_key_injected_stripped_and_mcp_survives(self, injector):
        script, target = injector
        self._run(
            script,
            target,
            {
                "LLM_BASE_URL": "http://rm:8900/llm/v1",
                "LLM_API_KEY": "deadbeef\n",
                "SANDBOX_ID": "opencode-poc-test\n",
            },
        )
        config = json.loads(target.read_text(encoding="utf-8"))
        options = config["provider"]["alibaba-cn"]["options"]
        assert options["baseURL"] == "http://rm:8900/llm/v1"
        assert options["apiKey"] == "deadbeef"
        assert options["headers"]["x-sandbox-id"] == "opencode-poc-test"
        assert "vibe-trading" in config["mcp"]
