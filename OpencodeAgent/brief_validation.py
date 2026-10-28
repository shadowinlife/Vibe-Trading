"""Dependency-free validator for the intake brief contract.

``tests/evals/intake/brief.schema.json`` is the contract; this module
interprets the subset of JSON Schema it uses (``$ref`` / ``const`` / ``enum`` /
``type`` / ``required`` / ``additionalProperties`` / ``propertyNames`` /
``items`` / ``minItems`` / ``maxProperties`` / ``minLength`` / ``minimum`` /
``maximum`` / ``exclusiveMinimum`` / ``pattern`` / ``allOf`` / ``anyOf`` /
``if``-``then``) plus the ``$defs.tool_specs`` data block that names each
tool's arguments and enum values.

Tool names are additionally checked against the runtime MCP ``tools/list``
snapshot: that snapshot, not ``config/vibe-trading-tools.json`` (a three-entry
deny-list), is the registry of what the deployment actually exposes.

Usage::

    from brief_validation import assert_valid, load_tool_snapshot, validate

    errors = validate(brief)              # snapshot loaded from the fixture
    assert_valid(brief)                   # raises AssertionError with details
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = _ROOT / "tests" / "evals" / "intake" / "brief.schema.json"
TOOL_SNAPSHOT_PATH = _ROOT / "tests" / "evals" / "intake" / "fixtures" / "tool_list.json"

__all__ = ["assert_valid", "load_schema", "load_tool_snapshot", "resolve_arg_spec", "validate"]


def load_schema(path: Path | str = SCHEMA_PATH) -> dict:
    """Load the brief schema from disk."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_tool_snapshot(path: Path | str = TOOL_SNAPSHOT_PATH) -> set[str]:
    """Load the tool names captured by the runtime ``tools/list`` call."""
    return {entry["name"] for entry in json.loads(Path(path).read_text(encoding="utf-8"))}


def resolve_arg_spec(args: dict, dotted: str) -> dict | None:
    """Resolve ``config.start_date`` through nested ``properties`` blocks."""
    node: Any = args
    for index, part in enumerate(dotted.split(".")):
        if not isinstance(node, dict):
            return None
        node = node.get(part) if index == 0 else node.get("properties", {}).get(part)
        if node is None:
            return None
    return node if isinstance(node, dict) else None


