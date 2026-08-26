"""E2 LLM-judge evaluation runner for the tool-selection suite.

Semantic arbiter for description changes the lexical baseline cannot measure
(AUDIT Q2 rename, Q4 keyword front-loading): a pinned LLM sees the FULL
routing surface (74 MCP tool descriptions + 90 skill descriptions) plus one
user query and must pick its top three candidates; the choice is scored
against ``queries.yaml``. Two surfaces are compared — the frozen pre-change
baseline corpus vs the current post-change corpus — under identical pins
from ``judge_config.yaml``.

The frozen prompt protocol (template, parsing, scoring) lives in
``llm_judge_protocol``; aggregation and the markdown report live in
``llm_judge_report``. This module owns orchestration: env parsing, golden
trace, resume, budget enforcement and the CLI.

Honesty of cost and accuracy reporting is the deliverable:

* The prompt template is sha256-pinned in the trace header and the design
  doc; a trace recorded under a different template is refused.
* Invalid/unparseable responses count as misses and are tallied separately;
  one bad response never crashes the run.
* A budget check runs BEFORE every call and aborts cleanly (exit code 3)
  before a cap is overshot; spent tokens/calls are re-derived from the
  golden trace on startup, so resume cannot double-spend.
* At most ONE retry on transient network errors, recorded in the trace.

Artifacts (under ``artifacts/`` next to this file):

* ``llm_judge_trace_<model>_<surface>.jsonl`` — append-only golden trace.
* ``llm_judge_report_<model>_<surface>.md`` — accuracy + cost report.
* ``llm_judge_probe_<model>_<surface>.jsonl`` — determinism probe records.

Usage:
    cd agent
    python -m src.evals.tool_selection.run_llm_judge                 # all keys present
    python -m src.evals.tool_selection.run_llm_judge --surface post \
        --model qwen3.8-max --limit 2                                # smoke
    python -m src.evals.tool_selection.run_llm_judge --probe-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.evals.tool_selection.llm_judge_protocol import (
    SYSTEM_PROMPT,
    USER_TEMPLATE,
    build_candidates_block,
    build_name_kinds,
    parse_response,
    prompt_template_sha256,
    score_response,
)
from src.evals.tool_selection.llm_judge_report import (
    aggregate_lines,
    render_report,
)

HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE.parents[2]
QUERIES_PATH = HERE / "queries.yaml"
CONFIG_PATH = HERE / "judge_config.yaml"
ENV_PATH = AGENT_DIR / ".env"
ARTIFACTS_DIR = HERE / "artifacts"
CORPUS_PATHS = {
    "post": HERE / "corpus_snapshot.yaml",
    "baseline": HERE / "corpus_baseline_snapshot.yaml",
}

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2
EXIT_BUDGET = 3


class JudgeCallError(RuntimeError):
    """A non-transient judge API failure — the run aborts, queries unburned."""


@dataclass(frozen=True)
class BudgetCaps:
    """Per-(model, surface) run caps from ``judge_config.yaml``."""

    max_tokens: int
    max_calls: int


@dataclass(frozen=True)
class BudgetState:
    """Tokens and API calls already spent by one (model, surface) run."""

    tokens_spent: int
    calls_made: int


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file by hand (no python-dotenv in this environment).

    Args:
        path: The .env file; a missing file yields an empty mapping.

    Returns:
        Key/value mapping; comments, blank lines and an optional ``export``
        prefix are skipped, surrounding quotes are stripped.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):]
        key, _, value = stripped.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_config(path: Path) -> dict:
    """Load the pinned judge configuration.

    Args:
        path: ``judge_config.yaml`` location.

    Returns:
        The parsed configuration dict.
    """
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_corpus(surface: str) -> dict:
    """Load one corpus snapshot.

    Args:
        surface: ``post`` (current descriptions) or ``baseline`` (frozen
            pre-change descriptions).

    Returns:
        The parsed corpus snapshot.
    """
    return yaml.safe_load(CORPUS_PATHS[surface].read_text(encoding="utf-8"))


def load_queries() -> list[dict]:
    """Load the versioned query entries in file order.

    Returns:
        Entry dicts from ``queries.yaml``.
    """
    return yaml.safe_load(QUERIES_PATH.read_text(encoding="utf-8"))["entries"]


# --------------------------------------------------------------------------- #
# Golden trace I/O + resume accounting.
# --------------------------------------------------------------------------- #
def trace_path_for(artifacts_dir: Path, model_id: str, surface: str) -> Path:
    """Return the golden-trace path for one (model, surface) run.

    Args:
        artifacts_dir: Artifact directory.
        model_id: Judge model id.
        surface: ``baseline`` or ``post``.

    Returns:
        The JSONL trace path.
    """
    return artifacts_dir / f"llm_judge_trace_{model_id}_{surface}.jsonl"


def load_trace(path: Path) -> tuple[dict | None, list[dict]]:
    """Read a trace file, tolerating blank or corrupted lines.

    Args:
        path: Trace file; missing yields an empty trace.

    Returns:
        The header record (or None) plus the per-call records.
    """
    header: dict | None = None
    lines: list[dict] = []
    if not path.exists():
        return None, []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if record.get("header"):
            header = record
        else:
            lines.append(record)
    return header, lines


def append_line(path: Path, record: dict) -> None:
    """Append one JSON record to a JSONL file.

    Args:
        path: Destination file (parent dirs created).
        record: JSON-serializable record.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def derive_spent(lines: list[dict]) -> BudgetState:
    """Re-derive spent tokens and API calls from trace records.

    Args:
        lines: Per-call trace records.

    Returns:
        Tokens (prompt + completion) and API calls already spent.
    """
    tokens = sum(
        int(record.get("prompt_tokens") or 0) + int(record.get("completion_tokens") or 0)
        for record in lines
    )
    calls = sum(int(record.get("api_calls") or 1) for record in lines)
    return BudgetState(tokens_spent=tokens, calls_made=calls)


