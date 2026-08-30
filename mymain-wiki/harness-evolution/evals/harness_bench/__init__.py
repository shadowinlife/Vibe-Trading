"""Harness benchmark suite: parity spec, preflight checks and validators.

This package owns the evaluation foundation shared by BOTH harnesses under
comparison (the opencode+OMO baseline and the PydanticAI PoC):

* ``parity_spec.json``       -- the single parity contract (model mapping,
  generation parameters, per-benchmark seeds/cost caps, subset policies).
* ``parity.py``              -- runtime validator both harnesses call to
  assert their config matches ``parity_spec.json``.
* ``preflight.py``           -- environment preflight (Docker, HuggingFace,
  disk, opencode-serve drivability probe) emitting
  ``preflight_report.json``.
* ``adapter.py``             -- ``HarnessAdapter`` protocol plus the
  deterministic ``MockAdapter`` reference implementation.
* ``report_schema.json`` / ``report.py`` -- benchmark report contract and
  validator (metrics rows + total-cost row + skip markers + provenance).
* ``manifest.py`` / ``canonical_tool_manifest.json`` -- the real MCP
  ``tools/list`` surface captured under a pinned env; the only authority for
  tool-count thresholds.
* ``soak.py`` / ``SOAK_RIG.md`` -- soak rig: standard workload loop plus
  fixed-frequency RSS sampling (docker-stats / psutil boundaries); samplers
  live in ``soak_samplers.py`` and workload executors in
  ``soak_executors.py``.
* ``mcp_spawn.py``           -- bounded MCP stdio client shared by the
  manifest capture and the soak workload.

Research-only: nothing here places orders or touches product runtime code.
"""

SPEC_VERSION = "1.0"
