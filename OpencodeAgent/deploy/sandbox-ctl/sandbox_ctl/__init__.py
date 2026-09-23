"""e2b-sandbox-ctl — standalone CLI for ACS Agent Sandbox lifecycle ops.

D7 shell-out carrier for the future router ``E2bSandboxBackend``: all SDK
dependencies live here so the router stays zero-new-dependency.

Import discipline (STATE.md lesson 3): the kruise ``patch_e2b(https=False)``
call MUST run before any ``e2b`` symbol is used. ``sandbox_ctl.bootstrap``
owns that call and ``sandbox_ctl.client`` enforces the ordering at its own
module top — pinned by ``tests/test_bootstrap_order.py``.
"""

__version__ = "0.1.0"