def estimate_prompt_tokens(
    system_text: str, user_text: str, last_prompt_tokens: int | None
) -> int:
    """Estimate the next call's prompt tokens for the budget pre-check.

    Uses the last provider-reported prompt count when one exists (the prompt
    is near-constant across calls of one surface); otherwise falls back to a
    conservative chars/3 heuristic for the mixed CJK/English payload.

    Args:
        system_text: Frozen system prompt.
        user_text: Rendered user message.
        last_prompt_tokens: Last observed provider-reported prompt count.

    Returns:
        Estimated prompt tokens for the next call.
    """
    if last_prompt_tokens:
        return last_prompt_tokens
    return max(1, math.ceil(len(system_text + user_text) / 3))


def budget_violation(
    state: BudgetState, estimated_next_tokens: int, caps: BudgetCaps
) -> str | None:
    """Check the budget BEFORE a call so a run never overshoots a cap.

    Args:
        state: Tokens/calls already spent.
        estimated_next_tokens: Estimated total tokens of the next call.
        caps: Configured budget caps.

    Returns:
        A message naming the cap when the next call would exceed it,
        otherwise None.
    """
    if state.calls_made + 1 > caps.max_calls:
        return (
            f"budget cap reached: next call would be #{state.calls_made + 1} "
            f"but max_calls_per_model_run={caps.max_calls}"
        )
    if state.tokens_spent + estimated_next_tokens > caps.max_tokens:
        return (
            f"budget cap reached: next call (~{estimated_next_tokens} tokens) "
            f"would exceed max_input_tokens_per_model_run={caps.max_tokens} "
            f"(already spent {state.tokens_spent})"
        )
    return None


# --------------------------------------------------------------------------- #
# Judge client.
# --------------------------------------------------------------------------- #
def resolve_base_url(model_cfg: dict, env: dict[str, str]) -> str | None:
    """Resolve a model's base URL from config pins and the parsed .env.

    Args:
        model_cfg: One ``models`` entry of ``judge_config.yaml``.
        env: Parsed ``agent/.env`` mapping.

    Returns:
        The base URL, or None to let the SDK use its default.
    """
    if model_cfg.get("base_url"):
        return model_cfg["base_url"]
    env_value = env.get(model_cfg.get("base_url_env", ""), "")
    return env_value or model_cfg.get("base_url_fallback")


