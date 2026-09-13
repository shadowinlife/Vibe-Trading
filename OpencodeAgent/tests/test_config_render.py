"""Config assembly tests for the OpencodeAgent container wiring.

Covers the correctness of the startup config pipeline that used to be
inlined in ``entrypoint.sh``:

- ``opencode.json.tmpl`` renders to valid JSON with both MCP servers.
- ``vibe-trading-tools.json`` (tool governance manifest) entries compile
  into opencode ``permission`` deny entries, so disabled VT tools are
  removed from the model's visible tool surface.
- ``subagents.json`` (domain subagent manifest) entries compile into
  ``agent.<name>`` sections gated deny-first across every MCP namespace.
- Per-agent tool scoping keeps finance MCP tools away from agents that do
  not need them (explore / multimodal-looker).
- ``oh-my-openagent.json`` model tiering invariants (cheap agents stay
  cheap, deep agents stay on the max model).
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
SUBAGENTS_PATH = CONFIG_DIR / "subagents.json"
AGENTS_MD_PATH = OPENCODE_AGENT_DIR / "AGENTS.md"
SCENARIOS_SKILL_PATH = OPENCODE_AGENT_DIR / "skills" / "research-scenarios" / "SKILL.md"

# Always-loaded instruction budget: every AGENTS.md line is paid on every
# turn, so growth must be deliberate. Scenario playbooks live in the
# research-scenarios skill instead.
AGENTS_MD_MAX_LINES = 450


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
    def test_renders_valid_json_with_both_mcp_servers(self):
        config = _rendered()
        assert "search mcp" in config["mcp"]
        assert "vibe-trading" in config["mcp"]
        assert config["mcp"]["search mcp"]["type"] == "local"
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


class TestToolGovernanceManifest:
    def test_manifest_schema(self):
        manifest = render_config.load_manifest(MANIFEST_PATH)
        disabled = manifest["disabled"]
        assert isinstance(disabled, list) and disabled
        assert all(isinstance(entry, str) and entry for entry in disabled)

    def test_trading_write_surface_stays_denied(self):
        # DEC-5 (2026-08-30): Tier-0/Tier-1 split. The read family is available
        # (B2 connector-gated); the write pair stays denied globally and must
        # never appear in any subagent whitelist either.
        manifest = render_config.load_manifest(MANIFEST_PATH)
        assert "trading_place_order" in manifest["disabled"]
        assert "trading_cancel_order" in manifest["disabled"]
        assert "trading_*" not in manifest["disabled"]
        subagents = {s["name"]: s for s in render_config.load_subagents(SUBAGENTS_PATH)}
        for name, spec in subagents.items():
            assert "trading_place_order" not in spec["tools"], name
            assert "trading_cancel_order" not in spec["tools"], name
        # The connector subagent holds exactly the Tier-0 read verbs.
        assert set(subagents["trading-connector-agent"]["tools"]) == {
            "trading_connections", "trading_select_connection", "trading_check",
            "trading_account", "trading_positions", "trading_orders",
            "trading_quote", "trading_history",
        }

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
        assert permission["search_mcp_*"] == "deny"


class TestDomainSubagents:
    """Domain subagents (D-batch pilots) get a small whitelisted tool surface.

    The routing descriptions and whitelists are the L2-validated D-batch v2
    artifacts; the deny gate must cover every MCP namespace in the
    deployment, not just vibe-trading (cross-namespace leakage was a live
    L2 finding).
    """

    EXPECTED = {
        "quant-agent": {
            "alpha_zoo", "alpha_bench", "factor_analysis", "list_strategies",
            "query_strategies", "get_strategy_evidence", "backtest",
            "write_file", "read_file", "pattern_recognition", "quantlib_call",
        },
        "web-docs-agent": {"web_search", "read_url", "read_document"},
        # D4 round-3 admitted candidates (d4_batch/candidates_d4.yaml):
        "market-data-agent": {"get_market_data", "search_symbol", "screen_market", "iwencai_search", "orderbook_depth", "get_fund_flow", "get_northbound_flow", "get_margin_trading", "get_block_trades", "get_dragon_tiger", "get_lockup_expiry", "get_shareholder_count"},
        "fundamentals-text-agent": {"get_financial_statements", "get_fundamentals", "get_sec_filings", "get_stock_profile", "get_institutional_holdings", "get_stock_news", "get_research_reports", "research_papers"},
        "derivatives-agent": {"analyze_options", "analyze_options_payoff", "get_options_chain"},
        "risk-portfolio-agent": {"quantlib_call", "cashflow_performance"},
        "valuation-agent": {"quantlib_call", "prediction_market"},
        "macro-sector-agent": {"get_macro_series", "get_sector_info"},
        "altdata-agent": {"sentiment"},
        "funds-fi-agent": {"etf_holdings"},
        "user-analytics-agent": {"analyze_trade_journal", "extract_shadow_strategy", "run_shadow_backtest", "render_shadow_report", "scan_shadow_signals"},
        # DEC-5 (2026-08-30): Tier-0 read-only connector verbs; the write pair
        # (place/cancel) is denied globally and never enters any whitelist.
        "trading-connector-agent": {"trading_connections", "trading_select_connection", "trading_check", "trading_account", "trading_positions", "trading_orders", "trading_quote", "trading_history"},
    }

    def test_subagent_sections_rendered(self):
        agents = _rendered()["agent"]
        for name in self.EXPECTED:
            assert name in agents, name
            assert agents[name]["mode"] == "subagent"
            assert agents[name]["description"].strip()
            assert agents[name]["prompt"].startswith("{file:./prompts/")

    def test_deny_gate_covers_every_mcp_namespace(self):
        config = _rendered()
        namespaces = [server.replace(" ", "_") for server in config["mcp"]]
        assert namespaces, "template must configure at least one MCP server"
        for name in self.EXPECTED:
            permission = config["agent"][name]["permission"]
            for ns in namespaces:
                assert permission[f"{ns}_*"] == "deny", f"{name}: {ns}_*"

    def test_deny_gate_covers_omo_builtin_namespaces(self):
        # The oh-my-openagent plugin injects websearch/context7/grep_app/lsp
        # MCP servers at runtime; they never appear in the template, and the
        # D-batch L2 runs caught a subagent escaping its whitelist through
        # websearch_web_search_exa. The deny gate must cover them.
        config = _rendered()
        for name in self.EXPECTED:
            permission = config["agent"][name]["permission"]
            for ns in render_config.OMO_BUILTIN_NAMESPACES:
                assert permission[f"{ns}_*"] == "deny", f"{name}: {ns}_*"

    def test_whitelists_match_manifest(self):
        config = _rendered()
        manifest = {s["name"]: s for s in render_config.load_subagents(SUBAGENTS_PATH)}
        for name, tools in self.EXPECTED.items():
            permission = config["agent"][name]["permission"]
            allowed = {
                key[len("vibe-trading_"):]
                for key, value in permission.items()
                if value == "allow"
            }
            assert allowed == tools | {"list_skills", "load_skill"}, name
            assert set(manifest[name]["tools"]) == tools, name

    def test_deny_entries_precede_allow_entries(self):
        # opencode permission evaluation is last-match-wins; an allow listed
        # before the wildcard deny would be silently dead.
        config = _rendered()
        for name in self.EXPECTED:
            keys = list(config["agent"][name]["permission"].keys())
            deny_at = [i for i, k in enumerate(keys) if k.endswith("_*")]
            allow_at = [i for i, k in enumerate(keys) if not k.endswith("_*")]
            assert deny_at and allow_at, name
            assert max(deny_at) < min(allow_at), name

    def test_prompt_files_exist_in_repo(self):
        for name in self.EXPECTED:
            prompt_ref = _rendered()["agent"][name]["prompt"]
            rel = prompt_ref.removeprefix("{file:").removesuffix("}")
            assert (CONFIG_DIR / rel).resolve().is_file(), prompt_ref

    def test_prompts_materialized_next_to_rendered_config(self, tmp_path):
        # opencode's {file:} loader only accepts references inside the
        # rendered config's directory subtree (probed on 1.18.23: ../ and
        # outside-absolute paths are silently dropped), so rendering must
        # colocate the prompt files with the output.
        target = tmp_path / "rendered" / "opencode.json"
        subagents = render_config.load_subagents(SUBAGENTS_PATH)
        written = render_config.materialize_prompts(subagents, CONFIG_DIR, target)
        assert len(written) == len(self.EXPECTED)
        for path in written:
            assert path.parent == target.parent / "prompts"
            assert path.read_text(encoding="utf-8") == (
                CONFIG_DIR / "prompts" / path.name
            ).read_text(encoding="utf-8")

    def test_invalid_subagents_manifest_fails_loud(self, tmp_path):
        bad = tmp_path / "bad-subagents.json"
        bad.write_text(json.dumps({"subagents": [{"name": "x"}]}), encoding="utf-8")
        with pytest.raises(ValueError):
            render_config.render(TEMPLATE_PATH, MANIFEST_PATH, bad)

    def test_template_agents_not_clobbered(self):
        agents = _rendered()["agent"]
        assert agents["explore"]["permission"]["vibe-trading_*"] == "deny"
        assert agents["multimodal-looker"]["permission"]["vibe-trading_*"] == "deny"


class TestOmoModelConfig:
    """OMO runs on a single uniform model: qwen3.8-max is multimodal, so no
    agent (including multimodal-looker) needs a different tier."""

    UNIFORM_MODEL = "alibaba-cn/qwen3.8-max"

    def _omo_config(self) -> dict:
        with open(CONFIG_DIR / "oh-my-openagent.json", encoding="utf-8") as f:
            return json.load(f)

    def test_all_agents_use_uniform_model(self):
        omo = self._omo_config()
        assert omo["agents"], "agents section must not be empty"
        for agent_name, spec in omo["agents"].items():
            assert spec["model"] == self.UNIFORM_MODEL, agent_name
            assert spec["reasoningEffort"] == "max", agent_name

    def test_all_categories_use_uniform_model(self):
        omo = self._omo_config()
        assert omo["categories"], "categories section must not be empty"
        for category, spec in omo["categories"].items():
            assert spec["model"] == self.UNIFORM_MODEL, category
            assert spec["reasoningEffort"] == "max", category

    def test_opencode_default_model_matches(self):
        config = _rendered()
        assert config["model"] == self.UNIFORM_MODEL


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


# ============================================================================
# T10 tenant-container invariants: version pins (B1), uploads reachability (B6),
# and MCP-subprocess env parity (B5/B6). These lock the freeze enforcement and
# the volume/state alignment so a future edit cannot silently regress them.
# ============================================================================

DOCKERFILE_PATH = OPENCODE_AGENT_DIR / "Dockerfile"
DOCKERFILE_BASE_PATH = OPENCODE_AGENT_DIR / "Dockerfile.base"
ENTRYPOINT_PATH = OPENCODE_AGENT_DIR / "entrypoint.sh"

OPENCODE_PIN = "opencode-ai@1.18.30"
OMO_PIN = "oh-my-openagent@4.19.4"
CONTAINER_HOME = "/home/opencode"
CONTAINER_VT_HOME = "/home/opencode/.vibe-trading"
CONTAINER_UPLOADS = "/home/opencode/.vibe-trading/uploads"
CONTAINER_VT_MEMORY = "/home/opencode/.vibe-trading/.vt-memory"


class TestVersionPins:
    """B1 / D10: the `@latest` specs are fictitious pins (T2 §1.3/§1.4). All four
    freeze-compat loci must carry the exact validated versions, and no `@latest`
    may survive anywhere in the image recipes."""

    def test_dockerfile_pins_opencode_cli(self):
        text = DOCKERFILE_PATH.read_text(encoding="utf-8")
        assert OPENCODE_PIN in text
        assert "opencode-ai@latest" not in text

    def test_dockerfile_base_pins_opencode_cli(self):
        text = DOCKERFILE_BASE_PATH.read_text(encoding="utf-8")
        assert OPENCODE_PIN in text
        assert "opencode-ai@latest" not in text

    def test_template_pins_omo_plugin(self):
        text = TEMPLATE_PATH.read_text(encoding="utf-8")
        assert OMO_PIN in text
        assert "oh-my-openagent@latest" not in text

    def test_rendered_plugin_entry_is_pinned(self):
        assert _rendered()["plugin"] == [OMO_PIN]

    def test_entrypoint_fallback_pins_omo_plugin(self):
        # The minimal-fallback block (entrypoint.sh) is the SECOND @latest site
        # (T2 freeze-compat row 4) — pinning only the tmpl would leave it live.
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        assert OMO_PIN in text
        assert "oh-my-openagent@latest" not in text

    def test_base_image_pinned_off_latest_tag(self):
        # T2 §1.1: `opencode-serve-base:latest` is TWO different images (local vs
        # registry split-brain). The app Dockerfile must not FROM the moving tag.
        text = DOCKERFILE_PATH.read_text(encoding="utf-8")
        assert "FROM opencode-serve-base:latest" not in text
        assert "opencode-serve-base:v3.0.0-tenant" in text


class TestMcpEntryShape:
    """GolemBot #42: a Claude-style `{command:"str"}` entry makes opencode refuse
    to boot. Every MCP entry must be `{type:"local", command:[...]}`."""

    def test_every_mcp_entry_is_local_with_array_command(self):
        for name, server in _rendered()["mcp"].items():
            assert server["type"] == "local", name
            assert isinstance(server["command"], list), name
            assert server["command"] and all(isinstance(c, str) for c in server["command"]), name


class TestUploadsReachability:
    """B6 / D9: the server-generated config's `external_directory` ALLOW entries
    cover UPLOADS_DIR, as an OBJECT (never a string — kimaki deep-merge trap),
    and never via session scope."""

    def test_external_directory_is_object_not_string(self):
        ext = _rendered()["permission"]["external_directory"]
        assert isinstance(ext, dict), "external_directory must be an object"

    def test_external_directory_allows_uploads(self):
        ext = _rendered()["permission"]["external_directory"]
        assert ext.get(f"{CONTAINER_UPLOADS}/*") == "allow"

    def test_allow_rule_is_last_for_findlast(self):
        # opencode resolves permissions with findLast (last match wins). The
        # uploads allow must come after the broad `*` rule or it is dead.
        keys = list(_rendered()["permission"]["external_directory"].keys())
        assert keys.index(f"{CONTAINER_UPLOADS}/*") > keys.index("*")


class TestMcpEnvParity:
    """B5/B6: the MCP subprocess env (fixed at spawn by the rendered config) must
    carry HOME/VIBE_TRADING_HOME identical to the gateway, and VT memory must live
    inside the persisted home volume."""

    def test_mcp_env_home_matches_container(self):
        vt_env = _rendered()["mcp"]["vibe-trading"]["env"]
        assert vt_env["HOME"] == CONTAINER_HOME

    def test_mcp_env_vibe_trading_home_matches_container(self):
        vt_env = _rendered()["mcp"]["vibe-trading"]["env"]
        assert vt_env["VIBE_TRADING_HOME"] == CONTAINER_VT_HOME

    def test_vt_memory_base_dir_inside_persisted_volume(self):
        vt_env = _rendered()["mcp"]["vibe-trading"]["env"]
        assert vt_env["VT_MEMORY_BASE_DIR"] == CONTAINER_VT_MEMORY
        # Consolidated INSIDE the runtime root (the tenant volume), not /workspace.
        assert vt_env["VT_MEMORY_BASE_DIR"].startswith(CONTAINER_VT_HOME + "/")

    def test_memory_tools_stay_enabled(self):
        # Freeze-compat row 5: VT_MEMORY_MCP_TOOLS=1 is the frozen ON state.
        vt_env = _rendered()["mcp"]["vibe-trading"]["env"]
        assert vt_env["VT_MEMORY"] == "full"
        assert vt_env["VT_MEMORY_MCP_TOOLS"] == "1"
