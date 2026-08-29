#!/usr/bin/env python3
"""D-batch Level-R routing judge runner.

Thin orchestration wrapper over ``run_llm_judge``'s budget/trace/client
helpers, driving the routing protocol (``d_routing_protocol``) instead of
the frozen selection protocol. Level-W (within-subagent selection) needs no
new code: it reuses ``run_llm_judge.py`` directly with ``--post-corpus`` /
``--queries-file`` / ``--tag``.

Artifacts (under ``../artifacts/``):
    d_routing_trace_<model>.jsonl — append-only golden trace.
    d_routing_probe_<model>[_<probe-tag>].jsonl — determinism probe records.

Usage:
    cd agent
    python -m src.evals.tool_selection.d_batch.run_d_judge            # full
    python -m src.evals.tool_selection.d_batch.run_d_judge \
        --model qwen3.8-max --limit 2                                 # smoke
    python -m src.evals.tool_selection.d_batch.run_d_judge \
        --probe-only --probe-tag r1                                   # noise floor
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import yaml

from src.evals.tool_selection.llm_judge_protocol import build_candidates_block
from src.evals.tool_selection.run_llm_judge import (
    BudgetCaps,
    BudgetState,
    JudgeCallError,
    append_line,
    budget_violation,
    build_client,
    call_judge,
    derive_spent,
    estimate_prompt_tokens,
    load_config,
    load_env_file,
    load_trace,
    utc_now_iso,
)
from src.evals.tool_selection.d_batch.d_routing_protocol import (
    SYSTEM_PROMPT_R,
    VALID_ROUTES,
    build_routing_messages,
    build_subagent_block,
    parse_route,
    routing_template_sha256,
)

HERE = Path(__file__).resolve().parent
TOOL_SELECTION_DIR = HERE.parent
AGENT_DIR = TOOL_SELECTION_DIR.parents[2]
ARTIFACTS_DIR = HERE.parent / "artifacts"
ROUTING_QUERIES = HERE / "queries_d_routing.yaml"
FULL_CORPUS = HERE.parent / "corpus_b_post.yaml"
DEFINITION_FILES = [
    HERE / "subagent_quant_agent.yaml",
    HERE / "subagent_web_docs_agent.yaml",
]
DEFAULT_CONFIG = HERE.parent / "judge_config_a5a8.yaml"
ENV_PATH = AGENT_DIR / ".env"

EXIT_OK, EXIT_ERROR, EXIT_CONFIG, EXIT_BUDGET = 0, 1, 2, 3


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _trace_path(model_id: str, tag: str | None) -> Path:
    suffix = f"_{tag}" if tag else ""
    return ARTIFACTS_DIR / f"d_routing_trace_{model_id}{suffix}.jsonl"


def _probe_path(model_id: str, probe_tag: str | None) -> Path:
    suffix = f"_{probe_tag}" if probe_tag else ""
    return ARTIFACTS_DIR / f"d_routing_probe_{model_id}{suffix}.jsonl"


def run_routing(*, model_cfg: dict, caps: BudgetCaps, entries: list[dict],
                candidates_block: str, subagents_block: str,
                env: dict[str, str], limit: int | None = None,
                tag: str | None = None, valid_routes: list | None = None,
                client_factory=build_client) -> int:
    """Score the routing corpus for one judge model, with resume + budget.

    Mirrors run_llm_judge.run_surface's contract: trace hash pinned,
    invalid responses count as misses, budget pre-check aborts cleanly.
    """
    trace_path = _trace_path(model_cfg["id"], tag)
    template_sha = routing_template_sha256()
    header, lines = load_trace(trace_path)
    if header is not None and header.get("prompt_template_sha256") != template_sha:
        print(
            f"error: {trace_path.name} recorded under routing template "
            f"{header.get('prompt_template_sha256')}; current is {template_sha}. "
            "Refusing to mix templates in one trace.",
            file=sys.stderr,
        )
        return EXIT_CONFIG
    if header is None:
        append_line(trace_path, {
            "header": True,
            "prompt_template_sha256": template_sha,
            "model": model_cfg["id"],
            "level": "routing",
            "valid_routes": list(valid_routes or VALID_ROUTES),
            "config_pins": {
                "temperature": model_cfg["temperature"],
                "max_response_tokens": model_cfg["max_response_tokens"],
                "budget": {"max_tokens": caps.max_tokens,
                           "max_calls": caps.max_calls},
            },
        })
    done_ids = {r.get("query_id") for r in lines}
    state = derive_spent(lines)
    last_prompt_tokens = next(
        (int(r.get("prompt_tokens")) for r in reversed(lines)
         if r.get("prompt_tokens")),
        None,
    )
    client = None
    selected = entries if limit is None else entries[:limit]
    for entry in selected:
        if entry["id"] in done_ids:
            continue
        user_text = build_routing_messages(
            candidates_block, subagents_block, entry["query"]
        )[1]["content"]
        estimated_next = estimate_prompt_tokens(
            SYSTEM_PROMPT_R, user_text, last_prompt_tokens
        ) + model_cfg["max_response_tokens"]
        violation = budget_violation(state, estimated_next, caps)
        if violation:
            print(f"aborted: {violation}", file=sys.stderr)
            return EXIT_BUDGET
        if client is None:
            client = client_factory(model_cfg, env)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_R},
            {"role": "user", "content": user_text},
        ]
        try:
            outcome = call_judge(client, model_cfg, messages)
        except JudgeCallError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        route = (parse_route(outcome["raw"], valid_routes)
                 if outcome["error"] is None else None)
        record = {
            "query_id": entry["id"],
            "model": model_cfg["id"],
            "level": "routing",
            "expected_route": entry["route"],
            "route": route,
            "route_hit": route == entry["route"],
            "prompt_sha256": hashlib.sha256(
                f"{SYSTEM_PROMPT_R}\n{user_text}".encode("utf-8")
            ).hexdigest(),
            "response_raw": outcome["raw"],
            "latency_ms": outcome["latency_ms"],
            "prompt_tokens": outcome["prompt_tokens"],
            "completion_tokens": outcome["completion_tokens"],
            "api_calls": outcome["api_calls"],
            "retried": outcome["retried"],
            "ts_utc": utc_now_iso(),
        }
        if outcome["error"] is not None:
            record["error"] = outcome["error"]
        append_line(trace_path, record)
        lines.append(record)
        state = BudgetState(
            tokens_spent=state.tokens_spent
            + outcome["prompt_tokens"] + outcome["completion_tokens"],
            calls_made=state.calls_made + outcome["api_calls"],
        )
        if outcome["prompt_tokens"]:
            last_prompt_tokens = outcome["prompt_tokens"]
        mark = "hit" if record["route_hit"] else (
            "INVALID" if route is None else "miss"
        )
        print(f"{entry['id']}: {mark} (route={route}, want={entry['route']})")
    hits = sum(1 for r in lines if r.get("route_hit"))
    invalid = sum(1 for r in lines if r.get("route") is None)
    print(f"{model_cfg['id']}/routing: {hits}/{len(lines)} hits, "
          f"{invalid} invalid")
    return EXIT_OK


def run_routing_probe(*, model_cfg: dict, caps: BudgetCaps,
                      entries: list[dict], candidates_block: str,
                      subagents_block: str, env: dict[str, str],
                      probe_cfg: dict, probe_tag: str | None,
                      client_factory=build_client) -> int:
    """Determinism probe for the routing template (test-retest noise floor)."""
    probe_path = _probe_path(model_cfg["id"], probe_tag)
    sample = entries[: int(probe_cfg.get("sample_queries", 8))]
    repeats = int(probe_cfg.get("repeats", 3))
    _, lines = load_trace(probe_path)
    done = {(r.get("query_id"), r.get("repeat")) for r in lines}
    state = derive_spent(lines)
    last_prompt_tokens = next(
        (int(r.get("prompt_tokens")) for r in reversed(lines)
         if r.get("prompt_tokens")),
        None,
    )
    client = None
    for entry in sample:
        for repeat in range(repeats):
            if (entry["id"], repeat) in done:
                continue
            user_text = build_routing_messages(
                candidates_block, subagents_block, entry["query"]
            )[1]["content"]
            estimated_next = estimate_prompt_tokens(
                SYSTEM_PROMPT_R, user_text, last_prompt_tokens
            ) + model_cfg["max_response_tokens"]
            violation = budget_violation(state, estimated_next, caps)
            if violation:
                print(f"aborted: {violation}", file=sys.stderr)
                return EXIT_BUDGET
            if client is None:
                client = client_factory(model_cfg, env)
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT_R},
                {"role": "user", "content": user_text},
            ]
            try:
                outcome = call_judge(client, model_cfg, messages)
            except JudgeCallError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_ERROR
            route = (parse_route(outcome["raw"], valid_routes)
                 if outcome["error"] is None else None)
            record = {
                "query_id": entry["id"],
                "repeat": repeat,
                "model": model_cfg["id"],
                "level": "routing-probe",
                "route": route,
                "prompt_sha256": routing_template_sha256(),
                "response_raw": outcome["raw"],
                "latency_ms": outcome["latency_ms"],
                "prompt_tokens": outcome["prompt_tokens"],
                "completion_tokens": outcome["completion_tokens"],
                "api_calls": outcome["api_calls"],
                "retried": outcome["retried"],
                "ts_utc": utc_now_iso(),
            }
            append_line(probe_path, record)
            lines.append(record)
            state = BudgetState(
                tokens_spent=state.tokens_spent
                + outcome["prompt_tokens"] + outcome["completion_tokens"],
                calls_made=state.calls_made + outcome["api_calls"],
            )
            if outcome["prompt_tokens"]:
                last_prompt_tokens = outcome["prompt_tokens"]
    routes: dict[str, list[str | None]] = {}
    for record in lines:
        routes.setdefault(record.get("query_id", ""), []).append(
            record.get("route")
        )
    per_query = {}
    for query_id, values in routes.items():
        counts: dict[str | None, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        per_query[query_id] = max(counts.values()) / len(values)
    agreement = sum(per_query.values()) / len(per_query) if per_query else 0.0
    print(f"probe {model_cfg['id']}/routing: {len(routes)} queries x "
          f"{repeats} repeats; route agreement rate = {agreement:.4f}")
    for query_id, rate in per_query.items():
        print(f"  {query_id}: agreement {rate:.2f} -> {routes[query_id]}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """CLI entry: full routing run, or the determinism probe."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG),
                        help="judge panel config (default: 2-model A5-A8 panel)")
    parser.add_argument("--model", default="all-available")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--tag", default=None,
                        help="run tag; namespaces the routing trace so a "
                             "revision round never mixes with the first "
                             "administration (same discipline as --probe-tag)")
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--probe-tag", default=None)
    parser.add_argument("--queries-file", default=None,
                        help="routing queries YAML (default: the D-batch "
                             "queries_d_routing.yaml); D4 passes its expanded corpus")
    parser.add_argument("--definitions", default=None,
                        help="comma-separated subagent definition YAMLs "
                             "(default: the two D-batch pilots); D4 passes the "
                             "pilot + candidate files")
    parser.add_argument("--extra-routes", default=None,
                        help="comma-separated additional valid route labels "
                             "(D4 candidate names); recorded in the trace header")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"error: config not found: {config_path}", file=sys.stderr)
        return EXIT_CONFIG
    config = load_config(config_path)
    env = load_env_file(ENV_PATH)
    models = config["models"]
    if args.model != "all-available":
        models = [m for m in models if m["id"] == args.model]
        if not models:
            print(f"error: model {args.model} not in config", file=sys.stderr)
            return EXIT_CONFIG

    corpus = _load_yaml(FULL_CORPUS)
    candidates_block = build_candidates_block(corpus)
    def_files = ([Path(p) for p in args.definitions.split(",")]
                 if args.definitions else DEFINITION_FILES)
    definitions = [_load_yaml(p) for p in def_files]
    subagents_block = build_subagent_block(definitions)
    queries_path = Path(args.queries_file) if args.queries_file else ROUTING_QUERIES
    entries = _load_yaml(queries_path)["entries"]
    valid_routes = (list(VALID_ROUTES)
                    + [r.strip() for r in args.extra_routes.split(",")]
                    if args.extra_routes else list(VALID_ROUTES))
    caps = BudgetCaps(
        max_tokens=int(config["budget"]["max_input_tokens_per_model_run"]),
        max_calls=int(config["budget"]["max_calls_per_model_run"]),
    )
    probe_cfg = config.get("determinism_probe", {})

    exit_code = EXIT_OK
    for model_cfg in models:
        if args.probe_only:
            code = run_routing_probe(
                model_cfg=model_cfg, caps=caps, entries=entries,
                candidates_block=candidates_block,
                subagents_block=subagents_block, env=env,
                probe_cfg=probe_cfg, probe_tag=args.probe_tag,
            )
        else:
            code = run_routing(
                model_cfg=model_cfg, caps=caps, entries=entries,
                candidates_block=candidates_block,
                subagents_block=subagents_block, env=env, limit=args.limit,
                tag=args.tag, valid_routes=valid_routes,
            )
        if code != EXIT_OK:
            exit_code = code
            break
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