def _expand(node: Any, root: dict, seen: frozenset[str] = frozenset()) -> Any:
    """Inline local ``$ref`` pointers so ``_check`` never has to resolve one."""
    if isinstance(node, list):
        return [_expand(item, root, seen) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/") and ref not in seen:
        target: Any = root
        for part in ref[2:].split("/"):
            target = target[part]
        expanded = _expand(copy.deepcopy(target), root, seen | {ref})
        expanded.update({key: value for key, value in node.items() if key != "$ref"})
        return expanded
    return {key: _expand(value, root, seen) for key, value in node.items()}


def _type_ok(value: Any, expected: str) -> bool:
    if expected in {"integer", "number"}:
        if isinstance(value, bool):
            return False
        return isinstance(value, int) if expected == "integer" else isinstance(value, (int, float))
    checks = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
    return isinstance(value, checks.get(expected, object))


def _check(value: Any, spec: dict, path: str) -> list[str]:
    """Validate one value against the supported subset of JSON Schema."""
    errors: list[str] = []
    if "const" in spec and value != spec["const"]:
        errors.append(f"{path}: expected {spec['const']!r}")
    if "enum" in spec and value not in spec["enum"]:
        errors.append(f"{path}: {value!r} is not one of {spec['enum']}")
    if "type" in spec and not _type_ok(value, spec["type"]):
        return errors + [f"{path}: expected {spec['type']}, got {type(value).__name__}"]

    if isinstance(value, str):
        if len(value) < spec.get("minLength", 0):
            errors.append(f"{path}: shorter than {spec['minLength']} character(s)")
        if spec.get("pattern") and not re.search(spec["pattern"], value):
            errors.append(f"{path}: {value!r} does not match {spec['pattern']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in spec and value < spec["minimum"]:
            errors.append(f"{path}: below minimum {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            errors.append(f"{path}: above maximum {spec['maximum']}")
        if "exclusiveMinimum" in spec and value <= spec["exclusiveMinimum"]:
            errors.append(f"{path}: must exceed {spec['exclusiveMinimum']}")

    if isinstance(value, list):
        if len(value) < spec.get("minItems", 0):
            errors.append(f"{path}: expected at least {spec['minItems']} item(s)")
        if isinstance(spec.get("items"), dict):
            for index, item in enumerate(value):
                errors.extend(_check(item, spec["items"], f"{path}[{index}]"))

    if isinstance(value, dict):
        if len(value) > spec.get("maxProperties", len(value)):
            errors.append(f"{path}: at most {spec['maxProperties']} properties allowed")
        properties = spec.get("properties", {})
        for key in spec.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required key {key!r}")
        for key, sub in properties.items():
            if key in value:
                errors.extend(_check(value[key], sub, f"{path}.{key}"))
        extra = [key for key in value if key not in properties]
        additional = spec.get("additionalProperties", True)
        if additional is False:
            errors.extend(f"{path}: unexpected key {key!r}" for key in extra)
        elif isinstance(additional, dict):
            for key in extra:
                errors.extend(_check(value[key], additional, f"{path}.{key}"))
        if isinstance(spec.get("propertyNames"), dict):
            for key in value:
                errors.extend(_check(key, spec["propertyNames"], f"{path} key {key!r}"))

    for sub in spec.get("allOf", []):
        errors.extend(_check(value, sub, path))
    branches = spec.get("anyOf") or []
    if branches and all(_check(value, branch, path) for branch in branches):
        errors.append(f"{path}: matches none of the anyOf branches")
    if "if" in spec and not _check(value, spec["if"], path):
        errors.extend(_check(value, spec.get("then", {}), path))
    return errors


def _check_plan(plan: Any, specs: dict, tool_names: set[str]) -> list[str]:
    if not isinstance(plan, list):
        return []
    errors: list[str] = []
    for index, call in enumerate(plan):
        path = f"brief.tool_plan[{index}]"
        if not isinstance(call, dict):
            continue
        tool = call.get("tool")
        if not isinstance(tool, str) or tool not in specs:
            errors.append(f"{path}.tool: {tool!r} has no entry in $defs.tool_specs")
            continue
        if tool not in tool_names:
            errors.append(f"{path}.tool: {tool!r} is absent from the runtime tools/list snapshot")
        args = call.get("args")
        if not isinstance(args, dict):
            continue
        for name in specs[tool].get("required", []):
            if name not in args:
                errors.append(f"{path}.args: missing required argument {name!r}")
        for name, value in args.items():
            sub = resolve_arg_spec(specs[tool].get("args", {}), name)
            if sub is None:
                errors.append(f"{path}.args.{name}: not an argument of {tool}")
            else:
                errors.extend(_check(value, sub, f"{path}.args.{name}"))
    return errors


def validate(
    brief: Any,
    *,
    schema: dict | None = None,
    tool_names: set[str] | None = None,
) -> list[str]:
    """Return every contract violation in ``brief`` (empty list means valid)."""
    raw = schema if schema is not None else load_schema()
    contract = _expand(raw, raw)
    snapshot = load_tool_snapshot() if tool_names is None else tool_names
    errors = _check(brief, contract, "brief")
    if isinstance(brief, dict):
        errors.extend(_check_plan(brief.get("tool_plan"), contract["$defs"]["tool_specs"], set(snapshot)))
        if not brief.get("closing_block") and brief.get("request_class") != "direct_answer":
            errors.append("brief.closing_block: empty only for request_class 'direct_answer'")
    return errors


def assert_valid(brief: Any, **kwargs: Any) -> None:
    """Raise ``AssertionError`` listing every violation when ``brief`` is invalid."""
    errors = validate(brief, **kwargs)
    if errors:
        raise AssertionError("; ".join(errors))
