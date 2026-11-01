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

import argparse
import copy
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = _ROOT / "tests" / "evals" / "intake" / "brief.schema.json"
TOOL_SNAPSHOT_PATH = (
    _ROOT / "tests" / "evals" / "intake" / "fixtures" / "tool_list.json"
)
DISCOVERY_DIR = _ROOT / "skills" / "research-discovery"

__all__ = [
    "assert_valid",
    "load_schema",
    "load_tool_snapshot",
    "resolve_arg_spec",
    "validate",
]


def load_schema(path: Path | str = SCHEMA_PATH) -> dict:
    """Load the brief schema from disk."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_tool_snapshot(path: Path | str = TOOL_SNAPSHOT_PATH) -> set[str]:
    """Load the tool names captured by the runtime ``tools/list`` call."""
    return {
        entry["name"] for entry in json.loads(Path(path).read_text(encoding="utf-8"))
    }


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


def _type_ok(value: Any, expected: str | list[str]) -> bool:
    if isinstance(expected, list):
        return any(_type_ok(value, item) for item in expected)
    if expected in {"integer", "number"}:
        if isinstance(value, bool):
            return False
        return (
            isinstance(value, int)
            if expected == "integer"
            else (isinstance(value, (int, float)) and math.isfinite(value))
        )
    checks = {
        "object": dict,
        "array": list,
        "string": str,
        "boolean": bool,
        "null": type(None),
    }
    return isinstance(value, checks.get(expected, object))


def _check(value: Any, spec: dict | bool, path: str) -> list[str]:
    """Validate one value against the supported subset of JSON Schema."""
    if isinstance(spec, bool):
        return [] if spec else [f"{path}: forbidden by schema"]
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
        if len(value) > spec.get("maxItems", len(value)):
            errors.append(f"{path}: too many items")
        if spec.get("uniqueItems") and any(
            item in value[:index] for index, item in enumerate(value)
        ):
            errors.append(f"{path}: duplicate items")
        if isinstance(spec.get("items"), (dict, bool)):
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


def _unsupported_schema(spec: dict | bool, path: str) -> list[str]:
    if isinstance(spec, bool):
        return []
    supported = {
        "$schema",
        "$id",
        "$defs",
        "definitions",
        "$comment",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
        "const",
        "enum",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "propertyNames",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "maxProperties",
        "minLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "allOf",
        "anyOf",
        "if",
        "then",
    }
    errors = [
        f"{path}: unsupported schema keyword {key!r}"
        for key in spec
        if key not in supported
    ]
    for name, child in spec.get("properties", {}).items():
        errors.extend(_unsupported_schema(child, f"{path}.{name}"))
    for key in ("items", "additionalProperties", "propertyNames", "if", "then"):
        if isinstance(spec.get(key), (dict, bool)):
            errors.extend(_unsupported_schema(spec[key], f"{path}.{key}"))
    for key in ("allOf", "anyOf"):
        for child in spec.get(key, []):
            errors.extend(_unsupported_schema(child, f"{path}.{key}"))
    return errors


def _check_dependencies(rows: list[dict], path: str) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for row in rows:
        identifier = row["id"]
        if identifier in seen:
            errors.append(f"{path}: duplicate id {identifier!r}")
        if any(dependency not in seen for dependency in row["depends_on"]):
            errors.append(f"{path}.{identifier}: dependency must precede its consumer")
        seen.add(identifier)
    return errors


def _check_discovery(
    brief: dict, tool_schemas: dict[str, dict], disabled_tools: set[str]
) -> list[str]:
    recipes = json.loads((DISCOVERY_DIR / "recipes.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    routes = brief["routes"]
    for route in routes:
        if route not in recipes:
            errors.append(f"brief.routes: unknown route {route!r}")
    selected = brief["selected_route"]
    if selected is not None and selected not in routes:
        errors.append("brief.selected_route: must be one of the offered routes")
    if brief["approval"] and brief["approval"]["revision"] != brief["revision"]:
        errors.append("brief.approval: stale revision")
    slots = list(brief["constraints"].values()) + [
        row["answer"] for row in brief["decisions"]
    ]
    if any(slot["source"] == "default" for slot in slots) and not brief["assumptions"]:
        errors.append("brief.assumptions: adopted defaults must be declared")
    errors.extend(_check_dependencies(brief["decisions"], "brief.decisions"))
    answers = {row["id"]: row["answer"]["source"] for row in brief["decisions"]}
    for row in brief["decisions"]:
        if row["answer"]["source"] != "unknown" and any(
            answers[dependency] == "unknown"
            for dependency in row["depends_on"]
            if dependency in answers
        ):
            errors.append(
                f"brief.decisions.{row['id']}: cannot settle before its prerequisite"
            )
    errors.extend(_check_dependencies(brief["tool_plan"], "brief.tool_plan"))
    if brief["status"] != "ready" and brief["tool_plan"]:
        errors.append("brief.tool_plan: executable plan requires ready status")
    market = brief["constraints"].get("market", {}).get("value")
    if (
        brief["status"] == "ready"
        and recipes.get(selected, {}).get("requires_data")
        and market is None
    ):
        errors.append("brief.constraints: data research requires a known market")
    if (
        selected in recipes
        and market is not None
        and market not in recipes[selected]["markets"]
    ):
        errors.append(
            f"brief.selected_route: {selected} does not support market {market!r}"
        )
    allowed = set(recipes.get(selected, {}).get("tools", []))
    allowed.update(
        (
            "start_research_goal",
            "get_research_goal",
            "add_goal_evidence",
            "update_research_goal_status",
        )
    )
    files = brief["files"]
    paths = [item["path"] for item in files]
    if len(paths) != len(set(paths)):
        errors.append("brief.files: duplicate paths")
    for item in files:
        if item["kind"] == "config" and not isinstance(item["content"], dict):
            errors.append("brief.files: config content must be an object")
        if item["kind"] == "signal" and not isinstance(item["content"], str):
            errors.append("brief.files: signal content must be source text")
    for call in brief["tool_plan"]:
        tool, args = call["tool"], call["args"]
        if tool not in allowed:
            errors.append(f"brief.tool_plan: {tool!r} is outside the selected recipe")
        if tool in disabled_tools:
            errors.append(f"brief.tool_plan: {tool!r} is disabled")
        if tool not in tool_schemas:
            errors.append(
                f"brief.tool_plan: {tool!r} is absent from tools/list inputSchema"
            )
            continue
        schema = tool_schemas[tool]
        expanded = _expand(schema, schema)
        unsupported = _unsupported_schema(expanded, f"tools/list.{tool}")
        errors.extend(unsupported)
        if not unsupported:
            errors.extend(_check(args, expanded, f"brief.tool_plan.{tool}.args"))
        if tool == "backtest" and isinstance(args.get("run_dir"), str):
            config_path = str(Path(args["run_dir"]) / "config.json")
            if not any(
                item["path"] == config_path and item["kind"] == "config"
                for item in files
            ):
                errors.append("brief.files: backtest requires its separate config.json")
        if tool == "screen_market" and args.get("market") not in {"a", "us", "hk"}:
            errors.append("brief.tool_plan: screen_market has no crypto market")
        if tool == "screen_market" and args.get("market") != market:
            errors.append("brief.tool_plan: screen_market differs from selected market")
    for name in ("sample_start", "sample_end"):
        value = brief["constraints"].get(name, {}).get("value")
        if value is not None:
            try:
                date.fromisoformat(value)
            except (TypeError, ValueError):
                errors.append(f"brief.constraints.{name}: expected ISO date")
    start = brief["constraints"].get("sample_start", {}).get("value")
    end = brief["constraints"].get("sample_end", {}).get("value")
    if isinstance(start, str) and isinstance(end, str) and start > end:
        errors.append("brief.constraints: sample_start is after sample_end")
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
            errors.append(
                f"{path}.tool: {tool!r} is absent from the runtime tools/list snapshot"
            )
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
    tool_schemas: dict[str, dict] | None = None,
    disabled_tools: set[str] | None = None,
) -> list[str]:
    """Return every contract violation in ``brief`` (empty list means valid)."""
    if isinstance(brief, dict) and brief.get("intake_version") == "2.0":
        try:
            json.dumps(brief, allow_nan=False)
        except (TypeError, ValueError):
            return ["brief: values must be finite JSON data"]
        raw = (
            schema
            if schema is not None
            else load_schema(DISCOVERY_DIR / "brief.schema.json")
        )
        errors = _check(brief, _expand(raw, raw), "brief")
        if errors:
            return errors
        if tool_schemas is None or disabled_tools is None:
            return [
                "brief: v2 requires tools/list inputSchema and explicit disabled_tools"
            ]
        return _check_discovery(brief, tool_schemas, disabled_tools)
    raw = schema if schema is not None else load_schema()
    contract = _expand(raw, raw)
    snapshot = load_tool_snapshot() if tool_names is None else tool_names
    errors = _check(brief, contract, "brief")
    if isinstance(brief, dict):
        errors.extend(
            _check_plan(
                brief.get("tool_plan"), contract["$defs"]["tool_specs"], set(snapshot)
            )
        )
        if (
            not brief.get("closing_block")
            and brief.get("request_class") != "direct_answer"
        ):
            errors.append(
                "brief.closing_block: empty only for request_class 'direct_answer'"
            )
    return errors


def assert_valid(brief: Any, **kwargs: Any) -> None:
    """Raise ``AssertionError`` listing every violation when ``brief`` is invalid."""
    errors = validate(brief, **kwargs)
    if errors:
        raise AssertionError("; ".join(errors))


def main() -> int:
    """Validate a v2 brief against a captured MCP schema/permission snapshot."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("brief", type=Path)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    schemas = {tool["name"]: tool["inputSchema"] for tool in snapshot["tools"]}
    errors = validate(
        json.loads(args.brief.read_text(encoding="utf-8")),
        tool_schemas=schemas,
        disabled_tools=set(snapshot["disabled_tools"]),
    )
    for error in errors:
        print(error)
    if not errors:
        print(
            "Valid contract only; user authorization, data and artifacts are not verified."
        )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
