---
name: memory-lifecycle
description: Save, recall, reinforce and reflect on persistent memories via the memory MCP tools to make strategy work compound across sessions.
category: tool
---

## Overview

The memory lifecycle tools expose the agent's persistent memory store
(`~/.vibe-trading/memory/`) over MCP so external callers (OpenCode, omo,
other MCP clients) can save findings, recall prior knowledge, reinforce
what worked, and store reflection lessons from backtest outcomes.

Five tools: `memory_save`, `memory_recall`, `memory_reinforce`,
`memory_reflect`, `memory_status`. All return a JSON envelope with
`status: ok | skipped | error`.

## Enablement

The tools are opt-in and hidden from `tools/list` by default:

```bash
export VT_MEMORY=full            # enable the full memory feature tier
export VT_MEMORY_MCP_TOOLS=1     # expose the memory tools over MCP
```

Notes:
- `VT_MEMORY=full` enables quality scoring, GC, hierarchy, links, FTS and
  reflections. `memory_reflect` additionally requires the reflections flag,
  which `full` already turns on (or set `VT_MEMORY_REFLECTIONS=1` alone).
- `VT_MEMORY_MCP_TOOLS` is never implied by a preset; it must be set
  explicitly. Restart the MCP server after changing it — tool registration
  happens at import time.

## Workflow Pattern

Use the loop: **recall → utilize → execute → reinforce → reflect**

1. **recall** — before designing anything, query prior knowledge:
   `memory_recall(query="momentum ETF daily", top_k=5)`
2. **utilize** — fold recalled lessons and parameters into the plan.
3. **execute** — run the backtest / analysis as usual.
4. **reinforce** — feed the outcome back:
   `memory_reinforce(name="momentum lookback sweet spot", event="task_success")`
5. **reflect** — persist a generalizable lesson:
   `memory_reflect(strategy_type="momentum", outcome={"sharpe": 1.2}, original_params={"lookback": 20})`

## When to Save

| Situation | Example call |
| --- | --- |
| A backtest completed with notable results | `memory_save(name, description, content, memory_type="project")` |
| User confirms a strategy works for them | `memory_save(..., memory_type="feedback")` |
| A new market pattern or data quirk discovered | `memory_save(..., memory_type="reference")` |
| User gives corrective feedback on an approach | `memory_save(..., memory_type="feedback")` |

Duplicate saves inside the dedup window return
`{"status": "skipped", "reason": "duplicate"}` — this is expected, not an
error.

## When to Recall

| Situation | Suggested query |
| --- | --- |
| Before designing a new strategy | strategy family + market, e.g. `"mean reversion A-share"` |
| Before selecting parameters | indicator + parameter names, e.g. `"lookback window momentum"` |
| Before a risk assessment | `"drawdown risk <symbol or sector>"` |
| Analyzing a symbol similar to past work | the symbol / sector keywords |

Use `type_filter` to narrow results (e.g. `type_filter="feedback"` for
user-confirmed knowledge only).

## When to Reinforce

| Event | Condition | Source | Effective delta |
| --- | --- | --- | --- |
| `task_success` | Recalled memory contributed to a successful run | `system` | +0.1 × 0.7 |
| `task_failure` | Recalled memory led to a failed / poor run | `system` | −0.15 × 0.7 |
| `user_confirm` | User explicitly validates the memory | `user` | +0.2 |
| `user_reject` | User explicitly rejects the memory | `user` | −0.3 |
| `passive_decay` | Periodic decay of unused entries | `system` | −0.05 × 0.7 |

Rules:
- `source="system"` applies a **0.7× discount** to the delta; reserve
  `source="user"` for explicit human feedback.
- Per-entry per-session reinforcement is capped; further calls return
  `status: skipped`.
- Unknown events return `status: error` with the list of valid events.

## OpenCode / omo Integration Example

Typical multi-agent sequence for one strategy iteration:

```text
planner   → memory_recall(query="dual MA crossover 159659", top_k=5)
          → designs the run using recalled lessons and parameters
executor  → runs the backtest via the backtest tool
evaluator → memory_reinforce(name="<recalled entry>", event="task_success")
          → memory_reflect(strategy_type="dual_ma",
                           outcome={"sharpe": 0.61, "max_drawdown": -0.084},
                           original_params={"fast": 5, "slow": 20})
```

`memory_status()` can be polled by an orchestrator to watch store growth
(`entry_count`), health (`avg_quality`, `avg_importance`) and GC backlog
(`gc_pending`).

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `memory_recall` returns empty results | Store is empty, or query keywords too narrow | Check `memory_status().entry_count`; broaden the query; drop `type_filter` |
| `memory_save` returns `skipped: duplicate` | Same name+content saved within the dedup window | Expected; change the content or wait out the window |
| `memory_reflect` returns `skipped` with a hint | `VT_MEMORY_REFLECTIONS` not enabled | Set `VT_MEMORY=full` or `VT_MEMORY_REFLECTIONS=1`, restart the server |
| `memory_reinforce` returns `skipped: not reinforced` | Quality flag off, session cap hit, or entry name not found | Enable `VT_MEMORY=on`/`full`; verify the exact entry title; retry next session |
| Memory tools missing from `tools/list` | `VT_MEMORY_MCP_TOOLS` unset when the server started | Export `VT_MEMORY_MCP_TOOLS=1` and restart the MCP server |
