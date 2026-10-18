"""paper_trade — deterministic paper-trading matching engine (pt-v1.1).

Step 1 scaffolding only: contract (schema.py), state hashing (state_hash.py),
calendar seam (calendar.py) and cn_a limit lookups (limits_cn_a.py).  The
matching engine, data layer and MCP tool itself are Steps 2-7 and gated on
G1 (RM written approval of pt-v1.1).

This ``__init__`` deliberately imports nothing heavy: the tool registry's
auto-discovery (src/tools/__init__.py) imports every module in this package
at agent startup, and the submodules pull pandas / the backtest engine
chain.  Consumers import submodules explicitly
(``from src.tools.paper_trade import state_hash``).
"""

from __future__ import annotations

__all__ = ["PaperTradeStepTool"]


# TODO-G1: pending RM written approval of pt-v1.1 — this placeholder is
# replaced in Step 6 (VD-12) by the real BaseTool subclass + the hand-written
# mcp_server.py @mcp.tool wrapper.  It is intentionally NOT a BaseTool
# subclass yet: registry auto-discovery collects BaseTool.__subclasses__(),
# and a half-built tool must never register itself (MCP/agent tool-count
# gates are pinned by test_readme_counts.py & friends).
class PaperTradeStepTool:
    """Symbol placeholder for the Step 6 ``paper_trade_step`` tool.

    Contract: deterministic paper-trading matching engine; never places
    real orders (REQUIREMENTS R1).  Implementation lands with Step 6
    registration (VD-12) after G1 approval.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError(
            "paper_trade_step is not implemented yet: the tool lands in Step 6 "
            "(VD-12 registration), gated on G1 (RM written approval of pt-v1.1)"
        )