def build_client(model_cfg: dict, env: dict[str, str]):
    """Build the OpenAI-compatible client for one judge model.

    Lazy-imports ``openai`` so offline use (tests, --help) never needs it.

    Args:
        model_cfg: One ``models`` entry of ``judge_config.yaml``.
        env: Parsed ``agent/.env`` mapping.

    Returns:
        An ``openai.OpenAI`` client.
    """
    from openai import OpenAI  # lazy: offline paths never import it

    return OpenAI(
        api_key=env.get(model_cfg["api_key_env"], ""),
        base_url=resolve_base_url(model_cfg, env),
    )


def _transient_error_types() -> tuple:
    """Return the openai exception types treated as transient network errors.

    Returns:
        Exception tuple (empty when openai is unavailable).
    """
    try:
        import openai
    except ImportError:
        return ()
    return (
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.InternalServerError,
        openai.RateLimitError,
    )


def call_judge(client, model_cfg: dict, messages: list[dict]) -> dict:
    """One judge call with at most ONE retry on transient network errors.

    A non-transient failure raises ``JudgeCallError`` instead of burning
    query budget: auth or request-shape errors would otherwise record every
    remaining query as a miss.

    Args:
        client: Object exposing ``chat.completions.create``.
        model_cfg: One ``models`` entry of ``judge_config.yaml``.
        messages: Chat messages from ``build_messages``.

    Returns:
        Dict with raw, prompt_tokens, completion_tokens, latency_ms,
        api_calls (1 or 2), retried, error (None on success).

    Raises:
        JudgeCallError: On a non-transient API failure.
    """
    transient = _transient_error_types()
    started = time.perf_counter()
    last_error: Exception | None = None
    for attempt in (1, 2):
        attempt_start = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=model_cfg["id"],
                messages=messages,
                temperature=model_cfg["temperature"],
                max_tokens=model_cfg["max_response_tokens"],
            )
            usage = response.usage
            return {
                "raw": response.choices[0].message.content or "",
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "latency_ms": round((time.perf_counter() - attempt_start) * 1000.0, 1),
                "api_calls": attempt,
                "retried": attempt > 1,
                "error": None,
            }
        except transient as exc:  # type: ignore[misc]
            last_error = exc
        except Exception as exc:
            raise JudgeCallError(f"judge call failed (non-transient): {exc}") from exc
    return {
        "raw": "",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
        "api_calls": 2,
        "retried": True,
        "error": repr(last_error),
    }


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 seconds form.

    Returns:
        Timestamp string, e.g. ``2026-08-26T04:00:00+00:00``.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Run drivers.
