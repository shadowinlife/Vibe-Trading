"""Config assembly tests for the OpencodeAgent container wiring.

Covers the correctness of the startup config pipeline that used to be
inlined in ``entrypoint.sh``:

- ``opencode.json.tmpl`` renders to valid JSON with both MCP servers.
- ``vibe-trading-tools.json`` (tool governance manifest) entries compile
  into opencode ``permission`` deny entries, so disabled VT tools are
  removed from the model's visible tool surface.
- Per-agent tool scoping keeps finance MCP tools away from agents that do
  not need them (explore / multimodal-looker).
- ``oh-my-openagent.json`` model/effort invariants (all agents DeepSeek;
  high/max reasoning tiers).
- ``AGENTS.md`` stays within the always-loaded context budget.
- The ``research-scenarios`` skill keeps its frontmatter + playbook body.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

OPENCODE_AGENT_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = OPENCODE_AGENT_DIR / "config"
TEMPLATE_PATH = CONFIG_DIR / "opencode.json.tmpl"
MANIFEST_PATH = CONFIG_DIR / "vibe-trading-tools.json"
AGENTS_MD_PATH = OPENCODE_AGENT_DIR / "AGENTS.md"
SCENARIOS_SKILL_PATH = OPENCODE_AGENT_DIR / "skills" / "research-scenarios" / "SKILL.md"

# Always-loaded instruction budget: every AGENTS.md line is paid on every
# turn, so growth must be deliberate. Scenario playbooks live in the
# research-scenarios skill and the clarification protocol lives in the
# intake skill instead.
AGENTS_MD_MAX_LINES = 350

# Captured verbatim from the current template (direct mode, no proxy) rendered
# with the fixture env below.
DIRECT_MODE_GOLDEN = """\
{
  "$schema": "https://opencode.ai/config.json",
  "model": "alibaba-cn/deepseek-v4.1-flash-1017",
  "provider": {
    "alibaba-cn": {
      "models": {
        "deepseek-v4.1-flash-1017": {
          "attachment": true,
          "limit": {
            "context": 1000000,
            "input": 500000,
            "output": 131072
          },
          "name": "DeepSeek V4.1 Flash 1017",
          "options": {
            "enable_thinking": true,
            "reasoning_effort": "high"
          },
          "reasoning": true,
          "tool_call": true
        }
      }
    }
  },
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
    "oh-my-openagent@5.1.35"
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
    """The provider block always declares the DeepSeek model; proxy options
    render only when LLM_BASE_URL is set. Provider ID must stay ``alibaba-cn``
    (opencode hardcodes enable_thinking injection against that ID)."""

    LLM_BASE_URL = "http://resource-manager.default.svc.cluster.local:8900/llm/v1"
    LLM_API_KEY = "pool-fake-token"

    def test_direct_mode_matches_golden_byte_for_byte(self):
        raw = render_config.render(TEMPLATE_PATH, MANIFEST_PATH)
        json.loads(raw)
        assert raw == DIRECT_MODE_GOLDEN

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
        assert config["model"] == "alibaba-cn/deepseek-v4.1-flash-1017"
        assert "deepseek-v4.1-flash-1017" in config["provider"]["alibaba-cn"]["models"]
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
    """All OMO agents run the same DeepSeek v4.1 Flash 1017 model. DeepSeek
    exposes low/high/max reasoning; roles that need deep planning or
    implementation run at max, while research/search/light roles run at high."""

    MODEL = "alibaba-cn/deepseek-v4.1-flash-1017"
    LIGHT_AGENTS = {"explore", "librarian", "multimodal-looker"}
    HEAVY_AGENTS = {
        "build",
        "plan",
        "sisyphus",
        "hephaestus",
        "sisyphus-junior",
        "OpenCode-Builder",
        "prometheus",
        "metis",
        "momus",
        "oracle",
        "atlas",
    }
    HEAVY_CATEGORIES = {
        "ultrabrain",
        "deep",
        "deep-high",
        "artistry",
        "unspecified-high",
    }
    LIGHT_CATEGORIES = {
        "visual-engineering",
        "deep-low",
        "quick",
        "unspecified-low",
        "writing",
    }

    def _omo_config(self) -> dict:
        with open(CONFIG_DIR / "oh-my-openagent.json", encoding="utf-8") as f:
            return json.load(f)

    def test_all_agents_use_deepseek_with_tiered_reasoning(self):
        omo = self._omo_config()
        assert set(omo["agents"]) == self.HEAVY_AGENTS | self.LIGHT_AGENTS
        for agent_name, spec in omo["agents"].items():
            assert spec["model"] == self.MODEL, agent_name
            expected = "high" if agent_name in self.LIGHT_AGENTS else "max"
            assert spec["reasoningEffort"] == expected, agent_name
            assert spec["thinking"] == {"type": "enabled"}, agent_name

    def test_all_categories_use_deepseek_with_tiered_reasoning(self):
        omo = self._omo_config()
        assert set(omo["categories"]) == self.HEAVY_CATEGORIES | self.LIGHT_CATEGORIES
        for category, spec in omo["categories"].items():
            assert spec["model"] == self.MODEL, category
            expected = "high" if category in self.LIGHT_CATEGORIES else "max"
            assert spec["reasoningEffort"] == expected, category
            assert spec["thinking"] == {"type": "enabled"}, category

    def test_opencode_default_model_matches(self):
        config = _rendered()
        assert config["model"] == self.MODEL

    def test_provider_declares_the_deepseek_model(self):
        config = _rendered()
        model = config["provider"]["alibaba-cn"]["models"]["deepseek-v4.1-flash-1017"]
        assert model["reasoning"] is True
        assert model["tool_call"] is True
        assert model["attachment"] is True
        assert model["options"]["enable_thinking"] is True
        assert model["options"]["reasoning_effort"] == "high"


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


COMMAND_DIR = CONFIG_DIR / "command"
DOCKERFILE_PATH = OPENCODE_AGENT_DIR / "Dockerfile"

COMMANDS = {
    "checkup.md": ("个股体检", "场景 A（股票/ETF 分析）"),
    "screen.md": ("选股清单", "场景 E（选股策略）"),
    "backtest.md": ("策略回测", "场景 B（量化回测）"),
    "review.md": ("交易复盘", "场景 B2（Shadow Account）"),
}

DEFAULT_EXECUTION_SENTENCE = "以下按默认值执行，可在结果后调整"
COMMAND_COPY_LINE = "COPY config/command/ /workspace/.opencode/command/"
RENDER_COPY_LINE = "COPY config/render_config.py /workspace/.opencode/render_config.py"


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse flat ``key: value`` frontmatter without a YAML dependency."""
    assert text.startswith("---\n"), "frontmatter must open on the first line"
    _, frontmatter, body = text.split("---\n", 2)
    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        assert separator, f"frontmatter line is not key: value -> {line!r}"
        fields[key.strip()] = value.strip().strip('"')
    return fields, body


class TestSlashCommands:
    """Pin the command-file template contract; runtime discovery lives in smoke-v311.sh."""

    def test_command_files_exist(self):
        assert COMMAND_DIR.is_dir(), f"missing {COMMAND_DIR}"
        assert sorted(p.name for p in COMMAND_DIR.glob("*.md")) == sorted(COMMANDS)

    @pytest.mark.parametrize("filename", sorted(COMMANDS))
    def test_frontmatter_schema(self, filename):
        fields, _ = _split_frontmatter((COMMAND_DIR / filename).read_text(encoding="utf-8"))
        assert fields.get("description"), filename
        assert fields.get("argument-hint"), filename

    @pytest.mark.parametrize("filename", sorted(COMMANDS))
    def test_description_is_a_short_chinese_prompt(self, filename):
        fields, _ = _split_frontmatter((COMMAND_DIR / filename).read_text(encoding="utf-8"))
        description = fields["description"]
        assert len(description) <= 80, f"{filename}: {len(description)} chars"
        assert any("\u4e00" <= ch <= "\u9fff" for ch in description), filename

    @pytest.mark.parametrize("filename,artifact,scenario", [
        (name, *contract) for name, contract in sorted(COMMANDS.items())
    ])
    def test_template_names_artifact_defaults_and_scenario(self, filename, artifact, scenario):
        _, body = _split_frontmatter((COMMAND_DIR / filename).read_text(encoding="utf-8"))
        assert artifact in body, f"{filename} must name the {artifact} artifact"
        assert DEFAULT_EXECUTION_SENTENCE in body, filename
        assert "research-scenarios" in body, filename
        assert scenario in body, f"{filename} must route to {scenario}"
        assert "$ARGUMENTS" in body, f"{filename} must pass the user's target through"

    def test_review_routes_to_shadow_account(self):
        _, body = _split_frontmatter((COMMAND_DIR / "review.md").read_text(encoding="utf-8"))
        assert "场景 B2" in body
        assert "交割单" in body

    def test_dockerfile_ships_the_command_directory(self):
        lines = DOCKERFILE_PATH.read_text(encoding="utf-8").splitlines()
        assert COMMAND_COPY_LINE in lines, "Dockerfile must COPY config/command/"
        index = lines.index(RENDER_COPY_LINE)
        assert lines[index + 1] == COMMAND_COPY_LINE, (
            "the command COPY belongs immediately after render_config.py"
        )
