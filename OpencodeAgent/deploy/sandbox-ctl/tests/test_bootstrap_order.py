"""Pins the patch-e2b-before-any-e2b-import discipline (STATE.md lesson 3).

Two independent guards:
1. AST ordering — in every module of the package, an ``e2b`` import may only
   appear at a line BELOW the ``bootstrap.ensure_patched()`` call, and only
   client.py may import e2b at all.
2. Runtime ordering — a subprocess imports sandbox_ctl.client against a
   manufactured meta-path e2b (and a stub kruise_agents): the recorded event
   sequence must show patch_e2b firing BEFORE the first e2b module loads.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent / "sandbox_ctl"

SPY_SCRIPT = r"""
import sys, types, json
import importlib.abc, importlib.machinery

events = []

class _FakeLoader(importlib.abc.Loader):
    def create_module(self, spec):
        mod = types.ModuleType(spec.name)
        if spec.name == "e2b":
            class Sandbox:  # minimal surface client.py touches at import time
                pass
            mod.Sandbox = Sandbox
            mod.__path__ = []  # make it a package so e2b.exceptions resolves
        if spec.name == "e2b.exceptions":
            def _getattr(name):
                return type(name, (Exception,), {})
            mod.__getattr__ = _getattr
        events.append(spec.name)
        return mod
    def exec_module(self, module):
        pass

class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "e2b" or name.startswith("e2b."):
            return importlib.machinery.ModuleSpec(name, _FakeLoader())
        return None

sys.meta_path.insert(0, _Finder())

kruise = types.ModuleType("kruise_agents")
patch_mod = types.ModuleType("kruise_agents.patch_e2b")
def patch_e2b(https=True, validate_key=True):
    events.append("patch_e2b:https=%s" % https)
patch_mod.patch_e2b = patch_e2b
kruise.patch_e2b = patch_mod
sys.modules["kruise_agents"] = kruise
sys.modules["kruise_agents.patch_e2b"] = patch_mod

import sandbox_ctl.client  # noqa: E402

print(json.dumps(events))
"""


def _first_e2b_import_line(tree: ast.AST) -> int | None:
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module == "e2b" or (node.module or "").startswith("e2b.")):
            lines.append(node.lineno)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "e2b" or alias.name.startswith("e2b."):
                    lines.append(node.lineno)
    return min(lines) if lines else None


def _ensure_patched_call_line(tree: ast.AST) -> int | None:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "ensure_patched"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "bootstrap"
        ):
            return node.lineno
    return None


def test_ast_every_module_imports_e2b_only_after_patching():
    for py in sorted(PKG_DIR.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        first_e2b = _first_e2b_import_line(tree)
        if py.name == "client.py":
            assert first_e2b is not None, "client.py must import e2b (it is the SDK layer)"
            patch_line = _ensure_patched_call_line(tree)
            assert patch_line is not None, f"{py.name}: bootstrap.ensure_patched() call missing"
            assert patch_line < first_e2b, (
                f"{py.name}: ensure_patched() at line {patch_line} must precede the first "
                f"e2b import at line {first_e2b}"
            )
        else:
            assert first_e2b is None, f"{py.name}: direct e2b import outside client.py is forbidden"


def test_runtime_patch_fires_before_first_e2b_import():
    proc = subprocess.run(
        [sys.executable, "-c", SPY_SCRIPT],
        cwd=str(PKG_DIR.parent),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    events = json.loads(proc.stdout.strip().splitlines()[-1])
    patch_events = [i for i, e in enumerate(events) if e.startswith("patch_e2b")]
    e2b_events = [i for i, e in enumerate(events) if e == "e2b" or e.startswith("e2b.")]
    assert patch_events, "patch_e2b never fired during sandbox_ctl.client import"
    assert e2b_events, "the manufactured e2b was never imported — spy script broken"
    assert min(patch_events) < min(e2b_events), f"patch must precede any e2b import: {events}"
    assert "patch_e2b:https=False" in events, "patch_e2b must run with https=False"