# --------------------------------------------------------------------------- #
def run_surface(
    *,
    model_cfg: dict,
    surface: str,
    caps: BudgetCaps,
    corpus: dict,
    entries: list[dict],
    env: dict[str, str],
    artifacts_dir: Path,
    prices: dict,
    limit: int | None = None,
    client_factory=build_client,
) -> int:
    """Score one (model, surface) run with resume and budget enforcement.

    Args:
        model_cfg: The judge model's config entry.
        surface: ``baseline`` or ``post``.
        caps: Budget caps.
        corpus: The corpus snapshot to score.
        entries: Query entries in file order.
        env: Parsed ``agent/.env`` mapping.
        artifacts_dir: Artifact directory for trace + report.
        prices: The ``prices`` block of ``judge_config.yaml``.
        limit: Score only the first N entries (None = all).
        client_factory: Callable (model_cfg, env) -> judge client;
            injectable for offline tests.

    Returns:
        Exit code: 0 ok, 1 non-transient API error, 2 template drift,
        3 budget cap reached.
    """
    trace_path = trace_path_for(artifacts_dir, model_cfg["id"], surface)
    template_sha = prompt_template_sha256()
    header, lines = load_trace(trace_path)
    if header is not None and header.get("prompt_template_sha256") != template_sha:
        print(
            f"error: {trace_path.name} was recorded under prompt template "
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
            "surface": surface,
            "corpus_captured_at": corpus["captured_at"],
            "config_pins": {
                "temperature": model_cfg["temperature"],
                "max_response_tokens": model_cfg["max_response_tokens"],
                "budget": {"max_tokens": caps.max_tokens, "max_calls": caps.max_calls},
            },
        })

    done_ids = {record.get("query_id") for record in lines}
    state = derive_spent(lines)
    name_kinds = build_name_kinds(corpus)
    candidates_block = build_candidates_block(corpus)
    last_prompt_tokens = next(
        (int(r.get("prompt_tokens")) for r in reversed(lines) if r.get("prompt_tokens")),
        None,
    )
    client = None
    abort_message = None
    selected = entries if limit is None else entries[:limit]
    for entry in selected:
        if entry["id"] in done_ids:
            continue
        user_text = USER_TEMPLATE.format(
            candidates=candidates_block, query=entry["query"]
        )
        estimated_next = estimate_prompt_tokens(
            SYSTEM_PROMPT, user_text, last_prompt_tokens
        ) + model_cfg["max_response_tokens"]
        violation = budget_violation(state, estimated_next, caps)
        if violation:
            abort_message = violation
            break
        if client is None:
            client = client_factory(model_cfg, env)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ]
        try:
            outcome = call_judge(client, model_cfg, messages)
        except JudgeCallError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        parsed = parse_response(outcome["raw"]) if outcome["error"] is None else None
        scores = score_response(parsed, entry, name_kinds)
        record = {
            "query_id": entry["id"],
            "model": model_cfg["id"],
            "surface": surface,
            "prompt_sha256": hashlib.sha256(
                f"{SYSTEM_PROMPT}\n{user_text}".encode("utf-8")
            ).hexdigest(),
            "response_raw": outcome["raw"],
            "parsed": parsed,
            "expected_id": scores["expected_id"],
            "top1_hit": scores["top1_hit"],
            "top3_hit": scores["top3_hit"],
            "neg_false_recall": scores["neg_false_recall"],
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
        mark = "top1" if scores["top1_hit"] else (
            "top3" if scores["top3_hit"] else ("INVALID" if parsed is None else "miss")
        )
        picked = parsed.get("first") if parsed else "-"
        print(f"{entry['id']}: {mark} (first={picked})")

    aggregates = aggregate_lines(lines, entries)
    report_path = artifacts_dir / f"llm_judge_report_{model_cfg['id']}_{surface}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        render_report(
            model_cfg=model_cfg,
            surface=surface,
            corpus=corpus,
            aggregates=aggregates,
            total_entries=len(entries),
            prices=prices,
        ),
        encoding="utf-8",
    )
    print(
        f"{model_cfg['id']}/{surface}: scored {aggregates['entries']}/{len(entries)} "
        f"entries; top-1 {aggregates['top1_hits']}, top-3 {aggregates['top3_hits']}, "
        f"invalid {aggregates['invalid_responses']}; "
        f"tokens {aggregates['prompt_tokens'] + aggregates['completion_tokens']}; "
        f"report {report_path.name}"
    )
    if abort_message:
        print(f"aborted: {abort_message}", file=sys.stderr)
        return EXIT_BUDGET
    return EXIT_OK


