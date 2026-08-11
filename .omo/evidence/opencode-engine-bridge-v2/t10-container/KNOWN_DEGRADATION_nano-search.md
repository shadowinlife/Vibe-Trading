# T10 — Known degradation: nano-search-mcp fails under mcp 2.x (DEFERRED, documented not fixed)

## Symptom

In the tenant container, opencode serve's `/mcp` status reports:

```json
{"vibe-trading": {"status": "connected"},          // ✓ the CORE MCP — 82 tools, works
 "search mcp": {"status": "failed", "error": "MCP error -32000: Connection closed"},  // ✗ nano-search
 "websearch": {"status": "connected"}, "context7": {"status": "connected"},
 "grep_app": {"status": "connected"}, "lsp": {"status": "connected"}, "codegraph": {"status": "disabled"}}
```

Running `nano-search-mcp` directly shows the root cause:

```
ModuleNotFoundError: No module named 'mcp.server.fastmcp'. This is mcp 2.x, where
FastMCP was renamed to MCPServer (from mcp.server.mcpserver import MCPServer) ...
or pin 'mcp<2' to keep running v1 code.
```

## Root cause (dependency drift, NOT a container-form defect)

- `nano-search-mcp` (OpencodeAgent/nano-search-mcp, v0.1.0, **unchanged by T10**) imports
  the **mcp v1** API: `from mcp.server.fastmcp import FastMCP` (in `server.py` + 9 tool
  modules = **10 files**), and declares `mcp[cli]>=1.0.0`.
- The newer vendored VT (`mymain-engine-bridge`) depends on `fastmcp>=2.14.0` (the
  **standalone** fastmcp package — vibe-trading's own `mcp_server.py` uses
  `from fastmcp import Context, FastMCP`, which works). Standalone fastmcp 4.0.3 is
  self-contained (requires `fastmcp-slim`, NOT `mcp`).
- nano-search's own `mcp[cli]>=1.0.0` resolves to **mcp 2.2.0**, which REMOVED the
  `mcp.server.fastmcp` module → nano-search's v1 import fails at startup → serve marks
  it `failed`.
- This is a **latent nano-search bug** (its `>=1.0.0` constraint permits 2.x while its
  code uses the v1-only import path) that only manifests once a co-installed dep pulls
  mcp 2.x. The legacy images (older VT, mcp v1.x era) did not hit it.

## Why NOT fixed in T10 (scope + risk)

1. **Not required for T10 acceptance.** The acceptance criterion (Web 聊天 + IM 往返全通；
   无 key 启动被拒) passes on the **core vibe-trading MCP (82 tools, connected)** — the
   web-chat round-trip E2E calls `vibe-trading_list_skills` successfully. nano-search is
   an AUXILIARY MCP (Chinese financial search: 新浪/百炼/公告/研报, 12 tools).
2. **The fix is a real API migration, not a one-liner.** Verified empirically:
   fastmcp 4.0.3's `FastMCP.__init__` **rejects** nano-search's `streamable_http_path`
   arg (`TypeError: FastMCP() no longer accepts streamable_http_path. Pass path to
   run_http_async()/http_app(), or set FASTMCP_STREAMABLE_HTTP_PATH`). So the fix needs:
   - swap `from mcp.server.fastmcp import FastMCP` → `from fastmcp import FastMCP` in 10 files;
   - drop/relocate `streamable_http_path="/mcp"` from the `FastMCP(...)` constructor
     (moot for the container's `--transport stdio`, but the arg must go);
   - verify `.run(transport="stdio")` + all 12 `@mcp.tool()` registrations under fastmcp 4.x;
   - update nano-search's `pyproject.toml` dep (`mcp[cli]>=1.0.0` → `fastmcp`).
   That is a focused migration deserving its own verification pass, not a rushed change
   folded into a container-assembly task.
3. **Pinning `mcp<2` is NOT a safe alternative.** vibe-trading imports `from mcp import
   types`, `from mcp.shared.auth import OAuthMetadata`, `from mcp.types import
   ToolAnnotations` (agent/src/tools/mcp.py, agent/src/live/classification.py) and is
   verified working on mcp 2.2.0; downgrading risks the CORE MCP to save an auxiliary one.

## Recommended follow-up (separate task)

Migrate `nano-search-mcp` to the standalone `fastmcp` 4.x API (the same package
vibe-trading already uses), aligning the whole image on one FastMCP source:
- 10-file import swap + constructor `streamable_http_path` removal + pyproject dep change;
- assert all 12 tools register and serve reports `search mcp: connected`;
- re-run the container E2E web-chat round-trip.

## Impact on the T10 verdict

NONE on acceptance. The tenant container's core chain (gateway → bridge → serve →
**vibe-trading MCP (82 tools)** → model → reply) is fully proven. nano-search's 12
auxiliary search tools are degraded (absent) until the follow-up migration lands. This
is recorded here honestly rather than papered over.
