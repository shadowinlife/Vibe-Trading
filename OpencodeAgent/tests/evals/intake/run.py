"""Drive the frozen intake corpus against a running opencode server.

Posts prompts via ``opencode run --attach``, derives four events from the
``--format json`` stream, and appends validated JSONL; ``--dry-run`` is offline.
Short runs can lose their final stream events (exit 0 with only ``step_start``);
in that case the transcript is recovered from the server session API.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from corpus_validation import EXPECT_LABELS, validate_corpus  # noqa: E402

CORPUS_PATH = BASE_DIR / "corpus.jsonl"
EXPECTED_PATH = BASE_DIR / "expected.json"
SCHEMA_PATH = BASE_DIR / "events.schema.json"
EVENT_TYPES = ("route_class", "intake_loaded", "question_called", "params_compiled")
RUN_ERROR = "run_error"
RECOVERY_PREFIX = "recovered_via_session_api;"
EMPTY_STREAM = {"calls": [], "text": "", "tokens": None}

NUMBERED_CHOICE = re.compile(r"(?m)^\s*(?:\d{1,2}[.)、)]|[（(]\d{1,2}[)）])\s+\S")
SESSION_ID_RE = re.compile(r'"(?:sessionID|session_id|session\.id)"\s*:\s*"([^"]+)"')
CHOICE_CUES = ("选项", "以上都不是", "请选择", "哪个", "还是", "希望", "偏好", "成本价", "仓位")
REFUSAL_MARKERS = ("无法转账", "无法划转", "无法执行", "无法完成", "无法办理", "无法操作", "无法协助", "不支持", "超出", "cannot", "unable", "not able", "out of scope")
VT_TOOL_PREFIXES = ("vibe-trading_", "vibe_trading_", "ch_")

FIELD_TYPES: dict[str, tuple[type, ...]] = {
    "run_id": (str,), "corpus_id": (str,), "event": (str,), "ts": (str,), "observed": (str, bool, type(None)), "expected": (str, bool, type(None)),
    "evidence": (str,), "tool": (str,), "args_digest": (str,), "question_count": (int,), "user_turns": (int,),
    "tokens": (int, type(None)), "wall_s": (int, float, type(None)),
}

def load_corpus(path: Path = CORPUS_PATH) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"{path}:{lineno}: corpus line must be a JSON object")
        items.append(item)
    return items

def load_expected(path: Path = EXPECTED_PATH) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected.json must map corpus ids to objects")
    return data

def validate_corpus_path(path: Path, items: list[dict[str, Any]]) -> list[str]:
    """Apply the frozen rules only to the default corpus path."""
    frozen = path.resolve() == CORPUS_PATH.resolve()
    return validate_corpus(items, load_expected() if frozen else None, frozen=frozen)

def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        value = list(value.values())
    if isinstance(value, list):
        for nested in value:
            yield from _walk(nested)

def _tool_name(obj: dict[str, Any]) -> str | None:
    name = obj.get("tool") or obj.get("name")
    if obj.get("type") in ("tool", "tool_use", "tool_call") and isinstance(name, str):
        return name
    return obj["tool"] if isinstance(obj.get("tool"), str) and "state" in obj else None

def _tool_args(obj: dict[str, Any]) -> dict[str, Any]:
    state = obj.get("state") if isinstance(obj.get("state"), dict) else {}
    candidates = (obj.get("input"), obj.get("args"), obj.get("arguments"), state.get("input"), state.get("args"))
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
        if isinstance(candidate, str) and candidate.strip().startswith("{"):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}

def _merge_usage(tokens: int | None, usage: Any) -> int | None:
    if tokens is None and isinstance(usage, dict):
        return int(sum(v for v in usage.values() if isinstance(v, (int, float)) and not isinstance(v, bool))) or None
    return tokens

def parse_stream(stdout: str) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    texts: list[str] = []
    tokens: int | None = None
    for line in stdout.splitlines():
        try:
            objects = list(_walk(json.loads(line.strip())))
        except json.JSONDecodeError:
            continue
        for obj in objects:
            name = _tool_name(obj)
            if name:
                call = {"name": name, "args": _tool_args(obj)}
                calls += [call] if call not in calls else []
            if obj.get("type") == "text" and isinstance(obj.get("text"), str):
                texts.append(obj["text"])
            elif obj.get("role") == "assistant" and isinstance(obj.get("content"), str):
                texts.append(obj["content"])
            tokens = _merge_usage(tokens, obj.get("tokens") or obj.get("usage"))
    return {"calls": calls, "text": "\n".join(texts), "tokens": tokens}

def _http_json(url: str, username: str, password: str, timeout: float = 30.0) -> Any:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def stream_session_id(stdout: str) -> str | None:
    if match := SESSION_ID_RE.search(stdout):
        return match.group(1)
    nested = re.search(r'"session"\s*:\s*\{[^}]*"id"\s*:\s*"([^"]+)"', stdout)
    return nested.group(1) if nested else None

def recover_stream(base: str, session_id: str, username: str, password: str, fetch: Any = _http_json) -> dict[str, Any]:
    # Endpoint confirmed from GET /doc (OpenAPI 3.1.0) on opencode 1.19.3:
    # /session/{sessionID}/message; the doc is consulted on every call.
    root = base.rstrip("/")
    try:
        paths = fetch(root + "/doc", username, password).get("paths", {})
    except Exception:
        return dict(EMPTY_STREAM)
    path = next((p for p in paths if p.endswith("/message") and "{sessionID}" in p and "/session/" in p), None)
    if not path:
        return dict(EMPTY_STREAM)
    try:
        payload = fetch(root + path.replace("{sessionID}", session_id), username, password)
    except Exception:
        return dict(EMPTY_STREAM)
    calls, texts, tokens = [], [], None
    for message in payload if isinstance(payload, list) else [payload]:
        info = message.get("info") if isinstance(message, dict) else {}
        if not isinstance(info, dict) or info.get("role") != "assistant":
            continue
        for obj in _walk(message.get("parts") or []):
            name = _tool_name(obj)
            if name:
                call = {"name": name, "args": _tool_args(obj)}
                calls += [call] if call not in calls else []
            if obj.get("type") == "text" and isinstance(obj.get("text"), str):
                texts.append(obj["text"])
        tokens = _merge_usage(tokens, info.get("tokens") or info.get("usage"))
    return {"calls": calls, "text": "\n".join(texts), "tokens": tokens}

def _is_question(name: str) -> bool:
    lowered = name.lower()
    return lowered == "question" or lowered.startswith(("question.", "question_"))

def _is_vt_tool(name: str) -> bool:
    return name.startswith(VT_TOOL_PREFIXES)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _names(calls: Iterable[dict[str, Any]]) -> str:
    return ",".join(sorted({call["name"] for call in calls})) or "-"

def derive_route(stream: dict[str, Any]) -> tuple[str, str]:
    calls = stream["calls"]
    question_calls = [call for call in calls if _is_question(call["name"])]
    vt_calls = [call for call in calls if _is_vt_tool(call["name"]) and call["args"]]
    numbered = NUMBERED_CHOICE.findall(stream["text"])
    choice_list = len(numbered) >= 2 and any(cue in stream["text"] for cue in CHOICE_CUES)
    if question_calls or choice_list:
        return "clarify_private", f"question_calls={len(question_calls)} numbered_lines={len(numbered)}"
    if vt_calls:
        return "execute_with_defaults", f"tools={_names(vt_calls)}"
    marker = next((m for m in REFUSAL_MARKERS if m in stream["text"].lower()), None)
    if marker:
        return "refuse", f"refusal_marker={marker!r} tools=-"
    return "direct_answer", f"tools=- text_len={len(stream['text'])}"

def build_events(run_id: str, item: dict[str, Any], stream: dict[str, Any], *, wall_s: float, stderr_tail: str = "", prefix: str = "") -> list[dict[str, Any]]:
    calls, text = stream["calls"], stream["text"]
    route, route_evidence = derive_route(stream)
    question_calls = [call for call in calls if _is_question(call["name"])]
    vt_calls = [call for call in calls if _is_vt_tool(call["name"]) and call["args"]]
    intake = any("skills/intake" in json.dumps(call["args"], ensure_ascii=False) for call in calls) or "intake" in text.lower()
    base = dict(run_id=run_id, corpus_id=item["id"], ts=_now(), user_turns=1, wall_s=round(wall_s, 3), tokens=stream["tokens"])
    intake_expected = bool(item.get("open_ended")) or item["expect"] == "clarify_private"
    events = [
        dict(event="route_class", observed=route, expected=item["expect"], evidence=route_evidence),
        dict(event="intake_loaded", observed=intake, expected=intake_expected, evidence=f"path_mention={intake}"),
        dict(event="question_called", observed=bool(question_calls), expected=item["expect"] == "clarify_private", evidence=f"question_calls={len(question_calls)}", question_count=len(question_calls)),
        dict(event="params_compiled", observed=bool(vt_calls), expected=item["expect"] == "execute_with_defaults", evidence=f"tools={_names(vt_calls)} stderr_tail={len(stderr_tail)}"),
    ]
    if vt_calls:
        payload = json.dumps(vt_calls[0]["args"], sort_keys=True, ensure_ascii=False, default=str)
        events[-1].update(tool=vt_calls[0]["name"], args_digest=hashlib.sha256(payload.encode()).hexdigest())
    return [{**base, **event, "evidence": prefix + event["evidence"]} for event in events]

def build_run_error(run_id: str, item: dict[str, Any], *, wall_s: float, reason: str, stdout_tail: str = "", stderr_tail: str = "", tokens: int | None = None) -> dict[str, Any]:
    evidence = reason + (f" | stdout_tail={stdout_tail[-500:]}" if stdout_tail else "")
    evidence += f" | stderr_tail={stderr_tail[-1500:]}" if stderr_tail else ""
    return {"run_id": run_id, "corpus_id": item["id"], "event": RUN_ERROR, "ts": _now(), "observed": "error", "expected": None, "evidence": evidence[-2000:], "user_turns": 1, "wall_s": round(wall_s, 3), "tokens": tokens}

def validate_event(record: dict[str, Any], schema: dict[str, Any] | None = None) -> list[str]:
    schema = schema if schema is not None else json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    event = record.get("event")
    if event not in EVENT_TYPES + (RUN_ERROR,):
        return [f"unknown event {event!r}"]
    required = schema.get("$defs", {}).get("base_record", {}).get("required", [])
    errors = [f"missing required field {key!r}" for key in required if key not in record]
    for key, value in record.items():
        if key not in FIELD_TYPES:
            errors.append(f"unexpected field {key!r}")
        elif not isinstance(value, FIELD_TYPES[key]) or (isinstance(value, bool) and key not in ("observed", "expected")):
            errors.append(f"field {key!r} has invalid type {type(value).__name__}")
    if event == "route_class":
        if any(record.get(key) not in EXPECT_LABELS for key in ("observed", "expected")):
            errors.append(f"route_class observed/expected must be one of {EXPECT_LABELS}")
    elif event == RUN_ERROR:
        if record.get("observed") != "error":
            errors.append("run_error observed must be 'error'")
        if record.get("expected") is not None:
            errors.append("run_error expected must be null")
    elif any(not isinstance(record.get(key), bool) for key in ("observed", "expected")):
        errors.append(f"{event} observed/expected must be booleans")
    return errors

def append_records(path: Path, records: list[dict[str, Any]], schema: dict[str, Any]) -> list[str]:
    problems = [f"{record.get('corpus_id')}:{record.get('event')}: {message}" for record in records for message in validate_event(record, schema)]
    if problems:
        return problems
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.writelines(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    return []

def build_command(args: argparse.Namespace, corpus_id: str, prompt: str) -> list[str]:
    cmd = ["docker", "exec", "-w", args.dir, args.container, "opencode"] if args.container else ["opencode"]
    cmd += ["run", "--attach", args.attach, "--dir", args.dir, "--format", "json", "--title", corpus_id]
    for flag, value in (("--model", args.model), ("--agent", args.agent), ("--command", args.command)):
        if value:
            cmd += [flag, value]
    cmd.append(prompt)
    return cmd

def _run_one(args: argparse.Namespace, item: dict[str, Any]) -> list[dict[str, Any]]:
    run_id = uuid.uuid4().hex
    command = build_command(args, item["id"], item["prompt"])
    started = time.monotonic()
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout)
    except subprocess.TimeoutExpired as exc:
        tail = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        if args.container:
            # Killing docker exec does not kill the in-container CLI; do it explicitly
            # so a timed-out item cannot accumulate a zombie process and memory.
            subprocess.run(["docker", "exec", args.container, "pkill", "-f", f"opencode run .*--title {item['id']}"], capture_output=True, timeout=30)
        return [build_run_error(run_id, item, wall_s=time.monotonic() - started, reason=f"timeout after {args.timeout}s", stderr_tail=tail)]
    except OSError as exc:
        return [build_run_error(run_id, item, wall_s=time.monotonic() - started, reason=f"exec failed: {exc}")]
    wall_s = time.monotonic() - started
    stream = parse_stream(proc.stdout)
    recovered = False
    if not args.disable_recovery and not stream["calls"] and not stream["text"]:
        session_id = stream_session_id(proc.stdout)
        if session_id:
            stream = recover_stream(args.recovery_url or args.attach, session_id, args.username, args.password)
            recovered = bool(stream["calls"] or stream["text"])
    if args.raw_dir:
        raw = Path(args.raw_dir)
        raw.mkdir(parents=True, exist_ok=True)
        (raw / f"{run_id}.stdout.txt").write_text(proc.stdout, encoding="utf-8")
        (raw / f"{run_id}.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    if (proc.returncode == 0 or recovered) and (stream["calls"] or stream["text"]):
        return build_events(run_id, item, stream, wall_s=wall_s, stderr_tail=proc.stderr, prefix=RECOVERY_PREFIX if recovered else "")
    reason = f"exit={proc.returncode}" if proc.returncode else "no parseable assistant text or tool call"
    return [build_run_error(run_id, item, wall_s=wall_s, reason=reason, stdout_tail=proc.stdout, stderr_tail=proc.stderr, tokens=stream["tokens"])]

def _real_run(args: argparse.Namespace) -> int:
    corpus_path = Path(args.corpus)
    corpus = load_corpus(corpus_path)
    if problems := validate_corpus_path(corpus_path, corpus):
        print("\n".join(f"[corpus] {problem}" for problem in problems), file=sys.stderr)
        return 2
    if args.only:
        known = {item["id"] for item in corpus}
        if unknown := sorted(set(args.only) - known):
            print(f"[corpus] unknown --only ids: {unknown}", file=sys.stderr)
            return 2
        corpus = [item for item in corpus if item["id"] in args.only]
    items = corpus[: args.limit] if args.limit is not None else corpus
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else Path("results") / f"run_{stamp}.jsonl"
    total = terminal = 0
    for _ in range(args.repeat):
        for item in items:
            total += 1
            records = _run_one(args, item)
            failures = append_records(out_path, records, schema)
            if failures:
                print(f"[schema] {failures}", file=sys.stderr)
            terminal += int(not failures and bool(records))
            print(f"[{item['id']}] {records[-1]['event'] if records else 'none'}", file=sys.stderr)
    print(f"[done] {terminal}/{total} terminal records -> {out_path}", file=sys.stderr)
    return 0 if terminal == total else 1

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the corpus and exit")
    parser.add_argument("--corpus", default=str(CORPUS_PATH), help="corpus JSONL path (default: frozen corpus)")
    parser.add_argument("--attach", default="http://localhost:4096", help="opencode server URL")
    parser.add_argument("--dir", default="/workspace", help="cwd for docker exec and --dir for opencode run")
    parser.add_argument("--container", help="run the CLI through `docker exec <container>`")
    parser.add_argument("--recovery-url", help="host-side server URL for session recovery (default: --attach)")
    parser.add_argument("--limit", type=int, help="only drive the first N corpus items")
    parser.add_argument("--only", action="append", metavar="ID", help="only drive the given corpus id (repeatable)")
    parser.add_argument("--disable-recovery", action="store_true", help="never fetch short-run transcripts from the session API")
    parser.add_argument("--username", default=os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"), help="basic auth username for recovery")
    parser.add_argument("--password", default=os.environ.get("OPENCODE_SERVER_PASSWORD", ""), help="basic auth password for recovery")
    parser.add_argument("--repeat", type=int, default=1, help="repeat the corpus N times")
    parser.add_argument("--timeout", type=float, default=600.0, help="per-item timeout in seconds")
    parser.add_argument("--out", help="output JSONL path (default results/run_<utc>.jsonl)")
    parser.add_argument("--raw-dir", help="raw stdout/stderr directory (default under $TMPDIR)")
    for flag in ("--model", "--agent", "--command"):
        parser.add_argument(flag, help=f"passed through as {flag}")
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.dry_run:
        corpus_path = Path(args.corpus)
        items = load_corpus(corpus_path)
        if problems := validate_corpus_path(corpus_path, items):
            print("\n".join(f"[corpus] {problem}" for problem in problems), file=sys.stderr)
            return 2
        for item in items:
            print(f"{item['id']} | {item['expect']} | {item['expected_product']} | {str(item['negative']).lower()} | {item['prompt']}")
        return 0
    if (args.limit is not None and args.limit < 1) or args.repeat < 1:
        parser.error("--limit and --repeat must be >= 1")
    if not args.raw_dir:
        args.raw_dir = str(Path(tempfile.gettempdir()) / "vt-intake-eval-raw")
    return _real_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