def run_probe(
    *,
    model_cfg: dict,
    surface: str,
    caps: BudgetCaps,
    probe_cfg: dict,
    corpus: dict,
    entries: list[dict],
    env: dict[str, str],
    artifacts_dir: Path,
    limit: int | None = None,
    client_factory=build_client,
) -> int:
    """Run the determinism probe: repeat the first N queries R times.

    Kept separate from the main trace; resume skips (query_id, repeat)
    pairs already recorded.

    Args:
        model_cfg: The judge model's config entry.
        surface: ``baseline`` or ``post``.
        caps: Budget caps (applied within the probe run).
        probe_cfg: The ``determinism_probe`` block of the config.
        corpus: The corpus snapshot to score.
        entries: Query entries in file order.
        env: Parsed ``agent/.env`` mapping.
        artifacts_dir: Artifact directory.
        limit: Unused by the probe (sample size comes from the config);
            accepted for CLI symmetry.
        client_factory: Callable (model_cfg, env) -> judge client.

    Returns:
        Exit code: 0 ok, 1 non-transient API error, 3 budget cap reached.
    """
    del limit  # probe sample size is pinned in judge_config.yaml
    probe_path = artifacts_dir / f"llm_judge_probe_{model_cfg['id']}_{surface}.jsonl"
    sample = entries[: int(probe_cfg.get("sample_queries", 8))]
    repeats = int(probe_cfg.get("repeats", 3))
    _, lines = load_trace(probe_path)
    done = {(r.get("query_id"), r.get("repeat")) for r in lines}
    state = derive_spent(lines)
    candidates_block = build_candidates_block(corpus)
    last_prompt_tokens = next(
        (int(r.get("prompt_tokens")) for r in reversed(lines) if r.get("prompt_tokens")),
        None,
    )
    client = None
    for entry in sample:
        for repeat in range(repeats):
            if (entry["id"], repeat) in done:
                continue
            user_text = USER_TEMPLATE.format(
                candidates=candidates_block, query=entry["query"]
            )
            estimated_next = estimate_prompt_tokens(
                SYSTEM_PROMPT, user_text, last_prompt_tokens
            ) + model_cfg["max_response_tokens"]
            violation = budget_violation(state, estimated_next, caps)
            if violation:
                print(f"aborted: {violation}", file=sys.stderr)
                return EXIT_BUDGET
            if client is None:
                client = client_factory(model_cfg, env)
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ]
            try:
                outcome = call_judge(client, model_cfg, messages)
            except JudgeCallError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_ERROR
            parsed = parse_response(outcome["raw"]) if outcome["error"] is None else None
            record = {
                "query_id": entry["id"],
                "repeat": repeat,
                "model": model_cfg["id"],
                "surface": surface,
                "prompt_sha256": prompt_template_sha256(),
                "response_raw": outcome["raw"],
                "parsed": parsed,
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

    firsts: dict[str, list[str | None]] = {}
    for record in lines:
        parsed = record.get("parsed")
        firsts.setdefault(record.get("query_id", ""), []).append(
            parsed.get("first") if isinstance(parsed, dict) else None
        )
    per_query = {}
    for query_id, values in firsts.items():
        counts: dict[str | None, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        per_query[query_id] = max(counts.values()) / len(values)
    agreement = sum(per_query.values()) / len(per_query) if per_query else 0.0
    print(
        f"probe {model_cfg['id']}/{surface}: {len(firsts)} queries x {repeats} "
        f"repeats; first-pick agreement rate = {agreement:.4f}"
    )
    for query_id, rate in per_query.items():
        print(f"  {query_id}: agreement {rate:.2f} -> {firsts[query_id]}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 ok, 1 API error, 2 config error, 3 budget).
    """
    config = load_config(CONFIG_PATH)
    known_ids = [model["id"] for model in config["models"]]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--surface", choices=("baseline", "post"), default="post",
        help="corpus surface to score (default: post)",
    )
    parser.add_argument(
        "--model",
        choices=known_ids + ["all-available"],
        default="all-available",
        help="judge model; all-available runs every model whose key is present",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="score only the first N query entries",
    )
    parser.add_argument(
        "--probe-only", action="store_true",
        help="run the determinism probe instead of the main evaluation",
    )
    args = parser.parse_args(argv)

    env = load_env_file(ENV_PATH)
    models = config["models"]
    if args.model != "all-available":
        models = [m for m in models if m["id"] == args.model]
        if not models:
            print(f"error: model {args.model} not found in judge_config.yaml",
                  file=sys.stderr)
            return EXIT_CONFIG
    caps = BudgetCaps(
        max_tokens=int(config["budget"]["max_input_tokens_per_model_run"]),
        max_calls=int(config["budget"]["max_calls_per_model_run"]),
    )
    corpus = load_corpus(args.surface)
    entries = load_queries()

    exit_code = EXIT_OK
    for model_cfg in models:
        key_env = model_cfg["api_key_env"]
        if not env.get(key_env):
            print(f"{model_cfg['id']}: skipped (no {key_env} key)")
            continue
        print(
            f"=== llm-judge {model_cfg['id']} / {args.surface} "
            f"(corpus captured_at {corpus['captured_at']}, "
            f"template sha256 {prompt_template_sha256()[:16]}…) ==="
        )
        if args.probe_only:
            code = run_probe(
                model_cfg=model_cfg, surface=args.surface, caps=caps,
                probe_cfg=config["determinism_probe"], corpus=corpus,
                entries=entries, env=env, artifacts_dir=ARTIFACTS_DIR,
                limit=args.limit,
            )
        else:
            code = run_surface(
                model_cfg=model_cfg, surface=args.surface, caps=caps,
                corpus=corpus, entries=entries, env=env,
                artifacts_dir=ARTIFACTS_DIR, prices=config.get("prices", {}),
                limit=args.limit,
            )
        exit_code = max(exit_code, code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
