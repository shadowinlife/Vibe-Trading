"""Test bootstrap for the OpencodeAgent deploy-side packages.

Puts ``OpencodeAgent/deploy`` on ``sys.path`` so tests can import the tenant
router package (``router.*``) the same way the CLI launcher does.
"""

from __future__ import annotations

import sys
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parents[1] / "deploy"
TESTS_DIR = Path(__file__).resolve().parent

for path in (DEPLOY_DIR, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
