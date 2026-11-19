#!/usr/bin/env bash
# Harness benchmark environment preflight entrypoint.
#
# Runs the Python preflight (Docker / HuggingFace / disk / opencode-serve
# drivability probe) and writes preflight_report.json next to the package.
#
# Exit codes:
#   0 - every check ok or carrying a recorded degradation decision
#   1 - some check failed/degraded WITHOUT a recorded decision
#   2 - hard script failure (interpreter/report problems)
#
# Interpreter selection: $HARNESS_BENCH_PYTHON wins, else python3 on PATH.
# (No hardcoded interpreter paths: set HARNESS_BENCH_PYTHON to pin one.)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

PYTHON_BIN="${HARNESS_BENCH_PYTHON:-python3}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "preflight.sh: interpreter not found: ${PYTHON_BIN}" >&2
    echo "preflight.sh: set HARNESS_BENCH_PYTHON to a python >= 3.11" >&2
    exit 2
fi

cd "${AGENT_DIR}"
export PYTHONPATH="${AGENT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
exec "${PYTHON_BIN}" -m src.evals.harness_bench.preflight "$@"
